"""Render an evaluation report as JSON (runner writes it) and Markdown.

The Markdown follows the repo docs style: ATX headers, pipe tables,
prose wrapped ~76 columns. Numbers use a fixed three-decimal format;
unscored values render as ``n/a`` so an empty cell is never ambiguous.
"""
from __future__ import annotations

import json
from typing import Any

from .metrics import (
    GUARD_FALSE_NEGATIVE,
    GUARD_FALSE_POSITIVE,
    GUARD_TRUE_NEGATIVE,
    GUARD_TRUE_POSITIVE,
)

_GUARD_SHORT = {
    GUARD_TRUE_POSITIVE: "TP", GUARD_TRUE_NEGATIVE: "TN",
    GUARD_FALSE_POSITIVE: "FP", GUARD_FALSE_NEGATIVE: "FN",
    "excluded": "exc", "not_scored": "not-run", None: "n/a",
}


def _num(v: float | None, digits: int = 3) -> str:
    return "n/a" if v is None else f"{v:.{digits}f}"


def render_json(report: dict[str, Any]) -> str:
    """Serialized report.json body (stable pretty formatting)."""
    return json.dumps(report, indent=2, ensure_ascii=False)


def _summary_rows(summary: dict[str, Any]) -> list[tuple[str, str]]:
    g = summary.get("guard", {})
    return [
        ("cases", f"{summary.get('cases', 0)} "
                  f"({summary.get('errors', 0)} errored)"),
        ("WER mean", _num(summary.get("wer_mean"))),
        ("CER mean", _num(summary.get("cer_mean"))),
        ("hotword recall mean", _num(summary.get("hotword_recall_mean"))),
        ("real-time factor mean", _num(summary.get("real_time_factor_mean"))
         + " (>1 = faster than realtime)"),
        ("first-preview latency mean",
         _num(summary.get("first_preview_latency_mean_s")) + " s"),
        ("final latency mean", _num(summary.get("final_latency_mean_s"))
         + " s"),
        ("guard true pos / true neg",
         f"{g.get(GUARD_TRUE_POSITIVE, 0)} / {g.get(GUARD_TRUE_NEGATIVE, 0)}"),
        ("guard FALSE POS / FALSE NEG",
         f"{g.get(GUARD_FALSE_POSITIVE, 0)} / "
         f"{g.get(GUARD_FALSE_NEGATIVE, 0)}"),
        ("guard not run / excluded",
         f"{g.get('not_scored', 0)} / {g.get('excluded', 0)}"),
    ]


def render_markdown(report: dict[str, Any]) -> str:
    """Human-readable report.md body."""
    lines: list[str] = []
    lines.append(f"# Speech evaluation report")
    lines.append("")
    conv = report.get("conventions") or {}
    rtf_note = conv.get("real_time_factor", "see docs/eval/README.md")
    lines.append(f"Generated {report.get('generated_utc', '?')} UTC by "
                 f"evalharness {report.get('harness_version', '?')} · "
                 f"RTF: {rtf_note}")
    manifests = report.get("manifests") or []
    if manifests:
        lines.append("")
        lines.append("Manifests: " + ", ".join(f"`{m}`" for m in manifests))
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    for name, value in _summary_rows(report.get("summary", {})):
        lines.append(f"| {name} | {value} |")
    lines.append("")

    lines.append("## Cases")
    lines.append("")
    lines.append("| id | lang | WER | CER | hotwords | RTF | preview s "
                 "| final s | guard |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for c in report.get("cases", []):
        m = c.get("metrics") or {}
        guard = _GUARD_SHORT.get(c.get("guard_category"),
                                 c.get("guard_category") or "n/a")
        if c.get("error"):
            lines.append(f"| {c['id']} | {c.get('language', '?')} | "
                         f"error | error | error | error | error | error "
                         f"| error: {c['error']} |")
            continue
        lines.append(
            f"| {c['id']} | {c.get('language', '?')} "
            f"| {_num(m.get('wer'))} | {_num(m.get('cer'))} "
            f"| {_num(m.get('hotword_recall'))} "
            f"| {_num(m.get('real_time_factor'))} "
            f"| {_num(m.get('first_preview_latency_s'))} "
            f"| {_num(m.get('final_latency_s'))} "
            f"| {guard} |")
    lines.append("")

    soak = report.get("soak")
    if soak:
        lines.append("## Soak")
        lines.append("")
        lines.append(f"Source: `{soak.get('source', '?')}` "
                     f"({soak.get('samples', 0)} samples over "
                     f"{_num(soak.get('duration_h'), 2)} h)")
        lines.append("")
        lines.append("| metric | value |")
        lines.append("|---|---|")
        lines.append(f"| RSS start / end | {_num(soak.get('rss_start_mb'), 1)}"
                     f" / {_num(soak.get('rss_end_mb'), 1)} MB |")
        lines.append(f"| RSS drift | {_num(soak.get('rss_drift_mb'), 1)} MB"
                     " |")
        lines.append(f"| CPU | {_num(soak.get('cpu_s_per_hour'), 1)} s/hour"
                     " |")
        lines.append("")

    lines.append("Conventions and the release policy behind this report: "
                 "docs/eval/README.md.")
    return "\n".join(lines)
