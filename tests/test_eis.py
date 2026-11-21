"""EIS wire client (fluidvoice/eis.py) against a scripted fake EIS.

Both wire generations are exercised — legacy (libei 0.4/0.5 era, what
GNOME 46/47 link against) and v1 (libei >= 0.99) — including the
seat-capability event that decides the generation, the bind width, the
device-event opcode shift, the keymap fd (SCM_RIGHTS) and the typing
event stream (one frame per key edge, start/stop_emulating batching).
AF_UNIX socketpairs only; no compositor, no portal, no D-Bus.
"""
from __future__ import annotations

import os
import socket
import struct
import threading

import pytest

from fluidvoice import eis

E = "<" if os.sys.byteorder == "little" else ">"
CONN = 0xFF00000000000001
SEAT = 0xFF00000000000002
DEV = 0xFF00000000000003
KB = 0xFF00000000000004

KEYMAP = (b'xkb_keymap {\n'
          b' xkb_keycodes "evdev" { <AE01> = 10; <AD01> = 24; '
          b'<LFSH> = 42; };\n'
          b' xkb_symbols "basic" { key <AE01> { [ 1, exclam ] }; '
          b'key <AD01> { [ q, Q ] }; key <LFSH> { [ Shift_L ] }; };\n'
          b'};\n')


def msg(obj: int, op: int, body: bytes = b"") -> bytes:
    return struct.pack(E + "QII", obj, 16 + len(body), op) + body


def s(text: str) -> bytes:
    raw = text.encode() + b"\x00"
    pad = -len(raw) % 4
    return struct.pack(E + "I", len(raw)) + raw + b"\x00" * pad


def recv_exact(conn, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise EOFError("fake EIS closed")
        buf += chunk
    return buf


class FakeEIS(threading.Thread):
    """Scripted EIS implementation: handshake, seat+device burst, then
    it records every key request until the connection drains."""

    daemon = True

    def __init__(self, gen: str, keymap: bool = False):
        super().__init__()
        self.gen = gen          # "legacy" | "v1"
        self.keymap = keymap
        self.a, self.b = socket.socketpair()
        self.keys: list[tuple[int, int]] = []
        self.binds: list[bytes] = []
        self.requests: list[tuple[int, int]] = []
        self.batches = 0
        self.done = threading.Event()

    def _read(self) -> tuple[int, int, bytes]:
        obj, length, op = struct.unpack(E + "QII", recv_exact(self.a, 16))
        body = recv_exact(self.a, length - 16) if length > 16 else b""
        self.requests.append((obj, op))
        return obj, op, body

    def run(self) -> None:
        try:
            conn = self.a
            conn.sendall(msg(0, 0, struct.pack(E + "I", 1)))
            # handshake_version, context_type, name, 4x interface_version,
            # finish — in that order, then the connection event
            for obj, op in ((0, 0), (0, 2), (0, 3),
                            (0, 4), (0, 4), (0, 4), (0, 4), (0, 1)):
                got_obj, got_op, _ = self._read()
                assert (got_obj, got_op) == (obj, op), (got_obj, got_op)
            conn.sendall(msg(0, 1, s("ei_connection")
                             + struct.pack(E + "I", 1)))
            conn.sendall(msg(0, 2, struct.pack(E + "I", 42)
                             + struct.pack(E + "Q", CONN)
                             + struct.pack(E + "I", 1)))
            # seat burst
            conn.sendall(msg(CONN, 1, struct.pack(E + "QI", SEAT, 1)))
            conn.sendall(msg(SEAT, 1, s("seat0")))
            if self.gen == "legacy":
                conn.sendall(msg(SEAT, 2, struct.pack(E + "I", 4)))
            else:
                conn.sendall(msg(SEAT, 2, struct.pack(E + "Q", 4)
                                 + s("ei_keyboard")))
            conn.sendall(msg(SEAT, 3, b""))
            # bind arrives next; its width is the generation contract
            obj, op, body = self._read()
            assert (obj, op) == (SEAT, 1), (hex(obj), op)
            self.binds.append(body)
            # device burst
            conn.sendall(msg(SEAT, 4, struct.pack(E + "QI", DEV, 1)))
            conn.sendall(msg(DEV, 1, s("virtual keyboard")))
            if self.gen == "legacy":
                conn.sendall(msg(DEV, 2, struct.pack(E + "I", 4)))
            conn.sendall(msg(DEV, 3 if self.gen == "legacy" else 2,
                             struct.pack(E + "I", 1)))
            iface_op = 6 if self.gen == "legacy" else 5
            done_op = 7 if self.gen == "legacy" else 6
            resumed_op = 8 if self.gen == "legacy" else 7
            conn.sendall(msg(DEV, iface_op, struct.pack(E + "Q", KB)
                             + s("ei_keyboard") + struct.pack(E + "I", 1)))
            if self.keymap:
                fd = os.memfd_create("eis-test-keymap")
                os.write(fd, KEYMAP)
                os.lseek(fd, 0, os.SEEK_SET)
                conn.sendmsg(
                    [msg(KB, 1, struct.pack(E + "II", 1, len(KEYMAP)))],
                    [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                      struct.pack("i", fd))])
            conn.sendall(msg(DEV, done_op, b""))
            conn.sendall(msg(DEV, resumed_op, struct.pack(E + "I", 99)))
            # typing traffic until the client disconnects
            while True:
                obj, op, body = self._read()
                if obj == DEV and op == 2:
                    self.batches += 1
                elif obj == KB and op == 1:
                    key, state = struct.unpack(E + "II", body)
                    self.keys.append((key, state))
                if obj == CONN and op == 1:  # disconnect
                    break
        except (EOFError, OSError):
            pass
        finally:
            self.done.set()
            try:
                self.a.close()
            except OSError:
                pass

    def client(self) -> eis.EISConnection:
        return eis.EISConnection(self.b, timeout=3.0)


@pytest.fixture()
def fake_eis(request):
    gen, keymap = request.param
    server = FakeEIS(gen, keymap)
    server.start()
    yield server
    try:
        server.b.close()
    except OSError:
        pass
    server.done.wait(timeout=3)


GENS = [("legacy", False), ("v1", False), ("legacy", True), ("v1", True)]


class TestHandshake:
    @pytest.mark.parametrize("fake_eis", GENS, indirect=True)
    def test_connect_binds_keyboard(self, fake_eis):
        client = fake_eis.client()
        client.connect()
        try:
            assert client.keyboard == (DEV, KB)
            assert client.keyboard_device().resumed
            assert fake_eis.binds, "seat.bind was not sent"
            width = 4 if fake_eis.gen == "legacy" else 8
            assert len(fake_eis.binds[0]) == width
            assert fake_eis.requests.count((0, 4)) == 4  # interface_version
        finally:
            client.close()

    @pytest.mark.parametrize("fake_eis", GENS, indirect=True)
    def test_keymap_vs_us_fallback(self, fake_eis):
        client = fake_eis.client()
        client.connect()
        try:
            if fake_eis.keymap:
                # server keymap wins: q->24, Q->shift+24, 1->10
                assert client._resolve("q") == (24, False)
                assert client._resolve("Q") == (24, True)
                assert client._resolve("1") == (10, False)
                assert client._resolve("exclam") == (10, True)
            else:
                assert client._resolve("q") == (16, False)
                assert client._resolve("H") == (35, True)  # shift + KEY_H
                assert client._resolve("!") == (2, True)
        finally:
            client.close()


class TestTyping:
    @pytest.mark.parametrize("fake_eis", GENS, indirect=True)
    def test_type_text_events(self, fake_eis):
        client = fake_eis.client()
        client.connect()
        text = "q1" if fake_eis.keymap else "Hi!"
        client.type_text(text, delay_ms=0)
        client.close()
        assert fake_eis.done.wait(timeout=3)
        keys = fake_eis.keys
        if fake_eis.keymap:
            assert (24, 1) in keys and (24, 0) in keys   # q
            assert (10, 1) in keys and (10, 0) in keys   # 1
        else:
            # H = shift+h(35), i(23), ! = shift+2
            assert (42, 1) in keys and (35, 1) in keys
            assert (23, 1) in keys
            assert (2, 1) in keys
        # one start/stop_emulating batch around the whole text (the
        # remote-desktop transaction boundary, not per character)
        assert fake_eis.batches == 1
        assert keys.count((42, 1)) == keys.count((42, 0))  # shift balanced

    @pytest.mark.parametrize("fake_eis", GENS, indirect=True)
    def test_ctrl_v_combo(self, fake_eis):
        client = fake_eis.client()
        client.connect()
        client.press_combo("ctrl+v")
        client.close()
        assert fake_eis.done.wait(timeout=3)
        keys = fake_eis.keys
        assert (29, 1) in keys and (29, 0) in keys
        assert (47, 1) in keys and (47, 0) in keys
        # modifiers around the tap: ctrl down first, up last
        assert keys.index((29, 1)) < keys.index((47, 1))
        assert keys.index((47, 0)) < keys.index((29, 0))

    @pytest.mark.parametrize("fake_eis", [("v1", False)], indirect=True)
    def test_out_of_layout_raises_unsupported(self, fake_eis):
        client = fake_eis.client()
        client.connect()
        try:
            with pytest.raises(eis.EISUnsupportedChar) as ei:
                client.type_text("héllo 你好", delay_ms=0)
            assert set(ei.value.chars) == {"é", "你", "好"}
            assert fake_eis.keys == []  # nothing was sent
        finally:
            client.close()

    @pytest.mark.parametrize("fake_eis", [("v1", True)], indirect=True)
    def test_unicode_keysym_names_resolve(self, fake_eis):
        """U00e9-style keysym naming covers Latin-1 when the server
        keymap spells it that way (xkbcomp emits U00e9 for é)."""
        client = fake_eis.client()
        client.connect()
        try:
            assert eis.char_keysym_names("é") == ["U00e9", "U00E9"]
            assert client.unsupported_chars("q") == ""
        finally:
            client.close()


class TestXkbParsing:
    def test_parse_levels_one_and_two(self):
        out = eis.parse_xkb_keymap(KEYMAP.decode())
        assert out["1"] == (10, False)
        assert out["exclam"] == (10, True)
        assert out["q"] == (24, False)
        assert out["Q"] == (24, True)
        assert out["Shift_L"] == (42, False)

    def test_level_one_wins_over_two(self):
        text = ('xkb_keymap {\n'
                ' xkb_keycodes "evdev" { <AD01> = 24; <AE01> = 10; };\n'
                ' xkb_symbols "basic" {\n'
                '  key <AD01> { [ q, Q ] };\n'
                '  key <AE01> { [ exclam, 1 ] };\n'
                ' };\n'
                '};\n')
        out = eis.parse_xkb_keymap(text)
        assert out["q"] == (24, False)
        assert out["Q"] == (24, True)
        assert out["1"] == (10, True)   # its own level-2 slot
        assert out["exclam"] == (10, False)

    def test_garbage_keymap_is_empty(self):
        assert eis.parse_xkb_keymap("not a keymap") == {}

    def test_type_declaration_before_levels(self):
        text = ('xkb_keymap {\n'
                ' xkb_keycodes "evdev" { <AE01> = 10; };\n'
                ' xkb_symbols "basic" {\n'
                '  key <AE01> { type[Group1]="TWO_LEVEL",'
                ' [ 1, exclam ] };\n'
                ' };\n'
                '};\n')
        out = eis.parse_xkb_keymap(text)
        assert out["1"] == (10, False) and out["exclam"] == (10, True)


class TestUsFallback:
    def test_ascii_printable_complete(self):
        printable = (chr(c) for c in range(0x20, 0x7F))
        missing = [ch for ch in printable if ch not in eis._US_KEYMAP]
        assert missing == []

    @pytest.mark.parametrize("ch,code,shifted", [
        ("a", 30, False), ("A", 30, True), ("!", 2, True),
        ("~", 41, True), ("Q", 16, True), ("?", 53, True),
        (" ", 57, False), ("\n", 28, False),
    ])
    def test_selected_mappings(self, ch, code, shifted):
        assert eis._US_KEYMAP[ch] == (code, shifted)


class TestErrors:
    def test_empty_combo_spec(self):
        a, b = socket.socketpair()
        client = eis.EISConnection(b, timeout=1.0)
        try:
            with pytest.raises(eis.EISError):
                client.press_combo("+")
        finally:
            client.close()
            a.close()

    def test_unknown_combo_token(self):
        a, b = socket.socketpair()
        client = eis.EISConnection(b, timeout=1.0)
        try:
            with pytest.raises(eis.EISError, match="no EIS keycode"):
                client.press_combo("ctrl+_volup")
        finally:
            client.close()
            a.close()

    def test_timeout_when_server_silent(self):
        a, b = socket.socketpair()
        client = eis.EISConnection(b, timeout=0.2)
        try:
            with pytest.raises(eis.EISTimeout):
                client.connect()
        finally:
            client.close()
            a.close()

    def test_disconnect_event_surfaces(self):
        a, b = socket.socketpair()
        client = eis.EISConnection(b, timeout=1.0)
        try:
            a.sendall(msg(0, 0, struct.pack(E + "I", 1)))
            a.close()  # server hangs up mid-handshake
            with pytest.raises(eis.EISDisconnected):
                client.connect()
        finally:
            client.close()
