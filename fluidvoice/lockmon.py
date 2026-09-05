"""Session lock/suspend watch for the daemon's pause_when_locked feature.

Sources, in priority order (all additive; the first flip wins and later
same-value signals are deduped):

1. logind session Lock()/Unlock() signals on the session object
   (`loginctl lock-session` path).
2. org.freedesktop.DBus.Properties.PropertiesChanged carrying LockedHint
   on the same session object - GNOME's path: it sets the property, and
   on this GNOME no screensaver D-Bus name is ever owned (verified live;
   relying on ActiveChanged alone would miss every GNOME lock).
3. Manager PrepareForSleep(bool) on /org/freedesktop/login1 - suspend
   counts as locked (a suspended screen with a live dictation is exactly
   the bug this feature fixes). Manager-level and session-independent:
   wired even when no graphical session resolves (sleep-only mode).
4. org.freedesktop.ScreenSaver / org.gnome.ScreenSaver ActiveChanged(bool)
   on the session bus, where a DE owns the names (KDE/XFCE paths).

Manager SessionRemoved/SessionNew swap the watched session on
logout/login (re-resolve). A 5 s LockedHint property poll inside the
GLib loop reconciles any missed signal. Without D-Bus or logind,
start() returns False and the feature is off (test boxes); with logind
but no resolvable graphical session the monitor runs sleep-only
(PrepareForSleep wired, start() True). The monitor fires
on_change(locked) ONLY on transitions (see _apply) - callers can treat
every callback as an edge.

Session path resolution (probe-verified): $XDG_SESSION_ID ->
/org/freedesktop/login1/session/<id> first (validated against the live
bus); else Manager.GetSessionByPID(os.getpid()), which works only for
processes inside a session scope (the old GNOME autostart); else
Manager.ListSessions() picks the best graphical user session of our own
UID - processes under the systemd USER unit live in user.slice
(user@1000.service), NOT in a session-XX.scope, so resolving by the
daemon's own PID finds nothing there and the ListSessions fallback is
what makes user-unit daemons work (live bug 2026-09-05).
/proc/self/sessionid is the KERNEL session id, not logind's - never use
it.
"""
from __future__ import annotations

import os
import re
import threading
from typing import Callable

LOGIN1 = "org.freedesktop.login1"
MANAGER_PATH = "/org/freedesktop/login1"
MANAGER_IFACE = "org.freedesktop.login1.Manager"
SESSION_IFACE = "org.freedesktop.login1.Session"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

# screensaver names a DE may own on the session bus (fallback sources)
SCREENSAVER_NAMES = ("org.freedesktop.ScreenSaver", "org.gnome.ScreenSaver")

RECONCILE_INTERVAL_S = 5.0

# logind session Type values that have a lock surface to watch ("desktop"
# is not a logind Type; real values: x11/wayland/tty/mir/unspecified)
GRAPHICAL_TYPES = ("x11", "wayland")
# ranking of session State: active beats online beats closing/closing-pending
STATE_RANK = {"active": 0, "online": 1}
# display names for the resolution step that found the session
VIA_DISPLAY = {"env": "XDG_SESSION_ID", "pid": "GetSessionByPID",
               "list": "ListSessions"}


def session_path_from_env() -> str | None:
    """/org/freedesktop/login1/session/<id> from $XDG_SESSION_ID, or None."""
    sid = (os.environ.get("XDG_SESSION_ID") or "").strip()
    if not sid or "/" in sid:  # defensive: it is a plain id, never a path
        return None
    return f"{MANAGER_PATH}/session/{sid}"


def _fifo_key(sid: str) -> tuple:
    """Sort key for earliest-created: logind ids are monotonic counters
    (c1 < c2 < ...), so the trailing digits ARE the creation order."""
    m = re.search(r"(\d+)$", str(sid))
    if m is None:
        return (1 << 62, str(sid))  # no digits: sorts after any numbered id
    return (int(m.group(1)), str(sid))


def pick_graphical_session(entries, uid, props_of, log=None):
    """Best same-UID graphical user session from ListSessions rows.

    entries: [(session_id, uid, username, seat, object_path)] exactly as
    Manager.ListSessions returns them. props_of(path) -> the session's
    property dict (State/Type/Class/...) or None for a dead path
    (bus-backed test seam). Filters: same uid, Class == "user" (no
    greeter/background rows), Type in {x11, wayland} - a tty session has
    no lock surface to watch, so graphical type is a hard filter, not a
    rank. Ranking: (STATE_RANK[State], FIFO(session_id), list_index),
    i.e. active first, then online, ties to the earliest-created.
    Pure ranking: no imports, no bus. Returns (path, meta) with
    meta = {"id", "uid", "State", "Type"} or None."""
    best = None
    best_key = None
    for idx, entry in enumerate(entries):
        row = list(entry) + [None] * 5
        sid, euid, _user, _seat, path = row[:5]
        if path is None:
            continue
        try:
            if int(euid) != int(uid):
                continue  # someone else's session
        except (TypeError, ValueError):
            continue
        props = props_of(path) if props_of is not None else None
        if not props:
            continue  # dead path (UnknownObject) or unreadable
        if props.get("Class") != "user":
            continue  # greeter / background class
        if props.get("Type") not in GRAPHICAL_TYPES:
            continue  # tty / mir / unspecified: nothing to watch
        state = props.get("State")
        key = (STATE_RANK.get(state, 2), _fifo_key(sid), idx)
        if best is None or key < best_key:
            best = (str(path), {"id": str(sid), "uid": int(uid),
                                "State": state, "Type": props.get("Type")})
            best_key = key
    return best


class LockMonitor:
    """Flips on_change(locked: bool) on lock/unlock/suspend transitions.

    Transitions only: the monitor dedups, so two lock signals in a row
    fire one callback. Every handler is a plain method (directly
    unit-testable without a bus); start() wires them to real signals."""

    def __init__(self, on_change: Callable[[bool], None],
                 log: Callable[[str], None] = (lambda m: None)):
        self._on_change = on_change
        self._log = log
        self._locked = False
        self._applied = False  # any _apply ran (initial state counts)
        self._loop = None
        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._started: bool | None = None  # tri-state: thread reports
        self._bus = None
        self._session_obj = None
        self.session_path: str | None = None
        self._via: str | None = None  # env | pid | list
        self._receivers: list = []  # (handler, signal, iface, path) tokens
        self._resolving = False  # re-entrancy guard for re-resolve

    # -- state machine --------------------------------------------------------

    @property
    def locked(self) -> bool:
        return self._locked

    def _apply(self, locked: bool, source: str) -> bool:
        """Apply one observation; fire on_change only on a flip. Returns
        True when the state actually changed (initial observation from
        False counts as a change only if it is a lock - an unlocked start
        is the assumed baseline and stays silent)."""
        locked = bool(locked)
        changed = self._locked != locked
        self._locked = locked
        self._applied = True
        if changed:
            try:
                self._on_change(locked)
            except Exception as e:  # noqa: BLE001 - must not kill the bus loop
                self._log(f"lock state callback failed "
                          f"({e.__class__.__name__}: {e})")
        return changed

    # -- signal handlers (plain methods: directly testable) -------------------

    def _on_session_lock(self) -> None:
        self._apply(True, "logind Lock")

    def _on_session_unlock(self) -> None:
        self._apply(False, "logind Unlock")

    def _on_session_props_changed(self, iface, props, _signature) -> None:
        # GNOME's path: the session sets LockedHint instead of emitting Lock
        if "LockedHint" in (props or {}):
            self._apply(bool(props["LockedHint"]), "LockedHint")

    def _on_prepare_for_sleep(self, sleeping) -> None:
        self._apply(bool(sleeping), "PrepareForSleep")

    def _on_screensaver_active(self, active) -> None:
        self._apply(bool(active), "screensaver ActiveChanged")

    # -- session resolution -----------------------------------------------------

    @staticmethod
    def _props_of(bus):
        """props_of(path) -> session property dict, or None when the path
        is dead (any D-Bus error, e.g. UnknownObject on a stale
        $XDG_SESSION_ID). bus None (manager-only test seam): every
        candidate validates as live - there is no bus to ask."""
        if bus is None:
            return lambda path: {}
        import dbus

        def props_of(path):
            try:
                obj = bus.get_object(LOGIN1, str(path))
                props = dbus.Interface(obj, PROPS_IFACE)
                got = props.GetAll(SESSION_IFACE, timeout=5)
                return {str(k): v for k, v in dict(got).items()}
            except Exception:  # noqa: BLE001 - dead/unreadable path
                return None
        return props_of

    def _resolve(self, bus=None, manager=None):
        """Resolution chain, first validated hit wins:
        1. $XDG_SESSION_ID (validated - a stale imported env var after a
           logout must not pin a dead session),
        2. Manager.GetSessionByPID(os.getpid()) (NoSessionForPID is an
           EXPECTED outcome under the systemd user unit: swallowed
           quietly, the chain continues),
        3. Manager.ListSessions() -> pick_graphical_session (validated).
        Returns (path, via, meta) or None. Only a total failure logs
        ("lock watch: session lookup failed" once, with the first
        underlying error); when a later step rescues an earlier failure,
        nothing is logged. Sets self._via."""
        bus = bus if bus is not None else self._bus
        props_of = self._props_of(bus)
        first_error: list = []

        def _note(e) -> None:
            if not first_error:
                first_error.append(e)

        # 1) env (validated)
        cand = session_path_from_env()
        if cand is not None:
            meta = props_of(cand)
            if meta is not None:
                self._via = "env"
                return cand, "env", meta
            _note(RuntimeError(f"stale $XDG_SESSION_ID session {cand}"))

        # 2) own PID (NoSessionForPID is expected under the user unit)
        mgr = manager
        if mgr is None and bus is not None:
            try:
                import dbus
                mgr = dbus.Interface(bus.get_object(LOGIN1, MANAGER_PATH),
                                     MANAGER_IFACE)
            except Exception as e:  # noqa: BLE001 - best-effort chain
                _note(e)
        if mgr is not None:
            cand = None
            try:
                result = mgr.GetSessionByPID(os.getpid(), timeout=5)
                cand = str(result) if result else None
            except Exception as e:  # noqa: BLE001 - incl. NoSessionForPID
                _note(e)
            if cand is not None:
                meta = props_of(cand)
                if meta is not None:
                    self._via = "pid"
                    return cand, "pid", meta
                _note(RuntimeError(f"session {cand} vanished"))

        # 3) ListSessions fallback (user-slice daemons land here)
        if mgr is not None and hasattr(mgr, "ListSessions"):
            try:
                entries = mgr.ListSessions(timeout=5)
            except Exception as e:  # noqa: BLE001 - best-effort chain
                _note(e)
                entries = None
            if entries is not None:
                picked = pick_graphical_session(entries, os.getuid(),
                                                props_of, log=self._log)
                if picked is not None:
                    path, meta = picked
                    self._via = "list"
                    return path, "list", meta

        if first_error:
            e = first_error[0]
            self._log(f"lock watch: session lookup failed "
                      f"({e.__class__.__name__}: {e})")
        return None

    def _session_path(self, bus=None, manager=None) -> str | None:
        """Logind session object path for THIS process (the resolution
        chain above; see the module docstring). None when nothing
        resolves. `bus`/`manager` are test seams standing in for the
        SystemBus and the Manager proxy."""
        resolved = self._resolve(bus=bus, manager=manager)
        return resolved[0] if resolved is not None else None

    # -- wiring helpers (plain methods: testable with fake proxies) -----------

    def _wire_manager(self, manager) -> None:
        """Session-independent manager signals: PrepareForSleep (suspend
        counts as locked - the sleep-only belt-and-braces path) plus
        SessionRemoved/SessionNew for the logout/login swap."""
        manager.connect_to_signal("PrepareForSleep",
                                  self._on_prepare_for_sleep,
                                  dbus_interface=MANAGER_IFACE)
        manager.connect_to_signal("SessionRemoved",
                                  self._on_session_removed,
                                  dbus_interface=MANAGER_IFACE)
        manager.connect_to_signal("SessionNew",
                                  self._on_session_new,
                                  dbus_interface=MANAGER_IFACE)

    def _watch_session(self, bus, path, via, meta=None) -> None:
        """Subscribe to one session's lock surface: Lock/Unlock signals
        plus a path-scoped PropertiesChanged for LockedHint (GNOME's
        path). Shared by the initial run and every re-resolve."""
        path = str(path)
        session = bus.get_object(LOGIN1, path)
        self._session_obj = session
        self.session_path = path
        self._via = via
        session.connect_to_signal("Lock", self._on_session_lock,
                                  dbus_interface=SESSION_IFACE)
        session.connect_to_signal("Unlock", self._on_session_unlock,
                                  dbus_interface=SESSION_IFACE)
        bus.add_signal_receiver(
            self._on_session_props_changed,
            signal_name="PropertiesChanged",
            dbus_interface=PROPS_IFACE, path=path)
        self._receivers = [
            (self._on_session_lock, "Lock", SESSION_IFACE, path),
            (self._on_session_unlock, "Unlock", SESSION_IFACE, path),
            (self._on_session_props_changed, "PropertiesChanged",
             PROPS_IFACE, path),
        ]
        sid = path.rsplit("/", 1)[-1]
        meta = meta or {}
        parts = [str(meta[k]) for k in ("State", "Type") if meta.get(k)]
        if meta.get("uid") is not None:
            parts.append(f"uid {meta['uid']}")
        detail = ", ".join(parts) if parts else "live"
        self._log(f"lock watch: session {sid} via "
                  f"{VIA_DISPLAY.get(via, via)} ({detail})")
        self._reconcile()  # initial truth (deduped: no spurious flip)

    def _detach_session(self, bus) -> None:
        """Best-effort unsubscribe from the watched session. Correctness
        does not depend on removal succeeding: the old receivers are
        path-scoped to a dead path and can never fire - removal is
        hygiene."""
        tokens, self._receivers = self._receivers, []
        self._session_obj = None
        self.session_path = None
        self._via = None
        if bus is None:
            return
        for handler, signal_name, iface, path in tokens:
            try:
                bus.remove_signal_receiver(handler,
                                           signal_name=signal_name,
                                           dbus_interface=iface, path=path)
            except Exception:  # noqa: BLE001 - hygiene only
                pass

    def _on_session_removed(self, sid, path) -> None:
        """logind Manager SessionRemoved(session_id, object_path): when
        the WATCHED session closed (logout), re-resolve - a login into a
        fresh session must be picked up."""
        if self.session_path is None or str(path) != self.session_path:
            return  # someone else's session closed
        self._log(f"lock watch: session {sid} closed - re-resolving")
        self._reresolve()

    def _on_session_new(self, sid, path) -> None:
        """logind Manager SessionNew: in sleep-only mode the new session
        may be ours - opportunistic re-resolve."""
        if self.session_path is not None or not self._started:
            return  # already watching a session / not running
        self._reresolve()

    def _reresolve(self) -> None:
        """Detach + re-run the full resolution chain (guarded: overlapping
        signals cannot double-subscribe). A hit watches the new session
        (reconcile dedups), a miss drops back to sleep-only mode."""
        if self._resolving:
            return
        self._resolving = True
        try:
            bus = self._bus
            if bus is None:
                return
            self._detach_session(bus)
            resolved = self._resolve(bus=bus)
            if resolved is not None:
                path, via, meta = resolved
                self._watch_session(bus, path, via, meta)
            else:
                self._log("lock watch: no graphical session after "
                          "re-resolve (suspend-only)")
        finally:
            self._resolving = False

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> bool:
        """Run the whole setup on the monitor thread and report success:
        wire the manager signals, resolve the session, subscribe to every
        source, run the GLib loop. Returns False (logged) when dbus/GLib
        is missing or logind is absent. With logind but NO graphical
        session (genuinely headless) the monitor still starts in
        sleep-only mode - PrepareForSleep keeps suspend gating - and
        start() returns True (status() disambiguates).

        NB: the bus connection is created on the thread AFTER
        DBusGMainLoop(set_as_default=True) - dbus-python caches
        connections per process, and a SystemBus created without a main
        loop attached can never receive signals (live-verified: the
        subscriptions raise "D-Bus connections must be attached to a main
        loop"). The tray owns the SESSION bus; the SYSTEM bus is ours."""
        try:
            import dbus  # noqa: F401
            from dbus.mainloop.glib import DBusGMainLoop  # noqa: F401
            from gi.repository import GLib  # noqa: F401
        except Exception as e:
            self._log(f"lock watch unavailable ({e.__class__.__name__}: {e})")
            return False
        self._started: bool | None = None  # tri-state until the thread says
        ready = threading.Event()
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run, args=(ready,),
                                        name="fluidvoice-lockmon", daemon=True)
        self._thread.start()
        ready.wait(timeout=8)
        return bool(self._started)

    def _run(self, ready: threading.Event) -> None:
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
        loop = None
        try:
            DBusGMainLoop(set_as_default=True)
            bus = dbus.SystemBus()
            self._bus = bus
            # manager signals FIRST: they are session-independent and are
            # the belt-and-braces path (sleep-only mode keeps suspend gated)
            self._wire_manager(bus.get_object(LOGIN1, MANAGER_PATH))
            self._resolving = True  # SessionNew during startup is a noop
            try:
                resolved = self._resolve(bus=bus)
            finally:
                self._resolving = False
            if resolved is None:
                self._log("lock watch unavailable (no logind session for "
                          "this process - headless?)")
                # sleep-only mode: manager PrepareForSleep stays wired
            else:
                path, via, meta = resolved
                self._watch_session(bus, path, via, meta)
            # screensaver fallback: additive, only where a DE owns the names
            try:
                sbus = dbus.SessionBus()
                for name in SCREENSAVER_NAMES:
                    sbus.add_signal_receiver(
                        self._on_screensaver_active,
                        signal_name="ActiveChanged",
                        dbus_interface=name)
            except Exception:
                pass  # optional source
            loop = GLib.MainLoop()
            self._loop = loop
            GLib.timeout_add_seconds(int(RECONCILE_INTERVAL_S),
                                     self._reconcile_loop)
            self._started = True  # both session and sleep-only modes
        except Exception as e:  # noqa: BLE001 - best-effort contract
            self._log(f"lock watch failed ({e.__class__.__name__}: {e})")
            self._started = False
        finally:
            ready.set()
        if loop is not None and not self._stop_flag.is_set():
            loop.run()

    def _reconcile(self) -> bool:
        """Read the session's LockedHint property; deduped through _apply.
        A dead session object (UnknownObject: a logout we missed) triggers
        a re-resolve instead of polling a corpse forever. Returns True for
        the GLib timeout (keep polling)."""
        if self._session_obj is None:
            return True  # sleep-only mode: nothing to poll
        try:
            import dbus
            props = dbus.Interface(self._session_obj, PROPS_IFACE)
            hint = bool(props.Get(SESSION_IFACE, "LockedHint", timeout=5))
            self._apply(hint, "reconcile")
        except Exception as e:  # noqa: BLE001 - bus hiccup or dead path
            if self.session_path is not None and \
                    "UnknownObject" in f"{e.__class__.__name__}: {e}":
                self._log("lock watch: session object vanished - "
                          "re-resolving")
                self._reresolve()
        return True  # GLib: keep the timeout alive

    def _reconcile_loop(self) -> bool:
        if self._stop_flag.is_set():
            return False  # GLib: drop the timeout
        return self._reconcile()

    def status(self) -> dict:
        """Live watch state for the daemon status surface / doctor:
        mode is "session" (watching session_path), "sleep-only"
        (PrepareForSleep wired, no graphical session) or "off"."""
        if self.session_path is not None:
            mode = "session"
        elif self._started:
            mode = "sleep-only"
        else:
            mode = "off"
        return {"active": bool(self._started), "mode": mode,
                "session": self.session_path, "via": self._via,
                "locked": self._locked}

    def stop(self) -> None:
        """Best-effort, idempotent."""
        self._stop_flag.set()
        loop, self._loop = self._loop, None
        if loop is not None:
            try:
                loop.quit()
            except Exception:
                pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2)
