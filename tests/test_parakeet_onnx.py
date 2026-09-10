"""Parakeet ONNX backend unit tests — no onnxruntime import anywhere:
sessions are duck-typed stubs, the catalog is a tiny fixture."""
from __future__ import annotations

import math
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from fluidvoice import model_catalog
from fluidvoice.backends.parakeet_onnx import (
    LogMelFeaturizer,
    ParakeetOnnxBackend,
    TdtGreedyDecoder,
    detokenize,
    load_tokens_txt,
)

REPO = Path(__file__).resolve().parents[1]


def sine(freq=1000.0, dur=0.1, amp=0.5, rate=16000) -> np.ndarray:
    t = np.arange(int(rate * dur)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def write_wav(path, rate=16000, dur=0.3, freq=1000.0) -> Path:
    t = np.arange(int(rate * dur)) / rate
    samples = (0.5 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return path


class TestLogMelFeaturizer:
    feat = LogMelFeaturizer()

    def test_shape_8x128_for_tenth_of_a_second(self):
        assert self.feat(sine()).shape == (8, 128)

    def test_frame0_power_peaks_at_bin_32(self):
        # (A/2 * sum(hann))^2 = (0.25*200)^2 = 2500 at the 1 kHz bin
        frame = sine()[:400] * self.feat.window
        power = np.abs(np.fft.rfft(frame, 512)) ** 2
        assert int(np.argmax(power)) == 32
        assert abs(power[32] - 2500.0) < 0.01

    def test_pinned_first_three_logmel_values(self):
        # pinned from the verified reference decode (the executable in
        # adws/.../parakeet_reference_decode.py, executed against the real
        # models): this featurizer matches it to <1e-6. (The plan's §1.5
        # floats differed by ~5e-3 and were not reproducible by any
        # window/precision/input variant; the reference wins.)
        feats = self.feat(sine())
        assert np.allclose(feats[0, :3],
                           [-16.84995931, -16.30331655, -15.92941702],
                           atol=1e-4)

    def test_silence_hits_the_log_floor_exactly(self):
        feats = self.feat(np.zeros(1600, np.float32))
        assert feats.shape == (8, 128)
        assert np.allclose(feats, math.log(1e-10))

    def test_framing_counts(self):
        assert self.feat(np.zeros(560, np.float32)).shape[0] == 2
        assert self.feat(np.zeros(399, np.float32)).shape == (0, 128)

    def test_tone_lands_in_mel_row_42(self):
        feats = self.feat(sine())
        assert int(np.argmax(feats[0])) == 42

    def test_matches_naive_reference(self):
        """Independent double-loop implementation of the same spec."""
        sr, n_mels, n_fft, win, hop = 16000, 128, 512, 400, 160
        fmin, fmax = 0.0, 8000.0
        t = np.arange(4000) / sr
        samples = (0.4 * np.sin(2 * np.pi * 440 * t)
                   + 0.2 * np.sin(2 * np.pi * 3000 * t)).astype(np.float32)

        def h2m(f):
            f_sp = 200.0 / 3
            if f >= 1000.0:
                return 1000.0 / f_sp + math.log(f / 1000.0) / (math.log(6.4) / 27.0)
            return f / f_sp

        def m2h(m):
            f_sp = 200.0 / 3
            lin = m * f_sp
            log = 1000.0 * math.exp((math.log(6.4) / 27.0) * (m - 1000.0 / f_sp))
            return log if m >= 1000.0 / f_sp else lin

        mpts = [h2m(fmin) + (h2m(fmax) - h2m(fmin)) * i / (n_mels + 1)
                for i in range(n_mels + 2)]
        hz = [m2h(m) for m in mpts]
        freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
        weights = np.zeros((n_mels, len(freqs)))
        for i in range(n_mels):
            for k, f in enumerate(freqs):
                lower = (f - hz[i]) / (hz[i + 1] - hz[i])
                upper = (hz[i + 2] - f) / (hz[i + 2] - hz[i + 1])
                weights[i, k] = max(0.0, min(lower, upper))
            weights[i] *= 2.0 / (hz[i + 2] - hz[i])

        hann = [0.5 - 0.5 * math.cos(2 * math.pi * n / win)
                for n in range(win)]
        out = []
        nframes = 1 + (len(samples) - win) // hop
        for i in range(nframes):
            frame = np.array([samples[i * hop + j] * hann[j]
                              for j in range(win)] + [0.0] * (n_fft - win))
            power = np.abs(np.fft.rfft(frame)) ** 2
            out.append([math.log(max(float(power @ weights[m]), 1e-10))
                        for m in range(n_mels)])
        got = self.feat(samples)
        naive = np.array(out, dtype=np.float32)
        # bins with real energy agree tightly; near-floor bins (sidelobe
        # cancellation) differ by FFT ulps, bounded below
        sig = naive > -16.0
        assert sig.any()
        assert np.allclose(naive[sig], got[sig], atol=1e-4)
        assert float(np.abs(naive - got).max()) < 5e-3


class TestTokens:
    TOKENS = "<unk> 0\n▁hello 1\n▁world 2\n, 3\n! 4\n▁ 5\n<blk> 6\n"

    def test_load_tokens_blank_is_last(self, tmp_path):
        p = tmp_path / "tokens.txt"
        p.write_text(self.TOKENS, encoding="utf-8")
        id2tok, blank = load_tokens_txt(p)
        assert blank == 6 and id2tok[6] == "<blk>"

    def test_detokenize(self, tmp_path):
        p = tmp_path / "tokens.txt"
        p.write_text(self.TOKENS, encoding="utf-8")
        id2tok, _ = load_tokens_txt(p)
        assert detokenize([1, 2], id2tok) == "hello world"
        assert detokenize([1, 3, 2], id2tok) == "hello, world"
        # runs of sentence-piece spaces collapse; outer spaces strip
        assert detokenize([5, 1, 5, 5, 2, 5], id2tok) == "hello world"
        assert detokenize([], id2tok) == ""


# -- duck-typed session stubs -------------------------------------------------

class StubSession:
    """Scripted outputs (popped per run) or a fn(session, feeds) callback."""

    def __init__(self, in_names, out_names, script=None, fn=None):
        self._in = [SimpleNamespace(name=n) for n in in_names]
        self._out = [SimpleNamespace(name=n) for n in out_names]
        self.script = list(script or [])
        self.fn = fn
        self.calls = 0
        self.feeds: list[dict] = []

    def get_inputs(self):
        return self._in

    def get_outputs(self):
        return self._out

    def run(self, names, feeds):
        self.calls += 1
        self.feeds.append(feeds)
        if self.fn is not None:
            return self.fn(self, feeds)
        return self.script.pop(0)


def logits_vec(tok: int, dur: int, V: int, D: int, dur_val: float = 2.0) -> np.ndarray:
    v = np.zeros(V + D, np.float32)
    v[tok] = 1.0
    if dur is not None:
        v[V + dur] = dur_val
    return v.reshape(1, 1, 1, V + D)


def make_decoder_stub():
    return StubSession(
        ["targets", "target_length", "state0", "state1"],
        ["outputs", "prednet_lengths", "state0_next", "state1_next"],
        fn=lambda s, f: [np.zeros((1, 640, 1), np.float32),
                         np.array([1], np.int32),
                         np.zeros((2, 1, 4), np.float32),
                         np.zeros((2, 1, 4), np.float32)])


def make_encoder_stub(T_prime: int):
    return StubSession(
        ["audio_signal", "length"], ["outputs", "encoded_lengths"],
        fn=lambda s, f: [np.zeros((1, 1024, T_prime), np.float32),
                         np.array([T_prime], np.int64)])


def joiner_scripted(*vecs):
    return StubSession(["encoder_outputs", "decoder_outputs"], ["outputs"],
                       script=[[v] for v in vecs])


class TestTdtGreedyDecoder:
    V, D = 9, 4  # blank = 8

    def decoder(self, enc, dec, jnt, max_tpf=4):
        return TdtGreedyDecoder(enc, dec, jnt,
                                {"pred_hidden": 4, "pred_rnn_layers": 2,
                                 "vocab_size": self.V},
                                max_tokens_per_frame=max_tpf)

    def test_emission_sequence_blank_skip_clamp_and_jump(self):
        enc = make_encoder_stub(4)
        dec = make_decoder_stub()
        jnt = joiner_scripted(
            logits_vec(8, 2, self.V, self.D),   # t=0: blank, skip 2 -> t=2
            logits_vec(3, 0, self.V, self.D),   # t=2: emit 3, dur 0 -> 1, t=3
            logits_vec(5, 3, self.V, self.D),   # t=3: emit 5, jump 3 -> t=6
        )
        feats = np.zeros((10, 128), np.float32)
        assert self.decoder(enc, dec, jnt).run(feats) == [3, 5]
        assert jnt.calls == 3 and dec.calls == 3  # blank-pred + one per emit
        assert [f["targets"].tolist() for f in dec.feeds] == \
            [[[8]], [[3]], [[5]]]

    def test_logits_split_token_vs_duration(self):
        enc = make_encoder_stub(2)
        dec = make_decoder_stub()
        # global argmax is the duration slot (10); the split must take the
        # token from [:V] (4) and the duration from [V:] (1)
        jnt = joiner_scripted(
            logits_vec(4, 1, self.V, self.D),
            logits_vec(8, 1, self.V, self.D),
        )
        assert self.decoder(enc, dec, jnt).run(
            np.zeros((8, 128), np.float32)) == [4]
        assert dec.feeds[1]["targets"].tolist() == [[4]]

    def test_always_emit_never_hangs(self):
        enc = make_encoder_stub(5)
        dec = make_decoder_stub()
        jnt = StubSession(["encoder_outputs", "decoder_outputs"], ["outputs"],
                          fn=lambda s, f: [logits_vec(0, 1, self.V, self.D)])
        out = self.decoder(enc, dec, jnt).run(np.zeros((40, 128), np.float32))
        assert out == [0] * 5  # one emission per frame, bounded, no hang
        assert jnt.calls == 5 and dec.calls == 6


# -- backend over fixture catalog + stub graphs -------------------------------

PK_FIXTURE_NAME = "pk-tiny"
TOKENS = "<unk> 0\n▁hello 1\n<blk> 2\n"


@pytest.fixture()
def pk_env(tmp_path, monkeypatch):
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
            "features": {"sample_rate": 16000, "n_mels": 128, "n_fft": 512,
                         "win": 400, "hop": 160, "fmin": 0.0,
                         "fmax": 8000.0}}})
    monkeypatch.setattr(model_catalog, "PARAKEET_DEFAULT_MODEL",
                        PK_FIXTURE_NAME)
    d = model_catalog.parakeet_model_dir(PK_FIXTURE_NAME)
    d.mkdir(parents=True)
    for f in ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx"):
        (d / f).write_bytes(b"\x00")
    (d / "tokens.txt").write_text(TOKENS, encoding="utf-8")
    return SimpleNamespace(model_dir=d, tmp=tmp_path)


def sessions_stub(_model_dir):
    V, D = 3, 2
    enc = make_encoder_stub(28)

    def jnt_fn(s, feeds):
        if s.calls == 1:
            return [logits_vec(1, 1, V, D)]  # emit "hello", advance 1
        return [logits_vec(2, 1, V, D)]      # blank from then on
    jnt = StubSession(["encoder_outputs", "decoder_outputs"], ["outputs"],
                      fn=jnt_fn)
    return enc, make_decoder_stub(), jnt, {
        "feat_dim": "128", "pred_hidden": "4", "pred_rnn_layers": "2",
        "normalize_type": "per_feature", "vocab_size": "3"}


def cfg(**model_overrides):
    import copy

    from fluidvoice.config import DEFAULTS
    c = copy.deepcopy(DEFAULTS)
    c["model"].update(model_overrides)
    return c


class TestParakeetOnnxBackend:
    def test_name_resolution(self, pk_env):
        be = ParakeetOnnxBackend(cfg(name=""), _sessions=sessions_stub)
        assert be.model_name == PK_FIXTURE_NAME
        be = ParakeetOnnxBackend(cfg(name="auto"), _sessions=sessions_stub)
        assert be.model_name == PK_FIXTURE_NAME
        be = ParakeetOnnxBackend(cfg(name=PK_FIXTURE_NAME),
                                 _sessions=sessions_stub)
        assert be.model_name == PK_FIXTURE_NAME
        with pytest.raises(ValueError, match="unknown parakeet model"):
            ParakeetOnnxBackend(cfg(name="nope"), _sessions=sessions_stub)
        try:
            ParakeetOnnxBackend(cfg(name="nope"), _sessions=sessions_stub)
        except ValueError as e:
            assert PK_FIXTURE_NAME in str(e)

    def test_not_downloaded_mentions_settings(self, pk_env):
        import shutil
        shutil.rmtree(pk_env.model_dir)
        with pytest.raises(RuntimeError, match="not downloaded"):
            ParakeetOnnxBackend(cfg(name=PK_FIXTURE_NAME))
        try:
            ParakeetOnnxBackend(cfg(name=PK_FIXTURE_NAME))
        except RuntimeError as e:
            assert "Settings" in str(e)

    def test_transcribe_contract(self, pk_env):
        wav = write_wav(pk_env.tmp / "t.wav", dur=0.3)
        be = ParakeetOnnxBackend(cfg(name=PK_FIXTURE_NAME),
                                 _sessions=sessions_stub)
        out = be.transcribe(wav)
        assert set(out) == {"text", "language", "duration", "segments"}
        assert out["text"] == "hello"
        assert out["duration"] == pytest.approx(0.3)
        assert out["segments"] == [{"start": 0.0, "end": 0.3,
                                    "text": "hello"}]
        assert out["language"] is None  # cfg language "auto" -> None
        assert be.transcribe(wav, language="de")["language"] == "de"
        assert be.transcribe(wav, language="auto")["language"] is None

    def test_wrong_rate_rejected(self, pk_env):
        wav = write_wav(pk_env.tmp / "t8k.wav", rate=8000)
        be = ParakeetOnnxBackend(cfg(name=PK_FIXTURE_NAME),
                                 _sessions=sessions_stub)
        with pytest.raises(RuntimeError, match="8000 Hz"):
            be.transcribe(wav)

    def test_module_import_leaves_onnxruntime_out(self):
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; import fluidvoice.backends.parakeet_onnx as m; "
             "assert 'onnxruntime' not in sys.modules, 'onnxruntime leaked'; "
             "print('clean')"],
            capture_output=True, text=True, cwd=str(REPO))
        assert out.returncode == 0, out.stderr
        assert "clean" in out.stdout
