"""GlobalShortcuts portal hotkey (ADR-0006): fake portal objects only —
no D-Bus daemon is needed (the task's spike test scope).

Covers:
- ShortcutRouter: Activated/Deactivated edge mapping per hotkey.mode
  (toggle fires on press only; hold fires press+release = native PTT;
  both uses the 250 ms tap window like the X11 listener).
- preferred_trigger_for: config -> portal trigger hint.
- PortalHotkeyListener lifecycle against a FakeSession: bind once per
  session (the KDE rebind cure), preferred_trigger sent but a different
  bound trigger accepted, summary/hotkey_grabbed surface, callback
  delivery, stop() closing the session, honest failures (no library,
  no backend) as PortalHotkeyError.
"""
from __future__ import annotations

import pytest

from fluidvoice.portal_hotkey import (
    PortalHotkeyError,
    PortalHotkeyListener,
    ShortcutRouter,
    preferred_trigger_for,
)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestPreferredTrigger:
    def test_modifier_plus_key(self):
        assert preferred_trigger_for("d", ["ctrl"]) == "CTRL+D"
        assert preferred_trigger_for("F9", []) == "F9"

    def test_modifier_only_hotkey(self):
        assert preferred_trigger_for("Right_Control", []) == "CTRL"
        assert preferred_trigger_for("super", []) == "LOGO"

    def test_modifiers_deduplicated_and_ordered(self):
        assert preferred_trigger_for("x", ["shift", "ctrl"]) == "CTRL+SHIFT+X"

    def test_empty_key_is_none(self):
        assert preferred_trigger_for("", ["ctrl"]) is None
        assert preferred_trigger_for("  ", []) is None


class TestRouterToggle:
    def test_press_fires_once(self):
        fired = []
        router = ShortcutRouter("toggle", lambda: fired.append(1))
        router.activated()
        router.deactivated()
        router.tick()
        assert fired == [1]

    def test_release_never_fires(self):
        fired = []
        router = ShortcutRouter("toggle", lambda: fired.append(1))
        router.deactivated()
        router.tick()
        assert fired == []


class TestRouterHold:
    def test_press_release_pair(self):
        events = []
        router = ShortcutRouter("hold",
                                lambda: events.append("toggle"))
        router.activated()
        assert events == ["toggle"]  # start talking
        router.deactivated()
        assert events == ["toggle", "toggle"]  # stop + transcribe

    def test_deactivated_without_press_ignored(self):
        events = []
        router = ShortcutRouter("hold", lambda: events.append("toggle"))
        router.deactivated()
        assert events == []


class TestRouterBoth:
    def test_quick_tap_is_toggle(self):
        clock = FakeClock()
        events = []
        router = ShortcutRouter("both", lambda: events.append("toggle"),
                                clock=clock)
        router.activated()
        clock.advance(0.10)  # inside the 250 ms window
        router.deactivated()
        router.tick()  # the tap deadline is cancelled: no hold fires
        assert events == ["toggle"]

    def test_held_key_past_window_is_ptt(self):
        clock = FakeClock()
        events = []
        router = ShortcutRouter("both", lambda: events.append("toggle"),
                                clock=clock)
        router.activated()
        clock.advance(0.30)  # past the window
        router.tick()
        assert events == ["toggle"]  # hold started
        router.deactivated()
        assert events == ["toggle", "toggle"]  # stop + transcribe

    def test_release_right_at_deadline_is_hold(self):
        clock = FakeClock()
        events = []
        router = ShortcutRouter("both", lambda: events.append("toggle"),
                                clock=clock)
        router.activated()
        clock.advance(0.25)
        router.tick()
        assert events == ["toggle"]

    def test_callback_exception_is_swallowed(self):
        def boom():
            raise RuntimeError("callback down")
        router = ShortcutRouter("toggle", boom)
        router.activated()  # must not raise


# -- listener lifecycle against fake portal objects --------------------------

class FakeBound:
    def __init__(self, shortcut_id, trigger_description):
        self.id = shortcut_id
        self.trigger_description = trigger_description


class FakeEvent:
    def __init__(self, shortcut_id):
        self.shortcut_id = shortcut_id


class FakeSession:
    """Records the bind calls and hands back configurable results."""

    def __init__(self, bound=None, bind_error=None):
        self.bound = bound if bound is not None else [
            FakeBound("dictate", "CTRL+ALT+D")]
        self.bind_error = bind_error
        self.bind_calls = []
        self.connect_calls = 0
        self.close_calls = 0
        self.callback = None

    async def connect(self):
        self.connect_calls += 1

    async def close(self):
        self.close_calls += 1

    async def bind(self, shortcuts):
        self.bind_calls.append(shortcuts)
        if self.bind_error is not None:
            raise self.bind_error
        return self.bound


def fake_listener(session: FakeSession, listener: PortalHotkeyListener):
    """Wire a FakeSession into a listener (the _open_session seam)."""
    listener._open_session = lambda: (
        setattr(session, "callback", listener._callbacks()),
        session)[1]
    listener._build_shortcut = lambda: {"id": listener.shortcut_id}
    return session


@pytest.fixture()
def no_real_portal(monkeypatch):
    """Guard: any accidental real import fails the test loudly."""
    import sys
    monkeypatch.setitem(sys.modules, "global_shortcut_portal", None)


def make_listener(**kw):
    fired = []
    defaults = dict(on_toggle=lambda: fired.append(1), mode="hold")
    defaults.update(kw)
    listener = PortalHotkeyListener(**defaults)
    return listener, fired


class TestListenerLifecycle:
    def test_start_binds_and_reports(self, no_real_portal):
        listener, fired = make_listener(key="d", modifiers=["ctrl"])
        session = fake_listener(FakeSession(), listener)
        listener.start()
        try:
            assert session.connect_calls == 1
            assert len(session.bind_calls) == 1
            assert listener.hotkey_grabbed is True
            assert any("CTRL+ALT+D" in line for line in listener.summary)
        finally:
            listener.stop()
        assert session.close_calls == 1

    def test_preferred_trigger_sent_but_not_required(self, no_real_portal):
        listener, _fired = make_listener(key="d", modifiers=["ctrl"])
        session = fake_listener(
            FakeSession(bound=[FakeBound("dictate", "Super+F9")]), listener)
        listener.start()
        try:
            # the hint was built and offered; the user-picked trigger
            # wins and is reported without complaint
            assert listener._trigger == "CTRL+D"
            assert len(session.bind_calls) == 1
            assert "Super+F9" in listener.summary[0]
        finally:
            listener.stop()

    def test_no_backend_bind_fails_loudly(self, no_real_portal):
        listener, _fired = make_listener()
        fake_listener(FakeSession(bound=[]), listener)
        with pytest.raises(PortalHotkeyError, match="no GlobalShortcuts"):
            listener.start()

    def test_missing_library_is_actionable(self, no_real_portal,
                                           monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "global_shortcut_portal", None)
        listener, _fired = make_listener()
        with pytest.raises(PortalHotkeyError,
                           match="global-shortcut-portal not installed"):
            listener.start()

    def test_bind_error_surfaces_as_portal_error(self, no_real_portal):
        listener, _fired = make_listener()
        fake_listener(FakeSession(bind_error=RuntimeError("portal said no")),
                      listener)
        with pytest.raises(PortalHotkeyError, match="portal said no"):
            listener.start()

    def test_rebind_every_session(self, no_real_portal):
        """BindShortcuts is re-called after a restart (KDE bug cure)."""
        listener, _fired = make_listener()
        sessions = [FakeSession(), FakeSession()]
        pick = {"i": 0}

        def open_session():
            session = sessions[pick["i"]]
            session.callback = listener._callbacks()
            pick["i"] += 1
            return session

        listener._open_session = open_session
        listener._build_shortcut = lambda: {"id": "dictate"}
        listener.start()
        listener.stop()
        listener.start()
        try:
            assert sessions[0].bind_calls and sessions[1].bind_calls
            assert sessions[0].close_calls == 1
            assert listener.hotkey_grabbed is True
        finally:
            listener.stop()


class TestRealShortcutSpec:
    def test_shortcut_carries_preferred_trigger(self):
        gsp = pytest.importorskip(
            "global_shortcut_portal", reason="portal extra not installed")
        listener, _fired = make_listener(key="d", modifiers=["ctrl"])
        spec = listener._build_shortcut()
        assert isinstance(spec, gsp.Shortcut)
        assert spec.id == "dictate"
        assert spec.preferred_trigger == "CTRL+D"
        dbus_tuple = spec.to_dbus_tuple()
        assert dbus_tuple[1]["preferred_trigger"] == "CTRL+D"


class TestListenerCallbacks:
    def _started(self, mode="hold"):
        listener, fired = make_listener(mode=mode)
        session = fake_listener(FakeSession(), listener)
        listener.start()
        return listener, session, fired

    def test_hold_edges_fire_toggle_twice(self, no_real_portal):
        listener, session, fired = self._started()
        try:
            session.callback.on_activated(FakeEvent("dictate"))
            session.callback.on_deactivated(FakeEvent("dictate"))
            assert fired == [1, 1]
        finally:
            listener.stop()

    def test_foreign_shortcut_id_ignored(self, no_real_portal):
        listener, session, fired = self._started()
        try:
            session.callback.on_activated(FakeEvent("other-app"))
            assert fired == []
        finally:
            listener.stop()

    def test_set_recording_is_a_noop(self, no_real_portal):
        listener, _session, _fired = self._started()
        try:
            listener.set_recording(True)  # must not raise
        finally:
            listener.stop()
