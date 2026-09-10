"""Shared speech-backend adapter contract (plan P1.1).

The SAME tests run against all five adapters (parametrized), offline —
every backend is exercised through its real `transcribe()` with the
fake-model/stub-session/fake-server patterns already used across the
suite (no model downloads, no onnxruntime import, no network beyond the
loopback fake STT server).

What the contract pins:
  - capabilities: each adapter declares a BackendCapabilities that is
    frozen, truthful (the verified per-adapter table below), and agrees
    with its legacy duck-typed flags
  - transcript invariants: a Transcript with str text, ordered segments
    (start <= end), float|None optional confidence fields, and consistent
    typing for language/duration
  - missing-optional-fields tolerance: model results without segments /
    confidence keys parse cleanly (the pre-seam world produced those)
  - serialization round trips: Transcript.from_dict(to_dict(x)) == x,
    and the legacy dict view reproduces the historical four-key shape
  - degradation: adapters whose capabilities are off (whisper.cpp
    segments/confidence, parakeet language) produce transcripts that
    consumers treat as absent signals; `capabilities_of` resolves
    pre-seam duck-typed fakes to all-off; `Transcript.of` accepts legacy
    dict results unchanged
"""
from __future__ import annotations

import copy
import struct
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from fluidvoice import backends
from fluidvoice.backends.base import (
    BackendCapabilities,
    SpeechBackend,
    Transcript,
    TranscriptSegment,
    capabilities_of,
)
from fluidvoice.config import DEFAULTS
from tests.fake_remote_stt_server import FakeRemoteSttServer


def make_wav(path: Path, seconds: float = 1.0) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", 9000)
                               for _ in range(int(seconds * 16000))))
    return path


def cfg(**model_overrides) -> dict:
    c = copy.deepcopy(DEFAULTS)
    c["model"].update(model_overrides)
    return c


# ---------------------------------------------------------------------------
# The five adapters, built offline (fake model / stub sessions / fake
# server / patched subprocess — the patterns from test_backend_segments,
# test_parakeet_onnx, test_remote_stt, test_hotwords)
# ---------------------------------------------------------------------------

class FakeFwModel:
    """faster-whisper-style: (segment generator, info)."""

    def transcribe(self, path, **kw):
        def gen():
            yield SimpleNamespace(start=0.1, end=1.0, text=" And so",
                                  avg_logprob=-0.2, no_speech_prob=0.05)
            yield SimpleNamespace(start=1.5, end=2.0, text=" it goes.",
                                  avg_logprob=-0.4, no_speech_prob=0.1)
        return gen(), SimpleNamespace(language="en", duration=2.0)


def make_faster_whisper():
    from fluidvoice.backends.faster_whisper_backend import FasterWhisperBackend
    be = object.__new__(FasterWhisperBackend)
    be._model = FakeFwModel()
    be.language = None
    return be


def make_torch():
    from fluidvoice.backends.torch_whisper import TorchWhisperBackend

    class FakeWhisperModel:
        def transcribe(self, path, **kw):
            return {"text": " hi there ", "language": "en",
                    "segments": [{"start": 0.0, "end": 1.0, "text": " hi there "}]}

    be = object.__new__(TorchWhisperBackend)
    be._model = FakeWhisperModel()
    be.device = "cpu"
    be.language = None
    return be


def make_whisper_cpp(monkeypatch):
    from fluidvoice.backends import whisper_cpp as wc

    class Proc:
        returncode = 0
        stderr = ""
        stdout = "hello world\n"

    monkeypatch.setattr(wc.subprocess, "run", lambda *a, **kw: Proc())
    be = object.__new__(wc.WhisperCppBackend)
    be.binary = "/fake/whisper-cli"
    be.model = "/fake/model.bin"
    be.language = "auto"
    return be


def _pk_env(tmp_path, monkeypatch):
    """Parakeet fixture catalog + stub-graph model dir (same pattern as
    tests/test_parakeet_onnx.pk_env, inlined because that helper is a
    pytest fixture)."""
    from fluidvoice import model_catalog
    from tests.test_parakeet_onnx import PK_FIXTURE_NAME, TOKENS
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(
        model_catalog, "PARAKEET_CATALOG",
        {PK_FIXTURE_NAME: {
            "size": "~tiny", "langs": "en", "note": "fixture",
            "url": "http://fake/t.tar.bz2", "tarball_sha256": "0" * 64,
            "files": {"encoder.int8.onnx": "0" * 64,
                      "decoder.int8.onnx": "0" * 64,
                      "joiner.int8.onnx": "0" * 64,
                      "tokens.txt": "0" * 64},
            "features": {"sample_rate": 16000, "n_mels": 128,
                         "n_fft": 512, "win": 400, "hop": 160,
                         "fmin": 0.0, "fmax": 8000.0}}})
    monkeypatch.setattr(model_catalog, "PARAKEET_DEFAULT_MODEL",
                        PK_FIXTURE_NAME)
    d = model_catalog.parakeet_model_dir(PK_FIXTURE_NAME)
    d.mkdir(parents=True)
    for f in ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx"):
        (d / f).write_bytes(b"\x00")
    (d / "tokens.txt").write_text(TOKENS, encoding="utf-8")
    return SimpleNamespace(model_dir=d, tmp=tmp_path)


def pk_sessions_deterministic(_model_dir):
    """Like test_parakeet_onnx.sessions_stub, but EVERY transcription
    decodes to the same text (the encoder run resets the emit flag), so
    contract tests may transcribe the same backend repeatedly."""
    import numpy as np

    from tests.test_parakeet_onnx import (
        StubSession,
        logits_vec,
        make_decoder_stub,
    )
    V, D = 3, 2
    state = {"emitted": False}

    def enc_fn(s, feeds):
        state["emitted"] = False  # new transcription epoch
        return [np.zeros((1, 1024, 28), np.float32),
                np.array([28], np.int64)]

    enc = StubSession(["audio_signal", "length"],
                      ["outputs", "encoded_lengths"], fn=enc_fn)

    def jnt_fn(s, feeds):
        if not state["emitted"]:
            state["emitted"] = True
            return [logits_vec(1, 1, V, D)]  # emit "▁hello", advance 1
        return [logits_vec(2, 1, V, D)]      # blank from then on

    jnt = StubSession(["encoder_outputs", "decoder_outputs"], ["outputs"],
                      fn=jnt_fn)
    return enc, make_decoder_stub(), jnt, {
        "feat_dim": "128", "pred_hidden": "4", "pred_rnn_layers": "2",
        "normalize_type": "per_feature", "vocab_size": "3"}


def make_parakeet(pk_env):
    """Real construction through the _sessions test seam (stub ONNX
    graphs + fixture catalog from the parakeet suite's own pattern)."""
    from fluidvoice.backends.parakeet_onnx import ParakeetOnnxBackend
    name = "pk-tiny"
    return ParakeetOnnxBackend(cfg(name=name),
                               _sessions=pk_sessions_deterministic), name


def make_remote():
    srv = FakeRemoteSttServer().start()
    be = backends.RemoteSttBackend(
        cfg(backend="remote", remote_url=srv.url))
    return be, srv


@pytest.fixture(params=["faster-whisper", "whisper-torch", "whisper.cpp",
                        "parakeet", "remote"])
def adapter(request, tmp_path, monkeypatch):
    """(backend, wav, teardown) for one of the five adapters."""
    if request.param == "faster-whisper":
        yield make_faster_whisper(), make_wav(tmp_path / "a.wav"), None
    elif request.param == "whisper-torch":
        yield make_torch(), make_wav(tmp_path / "a.wav"), None
    elif request.param == "whisper.cpp":
        be = make_whisper_cpp(monkeypatch)
        yield be, make_wav(tmp_path / "a.wav"), None
    elif request.param == "parakeet":
        env = _pk_env(tmp_path, monkeypatch)
        be, _ = make_parakeet(env)
        # the stub graph decodes any wav: write the parakeet suite's
        # 16 kHz int16 fixture (0.3 s)
        from tests.test_parakeet_onnx import write_wav
        yield be, write_wav(env.tmp / "a.wav"), None
    else:
        be, srv = make_remote()
        yield be, make_wav(tmp_path / "a.wav"), srv


# ---------------------------------------------------------------------------
# Capabilities declared (truthfully — this table was verified against
# each adapter's code, not guessed)
# ---------------------------------------------------------------------------

EXPECTED_CAPABILITIES = {
    "faster-whisper": dict(supports_language_select=True,
                           supports_language_detect=True,
                           supports_hotwords=True,     # native decoder param
                           supports_streaming=False,
                           supports_segments=True,
                           supports_confidence=True,  # avg_logprob+no_speech
                           supports_alternatives=False),
    "whisper-torch": dict(supports_language_select=True,
                          supports_language_detect=True,
                          supports_hotwords=True,     # initial_prompt hint
                          supports_streaming=False,
                          supports_segments=True,
                          supports_confidence=False,
                          supports_alternatives=False),
    "whisper.cpp": dict(supports_language_select=True,
                        supports_language_detect=False,
                        supports_hotwords=False,
                        supports_streaming=False,
                        supports_segments=False,
                        supports_confidence=False,
                        supports_alternatives=False),
    "parakeet": dict(supports_language_select=False,
                     supports_language_detect=False,
                     supports_hotwords=False,
                     supports_streaming=False,
                     supports_segments=True,         # one whole-audio segment
                     supports_confidence=False,
                     supports_alternatives=False),
    "remote": dict(supports_language_select=True,
                   supports_language_detect=False,
                   supports_hotwords=False,
                   supports_streaming=False,
                   supports_segments=False,          # passthrough only
                   supports_confidence=False,
                   supports_alternatives=False),
}


def adapter_classes():
    from fluidvoice.backends.faster_whisper_backend import FasterWhisperBackend
    from fluidvoice.backends.parakeet_onnx import ParakeetOnnxBackend
    from fluidvoice.backends.remote_stt import RemoteSttBackend
    from fluidvoice.backends.torch_whisper import TorchWhisperBackend
    from fluidvoice.backends.whisper_cpp import WhisperCppBackend
    return [FasterWhisperBackend, TorchWhisperBackend, WhisperCppBackend,
            ParakeetOnnxBackend, RemoteSttBackend]


@pytest.mark.parametrize("cls", adapter_classes(),
                         ids=lambda c: c.name)
class TestCapabilitiesDeclared:
    def test_declared_and_truthful(self, cls):
        caps = cls.capabilities
        assert isinstance(caps, BackendCapabilities)
        assert type(caps).__hash__ is not None  # frozen: hashable + immutable
        expected = BackendCapabilities(
            **EXPECTED_CAPABILITIES[cls.name])
        assert caps == expected, cls.name

    def test_legacy_flags_agree(self, cls):
        """The pre-seam duck-typed flags (still read by capabilities_of's
        fallback and asserted elsewhere) must mirror the typed caps."""
        caps = cls.capabilities
        assert caps.supports_language_select == getattr(
            cls, "selects_language", False)
        assert caps.supports_language_detect == getattr(
            cls, "surfaces_detected_language", False)

    def test_streaming_and_alternatives_off_everywhere(self, cls):
        # plan P3: no streaming interface and no n-best until a real
        # adapter needs them - the seam must not lie about the future
        assert cls.capabilities.supports_streaming is False
        assert cls.capabilities.supports_alternatives is False

    def test_lifecycle_methods_exist(self, cls):
        # the SpeechBackend protocol surface: transcribe/warmup/close
        for m in ("transcribe", "warmup", "close"):
            assert callable(getattr(cls, m, None)), (cls.name, m)


# ---------------------------------------------------------------------------
# Transcript invariants (the same assertions over every adapter's real
# transcribe output)
# ---------------------------------------------------------------------------

class TestTranscriptInvariants:
    def test_returns_typed_transcript(self, adapter):
        be, wav, _ = adapter
        out = be.transcribe(wav, language="auto")
        assert isinstance(out, Transcript)

    def test_text_is_str(self, adapter):
        be, wav, _ = adapter
        t = be.transcribe(wav, language="auto")
        assert isinstance(t.text, str)

    def test_segments_typed_ordered_and_bounded(self, adapter):
        be, wav, _ = adapter
        t = be.transcribe(wav, language="auto")
        assert isinstance(t.segments, tuple)
        for s in t.segments:
            assert isinstance(s, TranscriptSegment)
            assert isinstance(s.start, float) and isinstance(s.end, float)
            assert isinstance(s.text, str)
            assert s.start <= s.end + 1e-9
            # optional confidence signals are float|None, never other junk
            assert s.avg_logprob is None or isinstance(s.avg_logprob, float)
            assert s.no_speech_prob is None \
                or isinstance(s.no_speech_prob, float)
        # start times are non-decreasing (ordered segments)
        starts = [s.start for s in t.segments]
        assert starts == sorted(starts)

    def test_language_and_duration_typing(self, adapter):
        be, wav, _ = adapter
        t = be.transcribe(wav, language="auto")
        assert t.language is None or isinstance(t.language, str)
        assert t.duration is None or isinstance(t.duration, float)

    def test_segments_presence_matches_capability(self, adapter):
        be, wav, _ = adapter
        caps = capabilities_of(be)
        t = be.transcribe(wav, language="auto")
        if not caps.supports_segments:
            assert t.segments == ()
        if not caps.supports_confidence:
            assert all(s.avg_logprob is None and s.no_speech_prob is None
                       for s in t.segments)


# ---------------------------------------------------------------------------
# Missing optional fields degrade cleanly (pre-seam result shapes)
# ---------------------------------------------------------------------------

class TestMissingOptionalFields:
    def test_empty_result_dict_parses(self):
        t = Transcript.of({})
        assert t.text == "" and t.language is None
        assert t.duration is None and t.segments == ()

    def test_segments_without_confidence_keys(self):
        t = Transcript.of({"text": "x", "segments": [
            {"start": 0.0, "end": 1.0, "text": "x"}]})
        assert t.segments[0].avg_logprob is None
        assert t.segments[0].no_speech_prob is None

    def test_nonnumeric_confidence_ignored(self):
        t = Transcript.of({"text": "x", "segments": [
            {"start": 0.0, "end": 1.0, "text": "x",
             "avg_logprob": "bad", "no_speech_prob": True}]})
        assert t.segments[0].avg_logprob is None
        assert t.segments[0].no_speech_prob is None  # bool is not a signal

    def test_non_dict_segment_entries_dropped(self):
        t = Transcript.of({"text": "x", "segments": [
            "junk", {"start": 0.0, "end": 1.0, "text": "ok"}, 7]})
        assert len(t.segments) == 1
        assert t.segments[0].text == "ok"

    def test_unknown_extra_keys_tolerated(self):
        # remote verbose_json segments carry id/seek/tokens/...
        t = Transcript.of({"text": "x", "segments": [
            {"start": 0.0, "end": 1.0, "text": "x",
             "id": 3, "seek": 42, "tokens": [1, 2]}]})
        assert t.segments[0].text == "x"

    def test_torch_result_without_segments_key(self):
        t = Transcript.of({"text": "words", "language": "en"})
        assert t.segments == () and t.text == "words"

    def test_of_rejects_non_results(self):
        with pytest.raises(TypeError):
            Transcript.of("words")


# ---------------------------------------------------------------------------
# Serialization round trips + the legacy dict view
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_round_trip_after_transcribe(self, adapter):
        be, wav, _ = adapter
        t = be.transcribe(wav, language="auto")
        assert Transcript.from_dict(t.to_dict()) == t

    @pytest.mark.parametrize("t", [
        Transcript(text="hello"),
        Transcript(text="hello", language="de", duration=2.5),
        Transcript(text="a b", segments=(
            TranscriptSegment(0.0, 1.0, "a", -0.3, 0.05),
            TranscriptSegment(1.0, 2.0, "b", -0.9, None))),
        Transcript(text="", segments=(TranscriptSegment(0.0, 0.0, ""),)),
    ])
    def test_round_trip_synthetic(self, t):
        assert Transcript.from_dict(t.to_dict()) == t

    def test_dict_view_is_the_historical_shape(self, adapter):
        be, wav, _ = adapter
        t = be.transcribe(wav, language="auto")
        d = t.to_dict()
        assert set(d) == {"text", "language", "duration", "segments"}
        assert isinstance(d["segments"], list)

    def test_mapping_shim_legacy_access(self, adapter):
        be, wav, _ = adapter
        t = be.transcribe(wav, language="auto")
        assert t["text"] == t.text
        assert t.get("language") == t.language
        assert t.get("missing", "dflt") == "dflt"
        assert "duration" in t and "text" in t
        assert set(t) == {"text", "language", "duration", "segments"}
        with pytest.raises(KeyError):
            t["nope"]

    def test_plain_text(self, adapter):
        be, wav, _ = adapter
        t = be.transcribe(wav, language="auto")
        assert t.to_plain_text() == t.text


# ---------------------------------------------------------------------------
# Unsupported capability paths degrade cleanly
# ---------------------------------------------------------------------------

class TestDegradation:
    def test_no_confidence_signals_give_band_none(self, adapter):
        be, wav, _ = adapter
        if capabilities_of(be).supports_confidence:
            pytest.skip("adapter has confidence signals")
        from fluidvoice.pipeline import confidence_band
        assert confidence_band(be.transcribe(wav, language="auto")) is None

    def test_no_segments_degrade_to_repetition_only_guard(self, adapter):
        be, wav, _ = adapter
        if capabilities_of(be).supports_segments:
            pytest.skip("adapter has segments")
        from fluidvoice.pipeline import looks_like_hallucination
        assert looks_like_hallucination(be.transcribe(wav,
                                                      language="auto")) is False

    def test_capabilities_of_fakes_defaults_off(self):
        class LegacyFake:  # pre-seam duck-typed backend, no flags at all
            name = "fake"

            def transcribe(self, wav, language=None):
                return {"text": "x"}

        caps = capabilities_of(LegacyFake())
        assert caps == BackendCapabilities()

    def test_capabilities_of_legacy_flags(self):
        class ParakeetClassFake:  # sets the legacy flags only
            name = "fake"
            selects_language = False
            surfaces_detected_language = False

        caps = capabilities_of(ParakeetClassFake())
        assert caps.supports_language_select is False
        assert caps.supports_language_detect is False

        class TorchClassFake:
            selects_language = True
            surfaces_detected_language = True

        caps = capabilities_of(TorchClassFake())
        assert caps == BackendCapabilities(supports_language_select=True,
                                           supports_language_detect=True)

    def test_capabilities_of_none(self):
        assert capabilities_of(None) == BackendCapabilities()

    def test_adapters_satisfy_protocol_shape(self, adapter):
        be, _, _ = adapter
        assert isinstance(be, SpeechBackend)  # runtime-checkable protocol


# ---------------------------------------------------------------------------
# Byte-compat evidence for the serialized edges (goldens the brief asks
# to prove unchanged)
# ---------------------------------------------------------------------------

class TestEdgeGoldens:
    def test_cli_json_payload_shape(self, adapter, monkeypatch, tmp_path,
                                    capsys):
        """The CLI --json payload must serialize to the same structure a
        legacy dict backend produced: text/language/duration_s/segments,
        segment optionals omitted when absent."""
        from fluidvoice import cli
        be, wav, _ = adapter
        monkeypatch.setattr(backends, "load_backend", lambda c: be)
        monkeypatch.setattr(cli, "load_config",
                            lambda p=None: copy.deepcopy(DEFAULTS))
        rc = cli.main(["transcribe", str(wav), "--no-process", "--json"])
        out = capsys.readouterr()
        assert rc == 0
        import json
        payload = json.loads(out.out)
        assert set(payload) == {"text", "language", "duration_s", "segments"}
        assert isinstance(payload["segments"], list)
        t = be.transcribe(wav, language="auto")
        assert payload["text"] == t.text
        assert payload["language"] == t.language
        assert payload["duration_s"] == t.duration
        assert payload["segments"] == [s.to_dict() for s in t.segments]

    def test_transcript_hashable_and_frozen(self):
        t = Transcript(text="x", segments=(TranscriptSegment(0.0, 1.0, "x"),))
        assert isinstance(hash(t), int)
        with pytest.raises(Exception):  # frozen dataclass: no mutation
            t.text = "y"
