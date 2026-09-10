"""faster-whisper backend (default). CUDA when possible, CPU int8 otherwise."""
from __future__ import annotations

import os
import tempfile
import wave
from pathlib import Path
from typing import Any

from . import (
    FW_MODEL_REPOS,
    cuda_available,
    effective_language,
    preload_cuda_libs,
    resolve_model_name,
)
from .base import BackendCapabilities, Transcript, TranscriptSegment


class FasterWhisperBackend:
    name = "faster-whisper"
    # wrong-language guard: this backend surfaces the detected language
    # (info.language) under auto, so the whitelist guard can retry
    surfaces_detected_language = True
    # hallucination guard: transcribe() honors a language hint, so a
    # garbage forced-language decode can be retried with auto detection
    selects_language = True
    # the seam (plan P1.1): canonical capability declaration. The two
    # legacy attributes above are kept as aliases (pre-seam consumers);
    # the contract suite asserts they agree.
    capabilities = BackendCapabilities(
        supports_language_select=True,    # language= hint honored
        supports_language_detect=True,    # info.language under auto
        supports_hotwords=True,           # native decoder hotwords param
        supports_streaming=False,         # preview = segmented batch
        supports_segments=True,           # start/end/text per segment
        supports_confidence=True,         # avg_logprob + no_speech_prob
        supports_alternatives=False,
    )

    def __init__(self, cfg: dict):
        preload_cuda_libs()  # must run before ctranslate2 loads its CUDA libs
        from faster_whisper import WhisperModel  # deferred import

        self._WhisperModel = WhisperModel
        mcfg = cfg["model"]
        self.model_name = resolve_model_name(mcfg["name"])
        self.language = effective_language(cfg) or None
        # custom vocabulary biasing (#916): fed to the decoder as hotwords
        self.hotwords = " ".join(mcfg.get("hotwords") or []) or None
        device = mcfg["device"]
        compute = mcfg["compute"]
        if device == "auto":
            device = "cuda" if cuda_available() else "cpu"
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        self.device, self.compute = device, compute
        self._model: Any = None  # lazy load (first transcription)

    def warmup(self) -> None:
        self._load()
        # _load alone leaves the first real dictation paying CUDA kernel /
        # cuDNN setup (~+0.2 s on an RTX 4060); one throwaway inference on a
        # second of silence gets that out of the way at daemon start.
        try:
            self._warm_inference()
        except Exception:
            pass  # the model loaded; a failed probe must not fail startup

    def close(self) -> None:
        """No-op: CTranslate2 weights free on refcount drop + gc."""

    def _warm_inference(self) -> None:
        fd, name = tempfile.mkstemp(prefix="sayitermano-warmup-", suffix=".wav")
        try:
            with os.fdopen(fd, "wb") as f:
                with wave.open(f, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(16000)
                    w.writeframes(b"\0" * 32000)  # 1.0 s of silence
            self.transcribe(Path(name))
        finally:
            Path(name).unlink(missing_ok=True)

    def _load(self) -> None:
        if self._model is not None:
            return
        from .. import paths
        download_root = str(paths.models_dir() / "faster-whisper")
        try:
            self._model = self._WhisperModel(
                FW_MODEL_REPOS[self.model_name] if self.model_name in FW_MODEL_REPOS else self.model_name,
                device=self.device, compute_type=self.compute,
                download_root=download_root,
            )
        except Exception:
            if self.device == "cuda":
                # Missing cuDNN/cuBLAS etc. - fall back to CPU int8.
                self.device, self.compute = "cpu", "int8"
                self._model = self._WhisperModel(
                    FW_MODEL_REPOS.get(self.model_name, self.model_name),
                    device=self.device, compute_type=self.compute,
                    download_root=download_root,
                )
            else:
                raise

    def transcribe(self, wav_path: Path,
                   language: str | None = None) -> Transcript:
        self._load()
        lang = language or self.language
        if lang == "auto":
            lang = None
        segments, info = self._model.transcribe(
            str(wav_path), language=lang, vad_filter=False, beam_size=1,
            # getattr: duck-typed constructions (tests) bypass __init__
            hotwords=getattr(self, "hotwords", None),
        )
        segs, texts = [], []
        for seg in segments:  # generator - consume once, reuse for text AND segments
            lp = getattr(seg, "avg_logprob", None)
            ns = getattr(seg, "no_speech_prob", None)
            texts.append(seg.text)  # raw: joining unstripped keeps the spaces
            segs.append(TranscriptSegment(
                start=round(seg.start, 3), end=round(seg.end, 3),
                text=seg.text.strip(),
                avg_logprob=round(lp, 3) if lp is not None else None,
                no_speech_prob=round(ns, 3) if ns is not None else None))
        return Transcript(
            text="".join(texts).strip(),
            language=info.language, duration=info.duration,
            segments=tuple(segs))
