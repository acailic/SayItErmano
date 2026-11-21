"""Runtime Wayland protocol probe: what does THIS compositor advertise?

ADR-0006 decision: wtype (zwp_virtual_keyboard_manager_v1) selection is
gated on a runtime protocol probe, NEVER on desktop-environment name -
mutter and KWin refuse the protocol while wlroots ships it, and name
sniffing both over- and under-shoots (Hyprland is wlroots-based but not
matched by the old token list; a GNOME session with a nested sway is
the reverse).

The probe speaks the minimum Wayland wire protocol by hand - one
wl_display.get_registry + wl_display.sync roundtrip, then close. No
pywayland dependency, no state, nothing bound: the registry globals are
advisory until a client binds them, so this cannot disturb the
compositor.

Wire format (wayland.xml): every message is [opcode u16 | size u16]
followed by args; strings are [length u32 incl. NUL][bytes][pad to 4];
the size includes the 8-byte header. Object 1 is wl_display; ids 2, 3,
... are client-allocated on this connection.
"""
from __future__ import annotations

import os
import socket
import struct

WL_DISPLAY_GET_REGISTRY = 1   # (new_id registry)
WL_DISPLAY_SYNC = 0          # (new_id callback)
WL_REGISTRY_GLOBAL = 0       # (name u32, interface string, version u32)
WL_CALLBACK_DONE = 0         # (data u32)

REGISTRY_ID = 2              # first client-allocated object id
CALLBACK_ID = 3

PROBE_TIMEOUT_S = 2.0

# The virtual-keyboard manager protocol wtype needs (wlroots-family).
VIRTUAL_KEYBOARD_GLOBAL = "zwp_virtual_keyboard_manager_v1"

_header = struct.Struct("<HH")  # opcode, size (u16 each)


def _socket_path(env: dict | None = None) -> str | None:
    """Absolute path of the compositor socket, or None when unreachable
    by configuration (WAYLAND_DISPLAY may be an absolute path or a name
    under XDG_RUNTIME_DIR)."""
    e = os.environ if env is None else env
    wl = (e.get("WAYLAND_DISPLAY") or "").strip()
    if not wl:
        return None
    if os.path.isabs(wl):
        return wl
    runtime = (e.get("XDG_RUNTIME_DIR") or "").strip()
    if not runtime:
        return None
    path = os.path.join(runtime, wl)
    return path if os.path.exists(path) else None


def _encode_request(opcode: int, new_id: int) -> bytes:
    """One wayland request with a single new_id arg (get_registry/sync).
    The size field counts the WHOLE message: 8-byte header (object id +
    opcode + size) plus args."""
    args = struct.pack("<I", new_id)
    size = 8 + len(args)
    return _header.pack(opcode, size) + args


def _parse_string(args: bytes, off: int) -> tuple[str, int]:
    (n,) = struct.unpack_from("<I", args, off)
    off += 4
    pad = -n % 4
    raw = args[off:off + n]
    off += n + pad
    return raw[:max(0, n - 1)].decode(errors="replace"), off


def parse_globals(events: bytes) -> dict[str, int]:
    """Parse a buffer of wayland events into {interface: highest
    advertised version}. The wire format (wayland.xml): each message is
    [object-id u32][opcode u16][size u16][args], and `size` counts the
    WHOLE message including the 8-byte header. Non-registry events are
    ignored, never fatal.

    Exposed for tests: the live probe below feeds it real socket bytes.
    """
    globals_: dict[str, int] = {}
    off = 0
    total = len(events)
    while total - off >= _header.size + 4:
        (obj,) = struct.unpack_from("<I", events, off)
        opcode, size = _header.unpack_from(events, off + 4)
        if size < _header.size + 4 or size > total - off:
            break  # torn/garbage tail: stop, keep what we have
        args = events[off + _header.size + 4:off + size]
        off += size
        if obj == REGISTRY_ID and opcode == WL_REGISTRY_GLOBAL:
            (name,) = struct.unpack_from("<I", args, 0)
            interface, voff = _parse_string(args, 4)
            (version,) = struct.unpack_from("<I", args, voff)
            if interface:
                globals_[interface] = max(globals_.get(interface, 0),
                                          version)
    return globals_


def list_globals(timeout_s: float = PROBE_TIMEOUT_S,
                 path: str | None = None) -> dict[str, int] | None:
    """One registry roundtrip -> {interface: max version}; None when no
    compositor socket is reachable (no WAYLAND_DISPLAY, no runtime dir,
    connect failure, timeout). Never raises: a probe that cannot run is
    a probe that failed."""
    sock_path = path or _socket_path()
    if sock_path is None:
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout_s)
            s.connect(sock_path)
            s.sendall(_encode_request(WL_DISPLAY_GET_REGISTRY, REGISTRY_ID)
                      + _encode_request(WL_DISPLAY_SYNC, CALLBACK_ID))
            s.shutdown(socket.SHUT_WR)
            buf = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return parse_globals(buf)
    except OSError:
        return None
    except ValueError:
        return None


_cache: dict[tuple[str, str], bool] = {}


def probe_virtual_keyboard(env: dict | None = None,
                           *, refresh: bool = False) -> bool:
    """True when the compositor advertises zwp_virtual_keyboard_manager_v1
    (the wtype insertion path). Cached per (socket path, WAYLAND_DISPLAY)
    so daemon start and every insert do not re-roundtrip."""
    e = os.environ if env is None else env
    wl = (e.get("WAYLAND_DISPLAY") or "").strip()
    key = (wl, (e.get("XDG_RUNTIME_DIR") or "").strip())
    if not refresh:
        hit = _cache.get(key)
        if hit is not None:
            return hit
    globals_ = list_globals()
    supported = bool(globals_
                     and VIRTUAL_KEYBOARD_GLOBAL in globals_)
    _cache[key] = supported
    return supported


def reset_probe_cache() -> None:
    """Forget cached verdicts (tests; a compositor restart)."""
    _cache.clear()
