"""Local evaluation harness for the speech pipeline (plan P1.4).

Measures WER/CER, hotword recall, real-time factor, preview/final latency
and hallucination-guard accuracy over a manifest of audio cases —
backend-agnostic (the runner takes any transcriber callable) and
model-free (metric math needs no model; representative-model evaluation
is a documented manual step, see docs/eval/README.md).

Public API:

- :func:`load_manifest` / :func:`load_manifests` — manifest loading and
  validation (``manifest`` module)
- :mod:`fluidvoice.evalharness.metrics` — pure metric functions
- :func:`run_eval` — execute cases with a transcriber and write
  ``report.json`` / ``report.md`` (``runner`` module)
- :func:`main` — CLI (``python -m fluidvoice.evalharness``; also exposed
  as ``sayit-ermano eval-run``)
"""
from __future__ import annotations

from .manifest import (
    Case,
    ManifestError,
    SynthSpec,
    default_manifest_path,
    load_manifest,
    load_manifests,
    resolve_manifest_path,
)
from .metrics import (
    GUARD_FLAG,
    GUARD_NONE,
    GUARD_OK,
    cer,
    guard_category,
    guard_counts,
    hotword_recall,
    levenshtein,
    mean,
    normalize_chars,
    normalize_words,
    real_time_factor,
    wer,
)
from .runner import TranscriptionOutput, run_eval
from .synth import SYNTH_KINDS, ensure_case_audio, synth_wav, wav_duration_seconds

__all__ = [
    "Case",
    "GUARD_FLAG",
    "GUARD_NONE",
    "GUARD_OK",
    "ManifestError",
    "SYNTH_KINDS",
    "SynthSpec",
    "TranscriptionOutput",
    "cer",
    "default_manifest_path",
    "ensure_case_audio",
    "guard_category",
    "guard_counts",
    "hotword_recall",
    "levenshtein",
    "load_manifest",
    "load_manifests",
    "main",
    "mean",
    "normalize_chars",
    "normalize_words",
    "real_time_factor",
    "resolve_manifest_path",
    "run_eval",
    "synth_wav",
    "wav_duration_seconds",
    "wer",
]


def main(argv: list[str] | None = None) -> int:
    """CLI entry (imported lazily so importing the package stays light)."""
    from .cli import main as _main
    return _main(argv)
