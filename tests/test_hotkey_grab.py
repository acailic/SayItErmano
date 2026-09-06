"""Hotkey grab self-healing: error routing + retry state machine, no X server.

python-xlib never raises BadAccess through grab_key - it queues the error
and hands it to that request's onerror callback when the socket is next
read (a truthy return suppresses the printing default handler). These
tests fake exactly that contract: FakeRoot.grab_key records the call and
queues a refusal when the combo is "held"; FakeDisplay.sync() pumps the
queued callbacks the way the real Display delivers errors.
"""
from __future__ import annotations

from types import SimpleNamespace

import copy
from pathlib import Path

import pytest
from Xlib import XK

from fluidvoice import daemon as dm
from fluidvoice import hotkey
from fluidvoice.config import DEFAULTS
from fluidvoice.hotkey import _LOCK_MASKS, HotkeyListener

F9_KEYCODE = 67
ESCAPE_KEYCODE = 9


class FakeGrabError(Exception):
    """Stands in for Xlib's BadAccess (grab already held)."""


class FakeRoot:
    def __init__(self, display):
        self._display = display

    def grab_key(self, key, modifiers, owner_events, pointer_mode,
                 keyboard_mode, onerror=None):
        assert onerror is not None, "grab_key issued without onerror routing"
        self._display.calls.append(("grab_key", key, modifiers))
        if (key, modifiers) in self._display.refused:
            self._display.pending_errors.append((onerror, FakeGrabError()))

    def ungrab_key(self, key, modifiers, onerror=None):
        self._display.calls.append(("ungrab_key", key, modifiers))


class FakeDisplay:
    """The real-Display contract the listener uses: keysym mapping,
    screen().root, and sync() pumping queued onerror callbacks."""

    def __init__(self, refused=()):
        self.refused = set(refused)
        self.pending_errors: list = []
        self.calls: list = []
        self.closed = False
        self._root = FakeRoot(self)

    def keysym_to_keycode(self, keysym):
        if keysym == XK.string_to_keysym("F9"):
            return F9_KEYCODE
        if keysym == XK.string_to_keysym("Escape"):
            return ESCAPE_KEYCODE
        return 0

    def screen(self):
        return SimpleNamespace(root=self._root)

    def sync(self):
        pending, self.pending_errors = self.pending_errors, []
        for onerror, err in pending:
            onerror(err, None)

    def close(self):
        self.closed = True


def all_combos(keycode: int, mods: int = 0) -> set:
    return {(keycode, mods | extra) for extra in _LOCK_MASKS}


def grab_calls(display) -> list:
    return [c for c in display.calls if c[0] == "grab_key"]


@pytest.fixture()
def make(monkeypatch):
    """Listener wired to a fake Display; returns (listener, fake, lines, events)."""
    def _make(refused=(), key="F9", mods=None):
        fake = FakeDisplay(refused=refused)
        lines: list[str] = []
        events: list[bool] = []
        monkeypatch.setattr(hotkey, "Display", lambda name=None: fake)
        listener = HotkeyListener(key, mods or [], "toggle",
                                  on_toggle=lambda: None,
                                  log=lines.append,
                                  on_grab_change=events.append)
        return listener, fake, lines, events
    return _make


class TestGrabRouting:
    def test_setup_all_refused_reports_not_raises(self, make):
        # 8x BadAccess at startup must surface as state, not a crash nor a
        # silent keyless "ready" (the live 2026-09-04 incident)
        listener, fake, lines, _ = make(refused=all_combos(F9_KEYCODE))
        summary = listener.setup()
        assert listener.hotkey_grabbed is False
        assert summary and "keycode 67" in summary[0]
        assert len(grab_calls(fake)) == 8
        assert all(listener._combo_attempts[c] == 1
                   for c in all_combos(F9_KEYCODE))
        assert listener._refuse_warned is True  # startup latch: daemon owns it
        assert not any("still refused" in m for m in lines)  # no cap-WARN yet

    def test_every_combo_grab_carries_onerror(self, make):
        listener, fake, _, _ = make()
        listener.setup()
        assert len(grab_calls(fake)) == len(_LOCK_MASKS)  # fake asserts onerror

    def test_error_handler_returns_truthy_and_records(self, make):
        # a falsy return would fall through to python-xlib's printing
        # default handler - the refusal would be noise, not data
        listener, _, _, _ = make()
        handler = listener._make_grab_onerror((F9_KEYCODE, 0))
        assert handler(FakeGrabError(), None) == 1
        assert listener._combo_ok[(F9_KEYCODE, 0)] is False
        assert listener._combo_attempts[(F9_KEYCODE, 0)] == 1

    def test_healthy_setup_grabbed_true(self, make):
        listener, fake, _, events = make()
        listener.setup()
        assert listener.hotkey_grabbed is True
        assert events == [True]  # boundary crossing unknown -> healthy


class TestRetryLoop:
    def test_healthy_steady_state_zero_x_traffic(self, make):
        listener, fake, _, _ = make()
        listener.setup()
        before = len(grab_calls(fake))
        for _ in range(5):
            listener._sync_hotkey_grab()
        assert len(grab_calls(fake)) == before

    def test_retry_then_recovery_when_holder_releases(self, make):
        listener, fake, lines, events = make(refused=all_combos(F9_KEYCODE))
        listener.setup()
        # every tick retries only the missing combos (all 8 here)
        listener._sync_hotkey_grab()
        assert len(grab_calls(fake)) == 16
        assert all(listener._combo_attempts[c] == 2
                   for c in all_combos(F9_KEYCODE))
        # the conflicting holder lets go -> next sync re-takes the grab
        fake.refused.clear()
        listener._sync_hotkey_grab()
        assert listener.hotkey_grabbed is True
        assert lines.count("hotkey grab recovered") == 1
        assert all(listener._combo_attempts.get(c, 0) == 0
                   for c in all_combos(F9_KEYCODE))
        assert listener._refuse_warned is False  # fresh WARN cycle armed
        assert events == [True]  # surfaces told about the recovery
        # and the recovered steady state again costs nothing
        calls = len(grab_calls(fake))
        listener._sync_hotkey_grab()
        assert len(grab_calls(fake)) == calls

    def test_partial_refusal_retries_only_missing_masks(self, make):
        listener, fake, _, _ = make(refused={(F9_KEYCODE, 0)})
        listener.setup()
        assert listener.hotkey_grabbed is False
        listener._sync_hotkey_grab()
        retried = [c for c in grab_calls(fake)[8:]]
        assert retried == [("grab_key", F9_KEYCODE, 0)]

    def test_warn_caps_once_per_idle_period(self, make):
        # blocked at startup: the daemon owns the first WARN (setup latches
        # _refuse_warned), the listener stays quiet while retries continue
        listener, fake, lines, _ = make(refused=all_combos(F9_KEYCODE))
        listener.setup()
        for _ in range(12):  # > _MAX_GRAB_ATTEMPTS ticks while still blocked
            listener._sync_hotkey_grab()
        assert not any("still refused" in m for m in lines)
        # a new recording-idle period re-arms the cap-WARN latch
        listener.set_recording(True)
        listener.set_recording(False)
        listener._sync_hotkey_grab()
        warns = [m for m in lines if "still refused" in m]
        assert len(warns) == 1 and "attempts" in warns[0]
        for _ in range(3):
            listener._sync_hotkey_grab()  # still refused: still quiet
        assert len([m for m in lines if "still refused" in m]) == 1

    def test_warn_after_ten_distinct_attempts_from_healthy(self, make):
        # blocked only later (holder appeared between sessions): no startup
        # latch, so the cap-WARN fires at exactly the 10th distinct attempt
        listener, fake, lines, _ = make()
        listener.setup()
        assert listener.hotkey_grabbed is True
        # simulate the holder taking the key while the daemon is down, then
        # a late error pumping through the loop flips the combo missing
        fake.refused = all_combos(F9_KEYCODE)
        listener._combo_ok.clear()
        for _ in range(9):
            listener._sync_hotkey_grab()
        assert not any("still refused" in m for m in lines)
        listener._sync_hotkey_grab()  # 10th distinct attempt per combo
        warns = [m for m in lines if "still refused" in m]
        assert len(warns) == 1 and "after 10 attempts" in warns[0]

    def test_sync_is_silent_when_display_gone(self, make):
        listener, fake, _, _ = make()
        listener.setup()
        fake.refused = all_combos(F9_KEYCODE)
        fake.screen = None  # a closing display must not kill the loop thread
        listener._sync_hotkey_grab()  # no exception

    def test_no_keycode_no_op(self, make):
        listener, _, _, _ = make()
        listener._display = None
        listener._sync_hotkey_grab()  # no exception, nothing to do


class TestCancelKeyGrabRouting:
    def test_cancel_grab_refusal_is_routed_not_printed(self, make, capsys):
        # the cancel-key grab rides _grab too: its BadAccess must become
        # data (and eventually the cap-WARN), never an "X protocol error"
        listener, fake, lines, _ = make(refused=all_combos(ESCAPE_KEYCODE))
        listener.setup()
        listener.set_recording(True)
        listener._sync_cancel_grab()
        assert not capsys.readouterr().out  # nothing printed to stdout
        assert listener._cancel_grabbed is True  # state machine untouched
        assert all(listener._combo_attempts[c] == 1
                   for c in all_combos(ESCAPE_KEYCODE))

    def test_cancel_grab_warn_uses_injected_log(self, make):
        listener, fake, lines, _ = make()
        listener.setup()
        listener.set_recording(True)

        def broken(*_a, **_k):
            raise RuntimeError("display closed")

        fake._root.grab_key = broken  # connection error -> the except branch
        listener._sync_cancel_grab()
        assert any("cancel key" in m and "WARN" in m for m in lines)


# ---------------------------------------------------------------------------
# Daemon surfaces: status field, tooltip suffix, startup WARN + notification
# ---------------------------------------------------------------------------

class _NoopRecorder:
    def start(self, path):
        pass

    def stop(self):
        return None

    def cancel(self):
        pass


class _StubListener:
    """Stands in for HotkeyListener in daemon wiring tests."""
    grabbed = True

    def __init__(self, **kw):
        self.hotkey_grabbed = _StubListener.grabbed
        self.summary = [f"hotkey stub = keycode 67, mode toggle"]
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        pass


@pytest.fixture()
def daemon(tmp_path, monkeypatch):
    cfg = copy.deepcopy(DEFAULTS)
    monkeypatch.setattr(dm.history_mod.paths, "history_file",
                        lambda: tmp_path / "history.jsonl")
    return dm.Daemon(cfg, recorder=_NoopRecorder(),
                     use_hotkey=False, use_sounds=False)


class TestDaemonStatusField:
    def test_status_reflects_listener_health(self, daemon):
        daemon._hotkey = SimpleNamespace(hotkey_grabbed=False)
        assert daemon.handle_request({"action": "status"})["hotkey_grabbed"] is False
        daemon._hotkey = SimpleNamespace(hotkey_grabbed=True)
        assert daemon.handle_request({"action": "status"})["hotkey_grabbed"] is True

    def test_status_none_when_hotkey_disabled(self, daemon):
        # --no-hotkey / hotkey unavailable: null, not false (not "blocked")
        daemon._hotkey = None
        assert daemon.handle_request({"action": "status"})["hotkey_grabbed"] is None


class TestTrayTooltipSuffix:
    def test_blocked_hotkey_appends_suffix(self, daemon):
        daemon._hotkey = SimpleNamespace(hotkey_grabbed=False)
        assert daemon._tray_tooltip().endswith(" - hotkey blocked!")

    def test_healthy_hotkey_has_no_suffix(self, daemon):
        daemon._hotkey = SimpleNamespace(hotkey_grabbed=True)
        assert "blocked" not in daemon._tray_tooltip()

    def test_disabled_hotkey_has_no_suffix(self, daemon):
        daemon._hotkey = None
        assert "blocked" not in daemon._tray_tooltip()


class TestStartupHonesty:
    def _start(self, monkeypatch):
        logs, notes = [], []
        monkeypatch.setattr(hotkey, "HotkeyListener", _StubListener)
        monkeypatch.setattr(dm, "log", logs.append)
        monkeypatch.setattr(dm.ui, "notify",
                            lambda title, body="", timeout_ms=0, enabled=True:
                            notes.append((title, body)))
        cfg = copy.deepcopy(DEFAULTS)
        d = dm.Daemon(cfg, recorder=_NoopRecorder(), use_sounds=False)
        error = d._start_hotkey()
        return d, logs, notes, error

    def test_refused_startup_warns_and_notifies(self, monkeypatch):
        _StubListener.grabbed = False
        try:
            d, logs, notes, error = self._start(monkeypatch)
            assert error is None  # the daemon stays up and retries
            warns = [m for m in logs if "grab refused" in m]
            assert warns == ["WARN hotkey 'Right_Control' grab refused - "
                             "held by another client, will retry"]
            assert len(notes) == 1
            title, body = notes[0]
            assert title == "SayItErmano" and "retrying" in body
        finally:
            _StubListener.grabbed = True

    def test_healthy_startup_is_silent(self, monkeypatch):
        _StubListener.grabbed = True
        _, logs, notes, error = self._start(monkeypatch)
        assert error is None
        assert not any("grab refused" in m for m in logs)
        assert notes == []

    def test_refresh_tray_is_best_effort(self, daemon):
        calls = []

        class _Tray:
            def refresh(self):
                calls.append(1)

        daemon._tray = _Tray()
        daemon._refresh_tray()
        assert calls == [1]

        class _Broken:
            def refresh(self):
                raise RuntimeError("dbus gone")

        daemon._tray = _Broken()
        daemon._refresh_tray()  # no exception
        daemon._tray = None
        daemon._refresh_tray()  # no tray yet: fine
        assert calls == [1]


# ---------------------------------------------------------------------------
# Doctor: the hotkey-grab line
# ---------------------------------------------------------------------------

class TestDoctorHotkeyGrabLine:
    def _doctor(self, monkeypatch, tmp_path, status=None, socket_exists=True):
        from fluidvoice import control, doctor
        socket = tmp_path / "fluidvoice.sock"
        if socket_exists:
            socket.touch()
        monkeypatch.setattr(doctor.paths, "socket_path", lambda: socket)
        if not socket_exists:
            monkeypatch.setattr(control, "request",
                                lambda *a, **k: pytest.fail("must not query"))
            return doctor
        monkeypatch.setattr(control, "request",
                            lambda *a, **k: (status
                                             if not isinstance(status, Exception)
                                             else (_ for _ in ()).throw(status)))
        return doctor

    def test_ok(self, monkeypatch, tmp_path):
        doctor = self._doctor(monkeypatch, tmp_path, status={"hotkey_grabbed": True})
        assert doctor._hotkey_grab_line() == ["  hotkey grab: ok"]

    def test_blocked(self, monkeypatch, tmp_path):
        doctor = self._doctor(monkeypatch, tmp_path, status={"hotkey_grabbed": False})
        line = doctor._hotkey_grab_line()[0]
        assert "BLOCKED" in line and "retrying" in line

    def test_disabled_or_older_daemon(self, monkeypatch, tmp_path):
        doctor = self._doctor(monkeypatch, tmp_path, status={})  # no field
        assert "disabled" in doctor._hotkey_grab_line()[0]

    def test_daemon_down_no_socket(self, monkeypatch, tmp_path):
        doctor = self._doctor(monkeypatch, tmp_path, socket_exists=False)
        assert doctor._hotkey_grab_line() == \
            ["  hotkey grab: unknown (daemon down)"]

    def test_daemon_unreachable_socket(self, monkeypatch, tmp_path):
        from fluidvoice.control import ControlError
        doctor = self._doctor(monkeypatch, tmp_path, status=ControlError("refused"))
        assert doctor._hotkey_grab_line() == \
            ["  hotkey grab: unknown (daemon down)"]

    def test_run_prints_the_line(self, monkeypatch, capsys):
        from fluidvoice import doctor
        monkeypatch.setattr(doctor.paths, "socket_path",
                            lambda: Path("/nonexistent/fluidvoice.sock"))
        doctor.run()
        assert "hotkey grab:" in capsys.readouterr().out


class TestBothActivationMode:
    """B3: 'both' mode (upstream Automatic) - quick tap toggles, held key
    talks. Driven against a stub display; no X server needed."""

    class FakeRoot:
        def grab_key(self, *a, **k):
            pass

        def ungrab_key(self, *a, **k):
            pass

    class FakeDisplay:
        def __init__(self, still_down):
            self.root = TestBothActivationMode.FakeRoot()
            self._still_down = still_down
            self.calls = 0

        def screen(self):
            class S:
                root = self.root
            return S()

        def ungrab_keyboard(self, *a):
            pass

        def sync(self):
            pass

        def query_keymap(self):
            self.calls += 1
            km = bytearray(32)
            if self._still_down(self.calls):
                km[66 // 8] |= 1 << (66 % 8)  # keycode 66 bit
            return bytes(km)

        def pending_events(self):
            return 0

        def next_event(self):
            raise RuntimeError("no events")

    @staticmethod
    def make_listener(events):
        from fluidvoice.hotkey import HotkeyListener
        lst = HotkeyListener(key="F9", modifiers=[], mode="both",
                             on_toggle=lambda: events.append("toggle"),
                             on_cancel=lambda: events.append("cancel"),
                             cancel_key="Escape", log=lambda m: None)
        lst._grab = lambda *a, **k: None
        lst._settle_grabs = lambda *a, **k: None
        lst._escape_keycode = 9
        return lst

    def test_quick_tap_toggles_once(self):
        events = []
        lst = self.make_listener(events)
        d = self.FakeDisplay(lambda n: n > 1)  # released almost immediately
        lst._both_cycle(d, 66)
        assert events == ["toggle"]

    def test_held_key_talks_then_stops(self):
        events = []
        lst = self.make_listener(events)
        # down for ~40 polls (decision window + hold), then released
        d = self.FakeDisplay(lambda n: n <= 40)
        lst._both_cycle(d, 66)
        assert events == ["toggle", "toggle"]  # start ... stop+transcribe

    def test_escape_during_decision_cancels(self):
        events = []
        lst = self.make_listener(events)
        d = self.FakeDisplay(lambda n: True)

        class Ev:
            type = 2  # X.KeyPress
            detail = 9  # escape keycode

        d.pending_events = lambda: True
        d.next_event = lambda: Ev()
        lst._both_cycle(d, 66)
        assert events == ["cancel"]
