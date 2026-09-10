"""Scriptable unix-socket API (C1): transcribe + history routes via
handle_request (direct dispatch, stub backend — no network/audio)."""
from __future__ import annotations

import copy
import math
import struct
import wave
from pathlib import Path

import pytest

from fluidvoice import daemon as dm
from fluidvoice.config import DEFAULTS


def make_wav(path: Path, seconds: float = 1.0) -> Path:
    rate = 16000
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(
            struct.pack("<h", int(9000 * math.sin(i * 0.05)))
            for i in range(int(rate * seconds))))
    if path.stat().st_size < 200:
        with open(path, "ab") as fh:
            fh.write(b"\0" * 300)
    return path


class StubRecorder:
    def __init__(self, path):
        self.path = path

    def start(self, p):
        pass

    def stop(self):
        return self.path

    def cancel(self):
        pass


class StubBackend:
    name = "stub"
    surfaces_detected_language = False

    def __init__(self):
        self.calls = []

    def transcribe(self, wav, language=None):
        self.calls.append((str(wav), language))
        # filler-laden text so the process flag has visible work
        return {"text": "stub um text ", "language": language,
                "duration": 1.0, "segments": []}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cfg = copy.deepcopy(DEFAULTS)
    rec = StubRecorder(make_wav(tmp_path / "utt.wav"))
    backend = StubBackend()
    d = dm.Daemon(cfg, recorder=rec,
                  backend_factory=lambda c: backend,
                  use_hotkey=False, use_sounds=False)
    d.backend = backend
    monkeypatch.setattr(dm.ui, "notify", lambda *a, **k: None)
    monkeypatch.setattr(dm.ui, "play_sound", lambda *a, **k: None)
    monkeypatch.setattr(dm.history_mod.paths, "history_file",
                        lambda: tmp_path / "history.jsonl")
    return d, backend, tmp_path


class TestTranscribeRoute:
    def test_roundtrip(self, env):
        d, backend, tmp = env
        wav = make_wav(tmp / "in.wav")
        r = d.handle_request({"action": "transcribe", "path": str(wav)})
        assert r["ok"] is True and r["text"] == "stub um text "
        assert r["language"] == "auto"  # the daemon's live resolution
        assert backend.calls and backend.calls[0][0].endswith(".wav")

    def test_process_flag_runs_post_chain(self, env):
        d, _b, tmp = env
        wav = make_wav(tmp / "in2.wav")
        raw = d.handle_request({"action": "transcribe", "path": str(wav)})
        assert raw["text"] == "stub um text "       # default: raw
        proc = d.handle_request({"action": "transcribe", "path": str(wav),
                                 "process": True})
        assert proc["text"] == "stub text "         # filler "um" removed

    def test_missing_path_errors(self, env):
        d, _b, _tmp = env
        r = d.handle_request({"action": "transcribe", "path": ""})
        assert r["ok"] is False and "path" in r["error"]
        r = d.handle_request({"action": "transcribe",
                              "path": "/nonexistent/x.wav"})
        assert r["ok"] is False and "not found" in r["error"]

    def test_busy_refused_while_recording(self, env):
        d, _b, tmp = env
        d.handle_request({"action": "toggle"})
        try:
            r = d.handle_request(
                {"action": "transcribe", "path": str(make_wav(tmp / "b.wav"))})
            assert r["ok"] is False and "busy" in r["error"]
        finally:
            d.handle_request({"action": "cancel"})

    def test_too_long_rejected(self, env, monkeypatch):
        # P3: the 200 MB v1 byte cap is gone; the bound is decoded
        # duration (disk/temp safety), a structured error, never a hang
        from fluidvoice import chunking
        d, backend, tmp = env
        monkeypatch.setattr(chunking, "MAX_TOTAL_SECONDS", 0.5)
        wav = make_wav(tmp / "long.wav", seconds=2.0)
        r = d.handle_request({"action": "transcribe", "path": str(wav)})
        assert r["ok"] is False and "too long" in r["error"]
        assert backend.calls == []  # refused before any decode


class TestHistoryRoute:
    def test_limit_and_rows(self, env):
        d, _b, _tmp = env
        hf = dm.history_mod.paths.history_file()
        for i in range(5):
            dm.history_mod.append({"ts": 1000.0 + i, "text": f"row {i}",
                                   "raw": f"row {i}", "ai": False,
                                   "backend": "stub"})
        r = d.handle_request({"action": "history", "limit": 3})
        assert r["ok"] is True and r["count"] == 3
        assert [e["text"] for e in r["entries"]] == \
            ["row 2", "row 3", "row 4"]  # newest last, chronological
        r = d.handle_request({"action": "history"})
        assert r["count"] == 5  # default limit 10 keeps all

    def test_since_ts_filter(self, env):
        d, _b, _tmp = env
        for i in range(4):
            dm.history_mod.append({"ts": 2000.0 + i, "text": f"r{i}",
                                   "raw": f"r{i}", "ai": False,
                                   "backend": "stub"})
        r = d.handle_request({"action": "history", "since_ts": 2002.0})
        assert [e["text"] for e in r["entries"]] == ["r2", "r3"]

    def test_bad_params(self, env):
        d, _b, _tmp = env
        assert d.handle_request(
            {"action": "history", "limit": "many"})["ok"] is False
        assert d.handle_request(
            {"action": "history", "since_ts": "yesterday"})["ok"] is False

    def test_empty_history(self, env):
        d, _b, _tmp = env
        r = d.handle_request({"action": "history"})
        assert r["ok"] is True and r["entries"] == []
