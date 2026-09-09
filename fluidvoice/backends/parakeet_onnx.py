"""Parakeet TDT via ONNX Runtime — numpy featurizer + greedy TDT decode.

Runs the sherpa-onnx community exports of nvidia/parakeet-tdt-0.6b-v2/v3
(int8) fully offline. No NeMo/torchaudio/librosa dependency: featurization
and the token-and-duration decode are pure numpy.

Graph contract (identical for v2/v3; bind I/O POSITIONALLY — tensor names
vary across exports, e.g. the decoder state input may literally be
`onnx::Slice_3`):

  encoder  in : audio_signal [1,128,T] f32 , length [1] i64
             out: outputs [1,1024,T'] f32    , encoded_lengths [1] i64
             custom_metadata_map: feat_dim, vocab_size, pred_hidden,
                                  pred_rnn_layers, normalize_type
                                  (per_feature), subsampling_factor, ...
  decoder  in : targets [1,1] i32, target_length [1] i32,
                state0/state1 [pred_rnn_layers,1,pred_hidden] f32
                (zeros at start)
             out: outputs [1,640,1] f32, prednet_lengths,
                  state0_next, state1_next
  joiner   in : encoder_outputs [1,1024,1] f32, decoder_outputs [1,640,1] f32
             out: outputs [1,1,1,V+D] f32  (V = vocab incl. blank,
                  D = TDT duration bins) — token_logits = logits[:V],
                  duration_logits = logits[V:]

This module imports only stdlib + numpy + model_catalog at module level;
`onnxruntime` is imported inside `_default_sessions()` so environments
without it can still load the package (import hygiene is tested).
"""
from __future__ import annotations

import math
import re
import wave
from pathlib import Path
from typing import Any

import numpy as np

from .. import model_catalog
from . import effective_language

TAIL_PAD_S = 2.0  # seconds of silence appended before featurization
SAMPLE_RATE = 16000


# ---------------------------------------------------------------------------
# Slaney mel filterbank (librosa defaults: htk=False, norm='slaney')
# ---------------------------------------------------------------------------

def _hz_to_mel(f: float) -> float:
    f_sp = 200.0 / 3
    if f >= 1000.0:
        return (1000.0 / f_sp) + math.log(f / 1000.0) / (math.log(6.4) / 27.0)
    return f / f_sp


def _mel_to_hz(m: np.ndarray) -> np.ndarray:
    f_sp = 200.0 / 3
    min_log_mel = 1000.0 / f_sp
    logstep = math.log(6.4) / 27.0
    lin = np.asarray(m, dtype=float) * f_sp
    log = 1000.0 * np.exp(logstep * (np.asarray(m, dtype=float) - min_log_mel))
    return np.where(np.asarray(m) >= min_log_mel, log, lin)


def slaney_mel_fb(sr: int, n_fft: int, n_mels: int,
                  fmin: float, fmax: float) -> np.ndarray:
    """(n_mels, n_fft//2+1) Slaney-normalized mel filterbank matrix."""
    mpts = np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_mels + 2)
    hz = _mel_to_hz(mpts)
    fftf = np.fft.rfftfreq(n_fft, 1.0 / sr)
    ramps = np.subtract.outer(hz, fftf)          # (n_mels+2, bins)
    fdiff = np.diff(hz)                          # (n_mels+1,)
    weights = np.zeros((n_mels, fftf.size))
    for i in range(n_mels):
        lower = -ramps[i] / fdiff[i]
        upper = ramps[i + 2] / fdiff[i + 1]
        weights[i] = np.maximum(0.0, np.minimum(lower, upper))
        weights[i] *= 2.0 / (hz[i + 2] - hz[i])  # slaney area normalization
    return weights


class LogMelFeaturizer:
    """NeMo-compatible log-mel: periodic hann, no centering, natural log
    with a 1e-10 floor. Returns (T, n_mels) float32."""

    def __init__(self, sample_rate: int = 16000, n_mels: int = 128,
                 n_fft: int = 512, win: int = 400, hop: int = 160,
                 fmin: float = 0.0, fmax: float = 8000.0):
        self.sample_rate = sample_rate
        self.n_mels = n_mels
        self.n_fft = n_fft
        self.win = win
        self.hop = hop
        self.fmin = fmin
        self.fmax = fmax
        self.fb = slaney_mel_fb(sample_rate, n_fft, n_mels, fmin, fmax)
        self.window = 0.5 - 0.5 * np.cos(  # periodic hann
            2.0 * np.pi * np.arange(win) / win)

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        samples = np.asarray(samples, dtype=np.float32)
        n = samples.shape[0]
        nframes = 1 + (n - self.win) // self.hop if n >= self.win else 0
        if nframes == 0:
            return np.zeros((0, self.n_mels), dtype=np.float32)
        idx = np.arange(self.win)[None, :] + self.hop * np.arange(nframes)[:, None]
        frames = samples[idx] * self.window[None, :]
        spec = np.fft.rfft(frames, self.n_fft, axis=1)  # zero-pad win->n_fft
        power = spec.real ** 2 + spec.imag ** 2
        mel = power @ self.fb.T
        return np.log(np.maximum(mel, 1e-10)).astype(np.float32)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def load_tokens_txt(path: Path) -> tuple[dict[int, str], int]:
    """Parse a sherpa tokens.txt (`<piece> <id>` per line; the LAST id is
    the RNN-T blank). Returns (id2tok, blank_id)."""
    id2tok: dict[int, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            tok, idx = line.rsplit(" ", 1)
            id2tok[int(idx)] = tok
    return id2tok, len(id2tok) - 1


def detokenize(ids: list[int], id2tok: dict[int, str]) -> str:
    """Join sentencepieces: ▁ -> space, collapse space runs, strip."""
    text = "".join(id2tok.get(i, "") for i in ids).replace("▁", " ")
    return re.sub(r" +", " ", text).strip()


# ---------------------------------------------------------------------------
# Greedy TDT (token-and-duration) decode
# ---------------------------------------------------------------------------

class TdtGreedyDecoder:
    """Greedy TDT decode over duck-typed sessions (anything with
    .run(output_names, feeds) / .get_inputs() / .get_outputs(), i.e.
    onnxruntime sessions or test stubs). I/O is bound positionally."""

    def __init__(self, encoder: Any, decoder: Any, joiner: Any, meta: dict,
                 max_tokens_per_frame: int = 4):
        self.encoder = encoder
        self.decoder = decoder
        self.joiner = joiner
        self.pred_hidden = int(meta["pred_hidden"])
        self.pred_rnn_layers = int(meta["pred_rnn_layers"])
        self.vocab = int(meta["vocab_size"])
        self.blank = self.vocab - 1
        self.max_tokens_per_frame = max_tokens_per_frame

    def _predictor(self, token: int, s0: np.ndarray, s1: np.ndarray):
        d = self.decoder
        return d.run([o.name for o in d.get_outputs()[:4]],
                     {d.get_inputs()[0].name: np.array([[token]], np.int32),
                      d.get_inputs()[1].name: np.array([1], np.int32),
                      d.get_inputs()[2].name: s0,
                      d.get_inputs()[3].name: s1})

    def run(self, feats: np.ndarray) -> list[int]:
        x = feats.T[None, :, :].astype(np.float32)   # (T, C) -> [1, C, T]
        xlens = np.array([x.shape[2]], dtype=np.int64)
        e = self.encoder
        enc_out, _ = e.run([e.get_outputs()[0].name, e.get_outputs()[1].name],
                           {e.get_inputs()[0].name: x,
                            e.get_inputs()[1].name: xlens})
        frames = int(enc_out.shape[2])
        s0 = np.zeros((self.pred_rnn_layers, 1, self.pred_hidden), np.float32)
        s1 = np.zeros_like(s0)
        dec_out, _, s0n, s1n = self._predictor(self.blank, s0, s1)
        j = self.joiner
        t, emitted = 0, []
        max_tokens = self.max_tokens_per_frame * frames + 16
        while t < frames:
            logits = j.run([j.get_outputs()[0].name],
                           {j.get_inputs()[0].name: enc_out[:, :, t:t + 1],
                            j.get_inputs()[1].name: dec_out})[0].squeeze()
            tok_logits = logits[:self.vocab]
            dur_logits = logits[self.vocab:]
            idx = int(np.argmax(tok_logits))
            skip = max(int(np.argmax(dur_logits)), 1)  # duration 0 -> 1
            if idx != self.blank:
                emitted.append(idx)
                s0, s1 = s0n, s1n
                dec_out, _, s0n, s1n = self._predictor(idx, s0, s1)
                if len(emitted) >= max_tokens:  # runaway-guard
                    break
            t += skip
        return emitted


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class ParakeetOnnxBackend:
    name = "parakeet"
    # wrong-language guard: not applicable - the English-only v2 model
    # has no language selection at all (upstream #100 closure rationale)
    surfaces_detected_language = False
    # hallucination guard: no language selection to retry with either
    selects_language = False

    def __init__(self, cfg: dict, _sessions: Any | None = None):
        mcfg = cfg.get("model", {})
        name = str(mcfg.get("name") or "").strip()
        if name in ("", "auto"):
            name = model_catalog.PARAKEET_DEFAULT_MODEL
        if name not in model_catalog.PARAKEET_CATALOG:
            raise ValueError(
                f"unknown parakeet model '{name}' "
                f"(choose from {sorted(model_catalog.PARAKEET_CATALOG)})")
        self.model_name = name
        if not model_catalog.parakeet_downloaded(name):
            raise RuntimeError(
                f"parakeet model '{name}' not downloaded yet "
                f"(expected at {model_catalog.parakeet_model_dir(name)}) — "
                f"download it in Settings → Models, Parakeet (ONNX)")
        self.language = effective_language(cfg) or None
        # device/compute are accepted but ignored: the ONNX Runtime provider
        # list (CUDA EP when the installed wheel has it) is the source of truth
        self._device = mcfg.get("device")
        self._compute = mcfg.get("compute")
        self._sessions = _sessions  # test seam: callable(model_dir)
        self._decoder: TdtGreedyDecoder | None = None
        self._id2tok: dict[int, str] | None = None
        self._featurizer: LogMelFeaturizer | None = None
        self._normalize_type = ""

    def warmup(self) -> None:
        self._load()

    def _default_sessions(self, model_dir: Path):
        import onnxruntime as ort  # deferred: optional dependency

        providers = [p for p in ("CUDAExecutionProvider",
                                 "CPUExecutionProvider")
                     if p in ort.get_available_providers()]

        def make(fname: str):
            return ort.InferenceSession(str(model_dir / fname),
                                        providers=providers)

        enc = make("encoder.int8.onnx")
        meta = dict(enc.get_modelmeta().custom_metadata_map)
        return enc, make("decoder.int8.onnx"), make("joiner.int8.onnx"), meta

    def _load(self) -> None:
        if self._decoder is not None:
            return
        model_dir = model_catalog.parakeet_model_dir(self.model_name)
        if self._sessions is not None:
            enc, dec, jnt, meta = self._sessions(model_dir)
        else:
            enc, dec, jnt, meta = self._default_sessions(model_dir)
        # positional I/O contract — fail fast on unexpected re-exports
        assert len(enc.get_inputs()) == 2, "encoder must take 2 inputs"
        assert len(enc.get_outputs()) == 2, "encoder must give 2 outputs"
        assert len(dec.get_inputs()) == 4, "decoder must take 4 inputs"
        assert len(dec.get_outputs()) == 4, "decoder must give 4 outputs"
        assert len(jnt.get_inputs()) == 2, "joiner must take 2 inputs"
        assert len(jnt.get_outputs()) == 1, "joiner must give 1 output"
        features = model_catalog.PARAKEET_CATALOG[self.model_name]["features"]
        self._featurizer = LogMelFeaturizer(**features)
        if "feat_dim" in meta and int(meta["feat_dim"]) != self._featurizer.n_mels:
            raise RuntimeError(
                f"encoder expects feat_dim={meta['feat_dim']} but the catalog "
                f"featurizer produces {self._featurizer.n_mels} mels")
        id2tok, _blank = load_tokens_txt(model_dir / "tokens.txt")
        meta = dict(meta)
        meta["vocab_size"] = str(len(id2tok))  # tokens.txt is the truth
        self._normalize_type = meta.get("normalize_type", "")
        self._id2tok = id2tok
        self._decoder = TdtGreedyDecoder(enc, dec, jnt, meta)

    def _read_wav(self, path: Path) -> np.ndarray:
        with wave.open(str(path), "rb") as w:
            if w.getframerate() != SAMPLE_RATE:
                raise RuntimeError(
                    "parakeet needs 16 kHz mono WAV (the daemon records "
                    f"this; got {w.getframerate()} Hz)")
            if w.getnchannels() != 1:
                raise RuntimeError(
                    "parakeet needs 16 kHz mono WAV (the daemon records "
                    f"this; got {w.getnchannels()} channels)")
            raw = w.readframes(w.getnframes())
            width = w.getsampwidth()
        if width == 2:
            return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if width == 4:  # IEEE float
            return np.frombuffer(raw, dtype="<f4").astype(np.float32)
        raise RuntimeError(
            f"unsupported WAV sample width {width * 8} bit "
            "(need 16-bit PCM or 32-bit float)")

    def transcribe(self, wav_path: Path,
                   language: str | None = None) -> dict[str, Any]:
        self._load()
        samples = self._read_wav(Path(wav_path))
        n = samples.shape[0]
        padded = np.concatenate(
            [samples, np.zeros(int(TAIL_PAD_S * SAMPLE_RATE), np.float32)])
        feats = self._featurizer(padded)
        if self._normalize_type == "per_feature":
            feats = ((feats - feats.mean(axis=0, keepdims=True))
                     / (feats.std(axis=0, keepdims=True) + 1e-5))
        ids = self._decoder.run(feats)
        text = detokenize(ids, self._id2tok)
        lang = language or self.language or "auto"
        duration = n / SAMPLE_RATE
        return {"text": text,
                "language": None if lang == "auto" else lang,
                "duration": duration,
                "segments": [{"start": 0.0, "end": duration, "text": text}]}
