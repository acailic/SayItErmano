"""Global hotkey on Wayland via the GlobalShortcuts portal v2 (ADR-0006).

Wayland has no global key grabs; the sanctioned trigger is the
org.freedesktop.portal.GlobalShortcuts interface (GNOME 48+, Plasma
5.27+/6, xdg-desktop-portal-hyprland; sway and COSMIC ship NO backend -
there the documented fallback is a compositor bind running
``sayit-ermano --toggle`` over the control socket).

Implementation rides the pure-Python ``global-shortcut-portal`` package
(dbus-next underneath) instead of hand-rolled D-Bus. Semantics the
listener is responsible for:

- ``Activated``/``Deactivated`` signal pair == press/release edges ->
  native hold push-to-talk. In "toggle" mode only the activation fires
  (a tap would otherwise toggle twice); "both" disambiguates with the
  same 250 ms window the X11 hotkey uses.
- BindShortcuts is re-called on EVERY portal session (a fresh session
  per listener start): KDE may forget bindings across portal restarts,
  and the portal spec allows BindShortcuts once per session only.
- The config's preferred trigger is SENT but never REQUIRED: the DE
  decides (and remembers) the actual key per app id; the bind result's
  trigger description is logged for the user. Inside Flatpak the app id
  is always set, which also dodges the GNOME 49 empty-app_id bug.
- Failures degrade (WARN + False), never raise: the DE-shortcut
  fallback stays available (the daemon logs it).
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Callable

SHORTCUT_ID = "dictation"

# Upstream/X11 parity: the tap-vs-hold disambiguation window for
# mode="both" (hotkey.py HotkeyListener.BOTH_TAP_S).
BOTH_TAP_S = 0.25

BIND_TIMEOUT_S = 30.0        # the DE's bind dialog is user-paced
CONNECT_TIMEOUT_S = 10.0


class PortalHotkeyError(RuntimeError):
    pass


class PortalHotkeyMachine:
    """Pure press/release state machine - Activated/Deactivated edges
    in, daemon callbacks out (isolated from D-Bus for tests).

    mode "toggle": Activated fires on_toggle (a tap would double-toggle
    if the release fired too). "hold": Activated -> on_press,
    Deactivated -> on_release. "both": a release inside BOTH_TAP_S is a
    toggle tap; otherwise press at the deadline, release on Deactivated
    (the caller drives the deadline via both_deadline_expired()).
    """

    BOTH_TAP_S = BOTH_TAP_S

    def __init__(self, mode: str, on_press: Callable[[], None],
                 on_release: Callable[[], None],
                 on_toggle: Callable[[], None],
                 log: Callable[[str], None] = print):
        self.mode = "toggle" if mode not in ("hold", "both") else mode
        self.on_press = on_press
        self.on_release = on_release
        self.on_toggle = on_toggle
        self.log = log
        self._pressed = False
        self._press_ts = 0.0
        self._both_fired = False  # the tap window already decided

    @property
    def pressed(self) -> bool:
        return self._pressed

    def activate(self) -> None:
        """An Activated signal arrived for our shortcut."""
        if self._pressed:
            return  # duplicate activation (DE quirk): ignore
        self._pressed = True
        self._press_ts = time.monotonic()
        self._both_fired = False
        if self.mode == "toggle":
            self._safe(self.on_toggle)
        elif self.mode == "hold":
            self._safe(self.on_press)

    def deactivate(self) -> None:
        """A Deactivated signal arrived for our shortcut."""
        if not self._pressed:
            return  # release without press: ignore (startup strays)
        self._pressed = False
        if self.mode == "hold":
            self._safe(self.on_release)
        elif self.mode == "both":
            if not self._both_fired:
                self._safe(self.on_toggle)  # quick tap
            else:
                self._safe(self.on_release)  # end of a held talk

    def both_deadline_expired(self) -> None:
        """mode="both": still pressed at the deadline (BOTH_TAP_S after
        the activation edge) -> push-to-talk starts; no-op otherwise."""
        if self.mode != "both" or not self._pressed or self._both_fired:
            return
        if time.monotonic() - self._press_ts < self.BOTH_TAP_S:
            return
        self._both_fired = True
        self._safe(self.on_press)

    def _safe(self, cb) -> None:
        try:
            cb()
        except Exception as e:  # noqa: BLE001 - degrade like hotkey.py
            self.log(f"WARN portal hotkey callback failed: {e}")


class _Callbacks:
    """SessionCallback adapter: portal events -> machine edges."""

    def __init__(self, listener: "PortalHotkeyListener"):
        self._listener = listener

    def on_activated(self, event) -> None:
        self._listener._edge(True, getattr(event, "shortcut_id", ""),
                             getattr(event, "timestamp", 0))

    def on_deactivated(self, event) -> None:
        self._listener._edge(False, getattr(event, "shortcut_id", ""),
                             getattr(event, "timestamp", 0))

    def on_shortcuts_changed(self, session, shortcuts) -> None:
        self._listener._log_bound(shortcuts, changed=True)

    def on_error(self, error: Exception) -> None:
        self._listener.log(f"WARN portal hotkey error: {error}")


class PortalHotkeyListener:
    """Threaded GlobalShortcuts portal listener (EvdevPTT contract:
    start() -> bool, stop(), summary; never fatal).

    start() spawns one thread owning an asyncio loop: connect the
    portal, create a FRESH session (BindShortcuts is once-per-session,
    so every start - including rebinds after settings changes - binds
    again), subscribe to Activated/Deactivated, then idle until stop.
    """

    def __init__(self, *, mode: str = "toggle",
                 on_press: Callable[[], None] | None = None,
                 on_release: Callable[[], None] | None = None,
                 on_toggle: Callable[[], None] | None = None,
                 app_id: str = "",
                 preferred_trigger: str = "",
                 log: Callable[[str], None] = print,
                 session_factory=None):
        self.app_id = app_id
        self.preferred_trigger = (preferred_trigger or "").strip()
        self.log = log
        self.machine = PortalHotkeyMachine(
            mode,
            on_press=on_press or (lambda: None),
            on_release=on_release or (lambda: None),
            on_toggle=on_toggle or (lambda: None),
            log=log)
        # injectable for tests: () -> portal-session object with
        # connect()/bind()/close() + a callback attribute (fakes)
        self._session_factory = session_factory
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._session = None
        self.summary: list[str] = []
        self.start_error: str | None = None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> bool:
        """Best-effort start (WARN + False, never raises): missing
        package, no portal backend (sway/COSMIC), refused bind - the
        DE-shortcut fallback keeps the app useful."""
        if self._thread is not None:
            return True
        try:
            import global_shortcut_portal  # noqa: F401 - presence probe
        except ImportError:
            self.log("WARN portal hotkey: global-shortcut-portal not "
                     "installed - falling back to a DE shortcut "
                     "(bind `sayit-ermano --toggle`)")
            return False
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._thread_main, args=(loop,),
            name="fluidvoice-portal-hotkey", daemon=True)
        self._thread.start()
        ready = self._ready.wait(timeout=CONNECT_TIMEOUT_S
                                 + BIND_TIMEOUT_S)
        if not ready or self._session is None:
            error = self.start_error or "portal hotkey did not come up"
            self.log(f"WARN portal hotkey unavailable: {error} - "
                     "fall back to a DE shortcut "
                     "(bind `sayit-ermano --toggle`)")
            self.stop()
            return False
        return True

    _ready: threading.Event  # set once bind resolves (ok or not)

    def stop(self, timeout_s: float = 5.0) -> None:
        """Deterministic teardown; idempotent; never raises.

        The graceful path just sets _stop: the loop thread's idle poll
        notices within ~20 ms, closes the portal session INSIDE the
        loop and exits. The sledgehammer (loop.stop + manual coroutine
        close) is reserved for a thread stuck in a user-paced bind
        dialog."""
        self._stop.set()
        loop, self._loop = self._loop, None
        session, self._session = self._session, None
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout_s)
        if loop is not None and session is not None \
                and thread is not None and thread.is_alive():
            # still starting up (bind dialog): unwedge and close by hand
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._close_session(session), loop)
                fut.result(timeout=1.0)
            except Exception:  # noqa: S110 — teardown/cleanup must not raise
                close = getattr(session, "close", None)
                if close is not None:
                    try:
                        result = close()
                        if asyncio.iscoroutine(result):
                            result.close()
                    except Exception:  # noqa: S110 — teardown/cleanup must not raise
                        pass
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=1.0)
        if loop is not None and thread is not None and not thread.is_alive() \
                and not loop.is_closed():
            # normal path: the loop thread closed _run cleanly but may
            # not have closed the loop itself
            try:
                loop.close()
            except RuntimeError:  # noqa: S110 — teardown/cleanup must not raise
                pass

    async def _close_session(self, session) -> None:
        try:
            await asyncio.wait_for(session.close(), timeout=3.0)
        except Exception:  # noqa: S110 — teardown/cleanup must not raise
            pass

    # -- thread body ----------------------------------------------------------

    def _thread_main(self, loop: asyncio.AbstractEventLoop) -> None:
        # the loop rides the argument: stop() may null self._loop before
        # the thread body runs (failed-start race)
        try:
            loop.run_until_complete(self._run())
        except Exception as e:  # noqa: BLE001 - degrade to fallback
            self.start_error = str(e)
            self.log(f"WARN portal hotkey thread failed: {e}")
        finally:
            self._ready.set()
            try:
                loop.close()
            except Exception:  # noqa: S110 — teardown/cleanup must not raise
                pass

    async def _open_session(self):
        """Create + connect one fresh portal session (the
        global-shortcut-portal package does the D-Bus dance; tests
        inject a factory returning fakes - the callback adapter is
        attached so fake sessions deliver events like real ones)."""
        if self._session_factory is not None:
            session = self._session_factory()
            if asyncio.iscoroutine(session):
                session = await session
            if getattr(session, "callback", None) is None:
                session.callback = _Callbacks(self)
            await asyncio.wait_for(session.connect(), CONNECT_TIMEOUT_S)
            return session
        from global_shortcut_portal import (
            GlobalShortcutsSession,
            Portal,
        )
        portal = Portal()
        await asyncio.wait_for(portal.connect(), CONNECT_TIMEOUT_S)
        self._portal = portal  # torn down with the session (see _run)
        try:
            session = GlobalShortcutsSession(
                portal, app_id=self.app_id or None, callback=_Callbacks(self))
            await asyncio.wait_for(session.connect(), CONNECT_TIMEOUT_S)
        except Exception:
            if not self.app_id:
                raise
            # older portals have no host-portal Registry to Register()
            # with: bind without an explicit app id (the portal then
            # attributes the shortcut to our D-Bus sender) instead of
            # failing the whole hotkey
            self.log("portal Registry unavailable - binding without "
                     "an app id")
            session = GlobalShortcutsSession(
                portal, app_id=None, callback=_Callbacks(self))
            await asyncio.wait_for(session.connect(), CONNECT_TIMEOUT_S)
        return session

    async def _bind(self, session) -> list:
        shortcuts = self._shortcut_spec()
        return await asyncio.wait_for(session.bind(shortcuts),
                                      BIND_TIMEOUT_S)

    def _shortcut_spec(self) -> list:
        """The bind payload for SHORTCUT_ID. The real Shortcut model
        rides the package; a plain namespace stands in when only the
        injected factory is real (tests) so the fake session sees the
        same shape."""
        preferred = self.preferred_trigger or None
        try:
            from global_shortcut_portal import Shortcut
            return [Shortcut(id=SHORTCUT_ID,
                             description="Dictate (hold to talk / tap to "
                                         "toggle)",
                             preferred_trigger=preferred)]
        except ImportError:
            import types
            return [types.SimpleNamespace(
                id=SHORTCUT_ID,
                description="Dictate (hold to talk / tap to toggle)",
                preferred_trigger=preferred)]

    def _log_bound(self, bound, *, changed: bool = False) -> None:
        prefix = "portal shortcuts changed:" if changed else "portal hotkey:"
        for item in bound or []:
            sid = getattr(item, "id", None)
            if sid != SHORTCUT_ID:
                continue
            trigger = getattr(item, "trigger_description", "") or "(unset)"
            self.summary = [
                f"{prefix} bound {sid} to {trigger!r} "
                f"(app {self.app_id or 'host'})"]
            self.log(self.summary[0]
                     + (" - set the key in your desktop's shortcut "
                        "settings" if "unset" in trigger else ""))

    async def _run(self) -> None:
        """Connect, bind, then keep the loop alive for signals until
        stop() (the 'both' tap-window timer rides a periodic tick)."""
        session = await self._open_session()
        bound = await self._bind(session)
        self._session = session
        self._log_bound(bound)
        self._ready.set()
        try:
            while not self._stop.is_set():
                self.machine.both_deadline_expired()
                await asyncio.sleep(0.02)
        finally:
            try:
                await asyncio.wait_for(session.close(), timeout=3.0)
            except Exception:  # noqa: S110 — teardown/cleanup must not raise
                pass
            portal, self._portal = self._portal, None
            if portal is not None:
                try:
                    await asyncio.wait_for(portal.close(), timeout=3.0)
                except Exception:  # noqa: S110 — teardown/cleanup must not raise
                    pass  # the bus dies with the connection anyway
        self._session = None
        self._portal = None  # the real path's Portal (bus connection)

    # -- event seam (also the test seam) ---------------------------------

    def _edge(self, pressed: bool, shortcut_id: str,
              timestamp_ms: int) -> None:
        if shortcut_id and shortcut_id != SHORTCUT_ID:
            return
        if pressed:
            self.machine.activate()
        else:
            self.machine.deactivate()
