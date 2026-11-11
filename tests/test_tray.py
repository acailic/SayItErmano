"""Tray icon tests (headless - no D-Bus session needed)."""
from __future__ import annotations

import pytest

# Pillow >= 12 deprecates Image.getdata (removal in Pillow 14, 2027);
# production use lives in fluidvoice/tray.py:142 — migrate in a
# dedicated pass. Mark-level filter (NOT ini): with -W error on the
# CLI, ini filterwarnings entries lose and the gate stays red.
pytestmark = pytest.mark.filterwarnings(
    "ignore:Image.Image.getdata is deprecated:DeprecationWarning")

from fluidvoice.tray import (  # noqa: E402
    KIND_CHECK,
    KIND_SEPARATOR,
    TRAY_SIZE,
    WATCHER,
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


class _FakeGLib:
    """Synchronous stand-in for the GLib calls TrayIcon makes on its loop
    thread: idle_add runs now, timeout_add queues (fired by hand)."""

    def __init__(self):
        self.timers: list = []

    def idle_add(self, fn, *args):
        fn(*args)
        return False

    def timeout_add(self, ms, fn, *args):
        self.timers.append((ms, fn, args))
        return len(self.timers)

    def fire(self):
        while self.timers:
            _, fn, args = self.timers.pop(0)
            fn(*args)


class TestWatcherFlap:
    """2026-11-11 incident: the tray host's watcher name churned ~368 Hz
    for 57 minutes and every NameOwnerChanged drove a synchronous
    re-registration + log line - 1.26M journal lines, 82 CPU-minutes, and
    the user journal's whole prior history vacuumed away. Re-registrations
    must coalesce and their logs must throttle."""

    def _icon(self, monkeypatch, clock, log=None):
        import fluidvoice.tray as tray_mod
        monkeypatch.setattr(tray_mod.time, "monotonic", lambda: clock["t"])
        icon = TrayIcon(log=log if log is not None else (lambda m: None))
        icon._glib = _FakeGLib()
        return icon

    def _stub_register(self, icon, ok=True):
        calls = []

        def fake():
            calls.append(1)
            return ok
        icon._register = fake
        return calls

    def test_flap_storm_coalesces_to_one_registration(self, monkeypatch):
        clock = {"t": 1000.0}
        icon = self._icon(monkeypatch, clock)
        calls = self._stub_register(icon)
        icon._register_now()                      # startup registration
        assert len(calls) == 1
        for i in range(1000):                     # an hour of host flapping
            icon._watcher_changed(WATCHER, ":1.9", "")   # releases: ignored
            icon._watcher_changed(WATCHER, "", ":1.9")   # acquisitions
        assert len(calls) == 1                    # all inside the window
        assert len(icon._glib.timers) == 1        # exactly one coalesced timer
        icon._glib.fire()
        assert len(calls) == 2

    def test_reload_after_quiet_period_registers_promptly(self, monkeypatch):
        clock = {"t": 1000.0}
        icon = self._icon(monkeypatch, clock)
        calls = self._stub_register(icon)
        icon._register_now()
        clock["t"] += 10                          # host steady for 10 s
        icon._watcher_changed(WATCHER, "", ":1.10")
        assert len(calls) == 2                    # immediate, no timer
        assert not icon._glib.timers

    def test_log_lines_throttle_to_one_per_minute(self, monkeypatch):
        clock = {"t": 1000.0}
        logs: list[str] = []
        icon = self._icon(monkeypatch, clock, log=logs.append)
        self._stub_register(icon)
        icon._register_now()
        assert logs == ["tray icon registered"]
        for _ in range(5):                        # re-registrations every 2 s
            clock["t"] += 2
            icon._watcher_changed(WATCHER, "", ":1.9")
            icon._glib.fire()
        assert logs == ["tray icon registered"]   # silent inside the window
        clock["t"] += 61                          # next quiet-period reload
        icon._watcher_changed(WATCHER, "", ":1.11")
        icon._glib.fire()
        assert len(logs) == 2
        assert "re-registered" in logs[1]

    def test_failure_log_throttles_and_keeps_cause(self, monkeypatch):
        clock = {"t": 1000.0}
        logs: list[str] = []
        icon = self._icon(monkeypatch, clock, log=logs.append)
        self._stub_register(icon, ok=False)
        icon._register_now()
        assert len(logs) == 1 and "no tray host yet" in logs[0]
        clock["t"] += 2
        icon._watcher_changed(WATCHER, "", ":1.9")
        icon._glib.fire()
        assert len(logs) == 1                     # throttled, no flood

    def test_props_answer_accessible_descriptions(self):
        """ubuntu-appindicators refreshes IconAccessibleDesc /
        AttentionAccessibleDesc and logErrors on InvalidArgs (26,741 shell
        log lines during the same incident) - answer them instead."""
        dbus = pytest.importorskip("dbus")
        from fluidvoice.tray import _build_item_class
        icon = TrayIcon()
        icon._pixmaps = render_pixmaps(size=8)
        item_cls = _build_item_class(dbus, icon)
        item = item_cls.__new__(item_cls)
        item._pixmaps = icon._pixmaps
        item._recording = False
        item._tip = "SayItErmano"
        props = item._props()
        assert props["IconAccessibleDesc"] == ""
        assert props["AttentionAccessibleDesc"] == ""


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


class TestAIProfileMenu:
    def _daemon(self, cfg=None):
        import copy

        import fluidvoice.daemon as dm
        from fluidvoice.config import DEFAULTS
        from tests.test_daemon import StubRecorder
        return dm.Daemon(cfg or copy.deepcopy(DEFAULTS),
                         recorder=StubRecorder(),
                         backend_factory=lambda c: None,
                         use_hotkey=False, use_sounds=False)

    def test_hidden_when_store_empty(self, monkeypatch, tmp_path):
        from fluidvoice import paths
        monkeypatch.setattr(paths, "ai_profiles_file",
                            lambda: tmp_path / "ai-profiles.json")
        d = self._daemon()
        labels = [i.get("label") for i in d._build_tray_menu()]
        assert "AI profile" not in labels

    def test_lists_profiles_with_active_checkmark(self, monkeypatch, tmp_path):
        from fluidvoice import paths
        from fluidvoice.ai import provider_profiles as pp
        p = tmp_path / "ai-profiles.json"
        pp.save_named("GLM", {"base_url": "https://api.z.ai/api/paas/v4",
                              "model": "glm-4.7", "api_key_env": "K"}, path=p)
        pp.save_named("Local", {"base_url": "http://x", "model": "m",
                                "api_key_env": ""}, path=p)
        import copy

        from fluidvoice.config import DEFAULTS
        monkeypatch.setattr(paths, "ai_profiles_file", lambda: p)
        cfg = copy.deepcopy(DEFAULTS)
        cfg["ai"]["profile"] = "Local"
        d = self._daemon(cfg)
        menu = d._build_tray_menu()
        sub = next(i for i in menu if i.get("label") == "AI profile")
        assert [c["label"] for c in sub["children"]] == ["GLM", "Local"]
        checked = [c for c in sub["children"] if c.get("checked")]
        assert [c["label"] for c in checked] == ["Local"]
        mic = next(i for i in menu if i.get("label") == "Microphone")
        assert menu.index(sub) == menu.index(mic) + 1

    def test_switch_action_applies_set_config(self, monkeypatch, tmp_path):
        import fluidvoice.config as config_mod
        from fluidvoice import paths
        from fluidvoice.ai import provider_profiles as pp
        p = tmp_path / "ai-profiles.json"
        pp.save_named("GLM", {"base_url": "https://api.z.ai/api/paas/v4",
                              "model": "glm-4.7", "api_key_env": "K"},
                      path=p)
        monkeypatch.setattr(paths, "ai_profiles_file", lambda: p)
        monkeypatch.setattr(config_mod, "save_config",
                            lambda c, path=None: None)
        d = self._daemon()
        sub = next(i for i in d._build_tray_menu()
                   if i.get("label") == "AI profile")
        glm = next(c for c in sub["children"] if c["label"] == "GLM")
        glm["action"]()
        assert d.cfg["ai"]["profile"] == "GLM"
        assert d.cfg["ai"]["base_url"] == "https://api.z.ai/api/paas/v4"
        assert d.cfg["ai"]["model"] == "glm-4.7"
