"""Spoken-send quiet countdown (B7) — daemon-side state machine + config
surface. The engine-side arm/cancel table lives in
test_preview_segmented.py::TestSendCountdown."""
from __future__ import annotations

import copy
import time

import pytest

from fluidvoice import daemon as dm
from fluidvoice.config import DEFAULTS, coerce_setting


class StubRecorder:
    def __init__(self, path):
        import math
        import struct
        import wave
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"".join(
                struct.pack("<h", int(9000 * math.sin(i * 0.05)))
                for i in range(16000)))
        self.path = path
        self.started = 0
        self.stopped = 0

    def start(self, p):
        self.started += 1

    def stop(self):
        self.stopped += 1
        return self.path

    def cancel(self):
        self.stopped += 1


class StubBackend:
    name = "stub"
    surfaces_detected_language = False

    def transcribe(self, wav, language=None):
        return {"text": "typed text", "language": None,
                "duration": None, "segments": []}


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    c = copy.deepcopy(DEFAULTS)
    c["recording"]["spoken_send_enabled"] = True
    c["recording"]["spoken_send_countdown_s"] = 0.4
    monkeypatch.setattr(dm.ui, "notify", lambda *a, **k: None)
    monkeypatch.setattr(dm.ui, "play_sound", lambda *a, **k: None)
    monkeypatch.setattr(dm.history_mod.paths, "history_file",
                        lambda: tmp_path / "h.jsonl")
    return c


def make_daemon(cfg, tmp_path):
    rec = StubRecorder(tmp_path / "utt.wav")
    d = dm.Daemon(cfg, recorder=rec,
                  backend_factory=lambda c: StubBackend(),
                  use_hotkey=False, use_sounds=False)
    d.backend = StubBackend()
    return d, rec


def wait_done(d, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if d._process_thread is None or not d._process_thread.is_alive():
            return not d.busy
        time.sleep(0.02)
    return False


def test_countdown_stops_the_take(cfg, tmp_path):
    d, rec = make_daemon(cfg, tmp_path)
    assert d.handle_request({"action": "toggle"})["recording"] is True
    d._capture.on_send_countdown()
    assert d._capture.send_countdown_timer is not None
    # generous bound: the countdown is 0.4 s, but under `-n auto` load the
    # worker can be descheduled long enough that 2 s flaked (Q12: flaky
    # tests get fixed, not rerun); 10 s still proves it stops on its own.
    deadline = time.monotonic() + 10.0
    while d.recording and time.monotonic() < deadline:
        time.sleep(0.05)
    assert d.recording is False
    assert wait_done(d)
    assert d.last_result.get("text") == "typed text"
    assert d._capture.send_countdown_timer is None


def test_resume_cancels_the_timer(cfg, tmp_path):
    d, rec = make_daemon(cfg, tmp_path)
    d.handle_request({"action": "toggle"})
    d._capture.on_send_countdown()
    d._capture.on_send_resume()
    assert d._capture.send_countdown_timer is None
    time.sleep(0.8)  # past the 0.4 s countdown
    assert d.recording is True
    d.handle_request({"action": "cancel"})


def test_stale_timer_cannot_stop_a_take(cfg, tmp_path):
    d, rec = make_daemon(cfg, tmp_path)
    d.handle_request({"action": "toggle"})
    d._capture.on_send_countdown()
    stale = d._capture.send_countdown_timer
    d._capture.on_send_countdown()  # re-arm (cancels the first timer)
    fresh = d._capture.send_countdown_timer
    assert stale is not fresh
    d._capture.send_countdown_stop(stale)  # identity check: ignored
    assert d.recording is True
    d._capture.send_countdown_stop(fresh)  # current one stops the take
    assert d.recording is False
    assert wait_done(d)


def test_vad_auto_stop_cancels_pending_countdown(cfg, tmp_path):
    d, rec = make_daemon(cfg, tmp_path)
    d.handle_request({"action": "toggle"})
    d._capture.on_send_countdown()
    timer = d._capture.send_countdown_timer
    d._capture.vad_auto_stop()
    assert d.recording is False
    assert not timer.is_alive()
    assert wait_done(d)


def test_zero_countdown_disables_callbacks(cfg, tmp_path):
    cfg["recording"]["spoken_send_countdown_s"] = 0
    d, rec = make_daemon(cfg, tmp_path)
    d.handle_request({"action": "toggle"})
    d._capture.on_send_countdown()  # countdown <= 0: no timer armed
    assert d._capture.send_countdown_timer is None
    assert d.recording is True
    d.handle_request({"action": "cancel"})


# -- config surface ----------------------------------------------------------

def test_config_coercion():
    assert DEFAULTS["recording"]["spoken_send_countdown_s"] == 1.2
    for good in (0, 0.3, 1.2, 5.0):
        ok, _ = coerce_setting("recording", "spoken_send_countdown_s", good)
        assert ok, good
    for bad in (0.2, 5.1, -1, "soon", None):
        ok, _ = coerce_setting("recording", "spoken_send_countdown_s", bad)
        assert not ok, bad


def test_config_socket_settable():
    from fluidvoice.config import _SAVE_WHITELIST, ALLOWED_SETTINGS
    assert "spoken_send_countdown_s" in ALLOWED_SETTINGS["recording"]
    assert "spoken_send_countdown_s" in _SAVE_WHITELIST["recording"]


# -- doctor ------------------------------------------------------------------

def test_doctor_spoken_send_lines():
    from fluidvoice import doctor
    c = copy.deepcopy(DEFAULTS)
    lines = doctor._spoken_send_lines(c)
    assert any("spoken-send: off" in ln for ln in lines)
    c["recording"]["spoken_send_enabled"] = True
    lines = "\n".join(doctor._spoken_send_lines(c))
    assert "quiet countdown: 1.2 s" in lines
    c["recording"]["spoken_send_countdown_s"] = 0
    assert any("quiet countdown: off" in ln
               for ln in doctor._spoken_send_lines(c))
