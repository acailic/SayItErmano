"""Login autostart via the Background portal (Flatpak, ADR-0006/#11).

Inside the sandbox the deb's autostart mechanisms are unavailable:
there is no systemd user instance for the unit, and /etc/xdg/autostart
(packaging/autostart.desktop, the deb reference) is host-only. The
sanctioned replacement is org.freedesktop.portal.Background:
``RequestBackground(autostart=true, commandline=...)`` has the
desktop's portal backend write ``~/.config/autostart/<appid>.desktop``
on the HOST — an XDG autostart key whose Exec the backend composes to
``flatpak run --command=sayit-ermano io.github.acailic.SayItErmano
daemon``, so the DAEMON (not the settings window) starts at login.
The GTK app process owns the daemon when the user launches it instead
(fluidvoice.daemon_owner); the daemon's flock singleton resolves the
race when both fire.

Backend support: xdg-desktop-portal-gnome (GNOME 43+) and -kde; older
or wlroots-only setups have no Background backend — ``set_autostart``
reports that as a failure and callers show the manual command (see
Settings → General and docs/packaging/flatpak-permissions.md).
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
BACKGROUND_IFACE = "org.freedesktop.portal.Background"
REQUEST_IFACE = "org.freedesktop.portal.Request"

# The daemon, not the app: commandline[0] becomes the flatpak
# --command= override the backends compose into the autostart Exec.
AUTOSTART_COMMANDLINE = ["sayit-ermano", "daemon"]
DEFAULT_REASON = "Start the SayItErmano dictation daemon at login"

# The portal only answers after a (possibly first-time) permission
# dialog; callers run this off the UI thread and get bounded here.
CALL_TIMEOUT_S = 10.0
CONSENT_TIMEOUT_S = 120.0


class BackgroundPortalError(RuntimeError):
    pass


class BackgroundPortalUnavailable(BackgroundPortalError):
    """No Background portal / backend refused or no consent."""


class BackgroundPortal:
    """Raw org.freedesktop.portal.Background client (async; dbus-next is
    a vendored dependency of the Flatpak and a host extra)."""

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

    async def interface_version(self) -> int:
        """0 when the Background interface does not exist at all."""
        if self._bus is None:
            raise BackgroundPortalError("not connected")
        from dbus_next import Message
        reply = await self._call(Message(
            destination=BUS_NAME, path=OBJECT_PATH,
            interface="org.freedesktop.DBus.Properties", member="Get",
            signature="ss", body=[BACKGROUND_IFACE, "version"]))
        return int(reply.body[0].value) if reply.body else 0

    async def request_background(self, *, reason: str, autostart: bool,
                                 commandline: list[str] | None = None,
                                 parent_window: str = "") -> dict:
        """RequestBackground: returns the Response results vardict
        ({background: bool, autostart: bool}). Raises
        BackgroundPortalUnavailable when the user/portal declines."""
        from dbus_next import Message, Variant
        options = {
            "handle_token": Variant(
                "s", f"bg{os.getpid()}{int(time.time()) % 100000}"),
            "reason": Variant("s", reason),
            "autostart": Variant("b", autostart),
        }
        if commandline:
            options["commandline"] = Variant("as", commandline)
        reply = await self._call(Message(
            destination=BUS_NAME, path=OBJECT_PATH,
            interface=BACKGROUND_IFACE, member="RequestBackground",
            signature="sa{sv}", body=[parent_window, options]))
        results = await self._await_response(reply.body[0])
        if autostart and not results.get("autostart"):
            raise BackgroundPortalUnavailable(
                "portal did not grant the autostart request")
        return results

    # -- plumbing (mirrors portal_eis.RemoteDesktopPortal) -------------------

    async def _call(self, msg, timeout: float | None = None):
        from dbus_next import MessageType
        if self._bus is None:
            raise BackgroundPortalError("not connected")
        try:
            reply = await asyncio.wait_for(
                self._bus.call(msg), timeout=timeout or self._timeout)
        except asyncio.TimeoutError as e:
            raise BackgroundPortalError(
                f"portal call {msg.member} timed out") from e
        except BackgroundPortalError:
            raise
        except Exception as e:
            raise BackgroundPortalUnavailable(
                f"portal call {msg.member} failed: {e}") from e
        if reply.message_type == MessageType.ERROR.value:
            raise BackgroundPortalUnavailable(
                f"portal call {msg.member}: "
                f"{reply.body[0] if reply.body else 'error'}")
        return reply

    async def _await_response(self, request_path: str) -> dict:
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
            code, results = await asyncio.wait_for(
                done, timeout=CONSENT_TIMEOUT_S)
        except asyncio.TimeoutError as e:
            raise BackgroundPortalError(
                "timed out waiting for the portal response") from e
        finally:
            self._bus.remove_message_handler(handler)
        if code != 0:
            # 1 = cancelled, 2 = ended by the user (consent refused)
            raise BackgroundPortalUnavailable(
                f"portal request refused (code {code})")
        return {k: getattr(v, "value", v) for k, v in results.items()}


def load_autostart_state(state_file=None) -> bool:
    """The persisted portal intent (the portal has no query API — the
    Settings switch records what we last requested). Best-effort:
    missing/corrupt file reads as False."""
    from .paths import autostart_state_file
    path = state_file or autostart_state_file()
    try:
        return bool(json.loads(path.read_text()).get("enabled"))
    except Exception:  # noqa: S110 — state is best-effort
        return False


def save_autostart_state(enabled: bool, state_file=None) -> None:
    from .paths import autostart_state_file
    path = state_file or autostart_state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"enabled": enabled}))
    except Exception:  # noqa: S110 — state is best-effort
        pass


def set_autostart(enabled: bool, *, reason: str = DEFAULT_REASON,
                  portal_factory=None) -> tuple[bool, str]:
    """Toggle login autostart through the Background portal.

    Sync facade over :class:`BackgroundPortal` running on a private
    loop (this is called from a worker thread by Settings; the
    permission dialog can block for a human). Never raises:
    returns ``(ok, detail)`` with a caller-displayable detail for the
    failure paths (no portal, refused, timeout). ``portal_factory`` is
    the test seam — an object providing async ``connect``/``close``/
    ``request_background``.
    """
    async def _main() -> dict:
        portal = (portal_factory() if portal_factory is not None
                  else BackgroundPortal())
        await portal.connect()
        try:
            return await portal.request_background(
                reason=reason, autostart=enabled,
                commandline=AUTOSTART_COMMANDLINE if enabled else None)
        finally:
            await portal.close()

    outcome: dict[str, object] = {}

    def _run() -> None:
        try:
            outcome["results"] = asyncio.run(_main())
        except Exception as e:  # noqa: BLE001 - reported, never raises
            outcome["error"] = str(e)

    thread = threading.Thread(target=_run, name="fluidvoice-bg-portal",
                              daemon=True)
    thread.start()
    thread.join(timeout=CONSENT_TIMEOUT_S + CALL_TIMEOUT_S)
    if thread.is_alive():
        return False, "Background portal did not answer in time"
    if "error" in outcome:
        return False, str(outcome["error"])
    return True, ("autostart on" if enabled else "autostart off")
