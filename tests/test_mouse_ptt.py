"""Mouse push-to-talk (XGrabButton listener) + lock suppression.

Unit-level coverage mirrors test_hotkey_grab.py: pure parsing first, then
a fake-X listener contract (grab routing, hold-cycle state machine), then
daemon wiring with a stub listener, then the doctor lines. Lock-suppression
state-machine tests live in tests/test_lock_suppression.py.
"""
from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from Xlib import XK, X

from fluidvoice import daemon as dm
from fluidvoice import hotkey
from fluidvoice.config import DEFAULTS, TEMPLATE, apply_settings, coerce_setting
from fluidvoice.hotkey import (
    _LOCK_MASKS,
    HotkeyError,
    MousePTTListener,
    parse_button_spec,
    parse_raw_button_event,
)

BTN = 8
ESCAPE_KEYCODE = 9


class TestParseButtonSpec:
    @pytest.mark.parametrize("spec, want", [
        ("button8", 8), ("b8", 8), ("8", 8), ("BUTTON 8", 8),
        ("button08", 8), (" B8 ", 8), ("Button8", 8), ("b255", 255),
        ("button6", 6), ("button127", 127),
    ])
    def test_valid(self, spec, want):
        assert parse_button_spec(spec) == want

    @pytest.mark.parametrize("spec", ["", "  ", "none", "NONE", "off",
                                      "disabled", "None"])
    def test_off_is_none(self, spec):
        assert parse_button_spec(spec) is None

    @pytest.mark.parametrize("spec, kind", [
        ("button1", "click"), ("button2", "click"), ("button3", "click"),
        ("b3", "click"), ("1", "click"),
        ("button4", "scroll"), ("button5", "scroll"), ("4", "scroll"),
        ("b5", "scroll"),
    ])
    def test_buttons_1_to_5_refused(self, spec, kind):
        with pytest.raises(HotkeyError) as ei:
            parse_button_spec(spec)
        assert kind in str(ei.value)
        assert "break the desktop" in str(ei.value)

    @pytest.mark.parametrize("spec", ["button256", "b300", "button0", "0",
                                      "button", "b", "b-8", "button-8",
                                      "xyz", "eight", "8.5", "button8x"])
    def test_invalid_raises(self, spec):
        with pytest.raises(HotkeyError):
            parse_button_spec(spec)

    def test_boundary_messages(self):
        with pytest.raises(HotkeyError, match="out of range"):
            parse_button_spec("button0")
        with pytest.raises(HotkeyError, match="out of range"):
            parse_button_spec("button256")


class TestParseRawButtonEvent:
    def _payload(self, deviceid=2, time=1234, button=8):
        return (deviceid.to_bytes(2, "little")
                + time.to_bytes(4, "little")
                + button.to_bytes(4, "little")
                + b"\x00" * 14)  #valuator bits etc; ignored

    def test_layout(self):
        assert parse_raw_button_event(self._payload()) == (2, 8)
        assert parse_raw_button_event(self._payload(deviceid=7, button=255)) == (7, 255)

    @pytest.mark.parametrize("bad", [b"", b"\x01", b"\x02\x00\x00\x00",
                                     None, "not-bytes", 8, bytearray(b"\x00")])
    def test_short_or_wrong_type_is_none(self, bad):
        assert parse_raw_button_event(bad) is None


class TestConfigKeys:
    def test_defaults_present(self):
        assert DEFAULTS["recording"]["push_to_talk_button"] == ""
        assert DEFAULTS["recording"]["push_to_talk_modifiers"] == []
        assert DEFAULTS["general"]["pause_when_locked"] is True

    def test_template_contains_keys(self):
        assert "push_to_talk_button" in TEMPLATE
        assert "push_to_talk_modifiers" in TEMPLATE
        assert "pause_when_locked" in TEMPLATE
        # the template parses as the TOML it claims to be
        import tomllib
        tomllib.loads(TEMPLATE)

    def test_coerce_accepts_and_normalizes(self):
        assert coerce_setting("recording", "push_to_talk_button", "b8") == (True, "button8")
        assert coerce_setting("recording", "push_to_talk_button", " 8 ") == (True, "button8")
        assert coerce_setting("recording", "push_to_talk_button", "BUTTON 8") == (True, "button8")

    def test_coerce_empty_means_off(self):
        # "" is a meaningful value here (feature off), not a rejection
        assert coerce_setting("recording", "push_to_talk_button", "") == (True, "")
        assert coerce_setting("recording", "push_to_talk_button", "none") == (True, "")

    @pytest.mark.parametrize("bad", ["button3", "button5", "button256",
                                     "b-8", 8, None, "x" * 33])
    def test_coerce_rejects_bad_values(self, bad):
        assert coerce_setting("recording", "push_to_talk_button", bad)[0] is False

    def test_coerce_modifiers(self):
        assert coerce_setting("recording", "push_to_talk_modifiers",
                              ["ctrl", "super"]) == (True, ["ctrl", "super"])
        assert coerce_setting("recording", "push_to_talk_modifiers",
                              ["banana"])[0] is False
        assert coerce_setting("recording", "push_to_talk_modifiers",
                              "ctrl")[0] is False

    def test_coerce_pause_when_locked(self):
        assert coerce_setting("general", "pause_when_locked", True) == (True, True)
        assert coerce_setting("general", "pause_when_locked", "yes")[0] is False

    def test_apply_settings_roundtrip(self):
        cfg = copy.deepcopy(DEFAULTS)
        changed, rejected = apply_settings(cfg, {
            "recording": {"push_to_talk_button": "b9",
                          "push_to_talk_modifiers": ["ctrl"]},
            "general": {"pause_when_locked": False},
        })
        assert rejected == []
        assert set(changed) == {"recording.push_to_talk_button",
                                "recording.push_to_talk_modifiers",
                                "general.pause_when_locked"}
        assert cfg["recording"]["push_to_talk_button"] == "button9"
        assert cfg["recording"]["push_to_talk_modifiers"] == ["ctrl"]
        assert cfg["general"]["pause_when_locked"] is False

    def test_apply_settings_rejects_desktop_buttons(self):
        cfg = copy.deepcopy(DEFAULTS)
        changed, rejected = apply_settings(
            cfg, {"recording": {"push_to_talk_button": "button1"}})
        assert changed == []
        assert rejected == ["recording.push_to_talk_button"]
        assert cfg["recording"]["push_to_talk_button"] == ""


# ---------------------------------------------------------------------------
# MousePTTListener against a fake X connection (the test_hotkey_grab.py
# contract, pointer edition): grab_button refusals route through onerror
# into data; raw XI2 GenericEvents are parsed from their .data bytes.
# ---------------------------------------------------------------------------

BTN_KEYCODE_NOTE = "buttons have no keycodes; detail is the button number"


class FakeGrabError(Exception):
    """Stands in for Xlib's BadAccess (grab already held)."""


class FakeRoot:
    def __init__(self, display):
        self._display = display

    def grab_button(self, button, modifiers, owner_events, event_mask,
                    pointer_mode, keyboard_mode, confine_to, cursor,
                    onerror=None):
        assert onerror is not None, "grab_button issued without onerror routing"
        self._display.calls.append(("grab_button", button, modifiers,
                                    event_mask))
        if (button, modifiers) in self._display.refused:
            self._display.pending_errors.append((onerror, FakeGrabError()))

    def ungrab_button(self, button, modifiers, onerror=None):
        self._display.calls.append(("ungrab_button", button, modifiers))

    def grab_key(self, key, modifiers, owner_events, pointer_mode,
                 keyboard_mode, onerror=None):
        self._display.calls.append(("grab_key", key, modifiers))
        if (key, modifiers) in self._display.refused:
            self._display.pending_errors.append((onerror, FakeGrabError()))

    def ungrab_key(self, key, modifiers, onerror=None):
        self._display.calls.append(("ungrab_key", key, modifiers))

    def xinput_select_events(self, masks):
        self._display.calls.append(("xinput_select_events", masks))


class FakeDisplay:
    """The real-Display contract MousePTTListener uses: keysym mapping,
    screen().root, sync() pumping queued onerror callbacks, a scripted
    event queue, and a scripted XI negotiation."""

    def __init__(self, refused=(), events=(), xi=(2, 2), has_xi=True):
        self.refused = set(refused)
        self.events = list(events)
        self.xi = xi
        self.has_xi = has_xi
        self.pending_errors: list = []
        self.calls: list = []
        self.closed = False
        self.display = self  # _negotiate_xi passes d.display through
        self._root = FakeRoot(self)

    def has_extension(self, name):
        return self.has_xi

    def get_extension_major(self, name):
        return 131

    def keysym_to_keycode(self, keysym):
        if keysym == XK.string_to_keysym("Escape"):
            return ESCAPE_KEYCODE
        return 0

    def screen(self):
        return SimpleNamespace(root=self._root)

    def sync(self):
        pending, self.pending_errors = self.pending_errors, []
        for onerror, err in pending:
            onerror(err, None)

    def pending_events(self):
        return len(self.events)

    def next_event(self):
        return self.events.pop(0)

    def ungrab_pointer(self, time):
        self.calls.append(("ungrab_pointer", time))

    def ungrab_keyboard(self, time):
        self.calls.append(("ungrab_keyboard", time))

    def close(self):
        self.closed = True


def _raw_event(button, evtype=16, device=2):
    """A fake XI2 raw GenericEvent (unregistered in python-xlib's ge
    table): .evtype + .data bytes per the wire layout."""
    payload = (device.to_bytes(2, "little")
               + (1234).to_bytes(4, "little")
               + button.to_bytes(4, "little")
               + b"\x00" * 14)
    return SimpleNamespace(type=35, extension=131, sequence_number=1,
                           length=0, evtype=evtype, data=payload)


def _press(button=BTN):
    return SimpleNamespace(type=X.ButtonPress, detail=button,
                           send_event=False)


def _escape_press():
    return SimpleNamespace(type=X.KeyPress, detail=ESCAPE_KEYCODE,
                           send_event=False)


def button_combos(button: int, mods: int = 0) -> set:
    return {(button, mods | extra) for extra in _LOCK_MASKS}


def button_grab_calls(display) -> list:
    return [c for c in display.calls if c[0] == "grab_button"]


@pytest.fixture()
def make_ptt(monkeypatch):
    """MousePTTListener wired to a fake Display; returns a factory taking
    refused combos / scripted events / scripted XI version."""
    def _make(refused=(), events=(), xi=(2, 2), has_xi=True,
              modifiers=None, cancel_key="Escape"):
        fake = FakeDisplay(refused=refused, events=events, xi=xi,
                           has_xi=has_xi)
        lines: list[str] = []
        health: list[bool] = []
        monkeypatch.setattr(hotkey, "Display", lambda name=None: fake)

        def _xi(display, major, minor):
            assert (major, minor) == (2, 2), "must ask for XI 2.2 first"
            return SimpleNamespace(major_version=fake.xi[0],
                                   minor_version=fake.xi[1])

        monkeypatch.setattr(hotkey, "_xi_query_version", _xi)
        listener = MousePTTListener(BTN, modifiers or [],
                                    on_toggle=lambda: toggles.append(1),
                                    on_cancel=(lambda: cancels.append(1)
                                               if cancel_key != "none"
                                               else None),
                                    cancel_key=cancel_key,
                                    log=lines.append,
                                    on_grab_change=health.append)
        return listener, fake, lines, health

    toggles: list = []
    cancels: list = []
    _make.toggles = toggles
    _make.cancels = cancels
    return _make


class TestButtonGrabRouting:
    def test_setup_grabs_all_lock_combos_with_onerror(self, make_ptt):
        listener, fake, _, _ = make_ptt()
        summary = listener.setup()
        assert listener.button_grabbed is True
        assert len(button_grab_calls(fake)) == len(_LOCK_MASKS)
        # the raw-event subscription went to the root window
        xi_calls = [c for c in fake.calls if c[0] == "xinput_select_events"]
        assert len(xi_calls) == 1
        assert summary and "button 8" in summary[0] \
            and "XGrabButton" in summary[0]

    def test_all_refused_reports_not_raises(self, make_ptt):
        listener, fake, lines, _ = make_ptt(refused=button_combos(BTN))
        listener.setup()
        assert listener.button_grabbed is False
        assert listener._refuse_warned is True  # startup latch (daemon owns)
        assert not any("still refused" in m for m in lines)

    def test_holder_release_recovers_and_fires_health(self, make_ptt):
        listener, fake, lines, health = make_ptt(refused=button_combos(BTN))
        listener.setup()
        fake.refused.clear()
        listener._sync_button_grab()
        assert listener.button_grabbed is True
        assert lines.count("mouse PTT grab recovered") == 1
        assert health == [True]  # surfaces told about the recovery
        calls = len(button_grab_calls(fake))
        listener._sync_button_grab()
        assert len(button_grab_calls(fake)) == calls  # healthy: no traffic

    def test_healthy_steady_state_zero_x_traffic(self, make_ptt):
        listener, fake, _, _ = make_ptt()
        listener.setup()
        before = len(fake.calls)
        for _ in range(5):
            listener._sync_button_grab()
        assert len(fake.calls) == before

    def test_warn_caps_after_max_attempts(self, make_ptt):
        listener, fake, lines, _ = make_ptt()
        listener.setup()
        assert listener.button_grabbed is True
        fake.refused = button_combos(BTN)
        listener._combo_ok.clear()
        for _ in range(9):
            listener._sync_button_grab()
        assert not any("still refused" in m for m in lines)
        listener._sync_button_grab()  # 10th distinct attempt
        warns = [m for m in lines if "still refused" in m]
        assert len(warns) == 1 and "mouse PTT" in warns[0]


class TestXINegotiationGate:
    def test_old_xi_version_refuses_to_start(self, make_ptt):
        listener, fake, _, _ = make_ptt(xi=(2, 0))
        with pytest.raises(HotkeyError) as ei:
            listener.setup()
        assert "raw button releases" in str(ei.value)

    def test_missing_xi_extension_refuses_to_start(self, make_ptt):
        listener, fake, _, _ = make_ptt(has_xi=False)
        with pytest.raises(HotkeyError, match="raw button releases"):
            listener.setup()

    def test_xi_2_1_is_the_minimum(self, make_ptt):
        listener, _, _, _ = make_ptt(xi=(2, 1))
        listener.setup()  # >= 2.1 delivers RawButtonRelease
        assert listener.button_grabbed is True


class TestHoldCycle:
    def _armed(self, make_ptt, events, **kw):
        listener, fake, lines, health = make_ptt(events=events, **kw)
        listener.setup()
        make_ptt.toggles.clear()
        make_ptt.cancels.clear()
        return listener, fake

    def test_release_fires_stop_toggle_never_cancel(self, make_ptt):
        listener, fake = self._armed(make_ptt, [_raw_event(BTN)])
        listener._hold_cycle(fake)
        assert len(make_ptt.toggles) == 2  # start + stop&transcribe
        assert make_ptt.cancels == []
        # passthrough first: the pointer is freed BEFORE anything else
        pointer_calls = [i for i, c in enumerate(fake.calls)
                         if c[0] == "ungrab_pointer"]
        assert pointer_calls  # entered the cycle ungrabbed

    def test_escape_press_aborts_with_single_toggle(self, make_ptt):
        listener, fake = self._armed(make_ptt, [_escape_press()])
        listener._hold_cycle(fake)
        assert make_ptt.toggles == [1]  # start only
        assert make_ptt.cancels == [1]

    def test_mid_hold_repress_of_same_button_is_tolerated(self, make_ptt):
        # a second pointer device re-activates the surviving passive grab:
        # tolerate, ungrab again, keep holding until the real release
        listener, fake = self._armed(
            make_ptt, [_press(BTN), _raw_event(BTN)])
        listener._hold_cycle(fake)
        assert len(make_ptt.toggles) == 2  # start + stop
        assert make_ptt.cancels == []
        ungrabs = [c for c in fake.calls if c[0] == "ungrab_pointer"]
        assert len(ungrabs) >= 2  # hold-open + re-press re-ungrab

    def test_other_button_release_does_not_end_hold(self, make_ptt):
        listener, fake = self._armed(
            make_ptt, [_raw_event(9), _raw_event(7), _raw_event(BTN)])
        listener._hold_cycle(fake)
        assert len(make_ptt.toggles) == 2
        assert make_ptt.cancels == []

    def test_raw_press_events_are_ignored(self, make_ptt):
        listener, fake = self._armed(
            make_ptt, [_raw_event(BTN, evtype=15), _raw_event(BTN)])
        listener._hold_cycle(fake)
        assert len(make_ptt.toggles) == 2  # press ignored, release ended it

    def test_escape_grab_armed_during_hold_and_disarmed_after(self, make_ptt):
        listener, fake = self._armed(make_ptt, [_raw_event(BTN)])
        listener._hold_cycle(fake)
        armed = [c for c in fake.calls
                 if c[:2] == ("grab_key", ESCAPE_KEYCODE)]
        disarmed = [c for c in fake.calls
                    if c[:2] == ("ungrab_key", ESCAPE_KEYCODE)]
        assert armed and disarmed
        # escape activation released too (it is a keyboard grab)
        assert any(c[0] == "ungrab_keyboard" for c in fake.calls)

    def test_stop_flag_ends_hold_silently(self, make_ptt):
        listener, fake = self._armed(make_ptt, [])
        listener._stop_flag.set()  # shutdown raced the hold
        listener._hold_cycle(fake)
        assert make_ptt.toggles == [1]  # the start only; no stop, no cancel
        assert make_ptt.cancels == []

    def test_hold_with_modifiers_grabs_combo_masks(self, make_ptt):
        listener, fake, _, _ = make_ptt(modifiers=["ctrl"])
        listener.setup()
        masks = {c[2] for c in button_grab_calls(fake)}
        ctrl = X.ControlMask
        assert masks == {ctrl | extra for extra in _LOCK_MASKS}


class TestClassifyIdleEvent:
    def test_core_press_of_button_starts_hold(self, make_ptt):
        listener, _, _, _ = make_ptt()
        listener.setup()
        assert listener._classify_idle_event(_press(BTN)) == "hold"

    def test_other_events_ignored(self, make_ptt):
        listener, _, _, _ = make_ptt()
        listener.setup()
        assert listener._classify_idle_event(_raw_event(BTN)) == "ignore"
        assert listener._classify_idle_event(
            SimpleNamespace(type=X.ButtonRelease, detail=BTN)) == "ignore"
        assert listener._classify_idle_event(_press(9)) == "ignore"
        assert listener._classify_idle_event(
            SimpleNamespace(type=X.MotionNotify, detail=None)) == "ignore"

    def test_cancel_key_press_routes_to_cancel(self, make_ptt):
        listener, _, _, _ = make_ptt()
        listener.setup()
        assert listener._classify_idle_event(_escape_press()) == "cancel"


class TestCancelGrabWhileRecording:
    def test_set_recording_arms_and_disarms_cancel_key(self, make_ptt):
        listener, fake, _, _ = make_ptt()
        listener.setup()
        listener.set_recording(True)  # recording started by keyboard/tray
        listener._sync_cancel_grab()
        armed = [c for c in fake.calls if c[0] == "grab_key"]
        assert armed and all(c[1] == ESCAPE_KEYCODE for c in armed)
        listener.set_recording(False)
        listener._sync_cancel_grab()
        assert any(c[:2] == ("ungrab_key", ESCAPE_KEYCODE)
                   for c in fake.calls)

    def test_no_cancel_key_no_grab(self, make_ptt):
        listener, fake, _, _ = make_ptt(cancel_key="none")
        listener.setup()
        listener.set_recording(True)
        listener._sync_cancel_grab()
        assert not any(c[0] == "grab_key" for c in fake.calls)

    def test_cancel_grab_failure_warns_once(self, make_ptt):
        listener, fake, lines, _ = make_ptt()
        listener.setup()
        fake.refused = button_combos(ESCAPE_KEYCODE)

        def broken(*_a, **_k):
            raise RuntimeError("display closed")

        fake._root.grab_key = broken
        listener.set_recording(True)
        listener._sync_cancel_grab()
        listener._sync_cancel_grab()
        warns = [m for m in lines if "cancel key" in m and "WARN" in m]
        assert len(warns) == 1


# ---------------------------------------------------------------------------
# Daemon wiring: mouse PTT listener lifecycle, status field, apply_config
# ---------------------------------------------------------------------------

class _NoopRecorder:
    def start(self, path):
        pass

    def stop(self):
        return None

    def cancel(self):
        pass


class _StubPTT:
    """Stands in for MousePTTListener in daemon wiring tests."""
    grabbed = True
    last = None
    fail_start = None  # exception class raised from start()

    def __init__(self, **kw):
        self.kwargs = kw
        self.button = kw.get("button")
        self.button_grabbed = _StubPTT.grabbed
        self.summary = ["mouse PTT stub"]
        self.started = False
        self.stopped = False
        self.recording_calls: list = []
        _StubPTT.last = self

    def start(self):
        if _StubPTT.fail_start:
            raise _StubPTT.fail_start("no raw releases")
        self.started = True

    def stop(self):
        self.stopped = True

    def set_recording(self, active):
        self.recording_calls.append(active)


@pytest.fixture()
def ptt_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(dm.history_mod.paths, "history_file",
                        lambda: tmp_path / "history.jsonl")
    logs: list[str] = []
    monkeypatch.setattr(dm, "log", logs.append)
    notes: list = []
    monkeypatch.setattr(dm.ui, "notify",
                        lambda title, body="", timeout_ms=0, enabled=True:
                        notes.append((title, body)))
    holder = SimpleNamespace(logs=logs, notes=notes)

    def _make(button="", grabbed=True, fail_start=None):
        cfg = copy.deepcopy(DEFAULTS)
        if button:
            cfg["recording"]["push_to_talk_button"] = button
        _StubPTT.grabbed = grabbed
        _StubPTT.fail_start = fail_start
        _StubPTT.last = None
        monkeypatch.setattr(hotkey, "MousePTTListener", _StubPTT)
        d = dm.Daemon(cfg, recorder=_NoopRecorder(),
                      use_hotkey=False, use_sounds=False)
        holder.daemon = d
        return d

    holder.make = _make
    return holder


class TestDaemonMousePTTWiring:
    def test_configured_starts_listener_with_parsed_button(self, ptt_daemon):
        d = ptt_daemon.make(button="b8")
        assert d._start_mouse_ptt() is None
        stub = _StubPTT.last
        assert stub is not None and stub.started is True
        assert stub.kwargs["button"] == 8
        assert stub.kwargs["modifiers"] == []
        assert stub.kwargs["cancel_key"] == "Escape"
        assert any("mouse PTT stub" in m for m in ptt_daemon.logs)

    def test_unconfigured_starts_nothing(self, ptt_daemon):
        d = ptt_daemon.make()
        assert d._start_mouse_ptt() is None
        assert d._mouse_ptt is None
        assert _StubPTT.last is None
        assert not any("mouse PTT" in m for m in ptt_daemon.logs)

    def test_desktop_button_config_warns_and_lives(self, ptt_daemon):
        d = ptt_daemon.make(button="button1")
        error = d._start_mouse_ptt()
        assert error and "click" in error
        assert d._mouse_ptt is None
        assert any("WARN mouse PTT unavailable" in m for m in ptt_daemon.logs)
        assert ptt_daemon.notes  # desktop notification surfaced
        assert d.handle_request({"action": "status"})["ok"] is True

    def test_setup_failure_warns_and_lives(self, ptt_daemon):
        from fluidvoice.hotkey import HotkeyError
        d = ptt_daemon.make(button="button8", fail_start=HotkeyError)
        error = d._start_mouse_ptt()
        assert error and "raw" in error
        assert d._mouse_ptt is None
        assert any("WARN mouse PTT unavailable" in m for m in ptt_daemon.logs)

    def test_refused_startup_grab_warns(self, ptt_daemon):
        d = ptt_daemon.make(button="button8", grabbed=False)
        assert d._start_mouse_ptt() is None
        assert d._mouse_ptt is not None  # listener alive, retrying
        warns = [m for m in ptt_daemon.logs if "grab refused" in m]
        assert warns == ["WARN mouse PTT button 8 grab refused - "
                         "held by another client, will retry"]
        assert ptt_daemon.notes

    def test_status_field_follows_listener_health(self, ptt_daemon):
        d = ptt_daemon.make(button="button8")
        d._start_mouse_ptt()
        assert d.handle_request({"action": "status"})["mouse_ptt_grabbed"] is True
        d._mouse_ptt.button_grabbed = False
        assert d.handle_request({"action": "status"})["mouse_ptt_grabbed"] is False

    def test_status_none_when_unconfigured(self, ptt_daemon):
        d = ptt_daemon.make()
        assert d.handle_request({"action": "status"})["mouse_ptt_grabbed"] is None

    def test_tray_recording_forwards_set_recording(self, ptt_daemon):
        d = ptt_daemon.make(button="button8")
        d._start_mouse_ptt()
        stub = d._mouse_ptt
        d._tray_recording(True)
        assert stub.recording_calls == [True]
        d._tray_recording(False)
        assert stub.recording_calls == [True, False]

    def test_shutdown_stops_listener(self, ptt_daemon):
        d = ptt_daemon.make(button="button8")
        d._start_mouse_ptt()
        stub = d._mouse_ptt
        d.shutdown()
        assert stub.stopped is True

    def test_apply_config_restarts_on_button_change(self, ptt_daemon):
        d = ptt_daemon.make(button="button8")
        d._start_mouse_ptt()
        old = d._mouse_ptt
        d.cfg["recording"]["push_to_talk_button"] = "button9"
        feedback = d.apply_config(["recording.push_to_talk_button"])
        assert "mouse push-to-talk" in feedback["applied"]
        assert old.stopped is True
        assert d._mouse_ptt is not old and d._mouse_ptt.kwargs["button"] == 9

    def test_apply_config_restart_failure_surfaces(self, ptt_daemon):
        d = ptt_daemon.make(button="button1")  # unparseable on restart
        feedback = d.apply_config(["recording.push_to_talk_button"])
        assert feedback["errors"] and "mouse push-to-talk" in feedback["errors"][0]

    def test_default_config_changes_nothing(self, ptt_daemon):
        d = ptt_daemon.make()
        d._start_mouse_ptt()
        status = d.handle_request({"action": "status"})
        assert status["mouse_ptt_grabbed"] is None
        assert not any("mouse PTT" in m for m in ptt_daemon.logs)


# ---------------------------------------------------------------------------
# Doctor: the mouse PTT resolution + arm lines
# ---------------------------------------------------------------------------

class TestDoctorMousePTTLine:
    def _doctor(self, monkeypatch, tmp_path, cfg=None, status=None,
                socket_exists=True):
        from fluidvoice import control, doctor
        socket = tmp_path / "fluidvoice.sock"
        if socket_exists:
            socket.touch()
        monkeypatch.setattr(doctor.paths, "socket_path", lambda: socket)
        if not socket_exists:
            monkeypatch.setattr(control, "request",
                                lambda *a, **k: pytest.fail("must not query"))
            return doctor, (cfg or {})
        monkeypatch.setattr(control, "request",
                            lambda *a, **k: (status
                                             if not isinstance(status, Exception)
                                             else (_ for _ in ()).throw(status)))
        return doctor, (cfg or {})

    def test_not_configured(self, monkeypatch, tmp_path):
        doctor, cfg = self._doctor(monkeypatch, tmp_path)
        assert doctor._mouse_ptt_lines(cfg) == [
            "  push-to-talk button: not configured (keyboard hotkey only)"]

    def test_configured_with_arm_ok(self, monkeypatch, tmp_path):
        doctor, cfg = self._doctor(
            monkeypatch, tmp_path,
            cfg={"recording": {"push_to_talk_button": "button8"}},
            status={"mouse_ptt_grabbed": True})
        lines = doctor._mouse_ptt_lines(cfg)
        assert lines[0] == "  push-to-talk button: button8 " \
                           "(XGrabButton on button 8, modifiers none)"
        assert lines[1] == "  push-to-talk arm: ok"

    def test_configured_with_modifiers_and_blocked_arm(self, monkeypatch,
                                                       tmp_path):
        doctor, cfg = self._doctor(
            monkeypatch, tmp_path,
            cfg={"recording": {"push_to_talk_button": "b9",
                               "push_to_talk_modifiers": ["ctrl", "super"]}},
            status={"mouse_ptt_grabbed": False})
        lines = doctor._mouse_ptt_lines(cfg)
        assert "button9" in lines[0] and "ctrl+super" in lines[0]
        assert "BLOCKED" in lines[1] and "retrying" in lines[1]

    def test_invalid_spec(self, monkeypatch, tmp_path):
        doctor, cfg = self._doctor(
            monkeypatch, tmp_path,
            cfg={"recording": {"push_to_talk_button": "button3"}})
        lines = doctor._mouse_ptt_lines(cfg)
        assert len(lines) == 1 and "INVALID" in lines[0] and "click" in lines[0]

    def test_daemon_down_reports_resolution_only(self, monkeypatch, tmp_path):
        doctor, cfg = self._doctor(
            monkeypatch, tmp_path,
            cfg={"recording": {"push_to_talk_button": "button8"}},
            socket_exists=False)
        lines = doctor._mouse_ptt_lines(cfg)
        assert len(lines) == 1 and "XGrabButton" in lines[0]

    def test_daemon_unreachable(self, monkeypatch, tmp_path):
        from fluidvoice.control import ControlError
        doctor, cfg = self._doctor(
            monkeypatch, tmp_path,
            cfg={"recording": {"push_to_talk_button": "button8"}},
            status=ControlError("refused"))
        assert len(doctor._mouse_ptt_lines(cfg)) == 1

    def test_arm_disabled_when_daemon_reports_none(self, monkeypatch, tmp_path):
        doctor, cfg = self._doctor(
            monkeypatch, tmp_path,
            cfg={"recording": {"push_to_talk_button": "button8"}},
            status={"mouse_ptt_grabbed": None})
        assert doctor._mouse_ptt_lines(cfg)[1] == \
            "  push-to-talk arm: disabled (daemon not running it)"

    def test_run_prints_the_lines(self, monkeypatch, capsys, tmp_path):
        from fluidvoice import doctor
        monkeypatch.setattr(doctor.paths, "socket_path",
                            lambda: Path("/nonexistent/fluidvoice.sock"))
        doctor.run()
        out = capsys.readouterr().out
        assert "push-to-talk button:" in out
