"""RemoteDesktop portal + EIS keyboard: the sanctioned sandbox insertion
path (ADR-0006).

Inside the Flatpak sandbox xdotool/wtype/ydotool are unavailable or
refused; the portal stack replaces them:

    org.freedesktop.portal.RemoteDesktop.CreateSession
      -> ConnectToEIS(session)          # returns a Unix socket fd
      -> SelectDevices(keyboard)        # devices bitfield 1 = keyboard
      -> Start()                        # consent: persisted on portal
                                       # >= 1.21, per-session dialog on
                                       # older (both flows supported -
                                       # the user just answers the
                                       # dialog again)
    then keystrokes over the EIS socket (fluidvoice.eis).

This module owns the threading so insertion.py stays synchronous: one
dedicated asyncio loop thread does the D-Bus/portal dance, and once the
EIS socket is up the (fast, pre-computed) keystroke writes happen on
the calling thread under a lock.

Selection is always runtime-probed, never desktop-name-gated:
``probe()`` answers "does THIS session's portal expose ConnectToEIS"
via D-Bus introspection plus the RemoteDesktop interface version. On
wlroots compositors the RemoteDesktop portal is not implemented at all
- the probe fails there and the caller falls to the
zwp_virtual_keyboard (wtype) path.

EIS typing is ASCII-keycode-only (see fluidvoice.eis); out-of-layout
Unicode degrades to clipboard + EIS-sent Ctrl+V, which is exactly the
ADR fallback ladder.
"""
from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass

from . import eis

PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
REMOTE_DESKTOP_IFACE = "org.freedesktop.portal.RemoteDesktop"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"

REMOTE_DESKTOP_DEVICE_KEYBOARD = 1  # SelectDevices bitfield

START_TIMEOUT_S = 45.0     # consent dialog on older portals is user-paced
PROBE_TIMEOUT_S = 3.0


class EisUnavailable(RuntimeError):
    """The EIS insertion path is not usable in this session (probe
    failed, portal denied consent, or transport died)."""


@dataclass(frozen=True)
class EisProbe:
    """Runtime verdict on the RemoteDesktop/EIS path."""
    ok: bool
    version: int | None = None
    has_connect_to_eis: bool | None = None
    detail: str = ""

    @property
    def describe(self) -> str:
        if self.ok:
            return (f"available (RemoteDesktop v{self.version}, "
                    "ConnectToEIS present)")
        return f"unavailable ({self.detail})"


_probe_cache: EisProbe | None = None


def reset_probe_cache() -> None:
    """Forget the cached probe (tests; a portal restart)."""
    global _probe_cache
    _probe_cache = None


def _bus_connectable() -> bool:
    """True when a session bus is REACHABLE BY CONFIGURATION - never let
    the probe spawn dbus-daemon or block: flatpak and desktop sessions
    always set DBUS_SESSION_BUS_ADDRESS; without it (headless CI) there
    is nothing to ask."""
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return True
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    return bool(runtime and os.path.exists(os.path.join(runtime, "bus")))


async def _call(bus, msg, timeout_s: float):
    from dbus_next import MessageType
    try:
        reply = await asyncio.wait_for(bus.call(msg), timeout_s)
    except asyncio.TimeoutError:
        raise EisUnavailable(f"D-Bus call timed out ({msg.member})") from None
    except Exception as e:  # noqa: BLE001 - surfaced as probe/start failure
        raise EisUnavailable(f"D-Bus call {msg.member} failed: {e}") from e
    if reply.message_type == MessageType.ERROR.value:
        detail = reply.body[0] if reply.body else reply.error_name
        raise EisUnavailable(f"{msg.member}: {detail}")
    return reply


async def _probe_once() -> EisProbe:
    from dbus_next import Message
    from dbus_next.aio import MessageBus

    if not _bus_connectable():
        return EisProbe(False, detail="no session bus configured")
    try:
        bus = await asyncio.wait_for(
            MessageBus(negotiate_unix_fd=True).connect(), PROBE_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - probe verdict, not a crash
        return EisProbe(False, detail=f"session bus unreachable: {e}")
    try:
        version_reply = await _call(bus, Message(
            destination=PORTAL_BUS_NAME, path=PORTAL_OBJECT_PATH,
            interface="org.freedesktop.DBus.Properties", member="Get",
            signature="ss", body=[REMOTE_DESKTOP_IFACE, "version"]))
        version = int(version_reply.body[0].value)
    except EisUnavailable as e:
        return EisProbe(False, detail=f"portal not answering: {e}")
    finally:
        try:
            await bus.disconnect()
        except Exception:  # noqa: S110 — best-effort path; caller must proceed
            pass  # probe bus: nothing rides on a clean goodbye
    # ConnectToEIS presence from live introspection - the honest runtime
    # protocol probe (no version tables to drift): the member either is
    # or is not on the interface this portal exports right now.
    try:
        bus2 = await asyncio.wait_for(
            MessageBus().connect(), PROBE_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - probe verdict, not a crash
        return EisProbe(False, version=version, has_connect_to_eis=None,
                        detail=f"session bus unreachable for introspect: {e}")
    try:
        intro = await _call(bus2, Message(
            destination=PORTAL_BUS_NAME, path=PORTAL_OBJECT_PATH,
            interface="org.freedesktop.DBus.Introspectable",
            member="Introspect"))
        has = "ConnectToEIS" in (intro.body[0] if intro.body else "")
    except EisUnavailable as e:
        return EisProbe(False, version=version, has_connect_to_eis=None,
                        detail=f"introspection failed: {e}")
    finally:
        try:
            await bus2.disconnect()
        except Exception:  # noqa: S110 — best-effort path; caller must proceed
            pass
    if not has:
        return EisProbe(False, version=version, has_connect_to_eis=False,
                        detail="portal has no ConnectToEIS (wlroots "
                               "implements no RemoteDesktop backend)")
    return EisProbe(True, version=version, has_connect_to_eis=True)


def probe(*, refresh: bool = False) -> EisProbe:
    """Cached runtime probe of the RemoteDesktop/ConnectToEIS path.
    Safe from any thread (own throwaway loop); never raises."""
    global _probe_cache
    if _probe_cache is not None and not refresh:
        return _probe_cache
    loop = asyncio.new_event_loop()
    try:
        _probe_cache = loop.run_until_complete(
            asyncio.wait_for(_probe_once(), PROBE_TIMEOUT_S * 2))
    except Exception as e:  # noqa: BLE001 - probe verdict, not a crash
        _probe_cache = EisProbe(False, detail=f"probe error: {e}")
    finally:
        loop.close()
    return _probe_cache


# ---------------------------------------------------------------------------
# Portal session setup (the default transport connector)
# ---------------------------------------------------------------------------

async def _await_response(bus, request_path: str, timeout_s: float) -> dict:
    """Portal Request pattern: wait for Response(0, results) on the
    per-call request object."""
    from dbus_next import Variant
    outcome: dict = {}

    def _unwrap(d):
        return {k: v.value if isinstance(v, Variant) else v
                for k, v in (d or {}).items()}

    event = asyncio.Event()

    def handler(msg) -> None:
        if (msg.path == request_path and msg.interface == REQUEST_IFACE
                and msg.member == "Response"):
            outcome["code"] = msg.body[0]
            outcome["results"] = _unwrap(msg.body[1])
            event.set()

    bus.add_message_handler(handler)
    try:
        await asyncio.wait_for(event.wait(), timeout_s)
    except asyncio.TimeoutError:
        raise EisUnavailable(f"portal response timed out ({request_path})") \
            from None
    finally:
        bus.remove_message_handler(handler)
    if outcome.get("code") != 0:
        raise EisUnavailable(f"portal request refused "
                             f"(code {outcome.get('code')})")
    return outcome.get("results", {})


async def _request_call(bus, member: str, signature: str, body: list,
                        timeout_s: float) -> dict:
    from dbus_next import Message
    reply = await _call(bus, Message(
        destination=PORTAL_BUS_NAME, path=PORTAL_OBJECT_PATH,
        interface=REMOTE_DESKTOP_IFACE, member=member,
        signature=signature, body=body))
    return await _await_response(bus, reply.body[0], timeout_s)


def _random_token(prefix: str) -> str:
    import secrets
    return f"{prefix}_{secrets.token_hex(8)}"


class _PortalTransport:
    """The live portal+EIS plumbing behind PortalKeyboard.

    Kept private: tests inject a fake connector instead of faking D-Bus.
    ``type_text``/``press_combo`` are synchronous (small pre-computed
    socket writes); ``close_async`` tears the portal session down."""

    def __init__(self, bus, session_handle: str, conn: eis.EisConnection):
        self._bus = bus
        self._session = session_handle
        self.eis = conn

    def type_text(self, text: str, delay_ms: int) -> None:
        self.eis.type_text(text, delay_ms)

    def press_combo(self, keys: list[tuple[int, bool]]) -> None:
        self.eis.tap_combo(keys)

    def encodable(self, text: str) -> bool:
        return eis.encodable(text)

    def alive(self) -> bool:
        return self.eis.keyboard_id is not None

    async def close_async(self) -> None:
        from dbus_next import Message
        try:
            self.eis.close()
        except Exception:  # noqa: S110 — teardown/cleanup must not raise
            pass  # socket already gone
        if self._session:
            try:
                await asyncio.wait_for(_call(self._bus, Message(
                    destination=PORTAL_BUS_NAME, path=self._session,
                    interface=SESSION_IFACE, member="Close",
                    signature="", body=[])), 3.0)
            except Exception:  # noqa: S110 — teardown/cleanup must not raise
                pass  # portal closes sessions with the client anyway
        try:
            await self._bus.disconnect()
        except Exception:  # noqa: S110 — teardown/cleanup must not raise
            pass


async def connect_portal_keyboard() -> _PortalTransport:
    """CreateSession -> ConnectToEIS -> handshake -> SelectDevices ->
    Start; returns the live transport or raises EisUnavailable."""
    import socket as _socket

    from dbus_next import Message, Variant
    from dbus_next.aio import MessageBus

    if not _bus_connectable():
        raise EisUnavailable("no session bus configured")
    try:
        bus = await asyncio.wait_for(
            MessageBus(negotiate_unix_fd=True).connect(), PROBE_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - surfaced as start failure
        raise EisUnavailable(f"session bus unreachable: {e}") from e

    async def _fail(msg: str) -> EisUnavailable:
        try:
            await bus.disconnect()
        except Exception:  # noqa: S110 — best-effort path; caller must proceed
            pass
        return EisUnavailable(msg)

    # 1) CreateSession (Request pattern carries the session handle back)
    token = _random_token("sayerm")
    request = _random_token("req")
    try:
        reply = await _call(bus, Message(
            destination=PORTAL_BUS_NAME, path=PORTAL_OBJECT_PATH,
            interface=REMOTE_DESKTOP_IFACE, member="CreateSession",
            signature="a{sv}",
            body=[{"handle_token": Variant("s", request),
                   "session_handle_token": Variant("s", token)}]))
        results = await _await_response(bus, reply.body[0], 30.0)
    except EisUnavailable as e:
        raise await _fail(f"CreateSession failed: {e}") from e
    session_handle = results.get("session_handle")
    if not session_handle:
        raise await _fail("portal returned no session handle")

    # 2) ConnectToEIS: the EIS socket fd rides the method REPLY (unix fd
    #    passing, hence negotiate_unix_fd=True)
    try:
        reply = await _call(bus, Message(
            destination=PORTAL_BUS_NAME, path=PORTAL_OBJECT_PATH,
            interface=REMOTE_DESKTOP_IFACE, member="ConnectToEIS",
            signature="oa{sv}", body=[session_handle, {}]))
    except EisUnavailable as e:
        raise await _fail(f"ConnectToEIS failed: {e}") from e
    fds = getattr(reply, "unix_fds", None) or []
    if not fds:
        raise await _fail("ConnectToEIS returned no fd")

    # 3) EIS handshake + keyboard bind on the fresh socket (blocking,
    #    bounded - this socket has no consent dialog on it)
    sock = _socket.socket(fileno=fds[0])
    sock.setblocking(True)
    conn = eis.EisConnection(sock)
    try:
        conn.connect()
        conn.bind_keyboard()
    except eis.EisError as err:
        try:
            conn.close()
        except Exception:  # noqa: S110 — teardown/cleanup must not raise
            pass
        raise await _fail(f"EIS handshake failed: {err}") from err

    # 4) SelectDevices(keyboard) + Start: consent happens here (persisted
    #    on portal >= 1.21; a dialog on older ones - user-paced timeout)
    transport = _PortalTransport(bus, session_handle, conn)
    try:
        await _request_call(bus, "SelectDevices", "oa{sv}",
                            [session_handle,
                             {"types": Variant("u",
                                               REMOTE_DESKTOP_DEVICE_KEYBOARD)}],
                            30.0)
        await _request_call(bus, "Start", "osa{sv}",
                            [session_handle, "", {}], START_TIMEOUT_S)
    except EisUnavailable as e:
        await transport.close_async()
        raise EisUnavailable(f"portal Start failed (consent denied?): {e}") \
            from e
    return transport


# ---------------------------------------------------------------------------
# PortalKeyboard: thread-owning facade for the daemon/insertion seam
# ---------------------------------------------------------------------------

class PortalKeyboard:
    """Synchronous facade over the portal+EIS stack.

    start() spawns the loop thread, runs the connector and waits; on any
    failure it unwinds completely and returns False (the caller falls
    down the insertion ladder). type_text/press_combo then run on the
    CALLING thread under a lock - the writes are pre-computed socket
    sends, too small to justify a hop through the loop.
    """

    def __init__(self, *, log=None, connector=None):
        self.log = log or (lambda m: print(f"[sayit-ermano] {m}",
                                           flush=True))
        self._connector = connector or connect_portal_keyboard
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._transport = None
        self._io_lock = threading.Lock()
        self._stop = threading.Event()
        self.summary: list[str] = []
        self.start_error: str | None = None

    @property
    def ready(self) -> bool:
        transport = self._transport
        return bool(transport and transport.alive())

    def start(self, timeout_s: float = START_TIMEOUT_S) -> bool:
        """Best-effort start: False + WARN (never raises) when the portal
        path is unavailable - the caller keeps its fallbacks."""
        if self._thread is not None:
            return self.ready
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._thread = threading.Thread(
            target=self._loop_thread, args=(loop,),
            name="fluidvoice-eis-portal", daemon=True)
        self._thread.start()
        try:
            fut = asyncio.run_coroutine_threadsafe(self._connect(), loop)
            self._transport = fut.result(timeout=timeout_s)
        except Exception as e:  # noqa: BLE001 - start() reports, not raises
            self.start_error = str(e)
            self.log(f"WARN EIS insertion unavailable: {e}")
            self.stop()
            return False
        probed = probe()
        self.summary = [
            f"EIS insertion ready (RemoteDesktop v{probed.version}, "
            "ConnectToEIS)",
            "typing: EIS keycodes (ASCII); out-of-layout Unicode: "
            "clipboard + EIS Ctrl+V",
        ]
        return True

    async def _connect(self):
        return await self._connector()

    @staticmethod
    def _loop_thread(loop: asyncio.AbstractEventLoop) -> None:
        # the loop rides the argument: stop() may null self._loop before
        # the thread body runs (failed-start race)
        try:
            loop.run_forever()
        finally:
            loop.close()

    def stop(self, timeout_s: float = 5.0) -> None:
        """Deterministic teardown; idempotent; never raises."""
        self._stop.set()
        loop, self._loop = self._loop, None
        transport, self._transport = self._transport, None
        thread, self._thread = self._thread, None
        if loop is not None and transport is not None:
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    transport.close_async(), loop)
                fut.result(timeout=timeout_s)
            except Exception:  # noqa: S110 — teardown/cleanup must not raise
                pass  # portal sessions die with the connection anyway
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout_s)

    def _with_transport(self):
        with self._io_lock:
            transport = self._transport
            if transport is None or not transport.alive():
                raise EisUnavailable("EIS keyboard is not connected")
            return transport

    def type_text(self, text: str, delay_ms: int = 0) -> None:
        self._with_transport().type_text(text, delay_ms)

    def press_combo(self, keys: list[tuple[int, bool]]) -> None:
        self._with_transport().press_combo(keys)

    def encodable(self, text: str) -> bool:
        with self._io_lock:
            transport = self._transport
            return bool(transport and transport.encodable(text))


# ---------------------------------------------------------------------------
# Shared instance + preference resolution (the insertion seam)
# ---------------------------------------------------------------------------

_shared: PortalKeyboard | None = None
_shared_lock = threading.Lock()


def preference(cfg: dict) -> str:
    """insertion.wayland_tool normalized for the EIS decision: "eis" |
    "auto" | an external tool. The EIS path serves "auto" and "eis"."""
    pref = str(((cfg or {}).get("insertion") or {}).get(
        "wayland_tool", "auto")).strip().lower()
    return pref if pref else "auto"


def resolve_eis(cfg: dict) -> bool:
    """True when the EIS path should be PREFERRED for this config:
    explicit "eis", or "auto" with a passing runtime probe. The probe
    never spawns a bus and never blocks long."""
    pref = preference(cfg)
    if pref in ("wtype", "ydotool"):
        return False
    if pref == "eis":
        return probe().ok
    return probe().ok  # auto: portal first, wtype/ydotool behind it


def shared_keyboard() -> PortalKeyboard | None:
    with _shared_lock:
        return _shared


def ensure_shared(cfg: dict, log=None) -> bool:
    """Start (once) the shared portal keyboard when the preference and
    the runtime probe agree; False when unavailable. Cached start: a
    failed start is retried on the next ensure call after a probe
    refresh, so a portal that comes up later is picked up."""
    global _shared
    if not resolve_eis(cfg):
        return False
    with _shared_lock:
        if _shared is not None and _shared.ready:
            return True
        keyboard = PortalKeyboard(log=log)
        if not keyboard.start():
            reset_probe_cache()  # next ensure re-probes honestly
            return False
        _shared = keyboard
        return True


def stop_shared() -> None:
    """Tear down the shared keyboard (daemon shutdown; tests)."""
    global _shared
    with _shared_lock:
        keyboard, _shared = _shared, None
    if keyboard is not None:
        keyboard.stop()


def type_text(text: str, delay_ms: int = 0) -> None:
    """EIS type via the shared keyboard; EisUnavailable when not running."""
    keyboard = shared_keyboard()
    if keyboard is None or not keyboard.ready:
        raise EisUnavailable("EIS keyboard not started")
    keyboard.type_text(text, delay_ms)


def press_key(spec: str) -> None:
    """EIS key combo ('ctrl+v') via the shared keyboard."""
    keyboard = shared_keyboard()
    if keyboard is None or not keyboard.ready:
        raise EisUnavailable("EIS keyboard not started")
    keyboard.press_combo(eis.parse_key_spec(spec))


def encodable(text: str) -> bool:
    """True when the shared keyboard can type every char of text."""
    keyboard = shared_keyboard()
    if keyboard is None:
        return eis.encodable(text)
    return keyboard.encodable(text)
