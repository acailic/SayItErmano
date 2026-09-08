"""openai-whisper (torch) backend - used when a CUDA torch install already exists."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import cuda_available, effective_language, resolve_model_name


class TorchWhisperBackend:
    name = "whisper-torch"
    # wrong-language guard: this backend surfaces the detected language
    # (result["language"]) under auto, so the whitelist guard can retry
    surfaces_detected_language = True

    def __init__(self, cfg: dict):
        import whisper  # openai-whisper (deferred)

        self._whisper = whisper
        mcfg = cfg["model"]
        self.model_name = resolve_model_name(mcfg["name"])
        self.language = effective_language(cfg) or None
        # custom vocabulary biasing (#916): openai-whisper has no hotwords
        # param - the initial_prompt hint is the closest mechanism
        self.hotwords = " ".join(mcfg.get("hotwords") or []) or None
        self.device = mcfg["device"]
        if self.device == "auto":
            self.device = "cuda" if cuda_available() else "cpu"
        self._model = None  # lazy load

    def warmup(self) -> None:
        self._load()

    def _load(self) -> None:
        if self._model is None:
            self._model = self._whisper.load_model(self.model_name, device=self.device)

    def transcribe(self, wav_path: Path, language: str | None = None) -> dict[str, Any]:
        self._load()
        lang = language or self.language
        if lang == "auto":
            lang = None
        result = self._model.transcribe(
            str(wav_path), language=lang,
            # getattr: duck-typed constructions (tests) bypass __init__
            initial_prompt=getattr(self, "hotwords", None),
            fp16=self.device == "cuda")
        segments = [{"start": round(s.get("start", 0.0), 3),
                     "end": round(s.get("end", 0.0), 3),
                     "text": (s.get("text") or "").strip()}
                    for s in result.get("segments", [])]
        return {"text": result.get("text", "").strip(), "language": result.get("language"),
                "duration": None, "segments": segments}
