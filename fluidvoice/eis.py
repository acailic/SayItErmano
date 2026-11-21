"""Pure-Python EIS wire client: keyboard typing over the EI protocol.

The RemoteDesktop portal's ``ConnectToEIS`` hands us a socket to an EIS
implementation (the compositor's emulated-input server, usually proxied
by xdg-desktop-portal). Everything after that fd is the EI wire protocol
(https://libinput.pages.freedesktop.org/libei/) — a Wayland-style stream
of (object-id u64, length u32, opcode u32) messages in native byte
order. No Python EIS client exists on PyPI (checked 2026-11: the "pyei"
name is an ecological-inference package), libei's own bindings are
C-only, so this module speaks the wire directly. ADR-0006 makes this the
sanctioned insertion path on GNOME/KDE Wayland; wtype
(zwp_virtual_keyboard) stays the wlroots path and ydotool the last
resort.

Scope is deliberately minimal: a **sender** context that binds one
seat's keyboard capability and types. Pointer/touch regions, receiver
contexts and ei_seat v2 ``request_device`` negotiation are out of scope.

Wire-version drift is handled at runtime (never by compositor name):

- libei 0.4/0.5 era (GNOME 46/47 link against these) and libei >= 0.99
  share the same ``ei_handshake`` (interface version 1 everywhere), but
  differ afterwards:
  * seat capabilities: one ``capabilities(u32 mask)`` event (legacy) vs
    a burst of ``capability(u64 mask, string interface)`` events (1.x);
  * ``ei_seat.bind`` width: u32 (legacy) vs u64 (1.x);
  * legacy ``ei_device`` events carry an extra ``capabilities`` opcode
    (2), shifting every later device-event opcode by one.
  The seat burst always arrives before the client may send ``bind``,
  and the two encodings differ in body length at the same opcode, so
  the client discriminates there (``_legacy_wire``) and decodes the
  rest of the session accordingly.
- We announce ``ei_seat``/``ei_device`` interface version 1: that keeps
  the libei-1.x-only ``ei_device.ready`` (required since device v3)
  out of scope while staying compatible with legacy servers.

Typing is keycode-based — EIS has never carried keysyms on the wire
(verified against the 0.4 protobuf and the 0.5/1.x protocol XML).
Keycodes are interpreted through the server-provided XKB keymap
(``ei_keyboard.keymap`` fd); without a server keymap the built-in US
evdev table is used. Only keymap levels 1/2 (base + shift) are
resolved; anything else (AltGr layers, out-of-layout Unicode) raises
``EISUnsupportedChar`` so insertion.py can take the documented
clipboard + Ctrl+V fallback (EIS cannot upload keymaps; ``ei_text``
UTF-8 needs libei 1.6 compositor-side and is tracked as a follow-up).
"""
from __future__ import annotations

import os
import re
import socket
import struct
import sys
import time
from dataclasses import dataclass, field

# -- linux evdev keycodes (linux/input-event-codes.h) ------------------------
KEY_ESC = 1
KEY_1 = 2
KEY_TAB = 15
KEY_ENTER = 28
KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_LEFTALT = 56
KEY_SPACE = 57
KEY_RIGHTSHIFT = 54
KEY_RIGHTCTRL = 97
KEY_RIGHTALT = 100
KEY_LEFTMETA = 125
KEY_RIGHTMETA = 126
KEY_BACKSPACE = 14

TOKEN_KEY_CODES = {
    "ctrl": KEY_LEFTCTRL, "shift": KEY_LEFTSHIFT, "alt": KEY_LEFTALT,
    "super": KEY_LEFTMETA, "meta": KEY_LEFTMETA, "logo": KEY_LEFTMETA,
    "enter": KEY_ENTER, "return": KEY_ENTER, "esc": KEY_ESC,
    "escape": KEY_ESC, "tab": KEY_TAB, "space": KEY_SPACE,
    "backspace": KEY_BACKSPACE,
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34,
    "h": 35, "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49,
    "o": 24, "p": 25, "q": 16, "r": 19, "s": 31, "t": 20, "u": 22,
    "v": 47, "w": 17, "x": 45, "y": 21, "z": 44,
}

# -- X keysym names for printable ASCII + control chars ----------------------
_ASCII_KEYSYMS = {
    " ": "space", "\t": "Tab", "\n": "Return",
    "!": "exclam", '"': "quotedbl", "#": "numbersign", "$": "dollar",
    "%": "percent", "&": "ampersand", "'": "apostrophe",
    "(": "parenleft", ")": "parenright", "*": "asterisk", "+": "plus",
    ",": "comma", "-": "minus", ".": "period", "/": "slash",
    ":": "colon", ";": "semicolon", "<": "less", "=": "equal",
    ">": "greater", "?": "question", "@": "at",
    "[": "bracketleft", "\\": "backslash", "]": "bracketright",
    "^": "asciicircum", "_": "underscore", "`": "grave",
    "{": "braceleft", "|": "bar", "}": "braceright", "~": "asciitilde",
}

# US evdev fallback (used when the EIS sends no keymap):
# char -> (evdev keycode, shifted?)
_US_KEYMAP: dict[str, tuple[int, bool]] = {}


def _build_us_keymap() -> None:
    rows = (
        ("`1234567890-=", (41, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13)),
        ("qwertyuiop[]", (16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27)),
        ("asdfghjkl;'", (30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41)),
        ("\\zxcvbnm,./", (43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54)),
    )
    shifted_row1 = "~!@#$%^&*()_+"
    shifted_row4 = '|:"<>?'
    for chars, codes in rows:
        for ch, code in zip(chars, codes):
            _US_KEYMAP[ch] = (code, False)
    for i, ch in enumerate(shifted_row1):
        _US_KEYMAP[ch] = (41 if i == 0 else KEY_1 + i - 1, True)
    for i, ch in enumerate("qwertyuiop"):
        _US_KEYMAP[ch.upper()] = (16 + i, True)
    for i, ch in enumerate("asdfghjkl"):
        _US_KEYMAP[ch.upper()] = (30 + i, True)
    for i, ch in enumerate("zxcvbnm"):
        _US_KEYMAP[ch.upper()] = (44 + i, True)
    for i, ch in enumerate(shifted_row4):
        code = (43, 39, 40, 51, 52, 53)[i]
        _US_KEYMAP[ch] = (code, True)
    _US_KEYMAP["{"] = (26, True)
    _US_KEYMAP["}"] = (27, True)
    _US_KEYMAP[" "] = (KEY_SPACE, False)
    _US_KEYMAP["\n"] = (KEY_ENTER, False)
    _US_KEYMAP["\t"] = (KEY_TAB, False)


_build_us_keymap()


def char_keysym_names(ch: str) -> list[str]:
    """Candidate X keysym names for one character, best guess first.

    ASCII letters/digits/punctuation are named directly (X11
    convention); non-ASCII gets the Unicode keysym naming variants
    xkbcomp emits (``U00e9``/``Ue9``/``uni00e9``). Literal names like
    ``ccaron`` arrive via the keymap parse instead of here."""
    if ch in _ASCII_KEYSYMS:
        return [_ASCII_KEYSYMS[ch]]
    if ch.isascii() and ch.isalnum():
        return [ch]
    cp = ord(ch)
    if cp < 0x100:
        return [f"U{cp:04x}", f"U{cp:04X}"]
    return [f"U{cp:04x}", f"uni{cp:04x}", f"U{cp:x}"]


class EISError(RuntimeError):
    pass


class EISProtocolError(EISError):
    """The EIS implementation sent something we cannot parse."""


class EISTimeout(EISError):
    """A bounded wait for an EIS event expired."""


class EISDisconnected(EISError):
    """The EIS implementation disconnected us (reason rides along)."""


class EISUnsupportedChar(EISError):
    """Text contains characters unreachable at keymap levels 1/2.

    Callers take the clipboard + Ctrl+V fallback (ADR-0006); the
    offending characters ride on ``chars``."""

    def __init__(self, chars: str):
        shown = ", ".join(repr(c) for c in chars[:10])
        super().__init__(f"characters outside EIS keymap levels 1/2: {shown}")
        self.chars = chars


_CONTEXT_SENDER = 2
_CAP_KEYBOARD_LEGACY = 4   # legacy seat capability bit: keyboard
_HDR_LEN = 16


def parse_xkb_keymap(text: str) -> dict[str, tuple[int, bool]]:
    """XKB keymap string -> {keysym name: (keycode, shifted)}.

    Resolves levels 1 (base) and 2 (shift) only — everything deeper is
    the caller's clipboard fallback. The keymap an EIS implementation
    sends is an xkbcommon text dump: ``xkb_keycodes`` holds ``<AE01> =
    10;`` name/code pairs, ``xkb_symbols`` holds ``key <AE01> { [ 1,
    exclam ] };`` level lists (possibly behind ``type[Group1]=...``
    declarations and multiple groups — only the first bracket list is
    used, i.e. group 1)."""
    codes: dict[str, int] = {}
    keycodes = re.search(r'xkb_keycodes\s+"[^"]*"\s*\{(.*?)\};', text, re.S)
    if keycodes:
        for name, code in re.findall(r"<(\w+)>\s*=\s*(\d+)\s*;",
                                     keycodes.group(1)):
            codes[name] = int(code)
    out: dict[str, tuple[int, bool]] = {}
    symbols = re.search(r'xkb_symbols\s+"[^"]*"\s*\{(.*?)\n\};', text, re.S)
    if not symbols:
        return out
    for block in re.finditer(r"key\s*<(\w+)>\s*\{([^}]*)\};",
                             symbols.group(1)):
        keyname, inner = block.group(1), block.group(2)
        code = codes.get(keyname)
        if code is None:
            continue
        # a key block may open with type declarations ("type[Group1]=
        # ...") whose brackets are not levels - the LEVEL list is the
        # last bracket group in the block
        bracket_lists = re.findall(r"\[([^\]]*)\]", inner)
        if not bracket_lists:
            continue
        syms = [t.strip().strip('"')
                for t in bracket_lists[-1].split(",")]
        for idx, sym in enumerate(syms[:2]):
            if not sym:
                continue
            # a level-1 match wins over an existing level-2 entry
            if sym not in out or idx == 0:
                out[sym] = (code, idx == 1)
    return out


class _Wire:
    """EI message pack/unpack for one socket (native byte order)."""

    def __init__(self) -> None:
        # EI integers are in the EIS implementation's native byte order;
        # we run on the same host, so "=" (standard sizes, host order)
        # is the honest encoding on both little- and big-endian hosts.
        self._e = "<" if sys.byteorder == "little" else ">"

    def header(self, obj_id: int, opcode: int, body: bytes) -> bytes:
        return struct.pack(self._e + "QII", obj_id, _HDR_LEN + len(body),
                           opcode) + body

    def parse_header(self, buf: bytes) -> tuple[int, int, int]:
        if len(buf) < _HDR_LEN:
            raise EISProtocolError("short EI header")
        return struct.unpack_from(self._e + "QII", buf)

    def u32(self, v: int) -> bytes:
        return struct.pack(self._e + "I", v & 0xFFFFFFFF)

    def u64(self, v: int) -> bytes:
        return struct.pack(self._e + "Q", v)

    def string(self, s: str | None) -> bytes:
        if s is None:
            return self.u32(0)
        raw = s.encode()
        length = len(raw) + 1  # NUL always rides along
        pad = -length % 4
        return (struct.pack(self._e + "I", length)
                + raw + b"\x00" * (1 + pad))

    def read_u32(self, body: bytes, off: int) -> tuple[int, int]:
        if off + 4 > len(body):
            raise EISProtocolError("truncated u32 argument")
        return struct.unpack_from(self._e + "I", body, off)[0], off + 4

    def read_u64(self, body: bytes, off: int) -> tuple[int, int]:
        if off + 8 > len(body):
            raise EISProtocolError("truncated u64 argument")
        return struct.unpack_from(self._e + "Q", body, off)[0], off + 8

    def read_string(self, body: bytes, off: int) -> tuple[str | None, int]:
        raw_len, off = self.read_u32(body, off)
        if raw_len == 0:
            return None, off
        pad = -raw_len % 4
        if off + raw_len + pad > len(body):
            raise EISProtocolError("truncated string argument")
        raw = body[off:off + raw_len - 1]
        try:
            return raw.decode("utf-8"), off + raw_len + pad
        except UnicodeDecodeError as e:
            raise EISProtocolError("non-UTF8 string argument") from e


@dataclass
class _Keyboard:
    obj_id: int
    keymap_symbols: dict[str, tuple[int, bool]] = field(default_factory=dict)


@dataclass
class _Device:
    obj_id: int
    name: str = ""
    keyboard: _Keyboard | None = None
    done: bool = False
    resumed: bool = False


class EISConnection:
    """A sender-context EI connection with one keyboard device bound.

    Lifecycle: ``connect()`` runs the handshake, binds the keyboard
    capability on the first seat and waits for a keyboard device to be
    advertised and resumed. ``type_text``/``press_combo`` send events;
    ``close()`` disconnects. Every wait is bounded — the consent dialog
    lives at the PORTAL layer (before ConnectToEIS), so nothing here
    should ever block on a human. The socket is owned from ``connect()``
    and closed by ``close()``."""

    def __init__(self, sock: socket.socket, *, name: str = "SayItErmano",
                 timeout: float = 5.0, log=None) -> None:
        self._sock = sock
        self._name = name
        self._timeout = timeout
        self.log = log or (lambda m: None)
        self._wire = _Wire()
        self._buf = b""
        self._pending_fds: list[int] = []
        self._objects: dict[int, str] = {0: "ei_handshake"}
        self.server_versions: dict[str, int] = {}
        self._serial = 0
        self._sequence = 0
        self._legacy_wire: bool | None = None
        self._seat_caps: dict[str, int] = {}
        self._legacy_seat_caps = 0
        self._seat_id: int | None = None
        self._seat_bound = False
        self._connection_id: int | None = None
        self.devices: dict[int, _Device] = {}
        self.keyboard: tuple[int, int] | None = None  # (device, keyboard)
        self._keymap: dict[str, tuple[int, bool]] | None = None
        self._closed = False

    # -- public API ----------------------------------------------------------

    def connect(self) -> None:
        deadline = time.monotonic() + self._timeout
        _op, body = self._await(0, 0, "ei_handshake.handshake_version",
                                 deadline)
        server_version, _off = self._wire.read_u32(body, 0)
        self._send(0, 0, self._wire.u32(min(1, server_version)))
        self._send(0, 2, self._wire.u32(_CONTEXT_SENDER))
        self._send(0, 3, self._wire.string(self._name))
        for iface in ("ei_connection", "ei_seat", "ei_device", "ei_keyboard"):
            self._send(0, 4, self._wire.string(iface) + self._wire.u32(1))
        self._send(0, 1, b"")  # finish
        self._pump_until(lambda: self._connection_id is not None, deadline,
                         "ei_handshake.connection")
        self._pump_until(lambda: self._seat_id is not None, deadline, "seat")
        self._pump_until(lambda: self._legacy_wire is not None, deadline,
                         "seat capabilities")
        self._bind_seat_keyboard()
        self._pump_until(lambda: self._keyboard_ready(), deadline,
                         "keyboard device")
        keymap = self.keyboard_device().keyboard.keymap_symbols if \
            self.keyboard_device().keyboard else {}
        if keymap:
            self._keymap = dict(keymap)
        else:
            self._keymap = dict(_US_KEYMAP)
            self.log("EIS: no server keymap - using the US evdev table")

    def keyboard_device(self) -> _Device:
        if not self.keyboard:
            raise EISProtocolError("no keyboard device")
        return self.devices[self.keyboard[0]]

    def unsupported_chars(self, text: str) -> str:
        """The characters of `text` unreachable at levels 1/2."""
        keymap = self._keymap or _US_KEYMAP
        out = []
        for ch in text:
            if ch in keymap:
                continue
            if not any(n in keymap for n in char_keysym_names(ch)):
                out.append(ch)
        return "".join(out)

    def type_text(self, text: str, delay_ms: int = 8) -> None:
        """Type `text` at keymap levels 1/2.

        One frame per key edge (two requests for one key in a frame are
        a client bug per spec); shift state is sent explicitly around
        shifted characters. Raises EISUnsupportedChar listing everything
        unreachable — the caller decides the clipboard fallback."""
        missing = self.unsupported_chars(text)
        if missing:
            raise EISUnsupportedChar(missing)
        if not text:
            return
        self._begin_batch()
        try:
            for ch in text:
                code, shifted = self._resolve(ch)
                if shifted:
                    self._send_key(KEY_LEFTSHIFT, True)
                    self._send_key(code, True)
                    self._send_key(code, False)
                    self._send_key(KEY_LEFTSHIFT, False)
                else:
                    self._send_key(code, True)
                    self._send_key(code, False)
                if delay_ms > 0:
                    time.sleep(min(delay_ms, 100) / 1000.0)
        finally:
            self._end_batch()

    def press_combo(self, spec: str) -> None:
        """'ctrl+v'-style combo: modifiers down, key tap, modifiers up."""
        tokens = [t.strip().lower() for t in spec.split("+") if t.strip()]
        if not tokens:
            raise EISError(f"empty key spec: {spec!r}")
        codes = []
        for t in tokens:
            if t not in TOKEN_KEY_CODES:
                raise EISError(f"no EIS keycode for {t!r} (spec {spec!r})")
            codes.append(TOKEN_KEY_CODES[t])
        mods, key = codes[:-1], codes[-1]
        self._begin_batch()
        try:
            for m in mods:
                self._send_key(m, True)
            self._send_key(key, True)
            self._send_key(key, False)
            for m in reversed(mods):
                self._send_key(m, False)
        finally:
            self._end_batch()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._connection_id is not None:
                self._send(self._connection_id, 1, b"")  # disconnect
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    # -- sending --------------------------------------------------------------

    def _send(self, obj_id: int, opcode: int, body: bytes) -> None:
        try:
            self._sock.sendall(self._wire.header(obj_id, opcode, body))
        except OSError as e:
            raise EISDisconnected(f"EIS socket error: {e}") from e

    def _send_key(self, code: int, pressed: bool) -> None:
        dev, kb = self._keyboard_ids()
        self._send(kb, 1,
                   self._wire.u32(code) + self._wire.u32(1 if pressed else 0))
        self._frame(dev)

    def _frame(self, device_id: int) -> None:
        ts = time.monotonic_ns() // 1000
        self._send(device_id, 3,
                   self._wire.u32(self._serial) + self._wire.u64(ts))

    def _begin_batch(self) -> None:
        dev, _kb = self._keyboard_ids()
        self._sequence += 1
        self._send(dev, 1, self._wire.u32(self._serial)
                   + self._wire.u32(self._sequence))

    def _end_batch(self) -> None:
        dev, _kb = self._keyboard_ids()
        self._send(dev, 2, self._wire.u32(self._serial))

    def _keyboard_ids(self) -> tuple[int, int]:
        if not self.keyboard:
            raise EISProtocolError("no keyboard device")
        return self.keyboard

    def _bind_seat_keyboard(self) -> None:
        if self._seat_bound or self._seat_id is None:
            return
        if self._legacy_wire:
            caps = self._legacy_seat_caps & _CAP_KEYBOARD_LEGACY \
                or _CAP_KEYBOARD_LEGACY
            body = self._wire.u32(caps)
        else:
            mask = 0
            for iface, m in self._seat_caps.items():
                if iface in ("ei_keyboard", "keyboard"):
                    mask |= m
            if not mask:
                mask = next(iter(self._seat_caps.values()), 1)
            body = self._wire.u64(mask)
        self._send(self._seat_id, 1, body)
        self._seat_bound = True

    def _resolve(self, ch: str) -> tuple[int, bool]:
        keymap = self._keymap or _US_KEYMAP
        if ch in keymap:
            return keymap[ch]
        for name in char_keysym_names(ch):
            if name in keymap:
                return keymap[name]
        raise EISUnsupportedChar(ch)

    # -- receiving ------------------------------------------------------------

    def _pump_until(self, predicate, deadline: float, what: str) -> None:
        while not predicate():
            if time.monotonic() >= deadline:
                raise EISTimeout(f"EIS: timed out waiting for {what}")
            self._pump_one(deadline)

    def _await(self, obj_id: int, opcode: int, what: str,
               deadline: float) -> tuple[int, bytes]:
        while True:
            if time.monotonic() >= deadline:
                raise EISTimeout(f"EIS: timed out waiting for {what}")
            got = self._pump_one(deadline, want=(obj_id, opcode))
            if got is not None:
                return got

    def _pump_one(self, deadline: float, want=None):
        msg = self._recv_message(deadline)
        obj_id, opcode, body = msg
        if want is not None and (obj_id, opcode) == want:
            return opcode, body
        self._dispatch(obj_id, opcode, body)
        return None

    def _recv_message(self, deadline: float):
        while True:
            if len(self._buf) >= _HDR_LEN:
                _obj, length, _op = self._wire.parse_header(self._buf)
                if length < _HDR_LEN:
                    raise EISProtocolError(f"nonsense EI length {length}")
                if len(self._buf) >= length:
                    msg = self._buf[:length]
                    self._buf = self._buf[length:]
                    obj_id, _length, opcode = self._wire.parse_header(msg)
                    return obj_id, opcode, msg[_HDR_LEN:]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EISTimeout("EIS: read budget expired")
            self._sock.settimeout(remaining)
            try:
                chunk, ancdata, _flags, _addr = self._sock.recvmsg(
                    65536, socket.CMSG_SPACE(4 * 4))
            except socket.timeout as e:
                raise EISTimeout("EIS: read budget expired") from e
            except OSError as e:
                raise EISDisconnected(f"EIS socket error: {e}") from e
            if not chunk:
                raise EISDisconnected("EIS socket closed by the server")
            # fds ride as ancillary data; they are matched to fd-arg
            # events in FIFO order at dispatch time (the wire has no
            # per-message pairing, same as libwayland)
            for cmsg_level, cmsg_type, cmsg_data in ancdata:
                if (cmsg_level == socket.SOL_SOCKET
                        and cmsg_type == socket.SCM_RIGHTS):
                    self._pending_fds.extend(
                        struct.unpack(f"{len(cmsg_data) // 4}i", cmsg_data))
            self._buf += chunk

    def _dispatch(self, obj_id: int, opcode: int, body: bytes) -> None:
        iface = self._objects.get(obj_id)
        if iface is None:
            return  # unknown object: tolerated (knowledge may lag)
        handler = getattr(self, f"_ev_{iface.removeprefix('ei_')}", None)
        if handler is not None:
            handler(obj_id, opcode, body)

    def _ev_handshake(self, _obj, opcode, body) -> None:
        if opcode == 1:  # interface_version(name, version)
            name, off = self._wire.read_string(body, 0)
            version, off = self._wire.read_u32(body, off)
            if name:
                self.server_versions[name] = version
        elif opcode == 2:  # connection(serial, new_id, version)
            serial, off = self._wire.read_u32(body, 0)
            new_id, off = self._wire.read_u64(body, off)
            self._objects[new_id] = "ei_connection"
            self._connection_id = new_id
            self._serial = serial

    def _ev_connection(self, _obj, opcode, body) -> None:
        if opcode == 0:  # disconnected(last_serial, reason, explanation)
            serial, off = self._wire.read_u32(body, 0)
            _reason, off = self._wire.read_u32(body, off)
            why, _off = self._wire.read_string(body, off)
            self._serial = serial
            raise EISDisconnected(f"EIS disconnected: {why or 'no reason'}")
        if opcode == 1:  # seat(new_id, version)
            new_id, _off = self._wire.read_u64(body, 0)
            self._objects[new_id] = "ei_seat"
            self._seat_id = new_id

    def _ev_seat(self, _obj, opcode, body) -> None:
        if opcode == 2:
            # capabilities(u32) on legacy vs capability(u64, string) on
            # 1.x — same opcode, different body length: the wire
            # generation is decided HERE, before bind is ever sent.
            if len(body) == 4:
                self._legacy_wire = True
                caps, _off = self._wire.read_u32(body, 0)
                self._legacy_seat_caps = caps
            else:
                self._legacy_wire = False
                mask, off = self._wire.read_u64(body, 0)
                iface, _off = self._wire.read_string(body, off)
                if iface:
                    self._seat_caps[iface] = mask
        elif opcode == 4:  # device(new_id, version)
            new_id, _off = self._wire.read_u64(body, 0)
            self._objects[new_id] = "ei_device"
            self.devices[new_id] = _Device(obj_id=new_id)

    def _ev_device(self, obj, opcode, body) -> None:
        dev = self.devices.get(obj)
        if dev is None:
            return
        legacy = bool(self._legacy_wire)
        if opcode == 1:  # name(string)
            dev.name, _off = self._wire.read_string(body, 0)
        elif opcode == (6 if legacy else 5):  # interface(new_id, name, ver)
            new_id, off = self._wire.read_u64(body, 0)
            iface, _off = self._wire.read_string(body, off)
            self._objects[new_id] = iface or ""
            if iface == "ei_keyboard" and dev.keyboard is None:
                dev.keyboard = _Keyboard(obj_id=new_id)
        elif opcode == (7 if legacy else 6):  # done()
            dev.done = True
        elif opcode == (8 if legacy else 7):  # resumed(serial)
            serial, _off = self._wire.read_u32(body, 0)
            self._serial = serial
            dev.resumed = True

    def _ev_keyboard(self, obj, opcode, body) -> None:
        dev = next((d for d in self.devices.values()
                    if d.keyboard is not None
                    and d.keyboard.obj_id == obj), None)
        if dev is None:
            return
        if opcode == 1:  # keymap(type, size, fd)
            keymap_type, off = self._wire.read_u32(body, 0)
            size, off = self._wire.read_u32(body, off)
            if keymap_type == 1 and size > 0 and self._pending_fds:
                fd = self._pending_fds.pop(0)
                data = self._read_fd(fd, size)
                symbols = parse_xkb_keymap(
                    data.decode("utf-8", errors="replace"))
                dev.keyboard.keymap_symbols = symbols
                self.log(f"EIS: server keymap parsed ({len(symbols)} symbols)")

    def _read_fd(self, fd: int, size: int) -> bytes:
        try:
            data = b""
            while len(data) < size:
                chunk = os.read(fd, min(65536, size - len(data)))
                if not chunk:
                    break
                data += chunk
            return data
        finally:
            os.close(fd)

    def _keyboard_ready(self) -> bool:
        for dev in self.devices.values():
            if dev.keyboard is not None and dev.done and dev.resumed:
                self.keyboard = (dev.obj_id, dev.keyboard.obj_id)
                return True
        return False
