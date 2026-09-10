from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from fluidvoice import cli, paths, ui
from fluidvoice.hotkey import MODIFIER_MASKS, HotkeyError, resolve_keysym


class TestResolveKeysym:
    def test_friendly_aliases(self):
        assert resolve_keysym("Right_Control") == resolve_keysym("Control_R")
        assert resolve_keysym("right_ctrl") == resolve_keysym("Control_R")
        assert resolve_keysym("Right_Alt") == resolve_keysym("Alt_R")
        assert resolve_keysym("right_option") == resolve_keysym("Alt_R")
        assert resolve_keysym("Left_Control") == resolve_keysym("Control_L")
        assert resolve_keysym("esc") == resolve_keysym("Escape")

    def test_plain_names(self):
        assert resolve_keysym("F9") != 0
        assert resolve_keysym("space") != 0
        # multi-word normalization: "page up" -> "Page_Up"
        assert resolve_keysym("page up") == resolve_keysym("Page_Up")
        assert resolve_keysym("right control") == resolve_keysym("Control_R")

    def test_unknown_raises(self):
        with pytest.raises(HotkeyError, match="unknown key name"):
            resolve_keysym("make_me_a_sandwich")

    def test_modifier_masks_complete(self):
        assert set(MODIFIER_MASKS) == {"ctrl", "alt", "shift", "super"}
        assert all(v != 0 for v in MODIFIER_MASKS.values())


class TestUI:
    def test_notify_without_tool_is_silent(self, monkeypatch):
        ran = []
        monkeypatch.setattr(ui.shutil, "which", lambda n: None)
        monkeypatch.setattr(ui.subprocess, "run", lambda *a, **k: ran.append(a))
        ui.notify("t", "b", enabled=True)
        assert ran == []

    def test_notify_calls_notify_send(self, monkeypatch):
        ran = []
        monkeypatch.setattr(ui.shutil, "which", lambda n: "/usr/bin/" + n)
        monkeypatch.setattr(ui.subprocess, "run", lambda *a, **k: ran.append(a))
        ui.notify("Title", "Body", timeout_ms=1500, enabled=True)
        assert ran and ran[0][0][:3] == ["notify-send", "-a", "SayItErmano"]

    def test_notify_disabled(self, monkeypatch):
        ran = []
        monkeypatch.setattr(ui.shutil, "which", lambda n: "/usr/bin/" + n)
        monkeypatch.setattr(ui.subprocess, "run", lambda *a, **k: ran.append(a))
        ui.notify("t", enabled=False)
        assert ran == []

    def test_play_sound_unknown_name_noop(self, monkeypatch):
        monkeypatch.setattr(ui.shutil, "which", lambda n: "/usr/bin/" + n)
        ui.play_sound("explosion", enabled=True)  # must not raise

    def test_play_sound_disabled(self, monkeypatch):
        monkeypatch.setattr(ui.shutil, "which", lambda n: "/usr/bin/" + n)
        ui.play_sound("start", enabled=False)  # must not raise


class TestCliConfig:
    def test_config_init(self, tmp_path, monkeypatch, capsys):
        target = tmp_path / "config.toml"
        monkeypatch.setattr(paths, "config_file", lambda: target)
        assert cli.main(["config", "init"]) == 0
        assert "Right_Control" in target.read_text()
        out = capsys.readouterr().out
        assert str(target) in out

    def test_config_path(self, tmp_path, monkeypatch, capsys):
        target = tmp_path / "config.toml"
        monkeypatch.setattr(paths, "config_file", lambda: target)
        cli.main(["config", "path"])
        assert str(target) in capsys.readouterr().out

    def test_config_print(self, tmp_path, monkeypatch, capsys):
        target = tmp_path / "config.toml"
        monkeypatch.setattr(paths, "config_file", lambda: target)
        target.write_text('[hotkey]\nkey = "F9"\n')
        cli.main(["config", "print"])
        assert 'key = "F9"' in capsys.readouterr().out

    def test_config_print_missing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(paths, "config_file",
                            lambda: tmp_path / "missing.toml")
        cli.main(["config", "print"])
        assert "no config file" in capsys.readouterr().out


class TestCliHistory:
    def test_history_output(self, monkeypatch, capsys):
        from fluidvoice import history
        entries = [{"ts": 1700000000, "text": "hello", "ai": True},
                   {"ts": 1700000001, "text": "world", "ai": False}]
        monkeypatch.setattr(history, "tail", lambda n=10: entries[:n])
        assert cli.main(["history", "-n", "2"]) == 0
        out = capsys.readouterr().out
        assert "[AI]: hello" in out
        assert "world" in out and "[AI]" not in out.split("world")[0].split("\n")[-1]

    def test_history_export(self, tmp_path, monkeypatch, capsys):
        from fluidvoice import history
        seen = {}

        def fake_export(path, on_note=None):
            seen["path"] = path
            if on_note:
                on_note("skipped missing audio: x.wav")
            return 3

        monkeypatch.setattr(history, "export_zip", fake_export)
        target = tmp_path / "t.zip"
        assert cli.main(["history", "--export", str(target)]) == 0
        assert seen["path"] == target
        out = capsys.readouterr()
        assert "exported 3 entries" in out.out
        assert "skipped missing audio" in out.err  # notes go to stderr

    def test_history_export_oserror_fails(self, tmp_path, monkeypatch, capsys):
        from fluidvoice import history

        def broken(path, on_note=None):
            raise OSError("disk full")

        monkeypatch.setattr(history, "export_zip", broken)
        assert cli.main(["history", "--export", str(tmp_path / "t.zip")]) == 1
        err = capsys.readouterr().err
        assert "error: disk full" in err

    def test_status_prints_today(self, monkeypatch, capsys):
        from fluidvoice import control
        monkeypatch.setattr(
            control, "request",
            lambda action, **kw: {"ok": True, "recording": False,
                                  "today": {"dictations": 2, "seconds": 75.0,
                                            "words": 6}})
        assert cli.main(["status"]) == 0
        out = capsys.readouterr().out
        assert "stopped" in out
        assert "today: 2 dictations, 1:15 minutes, 6 words" in out

    def test_toggle_without_today_unchanged(self, monkeypatch, capsys):
        from fluidvoice import control
        monkeypatch.setattr(control, "request",
                            lambda action, **kw: {"ok": True, "recording": False})
        assert cli.main(["toggle"]) == 0
        assert capsys.readouterr().out.strip() == "stopped"

    def test_no_args_prints_help(self, capsys):
        assert cli.main([]) == 0
        assert "daemon" in capsys.readouterr().out


class TestCliToggleNoDaemon:
    def test_toggle_without_daemon_errors(self, monkeypatch, capsys):
        monkeypatch.setattr(paths, "socket_path",
                            lambda: Path("/nonexistent/fluidvoice.sock"))
        assert cli.main(["toggle"]) == 1
        assert "daemon not running" in capsys.readouterr().err


class TestHotkeyListenerConstruction:
    def test_cancel_key_wiring(self):
        from fluidvoice.hotkey import HotkeyListener
        listener = HotkeyListener("F9", [], "toggle", on_toggle=lambda: None,
                                  on_cancel=lambda: None, cancel_key="Escape")
        assert listener.cancel_key == "Escape"

    def test_cancel_key_resolution(self):
        from fluidvoice.hotkey import HotkeyListener
        # omitted and "" both mean the macOS default (old templates wrote ""
        # into saved configs - they must not silently lose Escape on upgrade)
        assert HotkeyListener("F9", [], "toggle", on_toggle=lambda: None)._resolve_cancel() == "Escape"
        assert HotkeyListener("F9", [], "toggle", on_toggle=lambda: None,
                              cancel_key="")._resolve_cancel() == "Escape"
        assert HotkeyListener("F9", [], "toggle", on_toggle=lambda: None,
                              cancel_key="none")._resolve_cancel() == ""
        assert HotkeyListener("F9", [], "toggle", on_toggle=lambda: None,
                              cancel_key="F10 ")._resolve_cancel() == "F10"

    def test_modifier_mask_summed(self):
        from fluidvoice.hotkey import MODIFIER_MASKS, HotkeyListener
        listener = HotkeyListener("space", ["ctrl", "shift"], "hold", on_toggle=lambda: None)
        assert listener._mods == MODIFIER_MASKS["ctrl"] | MODIFIER_MASKS["shift"]

    def test_set_recording_toggles_cancel_grab_state(self):
        from fluidvoice.hotkey import HotkeyListener
        listener = HotkeyListener("F9", [], "toggle", on_toggle=lambda: None,
                                  cancel_key="Escape")
        assert listener._want_cancel is False
        listener.set_recording(True)
        assert listener._want_cancel is True
        listener.set_recording(False)
        assert listener._want_cancel is False

    def test_config_cancel_key_defaults_to_escape(self):
        from fluidvoice.config import DEFAULTS
        assert DEFAULTS["hotkey"]["cancel_key"] == "Escape"


# ---------------------------------------------------------------------------
# Hold-mode key passthrough (classify / replay / hold cycle) — no X server:
# plain fakes with call recording.
# ---------------------------------------------------------------------------

class TestHoldClassification:
    """classify_hold_event is a pure table: how _hold_cycle treats one
    grabbed keyboard event."""

    def test_classification_table(self):
        from Xlib import X

        from fluidvoice.hotkey import (
            _HOLD_ABORT,
            _HOLD_END,
            _HOLD_IGNORE,
            _HOLD_REPLAY,
            classify_hold_event,
        )
        hotkey, escape, other, modifier = 67, 9, 38, 50  # F9, Esc, 'a', Shift
        cases = [
            # hotkey release ends the hold
            (X.KeyRelease, hotkey, escape, _HOLD_END),
            # escape press cancels the recording
            (X.KeyPress, escape, escape, _HOLD_ABORT),
            # any other press/release (modifiers included) is replayed
            (X.KeyPress, other, escape, _HOLD_REPLAY),
            (X.KeyRelease, other, escape, _HOLD_REPLAY),
            (X.KeyPress, modifier, escape, _HOLD_REPLAY),
            (X.KeyRelease, modifier, escape, _HOLD_REPLAY),
            # hotkey press (auto-repeat) and escape release are ignored
            (X.KeyPress, hotkey, escape, _HOLD_IGNORE),
            (X.KeyRelease, escape, escape, _HOLD_IGNORE),
            # non-key events / missing fields are ignored
            (X.MappingNotify, hotkey, escape, _HOLD_IGNORE),
            (None, hotkey, escape, _HOLD_IGNORE),
            (X.KeyPress, None, escape, _HOLD_IGNORE),
            (X.KeyRelease, None, escape, _HOLD_IGNORE),
            # cancel disabled: escape is a normal (replayed) key
            (X.KeyPress, escape, None, _HOLD_REPLAY),
            (X.KeyRelease, escape, None, _HOLD_REPLAY),
        ]
        for etype, detail, esc, expected in cases:
            got = classify_hold_event(etype, detail, hotkey, esc)
            assert got == expected, (etype, detail, esc, got, expected)




class _FakeRoot:
    def __init__(self, display):
        self._display = display
        self.id = 0x4f
        self.escape_grab_error = None

    def grab_key(self, keycode, modifiers, owner_events, pointer_mode,
                 keyboard_mode, onerror=None):
        # onerror: the listener now routes every grab through a per-request
        # handler (BadAccess never raises through the real grab_key)
        if self.escape_grab_error and keycode == self._display.escape_keycode:
            raise self.escape_grab_error
        self._display.calls.append(f"grab_key:{keycode}:{modifiers}")

    def ungrab_key(self, keycode, modifiers):
        self._display.calls.append(f"ungrab_key:{keycode}:{modifiers}")


class _HoldEvent:
    def __init__(self, etype=None, detail=None):
        self.type = etype
        self.detail = detail


class _HoldFakeDisplay:
    """Scripted keymap + event queue for _hold_cycle (no X server)."""

    def __init__(self, events=(), keymaps=(), ungrab_error=None,
                 escape_keycode=9):
        self.events = list(events)
        self.ungrab_error = ungrab_error
        self.escape_keycode = escape_keycode
        # keymaps: one 32-int state per query_keymap() call (last repeats)
        self.keymaps = [self._keymap(kcs) for kcs in keymaps]
        self.calls = []
        self.root = _FakeRoot(self)
        self.close_pending = False  # pending_events() raises when True

    @staticmethod
    def _keymap(down_keycodes):
        km = [0] * 32
        for kc in down_keycodes:
            km[kc // 8] |= 1 << (kc % 8)
        return km

    def screen(self):
        return SimpleNamespace(root=self.root)

    def sync(self):
        self.calls.append("sync")

    def ungrab_keyboard(self, time):
        self.calls.append(f"ungrab_keyboard:{time}")
        if self.ungrab_error:
            raise self.ungrab_error

    def query_keymap(self):
        self.calls.append("query_keymap")
        if len(self.keymaps) > 1:
            return self.keymaps.pop(0)
        return self.keymaps[0] if self.keymaps else [0] * 32

    def pending_events(self):
        if self.close_pending:
            raise RuntimeError("display closed")
        return len(self.events)

    def next_event(self):
        if not self.events:
            raise RuntimeError("display closed")
        ev = self.events.pop(0)
        self.calls.append(f"next_event:{ev.type}:{ev.detail}")
        return ev


def _hold_listener(toggles, cancels):
    from fluidvoice.hotkey import HotkeyListener
    listener = HotkeyListener("F9", [], "hold",
                              on_toggle=lambda: toggles.append(1),
                              on_cancel=lambda: cancels.append(1))
    listener._escape_keycode = 9   # XK_Escape keycode (as setup() would set)
    listener._keycode = 67         # F9
    return listener


class TestHoldCycle:
    """Native-passthrough hold: the keyboard is FREED (passive-grab
    activation released), release detection polls query_keymap, a passive
    Escape grab is armed just for the hold, the hotkey is re-armed after."""

    HOTKEY = 67   # F9
    ESCAPE = 9

    def _run(self, events=(), keymaps=(), ungrab_error=None,
             escape_keycode=9, close_pending=False, escape_grab_error=None):
        toggles, cancels = [], []
        listener = _hold_listener(toggles, cancels)
        listener._escape_keycode = escape_keycode
        d = _HoldFakeDisplay(events, keymaps, ungrab_error,
                             escape_keycode=escape_keycode)
        d.close_pending = close_pending
        d.root.escape_grab_error = escape_grab_error
        listener._display = d  # so the teardown re-arm hits the fake root
        listener._hold_cycle(d, self.HOTKEY)
        return listener, toggles, cancels, d

    def test_release_detected_via_keymap_ends_hold(self):
        from Xlib import X
        _, toggles, cancels, d = self._run(keymaps=[(self.HOTKEY,), (self.HOTKEY,), ()])
        assert toggles == [1, 1]          # start + stop (transcribe) path
        assert cancels == []
        # the passive-grab activation was released (native passthrough)
        assert "ungrab_keyboard:0" in d.calls
        assert f"ungrab_key:{self.HOTKEY}:{X.AnyModifier}" in d.calls
        # Escape armed just for the hold, disarmed at the end
        assert f"grab_key:{self.ESCAPE}:{X.AnyModifier}" in d.calls
        assert f"ungrab_key:{self.ESCAPE}:{X.AnyModifier}" in d.calls
        # the hotkey passive grab is re-armed for the next dictation
        assert f"grab_key:{self.HOTKEY}:0" in d.calls
        # and any escape-grab activation released at teardown
        assert d.calls.count("ungrab_keyboard:0") >= 2

    def test_escape_press_aborts_and_cancels(self):
        from Xlib import X
        _, toggles, cancels, _ = self._run(
            events=[_HoldEvent(X.KeyPress, self.ESCAPE)],
            keymaps=[(self.HOTKEY,)])
        assert cancels == [1]
        assert toggles == [1]             # started, never stopped

    def test_auto_repeat_does_not_end_hold(self):
        # the keymap bit stays set through the ~30 Hz synthetic repeat
        # pairs; only a real release (bit clear) ends the hold
        _, toggles, cancels, d = self._run(
            keymaps=[(self.HOTKEY,)] * 5 + [()])
        assert toggles == [1, 1]
        assert cancels == []
        assert d.calls.count("query_keymap") >= 5

    def test_display_closed_ends_hold_via_stop(self):
        _, toggles, cancels, _ = self._run(keymaps=[(self.HOTKEY,)],
                                           close_pending=True)
        assert toggles == [1, 1]          # stop path, not cancel
        assert cancels == []

    def test_ungrab_failure_degrades_to_swallow(self):
        # keyboard stays grabbed -> typed keys are delivered to us and
        # silently drained (the pre-passthrough behavior); hold still works
        from Xlib import X
        _, toggles, cancels, _ = self._run(
            events=[_HoldEvent(X.KeyPress, 38), _HoldEvent(X.KeyRelease, 38)],
            keymaps=[(self.HOTKEY,), (self.HOTKEY,), ()],
            ungrab_error=RuntimeError("ungrab failed"))
        assert toggles == [1, 1]
        assert cancels == []

    def test_escape_grab_failure_tolerated(self):
        _, toggles, cancels, _ = self._run(
            keymaps=[(self.HOTKEY,), ()],
            escape_grab_error=RuntimeError("cannot grab escape"))
        assert toggles == [1, 1]
        assert cancels == []

    def test_no_escape_keycode_skips_escape_grab(self):
        _, toggles, cancels, d = self._run(keymaps=[(self.HOTKEY,), ()],
                                           escape_keycode=None)
        assert toggles == [1, 1]
        assert not any(c.startswith(f"grab_key:{self.ESCAPE}:") for c in d.calls)

    def test_hotkey_and_typed_events_drained_not_aborting(self):
        # in degraded (still-grabbed) mode these arrive at us: repeats of
        # the held hotkey and typed keys must never cancel or mis-end
        from Xlib import X
        _, toggles, cancels, d = self._run(
            events=[_HoldEvent(X.KeyPress, self.HOTKEY),     # repeat press
                    _HoldEvent(X.KeyRelease, self.HOTKEY),   # repeat release
                    _HoldEvent(X.KeyPress, 38),              # typed 'a'
                    _HoldEvent(X.KeyRelease, 38)],
            keymaps=[(self.HOTKEY,), (self.HOTKEY,), ()])
        assert toggles == [1, 1]
        assert cancels == []
        assert any(c.startswith(f"next_event:{X.KeyPress}:{self.HOTKEY}") for c in d.calls)
