"""Live preview engine + raw recording tests (no audio hardware needed)."""
from __future__ import annotations

import struct
import time
import wave

from fluidvoice.audio_utils import raw_to_wav_bytes, raw_to_wav_file
from fluidvoice.preview import NotifyPreview, PreviewEngine


def pcm(seconds: float, rate: int = 16000, freq: int = 440) -> bytes:
    import math
    return b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(int(seconds * rate)))


class TestRawWav:
    def test_roundtrip(self):
        raw = pcm(0.1)
        wav = raw_to_wav_bytes(raw)
        with wave.open(__import__("io").BytesIO(wav)) as wf:
            assert wf.getframerate() == 16000
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getnframes() == 1600

    def test_file_wrap(self, tmp_path):
        raw = tmp_path / "x.raw"
        raw.write_bytes(pcm(0.2))
        wav = tmp_path / "x.wav"
        raw_to_wav_file(raw, wav, 16000)
        with wave.open(str(wav)) as wf:
            assert wf.getnframes() == 3200
        assert raw.exists()  # helper is non-destructive; recorder cleans up


class TestPreviewEngine:
    def test_rolling_partial_transcription(self, tmp_path):
        raw = tmp_path / "live.raw"
        raw.write_bytes(pcm(0.2))
        texts = []

        def fake_transcriber(wav_bytes: bytes) -> str:
            seconds = len(wav_bytes) / (16000 * 2)
            # pretend the model "hears" more words as audio grows
            return "hello " * max(1, int(seconds / 2))

        engine = PreviewEngine(raw, fake_transcriber, texts.append,
                               interval=0.15, min_audio=0.1)
        engine.start()
        deadline = time.monotonic() + 5
        while len(texts) < 2 and time.monotonic() < deadline:
            with open(raw, "ab") as fh:  # keep "recording"
                fh.write(pcm(0.5))
            time.sleep(0.08)
        engine.stop(timeout=2)
        assert len(texts) >= 2
        assert texts[1].startswith("hello")
        assert engine.last_text

    def test_skips_when_below_min_audio(self, tmp_path):
        raw = tmp_path / "tiny.raw"
        raw.write_bytes(pcm(0.05))  # 50 ms < min_audio=1.0
        called = []
        engine = PreviewEngine(raw, lambda b: called.append(b) or "",
                               lambda t: None, interval=0.1, min_audio=1.0)
        engine.start()
        time.sleep(0.5)
        engine.stop(timeout=2)
        assert called == []  # never transcribed

    def test_transcriber_errors_are_swallowed(self, tmp_path):
        raw = tmp_path / "e.raw"
        raw.write_bytes(pcm(0.3))

        def broken(wav_bytes):
            raise RuntimeError("cuda busy")

        engine = PreviewEngine(raw, broken, lambda t: None,
                               interval=0.1, min_audio=0.1)
        engine.start()
        time.sleep(0.4)
        engine.stop(timeout=2)  # must not raise

    def test_long_text_truncated_for_display(self, tmp_path):
        raw = tmp_path / "l.raw"
        raw.write_bytes(pcm(0.3))
        shown = []
        engine = PreviewEngine(raw, lambda b: "w" * 500, shown.append,
                               interval=0.1, min_audio=0.1, char_limit=160)
        engine.start()
        deadline = time.monotonic() + 3
        while not shown and time.monotonic() < deadline:
            time.sleep(0.05)
        engine.stop(timeout=2)
        assert shown and len(shown[0]) == 161 and shown[0].startswith("…")


class TestNotifyPreview:
    def test_no_notify_send_is_silent(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, "which", lambda n: None)
        p = NotifyPreview()
        assert not p.supported
        p.show("hello")  # must not raise
        p.close()


class TestRawRecorder:
    def test_stop_converts_raw_to_wav(self, tmp_path, monkeypatch):
        from fluidvoice import recorder as rec

        class SlowProc:
            def __init__(self):
                self.signals = []

            def poll(self):
                return None

            def send_signal(self, s):
                self.signals.append(s)

            def wait(self, timeout=None):
                return 0

        proc = SlowProc()
        monkeypatch.setattr(rec.subprocess, "Popen", lambda a, **k: proc)
        monkeypatch.setattr(rec.time, "sleep", lambda s: None)

        def fake_drain(p):
            pass

        monkeypatch.setattr(rec.threading, "Thread",
                            lambda target=None, args=(), daemon=None, name=None:
                            type("T", (), {"start": lambda s: None})())
        r = rec.Recorder()
        wav_path = tmp_path / "utt.wav"
        r.start(wav_path)
        # simulate the recorder writing raw PCM
        assert r.raw_path == tmp_path / "utt.raw"
        r.raw_path.write_bytes(pcm(0.3))
        raw = r.raw_path
        out = r.stop()
        assert out == wav_path
        assert wav_path.exists()
        with wave.open(str(wav_path)) as wf:
            assert wf.getnframes() == 4800
        assert not raw.exists()  # cleaned up
        assert r.raw_path is None  # reset after stop

    def test_cancel_removes_both(self, tmp_path, monkeypatch):
        from fluidvoice import recorder as rec

        class P:
            def poll(self):
                return None

            def send_signal(self, s):
                pass

            def wait(self, timeout=None):
                return 0

        monkeypatch.setattr(rec.subprocess, "Popen", lambda a, **k: P())
        monkeypatch.setattr(rec.time, "sleep", lambda s: None)
        monkeypatch.setattr(rec.threading, "Thread",
                            lambda target=None, args=(), daemon=None, name=None:
                            type("T", (), {"start": lambda s: None})())
        r = rec.Recorder()
        r.start(tmp_path / "c.wav")
        r.raw_path.write_bytes(pcm(0.1))
        raw = r.raw_path
        r.cancel()
        assert not (tmp_path / "c.wav").exists()
        assert not raw.exists()
