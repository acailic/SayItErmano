"""Lock suppression: lockmon's transition-only state machine + the daemon's
locked gate (hotkeys ignored, active dictation cancelled, tooltip notes
`paused (locked)`). The bus wiring itself is a live concern (documented
manual check in docs/STATUS.md); here every handler is driven directly,
exactly the way D-Bus would call it."""
from __future__ import annotations

import copy
import math
import os
import struct
import sys
import types
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from fluidvoice import daemon as dm
from fluidvoice.config import DEFAULTS
from fluidvoice.lockmon import (
    MANAGER_PATH,
    LockMonitor,
    pick_graphical_session,
    session_path_from_env,
)

# ---------------------------------------------------------------------------
# LockMonitor: transitions only
# ---------------------------------------------------------------------------

def _monitor(logs=None):
    flips: list[bool] = []
    logs = logs if logs is not None else []
    mon = LockMonitor(on_change=flips.append, log=logs.append)
    return mon, flips, logs


class TestApplyDedup:
    def test_same_value_no_callback(self):
        mon, flips, _ = _monitor()
        mon._apply(True, "Lock")
        mon._apply(True, "LockedHint")  # GNOME's duplicate source
        mon._apply(True, "reconcile")
        assert flips == [True]
        assert mon.locked is True

    def test_flip_sequence(self):
        mon, flips, _ = _monitor()
        mon._apply(True, "Lock")
        mon._apply(False, "Unlock")
        mon._apply(False, "reconcile")
        mon._apply(True, "PrepareForSleep")
        assert flips == [True, False, True]

    def test_initial_unlocked_stays_silent(self):
        # an unlocked start is the assumed baseline: no callback
        mon, flips, _ = _monitor()
        mon._apply(False, "reconcile")
        assert flips == []
        mon._apply(True, "reconcile")
        assert flips == [True]

    def test_callback_failure_is_contained(self):
        mon = LockMonitor(on_change=lambda l: (_ for _ in ()).throw(
            RuntimeError("boom")), log=(lambda m: None))
        assert mon._apply(True, "Lock") is True  # state applied anyway
        assert mon.locked is True


class TestSignalHandlers:
    def test_logind_lock_unlock(self):
        mon, flips, _ = _monitor()
        mon._on_session_lock()
        mon._on_session_unlock()
        assert flips == [True, False]

    def test_properties_changed_locked_hint(self):
        mon, flips, _ = _monitor()
        mon._on_session_props_changed("org.freedesktop.login1.Session",
                                      {"LockedHint": True}, [])
        assert flips == [True]
        mon._on_session_props_changed("org.freedesktop.login1.Session",
                                      {"IdleHint": True}, [])  # not ours
        assert flips == [True]

    def test_prepare_for_sleep(self):
        mon, flips, _ = _monitor()
        mon._on_prepare_for_sleep(True)
        mon._on_prepare_for_sleep(False)
        assert flips == [True, False]

    def test_screensaver_active_changed(self):
        mon, flips, _ = _monitor()
        mon._on_screensaver_active(True)
        mon._on_screensaver_active(False)
        assert flips == [True, False]

    def test_one_flip_across_sources(self):
        # Lock signal THEN PropertiesChanged with the same value: one flip
        mon, flips, _ = _monitor()
        mon._on_session_lock()
        mon._on_session_props_changed("s", {"LockedHint": True}, [])
        mon._on_screensaver_active(True)
        assert flips == [True]


class TestSessionPath:
    def test_env_id_builds_path(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_ID", "4")
        assert session_path_from_env() == f"{MANAGER_PATH}/session/4"

    def test_env_id_missing_or_weird(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_ID", raising=False)
        assert session_path_from_env() is None
        monkeypatch.setenv("XDG_SESSION_ID", "  ")
        assert session_path_from_env() is None
        monkeypatch.setenv("XDG_SESSION_ID", "../evil")
        assert session_path_from_env() is None

    def test_env_wins_over_pid_lookup(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_ID", "7")

        class _Mgr:
            def GetSessionByPID(self, *_a, **_k):
                raise AssertionError("must not be called")

        mon, _, _ = _monitor()
        assert mon._session_path(manager=_Mgr()) == \
            f"{MANAGER_PATH}/session/7"

    def test_pid_fallback(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_ID", raising=False)

        class _Mgr:
            def GetSessionByPID(self, pid, timeout=5):
                assert timeout
                return f"{MANAGER_PATH}/session/c1"

        mon, _, _ = _monitor()
        assert mon._session_path(manager=_Mgr()) == \
            f"{MANAGER_PATH}/session/c1"

    def test_pid_fallback_error_returns_none(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_ID", raising=False)

        class _Mgr:
            def GetSessionByPID(self, *_a, **_k):
                raise RuntimeError("NoSessionForPID")

        mon, _, logs = _monitor()
        assert mon._session_path(manager=_Mgr()) is None
        assert any("session lookup failed" in m for m in logs)

    def test_no_bus_no_manager(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_ID", raising=False)
        mon, _, _ = _monitor()
        assert mon._session_path() is None


# ---------------------------------------------------------------------------
# ListSessions fallback (user-slice daemons: NoSessionForPID by own PID)
# ---------------------------------------------------------------------------

UID = os.getuid()


def _sess_path(sid: str) -> str:
    return f"{MANAGER_PATH}/session/{sid}"


def _row(sid, uid, path=None, user="nistrator", seat="seat0"):
    return (sid, uid, user, seat, path or _sess_path(sid))


def _graphical(state="active", stype="x11", cls="user", locked=False):
    return {"State": state, "Type": stype, "Class": cls,
            "LockedHint": locked}


def _props_of(mapping, dead=()):
    """props_of seam for pick_graphical_session / _resolve tests."""
    dead = set(dead)

    def props_of(path):
        if path in dead or str(path) not in mapping:
            return None
        return mapping[str(path)]
    return props_of


class TestPickGraphicalSession:
    """Pure ranking table - no bus, no monitor."""

    def test_picks_active_same_uid(self):
        rows = [_row("c1", UID), _row("c2", UID)]
        got = pick_graphical_session(
            rows, UID, _props_of({_sess_path("c1"): _graphical("online"),
                                  _sess_path("c2"): _graphical("active")}))
        assert got[0] == _sess_path("c2")
        assert got[1]["State"] == "active" and got[1]["id"] == "c2"

    def test_active_preferred_over_online(self):
        rows = [_row("c1", UID), _row("c2", UID)]
        got = pick_graphical_session(
            rows, UID, _props_of({_sess_path("c1"): _graphical("online"),
                                  _sess_path("c2"): _graphical("active")}))
        assert got[0] == _sess_path("c2")

    def test_fifo_tiebreak_lowest_id_wins(self):
        # two active x11 rows, NEWER listed first: earliest-created wins
        rows = [_row("c3", UID), _row("c2", UID)]
        got = pick_graphical_session(
            rows, UID, _props_of({p: _graphical("active")
                                  for p in (_sess_path("c2"),
                                            _sess_path("c3"))}))
        assert got[0] == _sess_path("c2")

    def test_closing_state_ranked_last(self):
        rows = [_row("c1", UID), _row("c2", UID)]
        got = pick_graphical_session(
            rows, UID, _props_of({_sess_path("c1"): _graphical("online"),
                                  _sess_path("c2"): _graphical("closing")}))
        assert got[0] == _sess_path("c1")

    def test_other_uid_ignored(self):
        rows = [_row("c1", UID + 1)]
        assert pick_graphical_session(
            rows, UID, _props_of({_sess_path("c1"): _graphical()})) is None

    def test_greeter_and_background_class_ignored(self):
        rows = [_row("c1", UID), _row("c2", UID)]
        got = pick_graphical_session(
            rows, UID, _props_of({
                _sess_path("c1"): _graphical(cls="greeter"),
                _sess_path("c2"): _graphical(cls="background")}))
        assert got is None

    def test_tty_never_shadows_graphical(self):
        # documented deviation: graphical Type is a hard FILTER, so an
        # active tty must not beat an online x11 session
        rows = [_row("c1", UID), _row("c2", UID)]
        got = pick_graphical_session(
            rows, UID, _props_of({_sess_path("c1"): _graphical("active",
                                                                 "tty"),
                                  _sess_path("c2"): _graphical("online",
                                                                 "wayland")}))
        assert got[0] == _sess_path("c2")
        assert got[1]["Type"] == "wayland"

    def test_dead_path_skipped(self):
        rows = [_row("c1", UID), _row("c2", UID)]
        got = pick_graphical_session(
            rows, UID, _props_of({_sess_path("c1"): _graphical(),
                                  _sess_path("c2"): _graphical()},
                                 dead=(_sess_path("c1"),)))
        assert got[0] == _sess_path("c2")

    def test_wayland_and_x11_both_graphical(self):
        rows = [_row("c1", UID)]
        for stype in ("x11", "wayland"):
            got = pick_graphical_session(
                rows, UID, _props_of({_sess_path("c1"):
                                      _graphical("active", stype)}))
            assert got is not None

    def test_empty_and_unmatched(self):
        assert pick_graphical_session([], UID, _props_of({})) is None
        assert pick_graphical_session([_row("c1", UID)], UID,
                                      _props_of({})) is None


class _FakeManager:
    """Manager proxy fake: configurable GetSessionByPID outcome,
    ListSessions rows, and a connect_to_signal recorder."""

    def __init__(self, pid_result=None, pid_error=None, sessions=None):
        self.pid_result = pid_result
        self.pid_error = pid_error
        self.sessions = list(sessions or [])
        self.signals: list = []
        self.list_calls = 0

    def GetSessionByPID(self, pid, timeout=5):
        if self.pid_error is not None:
            raise self.pid_error
        return self.pid_result

    def ListSessions(self, timeout=5):
        self.list_calls += 1
        return list(self.sessions)

    def connect_to_signal(self, signal_name, handler, dbus_interface=None):
        self.signals.append((signal_name, handler, dbus_interface))


class _FakeSessionObj:
    """logind session object fake: connect_to_signal recorder + props."""

    def __init__(self, props=None, get_error=None):
        self.props = dict(props or {})
        self.get_error = get_error
        self.signals: list = []

    def connect_to_signal(self, signal_name, handler, dbus_interface=None):
        self.signals.append((signal_name, handler, dbus_interface))

    def Get(self, iface, name, timeout=5):
        if self.get_error is not None:
            raise self.get_error
        try:
            return self.props[name]
        except KeyError as e:
            raise RuntimeError(f"UnknownObject-ish: no {name}") from e

    def GetAll(self, iface, timeout=5):
        return dict(self.props)


class _FakeBus:
    """SystemBus fake: routes the manager path and known session paths,
    records add/remove_signal_receiver."""

    def __init__(self, manager, sessions=None):
        self.manager = manager
        self.sessions = dict(sessions or {})  # path -> _FakeSessionObj
        self.receivers: list = []
        self.removed: list = []

    def add_session(self, sid, obj):
        self.sessions[_sess_path(sid)] = obj

    def get_object(self, service, path):
        if str(path) == MANAGER_PATH:
            return self.manager
        if str(path) in self.sessions:
            return self.sessions[str(path)]
        raise KeyError(f"no object at {path}")

    def add_signal_receiver(self, handler, signal_name=None,
                            dbus_interface=None, path=None):
        self.receivers.append((handler, signal_name, dbus_interface, path))

    def remove_signal_receiver(self, handler, signal_name=None,
                               dbus_interface=None, path=None):
        self.removed.append((handler, signal_name, dbus_interface, path))


def _fake_dbus_modules(monkeypatch, bus):
    """sys.modules fakes so bus-level code paths (resolve, reconcile,
    _run) run against _FakeBus without any real D-Bus: dbus.Interface is
    the identity (fakes already speak Get/GetAll), SystemBus/SessionBus
    return the fake bus, GLib's MainLoop.run returns at once."""
    dbus_mod = types.ModuleType("dbus")
    dbus_mod.Interface = lambda obj, iface: obj
    dbus_mod.SystemBus = lambda: bus
    dbus_mod.SessionBus = lambda: bus
    mainloop_mod = types.ModuleType("dbus.mainloop")
    glib_dbus_mod = types.ModuleType("dbus.mainloop.glib")
    glib_dbus_mod.DBusGMainLoop = lambda **_kw: None
    mainloop_mod.glib = glib_dbus_mod
    dbus_mod.mainloop = mainloop_mod

    class _Loop:
        def __init__(self):
            self.quits = 0

        def run(self):
            return None

        def quit(self):
            self.quits += 1

    glib_mod = types.ModuleType("gi.repository.GLib", "fake GLib")
    glib_mod.MainLoop = _Loop
    glib_mod.timeout_add_seconds = lambda seconds, fn: 1
    repo_mod = types.ModuleType("gi.repository")
    repo_mod.GLib = glib_mod
    gi_mod = types.ModuleType("gi")
    gi_mod.repository = repo_mod
    for name, mod in (("dbus", dbus_mod), ("dbus.mainloop", mainloop_mod),
                      ("dbus.mainloop.glib", glib_dbus_mod),
                      ("gi", gi_mod), ("gi.repository", repo_mod)):
        monkeypatch.setitem(sys.modules, name, mod)


class TestListSessionsFallback:
    """The user-unit resolution chain (b): NoSessionForPID by own PID is
    swallowed quietly and ListSessions rescues the lookup."""

    NO_SESSION_FOR_PID = RuntimeError(
        "org.freedesktop.login1.NoSessionForPID: PID 1 does not belong "
        "to any known session")

    def _bus_with_c5(self, sessions=None):
        mgr = _FakeManager(pid_error=self.NO_SESSION_FOR_PID,
                           sessions=sessions if sessions is not None else
                           [_row("c5", UID)])
        sess = _FakeSessionObj(_graphical())
        bus = _FakeBus(mgr, {_sess_path("c5"): sess})
        return mgr, sess, bus

    def _no_env(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_ID", raising=False)

    def test_no_session_for_pid_falls_back(self, monkeypatch):
        self._no_env(monkeypatch)
        mgr, sess, bus = self._bus_with_c5()
        _fake_dbus_modules(monkeypatch, bus)
        mon, _, logs = _monitor()
        assert mon._session_path(bus=bus, manager=mgr) == _sess_path("c5")
        assert mon._via == "list"
        assert mgr.list_calls == 1
        assert not any("lookup failed" in m for m in logs)
        assert not any("unavailable" in m for m in logs)

    def test_fallback_skips_other_uid_greeter_and_tty(self, monkeypatch):
        self._no_env(monkeypatch)
        rows = [_row("c1", UID + 1), _row("c2", UID), _row("c3", UID),
                _row("c5", UID)]
        mgr = _FakeManager(pid_error=self.NO_SESSION_FOR_PID, sessions=rows)
        props = {_sess_path("c1"): _graphical(),
                 _sess_path("c2"): _graphical(cls="greeter"),
                 _sess_path("c3"): _graphical(stype="tty"),
                 _sess_path("c5"): _graphical()}
        objs = {p: _FakeSessionObj(pr) for p, pr in props.items()}
        bus = _FakeBus(mgr, objs)
        _fake_dbus_modules(monkeypatch, bus)
        mon, _, _ = _monitor()
        assert mon._session_path(bus=bus, manager=mgr) == _sess_path("c5")

    def test_active_preferred_over_online(self, monkeypatch):
        self._no_env(monkeypatch)
        rows = [_row("c1", UID), _row("c2", UID)]
        mgr = _FakeManager(pid_error=self.NO_SESSION_FOR_PID, sessions=rows)
        objs = {_sess_path("c1"): _FakeSessionObj(_graphical("online")),
                _sess_path("c2"): _FakeSessionObj(_graphical("active"))}
        bus = _FakeBus(mgr, objs)
        _fake_dbus_modules(monkeypatch, bus)
        mon, _, _ = _monitor()
        assert mon._session_path(bus=bus, manager=mgr) == _sess_path("c2")

    def test_pid_hit_skips_list(self, monkeypatch):
        self._no_env(monkeypatch)

        class _Mgr(_FakeManager):
            def ListSessions(self, timeout=5):  # pragma: no cover
                raise AssertionError("pid hit must not call ListSessions")

        mgr = _Mgr(pid_result=_sess_path("c9"))
        sess = _FakeSessionObj(_graphical())
        bus = _FakeBus(mgr, {_sess_path("c9"): sess})
        _fake_dbus_modules(monkeypatch, bus)
        mon, _, _ = _monitor()
        assert mon._session_path(bus=bus, manager=mgr) == _sess_path("c9")
        assert mon._via == "pid"

    def test_stale_env_id_falls_through(self, monkeypatch):
        # $XDG_SESSION_ID survived a logout: the env path is DEAD
        # (no object) -> chain continues to pid -> list
        monkeypatch.setenv("XDG_SESSION_ID", "c4")
        mgr, sess, bus = self._bus_with_c5()
        _fake_dbus_modules(monkeypatch, bus)
        mon, _, logs = _monitor()
        assert mon._session_path(bus=bus, manager=mgr) == _sess_path("c5")
        assert mon._via == "list"
        assert not any("lookup failed" in m for m in logs)

    def test_no_sessions_still_logs_lookup_failure(self, monkeypatch):
        # chain end with nothing found: the one chain-end diagnostic
        self._no_env(monkeypatch)
        mgr = _FakeManager(pid_error=self.NO_SESSION_FOR_PID, sessions=[])
        bus = _FakeBus(mgr, {})
        _fake_dbus_modules(monkeypatch, bus)
        mon, _, logs = _monitor()
        assert mon._session_path(bus=bus, manager=mgr) is None
        assert any("session lookup failed" in m for m in logs)


class TestRunWiring:
    """(d) no sessions -> sleep-only: headless WARN, PrepareForSleep
    still wired, started True, status disambiguates."""

    def test_wire_manager_signals(self):
        mgr = _FakeManager()
        mon, _, _ = _monitor()
        mon._wire_manager(mgr)
        assert [s for s, _h, _i in mgr.signals] == \
            ["PrepareForSleep", "SessionRemoved", "SessionNew"]

    def test_headless_still_wires_sleep(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_ID", raising=False)
        mgr = _FakeManager(pid_error=RuntimeError(
            "org.freedesktop.login1.NoSessionForPID: nope"), sessions=[])
        bus = _FakeBus(mgr, {})
        _fake_dbus_modules(monkeypatch, bus)
        mon, flips, logs = _monitor()
        assert mon.start() is True  # sleep-only contract change
        assert any("headless" in m for m in logs)
        names = [s for s, _h, _i in mgr.signals]
        assert "PrepareForSleep" in names  # suspend still gates
        assert "SessionRemoved" in names and "SessionNew" in names
        assert mon.status() == {"active": True, "mode": "sleep-only",
                                "session": None, "via": None,
                                "locked": False}
        assert flips == []
        # suspend still flips through the manager signal
        mgr.signals[0][1](True)
        assert flips == [True]

    def test_status_off_before_start(self):
        mon, _, _ = _monitor()
        assert mon.status()["mode"] == "off"
        assert mon.status()["active"] is False


class TestReResolve:
    """(e) the watched session closes -> detach, re-resolve, rewatch."""

    def _watching(self, monkeypatch, sessions, watch="c7", pid_error=None):
        """Monitor already watching `watch` against a bus whose manager
        lists `sessions` rows (dict sid -> _FakeSessionObj)."""
        monkeypatch.delenv("XDG_SESSION_ID", raising=False)
        objs = {_sess_path(sid): obj for sid, obj in sessions.items()}
        rows = [_row(sid, UID) for sid in sessions]
        mgr = _FakeManager(pid_error=pid_error, sessions=rows)
        bus = _FakeBus(mgr, objs)
        _fake_dbus_modules(monkeypatch, bus)
        mon, flips, logs = _monitor()
        mon._bus = bus
        mon._watch_session(bus, _sess_path(watch), "list",
                           {"State": "active", "Type": "x11", "uid": UID})
        return mon, mgr, bus, flips, logs

    def test_removed_other_path_noop(self, monkeypatch):
        mon, mgr, bus, flips, logs = self._watching(
            monkeypatch, {"c7": _FakeSessionObj(_graphical())})
        mon._on_session_removed("c9", _sess_path("c9"))
        assert mon.session_path == _sess_path("c7")
        assert flips == []
        assert not any("re-resolv" in m for m in logs)

    def test_removed_watched_path_resubscribes(self, monkeypatch):
        new_obj = _FakeSessionObj(_graphical())
        mon, mgr, bus, flips, logs = self._watching(
            monkeypatch,
            {"c7": _FakeSessionObj(_graphical()), "c8": new_obj})
        old = bus.sessions[_sess_path("c7")]
        assert [s for s, _h, _i in old.signals] == ["Lock", "Unlock"]
        mgr.sessions = [_row("c8", UID)]  # logind drops the closed row
        mon._on_session_removed("c7", _sess_path("c7"))
        assert mon.session_path == _sess_path("c8")
        assert [s for s, _h, _i in new_obj.signals] == ["Lock", "Unlock"]
        assert len(bus.removed) == 3  # Lock, Unlock, PropertiesChanged
        assert flips == []  # deduped reconcile: no spurious flip
        assert any("re-resolv" in m for m in logs)

    def test_session_new_attaches_in_sleep_only(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_ID", raising=False)
        obj = _FakeSessionObj(_graphical())
        mgr = _FakeManager(sessions=[_row("c8", UID)])
        bus = _FakeBus(mgr, {_sess_path("c8"): obj})
        _fake_dbus_modules(monkeypatch, bus)
        mon, flips, logs = _monitor()
        mon._bus = bus
        mon._started = True  # sleep-only mode (no session watched)
        mon._on_session_new("c8", _sess_path("c8"))
        assert mon.session_path == _sess_path("c8")
        assert [s for s, _h, _i in obj.signals] == ["Lock", "Unlock"]
        assert flips == []

    def test_session_new_noop_when_watching(self, monkeypatch):
        mon, mgr, bus, flips, logs = self._watching(
            monkeypatch, {"c7": _FakeSessionObj(_graphical())})
        mon._on_session_new("c8", _sess_path("c8"))
        assert mon.session_path == _sess_path("c7")
        assert mgr.list_calls == 0

    def test_reentrancy_guard_prevents_double_subscribe(self, monkeypatch):
        new_obj = _FakeSessionObj(_graphical())
        mon, mgr, bus, flips, logs = self._watching(
            monkeypatch,
            {"c7": _FakeSessionObj(_graphical()), "c8": new_obj})
        mon._resolving = True  # a resolve is already in flight
        mon._on_session_removed("c7", _sess_path("c7"))
        assert mon.session_path == _sess_path("c7")  # untouched
        assert new_obj.signals == []

    def test_reconcile_unknown_object_triggers_reresolve(self, monkeypatch):
        dead = _FakeSessionObj(_graphical())
        new_obj = _FakeSessionObj(_graphical())
        mon, mgr, bus, flips, logs = self._watching(
            monkeypatch, {"c7": dead, "c8": new_obj})
        # the watched session's object went away without SessionRemoved
        dead.get_error = RuntimeError(
            "org.freedesktop.DBus.Error.UnknownObject: no such object")
        mgr.sessions = [_row("c8", UID)]  # and logind dropped the row
        assert mon._reconcile() is True
        assert mon.session_path == _sess_path("c8")
        assert any("vanished" in m for m in logs)


class TestRunWithFakeBus:
    """The done-criteria scenario end-to-end: mocked bus where
    GetSessionByPID raises NoSessionForPID and ListSessions returns one
    active graphical session of the same UID -> the watcher subscribes
    to that session's Lock/Unlock and the pause-on-lock state machine
    works identically."""

    def test_userslice_daemon_subscribes_listed_session(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_ID", raising=False)
        sess = _FakeSessionObj(_graphical())
        mgr = _FakeManager(
            pid_error=RuntimeError(
                "org.freedesktop.login1.NoSessionForPID: PID 1288740 does "
                "not belong to any known session"),
            sessions=[_row("c7", UID)])
        bus = _FakeBus(mgr, {_sess_path("c7"): sess})
        _fake_dbus_modules(monkeypatch, bus)
        flips: list = []
        logs: list = []
        mon = LockMonitor(on_change=flips.append, log=logs.append)
        assert mon.start() is True
        assert mon.session_path == _sess_path("c7")
        assert [s for s, _h, _i in sess.signals] == ["Lock", "Unlock"]
        prop_recv = [r for r in bus.receivers
                     if r[1] == "PropertiesChanged" and r[3] == _sess_path("c7")]
        assert len(prop_recv) == 1
        names = [s for s, _h, _i in mgr.signals]
        assert "PrepareForSleep" in names  # manager wired first
        st = mon.status()
        assert st["mode"] == "session" and st["via"] == "list"
        assert st["active"] is True
        assert any("lock watch: session c7 via ListSessions" in m
                   for m in logs)
        assert not any("lookup failed" in m for m in logs)
        assert not any("unavailable" in m for m in logs)
        # the state machine works identically on the listed session
        sess.signals[0][1]()   # Lock
        sess.signals[1][1]()   # Unlock
        assert flips == [True, False]
        mon.stop()


# ---------------------------------------------------------------------------
# Daemon lock gate
# ---------------------------------------------------------------------------

def _make_wav(path: Path) -> Path:
    n = 16000
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 16000)))
            for i in range(n)))
    return path


class _StubRecorder:
    """Records start/stop/cancel - asserts the cancel path was used."""

    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.cancelled = 0

    def start(self, path):
        _make_wav(path)
        self.started += 1

    def stop(self):
        self.stopped += 1
        return None  # no audio: the daemon logs "no audio captured"

    def cancel(self):
        self.cancelled += 1


@pytest.fixture()
def lockd(tmp_path, monkeypatch):
    """Daemon with stub recorder, quiet UI, injected log capture."""
    calls = {"notify": [], "sound": []}

    def fake_notify(title, body="", timeout_ms=2500, enabled=True):
        if enabled:
            calls["notify"].append((title, body))

    def fake_sound(which, volume=1.0, enabled=True):
        calls["sound"].append(which)

    logs: list[str] = []
    monkeypatch.setattr(dm, "log", logs.append)
    monkeypatch.setattr(dm.ui, "notify", fake_notify)
    monkeypatch.setattr(dm.ui, "play_sound", fake_sound)
    monkeypatch.setattr(dm.insertion, "active_window_class", lambda: "TestApp")
    monkeypatch.setattr(dm.history_mod.paths, "history_file",
                        lambda: tmp_path / "history.jsonl")

    def _make(pause_when_locked=True):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["pause_when_locked"] = pause_when_locked
        rec = _StubRecorder()
        d = dm.Daemon(cfg, recorder=rec, use_hotkey=False, use_sounds=False)
        return SimpleNamespace(d=d, rec=rec, logs=logs, calls=calls)

    return _make


class TestDaemonLockedGate:
    def test_toggle_ignored_while_locked(self, lockd):
        h = lockd()
        h.d._locked = True
        assert h.d.toggle() is False
        assert h.rec.started == 0
        assert h.d.recording is False
        assert "paused (locked)" in h.d._tray_tooltip()

    def test_lock_cancels_active_recording(self, lockd):
        h = lockd()
        assert h.d.toggle() is True
        h.d._locked = True
        h.d._on_locked(True)
        assert h.rec.cancelled == 1
        assert h.d.recording is False
        assert any(body == "Cancelled" for _t, body in h.calls["notify"])

    def test_unlock_resumes_normal_toggles(self, lockd):
        h = lockd()
        h.d._locked = True
        assert h.d.toggle() is False
        h.d._on_locked(False)
        h.d._locked = False
        assert h.d.toggle() is True
        h.d.cancel()  # hygiene: never leave the take's watchdog pending
        assert h.rec.started == 1

    def test_lock_logs_each_transition_once(self, lockd):
        h = lockd()
        h.d._locked = True
        h.d._on_locked(True)
        h.d.toggle()  # ignored quietly
        h.d.toggle()  # ignored quietly
        assert h.logs.count("screen locked - hotkeys paused") == 1
        assert h.logs.count("screen unlocked - hotkeys resumed") == 0
        h.d._on_locked(False)
        h.d._locked = False
        assert h.logs.count("screen unlocked - hotkeys resumed") == 1

    def test_rewrite_and_command_gated_while_locked(self, lockd):
        h = lockd()
        h.d._locked = True
        h.d.start_rewrite()
        h.d.start_command()
        assert h.rec.started == 0

    def test_lock_cancels_pending_command(self, lockd):
        h = lockd()
        h.d._commands.pending = True
        h.d._commands.session = SimpleNamespace(cancel=lambda: None)
        h.d._locked = True
        h.d._on_locked(True)
        assert h.d._commands.pending is False

    def test_socket_cancel_still_works_while_locked(self, lockd):
        h = lockd()
        assert h.d.toggle() is True
        h.d._locked = True
        resp = h.d.handle_request({"action": "cancel"})
        assert resp["ok"] is True and resp["recording"] is False
        assert h.rec.cancelled == 1

    def test_status_reports_locked(self, lockd):
        h = lockd()
        assert h.d.handle_request({"action": "status"})["locked"] is False
        h.d._locked = True
        assert h.d.handle_request({"action": "status"})["locked"] is True

    def test_pause_when_locked_false_never_gates(self, lockd):
        h = lockd(pause_when_locked=False)
        h.d._start_lockmon()
        assert h.d._lockmon is None  # monitor never started
        h.d._locked = True  # even a stale state cannot gate
        h.d._locked = False
        assert h.d.toggle() is True
        h.d.cancel()  # hygiene: never leave the take's watchdog pending

    def test_disabled_setting_logs_nothing(self, lockd):
        h = lockd(pause_when_locked=False)
        h.d._start_lockmon()
        assert not any("lock" in m for m in h.logs)


class TestDaemonLockmonWiring:
    def test_start_lockmon_success(self, lockd, monkeypatch):
        h = lockd()
        started = []

        class _StubMon:
            def __init__(self, on_change, log=None):
                self.on_change = on_change
                self.stopped = False

            def start(self):
                started.append(1)
                return True

            def status(self):
                return {"active": True, "mode": "session",
                        "session": f"{MANAGER_PATH}/session/c3",
                        "via": "list", "locked": False}

            def stop(self):
                self.stopped = True

        import fluidvoice.lockmon as lockmon
        monkeypatch.setattr(lockmon, "LockMonitor", _StubMon)
        h.d._start_lockmon()
        assert started == [1]
        assert h.d._lockmon is not None
        # the callback routes into the daemon's lock gate
        h.d._lockmon.on_change(True)
        h.d._locked = True
        assert "screen locked - hotkeys paused" in h.logs
        mon = h.d._lockmon
        h.d.shutdown()
        assert mon.stopped is True

    def test_start_lockmon_failure_continues(self, lockd, monkeypatch):
        h = lockd()

        class _StubMon:
            def __init__(self, on_change, log=None):
                pass

            def start(self):
                return False

            def stop(self):
                pass

        import fluidvoice.lockmon as lockmon
        monkeypatch.setattr(lockmon, "LockMonitor", _StubMon)
        h.d._start_lockmon()  # logged inside, daemon continues
        assert h.d._lockmon is None

    def test_apply_config_flips_lock_pause_live(self, lockd, monkeypatch):
        h = lockd()
        stopped = []

        class _StubMon:
            def __init__(self, on_change, log=None):
                pass

            def start(self):
                return True

            def stop(self):
                stopped.append(1)

        import fluidvoice.lockmon as lockmon
        monkeypatch.setattr(lockmon, "LockMonitor", _StubMon)
        h.d._lockmon = _StubMon(None)
        h.d._locked = True
        h.d.cfg["general"]["pause_when_locked"] = False
        feedback = h.d.apply_config(["general.pause_when_locked"])
        assert "lock pause" in feedback["applied"]
        assert stopped == [1]
        assert h.d._lockmon is None
        assert h.d._locked is False  # paused state cleared


class TestDaemonLockWatchStatus:
    """The lock_watch status surface + the _start_lockmon log lines."""

    def _stub(self, status, started=None):
        class _StubMon:
            def __init__(self, on_change, log=None):
                pass

            def start(self):
                if started is not None:
                    started.append(1)
                return True

            def status(self):
                return dict(status)

            def stop(self):
                pass
        return _StubMon

    def test_start_lockmon_logs_session_and_via(self, lockd, monkeypatch):
        h = lockd()
        import fluidvoice.lockmon as lockmon
        monkeypatch.setattr(lockmon, "LockMonitor", self._stub({
            "active": True, "mode": "session",
            "session": _sess_path("c3"), "via": "list", "locked": False}))
        h.d._start_lockmon()
        assert any("lock watch active (session c3 via ListSessions" in m
                   for m in h.logs)

    def test_start_lockmon_logs_suspend_only(self, lockd, monkeypatch):
        h = lockd()
        import fluidvoice.lockmon as lockmon
        monkeypatch.setattr(lockmon, "LockMonitor", self._stub({
            "active": True, "mode": "sleep-only", "session": None,
            "via": None, "locked": False}))
        h.d._start_lockmon()
        assert any("lock watch active (suspend-only: no graphical session"
                   in m for m in h.logs)
        assert not any("lock watch active (session" in m for m in h.logs)

    def test_status_includes_lock_watch(self, lockd):
        h = lockd()
        resp = h.d.handle_request({"action": "status"})
        lw = resp["lock_watch"]
        assert set(lw) == {"active", "mode", "session", "via", "locked"}
        assert lw["locked"] is h.d._locked
        assert lw["mode"] in ("session", "sleep-only", "off")

    def test_status_lock_watch_off_without_monitor(self, lockd):
        h = lockd(pause_when_locked=False)
        assert h.d._lockmon is None
        lw = h.d.handle_request({"action": "status"})["lock_watch"]
        assert lw == {"active": False, "mode": "off", "session": None,
                      "via": None, "locked": False}


class TestDoctorLockLine:
    """doctor's lock-watch line: session / sleep-only / disabled / down."""

    def _lines(self, monkeypatch, tmp_path, status, pause=True, alive=True):
        from fluidvoice import control as control_mod
        from fluidvoice import doctor
        from fluidvoice import paths as paths_mod
        sock = tmp_path / "sock"
        if alive:
            sock.touch()
        monkeypatch.setattr(paths_mod, "socket_path", lambda: sock)
        monkeypatch.setattr(control_mod, "request",
                            lambda action: (dict(status)
                                            if action == "status" else {}))
        cfg = {"general": {"pause_when_locked": pause}}
        return doctor._lock_watch_lines(cfg)

    def test_session_mode_line(self, monkeypatch, tmp_path):
        lines = self._lines(monkeypatch, tmp_path, status={
            "lock_watch": {"active": True, "mode": "session",
                           "session": _sess_path("c3"), "via": "list",
                           "locked": False}})
        assert lines == ["  lock watch: ok (watching session c3 via "
                         "ListSessions)"]

    def test_sleep_only_line(self, monkeypatch, tmp_path):
        lines = self._lines(monkeypatch, tmp_path, status={
            "lock_watch": {"active": True, "mode": "sleep-only",
                           "session": None, "via": None, "locked": False}})
        assert lines == ["  lock watch: suspend-only (no graphical session)"]

    def test_disabled_line(self, monkeypatch, tmp_path):
        lines = self._lines(monkeypatch, tmp_path, status={},
                            pause=False, alive=False)
        assert lines == ["  lock watch: disabled "
                         "(general.pause_when_locked = false)"]

    def test_daemon_down_line(self, monkeypatch, tmp_path):
        lines = self._lines(monkeypatch, tmp_path, status={},
                            alive=False)
        assert lines == ["  lock watch: unknown (daemon down)"]

    def test_older_daemon_line(self, monkeypatch, tmp_path):
        # daemon answers status but has no lock_watch key (pre-change)
        lines = self._lines(monkeypatch, tmp_path, status={"ok": True})
        assert lines == ["  lock watch: unknown (older daemon)"]

    def test_off_line(self, monkeypatch, tmp_path):
        lines = self._lines(monkeypatch, tmp_path, status={
            "lock_watch": {"active": False, "mode": "off", "session": None,
                           "via": None, "locked": False}})
        assert lines == ["  lock watch: off"]
