"""Mid-take stall watchdog (upstream #852): a frozen capture stream
cancels the take with a clear error instead of recording air."""
from __future__ import annotations

import copy
import time
from pathlib import Path

import pytest

from fluidvoice import daemon as dm
from fluidvoice.capture import CaptureCoordinator as _Cap
from fluidvoice.config import DEFAULTS, coerce_setting


class RawRecorder:
    """Recorder stub with a real raw_path file the test grows or freezes."""

    def __init__(self, wav: Path, raw: Path):
        self.path = wav
        self.raw_path = raw
        self.cancelled = 0
        self.stopped = 0

    def start(self, p):
        self.raw_path.write_bytes(b"\0" * 32000)  # 1 s of stream

    def stop(self):
        self.stopped += 1
        return self.path

    def cancel(self):
        self.cancelled += 1


class Backend:
    name = "stub"
    surfaces_detected_language = False

    def transcribe(self, wav, language=None):
        return {"text": "x", "language": None, "duration": None,
                "segments": []}


@pytest.fixture()
def env(tmp_path, monkeypatch, request):
    # request.param (indirect) widens the stall timeout for the
    # healthy-stream test: 0.6 s assumes real-time pacing, and a worker
    # descheduled under `-n auto` load once blew past it (Q12 flake fix).

    import math
    import struct
    import wave

    with wave.open(str(tmp_path / "utt.wav"), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"".join(
            struct.pack("<h", int(9000 * math.sin(i * 0.05)))
            for i in range(16000)))
    raw = tmp_path / "raw.stream"
    rec = RawRecorder(tmp_path / "utt.wav", raw)
    cfg = copy.deepcopy(DEFAULTS)
    cfg["recording"]["stall_timeout_s"] = getattr(request, "param", 0.6)
    monkeypatch.setattr(dm.ui, "notify", lambda *a, **k: None)
    monkeypatch.setattr(dm.ui, "play_sound", lambda *a, **k: None)
    monkeypatch.setattr(_Cap, "STALL_CHECK_S", 0.2)
    d = dm.Daemon(cfg, recorder=rec, backend_factory=lambda c: Backend(),
                  use_hotkey=False, use_sounds=False)
    d.backend = Backend()
    return d, rec, raw


def test_frozen_stream_cancels_take(env):
    d, rec, raw = env
    assert d.handle_request({"action": "toggle"})["recording"] is True
    deadline = time.monotonic() + 4.0
    while d.recording and time.monotonic() < deadline:
        time.sleep(0.1)
    assert d.recording is False            # cancelled by the watchdog
    assert rec.cancelled == 1
    assert d._process_thread is None       # nothing transcribed


@pytest.mark.parametrize("env", [5.0], indirect=True)
def test_growing_stream_survives(env):
    d, rec, raw = env
    d.handle_request({"action": "toggle"})
    for _ in range(12):                    # keep the stream flowing ~2.4 s
        with open(raw, "ab") as fh:
            fh.write(b"\0" * 8000)         # 0.25 s per append
        time.sleep(0.2)
    assert d.recording is True             # checks ran, stream healthy
    d.handle_request({"action": "cancel"})


def test_zero_disables(env):
    d, rec, raw = env
    d.cfg["recording"]["stall_timeout_s"] = 0
    d.handle_request({"action": "toggle"})
    time.sleep(1.2)                        # frozen, but watchdog is off
    assert d.recording is True
    d.handle_request({"action": "cancel"})


def test_config_surface():
    assert DEFAULTS["recording"]["stall_timeout_s"] == 8.0
    for good in (0, 2.0, 8.0, 300.0):
        ok, _ = coerce_setting("recording", "stall_timeout_s", good)
        assert ok, good
    for bad in (-1, 301.0, "soon"):
        ok, _ = coerce_setting("recording", "stall_timeout_s", bad)
        assert not ok, bad
