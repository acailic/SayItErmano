"""openai-whisper (torch) backend - used when a CUDA torch install already exists."""
from __future__ import annotations

from pathlib import Path

from . import cuda_available, effective_language, resolve_model_name
from .base import BackendCapabilities, Transcript, TranscriptSegment


class TorchWhisperBackend:
    name = "whisper-torch"
    # wrong-language guard: this backend surfaces the detected language
    # (result["language"]) under auto, so the whitelist guard can retry
    surfaces_detected_language = True
    # hallucination guard: transcribe() honors a language hint
    selects_language = True
    # the seam (plan P1.1): canonical capability declaration. The two
    # legacy attributes above are kept as aliases (pre-seam consumers);
    # the contract suite asserts they agree.
    capabilities = BackendCapabilities(
        supports_language_select=True,    # language= hint honored
        supports_language_detect=True,    # result["language"] under auto
        # no native decoder hotwords: the initial_prompt hint (fed with
        # the configured vocabulary, #916) is the closest mechanism
        supports_hotwords=True,
        supports_streaming=False,
        supports_segments=True,           # start/end/text (no confidence)
        supports_confidence=False,        # openai-whisper segments carry no
                                          # logprobs we surface today
        supports_alternatives=False,
    )

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

    def close(self) -> None:
        """No-op: torch weights free on refcount drop + gc (same
        rationale as the old shared Backend base)."""

    def _load(self) -> None:
        if self._model is None:
            self._model = self._whisper.load_model(self.model_name, device=self.device)

    def transcribe(self, wav_path: Path,
                   language: str | None = None) -> Transcript:
        self._load()
        lang = language or self.language
        if lang == "auto":
            lang = None
        result = self._model.transcribe(
            str(wav_path), language=lang,
            # getattr: duck-typed constructions (tests) bypass __init__
            initial_prompt=getattr(self, "hotwords", None),
            fp16=self.device == "cuda")
        segs = tuple(TranscriptSegment(
            start=round(s.get("start", 0.0), 3),
            end=round(s.get("end", 0.0), 3),
            text=(s.get("text") or "").strip())
            for s in result.get("segments", []))
        return Transcript(text=result.get("text", "").strip(),
                          language=result.get("language"),
                          duration=None, segments=segs)
