"""Global hotkey on Wayland via the GlobalShortcuts portal v2.

The sandbox-friendly replacement for XGrabKey (ADR-0006): the
org.freedesktop.portal.GlobalShortcuts interface lets the compositor
own the grab and hand us ``Activated``/``Deactivated`` signals — real
press and release edges, so hold-PTT works natively (the X11 listener's
whole passthrough machinery exists only because XGrabKey activations
swallow the keyboard; here the compositor does the interception).

Built on the pure-Python ``global-shortcut-portal`` package (PyPI;
dbus-next underneath) instead of hand-rolled D-Bus. The library is
asyncio-only, so the listener owns one thread with a private event
loop; callbacks fire on that thread, mirroring how HotkeyListener
fires on its poll thread.

Behaviour decisions from the spike evidence (issue #10):

- BindShortcuts is called once per portal session, i.e. on every
  ``start()`` — the KDE restart bug (bindings silently dropped after a
  compositor/portal restart) is cured by the rebind, and creating a
  fresh session each start also re-triggers the bind dialog only when
  the portal lost the consent.
- ``preferred_trigger`` (from hotkey.key + modifiers) is SENT but never
  required: the portal is free to bind a different combo (user choice
  in the DE shortcut editor wins). The actually-bound trigger from the
  BindShortcuts response is reported in the summary.
- Modes map onto the two edges: "toggle" fires on Activated only;
  "hold" fires on both edges (native push-to-talk); "both" uses the
  same 250 ms tap window as the X11 listener — release inside the
  window is a toggle tap, a still-held key past it starts PTT.
- No portal backend (sway, COSMIC) or no library installed: start()
  raises PortalHotkeyError and the daemon falls back to the DE-shortcut
  assist (`sayit-ermano toggle` over the control socket).
- Under Flatpak the app-id is always set, which dodges the GNOME 49
  empty-app_id bug; outside Flatpak the session is created WITHOUT the
  library's Registry.Register call (that interface is not part of the
  portal spec and errors on stock portals).
"""
from __future__ import annotations

import threading
import time

DEFAULT_SHORTCUT_ID = "dictate"
BOTH_TAP_S = 0.25  # same disambiguation window as hotkey.HotkeyListener

# X11 keysym-name -> portal modifier token (the portal's parse accepts
# these case-insensitively; keys come from hotkey.key / hotkey.modifiers)
_MODIFIER_TOKENS = {
    "control_l": "CTRL", "control_r": "CTRL", "control": "CTRL",
    "left_control": "CTRL", "right_control": "CTRL", "ctrl": "CTRL",
    "shift_l": "SHIFT", "shift_r": "SHIFT", "shift": "SHIFT",
    "left_shift": "SHIFT", "right_shift": "SHIFT",
    "alt_l": "ALT", "alt_r": "ALT", "alt": "ALT", "meta_l": "ALT",
    "meta_r": "ALT", "left_alt": "ALT", "right_alt": "ALT",
    "left_meta": "ALT", "right_meta": "ALT",
    "super_l": "LOGO", "super_r": "LOGO", "super": "LOGO",
    "left_super": "LOGO", "right_super": "LOGO",
    "hyper_l": "HYPER", "hyper_r": "HYPER", "hyper": "HYPER",
}


class PortalHotkeyError(RuntimeError):
    pass


def preferred_trigger_for(key: str, modifiers: list[str]) -> str | None:
    """Config hotkey -> a portal preferred_trigger string like
    "CTRL+SHIFT+D", or None when nothing sensible can be built.

    Sent as a HINT only — the portal may bind something else and that
    is fine (see module docstring)."""
    tokens = []
    for mod in modifiers or []:
        token = _MODIFIER_TOKENS.get(str(mod).strip().lower())
        if token and token not in tokens:
            tokens.append(token)
    name = (key or "").strip()
    if not name:
        return None
    lowered = name.lower().replace(" ", "_")
    if lowered in _MODIFIER_TOKENS:
        # the hotkey is modifier-only: hint at that bare modifier
        token = _MODIFIER_TOKENS[lowered]
        if token not in tokens:
            tokens.append(token)
        return "+".join(tokens)
    # "Right_Control" style names collapse to their letter/key part
    part = name.split("_")[-1] if "_" in name else name
    tokens.append(part.upper() if len(part) == 1 else part)
    mods, key = sorted(tokens[:-1]), tokens[-1]
    return "+".join(mods + [key])


class ShortcutRouter:
    """Pure Activated/Deactivated -> callback mapping (unit-tested with
    fake events; mirrors the X11 listener's mode semantics)."""

    def __init__(self, mode: str, on_toggle, on_cancel=None,
                 tap_window_s: float = BOTH_TAP_S, clock=time.monotonic):
        self.mode = mode if mode in ("toggle", "hold", "both") else "toggle"
        self.on_toggle = on_toggle
        self.on_cancel = on_cancel
        self.tap_window_s = tap_window_s
        self._clock = clock
        self._pressed_at: float | None = None
        self._holding = False
        self._tap_deadline: float | None = None

    def _fire(self, cb) -> None:
        if cb is None:
            return
        try:
            cb()
        except Exception:  # noqa: S110 — best-effort callback contract
            pass  # matches HotkeyListener._safe

    def activated(self) -> None:
        now = self._clock()
        if self.mode == "toggle":
            self._fire(self.on_toggle)
            return
        if self.mode == "hold":
            self._fire(self.on_toggle)  # start talking
            self._holding = True
            return
        # both: wait out the tap window before committing to a hold
        self._pressed_at = now
        self._tap_deadline = now + self.tap_window_s
        self._holding = False

    def deactivated(self) -> None:
        if self.mode == "toggle":
            return  # release is meaningless for a toggle
        if self.mode == "hold":
            if self._holding:
                self._holding = False
                self._fire(self.on_toggle)  # stop and transcribe
            return
        # both
        if self._tap_deadline is not None:
            self._tap_deadline = None  # released inside the window: a tap
            self._fire(self.on_toggle)
            return
        if self._holding:
            self._holding = False
            self._fire(self.on_toggle)  # stop and transcribe

    def tick(self) -> None:
        """Advance the tap-window decision (called on the loop thread)."""
        if (self.mode == "both" and self._tap_deadline is not None
                and self._clock() >= self._tap_deadline):
            self._tap_deadline = None
            self._holding = True
            self._fire(self.on_toggle)  # held past the window: PTT starts


class PortalHotkeyListener:
    """GlobalShortcuts-portal hotkey listener with the daemon-facing
    surface of HotkeyListener: start/stop, summary, hotkey_grabbed,
    set_recording (cancel is not grabbable through the portal — the
    CLI/tray cancel paths stay the answer while recording)."""

    def __init__(self, key: str = "", modifiers: list[str] | None = None,
                 mode: str = "toggle", *, on_toggle=None, on_cancel=None,
                 shortcut_id: str = DEFAULT_SHORTCUT_ID,
                 description: str = "SayItErmano dictation",
                 log=None, on_grab_change=None):
        self.key = key
        self.mode = mode
        self.on_toggle = on_toggle
        self.on_cancel = on_cancel
        self.shortcut_id = shortcut_id
        self.description = description
        self.log = log or (lambda m: print(f"[sayit-ermano] {m}", flush=True))
        self._on_grab_change = on_grab_change or (lambda healthy: None)
        self._trigger = preferred_trigger_for(key, modifiers or [])
        self._router = ShortcutRouter(mode, on_toggle, on_cancel)
        self._thread: threading.Thread | None = None
        self._loop = None
        self._stop_flag = threading.Event()
        self._session = None
        self._portal = None
        self._bound_trigger = ""
        self._healthy = False
        self._summary: list[str] = []

    # -- daemon-facing surface ----------------------------------------------

    @property
    def summary(self) -> list[str]:
        return list(self._summary)

    @property
    def hotkey_grabbed(self) -> bool:
        """True once BindShortcuts succeeded (the portal owns the grab
        from then on; there is no refusal state to poll like X11)."""
        return self._healthy

    def set_recording(self, active: bool) -> None:
        """No-op: Escape cannot be grabbed through the portal; cancel
        while recording stays on the tray/`sayit-ermano cancel` paths."""

    def start(self) -> None:
        if self._thread:
            return
        try:
            import asyncio
        except ImportError as e:  # pragma: no cover - always present
            raise PortalHotkeyError(f"asyncio unavailable: {e}") from e
        self._stop_flag.clear()
        ready = threading.Event()
        error: list[str] = []
        self._thread = threading.Thread(
            target=self._run, name="fluidvoice-portal-hotkey",
            args=(asyncio.new_event_loop(), ready, error), daemon=True)
        self._thread.start()
        # bind failures surface synchronously so callers can fall back
        # to the DE-shortcut assist in one code path
        if not ready.wait(timeout=30.0):
            self.stop()
            raise PortalHotkeyError(
                "GlobalShortcuts portal did not answer in 30 s")
        if error:
            self.stop()
            raise PortalHotkeyError(error[0])

    def stop(self) -> None:
        self._stop_flag.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        self._thread = None

    # -- internals ------------------------------------------------------------

    def _run(self, loop, ready: threading.Event, error: list[str]) -> None:
        import asyncio

        async def _main() -> None:
            try:
                await self._connect_and_bind()
            except Exception as e:  # noqa: BLE001 - reported via ready
                error.append(str(e))
            finally:
                ready.set()
                if not error and not self._stop_flag.is_set():
                    while not self._stop_flag.is_set():
                        self._router.tick()
                        await asyncio.sleep(0.05)
                await self._aclose()

        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(_main())
        finally:
            try:
                loop.close()
            except Exception:  # noqa: S110 — teardown must not raise
                pass
            self._loop = None

    async def _connect_and_bind(self) -> None:
        self._session = self._open_session()  # friendly ImportError first
        await self._session.connect()
        bound = await self._session.bind([self._build_shortcut()])
        if not bound:
            raise PortalHotkeyError(
                "portal bound no shortcuts (no GlobalShortcuts backend? "
                "sway and COSMIC have none)")
        self._bound_trigger = bound[0].trigger_description or ""
        self._set_healthy(True)
        self._summary = [
            f"portal hotkey '{self.shortcut_id}' bound"
            + (f": {self._bound_trigger}" if self._bound_trigger
               else " (no trigger description)"),
            f"mode {self._router.mode}"
            + (f", preferred {self._trigger}" if self._trigger else ""),
        ]
        for line in self._summary:
            self.log(line)

    def _build_shortcut(self):
        from global_shortcut_portal import Shortcut
        return Shortcut(id=self.shortcut_id, description=self.description,
                        preferred_trigger=self._trigger)

    def _open_session(self):
        """(Re)build the portal session object; the override seam for
        tests (fake portal objects, no D-Bus daemon needed)."""
        try:
            from global_shortcut_portal import GlobalShortcutsSession, Portal
        except ImportError as e:
            raise PortalHotkeyError(
                "global-shortcut-portal not installed "
                "(pip install global-shortcut-portal)") from e
        self._portal = Portal()
        # app_id=None: never call the library's Registry.Register (a
        # non-portal interface); under Flatpak the app-id is known to
        # the portal already, elsewhere the desktop file identifies us
        return GlobalShortcutsSession(self._portal, app_id=None,
                                      callback=self._callbacks())

    def _callbacks(self):
        listener = self

        class _CB:
            def on_activated(self, event) -> None:
                if event.shortcut_id == listener.shortcut_id:
                    listener._router.activated()

            def on_deactivated(self, event) -> None:
                if event.shortcut_id == listener.shortcut_id:
                    listener._router.deactivated()

            def on_shortcuts_changed(self, session, shortcuts) -> None:
                for sc in shortcuts or []:
                    if sc.id == listener.shortcut_id:
                        listener._bound_trigger = sc.trigger_description or ""
                listener.log("portal shortcuts changed: "
                             + listener._bound_trigger)

            def on_error(self, error) -> None:
                listener.log(f"WARN portal hotkey error: {error}")

        return _CB()

    def _set_healthy(self, healthy: bool) -> None:
        if healthy != self._healthy:
            self._healthy = healthy
            try:
                self._on_grab_change(healthy)
            except Exception:  # noqa: S110 — best-effort surface
                pass

    async def _aclose(self) -> None:
        try:
            if self._session is not None:
                await self._session.close()
        except Exception:  # noqa: S110 — teardown must not raise
            pass
        try:
            if self._portal is not None:
                await self._portal.close()
        except Exception:  # noqa: S110 — teardown must not raise
            pass
