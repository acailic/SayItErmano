"""Minimal pure-Python EIS client: keystroke injection over libei/EIS.

This is the insertion half of ADR-0006: inside the Flatpak sandbox the
RemoteDesktop portal's ``ConnectToEIS`` hands us a Unix socket speaking
the Emulated Input Server protocol (libei's "ei" protocol, protocol.xml
in libinput/libei). The protocol is Wayland-like: a 16-byte header
(object-id u64, length u32 including the header, opcode u32) followed
by 4-byte-aligned arguments, in the EIS implementation's native byte
order. Only the sender-side requests this app needs are implemented:

    handshake  ei_handshake.handshake_version / context_type(sender) /
               name / interface_version(...) / finish
    bind       ei_seat.bind(mask of one announced "ei_keyboard" capability)
    inject     ei_device.start_emulating(last_serial, sequence) ->
               ei_keyboard.key(code, state) pairs framed by
               ei_device.frame(last_serial, timestamp us) ->
               ei_device.stop_emulating(last_serial)

Honest limits (documented in the ADR, kept visible here):
- keycodes are linux/input-event-codes.h numbers interpreted through
  the compositor's keymap; only ASCII is encodable (see CHAR_TO_KEYS).
  Out-of-layout Unicode CANNOT be typed this way - callers fall back to
  clipboard + EIS-sent Ctrl+V. (``ei_text`` UTF-8, libei 1.6, is the
  tracked follow-up.)
- The EIS connection never gates on a desktop-environment name; the
  caller probes the portal at runtime (eis_insertion.probe).

Everything is synchronous over a blocking socket so tests can drive it
with socketpair() fakes; the portal/session glue in eis_insertion.py
owns the threading.
"""
from __future__ import annotations

import os
import socket
import struct
import sys
import time
from typing import BinaryIO

# Native byte order of the EIS implementation == ours in practice
# (x86/ARM are little-endian); the protocol mandates native order.
_ENDIAN = "<" if sys.byteorder == "little" else ">"

# -- header ----------------------------------------------------------------
# | object-id u64 | length u32 (incl. header) | opcode u32 |
HEADER = struct.Struct(_ENDIAN + "QII")
HEADER_LEN = HEADER.size  # 16

HANDSHAKE_OBJ = 0  # "The object-id 0 is reserved for ei_handshake"

# -- interface opcode tables (proto/protocol.xml order) ---------------------
# ei_handshake requests
HS_HANDSHAKE_VERSION = 0  # (uint32 version)
HS_FINISH = 1
HS_CONTEXT_TYPE = 2        # (uint32 1=receiver 2=sender)
HS_NAME = 3                # (string)
HS_INTERFACE_VERSION = 4   # (string name, uint32 version)
# ei_handshake events
HS_EV_HANDSHAKE_VERSION = 0
HS_EV_INTERFACE_VERSION = 1
HS_EV_CONNECTION = 2       # (uint32 serial, new_id, uint32 version)

# ei_connection events
CONN_EV_DISCONNECTED = 0   # (uint32 last_serial, uint32 reason, string)
CONN_EV_SEAT = 1           # (new_id, uint32 version)
CONN_EV_INVALID_OBJECT = 2
CONN_EV_PING = 3

# ei_seat requests
SEAT_BIND = 1              # (uint64 capabilities)
# ei_seat events
SEAT_EV_DESTROYED = 0
SEAT_EV_NAME = 1
SEAT_EV_CAPABILITY = 2     # (uint64 mask, string interface)
SEAT_EV_DONE = 3
SEAT_EV_DEVICE = 4         # (new_id, uint32 version)

# ei_device requests
DEV_START_EMULATING = 1    # (uint32 last_serial, uint32 sequence)
DEV_STOP_EMULATING = 2     # (uint32 last_serial)
DEV_FRAME = 3              # (uint32 last_serial, uint64 timestamp_us)
# ei_device events
DEV_EV_DESTROYED = 0
DEV_EV_NAME = 1
DEV_EV_DEVICE_TYPE = 2
DEV_EV_DIMENSIONS = 3
DEV_EV_REGION = 4
DEV_EV_INTERFACE = 5       # (new_id, string interface_name, uint32 version)
DEV_EV_DONE = 6
DEV_EV_RESUMED = 7         # (uint32 serial)
DEV_EV_PAUSED = 8          # (uint32 serial)

# ei_keyboard requests
KBD_KEY = 1                # (uint32 key, uint32 state 0=release 1=press)

KEY_RELEASE = 0
KEY_PRESS = 1

CONTEXT_TYPE_SENDER = 2

# Interface versions we announce in the handshake (all are v1 for the
# requests used here).
_ANNOUNCED_INTERFACES = (
    ("ei_connection", 1),
    ("ei_seat", 1),
    ("ei_device", 1),
    ("ei_keyboard", 1),
)


class EisError(RuntimeError):
    """EIS protocol/transport failure."""


class EisEncodeError(EisError):
    """Text cannot be typed with EIS keycodes (out-of-layout Unicode).

    Callers must fall back to the clipboard + Ctrl+V path; the text
    itself rides on the exception for the fallback."""


# -- linux/input-event-codes.h keycodes (the single mapping for EIS) --------
KEY_ESC, KEY_1, KEY_2, KEY_3, KEY_4 = 1, 2, 3, 4, 5
KEY_5, KEY_6, KEY_7, KEY_8, KEY_9, KEY_0 = 6, 7, 8, 9, 10, 11
KEY_MINUS, KEY_EQUAL, KEY_BACKSPACE, KEY_TAB = 12, 13, 14, 15
KEY_Q, KEY_W, KEY_E, KEY_R, KEY_T, KEY_Y, KEY_U, KEY_I, KEY_O, KEY_P = \
    range(16, 26)
KEY_LEFTBRACE, KEY_RIGHTBRACE, KEY_ENTER, KEY_LEFTCTRL = 26, 27, 28, 29
KEY_A, KEY_S, KEY_D, KEY_F, KEY_G, KEY_H, KEY_J, KEY_K, KEY_L = \
    range(30, 39)
KEY_SEMICOLON, KEY_APOSTROPHE, KEY_GRAVE, KEY_LEFTSHIFT, KEY_BACKSLASH = \
    39, 40, 41, 42, 43
KEY_Z, KEY_X, KEY_C, KEY_V, KEY_B, KEY_N, KEY_M = 44, 45, 46, 47, 48, 49, 50
KEY_COMMA, KEY_DOT, KEY_SLASH, KEY_RIGHTSHIFT = 51, 52, 53, 54
KEY_LEFTALT, KEY_SPACE = 56, 57

# Keycode table shared with the ydotool path (insertion.LINUX_KEY_CODES
# documents that table; EIS speaks the same linux keycodes).
KEY_CODES = {
    "ctrl": KEY_LEFTCTRL, "shift": KEY_LEFTSHIFT, "alt": KEY_LEFTALT,
    "super": 125, "enter": KEY_ENTER, "return": KEY_ENTER,
    "esc": KEY_ESC, "escape": KEY_ESC, "tab": KEY_TAB, "space": KEY_SPACE,
    "backspace": KEY_BACKSPACE,
    "a": KEY_A, "c": KEY_C, "v": KEY_V, "x": KEY_X,
}

# printable ASCII -> (keycode, shift?) - enough for every dictation the
# recognizer can emit through the US-layout base; everything else raises
# EisEncodeError and degrades to the clipboard paste path.
_LETTERS = {chr(ord("a") + i): c for i, c in enumerate(
    # a b c d e f g h i j  k  l  m  n  o  p  q  r  s  t  u  v  w  x  y  z
    [30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50, 49, 24, 25,
     16, 19, 31, 20, 22, 47, 17, 45, 21, 44])}
_DIGITS = {str(d): 2 + d for d in range(10)}  # KEY_1..KEY_0 = 2..11
_PUNCT = {
    " ": KEY_SPACE, "\t": KEY_TAB, "\n": KEY_ENTER,
    "-": KEY_MINUS, "=": KEY_EQUAL, "[": KEY_LEFTBRACE, "]": KEY_RIGHTBRACE,
    "\\": KEY_BACKSLASH, ";": KEY_SEMICOLON, "'": KEY_APOSTROPHE,
    "`": KEY_GRAVE, ",": KEY_COMMA, ".": KEY_DOT, "/": KEY_SLASH,
}
_SHIFT_PUNCT = {  # shifted row keys: <shift, keycode>
    "!": KEY_1, "@": KEY_3, "#": KEY_4, "$": KEY_5, "%": KEY_6,
    "^": KEY_7, "&": KEY_7, "*": KEY_8, "(": KEY_9, ")": KEY_0,
    "_": KEY_MINUS, "+": KEY_EQUAL, "{": KEY_LEFTBRACE, "}": KEY_RIGHTBRACE,
    "|": KEY_BACKSLASH, ":": KEY_SEMICOLON, '"': KEY_APOSTROPHE,
    "~": KEY_GRAVE, "<": KEY_COMMA, ">": KEY_DOT, "?": KEY_SLASH,
}
CHAR_TO_KEYS: dict[str, tuple[int, bool]] = {}
for _c, _k in _LETTERS.items():
    CHAR_TO_KEYS[_c] = (_k, False)
    CHAR_TO_KEYS[_c.upper()] = (_k, True)
for _c, _k in {**_DIGITS, **_PUNCT}.items():
    CHAR_TO_KEYS[_c] = (_k, False)
for _c, _k in _SHIFT_PUNCT.items():
    CHAR_TO_KEYS[_c] = (_k, True)

del _c, _k, _LETTERS, _DIGITS, _PUNCT, _SHIFT_PUNCT


def encodable(text: str) -> bool:
    """True when every char of text can be typed via EIS keycodes."""
    return all(c in CHAR_TO_KEYS for c in text)


def parse_key_spec(spec: str) -> list[tuple[int, bool]]:
    """'ctrl+shift+v' -> [(29, True), (42, True), (47, False)] (mods held,
    key tapped). Unknown tokens raise EisError instead of mistyping."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise EisError(f"empty key spec: {spec!r}")
    out: list[tuple[int, bool]] = []
    for p in parts:
        code = KEY_CODES.get(p)
        if code is None:
            raise EisError(f"no key code for {p!r} (spec {spec!r})")
        out.append((code, p in ("ctrl", "shift", "alt", "super")))
    return out


# -- wire helpers ------------------------------------------------------------

def encode_string(s: str) -> bytes:
    """EIS string: u32 length INCLUDING the NUL, then NUL-terminated UTF-8
    zero-padded to a 4-byte multiple."""
    raw = s.encode() + b"\0"
    pad = -len(raw) % 4
    return struct.pack(_ENDIAN + "I", len(raw)) + raw + b"\0" * pad


def build_message(obj: int, opcode: int, args: bytes = b"") -> bytes:
    length = HEADER_LEN + len(args)
    return HEADER.pack(obj, length, opcode) + args


def parse_header(data: bytes) -> tuple[int, int, int]:
    """(object-id, length, opcode) from a 16-byte header."""
    obj, length, opcode = HEADER.unpack(data)
    if length < HEADER_LEN:
        raise EisError(f"impossible EIS message length {length}")
    return obj, length, opcode


class _Reader:
    """Buffered message reader over a socket/file, draining ancillary fds
    (the server may send an ei_keyboard.keymap fd; we accept and close
    it - the compositor's keymap governs keycode interpretation and we
    only send ASCII codes)."""

    def __init__(self, sock, deadline: float):
        self._sock = sock
        self._deadline = deadline
        self._buf = b""

    def _fill(self, need: int) -> None:
        while len(self._buf) < need:
            self._check_deadline()
            try:
                if isinstance(self._sock, socket.socket):
                    self._sock.settimeout(max(0.05, self._deadline
                                              - time.monotonic()))
                    chunk, ancdata, _flags, _addr = self._sock.recvmsg(65536)
                    for level, ctype, data in ancdata:
                        if (level == socket.SOL_SOCKET
                                and ctype == socket.SCM_RIGHTS
                                and len(data) % 4 == 0):
                            for fd in struct.unpack("i" * (len(data) // 4),
                                                    data):
                                os.close(fd)  # keymap fds: not parsed
                else:  # BinaryIO fake (tests)
                    chunk = self._sock.read(65536)
            except socket.timeout:
                raise EisError("EIS read timed out") from None
            except OSError as e:
                raise EisError(f"EIS transport error: {e}") from e
            if not chunk:
                raise EisError("EIS connection closed")
            self._buf += chunk

    def _check_deadline(self) -> None:
        if time.monotonic() >= self._deadline:
            raise EisError("EIS wait deadline exceeded")

    def message(self) -> tuple[int, int, bytes]:
        """Next (object-id, opcode, args-bytes)."""
        self._fill(HEADER_LEN)
        obj, length, opcode = parse_header(self._buf[:HEADER_LEN])
        self._fill(length)
        args = self._buf[HEADER_LEN:length]
        self._buf = self._buf[length:]
        return obj, opcode, args


def _take(fmt: str, args: memoryview | bytes, off: int) -> tuple:
    size = struct.calcsize(_ENDIAN + fmt)
    if off + size > len(args):
        raise EisError("truncated EIS message")
    return struct.unpack_from(_ENDIAN + fmt, args, off), off + size


def take_uint32(args, off: int) -> tuple[int, int]:
    (v,), off = _take("I", args, off)
    return v, off


def take_uint64(args, off: int) -> tuple[int, int]:
    (v,), off = _take("Q", args, off)
    return v, off


def take_string(args, off: int) -> tuple[str, int]:
    (n,), off = _take("I", args, off)
    if n == 0:
        return "", off
    padded = -n % 4
    raw = bytes(args[off:off + n + padded])
    if len(raw) < n:
        raise EisError("truncated EIS string")
    off += n + padded
    return raw[:n - 1].decode(errors="replace"), off


class EisConnection:
    """One EIS sender connection over an already-connected socket.

    The lifecycle mirrors the portal session that owns the socket:
    connect() (handshake) -> bind_keyboard() -> type_text()/key events
    -> close(). All state lives on the instance; nothing is global.
    """

    def __init__(self, sock, *, client_name: str = "SayItErmano"):
        self._sock = sock
        self._client_name = client_name
        self._writer: BinaryIO = sock.makefile("wb") \
            if hasattr(sock, "makefile") else sock  # type: ignore[assignment]
        # ONE persistent reader: portal servers burst seat/device events
        # in the same TCP segment as the connection event, so per-call
        # readers would drop buffered bytes on the floor.
        self._reader = _Reader(sock, time.monotonic() + 10.0)
        self.connection_id: int | None = None
        self.seat_id: int | None = None
        self.device_id: int | None = None
        self.keyboard_id: int | None = None
        self.keyboard_mask: int | None = None
        self.last_serial = 0
        self._sequence = 0
        self._emulating = False

    # -- transport ----------------------------------------------------------

    def _send(self, obj: int, opcode: int, args: bytes = b"") -> None:
        try:
            self._writer.write(build_message(obj, opcode, args))
            self._writer.flush()
        except (OSError, ValueError) as e:
            raise EisError(f"EIS send failed: {e}") from e

    def _arm(self, timeout_s: float) -> _Reader:
        self._reader._deadline = time.monotonic() + timeout_s
        return self._reader

    # -- setup ----------------------------------------------------------------

    def connect(self, timeout_s: float = 10.0) -> None:
        """Run the ei_handshake dance up to (and including) the
        ei_handshake.connection event."""
        reader = self._arm(timeout_s)
        # 1) server speaks first: ei_handshake.handshake_version(N)
        obj, opcode, args = reader.message()
        if (obj, opcode) != (HANDSHAKE_OBJ, HS_EV_HANDSHAKE_VERSION):
            raise EisError(f"expected handshake_version event, got "
                           f"(obj={obj}, opcode={opcode})")
        server_version, _ = take_uint32(args, 0)
        # 2) our requests, in the documented order
        self._send(HANDSHAKE_OBJ, HS_HANDSHAKE_VERSION,
                   struct.pack(_ENDIAN + "I", min(1, server_version or 1)))
        self._send(HANDSHAKE_OBJ, HS_CONTEXT_TYPE,
                   struct.pack(_ENDIAN + "I", CONTEXT_TYPE_SENDER))
        self._send(HANDSHAKE_OBJ, HS_NAME, encode_string(self._client_name))
        for name, version in _ANNOUNCED_INTERFACES:
            self._send(HANDSHAKE_OBJ, HS_INTERFACE_VERSION,
                       encode_string(name) + struct.pack(_ENDIAN + "I",
                                                         version))
        self._send(HANDSHAKE_OBJ, HS_FINISH)
        # 3) wait for the connection object
        while True:
            obj, opcode, args = reader.message()
            if obj != HANDSHAKE_OBJ:
                continue  # stray early event (server burst) - keep waiting
            if opcode == HS_EV_CONNECTION:
                serial, off = take_uint32(args, 0)
                self.last_serial = serial
                self.connection_id, off = take_uint64(args, off)
                return
            if opcode == HS_EV_INTERFACE_VERSION:
                continue  # announcements

    def bind_keyboard(self, timeout_s: float = 10.0) -> None:
        """Bind the keyboard capability on the announced seat and wait for
        a resumed keyboard device."""
        if self.connection_id is None:
            raise EisError("not connected")
        reader = self._arm(timeout_s)
        while self.seat_id is None or self.keyboard_mask is None:
            obj, opcode, args = reader.message()
            if (obj, opcode) == (self.connection_id, CONN_EV_SEAT):
                self.seat_id, _ = take_uint64(args, 0)
                continue
            if (obj, opcode) == (self.connection_id, CONN_EV_DISCONNECTED):
                reason, off = take_uint32(args, 4)
                explanation, _ = take_string(args, off + 4)
                raise EisError(f"EIS disconnected during setup "
                               f"(reason {reason}: {explanation})")
            if self.seat_id is not None and obj == self.seat_id:
                if opcode == SEAT_EV_CAPABILITY:
                    mask, off = take_uint64(args, 0)
                    iface, _ = take_string(args, off)
                    if iface == "ei_keyboard":
                        self.keyboard_mask = mask
                # name/destroyed/done events carry no new state here
        self._send(self.seat_id, SEAT_BIND,
                   struct.pack(_ENDIAN + "Q", self.keyboard_mask))
        # device burst: wait until a device with an ei_keyboard interface
        # is resumed (a newly advertised device starts paused)
        current_device: int | None = None
        current_keyboard: int | None = None
        device_resumed: set[int] = set()
        while self.keyboard_id is None:
            obj, opcode, args = reader.message()
            if obj == self.connection_id and opcode == CONN_EV_DISCONNECTED:
                reason, off = take_uint32(args, 4)
                explanation, _ = take_string(args, off + 4)
                raise EisError(f"EIS disconnected after bind "
                               f"(reason {reason}: {explanation})")
            if obj == self.seat_id and opcode == SEAT_EV_DEVICE:
                current_device, _ = take_uint64(args, 0)
                current_keyboard = None
                continue
            if current_device is not None and obj == current_device:
                if opcode == DEV_EV_INTERFACE:
                    kbd_id, off = take_uint64(args, 0)
                    iface, off = take_string(args, off)
                    if iface == "ei_keyboard":
                        current_keyboard = kbd_id
                elif opcode == DEV_EV_RESUMED:
                    serial, _ = take_uint32(args, 0)
                    self.last_serial = serial
                    device_resumed.add(current_device)
                elif opcode == DEV_EV_PAUSED:
                    serial, _ = take_uint32(args, 0)
                    self.last_serial = serial
                    device_resumed.discard(current_device)
                if (current_keyboard is not None
                        and current_device in device_resumed):
                    self.device_id = current_device
                    self.keyboard_id = current_keyboard
        # drain anything already readable so later sends start clean
        self._drain_pending()

    def _drain_pending(self) -> None:
        """Non-blocking drain of buffered events (serial tracking)."""
        try:
            if isinstance(self._sock, socket.socket):
                self._sock.setblocking(False)
            while True:
                try:
                    obj, opcode, args = _Reader(
                        self._sock, time.monotonic()).message()
                except (EisError, BlockingIOError, OSError):
                    return
                self._track(obj, opcode, args)
        finally:
            if isinstance(self._sock, socket.socket):
                self._sock.setblocking(True)

    def _track(self, obj: int, opcode: int, args: bytes) -> None:
        """Keep last_serial fresh from any event carrying one."""
        if obj == self.device_id and opcode == DEV_EV_RESUMED:
            self.last_serial, _ = take_uint32(args, 0)
        if obj == self.device_id and opcode == DEV_EV_PAUSED:
            self.last_serial, _ = take_uint32(args, 0)

    # -- injection -------------------------------------------------------------

    def _require_ready(self) -> None:
        if self.keyboard_id is None or self.device_id is None:
            raise EisError("EIS keyboard not bound (connect+bind first)")

    def _key(self, code: int, state: int) -> None:
        self._send(self.keyboard_id, KBD_KEY,
                   struct.pack(_ENDIAN + "II", code, state))

    def _frame(self) -> None:
        ts_us = int(time.monotonic() * 1_000_000)
        self._send(self.device_id, DEV_FRAME,
                   struct.pack(_ENDIAN + "IQ", self.last_serial, ts_us))

    def start_emulating(self) -> None:
        """Begin one emulation transaction (sequence increments)."""
        self._require_ready()
        if self._emulating:
            return
        self._sequence += 1
        self._send(self.device_id, DEV_START_EMULATING,
                   struct.pack(_ENDIAN + "II", self.last_serial,
                               self._sequence))
        self._emulating = True

    def stop_emulating(self) -> None:
        if not self._emulating:
            return
        self._send(self.device_id, DEV_STOP_EMULATING,
                   struct.pack(_ENDIAN + "I", self.last_serial))
        self._emulating = False

    def type_keys(self, strokes: list[list[tuple[int, int]]],
                  delay_ms: int = 0, *, sleep=time.sleep) -> None:
        """Send pre-computed strokes: a list of frames, each a list of
        (keycode, state) pairs. A key press and its release NEVER share a
        frame (the protocol defines that pair as a logical noop)."""
        self.start_emulating()
        try:
            for i, frame in enumerate(strokes):
                if i and delay_ms > 0:
                    sleep(delay_ms / 1000.0)
                for code, state in frame:
                    self._key(code, state)
                self._frame()
        finally:
            self.stop_emulating()

    def type_text(self, text: str, delay_ms: int = 0, *,
                  sleep=time.sleep) -> None:
        """Type ASCII text: per char one press frame, one release frame
        (shift wrapped around the key when needed)."""
        if not encodable(text):
            bad = next(c for c in text if c not in CHAR_TO_KEYS)
            raise EisEncodeError(
                f"character {bad!r} is not typeable via EIS keycodes "
                "(out-of-layout Unicode needs the clipboard fallback)")
        strokes: list[list[tuple[int, int]]] = []
        for ch in text:
            code, shifted = CHAR_TO_KEYS[ch]
            strokes.append([(KEY_LEFTSHIFT, KEY_PRESS), (code, KEY_PRESS)]
                           if shifted else [(code, KEY_PRESS)])
            strokes.append([(code, KEY_RELEASE), (KEY_LEFTSHIFT, KEY_RELEASE)]
                           if shifted else [(code, KEY_RELEASE)])
        self.type_keys(strokes, delay_ms, sleep=sleep)

    def tap_combo(self, keys: list[tuple[int, bool]], *,
                  hold_s: float = 0.01, sleep=time.sleep) -> None:
        """Press a combo ('ctrl+v'-style parse result): mods down + key
        down in one frame, key up + mods up in the next."""
        self._require_ready()
        mods = [c for c, is_mod in keys if is_mod]
        plain = [c for c, is_mod in keys if not is_mod]
        self.start_emulating()
        try:
            self._send_keys_frame([(m, KEY_PRESS) for m in mods]
                                  + [(p, KEY_PRESS) for p in plain])
            sleep(hold_s)
            self._send_keys_frame([(p, KEY_RELEASE) for p in plain]
                                  + [(m, KEY_RELEASE) for m in reversed(mods)])
        finally:
            self.stop_emulating()

    def _send_keys_frame(self, pairs: list[tuple[int, int]]) -> None:
        for code, state in pairs:
            self._key(code, state)
        self._frame()

    # -- teardown ---------------------------------------------------------------

    def close(self) -> None:
        """Best-effort polite disconnect; never raises."""
        try:
            if self.connection_id is not None:
                self._send(self.connection_id, 1)  # ei_connection.disconnect
        except EisError:
            pass
        try:
            self._writer.close()  # type: ignore[attr-defined]
        except Exception:  # noqa: S110 — teardown/cleanup must not raise
            pass
        try:
            self._sock.close()
        except OSError:  # noqa: S110 — teardown/cleanup must not raise
            pass  # already closed under us
