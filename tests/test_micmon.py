"""Input-device monitoring (micmon): pactl source polling, diffing, and
priority matching — plus the daemon's auto-switch wiring on top."""
from __future__ import annotations

import copy
import threading

import pytest

from fluidvoice.config import DEFAULTS
from fluidvoice.micmon import (
    MicMonitor,
    list_source_names,
    match_priority,
    priority_rank,
    sort_by_priority,
)

BLUEZ = "bluez_source.00_11_22_33_44_55.headset-mono"
USBCAM = "alsa_input.usb-Cam.mono-fallback"
PCI = "alsa_input.pci.analog-stereo"
MONITOR = "alsa_output.pci.analog-stereo.monitor"


class TestListSourceNames:
    def test_parses_short_sources_skips_monitors(self, monkeypatch):
        import subprocess as sp

        fake = ("41\t" + BLUEZ + "\tpipe_wire\tfloat32le 1ch 16000Hz\trunning\n"
                "42\t" + USBCAM + "\tpipe_wire\tfloat32le 1ch 48000Hz\tsuspended\n"
                "43\t" + PCI + "\tpipe_wire\tfloat32le 2ch 48000Hz\tsuspended\n"
                "50\t" + MONITOR + "\tpipe_wire\tfloat32le 2ch 48000Hz\tidle\n")

        def fake_run(cmd, **kwargs):
            assert cmd == ["pactl", "list", "short", "sources"]
            return type("R", (), {"stdout": fake})()

        monkeypatch.setattr(sp, "run", fake_run)
        assert list_source_names() == [BLUEZ, USBCAM, PCI]

    def test_no_pactl_returns_empty_no_raise(self, monkeypatch):
        import subprocess as sp

        def boom(*a, **k):
            raise FileNotFoundError("pactl")

        monkeypatch.setattr(sp, "run", boom)
        assert list_source_names() == []

    def test_garbage_lines_skipped(self, monkeypatch):
        import subprocess as sp

        def fake_run(cmd, **kwargs):
            return type("R", (), {"stdout": "\nnoise without tabs\n41\t"
                                            + BLUEZ + "\tdrv\n"})()

        monkeypatch.setattr(sp, "run", fake_run)
        assert list_source_names() == [BLUEZ]


class TestMatching:
    def test_pattern_order_beats_listing_order(self):
        assert match_priority(["usb-cam", "bluez"], [BLUEZ, USBCAM, PCI]) \
            == USBCAM

    def test_same_pattern_tie_first_listing_order_wins(self):
        assert match_priority(["bluez"], [BLUEZ + "2", BLUEZ]) == BLUEZ + "2"

    def test_no_match_and_empty_patterns(self):
        assert match_priority(["nope"], [BLUEZ, PCI]) is None
        assert match_priority([], [BLUEZ]) is None
        assert match_priority(["bluez"], []) is None

    def test_case_insensitive(self):
        assert match_priority(["BLUEZ"], [BLUEZ]) == BLUEZ
        assert priority_rank(BLUEZ, ["UsB-cAm", "BLUEZ"]) == 1

    def test_priority_rank(self):
        assert priority_rank(USBCAM, ["bluez", "usb-cam"]) == 1
        assert priority_rank(PCI, ["bluez"]) is None

    def test_sort_by_priority_stable(self):
        mics = [{"name": PCI}, {"name": BLUEZ}, {"name": USBCAM},
                {"name": "alsa_input.other"}]
        out = sort_by_priority(mics, ["usb-cam", "bluez"])
        assert [m["name"] for m in out] == [USBCAM, BLUEZ, PCI,
                                            "alsa_input.other"]

    def test_sort_by_priority_no_patterns_keeps_order(self):
        mics = [{"name": PCI}, {"name": BLUEZ}]
        assert sort_by_priority(mics, []) == mics


class TestMicMonitor:
    def make(self, script, calls):
        def poll():
            if not script:
                raise AssertionError("script exhausted")
            return script.pop(0)

        mon = MicMonitor(on_change=lambda a, r, c: calls.append((a, r, c)),
                         poll=poll)
        return mon

    def test_baseline_poll_fires_no_callback(self):
        calls: list = []
        mon = self.make([[USBCAM, PCI]], calls)
        mon.poll_once()
        assert calls == []
        assert mon.last_names == [USBCAM, PCI]

    def test_addition_detected(self):
        calls: list = []
        mon = self.make([[PCI], [PCI, BLUEZ]], calls)
        mon.poll_once()  # baseline
        mon.poll_once()
        assert calls == [([BLUEZ], [], [PCI, BLUEZ])]

    def test_removal_detected(self):
        calls: list = []
        mon = self.make([[PCI, USBCAM], [USBCAM]], calls)
        mon.poll_once()
        mon.poll_once()
        assert calls == [([], [PCI], [USBCAM])]

    def test_no_change_still_fires_callback_with_empty_diffs(self):
        # contract: every post-baseline poll fires on_change — this is what
        # lets the daemon retry reselects without its own pending flag
        calls: list = []
        mon = self.make([[PCI], [PCI], [PCI]], calls)
        mon.poll_once()
        mon.poll_once()
        mon.poll_once()
        assert calls == [([], [], [PCI]), ([], [], [PCI])]

    def test_monitor_sources_filtered_by_poll_once(self):
        calls: list = []
        mon = self.make([[PCI, MONITOR]], calls)
        mon.poll_once()
        assert mon.last_names == [PCI]  # .monitor never enters the state

    def test_poll_raising_is_swallowed(self):
        calls: list = []

        def boom():
            raise TimeoutError("pactl hung")

        mon = MicMonitor(on_change=lambda a, r, c: calls.append((a, r, c)),
                         poll=boom)
        mon.poll_once()  # must not raise
        assert calls == []

    def test_callback_raising_is_swallowed(self):
        script = [[PCI], [PCI]]

        def boom(a, r, c):
            raise RuntimeError("callback bug")

        mon = MicMonitor(on_change=boom, poll=lambda: script.pop(0))
        mon.poll_once()
        mon.poll_once()  # must not raise; state still updated
        assert mon.last_names == [PCI]

    def test_lifecycle_start_stop(self):
        calls: list = []
        got_two = threading.Event()

        def on_change(added, removed, current):
            calls.append((added, removed, current))
            if len(calls) >= 2:
                got_two.set()

        mon = MicMonitor(on_change=on_change, poll=lambda: [PCI],
                         interval=0.01)
        assert mon.start() is True
        assert got_two.wait(timeout=5)  # >= 2 callbacks after the baseline
        thread = mon._thread
        mon.stop()
        assert not thread.is_alive()
        n = len(calls)
        assert mon._thread is None
        mon.stop()  # idempotent
        threading.Event().wait(0.05)
        assert len(calls) == n  # no callbacks after stop

    def test_start_without_pactl_returns_false(self, monkeypatch):
        import fluidvoice.micmon as mm
        monkeypatch.setattr(mm.shutil, "which", lambda n: None)
        logged: list[str] = []
        mon = MicMonitor(on_change=lambda *a: None, log=logged.append)
        assert mon.start() is False
        assert any("unavailable" in m for m in logged)
        assert mon._thread is None

    def test_start_with_custom_poll_skips_pactl_check(self, monkeypatch):
        import fluidvoice.micmon as mm
        monkeypatch.setattr(mm.shutil, "which", lambda n: None)
        mon = MicMonitor(on_change=lambda *a: None, poll=lambda: [PCI],
                         interval=0.01)
        assert mon.start() is True
        mon.stop()


# ---------------------------------------------------------------------------
# Daemon wiring (auto-switch)
# ---------------------------------------------------------------------------


@pytest.fixture()
def cfg():
    return copy.deepcopy(DEFAULTS)


class TestDaemonAutoSwitch:
    def make(self, cfg, monkeypatch):
        import fluidvoice.config as config_mod
        import fluidvoice.daemon as dm
        from tests.test_daemon import StubRecorder

        saved: dict = {}
        monkeypatch.setattr(config_mod, "save_config",
                            lambda c, path=None: saved.update(c["recording"]))
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        return d, saved

    def test_switches_when_configured_device_vanishes(self, cfg, quiet_ui,
                                                      monkeypatch):
        cfg["recording"]["device"] = USBCAM
        cfg["recording"]["mic_priority"] = ["bluez"]
        d, saved = self.make(cfg, monkeypatch)
        d._on_sources_changed([], [], [USBCAM, PCI])  # device still there
        assert d.cfg["recording"]["device"] == USBCAM
        assert quiet_ui["notify"] == []
        d._on_sources_changed([BLUEZ], [USBCAM], [BLUEZ, PCI])  # it died
        assert d.cfg["recording"]["device"] == BLUEZ
        assert d.recorder.device == BLUEZ
        assert saved["device"] == BLUEZ
        assert any("Microphone switched to" in b for _t, b in
                   quiet_ui["notify"])

    def test_no_switch_when_configured_device_present(self, cfg, quiet_ui,
                                                      monkeypatch):
        cfg["recording"]["device"] = USBCAM
        cfg["recording"]["mic_priority"] = ["bluez"]
        d, saved = self.make(cfg, monkeypatch)
        d._on_sources_changed([BLUEZ], [], [BLUEZ, USBCAM, PCI])
        assert d.cfg["recording"]["device"] == USBCAM  # never upgraded
        assert quiet_ui["notify"] == [] and saved == {}

    def test_auto_device_never_switches(self, cfg, quiet_ui, monkeypatch):
        cfg["recording"]["device"] = ""
        cfg["recording"]["mic_priority"] = ["bluez"]
        d, saved = self.make(cfg, monkeypatch)
        d._on_sources_changed([BLUEZ], [USBCAM], [BLUEZ, PCI])
        assert d.cfg["recording"]["device"] == ""  # auto = system default
        assert quiet_ui["notify"] == [] and saved == {}

    def test_no_priority_match_logs_once(self, cfg, quiet_ui, monkeypatch):
        import fluidvoice.daemon as dm

        cfg["recording"]["device"] = USBCAM
        cfg["recording"]["mic_priority"] = ["bluez"]
        logs: list[str] = []
        monkeypatch.setattr(dm, "log", lambda m: logs.append(m))
        d, _saved = self.make(cfg, monkeypatch)
        d._on_sources_changed([], [USBCAM], [PCI])   # gone, nothing matches
        d._on_sources_changed([], [], [PCI])         # warn-once latch holds
        assert sum("unavailable and no priority match" in m for m in logs) == 1
        assert d.cfg["recording"]["device"] == USBCAM
        d._on_sources_changed([USBCAM], [], [USBCAM, PCI])  # back: latch reset
        assert sum("unavailable and no priority match" in m for m in logs) == 1
        d._on_sources_changed([], [USBCAM], [PCI])   # gone again: re-warns
        assert sum("unavailable and no priority match" in m for m in logs) == 2

    def test_pattern_order_beats_listing_order(self, cfg, quiet_ui,
                                               monkeypatch):
        cfg["recording"]["device"] = PCI
        cfg["recording"]["mic_priority"] = ["usb-cam", "bluez"]
        d, _saved = self.make(cfg, monkeypatch)
        d._on_sources_changed([], [PCI], [BLUEZ, USBCAM])
        assert d.cfg["recording"]["device"] == USBCAM  # pattern #1 wins

    def test_never_switches_while_recording(self, cfg, quiet_ui, monkeypatch):
        cfg["recording"]["pause_media"] = False
        cfg["recording"]["device"] = USBCAM
        cfg["recording"]["mic_priority"] = ["bluez"]
        d, _saved = self.make(cfg, monkeypatch)
        d.toggle()
        d._on_sources_changed([], [USBCAM], [BLUEZ])  # mid-take: untouched
        assert d.cfg["recording"]["device"] == USBCAM
        assert quiet_ui["notify"] == []
        d.cancel()
        d._on_sources_changed([], [], [BLUEZ])        # first idle poll: switch
        assert d.cfg["recording"]["device"] == BLUEZ

    def test_never_switches_while_busy(self, cfg, quiet_ui, monkeypatch):
        cfg["recording"]["device"] = USBCAM
        cfg["recording"]["mic_priority"] = ["bluez"]
        d, _saved = self.make(cfg, monkeypatch)
        d.busy = True
        d._on_sources_changed([], [USBCAM], [BLUEZ])
        assert d.cfg["recording"]["device"] == USBCAM
        d.busy = False
        d._on_sources_changed([], [], [BLUEZ])
        assert d.cfg["recording"]["device"] == BLUEZ

    def test_startup_recovery(self, cfg, quiet_ui, monkeypatch):
        cfg["recording"]["device"] = USBCAM   # configured but absent at boot
        cfg["recording"]["mic_priority"] = ["bluez"]
        d, _saved = self.make(cfg, monkeypatch)
        d._start_micmon(poll=lambda: [PCI, BLUEZ])
        try:
            assert d._micmon is not None
            assert d.cfg["recording"]["device"] == BLUEZ  # fell back at start
        finally:
            d.shutdown()  # watcher lifecycle: clean, prompt exit
        assert d._micmon is None

    def test_watcher_not_started_by_constructor(self, cfg, quiet_ui,
                                                monkeypatch):
        d, _saved = self.make(cfg, monkeypatch)
        assert d._micmon is None  # only run() starts it


from tests.test_daemon import quiet_ui  # noqa: E402,F401  (shared fixture)
