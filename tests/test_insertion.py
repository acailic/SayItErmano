from __future__ import annotations

import subprocess

import pytest

from fluidvoice import insertion


def ok(args=None, stdout=b""):
    return subprocess.CompletedProcess(args or [], 0, stdout, b"")


@pytest.fixture()
def runner(monkeypatch):
    """Capture subprocess calls made through insertion._run (headless:
    no hold is ever taken, so tests never touch a real X connection)."""
    calls: dict = {"run": [], "popen": [], "sleeps": []}

    def fake_run(args, timeout=15.0, stdin=None):
        calls["run"].append(list(args))
        if args[0] == "xclip" and "-t" in args:
            return ok(args, b"UTF8_STRING\ntext/plain\n")  # text clipboard
        if args[0] == "xclip" and "-o" in args:
            return ok(args, b"previous clipboard")
        return ok(args)

    def fake_popen(args, **kwargs):
        calls["popen"].append(list(args))

        class P:
            def communicate(self, data=None):
                return (data, None)
        return P()

    monkeypatch.setattr(insertion, "_run", fake_run)
    monkeypatch.setattr(insertion.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(insertion.time, "sleep",
                        lambda s: calls["sleeps"].append(s))
    monkeypatch.setattr(insertion, "_make_hold", lambda *a, **k: None)
    return calls


def scripted_reads(monkeypatch, calls, plain_reads, targets=b"UTF8_STRING\ntext/plain\n"):
    """Replace _run with a scripted clipboard reader: successive plain
    `xclip -o` reads return plain_reads[0], [1], ... (the last repeats);
    `-t TARGETS` probes return `targets`. Read #1 is always the snapshot."""
    state = {"i": 0}

    def fake_run(args, timeout=15.0, stdin=None):
        calls["run"].append(list(args))
        if args[0] == "xclip" and "-t" in args:
            return ok(args, targets)
        if args[0] == "xclip" and "-o" in args:
            out = plain_reads[min(state["i"], len(plain_reads) - 1)]
            state["i"] += 1
            return ok(args, out)
        return ok(args)

    monkeypatch.setattr(insertion, "_run", fake_run)


class FakeHold:
    """Scriptable SelectionHold stand-in for insert_paste's state machine."""

    def __init__(self, reader=None, known=(0xAAA,), lost=False):
        self.reader = reader      # window id observed by wait_read, or None
        self.known = set(known)
        self.lost = lost
        self.released = False
        self.quiesced = []
        self.read_calls = []

    def quiesce(self, seconds, interval=None):
        self.quiesced.append(seconds)
        return set(self.known)

    @property
    def lost_ownership(self):
        return self.lost

    def wait_read(self, timeout, exclude_windows=(), interval=None):
        self.read_calls.append((timeout, tuple(exclude_windows)))
        return self.reader

    def release(self):
        self.released = True


def base_cfg(mode="auto", threshold=1200, delay=8):
    return {"insertion": {"mode": mode, "type_delay_ms": delay,
                          "paste_threshold_chars": threshold}}


def full_cfg(mode="auto", threshold=1200, delay=8, apps=None, space=True):
    import copy

    from fluidvoice.config import DEFAULTS
    cfg = copy.deepcopy(DEFAULTS)
    cfg["insertion"].update(mode=mode, type_delay_ms=delay,
                            paste_threshold_chars=threshold,
                            terminal_autocomplete_space=space)
    if apps is not None:
        cfg["general"]["terminal_apps"] = apps
    return cfg


class TestTyped:
    def test_command_construction(self, runner):
        assert insertion.insert_text("hello world", base_cfg()) == "typed"
        cmd = next(c for c in runner["run"] if c[:2] == ["xdotool", "type"])
        assert "--clearmodifiers" in cmd
        assert cmd[cmd.index("--delay") + 1] == "8"
        assert cmd[-1] == "hello world"

    def test_leading_dash_never_typed(self, runner):
        # "-" would be parsed as an xdotool option -> must go via paste
        assert insertion.insert_text("--verbose please", base_cfg()) == "paste"

    def test_failure_raises(self, monkeypatch):
        def failing(args, timeout=15.0, stdin=None):
            return subprocess.CompletedProcess(args, 1, b"", b"boom")
        monkeypatch.setattr(insertion, "_run", failing)
        with pytest.raises(insertion.InsertError):
            insertion.insert_typed("hi", 5)


class TestPaste:
    def test_long_text_uses_paste(self, runner):
        text = "x" * 2000
        assert insertion.insert_text(text, base_cfg()) == "paste"
        # clipboard was used, ctrl+v sent, previous restored
        writes = [c for c in runner["popen"] if c[0] == "xclip"]
        assert writes, "xclip must be used"
        keys = [c for c in runner["run"] if c[:2] == ["xdotool", "key"]]
        assert keys and "ctrl+v" in keys[0]

    def test_paste_mode_restores_clipboard(self, runner):
        insertion.insert_text("typed via clipboard", base_cfg(mode="paste"))
        # two xclip writes (headless): the text flash + blind restore
        assert len([c for c in runner["popen"] if c[0] == "xclip"]) == 2

    def test_paste_failure_in_auto_mode_falls_back_to_typed(self, runner, monkeypatch):
        def run_no_xdotool_key(args, timeout=15.0, stdin=None):
            if args[:2] == ["xdotool", "key"]:
                return subprocess.CompletedProcess(args, 1, b"", b"no key")
            return ok(args)
        monkeypatch.setattr(insertion, "_run", run_no_xdotool_key)
        # long text chooses paste in auto mode; failure falls back to typed
        assert insertion.insert_text("x" * 2000, base_cfg()) == "typed"

    def test_paste_failure_in_paste_mode_raises(self, runner, monkeypatch):
        def run_no_xdotool_key(args, timeout=15.0, stdin=None):
            if args[:2] == ["xdotool", "key"]:
                return subprocess.CompletedProcess(args, 1, b"", b"no key")
            return ok(args)
        monkeypatch.setattr(insertion, "_run", run_no_xdotool_key)
        with pytest.raises(insertion.InsertError):
            insertion.insert_text("short", base_cfg(mode="paste"))

    def test_missing_xclip_raises(self, monkeypatch):
        monkeypatch.setattr(insertion.shutil, "which", lambda n: None)
        with pytest.raises(insertion.InsertError):
            insertion.insert_paste("text")


class TestActiveWindowClass:
    def test_parses_wm_class(self, runner, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":99")  # hermetic: no ambient X needed
        monkeypatch.setattr(insertion.shutil, "which",
                            lambda n: "/usr/bin/" + n if n in ("xdotool", "xprop") else None)

        def fake_run(args, timeout=15.0, stdin=None):
            if args[0] == "xdotool":
                return ok(args, b"12345\n")
            return ok(args, b'WM_CLASS(STRING) = "instance", "dev.warp.Warp"\n')

        monkeypatch.setattr(insertion, "_run", fake_run)
        assert insertion.active_window_class() == "dev.warp.Warp"

    def test_falls_back_to_window_name(self, runner, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":99")  # hermetic: no ambient X needed
        monkeypatch.setattr(insertion.shutil, "which",
                            lambda n: "/usr/bin/xdotool" if n == "xdotool" else None)

        def fake_run(args, timeout=15.0, stdin=None):
            if args[:2] == ["xdotool", "getwindowname"]:
                return ok(args, b"Some Window Title")
            return ok(args, b"12345\n")

        monkeypatch.setattr(insertion, "_run", fake_run)
        assert insertion.active_window_class() == "Some Window Title"

    def test_no_display(self, runner, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        assert insertion.active_window_class() is None


class TestTerminalAutocompleteSpace:
    """general.terminal_apps matching + the one-space autocomplete rule."""

    def test_class_matching(self):
        assert insertion.is_terminal_app("gnome-terminal-server", full_cfg())
        assert insertion.is_terminal_app("Kitty", full_cfg())       # case
        assert insertion.is_terminal_app("GHOSTTY", full_cfg())     # -insensitive
        assert insertion.is_terminal_app("org.wezfurlong.wezterm",
                                         full_cfg())                  # substring
        assert not insertion.is_terminal_app("firefox", full_cfg())
        assert not insertion.is_terminal_app(None, full_cfg())
        assert not insertion.is_terminal_app("", full_cfg())

    def test_custom_list_honored(self):
        cfg = full_cfg(apps=["contour"])
        assert insertion.is_terminal_app("Contour Term", cfg)
        assert not insertion.is_terminal_app("kitty", cfg)

    def test_empty_list_matches_nothing(self):
        cfg = full_cfg(apps=[])
        assert not insertion.is_terminal_app("kitty", cfg)

    def test_trailing_space_helper(self):
        assert insertion.terminal_trailing_space("git checkout") \
            == "git checkout "
        assert insertion.terminal_trailing_space("done.") == "done."
        assert insertion.terminal_trailing_space("already ") == "already "
        assert insertion.terminal_trailing_space("") == ""

    def test_typed_text_in_terminal_gains_space(self, runner):
        assert insertion.insert_text("git checkout", full_cfg(),
                                     wm_class="kitty") == "typed"
        cmd = runner["run"][0]
        assert cmd[-1] == "git checkout "  # trailing space committed

    def test_punctuation_ending_no_space(self, runner):
        insertion.insert_text("done.", full_cfg(), wm_class="kitty")
        assert runner["run"][0][-1] == "done."

    def test_already_spaced_idempotent(self, runner):
        insertion.insert_text("already ", full_cfg(), wm_class="kitty")
        assert runner["run"][0][-1] == "already "

    def test_paste_path_never_gains_space(self, runner):
        assert insertion.insert_text("x" * 2000, full_cfg(),
                                     wm_class="kitty") == "paste"
        writes = [c for c in runner["popen"] if c[0] == "xclip"]
        assert writes and writes[0][-1] is not None  # clipboard used
        typed = [c for c in runner["run"] if c[:2] == ["xdotool", "type"]]
        assert typed == []  # nothing typed, nothing spaced

    def test_disabled_by_config(self, runner):
        insertion.insert_text("git checkout", full_cfg(space=False),
                              wm_class="kitty")
        assert runner["run"][0][-1] == "git checkout"

    def test_non_terminal_app_no_space(self, runner):
        insertion.insert_text("git checkout", full_cfg(), wm_class="firefox")
        assert runner["run"][0][-1] == "git checkout"

    def test_none_wm_class_resolved_live(self, runner, monkeypatch):
        monkeypatch.setattr(insertion, "active_window_class", lambda: "kitty")
        insertion.insert_text("git checkout", full_cfg())
        assert runner["run"][0][-1] == "git checkout "

    def test_none_wm_class_headless_no_lookup_crash(self, runner, monkeypatch):
        monkeypatch.setattr(insertion, "active_window_class", lambda: None)
        insertion.insert_text("git checkout", full_cfg())
        assert runner["run"][0][-1] == "git checkout"


class TestClipboardFallback:
    def test_writes_when_xclip_exists(self, runner):
        insertion.clipboard_fallback("emergency text")
        assert any(c[0] == "xclip" for c in runner["popen"])

    def test_silent_without_xclip(self, runner, monkeypatch):
        monkeypatch.setattr(insertion.shutil, "which", lambda n: None)
        insertion.clipboard_fallback("emergency")  # must not raise


class TestPasteVerification:
    """insert_paste's verify-then-restore state machine (faked holds)."""

    def _hold_run(self, monkeypatch, runner, hold, plain=None, targets=None):
        monkeypatch.setattr(insertion, "_make_hold", lambda *a, **k: hold)
        if plain is not None:
            scripted_reads(monkeypatch, runner, plain,
                           targets if targets is not None
                           else b"UTF8_STRING\ntext/plain\n")

    def test_read_verifies_paste(self, runner, monkeypatch):
        hold = FakeHold(reader=0x123)
        self._hold_run(monkeypatch, runner, hold)
        assert insertion.insert_text("x" * 2000, full_cfg()) == "paste"
        assert hold.quiesced == [insertion.PASTE_QUIESCE_S]
        assert hold.read_calls[0][0] == insertion.PASTE_VERIFY_TIMEOUT_S
        assert set(hold.read_calls[0][1]) == hold.known  # quiesce windows excluded
        assert hold.released
        # the dictation NEVER hits the clipboard via xclip while the hold
        # serves it: the only xclip write is the restore of the previous
        writes = [c for c in runner["popen"] if c[0] == "xclip"]
        assert len(writes) == 1  # read-back matched: no retry write

    def test_slow_app_within_cap_uses_full_timeout(self, runner, monkeypatch):
        hold = FakeHold(reader=0x123)
        self._hold_run(monkeypatch, runner, hold,
                       plain=[b"previous clipboard"])  # read-back matches
        assert insertion.insert_paste("text", verify=True) is None
        # the 0.60 s cap gives slow-to-focus apps the full window
        assert hold.read_calls[0][0] == 0.60
        assert insertion.PASTE_VERIFY_TIMEOUT_S == 0.60

    def test_never_reads_falls_back_to_typed_and_notifies(self, runner, monkeypatch):
        hold = FakeHold(reader=None)  # target never read the selection
        self._hold_run(monkeypatch, runner, hold)
        notices = []
        assert insertion.insert_text("x" * 2000, full_cfg(),
                                     on_notice=notices.append) == "typed"
        assert any("Paste did not land" in n for n in notices)
        assert hold.released  # hold always cleaned up
        # clipboard restored before the InsertError surfaced (clean state)
        assert [c for c in runner["popen"] if c[0] == "xclip"]
        # typed fallback happened
        assert any(c[:2] == ["xdotool", "type"] for c in runner["run"])

    def test_never_reads_paste_mode_raises(self, runner, monkeypatch):
        hold = FakeHold(reader=None)
        self._hold_run(monkeypatch, runner, hold)
        with pytest.raises(insertion.InsertError) as ei:
            insertion.insert_text("short", full_cfg(mode="paste"))
        assert "not verified" in str(ei.value)

    def test_restore_mismatch_retries_once(self, runner, monkeypatch):
        hold = FakeHold(reader=0x123)
        self._hold_run(monkeypatch, runner, hold,
                       plain=[b"previous clipboard", b"WRONG",
                              b"previous clipboard"])
        assert insertion.insert_paste("text") is None
        writes = [c for c in runner["popen"] if c[0] == "xclip"]
        assert len(writes) == 2  # restore retried exactly once

    def test_restore_still_mismatch_notifies_no_raise(self, runner, monkeypatch):
        hold = FakeHold(reader=0x123)
        self._hold_run(monkeypatch, runner, hold,
                       plain=[b"previous clipboard", b"WRONG"])
        notices = []
        # the paste already landed: raising would re-type and double-insert
        assert insertion.insert_paste("text", on_notice=notices.append) is None
        assert any("Clipboard restore could not be verified" in n
                   for n in notices)

    def test_non_text_previous_blind_restore(self, runner, monkeypatch):
        hold = FakeHold(reader=0x123)
        self._hold_run(monkeypatch, runner, hold,
                       plain=[b"\x89PNG..."], targets=b"image/png")
        assert insertion.insert_paste("text") is None
        writes = [c for c in runner["popen"] if c[0] == "xclip"]
        assert len(writes) == 1  # restored blind, no read-back verify
        # only one plain read happened: the snapshot (no read-back)
        plain_reads = [c for c in runner["run"]
                       if c[:1] == ["xclip"] and "-o" in c and "-t" not in c]
        assert len(plain_reads) == 1

    def test_lost_ownership_before_verify_unverified(self, runner, monkeypatch):
        hold = FakeHold(reader=None, lost=True)  # someone stole the selection
        self._hold_run(monkeypatch, runner, hold)
        notices = []
        assert insertion.insert_text("x" * 2000, full_cfg(),
                                     on_notice=notices.append) == "typed"
        # their content wins: no restore write at all
        assert [c for c in runner["popen"] if c[0] == "xclip"] == []

    def test_lost_ownership_after_verify_skips_restore(self, runner, monkeypatch):
        hold = FakeHold(reader=0x123, lost=True)  # paste landed, then stolen
        self._hold_run(monkeypatch, runner, hold)
        assert insertion.insert_paste("text") is None  # no raise, no notice
        assert [c for c in runner["popen"] if c[0] == "xclip"] == []

    def test_verify_disabled_is_today_behavior(self, runner, monkeypatch):
        made = []
        monkeypatch.setattr(insertion, "_make_hold",
                            lambda *a, **k: made.append(1))
        cfg = full_cfg()
        cfg["insertion"]["verify_paste"] = False
        assert insertion.insert_text("x" * 2000, cfg) == "paste"
        assert made == []  # no hold ever created
        # exactly two writes: the text flash + the blind restore
        assert len([c for c in runner["popen"] if c[0] == "xclip"]) == 2
        # the fixed settle (not the ladder) ran after the keystroke
        assert insertion.LEGACY_SETTLE_S in runner["sleeps"]
        assert not any(s in runner["sleeps"] for s in insertion.VERIFY_LADDER_S)

    def test_no_ownership_ladder_and_blind_restore(self, runner, monkeypatch):
        monkeypatch.setattr(insertion, "_make_hold", lambda *a, **k: None)
        cfg = full_cfg()  # verify_paste defaults True, hold unavailable
        assert insertion.insert_text("x" * 2000, cfg) == "paste"
        assert all(s in runner["sleeps"] for s in insertion.VERIFY_LADDER_S)
        assert len([c for c in runner["popen"] if c[0] == "xclip"]) == 2

    def test_unverified_error_is_flagged_for_the_notice(self, runner, monkeypatch):
        hold = FakeHold(reader=None)
        self._hold_run(monkeypatch, runner, hold)
        with pytest.raises(insertion.InsertError) as ei:
            insertion.insert_paste("text")
        assert getattr(ei.value, "not_verified", False)


class TestTerminalPasteKey:
    """X11 terminals paste with ctrl+shift+v (general.terminal_apps key)."""

    def _key(self, runner):
        return next(c for c in runner["run"] if c[:2] == ["xdotool", "key"])[-1]

    def test_terminal_app_gets_shifted_key(self, runner):
        assert insertion.insert_text("x" * 2000, full_cfg(),
                                     wm_class="kitty") == "paste"
        assert self._key(runner) == "ctrl+shift+v"

    def test_non_terminal_app_gets_plain_ctrl_v(self, runner):
        insertion.insert_text("x" * 2000, full_cfg(), wm_class="firefox")
        assert self._key(runner) == "ctrl+v"

    def test_custom_terminal_paste_key_honored(self, runner):
        cfg = full_cfg()
        cfg["insertion"]["terminal_paste_key"] = "ctrl+alt+v"
        insertion.insert_text("x" * 2000, cfg, wm_class="kitty")
        assert self._key(runner) == "ctrl+alt+v"

    def test_empty_terminal_list_uses_ctrl_v(self, runner):
        insertion.insert_text("x" * 2000, full_cfg(apps=[]), wm_class="kitty")
        assert self._key(runner) == "ctrl+v"

    def test_paste_mode_in_terminal_uses_terminal_key(self, runner):
        insertion.insert_text("short", full_cfg(mode="paste"), wm_class="kitty")
        assert self._key(runner) == "ctrl+shift+v"

    def test_typed_path_unaffected(self, runner):
        assert insertion.insert_text("git checkout", full_cfg(),
                                     wm_class="kitty") == "typed"
        assert [c for c in runner["run"] if c[:2] == ["xdotool", "key"]] == []
