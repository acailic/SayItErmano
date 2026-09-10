"""Execute evaluation cases with a pluggable transcriber; emit reports.

The runner is backend-agnostic: it never imports a speech backend. It
takes any *transcriber callable*

    transcriber(audio_path: Path, case: Case) -> Mapping

returning at least ``{"text": str}`` plus any of the optional keys below
(unknown keys are recorded verbatim under ``raw`` — the report keeps the
model's own outputs next to the computed metrics):

=========================  =====================================
key                        meaning
=========================  =====================================
``text``                   final transcript (required, str)
``guard``                  ``"ok"`` / ``"flag"`` / ``"none"``
                           (observed hallucination-guard outcome;
                           absent = guard didn't run)
``first_preview_latency_s`` seconds until the first preview text
``final_latency_s``        seconds until the final transcript
``processing_time_s``      seconds of decode work (defaults to the
                           runner's wall-clock around the call)
=========================  =====================================

Real backends are plugged by the user in a manual run (see
docs/eval/README.md for a wrapper example); tests use fakes.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from . import metrics
from .manifest import Case
from .report import render_markdown
from .synth import ensure_case_audio, wav_duration_seconds

Transcriber = Callable[[Path, Case], Mapping[str, Any]]

_KNOWN_KEYS = ("text", "guard", "first_preview_latency_s",
               "final_latency_s", "processing_time_s")


class TranscriptionOutputError(ValueError):
    """A transcriber returned something that isn't a transcription."""


@dataclass(frozen=True)
class TranscriptionOutput:
    """Normalized transcriber result (+ everything else under ``raw``)."""

    text: str
    guard: str = metrics.GUARD_NONE
    first_preview_latency_s: float | None = None
    final_latency_s: float | None = None
    processing_time_s: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, d: Mapping[str, Any]) -> "TranscriptionOutput":
        text = d.get("text")
        if not isinstance(text, str):
            raise TranscriptionOutputError(
                f"transcriber result must map 'text' to a string, got "
                f"{text!r}")
        guard = d.get("guard", metrics.GUARD_NONE)
        if guard is None:
            guard = metrics.GUARD_NONE
        if not isinstance(guard, str) or guard.lower() not in (
                metrics.GUARD_OK, metrics.GUARD_FLAG, metrics.GUARD_NONE):
            raise TranscriptionOutputError(
                f"'guard' must be ok/flag/none, got {guard!r}")
        lats: dict[str, float | None] = {}
        for k in ("first_preview_latency_s", "final_latency_s",
                  "processing_time_s"):
            v = d.get(k)
            if v is not None:
                if not isinstance(v, (int, float)) or isinstance(v, bool) \
                        or v < 0:
                    raise TranscriptionOutputError(
                        f"'{k}' must be a number >= 0, got {v!r}")
                v = float(v)
            lats[k] = v
        return cls(
            text=text, guard=guard.lower(),
            first_preview_latency_s=lats["first_preview_latency_s"],
            final_latency_s=lats["final_latency_s"],
            processing_time_s=lats["processing_time_s"],
            raw={k: v for k, v in d.items() if k not in _KNOWN_KEYS},
        )


def _case_report(case: Case, out: TranscriptionOutput, *,
                 audio_duration_s: float | None,
                 processing_time_s: float | None,
                 wall_time_s: float) -> dict[str, Any]:
    recall = metrics.hotword_recall(out.text, case.tags, case.reference_text)
    return {
        "id": case.id,
        "audio": str(case.audio),
        "reference_text": case.reference_text,
        "hypothesis_text": out.text,
        "language": case.language,
        "tags": list(case.tags),
        "expected_guard": case.expected_guard,
        "observed_guard": out.guard,
        "guard_category": metrics.guard_category(case.expected_guard,
                                                 out.guard),
        "metrics": {
            "wer": metrics.wer(case.reference_text, out.text),
            "cer": metrics.cer(case.reference_text, out.text),
            "hotword_recall": recall,
            "real_time_factor": metrics.real_time_factor(
                audio_duration_s, processing_time_s),
            "audio_duration_s": audio_duration_s,
            "processing_time_s": processing_time_s,
            "wall_time_s": round(wall_time_s, 6),
            "first_preview_latency_s": out.first_preview_latency_s,
            "final_latency_s": out.final_latency_s,
        },
        "raw": out.raw or None,
        "error": None,
    }


def _summarize(case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    def m(key: str) -> float | None:
        return metrics.mean(
            c["metrics"][key] for c in case_reports
            if c.get("error") is None and c["metrics"] is not None)

    ok = [c for c in case_reports if c.get("error") is None]
    guard = metrics.guard_counts(c["guard_category"] for c in ok)
    return {
        "cases": len(case_reports),
        "errors": len(case_reports) - len(ok),
        "wer_mean": m("wer"),
        "cer_mean": m("cer"),
        "hotword_recall_mean": m("hotword_recall"),
        "real_time_factor_mean": m("real_time_factor"),
        "first_preview_latency_mean_s": m("first_preview_latency_s"),
        "final_latency_mean_s": m("final_latency_s"),
        "guard": guard,
    }


def run_eval(cases: list[Case], transcriber: Transcriber, *,
             out_dir: Path, manifests: list[Path] | None = None,
             soak: dict[str, Any] | None = None,
             harness_version: str = "", allow_synth: bool = True) -> dict:
    """Run every case, write ``report.json`` + ``report.md`` to ``out_dir``.

    Returns the report dict (the same structure written as report.json).
    A case whose audio can't be produced or whose transcriber call raises
    is recorded with an ``error`` and excluded from aggregates — one bad
    case never kills the whole evaluation.
    """
    case_reports: list[dict[str, Any]] = []
    for case in cases:
        entry: dict[str, Any] = {
            "id": case.id, "audio": str(case.audio),
            "reference_text": case.reference_text,
            "language": case.language, "tags": list(case.tags),
            "expected_guard": case.expected_guard,
            "observed_guard": None, "guard_category": None,
            "metrics": None, "raw": None, "error": None,
        }
        try:
            audio = ensure_case_audio(case, allow_synth=allow_synth)
            duration = wav_duration_seconds(audio)
            t0 = time.perf_counter()
            out = TranscriptionOutput.from_mapping(transcriber(audio, case))
            wall = time.perf_counter() - t0
            processing = out.processing_time_s if out.processing_time_s \
                is not None else wall
            entry = _case_report(case, out, audio_duration_s=duration,
                                 processing_time_s=processing,
                                 wall_time_s=wall)
        except Exception as e:  # noqa: BLE001 - record, don't die
            entry["error"] = f"{type(e).__name__}: {e}"
        case_reports.append(entry)

    report = {
        "harness_version": harness_version or __version__,
        "generated_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "manifests": [str(p) for p in (manifests or [])],
        "conventions": {
            "text_normalization": "NFKC + casefold + [\\w']+ tokens, "
                                  "single spaces",
            "wer": "word-level levenshtein / reference words",
            "cer": "character-level levenshtein / reference chars "
                   "(normalized stream, spaces included)",
            "real_time_factor": "audio duration / processing time "
                                "(> 1 is faster than realtime)",
            "hotword_recall": "fraction of reference-contained tags "
                              "present in the hypothesis",
            "guard": "expected vs observed outcome; expected 'none' "
                     "cases are excluded",
        },
        "summary": _summarize(case_reports),
        "cases": case_reports,
    }
    if soak is not None:
        report["soak"] = soak

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(report) + "\n",
                                       encoding="utf-8")
    return report
