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
``guard_score``            float in [0, 1] — the adapter's
                           hallucination-suspicion score, higher =
                           more suspicious (flag when >= threshold);
                           used by the train-only guard sweep
``detected_language``      str — the language the adapter detected
                           (BCP-47 style); feeds the language
                           confusion table
=========================  =====================================

``guard_score`` and ``detected_language`` are *raw-key contracts*: they
stay verbatim under ``raw`` (so re-analysis never loses them) AND are
copied into typed per-case fields plus the guard sweep / language
confusion summaries.

Real backends are plugged by the user in a manual run (see
docs/eval/README.md for a wrapper example); tests use fakes.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from . import metrics
from .manifest import Case
from .metrics import (
    GUARD_FALSE_NEGATIVE,
    GUARD_FALSE_POSITIVE,
    GUARD_TRUE_NEGATIVE,
    GUARD_TRUE_POSITIVE,
    SUBGROUP_DIMENSIONS,
    UNSET_LABEL,
)
from .report import render_markdown
from .synth import ensure_case_audio, wav_duration_seconds

Transcriber = Callable[[Path, Case], Mapping[str, Any]]

_KNOWN_KEYS = ("text", "guard", "first_preview_latency_s",
               "final_latency_s", "processing_time_s")

# Default threshold grid for the guard sweep (G6): flags when
# guard_score >= threshold. Tuned on train runs only — corpus-spec §6.
GUARD_SWEEP_THRESHOLDS: tuple[float, ...] = tuple(
    round(0.05 * i, 2) for i in range(1, 20))

GUARD_SWEEP_CONVENTION = (
    "threshold sweep over raw guard_score (flag when score >= "
    "threshold) on TRAIN cases only: cases labeled split='train', or "
    "unlabeled cases in a run labeled --split train. Held-out cases "
    "never enter a sweep — thresholds are tuned on train and applied "
    "to held-out untouched (corpus-spec §6).")

# Per-case metric keys aggregated into every summary/subgroup row.
_AGGREGATED_METRICS = (
    ("wer", "wer_mean"), ("cer", "cer_mean"),
    ("omission", "omission_mean"),
    ("hotword_recall", "hotword_recall_mean"),
    ("real_time_factor", "real_time_factor_mean"),
    ("first_preview_latency_s", "first_preview_latency_mean_s"),
    ("final_latency_s", "final_latency_mean_s"),
    ("hallucination_word_rate", "hallucination_word_rate_mean"),
    ("terminal_punctuation_accuracy",
     "terminal_punctuation_accuracy_mean"),
    ("sentence_start_capital_accuracy",
     "sentence_start_capital_accuracy_mean"),
)


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
    guard_score: float | None = None
    detected_language: str | None = None
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
        guard_score = d.get("guard_score")
        if guard_score is not None:
            if not isinstance(guard_score, (int, float)) \
                    or isinstance(guard_score, bool) \
                    or not 0.0 <= guard_score <= 1.0:
                raise TranscriptionOutputError(
                    f"'guard_score' must be a number in [0, 1], got "
                    f"{guard_score!r}")
            guard_score = float(guard_score)
        detected_language = d.get("detected_language")
        if detected_language is not None and \
                not isinstance(detected_language, str):
            raise TranscriptionOutputError(
                f"'detected_language' must be a string, got "
                f"{detected_language!r}")
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
            guard_score=guard_score,
            detected_language=detected_language,
            raw={k: v for k, v in d.items() if k not in _KNOWN_KEYS},
        )


def _case_report(case: Case, out: TranscriptionOutput, *,
                 audio_duration_s: float | None,
                 processing_time_s: float | None,
                 wall_time_s: float) -> dict[str, Any]:
    recall = metrics.hotword_recall(out.text, case.tags, case.reference_text)
    ref_words = metrics.normalize_words(case.reference_text)
    return {
        "id": case.id,
        "audio": str(case.audio),
        "reference_text": case.reference_text,
        "hypothesis_text": out.text,
        "language": case.language,
        "language_detected": out.detected_language,
        "language_match": (
            metrics.language_codes_match(case.language,
                                         out.detected_language)
            if out.detected_language is not None else None),
        "tags": list(case.tags),
        "speaker": case.speaker,
        "mic_class": case.mic_class,
        "taxonomy": case.taxonomy,
        "snr_db": case.snr_db,
        "snr_bucket": metrics.snr_bucket(case.snr_db),
        "split": case.split,
        "expected_guard": case.expected_guard,
        "observed_guard": out.guard,
        "guard_category": metrics.guard_category(case.expected_guard,
                                                 out.guard),
        "guard_score": out.guard_score,
        "metrics": {
            "wer": metrics.wer(case.reference_text, out.text),
            "cer": metrics.cer(case.reference_text, out.text),
            "omission": metrics.omission_rate(case.reference_text,
                                               out.text),
            "hotword_recall": recall,
            "real_time_factor": metrics.real_time_factor(
                audio_duration_s, processing_time_s),
            "audio_duration_s": audio_duration_s,
            "processing_time_s": processing_time_s,
            "wall_time_s": round(wall_time_s, 6),
            "first_preview_latency_s": out.first_preview_latency_s,
            "final_latency_s": out.final_latency_s,
            # negative cases only (empty reference): tokens per second
            "hallucination_word_rate": (
                metrics.hallucination_word_rate(out.text, audio_duration_s)
                if not ref_words else None),
            "terminal_punctuation_accuracy":
                metrics.terminal_punctuation_accuracy(
                    case.reference_text, out.text),
            "sentence_start_capital_accuracy":
                metrics.sentence_start_capital_accuracy(
                    case.reference_text, out.text),
        },
        "raw": out.raw or None,
        "error": None,
    }


def _summary_of(members: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one set of case reports (the corpus or any subgroup).

    Empty/metric-less handling: means are None when no member scores
    that metric (``metrics.mean`` skips non-numbers); guard counts cover
    non-errored members only.
    """
    ok = [c for c in members if c.get("error") is None]

    def m(key: str) -> float | None:
        return metrics.mean(
            c["metrics"][key] for c in ok if c["metrics"] is not None)

    out: dict[str, Any] = {
        "cases": len(members),
        "errors": len(members) - len(ok),
    }
    for src, dst in _AGGREGATED_METRICS:
        out[dst] = m(src)
    out["guard"] = metrics.guard_counts(c["guard_category"] for c in ok)
    return out


def _summarize(case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    return _summary_of(case_reports)


def _group_label(value: Any) -> str:
    return value if isinstance(value, str) and value else UNSET_LABEL


def _subgroups(case_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One aggregate row per (dimension, group) present in the corpus
    (G1). Groups come from the cases themselves, so no empty group is
    ever emitted; cases missing a dimension's provenance key collect
    under ``(unset)`` so each dimension's counts sum to the corpus.
    """
    rows: list[dict[str, Any]] = []
    for dim in SUBGROUP_DIMENSIONS:
        groups: dict[str, list[dict[str, Any]]] = {}
        for c in case_reports:
            groups.setdefault(_group_label(c.get(dim)), []).append(c)
        for label in sorted(groups, key=lambda g: (g == UNSET_LABEL, g)):
            row = {"dimension": dim, "group": label}
            row.update(_summary_of(groups[label]))
            rows.append(row)
    return rows


def _negatives(case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Negative (empty-reference) case rows + hallucination rate per
    noise type (G3, corpus-spec §8 negative-case table)."""
    rows = []
    by_taxonomy: dict[str, list[float | None]] = {}
    for c in case_reports:
        if c.get("error") is not None or c.get("reference_text"):
            continue
        m = c["metrics"]
        taxonomy = c.get("taxonomy")
        label = _group_label(taxonomy)
        rows.append({
            "id": c["id"],
            "taxonomy": taxonomy,
            "tokens": len(metrics.normalize_words(c["hypothesis_text"])),
            "audio_duration_s": m["audio_duration_s"],
            "hallucination_word_rate": m["hallucination_word_rate"],
            "observed_guard": c["observed_guard"],
            "guard_category": c["guard_category"],
        })
        by_taxonomy.setdefault(label, []).append(
            m["hallucination_word_rate"])
    return {
        "cases": rows,
        "hallucination_word_rate_by_taxonomy": {
            t: metrics.mean(rates) for t, rates in sorted(
                by_taxonomy.items())
        },
    }


def _language_confusion(
        case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Expected-vs-detected language table (G5): per (expected,
    detected) pair, which cases; plus match/unreported totals."""
    cells: dict[tuple[str, str, bool], list[str]] = {}
    unreported: list[str] = []
    matches = 0
    for c in case_reports:
        if c.get("error") is not None:
            continue
        detected = c.get("language_detected")
        if detected is None:
            unreported.append(c["id"])
            continue
        match = bool(c.get("language_match"))
        matches += 1 if match else 0
        cells.setdefault((c["language"], detected, match),
                         []).append(c["id"])
    rows = [
        {"expected": exp, "detected": det, "match": match,
         "cases": len(ids), "ids": sorted(ids)}
        for (exp, det, match), ids in sorted(
            cells.items(), key=lambda kv: (kv[0][0], not kv[0][2],
                                           kv[0][1]))
    ]
    return {
        "match_rule": "primary subtag, casefolded (en == en-US)",
        "reported": sum(r["cases"] for r in rows),
        "matches": matches,
        "unreported": sorted(unreported),
        "rows": rows,
    }


def _effective_split(case: dict[str, Any],
                     run_split: str | None) -> str | None:
    """Case labels win over the run label (a labeled case keeps its
    split even in a full-corpus run); unlabeled cases follow the run."""
    return case.get("split") or run_split


def _guard_sweep(case_reports: list[dict[str, Any]], *,
                 run_split: str | None,
                 thresholds: Sequence[float]) -> dict[str, Any] | None:
    """Threshold sweep over raw guard scores — train-only (G6).

    Returns None (no sweep, per the held-out exclusion convention)
    unless at least one *train-effective* case carries a guard score
    and a scoreable guard expectation. See GUARD_SWEEP_CONVENTION.
    """
    pool = [
        c for c in case_reports
        if c.get("error") is None
        and _effective_split(c, run_split) == "train"
        and c.get("guard_score") is not None
        and c.get("expected_guard") in (metrics.GUARD_OK,
                                         metrics.GUARD_FLAG)
    ]
    if not pool:
        return None
    rows = []
    for t in thresholds:
        counts = metrics.guard_counts(
            metrics.guard_category(
                c["expected_guard"],
                metrics.GUARD_FLAG if c["guard_score"] >= t
                else metrics.GUARD_OK)
            for c in pool)
        rows.append({
            "threshold": t,
            "flagged": counts[GUARD_TRUE_POSITIVE]
                      + counts[GUARD_FALSE_POSITIVE],
            "tp": counts[GUARD_TRUE_POSITIVE],
            "tn": counts[GUARD_TRUE_NEGATIVE],
            "fp": counts[GUARD_FALSE_POSITIVE],
            "fn": counts[GUARD_FALSE_NEGATIVE],
        })
    return {
        "convention": GUARD_SWEEP_CONVENTION,
        "cases": len(pool),
        "thresholds": list(thresholds),
        "rows": rows,
    }


def _worst_cases(case_reports: list[dict[str, Any]],
                 limit: int = 10) -> list[dict[str, Any]]:
    """The ``limit`` highest-WER cases — failures are named, not
    averaged away (corpus-spec §8)."""
    scored = [c for c in case_reports
              if c.get("error") is None and c["metrics"]
              and c["metrics"]["wer"] is not None]
    scored.sort(key=lambda c: (-c["metrics"]["wer"], c["id"]))
    return [{
        "id": c["id"],
        "speaker": c.get("speaker"),
        "language": c["language"],
        "taxonomy": c.get("taxonomy"),
        "wer": c["metrics"]["wer"],
    } for c in scored[:limit]]


def run_eval(cases: list[Case], transcriber: Transcriber, *,
             out_dir: Path, manifests: list[Path] | None = None,
             soak: dict[str, Any] | None = None,
             harness_version: str = "", allow_synth: bool = True,
             split: str | None = None,
             guard_thresholds: Sequence[float] | None = None
             ) -> dict:
    """Run every case, write ``report.json`` + ``report.md`` to ``out_dir``.

    ``split`` labels the whole run ``train`` / ``heldout`` (or None for
    a full/unlabeled corpus run); it is stamped on the report header
    and — with the per-case ``split`` label taking precedence — decides
    whether a guard sweep may be computed (train only, corpus-spec §6).
    ``guard_thresholds`` overrides the default sweep grid.

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
            "speaker": case.speaker, "mic_class": case.mic_class,
            "taxonomy": case.taxonomy, "snr_db": case.snr_db,
            "snr_bucket": metrics.snr_bucket(case.snr_db),
            "split": case.split,
            "expected_guard": case.expected_guard,
            "observed_guard": None, "guard_category": None,
            "guard_score": None,
            "language_detected": None, "language_match": None,
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
        "split": split or "full",
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
            "omission": "deletion-only errors / reference words "
                        "(empty reference: not scored)",
            "hallucination_word_rate": "hypothesis tokens per audio "
                                       "second on empty-reference "
                                       "(negative) cases",
            "punctuation": "separate metric family on raw text "
                           "(terminal punctuation runs, sentence-start "
                           "capitals); never feeds WER/CER normalization",
            "language_confusion": "expected vs adapter-detected "
                                  "language; primary subtag, casefolded",
            "guard": "expected vs observed outcome; expected 'none' "
                     "cases are excluded",
            "guard_sweep": GUARD_SWEEP_CONVENTION,
            "subgroups": "per-dimension aggregates; '(unset)' rows "
                         "collect cases missing the provenance key; "
                         "means are n/a when no case in the group "
                         "scores that metric",
        },
        "summary": _summarize(case_reports),
        "subgroups": _subgroups(case_reports),
        "language_confusion": _language_confusion(case_reports),
        "guard_sweep": _guard_sweep(
            case_reports, run_split=split,
            thresholds=guard_thresholds or GUARD_SWEEP_THRESHOLDS),
        "negatives": _negatives(case_reports),
        "worst_cases": _worst_cases(case_reports),
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
