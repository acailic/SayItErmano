"""Runtime input probes (fluidvoice/input_probes.py): the Wayland
registry scan that gates wtype on zwp_virtual_keyboard_manager_v1.

A fake compositor socket (AF_UNIX) answers get_registry with a
scripted global list — the probe must find the protocol, miss it, and
report None (indeterminate) when it cannot even connect. Pure parser
tests cover the wire layout.
"""
from __future__ import annotations

import socket
import struct
import threading

import pytest

from fluidvoice import input_probes


def wl_msg(obj: int, opcode: int, body: bytes = b"") -> bytes:
    return struct.pack("<II", obj, (opcode << 16) | (8 + len(body))) + body


def wl_str(text: str) -> bytes:
    raw = text.encode() + b"\x00"
    return struct.pack("<I", len(raw)) + raw + b"\x00" * (-len(raw) % 4)


def global_msg(name: int, interface: str, version: int) -> bytes:
    return wl_msg(2, 0, struct.pack("<I", name) + wl_str(interface)
                  + struct.pack("<I", version))


class FakeCompositor(threading.Thread):
    daemon = True

    def __init__(self, interfaces, accept=True):
        super().__init__()
        self.interfaces = interfaces   # list[(iface, version)]
        self.accept = accept
        self.path = None
        self._srv = None
        self.done = threading.Event()

    def run(self):
        try:
            self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._srv.bind(self.path)
            self._srv.listen(1)
            self.ready.set()
            if not self.accept:
                self.done.set()
                return
            conn, _ = self._srv.accept()
            req = conn.recv(64)
            assert len(req) == 24  # get_registry(2) + sync(3)
            for i, (iface, version) in enumerate(self.interfaces):
                conn.sendall(global_msg(i + 1, iface, version))
            conn.sendall(wl_msg(3, 0, struct.pack("<I", 0)))  # callback done
            conn.close()
        except OSError:
            pass
        finally:
            self.done.set()

    def stop(self):
        self.done.wait(timeout=2)
        try:
            self._srv.close()
        except OSError:
            pass


@pytest.fixture()
def compositor_factory(tmp_path):
    made = []

    def make(interfaces, accept=True):
        comp = FakeCompositor(interfaces, accept=accept)
        comp.path = str(tmp_path / f"wl-{len(made)}")
        comp.ready = threading.Event()
        made.append(comp)
        comp.start()
        comp.ready.wait(timeout=2)
        return comp

    yield make
    for comp in made:
        comp.stop()


class TestRegistryProbe:
    def test_finds_virtual_keyboard(self, compositor_factory):
        comp = compositor_factory([
            ("wl_compositor", 5),
            ("zwp_virtual_keyboard_manager_v1", 1),
            ("wl_seat", 8),
        ])
        assert input_probes.probe_virtual_keyboard(comp.path) is True

    def test_missing_protocol_reports_false(self, compositor_factory):
        comp = compositor_factory([("wl_compositor", 5)])
        assert input_probes.probe_virtual_keyboard(comp.path) is False

    def test_unreachable_socket_is_indeterminate(self, tmp_path):
        missing = str(tmp_path / "nope")
        assert input_probes.probe_virtual_keyboard(missing) is None

    def test_none_without_wayland_display(self, monkeypatch):
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        assert input_probes.probe_virtual_keyboard() is None

    def test_absolute_path_display(self, monkeypatch, tmp_path,
                                   compositor_factory):
        comp = compositor_factory(
            [("zwp_virtual_keyboard_manager_v1", 1)])
        monkeypatch.setenv("WAYLAND_DISPLAY", comp.path)
        assert input_probes.probe_virtual_keyboard() is True


class TestParser:
    def test_globals_parsed_with_versions(self):
        data = (global_msg(1, "wl_compositor", 5)
                + global_msg(2, "wl_seat", 8)
                + global_msg(3, "zwp_virtual_keyboard_manager_v1", 1)
                + wl_msg(3, 0, struct.pack("<I", 0)))
        out = input_probes.parse_wl_globals(data)
        assert out["wl_compositor"] == 5
        assert out["zwp_virtual_keyboard_manager_v1"] == 1

    def test_max_version_wins_on_duplicates(self):
        data = (global_msg(1, "wl_seat", 3)
                + global_msg(2, "wl_seat", 8)
                + wl_msg(3, 0, struct.pack("<I", 0)))
        assert input_probes.parse_wl_globals(data)["wl_seat"] == 8

    def test_truncated_stream_is_partial_not_error(self):
        data = global_msg(1, "wl_seat", 3) + b"\x00\x00"
        out = input_probes.parse_wl_globals(data)
        assert out == {"wl_seat": 3}


class TestCompositeProbe:
    def test_probe_cache_and_reset(self, monkeypatch, compositor_factory):
        input_probes.reset_probe_cache()
        comp = compositor_factory(
            [("zwp_virtual_keyboard_manager_v1", 1)])
        monkeypatch.setenv("WAYLAND_DISPLAY", comp.path)
        # the dbus leg stays deterministic in unit tests: patch it out
        monkeypatch.setattr(input_probes, "_probe_remote_desktop",
                            lambda timeout=1.0: False)
        probes = input_probes.probe_input_backends()
        assert probes.virtual_keyboard is True
        assert probes.remote_desktop is False
        input_probes.reset_probe_cache()

    def test_no_wayland_display_skips_registry(self, monkeypatch):
        input_probes.reset_probe_cache()
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setattr(input_probes, "_probe_remote_desktop",
                            lambda timeout=1.0: None)
        probes = input_probes.probe_input_backends()
        assert probes.virtual_keyboard is None
        input_probes.reset_probe_cache()
