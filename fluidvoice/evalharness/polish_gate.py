"""Local-vs-cloud polish quality comparison + regression gate (#21).

The STT eval harness scores how faithfully audio becomes text; this
module scores what happens NEXT: how each AI-polish variant transforms
the raw transcript, against the same corpus references, with the same
metric math (``evalharness.metrics`` — no second definitions).

Flow (``run_polish_eval``):

1. per case, obtain the RAW transcript — through the plugged
   ``transcriber`` (real runs) or, when None, the case's reference text
   (harness smoke only: it proves the plumbing, it is NOT an
   evaluation — same convention as the stub transcriber in ``run``);
2. run every polish variant (``{"name": polisher}`` callable
   ``(raw_text, case) -> {"text": str, ...}``) plus the built-in
   ``raw`` identity baseline;
3. score raw + each variant against the reference (WER / CER /
   omission / hotword recall);
4. compare variants against the ``raw`` baseline (deltas) and evaluate
   the REGRESSION GATE: the local polisher must not degrade quality vs
   the cloud polisher by more than the configured margins.

The gate is the release discipline for local polish (docs/eval/README
§Polish quality gate): a run with both ``local`` and ``cloud`` variants
fails (``gate.passed = False``, CLI exit 1) when

* any local-polish call errored, or
* ``wer_mean(local) - wer_mean(cloud) > max_wer_degradation``, or
* ``recall_mean(cloud) - recall_mean(local) > max_recall_loss``.

Everything is offline-safe: unit tests drive stub polishers; real runs
plug factories (see docs/eval/README.md for a local-polish adapter
example built on ``LocalPolishManager.polish_text``).
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from . import metrics
from .manifest import Case
from .synth import ensure_case_audio

#: polish variant: (raw_text, case) -> {"text": str, ...} (unknown keys
#: are kept verbatim under the case row, like the STT runner)
Polisher = Callable[[str, Case], Mapping[str, Any]]

RAW_VARIANT = "raw"
LOCAL_VARIANT = "local"
CLOUD_VARIANT = "cloud"


@dataclass(frozen=True)
class PolishGate:
    """Regression-gate margins (see module docstring)."""

    local: str = LOCAL_VARIANT
    cloud: str = CLOUD_VARIANT
    max_wer_degradation: float = 0.02
    max_recall_loss: float = 0.0


@dataclass
class _VariantState:
    name: str
    polisher: Polisher | None  # None = the raw identity baseline


def _variant_scores(case: Case, text: str) -> dict[str, float | None]:
    return {
        "wer": metrics.wer(case.reference_text, text),
        "cer": metrics.cer(case.reference_text, text),
        "omission": metrics.omission_rate(case.reference_text, text),
        "hotword_recall": metrics.hotword_recall(
            text, case.tags, case.reference_text),
    }


def _raw_text_for(case: Case, transcriber) -> str:
    """The raw (unpolished) transcript for one case."""
    out = transcriber(case.audio, case)
    text = out.get("text") if isinstance(out, Mapping) else None
    if not isinstance(text, str):
        raise ValueError(
            f"transcriber result for {case.id!r} must map 'text' to a "
            f"string, got {text!r}")
    return text


def run_polish_eval(cases: list[Case], polisher_variants: dict[str, Polisher],
                    *, transcriber: Callable | None = None,
                    out_dir: Path, manifests: list[Path] | None = None,
                    gate: PolishGate | None = PolishGate(),
                    harness_version: str = "",
                    allow_synth: bool = True) -> dict:
    """Run the polish comparison; write polish-report.json/.md to out_dir.

    ``transcriber`` None = smoke mode (reference passthrough — loud
    note in the report). Returns the report dict."""
    smoke = transcriber is None
    variants: list[_VariantState] = [
        _VariantState(name=RAW_VARIANT, polisher=None)]
    for name, polisher in (polisher_variants or {}).items():
        if name == RAW_VARIANT:
            raise ValueError(f"{RAW_VARIANT!r} is the built-in baseline name")
        variants.append(_VariantState(name=name, polisher=polisher))

    case_rows: list[dict[str, Any]] = []
    for case in cases:
        row: dict[str, Any] = {
            "id": case.id,
            "reference_text": case.reference_text,
            "language": case.language,
            "tags": list(case.tags),
            "split": case.split,
            "variants": {},
            "error": None,
        }
        try:
            if smoke:
                raw_text = case.reference_text
            else:
                audio = ensure_case_audio(case, allow_synth=allow_synth)
                del audio  # proven to exist; the transcriber reads it
                raw_text = _raw_text_for(case, transcriber)
        except Exception as e:  # noqa: BLE001 - record, don't die
            row["error"] = f"{type(e).__name__}: {e}"
            case_rows.append(row)
            continue
        for v in variants:
            entry: dict[str, Any] = {"raw_input": raw_text}
            try:
                if v.polisher is None:
                    text = raw_text
                else:
                    out = v.polisher(raw_text, case)
                    text = out.get("text") if isinstance(out, Mapping) else None
                    if not isinstance(text, str):
                        raise ValueError(
                            f"polisher result must map 'text' to a string, "
                            f"got {text!r}")
                    entry["raw_output"] = dict(out)
            except Exception as e:  # noqa: BLE001 - variant error, not run death
                entry["error"] = f"{type(e).__name__}: {e}"
            else:
                entry["text"] = text
                entry["metrics"] = _variant_scores(case, text)
            row["variants"][v.name] = entry
        case_rows.append(row)

    report = _build_report(case_rows, variants, smoke=smoke,
                           manifests=manifests, gate=gate,
                           harness_version=harness_version)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "polish-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (out_dir / "polish-report.md").write_text(render_polish_markdown(report)
                                              + "\n", encoding="utf-8")
    return report


def _mean_of(metric_rows: list[dict], key: str) -> float | None:
    return metrics.mean(row[key] for row in metric_rows)


def _build_report(case_rows: list[dict], variants: list[_VariantState], *,
                  smoke: bool, manifests: list[Path] | None,
                  gate: PolishGate | None, harness_version: str) -> dict:
    """Variant summaries from the per-case metric rows (one source of
    truth: the scores already computed per case), the raw-baseline
    deltas, and the gate verdict."""
    summaries = {}
    for v in variants:
        metric_rows = [r["variants"][v.name].get("metrics")
                       for r in case_rows if r.get("error") is None]
        metric_rows = [m for m in metric_rows if m is not None]
        errors = {r["id"]: r["variants"][v.name]["error"]
                  for r in case_rows
                  if r.get("error") is None
                  and r["variants"].get(v.name, {}).get("error")}
        summaries[v.name] = {
            "cases": len(case_rows),
            "scored": len(metric_rows),
            "errors": len(errors),
            "error_details": errors,
            "wer_mean": _mean_of(metric_rows, "wer"),
            "cer_mean": _mean_of(metric_rows, "cer"),
            "omission_mean": _mean_of(metric_rows, "omission"),
            "hotword_recall_mean": _mean_of(metric_rows, "hotword_recall"),
        }
    baseline = summaries.get(RAW_VARIANT, {})
    comparison = {}
    for name, s in summaries.items():
        if name == RAW_VARIANT:
            continue
        comparison[name] = {
            "wer_delta_vs_raw": (
                s["wer_mean"] - baseline["wer_mean"]
                if s["wer_mean"] is not None
                and baseline.get("wer_mean") is not None else None),
            "hotword_recall_delta_vs_raw": (
                s["hotword_recall_mean"] - baseline["hotword_recall_mean"]
                if s["hotword_recall_mean"] is not None
                and baseline.get("hotword_recall_mean") is not None
                else None),
        }
    gate_block = _evaluate_gate(gate, summaries, smoke=smoke)
    return {
        "harness_version": harness_version or __version__,
        "generated_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "mode": "smoke (reference passthrough — NOT an evaluation)" if smoke
                else "evaluation",
        "manifests": [str(p) for p in (manifests or [])],
        "variants": [v.name for v in variants],
        "summary": summaries,
        "comparison_vs_raw": comparison,
        "gate": gate_block,
        "cases": case_rows,
    }


def _evaluate_gate(gate: PolishGate | None, summaries: dict,
                   *, smoke: bool) -> dict:
    """The regression-gate verdict. Skipped (never passed-by-default)
    when no gate was requested or a named variant is missing."""
    if gate is None:
        return {"status": "skipped", "reason": "no gate requested"}
    missing = [n for n in (gate.local, gate.cloud)
               if n not in summaries]
    if missing:
        return {"status": "skipped",
                "reason": "missing variant(s): " + ", ".join(missing)
                          + (" (smoke run)" if smoke else "")}
    local, cloud = summaries[gate.local], summaries[gate.cloud]
    checks: list[dict] = []

    def _check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(ok), "detail": detail})

    _check("local-polish errors", local["errors"] == 0,
           f"{local['errors']} errored case(s)")
    wer_delta = (local["wer_mean"] - cloud["wer_mean"]
                 if local["wer_mean"] is not None
                 and cloud["wer_mean"] is not None else None)
    _check(
        "WER degradation vs cloud",
        wer_delta is not None and wer_delta <= gate.max_wer_degradation,
        f"wer_mean(local) - wer_mean(cloud) = "
        f"{'n/a' if wer_delta is None else f'{wer_delta:+.4f}'} "
        f"(max {gate.max_wer_degradation:+.4f})")
    recall = (cloud["hotword_recall_mean"], local["hotword_recall_mean"])
    recall_loss = (recall[0] - recall[1]
                   if recall[0] is not None and recall[1] is not None
                   else None)
    _check(
        "hotword recall loss vs cloud",
        recall_loss is not None and recall_loss <= gate.max_recall_loss,
        f"recall_mean(cloud) - recall_mean(local) = "
        f"{'n/a' if recall_loss is None else f'{recall_loss:+.4f}'} "
        f"(max {gate.max_recall_loss:+.4f})")
    return {
        "status": "failed" if not all(c["passed"] for c in checks)
                  else "passed",
        "config": {
            "local": gate.local, "cloud": gate.cloud,
            "max_wer_degradation": gate.max_wer_degradation,
            "max_recall_loss": gate.max_recall_loss,
        },
        "checks": checks,
    }


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.4f}"


def render_polish_markdown(report: dict) -> str:
    """Readable rendering of the polish report (report.md)."""
    lines = ["# AI polish comparison (local vs cloud)", ""]
    lines.append(f"*generated {report['generated_utc']} by "
                 f"harness v{report['harness_version']} — "
                 f"mode: {report['mode']}*")
    lines.append("")
    lines.append("## Per-variant quality (vs the corpus references)")
    lines.append("")
    lines.append("| variant | scored | errors | WER | CER | omission | "
                 "hotword recall |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, s in report["summary"].items():
        lines.append(
            f"| {name} | {s['scored']} | {s['errors']} "
            f"| {_fmt(s['wer_mean'])} | {_fmt(s['cer_mean'])} "
            f"| {_fmt(s['omission_mean'])} "
            f"| {_fmt(s['hotword_recall_mean'])} |")
    lines.append("")
    lines.append("## Deltas vs the raw baseline")
    lines.append("")
    lines.append("| variant | ΔWER | Δhotword recall |")
    lines.append("|---|---|---|")
    for name, c in report["comparison_vs_raw"].items():
        lines.append(f"| {name} | {_fmt(c['wer_delta_vs_raw'])} "
                     f"| {_fmt(c['hotword_recall_delta_vs_raw'])} |")
    lines.append("")
    gate = report["gate"]
    lines.append("## Regression gate")
    lines.append("")
    lines.append(f"*status: {gate['status']}*")
    if "reason" in gate:
        lines.append("")
        lines.append(f"skipped: {gate['reason']}")
    else:
        lines.append("")
        lines.append("| check | passed | detail |")
        lines.append("|---|---|---|")
        for c in gate["checks"]:
            lines.append(f"| {c['check']} | {'yes' if c['passed'] else 'NO'} "
                         f"| {c['detail']} |")
    lines.append("")
    return "\n".join(lines)
