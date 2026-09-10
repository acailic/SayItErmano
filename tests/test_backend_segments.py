"""Segment exposure in the three backends (constructors bypassed, no models).

Result shape note (P1.1 seam): backends now return `Transcript`; the
legacy dict view (`out["segments"]` etc.) serializes via to_dict(),
which omits absent optional confidence fields (avg_logprob/
no_speech_prob) instead of emitting nulls. tests/test_backend_contract.py
runs the shared adapter contract over all five backends."""
from __future__ import annotations


class FakeSeg:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


class FakeInfo:
    def __init__(self, language="en", duration=1.5):
        self.language = language
        self.duration = duration


class TestFasterWhisperSegments:
    def test_text_join_and_segment_dicts(self, tmp_path):
        from fluidvoice.backends.faster_whisper_backend import FasterWhisperBackend
        wav = tmp_path / "u.wav"
        wav.write_bytes(b"x")
        calls = []

        class FakeModel:
            def transcribe(self, path, **kw):
                calls.append(path)

                def gen():
                    yield FakeSeg(0.1234, 1.0006, " And so")
                    yield FakeSeg(1.5, 2.0, " it goes.")
                return gen(), FakeInfo("en", 2.0)

        be = object.__new__(FasterWhisperBackend)
        be._model = FakeModel()
        be.language = None
        out = be.transcribe(wav)
        assert out["text"] == "And so it goes."
        assert out["language"] == "en" and out["duration"] == 2.0
        # FakeSeg carries no confidence attrs: the absent optionals are
        # OMITTED (typed contract), not emitted as nulls
        assert out["segments"] == [
            {"start": 0.123, "end": 1.001, "text": "And so"},
            {"start": 1.5, "end": 2.0, "text": "it goes."},
        ]
        assert len(calls) == 1  # generator consumed once, no re-transcription

    def test_no_segments_yields_empty_list(self, tmp_path):
        from fluidvoice.backends.faster_whisper_backend import FasterWhisperBackend
        wav = tmp_path / "u.wav"
        wav.write_bytes(b"x")

        class FakeModel:
            def transcribe(self, path, **kw):
                return iter([]), FakeInfo("en", 0.5)

        be = object.__new__(FasterWhisperBackend)
        be._model = FakeModel()
        be.language = None
        out = be.transcribe(wav)
        assert out["text"] == "" and out["segments"] == []


class TestTorchWhisperSegments:
    def test_segments_stripped_and_rounded(self, tmp_path):
        from fluidvoice.backends.torch_whisper import TorchWhisperBackend
        wav = tmp_path / "u.wav"
        wav.write_bytes(b"x")
        result = {"text": " hi ", "language": "en",
                  "segments": [{"start": 0.20001, "end": 1.0, "text": " hi "}]}

        class FakeModel:
            def transcribe(self, path, **kw):
                return result

        be = object.__new__(TorchWhisperBackend)
        be._model = FakeModel()
        be.device = "cpu"
        be.language = None
        out = be.transcribe(wav)
        assert out["text"] == "hi" and out["language"] == "en"
        assert out["duration"] is None
        assert out["segments"] == [{"start": 0.2, "end": 1.0, "text": "hi"}]

    def test_missing_segments_key_ok(self, tmp_path):
        from fluidvoice.backends.torch_whisper import TorchWhisperBackend
        wav = tmp_path / "u.wav"
        wav.write_bytes(b"x")

        class FakeModel:
            def transcribe(self, path, **kw):
                return {"text": "words", "language": "en"}

        be = object.__new__(TorchWhisperBackend)
        be._model = FakeModel()
        be.device = "cpu"
        be.language = None
        out = be.transcribe(wav)
        assert out["segments"] == []


class TestWhisperCppSegments:
    def test_returns_empty_segments(self, tmp_path, monkeypatch):
        import fluidvoice.backends.whisper_cpp as wc
        wav = tmp_path / "u.wav"
        wav.write_bytes(b"x")

        class Proc:
            returncode = 0
            stderr = ""
            stdout = "hello world\n"

        monkeypatch.setattr(wc.subprocess, "run", lambda *a, **kw: Proc())
        be = object.__new__(wc.WhisperCppBackend)
        be.binary = "/fake/whisper-cli"
        be.model = "/fake/model.bin"
        be.language = "auto"
        out = be.transcribe(wav)
        assert out["text"] == "hello world" and out["segments"] == []
        assert out["duration"] is None
