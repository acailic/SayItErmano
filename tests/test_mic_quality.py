"""Mic-quality verdicts (dead / too far / clipping) from PCM hops, the
once-per-take latch, and the per-device level baseline sidecar."""
from __future__ import annotations

import json
import math

from fluidvoice.mic_quality import (
    FIXED_FLOOR_DBFS,
    LevelWatch,
    level_floor_for,
    load_baselines,
    analyze_wav,
    monitor,
    update_baseline,
)

RATE = 16000


def pcm_silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(RATE * seconds)


def pcm_tone(seconds: float, amp: float, freq: float = 220.0,
             clip: bool = False) -> bytes:
    """Sine at int16 amplitude `amp` (0..1 FS); clip=True pins |x| to FS
    for a distorted/garbage signal."""
    n = int(RATE * seconds)
    out = bytearray()
    for i in range(n):
        x = math.sin(2 * math.pi * freq * i / RATE)
        v = int(amp * 32767 * x)
        if clip:
            v = 32767 if x >= 0 else -32768
        out += int(v).to_bytes(2, "little", signed=True)
    return bytes(out)


def speech_like(seconds: float, amp: float) -> bytes:
    """Bursty voiced-ish signal: 160 ms tone + 60 ms gap cycles (speech
    fraction ~0.73; the RMS VAD gate sees the tone frames)."""
    n = int(seconds * RATE)
    out = bytearray()
    i = 0
    while i < n:
        burst = min(int(0.16 * RATE), n - i)
        for k in range(burst):
            x = math.sin(2 * math.pi * 180 * (i + k) / RATE)
            out += int(amp * 32767 * x).to_bytes(2, "little", signed=True)
        gap = min(int(0.06 * RATE), n - i - burst)
        out += b"\x00\x00" * gap
        i += burst + gap
    return bytes(out)


class TestHops:
    def test_silence_below_min_audio_no_verdict(self):
        w = LevelWatch()
        w.feed(pcm_silence(1.0))
        assert w.verdicts() == []  # < MIN_AUDIO_S gate

    def test_dead_verdict(self):
        w = LevelWatch()
        w.feed(pcm_silence(3.0))
        assert w.verdicts() == ["dead"]

    def test_healthy_speech_no_verdict(self):
        w = LevelWatch()
        w.feed(speech_like(3.0, 0.25))  # ~-18 dBFS bursts
        assert w.verdicts() == []

    def test_far_verdict_below_floor(self):
        # bursts just above the VAD gate (~-44 dBFS speech): detectable
        # speech, far below the fixed floor
        w = LevelWatch()
        w.feed(speech_like(3.0, 0.009))
        assert w.verdicts() == ["far"]

    def test_far_uses_baseline_floor(self):
        # ~-33 dBFS speech is healthy vs the fixed floor but "far" vs a
        # device whose recent norm was ~-18 dBFS (floor ~-30)
        w = LevelWatch()
        w.feed(speech_like(3.0, 0.037))
        assert w.verdicts() == []  # above the fixed floor: healthy
        assert w.verdicts(floor_dbfs=-30.0) == ["far"]  # below its own norm

    def test_clip_verdict(self):
        w = LevelWatch()
        w.feed(pcm_tone(3.0, 1.0, clip=True))
        assert "clip" in w.verdicts()

    def test_latch_once(self):
        w = LevelWatch()
        w.feed(pcm_silence(3.0))
        assert w.verdicts() == ["dead"]
        w.feed(pcm_silence(3.0))
        assert w.verdicts() == []  # already warned this take


class TestSummary:
    def test_summary_shape_for_log_and_doctor(self):
        w = LevelWatch()
        w.feed(speech_like(3.0, 0.1))
        s = w.summary()
        assert {"elapsed_s", "speech_frac", "speech_dbfs"} <= set(s)


class TestBaseline:
    def test_missing_is_empty_and_fallback_floor(self, tmp_path):
        p = tmp_path / "mic-baseline.json"
        assert load_baselines(path=p) == {}
        assert level_floor_for("any", path=p) == FIXED_FLOOR_DBFS

    def test_rolling_mean_and_floor_raises(self, tmp_path):
        p = tmp_path / "mic-baseline.json"
        for dbfs in (-20.0, -18.0):
            update_baseline("usb-cam", dbfs, path=p)
        assert load_baselines(path=p)["usb-cam"]["takes"] == 2
        floor = level_floor_for("usb-cam", path=p)
        assert floor > FIXED_FLOOR_DBFS  # above the fixed fallback
        data = json.loads(p.read_text())
        assert abs(data["usb-cam"]["speech_dbfs"] - (-19.0)) < 1e-6

    def test_corrupt_degrades(self, tmp_path):
        p = tmp_path / "mic-baseline.json"
        p.write_text("{nope", encoding="utf-8")
        assert load_baselines(path=p) == {}

    def test_atomic_0600(self, tmp_path):
        p = tmp_path / "mic-baseline.json"
        update_baseline("d", -18.0, path=p)
        assert not list(tmp_path.glob("*.tmp"))
        import os
        assert (os.stat(p).st_mode & 0o777) == 0o600


class TestMonitor:
    def test_dead_take_fires_one_notice(self, tmp_path):
        import threading

        p = tmp_path / "take.raw"
        p.write_bytes(pcm_silence(3.0))
        got: list[tuple[str, str]] = []
        ended = threading.Event()

        def notify(title, body):
            got.append((title, body))
            ended.set()  # end the loop at the first verdict

        monitor(p, "usb-cam", notify=notify, poll_s=0.01, stop=ended)
        assert len(got) == 1
        assert got[0][0] == "SayItErmano"
        assert "silent" in got[0][1].lower()

    def test_healthy_take_no_notice(self, tmp_path):
        import threading

        p = tmp_path / "take.raw"
        p.write_bytes(speech_like(3.0, 0.2))
        got: list[tuple] = []
        ended = threading.Event()

        def stop_soon():
            ended.set()

        import time as _t
        threading.Timer(0.2, stop_soon).start()
        monitor(p, "usb-cam", notify=lambda *a: got.append(a),
                poll_s=0.01, stop=ended)
        assert got == []

    def test_stop_event_ends_loop(self, tmp_path):
        import threading

        p = tmp_path / "take.raw"
        p.write_bytes(pcm_silence(10.0))
        ended = threading.Event()
        ended.set()  # already ended: must return immediately
        watch = monitor(p, "usb-cam", poll_s=0.5, stop=ended)
        assert watch.elapsed_s == 0.0  # never read anything


class TestAnalyzeWav:
    def test_summary_from_wav_file(self, tmp_path):
        import wave

        p = tmp_path / "take.wav"
        pcm = speech_like(3.0, 0.2)
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
        s = analyze_wav(p)
        assert s["speech_frac"] > 0.5
        assert -35 < s["speech_dbfs"] < -10

    def test_unreadable_is_empty(self, tmp_path):
        assert analyze_wav(tmp_path / "nope.wav") == {}
