"""Runtime input-backend probes (ADR-0006): decide by PROTOCOL, not name.

The old resolver guessed the compositor from XDG_CURRENT_DESKTOP and
excluded wtype on "GNOME" by name. The ADR replaces that with runtime
probes:

- ``probe_virtual_keyboard()`` — speaks just enough Wayland wire
  protocol (wl_display.get_registry + one roundtrip) to list the
  compositor's globals and look for ``zwp_virtual_keyboard_manager_v1``
  (wtype's requirement; implemented by wlroots-family compositors, not
  mutter#1974, not KWin bug 512996).
- ``probe_remote_desktop()`` — the RemoteDesktop portal's interface
  version (ConnectToEIS from v2), from portal_eis.

Both return ``None`` when the probe cannot run (no WAYLAND_DISPLAY, no
session bus, library missing) — "unknown", never a guess. Results are
cached briefly (``CACHE_TTL_S``) with short socket timeouts so callers
can probe freely; failures are cheap and non-fatal.

AF_UNIX only: the registry probe connects to the compositor socket in
XDG_RUNTIME_DIR; the portal probe goes through the session bus. The
unit-tier network guard is untouched (loopback/AF_UNIX exemptions).
"""
from __future__ import annotations

import os
import struct
import time
from dataclasses import dataclass

CACHE_TTL_S = 30.0
PROBE_TIMEOUT_S = 1.0

VIRTUAL_KEYBOARD_IFACE = "zwp_virtual_keyboard_manager_v1"

# Wayland wire: u32 object-id header, then u32 (opcode << 16 | size).
# wl_display is object 1; get_registry is request 1, sync is request 0.
_WL_DISPLAY_ID = 1
_WL_DISPLAY_GET_REGISTRY = 1
_WL_DISPLAY_SYNC = 0
_WL_REGISTRY_GLOBAL = 0     # (name u32, interface string, version u32)
_WL_CALLBACK_DONE = 0       # (data u32)


def _wl_str(s: str) -> bytes:
    raw = s.encode()
    pad = -len(raw) % 4
    return struct.pack("I", len(raw)) + raw + b"\x00" * pad


def parse_wl_globals(data: bytes) -> dict[str, int]:
    """Parse a stream of wl_registry.global + wl_callback.done messages
    (collected from the compositor) into {interface: max version}.

    Body layout of wl_registry.global: name u32, interface string
    (u32 length incl. NUL + padded bytes), version u32."""
    out: dict[str, int] = {}
    off = 0
    while off + 8 <= len(data):
        obj_id, word = struct.unpack_from("<II", data, off)
        opcode, size = word >> 16, word & 0xFFFF
        if size < 8 or off + size > len(data):
            break
        body = data[off + 8:off + size]
        if opcode == _WL_REGISTRY_GLOBAL and len(body) >= 16:
            slen = struct.unpack_from("<I", body, 4)[0]
            if slen > 0:
                raw = body[8:8 + slen - 1]
                voff = 8 + slen + (-slen % 4)
                if voff + 4 <= len(body):
                    version = struct.unpack_from("<I", body, voff)[0]
                    try:
                        iface = raw.decode()
                    except UnicodeDecodeError:
                        iface = ""
                    if iface:
                        out[iface] = max(out.get(iface, 0), version)
        off += size
    return out


def wayland_socket_path() -> str | None:
    display = (os.environ.get("WAYLAND_DISPLAY") or "").strip()
    if not display:
        return None
    if display.startswith("/"):
        return display
    runtime = os.environ.get("XDG_RUNTIME_DIR") or ""
    if not runtime:
        return None
    return os.path.join(runtime, display)


def probe_virtual_keyboard(path: str | None = None,
                           timeout: float = PROBE_TIMEOUT_S) -> bool | None:
    """True/False when the registry probe answers, None when it can't
    run (no WAYLAND_DISPLAY / XDG_RUNTIME_DIR, socket refused)."""
    sock_path = path or wayland_socket_path()
    if not sock_path:
        return None
    import socket
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(sock_path)
            # get_registry(new_id=2) + sync(new_id=3)
            req = struct.pack("<II", _WL_DISPLAY_ID,
                              (_WL_DISPLAY_GET_REGISTRY << 16) | 12) \
                + struct.pack("<I", 2)
            req += struct.pack("<II", _WL_DISPLAY_ID,
                               (_WL_DISPLAY_SYNC << 16) | 12) \
                + struct.pack("<I", 3)
            s.sendall(req)
            s.settimeout(timeout)
            data = b""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                data += chunk
                # stop once the sync callback fired (id 3, done)
                if _sync_done_seen(data):
                    break
            globals_found = parse_wl_globals(data)
            if not globals_found:
                return None if not data else False
            return VIRTUAL_KEYBOARD_IFACE in globals_found
    except OSError:
        return None


def _sync_done_seen(data: bytes) -> bool:
    off = 0
    while off + 8 <= len(data):
        _obj, word = struct.unpack_from("<II", data, off)
        size = word & 0xFFFF
        if size < 8 or off + size > len(data):
            return False
        if _obj == 3 and (word >> 16) == _WL_CALLBACK_DONE:
            return True
        off += size
    return False


@dataclass(frozen=True)
class InputProbes:
    virtual_keyboard: bool | None
    remote_desktop: bool | None


_probe_cache: dict[str, tuple[float, InputProbes]] = {}


def probe_input_backends(*, refresh: bool = False,
                         timeout: float = PROBE_TIMEOUT_S) -> InputProbes:
    """The composite probe (cached CACHE_TTL_S): registry scan for the
    virtual-keyboard protocol + RemoteDesktop portal ConnectToEIS.

    Never raises; every leg that cannot run reports None."""
    key = f"t{timeout}"
    now = time.monotonic()
    if not refresh:
        hit = _probe_cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_S:
            return hit[1]
    vkb = None
    if os.environ.get("WAYLAND_DISPLAY"):
        try:
            vkb = probe_virtual_keyboard(timeout=timeout)
        except Exception:  # noqa: BLE001 - a probe never raises
            vkb = None
    rd = None
    try:
        rd = _probe_remote_desktop(timeout=timeout)
    except Exception:  # noqa: BLE001 - a probe never raises
        rd = None
    probes = InputProbes(virtual_keyboard=vkb, remote_desktop=rd)
    _probe_cache[key] = (now, probes)
    return probes


def _probe_remote_desktop(timeout: float = PROBE_TIMEOUT_S) -> bool | None:
    """The dbus leg of the composite probe (separate so tests and the
    portal-free paths can patch it out)."""
    try:
        import asyncio

        from .portal_eis import probe_remote_desktop
        return asyncio.run(probe_remote_desktop(timeout=timeout))
    except ImportError:
        return None


def reset_probe_cache() -> None:
    """Tests: forget cached probes."""
    _probe_cache.clear()
