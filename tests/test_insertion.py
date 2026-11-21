from __future__ import annotations

import struct
import subprocess
import threading
import time

import pytest

from fluidvoice import eis as eis_mod
from fluidvoice import eis_insertion, insertion, wayland_probe
from fluidvoice.config import DEFAULTS

# captured at import (test fixtures shrink the live-loop deadline)
REAL_VERIFY_TIMEOUT_S = insertion.PASTE_VERIFY_TIMEOUT_S


def set_wl_session(monkeypatch):
    """Pin a wayland session (tests/conftest.py pins x11 suite-wide)."""
    for var in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY",
                "XDG_CURRENT_DESKTOP"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")


def wl_cfg(**over):
    import copy
    cfg = copy.deepcopy(DEFAULTS)
    cfg["insertion"].update(over)
    return cfg


@pytest.fixture()
def wl_runner(monkeypatch):
    """Wayland-pinned capture of every subprocess call insertion makes
    (the twin of the x11 runner above; wtype + wl-clipboard installed)."""
    set_wl_session(monkeypatch)
    calls: dict = {"run": [], "popen": [], "sleeps": []}
    installed = {"wtype", "ydotool", "wl-copy", "wl-paste"}

    def fake_run(args, timeout=15.0, stdin=None):
        calls["run"].append(list(args))
        if args[:2] == ["wl-paste", "--no-newline"]:
            return ok(args, b"previous clipboard")
        if args[0] == "wl-paste":
            return ok(args, b"text/plain;charset=utf-8\n")
        return ok(args)

    class P:
        def communicate(self, data=None):
            return (data, None)

    monkeypatch.setattr(insertion, "_run", fake_run)
    monkeypatch.setattr(insertion.subprocess, "Popen",
                        lambda args, **kw: (calls["popen"].append(list(args))
                                            or P()))
    monkeypatch.setattr(insertion.time, "sleep",
                        lambda s: calls["sleeps"].append(s))
    monkeypatch.setattr(insertion.shutil, "which",
                        lambda n: f"/usr/bin/{n}" if n in installed else None)
    calls["installed"] = installed
    calls["set_installed"] = lambda s: installed.clear() or installed.update(s)
    return calls


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
    # field probe blind: unit tests never touch a real a11y bus
    monkeypatch.setattr(insertion, "_probe_field_text",
                        lambda *a, **k: None)
    # hermetic capability/paste gating: xdotool+xclip "installed" (the
    # paste paths branch on shutil.which before any _run call)
    monkeypatch.setattr(insertion.shutil, "which",
                        lambda n: f"/usr/bin/{n}"
                        if n in ("xdotool", "xclip") else None)
    # the verify loop's real-world deadline (1.5 s) would busy-spin a
    # test (sleep is faked) - shrink it to a tick
    monkeypatch.setattr(insertion, "PASTE_VERIFY_TIMEOUT_S", 0.05)
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


def scripted_probe(monkeypatch, snapshots):
    """Replace _probe_field_text: returns snapshots[0], [1], ... (the
    last repeats). Call order in insert_paste: field_before, post-
    keystroke, then one grace recheck when probe and signal disagree."""
    state = {"i": 0}

    def fake(*a, **k):
        out = snapshots[min(state["i"], len(snapshots) - 1)]
        state["i"] += 1
        return out

    monkeypatch.setattr(insertion, "_probe_field_text", fake)
    return state


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

    def wait_content_read(self, timeout, exclude_windows=(), interval=None,
                          since=None):
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


class TestPayloadLanded:
    """The pure field-snapshot judgement behind paste verification."""

    def test_blind_probe_is_unknown(self):
        assert insertion._payload_landed("x", None, "payload") is None
        assert insertion._payload_landed(None, None, "payload") is None

    def test_unchanged_field_is_false(self):
        assert insertion._payload_landed("stable", "stable", "p") is False

    def test_payload_present_is_true(self):
        assert insertion._payload_landed("a", "apayload", "payload") is True
        assert insertion._payload_landed(None, "apayload", "payload") is True

    def test_long_payload_judged_by_bounded_tail(self):
        payload = "A" * 3000
        after = "z" + "A" * 2500  # only the payload TAIL is visible
        assert insertion._payload_landed("z", after, payload) is True

    def test_absent_payload_is_false(self):
        assert insertion._payload_landed("a", "ab", "payload") is False


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
        # the verify loop POLLS the hold non-blockingly; the first poll
        # sees the content read and the blind field probe defers to it
        assert hold.read_calls, "the loop must poll the hold"
        assert set(hold.read_calls[0][1]) == hold.known  # quiesce windows excluded
        assert hold.released
        # the dictation NEVER hits the clipboard via xclip while the hold
        # serves it: the only xclip write is the restore of the previous
        writes = [c for c in runner["popen"] if c[0] == "xclip"]
        assert len(writes) == 1  # read-back matched: no retry write

    def test_verify_window_covers_proxied_pastes(self, runner, monkeypatch):
        hold = FakeHold(reader=None)  # proxy path: no observable read
        self._hold_run(monkeypatch, runner, hold,
                       plain=[b"previous clipboard"])  # read-back matches
        # live-measured 2026-09-14: the mutter clipboard proxy serves
        # VTE pastes ~0.5-1 s after the keystroke - the poll deadline
        # must cover it (0.6 s cut them off: F-34)
        assert REAL_VERIFY_TIMEOUT_S >= 1.0

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

    def test_field_rescue_prevents_double_type(self, runner, monkeypatch):
        # F-34: the paste landed but the selection signal missed it
        # (proxied by an already-known window) - the field probe rescues
        # the verification and the typed fallback never runs
        hold = FakeHold(reader=None)
        self._hold_run(monkeypatch, runner, hold)
        scripted_probe(monkeypatch, ["prompt$ ", "prompt$ " + "x" * 2000])
        payload = "x" * 2000
        assert insertion.insert_text(payload, full_cfg()) == "paste"
        assert hold.released
        typed = [c for c in runner["run"] if c[:2] == ["xdotool", "type"]]
        assert typed == []  # NOTHING typed: no duplicated transcript

    def test_field_rescue_paste_mode_no_raise(self, runner, monkeypatch):
        hold = FakeHold(reader=None)
        self._hold_run(monkeypatch, runner, hold)
        scripted_probe(monkeypatch, ["", "rescued payload"])
        assert insertion.insert_paste("rescued payload") is None

    def test_unchanged_field_downgrades_false_signal(self, runner, monkeypatch):
        # F-35a: a probe/proxy read fired the signal but the field never
        # received the text - the field outranks the signal, the paste is
        # declared unverified and the typed fallback delivers the text
        hold = FakeHold(reader=0x123)
        self._hold_run(monkeypatch, runner, hold)
        scripted_probe(monkeypatch, ["stable field", "stable field"])
        notices = []
        assert insertion.insert_text("x" * 2000, full_cfg(),
                                     on_notice=notices.append) == "typed"
        assert any("Paste did not land" in n for n in notices)
        assert any(c[:2] == ["xdotool", "type"]
                   for c in runner["run"])  # the text WAS delivered

    def test_proxy_path_polls_field_until_landed(self, runner, monkeypatch):
        # the proxy paste inserts LATE: several False polls, then the
        # payload appears - the loop must keep polling, not give up
        hold = FakeHold(reader=None)
        self._hold_run(monkeypatch, runner, hold)
        state = scripted_probe(
            monkeypatch, ["old ", "old ", "old ", "old payload"])
        assert insertion.insert_paste("payload") is None
        assert state["i"] >= 4  # at least: before + 3 field polls

    def test_signal_then_insert_race_still_verified(self, runner, monkeypatch):
        # the signal fired but the first post-keystroke probe raced the
        # app's read->insert step: the loop must keep polling until the
        # payload lands instead of downgrading on a momentary negative
        hold = FakeHold(reader=0x123)
        self._hold_run(monkeypatch, runner, hold)
        scripted_probe(monkeypatch, ["old ", "old ", "old payload"])
        assert insertion.insert_paste("payload") is None

    def test_agree_on_first_poll_is_immediate(self, runner, monkeypatch):
        # signal fired, field confirms on the first poll: no spin
        hold = FakeHold(reader=0x123)
        self._hold_run(monkeypatch, runner, hold)
        state = scripted_probe(monkeypatch, ["old ", "old payload"])
        assert insertion.insert_paste("payload") is None
        assert state["i"] == 2  # before + exactly one field poll

    def test_probe_blind_signal_decides(self, runner, monkeypatch):
        # probe None everywhere (unreadable field / no atspi): the
        # ICCCM content-read signal decides, exactly as before
        hold = FakeHold(reader=0x123)
        self._hold_run(monkeypatch, runner, hold)
        scripted_probe(monkeypatch, [None])
        assert insertion.insert_paste("text") is None  # signal verified


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

# ---------------------------------------------------------------------------
# ADR-0006: RemoteDesktop/EIS portal insertion (fake transports + fake
# portal keyboards; no D-Bus daemon, no compositor - the conftest drops
# DBUS_SESSION_BUS_ADDRESS so probes resolve "unavailable" hermetically).
# ---------------------------------------------------------------------------

class TestEisWire:
    """Pure wire-level pieces of fluidvoice.eis."""

    def test_string_encoding_matches_the_spec(self):
        # null string, empty string and "hello" byte layouts from the
        # protocol's types.md (little-endian)
        assert eis_mod.encode_string("")[:8] == b"\x01\x00\x00\x00" + b"\0" * 4
        hello = eis_mod.encode_string("hello")
        assert hello[:4] == b"\x06\x00\x00\x00"
        assert hello[4:10] == b"hello\0"
        assert len(hello) % 4 == 0

    def test_header_roundtrip(self):
        raw = eis_mod.build_message(0, 4, b"abcd")
        assert len(raw) == 20
        obj, length, opcode = eis_mod.parse_header(raw[:16])
        assert (obj, length, opcode) == (0, 20, 4)

    def test_ascii_table_covers_printables_with_shift_pairs(self):
        for ch in "abcdefghijklmnopqrstuvwxyz0123456789`-=[]\\;',./ ":
            assert eis_mod.encodable(ch), ch
        assert eis_mod.CHAR_TO_KEYS["A"] == (eis_mod.KEY_A, True)
        assert eis_mod.CHAR_TO_KEYS["a"] == (eis_mod.KEY_A, False)
        assert eis_mod.CHAR_TO_KEYS["!"][1] is True  # shifted 1
        assert eis_mod.CHAR_TO_KEYS["?"][1] is True  # shifted slash

    def test_non_ascii_not_encodable(self):
        assert not eis_mod.encodable("héllo")
        assert not eis_mod.encodable("你好")

    def test_key_spec_parse(self):
        assert eis_mod.parse_key_spec("ctrl+v") == [
            (29, True), (47, False)]
        assert eis_mod.parse_key_spec("enter") == [(28, False)]
        with pytest.raises(eis_mod.EisError):
            eis_mod.parse_key_spec("ctrl+f13")

    def test_type_text_rejects_out_of_layout_loudly(self):
        conn = eis_mod.EisConnection.__new__(eis_mod.EisConnection)
        with pytest.raises(eis_mod.EisEncodeError, match="clipboard"):
            conn.type_text("héllo")


class FakeEisServer:
    """Scripted EIS server: handshake, seat+device burst, then records
    every client message. Drives the real byte-level client."""

    def __init__(self):
        import socket as _socket
        self.client, self.srv = _socket.socketpair()
        self.sent_by_client = b""
        self._buf = b""
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self.thread.start()
        return self

    # -- wire helpers (server-side encodings) ---------------------------

    @staticmethod
    def _msg(obj, opcode, args=b""):
        import struct
        return struct.pack("<QII", obj, 16 + len(args), opcode) + args

    @staticmethod
    def _u32(v):
        import struct
        return struct.pack("<I", v)

    @staticmethod
    def _u64(v):
        import struct
        return struct.pack("<Q", v)

    @classmethod
    def _str(cls, s):
        raw = s.encode() + b"\0"
        return cls._u32(len(raw)) + raw + b"\0" * (-len(raw) % 4)

    def _read_message(self):
        import struct
        while len(self._buf) < 16:
            self._buf += self.srv.recv(65536)
        _, length, opcode = struct.unpack_from("<QII", self._buf, 0)
        while len(self._buf) < length:
            self._buf += self.srv.recv(65536)
        msg, self._buf = self._buf[:length], self._buf[length:]
        self.sent_by_client += msg
        return msg

    # -- the scripted conversation ---------------------------------------

    def _serve(self):
        CONN, SEAT, DEV, KBD = (0xFF00000000000001, 0xFF00000000000002,
                                0xFF00000000000003, 0xFF00000000000004)
        self.CONN, self.SEAT, self.DEV, self.KBD = CONN, SEAT, DEV, KBD
        srv = self.srv
        srv.sendall(self._msg(0, 0, self._u32(1)))          # handshake v1
        opcodes = []
        while True:
            import struct
            m = self._read_message()
            opcodes.append(struct.unpack_from("<I", m, 12)[0])
            if opcodes[-1] == 1:  # HS_FINISH
                break
        self.handshake_opcodes = opcodes
        # connection event: serial 7, conn object v1
        srv.sendall(self._msg(0, 2, self._u32(7) + self._u64(CONN)
                              + self._u32(1)))
        # seat + burst, then read bind
        srv.sendall(self._msg(CONN, 1, self._u64(SEAT) + self._u32(1)))
        srv.sendall(self._msg(SEAT, 1, self._str("seat0")))
        srv.sendall(self._msg(SEAT, 2, self._u64(4)
                              + self._str("ei_keyboard")))
        srv.sendall(self._msg(SEAT, 3))
        self.bind_message = self._read_message()
        # device burst + resume
        srv.sendall(self._msg(SEAT, 4, self._u64(DEV) + self._u32(1)))
        srv.sendall(self._msg(DEV, 1, self._str("virtual keyboard")))
        srv.sendall(self._msg(DEV, 2, self._u32(1)))        # virtual
        srv.sendall(self._msg(DEV, 5, self._u64(KBD)
                              + self._str("ei_keyboard") + self._u32(1)))
        srv.sendall(self._msg(DEV, 6))                       # done
        srv.sendall(self._msg(DEV, 7, self._u32(42)))        # resumed
        while True:
            try:
                data = srv.recv(65536)
            except OSError:
                return
            if not data:
                return
            self.sent_by_client += data

    def client_messages(self):
        """Parsed (obj, opcode, args) of everything the client sent."""
        import struct
        out, off = [], 0
        data = self.sent_by_client
        while off + 16 <= len(data):
            obj, length, opcode = struct.unpack_from("<QII", data, off)
            if off + length > len(data):
                break
            out.append((obj, opcode, data[off + 16:off + length]))
            off += length
        return out


def _wait_for(predicate, timeout_s: float = 2.0) -> None:
    """Poll until the predicate holds (the fake EIS server drains the
    client asynchronously); AssertionError on timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not reached before the test timeout")


class TestEisConnection:
    def _connect(self):
        server = FakeEisServer().start()
        conn = eis_mod.EisConnection(server.client)
        conn.connect()
        conn.bind_keyboard()
        return server, conn

    def test_handshake_requests_in_spec_order(self):
        server, conn = self._connect()
        # handshake_version FIRST, context_type(2=sender), name,
        # interface_version x4, finish LAST
        ops = server.handshake_opcodes
        assert ops[0] == 0 and ops[-1] == 1
        assert 2 in ops and 3 in ops and ops.count(4) == 4
        assert conn.connection_id == server.CONN
        assert conn.last_serial == 42  # connection's 7, then 'resumed'
        conn.close()

    def test_bind_uses_the_announced_keyboard_mask(self):
        import struct
        server, conn = self._connect()
        obj, opcode, args = server.client_messages()[
            len(server.handshake_opcodes)]
        assert (obj, opcode) == (server.SEAT, 1)  # ei_seat.bind
        assert struct.unpack("<Q", args)[0] == 4  # the announced mask
        assert conn.keyboard_id == server.KBD
        assert conn.device_id == server.DEV
        assert conn.last_serial == 42  # resumed carried the serial
        conn.close()

    def test_type_text_keycodes_and_framing(self):
        import struct
        server, conn = self._connect()
        conn.type_text("Hi!", delay_ms=0, sleep=lambda s: None)
        # wait for the emulation transaction to conclude (stop_emulating)
        _wait_for(lambda: any(
            o == server.DEV and op == 2 for o, op, a
            in server.client_messages()))
        msgs = server.client_messages()
        keys = [(struct.unpack("<II", a)) for o, op, a in msgs
                if op == 1 and o == server.KBD]
        # 'H' = shift+h(35), 'i' = 23, '!' = shift+2
        assert (42, 1) in keys and (35, 1) in keys
        assert (23, 1) in keys and (2, 1) in keys
        # press+release never share a frame: 3 chars x 2 frames exactly
        frames = [1 for o, op, a in msgs if op == 3 and o == server.DEV]
        assert len(frames) == 6
        # one emulation transaction around the burst
        starts = [1 for o, op, a in msgs
                  if op == 1 and o == server.DEV]
        stops = [1 for o, op, a in msgs if op == 2 and o == server.DEV]
        assert len(starts) == len(stops) == 1
        # last_serial from 'resumed' rides the transaction requests
        start_args = next(a for o, op, a in msgs
                          if op == 1 and o == server.DEV)
        assert struct.unpack("<II", start_args)[0] == 42
        conn.close()

    def test_tap_combo_modifier_order(self):
        import struct
        server, conn = self._connect()
        conn.tap_combo(eis_mod.parse_key_spec("ctrl+shift+v"),
                       sleep=lambda s: None)
        _wait_for(lambda: any(
            o == server.DEV and op == 2 for o, op, a
            in server.client_messages()))
        keys = [struct.unpack("<II", a) for o, op, a in server.client_messages()
                if op == 1 and o == server.KBD]
        assert keys == [(29, 1), (42, 1), (47, 1),
                        (47, 0), (42, 0), (29, 0)]
        conn.close()

    def test_close_sends_polite_disconnect(self):
        server, conn = self._connect()
        conn.close()
        _wait_for(lambda: any(
            o == server.CONN and op == 1 for o, op, a
            in server.client_messages()))


class FakeTransport:
    """The _PortalTransport surface, faked (type/press/encodable/alive)."""

    def __init__(self, fail=None):
        self.fail = fail
        self.typed: list = []
        self.combos: list = []
        self.closed = False

    def type_text(self, text, delay_ms):
        if self.fail:
            raise RuntimeError(self.fail)
        self.typed.append((text, delay_ms))

    def press_combo(self, keys):
        if self.fail:
            raise RuntimeError(self.fail)
        self.combos.append(keys)

    def encodable(self, text):
        return eis_mod.encodable(text)

    def alive(self):
        return not self.closed

    async def close_async(self):
        self.closed = True


class TestPortalKeyboardFacade:
    def teardown_method(self):
        eis_insertion.stop_shared()
        eis_insertion.reset_probe_cache()

    def test_start_type_press_stop(self):
        transport = FakeTransport()

        async def connector():
            return transport

        kb = eis_insertion.PortalKeyboard(log=lambda m: None,
                                          connector=connector)
        assert kb.start(timeout_s=5) is True
        assert kb.ready
        kb.type_text("hello", 8)
        kb.press_combo(eis_mod.parse_key_spec("ctrl+v"))
        assert transport.typed == [("hello", 8)]
        assert transport.combos == [[(29, True), (47, False)]]
        kb.stop()
        assert transport.closed and not kb.ready

    def test_connector_failure_degrades_to_false(self):
        async def connector():
            raise eis_insertion.EisUnavailable("portal says no")

        logs: list[str] = []
        kb = eis_insertion.PortalKeyboard(log=logs.append,
                                          connector=connector)
        assert kb.start(timeout_s=5) is False
        assert any("unavailable" in line for line in logs)
        kb.stop()  # idempotent
        with pytest.raises(eis_insertion.EisUnavailable):
            kb.type_text("x")

    def test_probe_without_a_bus_is_unavailable(self):
        # conftest drops DBUS_SESSION_BUS_ADDRESS and XDG_RUNTIME_DIR/bus
        # does not answer: the probe must refuse fast (never autolaunch)
        eis_insertion.reset_probe_cache()
        monkey = pytest.MonkeyPatch()
        monkey.setattr(eis_insertion, "_bus_connectable", lambda: False)
        try:
            verdict = eis_insertion.probe(refresh=True)
        finally:
            monkey.undo()
        assert verdict.ok is False
        assert "no session bus" in verdict.describe

    def test_ensure_shared_starts_once_and_stops(self, monkeypatch):
        started: list = []

        def fake_start(self, timeout_s=45.0):
            started.append(1)
            self._transport = FakeTransport()  # a live, ready transport
            return True

        monkeypatch.setattr(eis_insertion.PortalKeyboard, "start",
                            fake_start)
        monkeypatch.setattr(eis_insertion, "resolve_eis",
                            lambda cfg: True)
        cfg = {"insertion": {"wayland_tool": "auto"}}
        assert eis_insertion.ensure_shared(cfg, log=lambda m: None)
        assert eis_insertion.ensure_shared(cfg, log=lambda m: None)
        assert started == [1]  # one shared instance, reused
        eis_insertion.stop_shared()
        assert eis_insertion.shared_keyboard() is None

    def test_ensure_shared_respects_external_tool_preference(self):
        # explicit wtype/ydotool: the portal keyboard never starts even
        # when the probe would pass
        assert eis_insertion.resolve_eis(
            {"insertion": {"wayland_tool": "wtype"}}) is False


class TestWaylandEisLadder:
    """insertion.py x eis_insertion seam: the ADR-0006 ladder with the
    portal keyboard faked at module level."""

    @pytest.fixture()
    def eis_env(self, monkeypatch):
        """Wayland session + a 'running' shared EIS keyboard recorder."""
        set_wl_session(monkeypatch)
        recorder = {"typed": [], "keys": [], "encodable": True,
                    "preferred": True}

        def fake_type(text, delay_ms=0):
            recorder["typed"].append((text, delay_ms))

        def fake_press(spec):
            recorder["keys"].append(spec)

        monkeypatch.setattr(insertion, "_eis_preferred",
                            lambda cfg: recorder["preferred"])
        monkeypatch.setattr(insertion, "_eis_encodable",
                            lambda text: recorder["encodable"])
        monkeypatch.setattr(eis_insertion, "type_text", fake_type)
        monkeypatch.setattr(eis_insertion, "press_key", fake_press)
        return recorder

    def test_typed_via_eis_when_preferred(self, eis_env, wl_runner):
        eis_env.update(encodable=True)
        assert insertion.insert_text("hello portal", wl_cfg()) == "typed"
        assert eis_env["typed"] == [("hello portal", 8)]
        # no external tool spawn at all
        assert wl_runner["run"] == [] and wl_runner["popen"] == []

    def test_out_of_layout_unicode_routes_to_clipboard_paste(self,
                                                             eis_env,
                                                             wl_runner):
        # the sandbox case: EIS is the only typing backend (no
        # wtype/ydotool) - Unicode it cannot type goes out as
        # clipboard + EIS-sent Ctrl+V (the ADR-0006 fallback)
        eis_env.update(encodable=False)
        wl_runner["set_installed"]({"wl-copy", "wl-paste"})
        assert insertion.insert_text("héllo", wl_cfg()) == "paste"
        assert any(c[0] == "wl-copy" for c in wl_runner["popen"])
        assert eis_env["keys"] == ["ctrl+v"]

    def test_out_of_layout_unicode_types_via_external_tool(self, eis_env,
                                                           wl_runner):
        # host case: wtype is installed and types Unicode natively -
        # preferred over the clipboard dance
        eis_env.update(encodable=False)
        assert insertion.insert_text("héllo", wl_cfg()) == "typed"
        assert wl_runner["run"] == [["wtype", "-d", "8", "héllo"]]
        assert eis_env["keys"] == []

    def test_eis_failure_falls_to_wtype(self, eis_env, wl_runner,
                                        monkeypatch):
        def failing_type(text, delay_ms=0):
            raise eis_insertion.EisUnavailable("EIS died mid-take")

        monkeypatch.setattr(eis_insertion, "type_text", failing_type)
        assert insertion.insert_text("hello", wl_cfg()) == "typed"
        assert wl_runner["run"] == [["wtype", "-d", "8", "hello"]]

    def test_not_preferred_keeps_wtype_path(self, eis_env, wl_runner):
        eis_env.update(preferred=False)
        assert insertion.insert_text("hello", wl_cfg()) == "typed"
        assert wl_runner["run"] == [["wtype", "-d", "8", "hello"]]
        assert eis_env["typed"] == []

    def test_capability_report_names_eis(self, eis_env):
        insertion.reset_capability_cache()
        report = insertion.check_insertion_capability(wl_cfg())
        assert report.typing_tool == "eis"

    def test_press_key_via_eis(self, eis_env):
        insertion.press_key("enter")
        assert eis_env["keys"] == ["enter"]

    def test_explicit_eis_preference_reported(self, wl_runner):
        insertion.reset_capability_cache()
        report = insertion.check_insertion_capability(
            wl_cfg(wayland_tool="eis"))
        # probe is unavailable headless -> falls to the external ladder
        assert report.typing_tool in ("eis", "wtype")


class TestWaylandRegistryProbe:
    """fluidvoice.wayland_probe: the zwp gate that replaces
    desktop-name sniffing."""

    @staticmethod
    def _wl_string(interface):
        raw = interface.encode() + b"\0"
        return struct.pack("<I", len(raw)) + raw + b"\0" * (-len(raw) % 4)

    @classmethod
    def _global(cls, name, interface, version):
        # object-id (registry=2), opcode 0, size, then the args
        args = struct.pack("<I", name) + cls._wl_string(interface) \
            + struct.pack("<I", version)
        size = 8 + len(args)
        return struct.pack("<IHH", 2, 0, size) + args

    @classmethod
    def _callback_done(cls, data=0):
        return struct.pack("<IHH", 3, 0, 12) + struct.pack("<I", data)

    def test_parse_globals_reads_interfaces_and_versions(self):
        stream = (self._global(10, "wl_shm", 1)
                  + self._global(11, "zwp_virtual_keyboard_manager_v1", 1)
                  + self._global(12, "wl_seat", 9)
                  + self._global(11, "zwp_virtual_keyboard_manager_v1", 1)
                  + self._callback_done())
        globals_ = wayland_probe.parse_globals(stream)
        assert globals_["wl_shm"] == 1
        assert globals_["wl_seat"] == 9
        assert wayland_probe.VIRTUAL_KEYBOARD_GLOBAL in globals_
        # non-registry events (the callback) are ignored, not fatal
        assert "wl_callback" not in globals_

    def test_probe_virtual_keyboard_uses_the_socket_verdict(self,
                                                            monkeypatch):
        wayland_probe.reset_probe_cache()
        monkeypatch.setattr(
            wayland_probe, "list_globals",
            lambda timeout_s=2.0, path=None:
                {"zwp_virtual_keyboard_manager_v1": 1})
        assert wayland_probe.probe_virtual_keyboard() is True
        # cached: a changed compositor answer needs refresh=True
        monkeypatch.setattr(wayland_probe, "list_globals",
                            lambda timeout_s=2.0, path=None: {})
        assert wayland_probe.probe_virtual_keyboard() is True
        assert wayland_probe.probe_virtual_keyboard(refresh=True) is False
        wayland_probe.reset_probe_cache()

    def test_probe_unreachable_compositor_is_false(self, monkeypatch):
        wayland_probe.reset_probe_cache()
        monkeypatch.setattr(wayland_probe, "list_globals",
                            lambda timeout_s=2.0, path=None: None)
        assert wayland_probe.probe_virtual_keyboard() is False

    def test_socket_path_resolution(self, monkeypatch, tmp_path):
        sock = tmp_path / "wayland-1"
        sock.write_bytes(b"")
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
        assert wayland_probe._socket_path() == str(sock)
        monkeypatch.setenv("WAYLAND_DISPLAY", str(sock))  # absolute
        assert wayland_probe._socket_path() == str(sock)
        monkeypatch.delenv("WAYLAND_DISPLAY")
        assert wayland_probe._socket_path() is None
