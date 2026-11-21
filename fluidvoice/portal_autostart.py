"""Login autostart inside the Flatpak sandbox via the Background portal
(ADR-0006, issue #11).

The deb ships ``/etc/xdg/autostart/sayit-ermano.desktop``
(packaging/autostart.desktop is its reference content). Inside a
sandbox nothing on the host reads ``/app/etc/xdg``, so the sanctioned
replacement is ``org.freedesktop.portal.Background.RequestBackground``:
the portal validates that the commandline belongs to this app, rewrites
it to ``flatpak run --command=... <appid>`` itself, and writes
``~/.config/autostart/<desktop-id>`` on the HOST (GNOME shows one
consent dialog the first time; the portal persists the grant after).

Ask-once state lives in portal-state.json beside the EIS restore token:
once the portal answered — enabled, denied, or no backend — the app
never re-asks automatically (delete the key or run
``flatpak permission-reset`` to re-trigger). Everything here is
best-effort: a failed ask never blocks the daemon owner lifecycle.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
BACKGROUND_IFACE = "org.freedesktop.portal.Background"
REQUEST_IFACE = "org.freedesktop.portal.Request"

#: what the autostart entry runs: the windowless primary instance that
#: owns the daemon (see gtkui/daemon_owner.py)
AUTOSTART_COMMANDLINE = ["sayit-ermano", "app", "--background"]
AUTOSTART_REASON = "Run the dictation daemon at login"

#: the consent dialog can wait for a human; callers run this off the
#: main thread (the app does — see gtkui/application.py)
CONSENT_TIMEOUT_S = 300.0
CALL_TIMEOUT_S = 10.0

#: ask-once outcomes persisted in portal-state.json
STATE_KEY = "background_autostart"
ENABLED, DENIED, UNAVAILABLE = "enabled", "denied", "unavailable"


class BackgroundPortal:
    """Async D-Bus client for org.freedesktop.portal.Background
    (dbus-next, the same plumbing pattern as portal_eis)."""

    def __init__(self, timeout: float = CALL_TIMEOUT_S) -> None:
        self._bus = None
        self._timeout = timeout

    async def connect(self) -> None:
        from dbus_next.aio import MessageBus
        self._bus = await MessageBus().connect()

    async def close(self) -> None:
        if self._bus is not None:
            try:
                self._bus.disconnect()
            except Exception:  # noqa: S110 — teardown must not raise
                pass
            self._bus = None

    async def request_autostart(self, enable: bool, reason: str,
                                commandline: list[str]) -> bool:
        """RequestBackground(): True when autostart is enabled after
        the call (the user may refuse the consent dialog)."""
        from dbus_next import Message, Variant
        options = {
            "handle_token": Variant(
                "s", f"bg{os.getpid()}{int(time.time()) % 100000}"),
            "reason": Variant("s", reason),
            "autostart": Variant("b", enable),
        }
        if enable:
            options["commandline"] = Variant("as", list(commandline))
        reply = await self._call(Message(
            destination=BUS_NAME, path=OBJECT_PATH,
            interface=BACKGROUND_IFACE, member="RequestBackground",
            signature="sa{sv}", body=["", options]))
        request_path = reply.body[0]
        results = await self._await_response(request_path)
        return bool(results.get("autostart", False))

    # -- plumbing (mirrors portal_eis.RemoteDesktopPortal) ---------------

    async def _call(self, msg, timeout: float | None = None):
        from dbus_next import MessageType
        if self._bus is None:
            raise RuntimeError("not connected")
        try:
            reply = await asyncio.wait_for(
                self._bus.call(msg), timeout=timeout or self._timeout)
        except asyncio.TimeoutError as e:
            raise RuntimeError(
                f"portal call {msg.member} timed out") from e
        except Exception as e:
            raise RuntimeError(
                f"portal call {msg.member} failed: {e}") from e
        if reply.message_type == MessageType.ERROR.value:
            raise RuntimeError(
                f"portal call {msg.member}: "
                f"{reply.body[0] if reply.body else 'error'}")
        return reply

    async def _await_response(self, request_path: str,
                              timeout: float = CONSENT_TIMEOUT_S) -> dict:
        from dbus_next import Message
        loop = asyncio.get_running_loop()
        done = loop.create_future()

        def handler(msg: Message) -> None:
            if (msg.path == request_path
                    and msg.interface == REQUEST_IFACE
                    and msg.member == "Response"):
                if not done.done():
                    done.set_result((msg.body[0],
                                     msg.body[1] if len(msg.body) > 1 else {}))

        self._bus.add_message_handler(handler)
        try:
            code, results = await asyncio.wait_for(done, timeout=timeout)
        except asyncio.TimeoutError as e:
            raise RuntimeError(
                "timed out waiting for the Background portal") from e
        finally:
            self._bus.remove_message_handler(handler)
        if code != 0:  # 1 cancelled / 2 refused — a denial, not an error
            raise PermissionError(f"background request refused (code {code})")
        return {k: getattr(v, "value", v) for k, v in results.items()}


def set_autostart(enable: bool, reason: str = AUTOSTART_REASON,
                  commandline: list[str] | None = None) -> bool:
    """One-shot synchronous RequestBackground round trip (private loop
    thread; blocking up to the consent timeout)."""
    portal = BackgroundPortal()

    async def _round() -> bool:
        await portal.connect()
        try:
            return await portal.request_autostart(
                enable, reason, commandline or AUTOSTART_COMMANDLINE)
        finally:
            await portal.close()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_round())
    finally:
        loop.close()


def ask_autostart_once(state_file=None, log=None) -> str:
    """First-run autostart ask (flatpak only). Returns and persists the
    outcome; never raises — a failed ask is reported, not fatal."""
    from . import paths
    from .gtkui.daemon_owner import in_flatpak
    state_file = state_file or paths.portal_state_file()
    log = log or (lambda m: print(f"[sayit-ermano] {m}", flush=True))
    if not in_flatpak():
        return "skipped"
    prior = _load_state(state_file).get(STATE_KEY)
    if prior:
        return str(prior)  # already asked once
    try:
        enabled = set_autostart(True)
    except PermissionError as e:
        outcome = DENIED
        log(f"autostart declined: {e}")
    except Exception as e:  # noqa: BLE001 — no portal backend / bus error
        outcome = UNAVAILABLE
        log(f"autostart unavailable ({e}); see docs/packaging/"
            "flatpak-permissions.md for the manual fallback")
    else:
        outcome = ENABLED if enabled else DENIED
        log("login autostart enabled via the Background portal"
            if enabled else "portal reported autostart not enabled")
    _update_state(state_file, **{STATE_KEY: outcome})
    return outcome


# -- portal-state.json (shared with the EIS restore token) ----------------

def _load_state(state_file) -> dict:
    try:
        return json.loads(state_file.read_text())
    except Exception:  # noqa: BLE001 — state is best-effort
        return {}


def _update_state(state_file, **kv) -> None:
    """Read-modify-write so sibling keys (the EIS restore token) are
    never clobbered."""
    try:
        data = _load_state(state_file)
        data.update(kv)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(data))
    except Exception as e:  # noqa: BLE001 — state is best-effort
        print(f"[sayit-ermano] WARN could not save portal state: {e}",
              flush=True)
