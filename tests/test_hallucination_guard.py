"""Hallucination/garbage guard (2026-09-10 incident: forced-language and
bad-mic takes typed fluent garbage - "you you you you", "Thank you for
watching", phonetic English over Slovenian speech).

Covers:
  - the repetition detector (single- and n-gram runs) and the
    no_speech_prob tell from backend segments
  - the forced-language garbage retry: one re-decode with auto detection,
    accepted only when the retry comes back clean; the whitelist guard
    still applies to the retry's detected language
  - suppression: pure repetition output is never typed (notify instead)
  - parakeet-class backends: no language retry (English-only), repetition
    suppression still applies
  - the segmented preview: pathological segment/tail text is suppressed
    before commit/emit
"""
from __future__ import annotations

import copy
import struct
import wave
from pathlib import Path

import pytest

from fluidvoice.config import DEFAULTS
from fluidvoice import daemon as dm
from fluidvoice.pipeline import (is_repeat_hallucination,
                                 looks_like_hallucination)
from fluidvoice.preview import SegmentedPreviewEngine

RATE = 16000
BPS = RATE * 2


def make_wav(path, seconds: float = 1.0, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(6000 * ((i % 100) / 50 - 1)))
            for i in range(int(seconds * rate)))
        w.writeframes(frames)
    return path


class FakeModelBackend:
    """results: list of dicts (or callables taking the language)."""

    name = "fake"
    surfaces_detected_language = True
    selects_language = True

    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls: list[str | None] = []

    def transcribe(self, wav, language=None):
        self.calls.append(language)
        r = self.results.pop(0) if self.results else {}
        return r(language) if callable(r) else dict(r)


def _cfg(**general):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["general"].update(general)
    return cfg


def _pipeline(cfg, backend):
    logs: list[str] = []
    pipe = dm.DictationPipeline(
        cfg, backend,
        inserter=lambda text, c: "typed",
        history_writer=lambda e, w: None,
        logger=logs.append)
    return pipe, logs


# -- detectors -------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("you you you you", True),
    ("You. You. You. You.", True),           # punctuation/case variance
    ("you you you you you you", True),
    ("Thank you. Thank you. Thank you.", True),  # 2-gram run
    ("so so so so", True),
    ("you you you", False),                  # too short a run
    ("yeah yeah", False),
    ("no no, that is not right at all", False),
    ("Well, I don't wish to see it anymore, observed Phoebe.", False),
    ("", False),
])
def test_is_repeat_hallucination(text, expected):
    assert is_repeat_hallucination(text) is expected


def test_no_speech_tell_flags_confident_garbage():
    # fluent hallucinations carry healthy logprobs (confident garbage) -
    # only no_speech_prob separates them
    result = {"text": "That's all for this video.",
              "segments": [{"avg_logprob": -0.2, "no_speech_prob": 0.9},
                           {"avg_logprob": -0.2, "no_speech_prob": 0.8}]}
    assert looks_like_hallucination(result) is True


def test_healthy_result_is_not_garbage():
    result = {"text": "hello is this working",
              "segments": [{"avg_logprob": -0.2, "no_speech_prob": 0.05}]}
    assert looks_like_hallucination(result) is False


# -- forced-language garbage retry ----------------------------------------

class TestForcedLanguageRetry:
    def test_garbage_forced_decode_retries_auto(self, tmp_path):
        # tonight's incident shape (measured on the real model): language
        # pinned en, Slovenian speech -> fluent phonetic English at
        # logprob ~ -1.2 (confidence band 0)
        cfg = _cfg(language="en", language_whitelist=["sl", "en"])
        b = FakeModelBackend(results=[
            {"text": "Frenchman is overshadowed. Marie-Bour",
             "language": "en", "duration": 5.0,
             "segments": [{"avg_logprob": -1.22, "no_speech_prob": 0.41},
                          {"avg_logprob": -1.22, "no_speech_prob": 0.41}]},
            {"text": "slovenski tekst", "language": "sl", "duration": 5.0},
        ])
        pipe, logs = _pipeline(cfg, b)
        result = pipe._transcribe(make_wav(tmp_path / "utt.wav"))
        assert b.calls == ["en", "auto"]
        assert result["text"] == "slovenski tekst"
        assert any("hallucination guard" in line and "forced=en" in line
                   for line in logs)

    def test_band0_forced_decode_retries_even_without_hard_tells(
            self, tmp_path):
        # difficult audio lands band 0 without any hallucination tell -
        # still worth one auto retry; accepted only when confidently better
        cfg = _cfg(language="en")
        b = FakeModelBackend(results=[
            {"text": "maybe wrong words here", "language": "en",
             "duration": 5.0,
             "segments": [{"avg_logprob": -1.1, "no_speech_prob": 0.2}]},
            {"text": "clean retry text", "language": "en", "duration": 5.0,
             "segments": [{"avg_logprob": -0.2, "no_speech_prob": 0.1}]},
        ])
        pipe, logs = _pipeline(cfg, b)
        result = pipe._transcribe(make_wav(tmp_path / "utt.wav"))
        assert b.calls == ["en", "auto"]
        assert result["text"] == "clean retry text"
        assert any("was low-confidence" in line for line in logs)

    def test_no_speech_forced_decode_retries_auto(self, tmp_path):
        cfg = _cfg(language="en")
        b = FakeModelBackend(results=[
            {"text": "That's all for this video, thank you for watching.",
             "language": "en", "duration": 10.0,
             "segments": [{"avg_logprob": -0.3, "no_speech_prob": 0.85}]},
            {"text": "hello there", "language": "en", "duration": 10.0},
        ])
        pipe, _ = _pipeline(cfg, b)
        result = pipe._transcribe(make_wav(tmp_path / "utt.wav"))
        assert b.calls == ["en", "auto"]
        assert result["text"] == "hello there"

    def test_retry_whitelisted_after_auto_detects_outside(self, tmp_path):
        # garbage under forced en -> auto retry detects ru (outside the
        # whitelist) -> whitelist guard re-decodes sl -> clean, accepted
        cfg = _cfg(language="en", language_whitelist=["sl", "en"])
        b = FakeModelBackend(results=[
            {"text": "you you you you", "language": "en", "duration": 5.0},
            {"text": "ru garbage", "language": "ru", "duration": 5.0},
            {"text": "slovenski tekst", "language": "sl", "duration": 5.0},
        ])
        pipe, logs = _pipeline(cfg, b)
        result = pipe._transcribe(make_wav(tmp_path / "utt.wav"))
        assert b.calls == ["en", "auto", "sl"]
        assert result["text"] == "slovenski tekst"

    def test_garbage_retry_kept_only_when_retry_clean(self, tmp_path):
        # bad mic: both decodes garbage -> original stands (typed text is
        # then subject to the repetition suppression below, or passes as
        # unverifiable), never a worse rewrite
        cfg = _cfg(language="en")
        b = FakeModelBackend(results=[
            {"text": "to investigate the child.", "language": "en",
             "duration": 5.0,
             "segments": [{"avg_logprob": -0.4, "no_speech_prob": 0.9}]},
            {"text": "ru ru ru ru", "language": "ru", "duration": 5.0},
        ])
        pipe, logs = _pipeline(cfg, b)
        result = pipe._transcribe(make_wav(tmp_path / "utt.wav"))
        assert b.calls == ["en", "auto"]
        assert result["text"] == "to investigate the child."
        assert any("retry not confidently better" in line for line in logs)

    def test_healthy_forced_decode_never_retries(self, tmp_path):
        cfg = _cfg(language="en")
        b = FakeModelBackend(results=[
            {"text": "hello is this working", "language": "en",
             "duration": 4.0}])
        pipe, logs = _pipeline(cfg, b)
        pipe._transcribe(make_wav(tmp_path / "utt.wav"))
        assert b.calls == ["en"]
        assert not [l for l in logs if "hallucination guard" in l]

    def test_pinned_runtime_override_retries_too(self, tmp_path):
        # the F7 cycle pins a concrete language the same way config does
        cfg = _cfg(language="auto")
        b = FakeModelBackend(results=[
            {"text": "you you you you", "language": "en", "duration": 5.0},
            {"text": "slovenski tekst", "language": "sl", "duration": 5.0},
        ])
        pipe, _ = _pipeline(cfg, b)
        pipe._language_override = "en"
        result = pipe._transcribe(make_wav(tmp_path / "utt.wav"))
        assert b.calls == ["en", "auto"]
        assert result["text"] == "slovenski tekst"

    def test_parakeet_class_backend_never_retries(self, tmp_path):
        cfg = _cfg(language="en")
        b = FakeModelBackend(results=[
            {"text": "you you you you", "language": None, "duration": 5.0},
        ])
        b.selects_language = False
        b.surfaces_detected_language = False
        pipe, logs = _pipeline(cfg, b)
        pipe._transcribe(make_wav(tmp_path / "utt.wav"))
        assert b.calls == ["en"]
        assert not [l for l in logs if "hallucination guard" in l]


# -- suppression ----------------------------------------------------------

class TestRepetitionSuppression:
    def _run(self, tmp_path, script, language="en"):
        cfg = _cfg(language=language)
        b = FakeModelBackend(results=script)
        pipe, logs = _pipeline(cfg, b)
        notes: list[tuple[str, str]] = []
        pipe.notify = lambda title, body="": notes.append((title, body))
        out = pipe.run(make_wav(tmp_path / "utt.wav"), "TestApp")
        return pipe, logs, notes, out

    def test_pure_repetition_never_typed(self, tmp_path):
        # every decode is the classic bad-mic loop - nothing to retry to
        pipe, logs, notes, out = self._run(tmp_path, [
            {"text": "you you you you", "language": "en", "duration": 8.0},
            {"text": "you you you you", "language": "ru", "duration": 8.0},
        ])
        assert out is None
        assert notes and "check" in notes[0][1].lower()
        assert any("suppressed repetition" in line for line in logs)

    def test_parakeet_repetition_suppressed_without_retry(self, tmp_path):
        cfg = _cfg(language="en")
        b = FakeModelBackend(results=[
            {"text": "you you you you", "language": None, "duration": 8.0},
        ])
        b.selects_language = False
        b.surfaces_detected_language = False
        pipe, logs = _pipeline(cfg, b)
        notes: list[tuple[str, str]] = []
        pipe.notify = lambda title, body="": notes.append((title, body))
        out = pipe.run(make_wav(tmp_path / "utt.wav"), "TestApp")
        assert out is None
        assert b.calls == ["en"]
        assert notes

    def test_normal_text_still_typed(self, tmp_path):
        pipe, logs, notes, out = self._run(tmp_path, [
            {"text": "hello is this working", "language": "en",
             "duration": 4.0}])
        assert out is not None
        assert out["text"].startswith("hello")
        assert not notes


# -- dead-mic notice -------------------------------------------------------

def silent_wav(path, seconds: float = 2.0, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(seconds * rate) * 2)
    return path


class TestDeadMicNotice:
    """A mic streaming digital zeros (wedged capture path) must not look
    like 'user said nothing': the 2026-09-10 PipeWire wedge produced bare
    empty transcriptions with no hint the input was dead."""

    def test_digital_silence_empty_notifies(self, tmp_path):
        cfg = _cfg(language="en")
        b = FakeModelBackend(results=[{"text": "", "language": "en"}])
        pipe, logs = _pipeline(cfg, b)
        notes: list[tuple[str, str]] = []
        pipe.notify = lambda title, body="": notes.append((title, body))
        out = pipe.run(silent_wav(tmp_path / "dead.wav"), "TestApp")
        assert out is None
        assert notes and "mic" in notes[0][1].lower()
        assert any("digital silence" in l for l in logs)

    def test_empty_on_live_mic_stays_quiet(self, tmp_path):
        # quiet room, user just said nothing audible: no notification
        cfg = _cfg(language="en")
        b = FakeModelBackend(results=[{"text": "", "language": "en"}])
        pipe, logs = _pipeline(cfg, b)
        notes: list[tuple[str, str]] = []
        pipe.notify = lambda title, body="": notes.append((title, body))
        out = pipe.run(make_wav(tmp_path / "live.wav", seconds=2.0), "TestApp")
        assert out is None
        assert not notes
        assert any("empty transcription" in l for l in logs)

    def test_is_digital_silence_detector(self, tmp_path):
        from fluidvoice.audio_utils import is_digital_silence
        assert is_digital_silence(str(silent_wav(tmp_path / "s.wav"))) is True
        assert is_digital_silence(
            str(make_wav(tmp_path / "l.wav", seconds=2.0))) is False


# -- preview suppression ----------------------------------------------------

def _pcm(seconds: float, freq: int = 440) -> bytes:
    import math
    return b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / RATE)))
        for i in range(int(seconds * RATE)))


class TestPreviewSuppression:
    def _engine(self, tmp_path, transcriber):
        # on_text receives the pill display text (committed + live tail)
        shown: list[str] = []

        def on_text(text: str, stable: int = 0) -> None:
            shown.append(text)

        raw = tmp_path / "take.raw"
        raw.write_bytes(_pcm(0.2))
        eng = SegmentedPreviewEngine(raw, transcriber, on_text,
                                     interval=1.2, min_audio=0.5,
                                     segment_s=2.0)
        return eng, shown

    def _drive(self, eng, raw: Path, total_s: float, step_s: float) -> None:
        data = b""
        t = step_s
        while t <= total_s + 1e-9:
            data += _pcm(step_s)
            raw.write_bytes(data)   # the engine decodes from the file
            eng._tick(data, len(data) / BPS)
            t += step_s

    def test_repetition_segment_not_committed(self, tmp_path):
        eng, shown = self._engine(tmp_path, lambda w, c: "you you you you")
        self._drive(eng, tmp_path / "take.raw", total_s=6.0, step_s=0.5)
        assert eng.stats["commits"] == 3
        assert eng.stats["suppressed"] >= 3
        assert not any(eng.committed)          # nothing hallucinated committed
        assert not [t for t in shown if "you" in t]

    def test_mixed_segments_keep_healthy_text(self, tmp_path):
        from itertools import cycle
        replies = cycle(["hello there", "you you you you", "real words"])
        eng, shown = self._engine(tmp_path, lambda w, c: next(replies))
        self._drive(eng, tmp_path / "take.raw", total_s=6.0, step_s=0.5)
        committed = " ".join(t for t in eng.committed if t)
        assert "hello there" in committed
        assert "you you" not in committed
        assert eng.stats["suppressed"] >= 1

    def test_tail_repetition_suppressed(self, tmp_path):
        # first commit healthy, then the live tail decodes the loop: the
        # pill display never shows it
        replies = iter(["hello", "you you you you", "you you you you",
                        "you you you you", "you you you you"])
        eng, shown = self._engine(
            tmp_path, lambda w, c: next(replies, "you you you you"))
        self._drive(eng, tmp_path / "take.raw", total_s=5.5, step_s=0.5)
        assert "hello" in " ".join(shown)
        assert not [t for t in shown if "you you" in t]
