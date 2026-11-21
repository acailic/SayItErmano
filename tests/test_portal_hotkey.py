"""GlobalShortcuts portal hotkey (ADR-0006): fake portal objects only -
no D-Bus daemon is ever needed.

Covers the pure Activated/Deactivated state machine (press/release ->
hold-PTT, toggle, both), the listener lifecycle around an injected fake
session (bind-every-session, stop determinism, degrade-to-fallback on
every failure class), and the machine's edge robustness (duplicate
activations, stray releases, foreign shortcut ids).
"""
from __future__ import annotations

import asyncio
import sys
import time

from fluidvoice import portal_hotkey as ph


class Recorder:
    def __init__(self):
        self.events: list[str] = []

    def press(self):
        self.events.append("press")

    def release(self):
        self.events.append("release")

    def toggle(self):
        self.events.append("toggle")


def machine(mode, rec):
    return ph.PortalHotkeyMachine(mode, on_press=rec.press,
                                  on_release=rec.release,
                                  on_toggle=rec.toggle, log=lambda m: None)


class TestMachine:
    def test_toggle_fires_on_activation_only(self):
        rec = Recorder()
        m = machine("toggle", rec)
        m.activate()
        m.deactivate()
        assert rec.events == ["toggle"]  # a tap must not double-toggle

    def test_hold_maps_edges_to_press_release(self):
        rec = Recorder()
        m = machine("hold", rec)
        m.activate()
        m.deactivate()
        assert rec.events == ["press", "release"]

    def test_both_quick_tap_toggles(self):
        rec = Recorder()
        m = machine("both", rec)
        m.activate()
        m.deactivate()
        assert rec.events == ["toggle"]

    def test_both_hold_presses_at_deadline(self):
        rec = Recorder()
        m = machine("both", rec)
        m.BOTH_TAP_S = 0.0  # deadline already passed for this test
        m.activate()
        m.both_deadline_expired()
        m.deactivate()
        assert rec.events == ["press", "release"]

    def test_both_deadline_early_is_noop(self):
        rec = Recorder()
        m = machine("both", rec)
        m.activate()
        m.both_deadline_expired()  # 250ms window: not expired yet
        m.deactivate()
        assert rec.events == ["toggle"]

    def test_duplicate_activation_ignored(self):
        rec = Recorder()
        m = machine("hold", rec)
        m.activate()
        m.activate()
        m.deactivate()
        assert rec.events == ["press", "release"]

    def test_stray_release_ignored(self):
        rec = Recorder()
        m = machine("hold", rec)
        m.deactivate()
        m.activate()
        m.deactivate()
        assert rec.events == ["press", "release"]

    def test_unknown_mode_falls_back_to_toggle(self):
        rec = Recorder()
        m = machine("yolo", rec)
        assert m.mode == "toggle"
        m.activate()
        assert rec.events == ["toggle"]

    def test_callback_errors_do_not_propagate(self):
        def boom():
            raise RuntimeError("daemon hiccup")

        m = ph.PortalHotkeyMachine("hold", on_press=boom,
                                   on_release=boom, on_toggle=boom,
                                   log=lambda msg: None)
        m.activate()  # must not raise
        m.deactivate()


class FakeBoundShortcut:
    def __init__(self, id, trigger_description=""):
        self.id = id
        self.trigger_description = trigger_description


class FakeEvent:
    def __init__(self, shortcut_id, timestamp=1234):
        self.shortcut_id = shortcut_id
        self.timestamp = timestamp


class FakeSession:
    """The global-shortcut-portal session surface the listener uses:
    connect()/bind()/close() + callback delivery via on_* methods."""

    def __init__(self, bound=None, fail_bind=None, fail_connect=None):
        self.bound = bound if bound is not None else [
            FakeBoundShortcut(ph.SHORTCUT_ID, "ctrl+alt+d")]
        self.fail_bind = fail_bind
        self.fail_connect = fail_connect
        self.callback = None
        self.bind_calls: list = []
        self.closed = False

    async def connect(self):
        if self.fail_connect:
            raise RuntimeError(self.fail_connect)

    async def bind(self, shortcuts):
        self.bind_calls.append(shortcuts)
        if self.fail_bind:
            raise RuntimeError(self.fail_bind)
        return self.bound

    async def close(self):
        self.closed = True

    def fire(self, activated: bool, shortcut_id=ph.SHORTCUT_ID) -> None:
        cb = self.callback
        event = FakeEvent(shortcut_id)
        if cb is None:
            return
        if activated:
            cb.on_activated(event)
        else:
            cb.on_deactivated(event)


def make_listener(rec, session, **kw):
    return ph.PortalHotkeyListener(
        mode=kw.pop("mode", "hold"),
        on_press=rec.press, on_release=rec.release,
        on_toggle=rec.toggle, log=kw.pop("log", lambda m: None),
        session_factory=lambda: session, **kw)


class TestListenerLifecycle:
    def test_start_binds_and_edges_fire(self):
        rec = Recorder()
        session = FakeSession()
        listener = make_listener(rec, session)
        assert listener.start() is True
        assert session.bind_calls and session.bind_calls[0][0].id \
            == ph.SHORTCUT_ID
        session.fire(True)
        session.fire(False)
        listener.stop()
        assert rec.events == ["press", "release"]
        assert session.closed

    def test_foreign_shortcut_ids_ignored(self):
        rec = Recorder()
        session = FakeSession()
        listener = make_listener(rec, session)
        assert listener.start() is True
        session.fire(True, shortcut_id="some-other-app")
        session.fire(False, shortcut_id="some-other-app")
        listener.stop()
        assert rec.events == []

    def test_preferred_trigger_sent_but_not_required(self):
        rec = Recorder()
        session = FakeSession(bound=[
            FakeBoundShortcut(ph.SHORTCUT_ID, "")])  # DE assigned nothing
        logs: list[str] = []
        listener = make_listener(rec, session,
                                 preferred_trigger="ctrl+alt+space", log=logs.append)
        assert listener.start() is True
        assert session.bind_calls[0][0].preferred_trigger == "ctrl+alt+space"
        # unset trigger still logs a working bind + where to set the key
        assert any("unset" in line for line in logs)

    def test_bind_failure_degrades_to_fallback(self):
        rec = Recorder()
        session = FakeSession(fail_bind="portal says no")
        logs: list[str] = []
        listener = make_listener(rec, session, log=logs.append)
        assert listener.start() is False
        assert any("unavailable" in line and "--toggle" in line
                   for line in logs)

    def test_connect_failure_degrades(self):
        rec = Recorder()
        session = FakeSession(fail_connect="no portal backend (sway)")
        listener = make_listener(rec, session, log=lambda m: None)
        assert listener.start() is False
        listener.stop()  # idempotent after a failed start

    def test_missing_package_degrades(self, monkeypatch):
        rec = Recorder()
        monkeypatch.setitem(sys.modules, "global_shortcut_portal", None)
        logs: list[str] = []
        listener = ph.PortalHotkeyListener(
            mode="toggle", on_toggle=rec.toggle, log=logs.append)
        assert listener.start() is False
        assert any("not installed" in line for line in logs)

    def test_restart_rebinds_on_a_fresh_session(self):
        """BindShortcuts is once per portal session: every start() must
        come with a fresh session + bind (the KDE-restart workaround)."""
        rec = Recorder()
        sessions = [FakeSession(), FakeSession()]
        made = iter(sessions)
        listener = ph.PortalHotkeyListener(
            mode="toggle", on_toggle=rec.toggle, log=lambda m: None,
            session_factory=lambda: next(made))
        assert listener.start() is True
        listener.stop()
        assert listener.start() is True
        listener.stop()
        assert all(s.bind_calls for s in sessions)
        assert all(s.closed for s in sessions)

    def test_stop_is_deterministic_and_idempotent(self):
        rec = Recorder()
        session = FakeSession()
        listener = make_listener(rec, session)
        assert listener.start() is True
        listener.stop()
        listener.stop()
        assert listener._thread is None and listener._loop is None

    def test_shortcuts_changed_relogs_binding(self):
        rec = Recorder()
        session = FakeSession()
        logs: list[str] = []
        listener = make_listener(rec, session, log=logs.append)
        assert listener.start() is True
        session.callback.on_shortcuts_changed(
            session, [FakeBoundShortcut(ph.SHORTCUT_ID, "ctrl+alt+k")])
        listener.stop()
        assert any("changed" in line and "ctrl+alt+k" in line
                   for line in logs)

    def test_toggle_mode_edges(self):
        rec = Recorder()
        session = FakeSession()
        listener = make_listener(rec, session, mode="toggle")
        assert listener.start() is True
        session.fire(True)
        session.fire(False)
        listener.stop()
        assert rec.events == ["toggle"]

    def test_hold_ptt_real_timing(self):
        """Full thread path with real clocks: activate, wait past the
        window, deactivate (mode=both)."""
        rec = Recorder()
        session = FakeSession()
        listener = make_listener(rec, session, mode="both")
        listener.machine.BOTH_TAP_S = 0.05
        assert listener.start() is True
        session.fire(True)
        deadline = time.monotonic() + 2.0
        while rec.events != ["press"] and time.monotonic() < deadline:
            time.sleep(0.01)
        session.fire(False)
        deadline = time.monotonic() + 2.0
        while rec.events != ["press", "release"] and time.monotonic() < deadline:
            time.sleep(0.01)
        listener.stop()
        assert rec.events == ["press", "release"]


class TestListenerWire:
    """The listener's _open_session against the REAL package classes
    requires D-Bus; here we only pin that the injected factory short-
    circuit is honored (async factories too)."""

    def test_async_factory_supported(self):
        rec = Recorder()
        session = FakeSession()

        async def factory():
            await asyncio.sleep(0)
            return session

        listener = ph.PortalHotkeyListener(
            mode="hold", on_press=rec.press, on_release=rec.release,
            on_toggle=rec.toggle, log=lambda m: None,
            session_factory=factory)
        assert listener.start() is True
        listener.stop()
        assert session.closed


class TestPackageBridge:
    def test_callbacks_adapter_routes_edges(self, monkeypatch):
        """The _Callbacks adapter feeds any object with shortcut_id/
        timestamp through to the machine (the real ShortcutEvent shape
        from the package, faked)."""
        rec = Recorder()
        session = FakeSession()
        listener = make_listener(rec, session)
        assert listener.start() is True
        cb = session.callback
        cb.on_error(RuntimeError("portal hiccup"))  # logged, not raised
        cb.on_activated(FakeEvent(ph.SHORTCUT_ID))
        cb.on_deactivated(FakeEvent(ph.SHORTCUT_ID))
        listener.stop()
        assert rec.events == ["press", "release"]
