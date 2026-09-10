"""Tray icon tests (headless - no D-Bus session needed)."""
from __future__ import annotations

from fluidvoice.tray import (
    KIND_CHECK,
    KIND_SEPARATOR,
    TRAY_SIZE,
    TrayIcon,
    list_microphones,
    render_pixmaps,
)


class TestPixmaps:
    def test_both_variants_sized(self):
        pm = render_pixmaps()
        assert set(pm) == {"idle", "recording"}
        for w, h, data in pm.values():
            assert w == h == TRAY_SIZE
            assert len(data) == w * h * 4

    def test_recording_badge_changes_pixels(self):
        pm = render_pixmaps()
        assert pm["idle"][2] != pm["recording"][2]

    def test_argb32_byte_order(self):
        # SNI pixmaps are ARGB32 big-endian: the rounded corner pixel must
        # lead with alpha ~= 0 and carry no RGB under it
        w, h, data = render_pixmaps(size=32)["idle"]
        assert data[0] < 40
        assert data[1] == data[2] == data[3] == 0

    def test_missing_icon_falls_back_to_drawn_glyph(self, tmp_path):
        w, h, data = render_pixmaps(tmp_path / "nope.png")["idle"]
        assert len(data) == TRAY_SIZE * TRAY_SIZE * 4
        i = (h // 2 * w + w // 2) * 4
        assert data[i] > 200  # opaque glyph at the center


FAKE_PACTL_SOURCES = """\
Source #41
\tState: RUNNING
\tName: alsa_input.usb-Cam.mono-fallback
\tDescription: USB Cam Mono
\tMonitor of Sink: n/a
Source #42
\tState: SUSPENDED
\tName: alsa_output.monitor
\tDescription: Monitor of Built-in Audio
\tMonitor of Sink: alsa_output
Source #43
\tState: SUSPENDED
\tName: alsa_input.pci.analog-stereo
\tDescription: Built-in Analog
"""


class TestMicrophoneListing:
    def test_parses_sources_and_skips_monitors(self, monkeypatch):
        import subprocess as sp

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["pactl", "list"]:
                return type("R", (), {"stdout": FAKE_PACTL_SOURCES})()
            if cmd[:2] == ["pactl", "get-default-source"]:
                return type("R", (), {"stdout": "alsa_input.usb-Cam.mono-fallback\n"})()
            raise FileNotFoundError(cmd)

        monkeypatch.setattr(sp, "run", fake_run)
        mics = list_microphones(refresh=True)
        names = [m["name"] for m in mics]
        assert names == ["alsa_input.usb-Cam.mono-fallback",
                         "alsa_input.pci.analog-stereo"]
        assert mics[0]["description"] == "USB Cam Mono"
        assert mics[0]["default"] is True
        assert mics[1]["default"] is False

    def test_no_pactl_returns_empty(self, monkeypatch):
        import subprocess as sp

        def boom(*a, **k):
            raise FileNotFoundError("pactl")

        monkeypatch.setattr(sp, "run", boom)
        assert list_microphones(refresh=True) == []


class TestMenuModel:
    def test_menu_model_shape(self):
        """Status line + cancel (state-dependent) + copy-last + settings +
        microphone submenu + quit, mirroring the macOS menu bar menu."""
        import copy

        import fluidvoice.daemon as dm
        from fluidvoice.config import DEFAULTS
        from tests.test_daemon import StubRecorder

        d = dm.Daemon(copy.deepcopy(DEFAULTS), recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        menu = d._build_tray_menu()
        labels = [i.get("label") for i in menu]
        assert "Ready to Record (Right_Control)" in labels
        cancel = next(i for i in menu if "Cancel Dictation" in i.get("label", ""))
        assert cancel["enabled"] is False  # idle
        mic = next(i for i in menu if i.get("label") == "Microphone")
        assert mic["children"][0]["label"] == "Auto (system default)"
        assert mic["children"][0]["kind"] == KIND_CHECK
        assert any(i.get("kind") == KIND_SEPARATOR for i in menu)
        assert "Quit SayItErmano" in labels

    def test_set_device_updates_config_and_recorder(self, monkeypatch):
        import copy

        import fluidvoice.config as config_mod
        import fluidvoice.daemon as dm
        from fluidvoice.config import DEFAULTS
        from tests.test_daemon import StubRecorder

        saved = {}
        monkeypatch.setattr(config_mod, "save_config",
                            lambda c, path=None: saved.update(c["recording"]))
        d = dm.Daemon(copy.deepcopy(DEFAULTS), recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        d._set_device("alsa_input.usb-Cam.mono-fallback")
        assert saved["device"] == "alsa_input.usb-Cam.mono-fallback"
        assert d.recorder.device == "alsa_input.usb-Cam.mono-fallback"
        assert "Auto (system default)" in [c["label"] for c in
                                           next(i for i in d._build_tray_menu()
                                                if i.get("label") == "Microphone")["children"]]
        d._set_device("")
        assert d.recorder.device == ""

    def test_menu_orders_mics_by_priority(self, monkeypatch):
        import copy

        import fluidvoice.daemon as dm
        import fluidvoice.tray as tray_mod
        from fluidvoice.config import DEFAULTS
        from tests.test_daemon import StubRecorder

        BLUEZ = "bluez_source.00_11_22_33_44_55.headset-mono"
        USBCAM = "alsa_input.usb-Cam.mono-fallback"
        PCI = "alsa_input.pci.analog-stereo"
        monkeypatch.setattr(tray_mod, "list_microphones", lambda: [
            {"name": PCI, "description": "Built-in Analog"},
            {"name": BLUEZ, "description": "BT Headset"},
            {"name": USBCAM, "description": "USB Cam Mono"}])
        cfg = copy.deepcopy(DEFAULTS)
        cfg["recording"]["device"] = BLUEZ
        cfg["recording"]["mic_priority"] = ["bluez", "usb-cam"]
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        mic = next(i for i in d._build_tray_menu()
                   if i.get("label") == "Microphone")
        assert [c["label"] for c in mic["children"]] == \
            ["Auto (system default)", "BT Headset", "USB Cam Mono",
             "Built-in Analog"]
        checked = [c for c in mic["children"] if c.get("checked")]
        assert [c["label"] for c in checked] == ["BT Headset"]


class TestFallback:
    def test_start_fails_cleanly_without_dbus(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def no_dbus(name, *args, **kwargs):
            if name.split(".")[0] in ("dbus", "gi"):
                raise ImportError("no dbus here")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_dbus)
        t = TrayIcon()
        assert t.start() is False
        t.set_recording(True)  # inactive paths must not raise
        t.stop()

    def test_config_default_enabled(self):
        from fluidvoice.config import DEFAULTS
        assert DEFAULTS["general"]["tray_enabled"] is True
        assert DEFAULTS["recording"]["preview_bottom_offset"] == 64
