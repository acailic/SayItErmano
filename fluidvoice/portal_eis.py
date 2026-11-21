"""RemoteDesktop portal + EIS: the sanctioned Wayland insertion path.

Two layers (ADR-0006):

- ``RemoteDesktopPortal`` — the async D-Bus client for
  org.freedesktop.portal.RemoteDesktop (dbus-next, the same stack the
  GlobalShortcuts listener rides). Consent flow: CreateSession ->
  SelectDevices(KEYBOARD, persist_mode=2 + restore_token when we have
  one) -> Start (this is the step that shows the consent dialog on
  portals < 1.21; with a valid restore token the session restores
  silently) -> ConnectToEIS, which returns the EIS socket fd. The
  restore token from the Start response is persisted so the dialog
  appears once, ever, not once per daemon start.
- ``EISInserter`` — the sync facade the daemon/insertion layers use.
  It owns one thread with a private asyncio loop, keeps the portal
  session and the EIS connection alive across insertions, and exposes
  ``ensure()`` / ``type_text()`` / ``press_key()`` / ``available`` /
  ``stop()``. ``ensure()`` is the consent boundary: it can block on
  the dialog the FIRST time, so callers warm it from a background
  thread (the daemon does) and insertion only ever asks
  ``available``.

Everything is a runtime probe: nothing here is selected by desktop
name. ``probe_remote_desktop()`` reports the portal interface version
(ConnectToEIS exists from version 2); wlroots sessions without the
interface report version 0 and the ladder moves on to wtype.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time

from .eis import EISConnection, EISError

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
REMOTE_DESKTOP_IFACE = "org.freedesktop.portal.RemoteDesktop"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

DEVICE_TYPE_KEYBOARD = 1
PERSIST_EXPLICIT_REVOCATION = 2

# bounded waits: consent can involve a human the FIRST time; subsequent
# calls are fast. Callers warm this from a background thread.
CONSENT_TIMEOUT_S = 300.0
CALL_TIMEOUT_S = 10.0


class PortalEISError(RuntimeError):
    pass


class PortalEISUnavailable(PortalEISError):
    """No RemoteDesktop portal / no ConnectToEIS / user refused consent."""


class RemoteDesktopPortal:

    def __init__(self, timeout: float = CALL_TIMEOUT_S) -> None:
        self._bus = None
        self._timeout = timeout
        self._session_handle: str | None = None

    @property
    def connected(self) -> bool:
        return self._bus is not None

    async def connect(self) -> None:
        from dbus_next.aio import MessageBus
        self._bus = await MessageBus(negotiate_unix_fd=True).connect()

    async def close(self) -> None:
        if self._session_handle:
            from dbus_next import Message
            try:
                await self._call(Message(
                    destination=BUS_NAME, path=self._session_handle,
                    interface=SESSION_IFACE, member="Close",
                    signature="", body=[]))
            except Exception:  # noqa: S110 — teardown must not raise
                pass
            self._session_handle = None
        if self._bus is not None:
            try:
                self._bus.disconnect()
            except Exception:  # noqa: S110 — teardown must not raise
                pass
            self._bus = None

    async def interface_version(self) -> int:
        """The org.freedesktop.portal.RemoteDesktop `version` property
        (0 when the interface does not exist at all)."""
        if self._bus is None:
            raise PortalEISError("not connected")
        from dbus_next import Message
        reply = await self._call(Message(
            destination=BUS_NAME, path=OBJECT_PATH,
            interface="org.freedesktop.DBus.Properties", member="Get",
            signature="ss", body=[REMOTE_DESKTOP_IFACE, "version"]))
        return int(reply.body[0].value) if reply.body else 0

    async def create_session(self) -> str:
        from dbus_next import Message, Variant
        token = f"sayitermano{os.getpid()}{int(time.time()) % 100000}"
        request_path = await self._request(Message(
            destination=BUS_NAME, path=OBJECT_PATH,
            interface=REMOTE_DESKTOP_IFACE, member="CreateSession",
            signature="a{sv}",
            body=[{"handle_token": Variant("s", token),
                   "session_handle_token": Variant("s", f"{token}s")}]))
        results = await self._await_response(request_path)
        handle = results.get("session_handle")
        if not handle:
            raise PortalEISError("CreateSession returned no session_handle")
        self._session_handle = handle
        return handle

    async def select_devices(self, *, types: int = DEVICE_TYPE_KEYBOARD,
                             persist_mode: int = PERSIST_EXPLICIT_REVOCATION,
                             restore_token: str = "") -> None:
        from dbus_next import Message, Variant
        options = {
            "handle_token": Variant("s", f"sd{int(time.time()) % 100000}"),
            "types": Variant("u", types),
            "persist_mode": Variant("u", persist_mode),
        }
        if restore_token:
            options["restore_token"] = Variant("s", restore_token)
        request_path = await self._request(Message(
            destination=BUS_NAME, path=OBJECT_PATH,
            interface=REMOTE_DESKTOP_IFACE, member="SelectDevices",
            signature="oa{sv}",
            body=[self._session_handle, options]))
        await self._await_response(request_path)

    async def start(self, parent_window: str = "") -> tuple[int, str]:
        """Start(): the consent boundary. Returns (devices mask,
        restore_token). The consent dialog can block here for a human
        the first time — callers warm this from a background thread."""
        from dbus_next import Message, Variant
        request_path = await self._request(Message(
            destination=BUS_NAME, path=OBJECT_PATH,
            interface=REMOTE_DESKTOP_IFACE, member="Start",
            signature="osa{sv}",
            body=[self._session_handle, parent_window,
                  {"handle_token": Variant(
                      "s", f"st{int(time.time()) % 100000}")}]),
            timeout=CONSENT_TIMEOUT_S)
        results = await self._await_response(
            request_path, timeout=CONSENT_TIMEOUT_S)
        devices = int(results.get("devices", 0))
        token = str(results.get("restore_token", "") or "")
        if not devices & DEVICE_TYPE_KEYBOARD:
            raise PortalEISUnavailable(
                "user did not grant keyboard control")
        return devices, token

    async def connect_to_eis(self) -> int:
        """ConnectToEIS() -> the EIS socket fd (once per session)."""
        from dbus_next import Message, Variant
        reply = await self._call(Message(
            destination=BUS_NAME, path=OBJECT_PATH,
            interface=REMOTE_DESKTOP_IFACE, member="ConnectToEIS",
            signature="oa{sv}",
            body=[self._session_handle,
                  {"handle_token": Variant(
                      "s", f"eis{int(time.time()) % 100000}")}]))
        if not reply.body or not reply.unix_fds:
            raise PortalEISError("ConnectToEIS returned no fd")
        return reply.unix_fds[int(reply.body[0])]

    # -- plumbing -------------------------------------------------------------

    async def _request(self, msg, timeout: float | None = None):
        reply = await self._call(msg, timeout=timeout)
        return reply.body[0]

    async def _call(self, msg, timeout: float | None = None):
        from dbus_next import MessageType
        if self._bus is None:
            raise PortalEISError("not connected")
        try:
            reply = await asyncio.wait_for(
                self._bus.call(msg), timeout=timeout or self._timeout)
        except asyncio.TimeoutError as e:
            raise PortalEISError(
                f"portal call {msg.member} timed out") from e
        except Exception as e:
            raise PortalEISError(
                f"portal call {msg.member} failed: {e}") from e
        if reply.message_type == MessageType.ERROR.value:
            raise PortalEISUnavailable(
                f"portal call {msg.member}: {reply.body[0] if reply.body else 'error'}")
        return reply

    async def _await_response(self, request_path: str,
                              timeout: float | None = None) -> dict:
        """Wait for the Request.Response signal on `request_path`."""
        from dbus_next import Message
        loop = asyncio.get_running_loop()
        done = loop.create_future()

        def handler(msg: Message) -> None:
            if (msg.path == request_path
                    and msg.interface == REQUEST_IFACE
                    and msg.member == "Response"):
                code = msg.body[0]
                results = msg.body[1] if len(msg.body) > 1 else {}
                if not done.done():
                    done.set_result((code, results))

        self._bus.add_message_handler(handler)
        try:
            code, results = await asyncio.wait_for(
                done, timeout=timeout or self._timeout)
        except asyncio.TimeoutError as e:
            raise PortalEISError(
                "timed out waiting for the portal response") from e
        finally:
            self._bus.remove_message_handler(handler)
        if code != 0:
            # 1 = cancelled, 2 = ended by the user (consent refused)
            raise PortalEISUnavailable(
                f"portal request refused (code {code})")
        return {k: getattr(v, "value", v) for k, v in results.items()}


async def probe_remote_desktop(timeout: float = CALL_TIMEOUT_S) -> bool:
    """Runtime probe: can ConnectToEIS exist here? (interface >= 2).

    Never raises: False for no portal, old portal, or bus error — a
    probe result, not a diagnosis."""
    portal = RemoteDesktopPortal(timeout=timeout)
    try:
        await portal.connect()
        version = await portal.interface_version()
    except Exception:  # noqa: BLE001 - a probe never raises
        return False
    finally:
        try:
            await portal.close()
        except Exception:  # noqa: S110 — teardown must not raise
            pass
    return version >= 2


class EISInserter:
    """Sync facade: one portal session + one EIS connection, kept warm.

    ``ensure()`` runs the full consent flow (blocking; warm it from a
    background thread — the daemon does). ``type_text``/``press_key``
    are cheap once ``available``. Thread-safe: methods bounce onto the
    private loop thread, so insertion paths can call from anywhere."""

    def __init__(self, *, state_file=None, log=None) -> None:
        from . import paths
        self._state_file = state_file or paths.portal_state_file()
        self.log = log or (lambda m: print(f"[sayit-ermano] {m}", flush=True))
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._eis: EISConnection | None = None
        self._portal: RemoteDesktopPortal | None = None
        self._stop = threading.Event()
        self._error = ""

    @property
    def available(self) -> bool:
        with self._lock:
            return self._eis is not None

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._error

    def ensure(self) -> bool:
        """Full connect (portal consent + EIS handshake). Blocking."""
        with self._lock:
            if self._eis is not None:
                return True
        try:
            loop = self._ensure_loop()
            fd = self._run(loop, self._consent_and_connect())
            eis = EISConnection(_fd_socket(fd), name="SayItErmano",
                                timeout=5.0, log=self.log)
            eis.connect()
            with self._lock:
                if self._stop.is_set():
                    eis.close()
                    return False
                self._eis = eis
                self._error = ""
            self.log("EIS insertion ready (RemoteDesktop portal)")
            return True
        except Exception as e:  # noqa: BLE001 - reported, never fatal
            with self._lock:
                self._error = str(e)
            self.log(f"WARN EIS insertion unavailable: {e}")
            return False

    def type_text(self, text: str, delay_ms: int = 8) -> None:
        """Type via EIS (levels 1/2). Raises EISUnsupportedChar for
        out-of-layout text — callers take the clipboard fallback."""
        eis = self._live()
        eis.type_text(text, delay_ms=delay_ms)

    def press_key(self, spec: str) -> None:
        """One combo (e.g. 'ctrl+v') via EIS."""
        self._live().press_combo(spec)

    def unsupported_chars(self, text: str) -> str:
        return self._live().unsupported_chars(text)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            eis, self._eis = self._eis, None
            portal, self._portal = self._portal, None
            loop = self._loop
            self._loop = None
        if eis is not None:
            try:
                eis.close()
            except Exception:  # noqa: S110 — teardown must not raise
                pass
        if portal is not None and loop is not None:
            try:
                self._run(loop, portal.close())
            except Exception:  # noqa: S110 — teardown must not raise
                pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    # -- internals ------------------------------------------------------------

    def _live(self) -> EISConnection:
        with self._lock:
            eis = self._eis
        if eis is None:
            raise EISError("EIS insertion not connected")
        return eis

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            loop = self._loop
            if loop is not None and self._thread is not None \
                    and self._thread.is_alive():
                return loop
        loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=loop.run_forever, name="fluidvoice-eis-portal",
            daemon=True)
        self._thread.start()
        with self._lock:
            self._loop = loop
        return loop

    @staticmethod
    def _run(loop, coro, timeout: float = CONSENT_TIMEOUT_S):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    async def _consent_and_connect(self) -> int:
        token = self._load_restore_token()
        portal = RemoteDesktopPortal()
        await portal.connect()
        try:
            await portal.create_session()
            await portal.select_devices(restore_token=token)
            _devices, new_token = await portal.start()
            if new_token and new_token != token:
                self._save_restore_token(new_token)
            fd = await portal.connect_to_eis()
        except Exception:
            await portal.close()
            raise
        with self._lock:
            self._portal = portal  # kept open: the session owns the EIS
        return fd

    def _load_restore_token(self) -> str:
        try:
            data = json.loads(self._state_file.read_text())
            return str(data.get("remotedesktop_restore_token", "") or "")
        except Exception:  # noqa: BLE001 - state is best-effort
            return ""

    def _save_restore_token(self, token: str) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps(
                {"remotedesktop_restore_token": token}))
        except Exception as e:  # noqa: BLE001 - state is best-effort
            self.log(f"WARN could not save the portal restore token: {e}")


def _fd_socket(fd: int):
    """Wrap a received EIS fd in a socket object (and take ownership)."""
    import socket
    sock = socket.socket(fileno=fd)
    sock.setblocking(True)
    return sock
