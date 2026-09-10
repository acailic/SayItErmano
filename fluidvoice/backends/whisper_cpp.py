"""whisper.cpp backend - uses an external whisper-cli binary + ggml/gguf model."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .. import model_catalog
from . import _whispercpp_binary, effective_language
from .base import BackendCapabilities, Transcript


class WhisperCppBackend:
    name = "whisper.cpp"
    # wrong-language guard: under auto the binary only prints the detected
    # language to verbose output we do not parse -> the guard skips
    # silently (documented limitation; see backends.LANGUAGE_GUARD)
    surfaces_detected_language = False
    # hallucination guard: the binary honors -l, so a garbage forced
    # decode can still be retried with auto detection
    selects_language = True
    # the seam (plan P1.1): canonical capability declaration. The two
    # legacy attributes above are kept as aliases (pre-seam consumers);
    # the contract suite asserts they agree.
    capabilities = BackendCapabilities(
        supports_language_select=True,    # -l flag honored
        supports_language_detect=False,   # not surfaced under auto
        supports_hotwords=False,          # no vocabulary biasing flag in v1
        supports_streaming=False,
        supports_segments=False,          # needs whisper-cli -ml parsing
        supports_confidence=False,
        supports_alternatives=False,
    )

    def __init__(self, cfg: dict):
        self.binary = _whispercpp_binary()
        if not self.binary:
            raise RuntimeError("whisper.cpp binary not found (whisper-cli/whisper-cpp)")
        raw = (cfg["model"].get("whispercpp_model") or "").strip()
        if not raw:
            raise RuntimeError(
                "model.whispercpp_model is required for the whisper.cpp backend "
                "(a catalog name like 'ggml-base.bin' or a path to a ggml/gguf file)")
        if "/" in raw or raw.startswith("~"):
            path = Path(raw).expanduser()
            self.model = str(path)
            if not path.is_file():
                raise RuntimeError(f"whisper.cpp model not found: {path}")
        else:
            if raw not in model_catalog.GGUF_CATALOG:
                raise RuntimeError(
                    f"unknown whisper.cpp model '{raw}' — catalog names: "
                    f"{', '.join(sorted(model_catalog.GGUF_CATALOG))}, or give a full path")
            self.model = str(model_catalog.gguf_path(raw))
            if not Path(self.model).is_file():
                raise RuntimeError(
                    f"whisper.cpp model '{raw}' not downloaded yet "
                    f"(expected at {self.model}) — download it in "
                    f"Settings → Models, whisper.cpp GGUF")
        self.language = effective_language(cfg) or "auto"

    def warmup(self) -> None:
        """No-op: the binary runs a subprocess per transcription, so
        there is no model memory to preload in-process."""

    def close(self) -> None:
        """No-op: nothing is held between transcriptions."""

    def transcribe(self, wav_path: Path,
                   language: str | None = None) -> Transcript:
        lang = language or self.language or "auto"
        args = [self.binary, "-m", self.model, "-f", str(wav_path), "-nt", "-np"]
        if lang != "auto":
            args += ["-l", lang]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"whisper.cpp failed: {proc.stderr.strip()[:500]}")
        text = " ".join(line.strip() for line in proc.stdout.splitlines() if line.strip())
        return Transcript(text=text.strip(),
                          language=None if lang == "auto" else lang,
                          duration=None,
                          # segments not exposed in v1: needs whisper-cli -ml parsing
                          segments=())
