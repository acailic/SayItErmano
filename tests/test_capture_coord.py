"""CaptureCoordinator (P1.2): the take lifecycle state machine tested with
fake deps - no Daemon, no X11, no audio devices. Daemon-level wiring
(toggle routing, pipeline handoff, shutdown ordering) stays covered by
test_daemon.py / test_first_pcm_lifecycle.py / test_spoken_send_countdown.py
through the daemon's delegation."""
from __future__ import annotations

import copy
import threading
import time
from pathlib import Path

from fluidvoice.capture import CaptureCoordinator
from fluidvoice.config import DEFAULTS
from fluidvoice.recorder import RecorderError
from fluidvoice.runtime_tasks import RuntimeTasks


def make_wav(path, seconds: float = 1.0) -> Path:
    import math
    import struct
    import wave
    n = int(16000 * seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        frames = bytearray()
        for i in range(n):
            v = int(12000 * math.sin(2 * math.pi * 440 * i / 16000))
            frames += struct.pack("<h", v)
        wf.writeframes(bytes(frames))
    if path.stat().st_size < 200:
        with open(path, "ab") as fh:
            fh.write(b"\0" * 300)
    return path


class StubRecorder:
    """Start writes a real wav (so size checks pass); cancel discards."""

    def __init__(self, fail_start=False, stop_result="real"):
        self.fail_start = fail_start
        self.stop_result = stop_result  # "real" | None
        self.path: Path | None = None
        self.raw_path: Path | None = None
        self.started = 0
        self.stopped = 0
        self.cancelled = 0

    def start(self, path):
        if self.fail_start:
            raise RecorderError("no recorder found")
        self.path = make_wav(path)
        self.raw_path = self.path  # what the stall monitor watches
        self.started += 1

    def stop(self):
        self.stopped += 1
        if self.stop_result is None:
            return None
        return self.path

    def cancel(self):
        self.cancelled += 1
        self.stopped += 1
        self.path = None


def make(cfg=None, recorder=None, tmp_path=None):
    """Coordinator with recorded callbacks and a stub recorder."""
    cfg = cfg if cfg is not None else copy.deepcopy(DEFAULTS)
    recorder = recorder or StubRecorder()
    calls = {"notify": [], "sound": [], "change": [], "complete": [],
             "start_hooks": [], "activity": 0, "media": []}

    class _Rec:
        def pause_if_playing(self):
            calls["media"].append("pause")
            return True

        def resume(self):
            calls["media"].append("resume")

    coord = CaptureCoordinator(
        cfg, RuntimeTasks(), threading.Lock(),
        recorder_provider=lambda: recorder,
        log=lambda msg: None,
        notify=lambda t, b: calls["notify"].append((t, b)),
        play_sound=lambda which: calls["sound"].append(which),
        on_recording_change=lambda active: calls["change"].append(active),
        on_take_start=lambda: calls["start_hooks"].append(1),
        on_take_complete=lambda *a: calls["complete"].append(a),
        on_activity=lambda: calls.__setitem__(
            "activity", calls["activity"] + 1),
        language_provider=lambda: "auto",
        backend_provider=lambda: None,
        on_copy_last=lambda: None,
        on_paste_last=lambda: None)
    coord._media = _Rec()  # deterministic media stub
    return coord, recorder, calls


def wait_until(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# start / stop / cancel
# ---------------------------------------------------------------------------

class TestTakeLifecycle:
    def test_start_and_stop_happy_path(self, monkeypatch):
        from fluidvoice import insertion
        monkeypatch.setattr(insertion, "active_window_class",
                            lambda: "TestApp")
        cfg = copy.deepcopy(DEFAULTS)
        cfg["recording"]["max_seconds"] = 300
        cfg["recording"]["first_pcm_timeout"] = 0  # keep the test lean
        cfg["recording"]["stall_timeout_s"] = 0
        coord, rec, calls = make(cfg=cfg)
        with coord._lock:
            coord.start_locked(mode="rewrite", rewrite_context="sel text")
        assert coord.recording is True and rec.started == 1
        assert coord.take_mode == "rewrite"
        assert coord.rewrite_context == "sel text"
        assert coord.app_hint is not None  # insertion probed at take start
        assert calls["sound"] == ["start"]
        assert calls["change"] == [True]
        assert calls["start_hooks"] == [1]  # daemon may spawn a reload
        assert calls["media"] == ["pause"]
        assert coord.watchdog is not None
        assert coord.watchdog.is_alive()
        with coord._lock:
            coord.stop_locked()
        assert coord.recording is False
        assert calls["sound"] == ["start", "stop"]
        assert calls["change"] == [True, False]
        assert calls["media"] == ["pause", "resume"]
        # the finished take was handed off with its take metadata
        assert len(calls["complete"]) == 1
        wav, app_hint, mode, context = calls["complete"][0]
        assert wav.exists() and mode == "rewrite" and context == "sel text"
        # timers never outlive the take
        assert coord.watchdog is None and coord.first_pcm_timer is None
        # mode flags reset for the next take
        assert coord.take_mode == "dictate" and coord.rewrite_context is None

    def test_start_failure_notifies_and_cleans_up(self, tmp_path):
        coord, rec, calls = make(recorder=StubRecorder(fail_start=True))
        with coord._lock:
            coord.start_locked()
        assert coord.recording is False
        assert rec.started == 0
        assert any("Recording failed" in (t + b) for t, b in calls["notify"])

    def test_stop_without_audio_resets_and_hands_off_nothing(self):
        coord, rec, calls = make(recorder=StubRecorder(stop_result=None))
        coord.profile_override = "Terse"
        with coord._lock:
            coord.start_locked(mode="command")
        with coord._lock:
            coord.stop_locked()
        assert calls["complete"] == []  # nothing to process
        assert coord.take_mode == "dictate"
        assert rec.stopped == 1  # the recorder WAS stopped, just discarded

    def test_cancel_discards_the_take(self):
        coord, rec, calls = make()
        with coord._lock:
            coord.start_locked()
        coord.profile_override = "Terse"
        coord.cancel()
        assert coord.recording is False
        assert rec.cancelled == 1
        assert coord.profile_override is None  # cleared with the take
        assert coord.take_mode == "dictate"
        assert calls["change"] == [True, False]
        assert any("Cancelled" in (t + b) for t, b in calls["notify"])

    def test_cancel_when_idle_is_a_noop(self):
        coord, rec, calls = make()
        coord.cancel()
        assert rec.cancelled == 0
        assert calls["notify"] == []

    def test_abort_locked_is_the_shutdown_take_teardown(self):
        coord, rec, _ = make()
        with coord._lock:
            coord.start_locked()
        timer = coord.first_pcm_timer
        with coord._lock:
            coord.abort_locked()
        assert coord.recording is False and rec.cancelled == 1
        assert coord.first_pcm_timer is None
        assert timer is None or timer.finished.is_set()


# ---------------------------------------------------------------------------
# first-PCM liveness timer
# ---------------------------------------------------------------------------

class TestFirstPcm:
    def _silent_start(self):
        class SilentRecorder(StubRecorder):
            def start(self, path):
                self.path = Path(path)
                Path(path).write_bytes(b"\0" * 100)  # header only
                self.started += 1

        cfg = copy.deepcopy(DEFAULTS)
        cfg["recording"]["stall_timeout_s"] = 0
        coord, rec, calls = make(cfg=cfg, recorder=SilentRecorder())
        with coord._lock:
            coord.start_locked()
        recorder, wav = coord.first_pcm_timer.args
        return coord, rec, calls, recorder, wav

    def test_silent_mic_stops_its_own_take(self):
        coord, rec, calls, recorder, wav = self._silent_start()
        coord.check_first_pcm(recorder, wav)
        assert coord.recording is False
        assert rec.cancelled == 1
        assert coord.first_pcm_timer is None  # consumed by its own firing
        assert calls["media"][-1] == "resume"
        assert any("no audio" in (t + b).lower() for t, b in calls["notify"])

    def test_firing_after_cancel_is_noop(self):
        coord, rec, calls, recorder, wav = self._silent_start()
        coord.cancel()
        before = list(calls["notify"])
        coord.check_first_pcm(recorder, wav)  # the lost race
        assert rec.cancelled == 1  # only cancel(), nothing extra
        assert calls["notify"] == before

    def test_replaced_recorder_is_ignored(self):
        coord, rec, calls, recorder, wav = self._silent_start()
        new = StubRecorder()
        coord._recorder = lambda: new  # settings change swapped it
        coord.check_first_pcm(recorder, wav)
        assert coord.recording is True
        assert rec.cancelled == 0 and new.cancelled == 0
        coord.cancel()  # hygiene: never leave the take's watchdog pending

    def test_zero_disables_the_timer(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["recording"]["first_pcm_timeout"] = 0
        cfg["recording"]["stall_timeout_s"] = 0
        coord, _, _ = make(cfg=cfg)
        with coord._lock:
            coord.start_locked()
        assert coord.first_pcm_timer is None
        coord.cancel()  # hygiene: never leave the take's watchdog pending


# ---------------------------------------------------------------------------
# max-duration watchdog + VAD auto-stop
# ---------------------------------------------------------------------------

class TestAutoStop:
    def test_watchdog_stops_the_take(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["recording"]["max_seconds"] = 0.15
        cfg["recording"]["first_pcm_timeout"] = 0
        cfg["recording"]["stall_timeout_s"] = 0
        coord, rec, calls = make(cfg=cfg)
        with coord._lock:
            coord.start_locked()
        assert wait_until(lambda: not coord.recording)
        assert len(calls["complete"]) == 1  # processed, not discarded

    def test_auto_stop_after_cancel_starts_nothing(self):
        coord, rec, calls = make()
        with coord._lock:
            coord.start_locked()
        coord.cancel()
        stale = threading.Timer(0.05, coord.auto_stop)  # simulate late fire
        stale.start()
        stale.join(timeout=2)
        assert not coord.recording
        assert rec.started == 1  # no second recording began

    def test_vad_auto_stop_finishes_the_take(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["recording"]["first_pcm_timeout"] = 0
        cfg["recording"]["stall_timeout_s"] = 0
        coord, rec, calls = make(cfg=cfg)
        with coord._lock:
            coord.start_locked()
        coord.vad_auto_stop()
        assert coord.recording is False
        assert len(calls["complete"]) == 1

    def test_vad_when_not_recording_is_noop(self):
        coord, rec, calls = make()
        coord.vad_auto_stop()
        assert rec.stopped == 0


# ---------------------------------------------------------------------------
# spoken-send countdown
# ---------------------------------------------------------------------------

class TestSpokenSendCountdown:
    def _armed(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["recording"]["first_pcm_timeout"] = 0
        cfg["recording"]["stall_timeout_s"] = 0
        cfg["recording"]["spoken_send_countdown_s"] = 5.0
        coord, rec, calls = make(cfg=cfg)
        with coord._lock:
            coord.start_locked()
        coord.on_send_countdown()
        return coord, rec, calls

    def test_countdown_arms_and_stops_the_take(self):
        coord, rec, calls = self._armed()
        assert coord.send_countdown_timer is not None
        coord.send_countdown_stop(coord.send_countdown_timer)
        assert coord.recording is False
        assert coord.send_countdown_timer is None
        assert len(calls["complete"]) == 1

    def test_stale_timer_cannot_stop_a_take(self):
        coord, _, _ = self._armed()
        stale = coord.send_countdown_timer
        coord.on_send_countdown()  # re-arm (cancels the first timer)
        fresh = coord.send_countdown_timer
        assert fresh is not stale
        coord.send_countdown_stop(stale)  # identity check: ignored
        assert coord.recording is True
        coord.send_countdown_stop(fresh)  # the current one stops the take
        assert coord.recording is False

    def test_resume_cancels_the_timer(self):
        coord, _, _ = self._armed()
        coord.on_send_resume()
        assert coord.send_countdown_timer is None
        coord.cancel()  # hygiene: never leave the take's watchdog pending

    def test_zero_countdown_disables_callbacks(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["recording"]["spoken_send_countdown_s"] = 0
        coord, _, _ = make(cfg=cfg)
        coord.on_send_countdown()
        assert coord.send_countdown_timer is None


# ---------------------------------------------------------------------------
# stall monitor
# ---------------------------------------------------------------------------

class TestStallMonitor:
    def test_frozen_stream_cancels(self, monkeypatch):
        monkeypatch.setattr(CaptureCoordinator, "STALL_CHECK_S", 0.05)
        cfg = copy.deepcopy(DEFAULTS)
        cfg["recording"]["first_pcm_timeout"] = 0
        cfg["recording"]["stall_timeout_s"] = 0.2
        coord, rec, calls = make(cfg=cfg)
        with coord._lock:
            coord.start_locked()
        assert wait_until(lambda: not coord.recording, timeout=5.0)
        assert rec.cancelled == 1
        assert any("stalled" in (t + b) for t, b in calls["notify"])

    def test_growing_stream_survives(self, monkeypatch):
        monkeypatch.setattr(CaptureCoordinator, "STALL_CHECK_S", 0.05)
        cfg = copy.deepcopy(DEFAULTS)
        cfg["recording"]["first_pcm_timeout"] = 0
        cfg["recording"]["stall_timeout_s"] = 0.2
        coord, rec, _ = make(cfg=cfg)
        with coord._lock:
            coord.start_locked()
        stop = threading.Event()

        def grow():
            while not stop.is_set():
                with open(rec.raw_path, "ab") as fh:
                    fh.write(b"\0" * 4096)
                time.sleep(0.03)

        t = threading.Thread(target=grow, daemon=True)
        t.start()
        time.sleep(0.5)
        stop.set()
        t.join(timeout=2)
        assert coord.recording is True  # never froze
        coord.cancel()


# ---------------------------------------------------------------------------
# closing display + language announcement
# ---------------------------------------------------------------------------

class TestDisplayHooks:
    def test_take_closing_display_hands_over_and_clears(self):
        coord, _, _ = make()
        disp = type("D", (), {"closed": 0,
                              "close": lambda self: None})()
        coord.closing_display = disp
        assert coord.take_closing_display() is disp
        assert coord.closing_display is None
        coord.close_closing_display()  # already cleared: no crash

    def test_announce_uses_the_live_pill_badge(self):
        coord, _, _ = make()
        badges = []
        disp = type("D", (), {"set_badge": lambda self, b: badges.append(b)})()
        coord.preview = (None, disp)
        coord.announce_language("sl")
        assert badges == ["lang: sl"]

    def test_announce_falls_back_to_notify_preview(self, monkeypatch):
        shown = []

        class FakeNotify:
            def show(self, text):
                shown.append(text)

        import fluidvoice.preview as preview_mod
        monkeypatch.setattr(preview_mod, "NotifyPreview", FakeNotify)
        coord, _, _ = make()
        coord.announce_language("de")
        assert shown == ["Language: de"]
