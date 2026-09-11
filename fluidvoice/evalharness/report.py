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
    SUBGROUP_DIMENSIONS,
    UNSET_LABEL,
)

_GUARD_SHORT = {
    GUARD_TRUE_POSITIVE: "TP", GUARD_TRUE_NEGATIVE: "TN",
    GUARD_FALSE_POSITIVE: "FP", GUARD_FALSE_NEGATIVE: "FN",
    "excluded": "exc", "not_scored": "not-run", None: "n/a",
}


def _num(v: float | None, digits: int = 3) -> str:
    return "n/a" if v is None else f"{v:.{digits}f}"


def _txt(v: Any) -> str:
    """Provenance-ish fields: None renders as '(unset)'."""
    return v if isinstance(v, str) and v else UNSET_LABEL


def render_json(report: dict[str, Any]) -> str:
    """Serialized report.json body (stable pretty formatting)."""
    return json.dumps(report, indent=2, ensure_ascii=False)


def _summary_rows(summary: dict[str, Any]) -> list[tuple[str, str]]:
    g = summary.get("guard", {})
    return [
        ("cases", f"{summary.get('cases', 0)} "
                  f"({summary.get('errors', 0)} errored)"),
        ("WER mean", _num(summary.get("wer_mean"))),
        ("omission mean", _num(summary.get("omission_mean"))),
        ("CER mean", _num(summary.get("cer_mean"))),
        ("hotword recall mean", _num(summary.get("hotword_recall_mean"))),
        ("real-time factor mean", _num(summary.get("real_time_factor_mean"))
         + " (>1 = faster than realtime)"),
        ("first-preview latency mean",
         _num(summary.get("first_preview_latency_mean_s")) + " s"),
        ("final latency mean", _num(summary.get("final_latency_mean_s"))
         + " s"),
        ("terminal punctuation mean",
         _num(summary.get("terminal_punctuation_accuracy_mean"))),
        ("sentence-start capitals mean",
         _num(summary.get("sentence_start_capital_accuracy_mean"))),
        ("hallucination words/s mean (negatives)",
         _num(summary.get("hallucination_word_rate_mean"))),
        ("guard true pos / true neg",
         f"{g.get(GUARD_TRUE_POSITIVE, 0)} / {g.get(GUARD_TRUE_NEGATIVE, 0)}"),
        ("guard FALSE POS / FALSE NEG",
         f"{g.get(GUARD_FALSE_POSITIVE, 0)} / "
         f"{g.get(GUARD_FALSE_NEGATIVE, 0)}"),
        ("guard not run / excluded",
         f"{g.get('not_scored', 0)} / {g.get('excluded', 0)}"),
    ]


def _subgroup_tables(subgroups: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    lines.append("## Subgroups")
    lines.append("")
    lines.append("Subgroup means alongside the corpus means above — no "
                 "aggregate-only reporting. `'(unset)'` rows collect "
                 "cases whose manifest lacks the provenance key; a cell "
                 "is `n/a` when no case in the group scores that metric.")
    lines.append("")
    for dim in SUBGROUP_DIMENSIONS:
        rows = [r for r in subgroups if r.get("dimension") == dim]
        if not rows:
            continue
        lines.append(f"### By {dim}")
        lines.append("")
        lines.append("| group | cases | WER | omissions | hotwords | RTF "
                     "| preview s | final s | guard FP/FN |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            g = r.get("guard", {})
            cases = (f"{r.get('cases', 0)} "
                     f"({r.get('errors', 0)} errored)"
                     if r.get("errors") else str(r.get("cases", 0)))
            lines.append(
                f"| {r['group']} | {cases} "
                f"| {_num(r.get('wer_mean'))} "
                f"| {_num(r.get('omission_mean'))} "
                f"| {_num(r.get('hotword_recall_mean'))} "
                f"| {_num(r.get('real_time_factor_mean'))} "
                f"| {_num(r.get('first_preview_latency_mean_s'))} "
                f"| {_num(r.get('final_latency_mean_s'))} "
                f"| {g.get(GUARD_FALSE_POSITIVE, 0)} / "
                f"{g.get(GUARD_FALSE_NEGATIVE, 0)} |")
        lines.append("")
    return lines


def _language_confusion_lines(conf: dict[str, Any]) -> list[str]:
    lines = ["## Language confusion", ""]
    reported = conf.get("reported", 0)
    matches = conf.get("matches", 0)
    unreported = conf.get("unreported") or []
    if not reported and not unreported:
        lines.append("No case reported a detected language.")
        lines.append("")
        return lines
    lines.append(
        f"Match rule: {conf.get('match_rule', '?')}. "
        f"{matches}/{reported} detected languages matched; "
        f"{len(unreported)} unreported.")
    lines.append("")
    lines.append("| expected | detected | match | cases | ids |")
    lines.append("|---|---|---|---|---|")
    for r in conf.get("rows", []):
        lines.append(f"| {r['expected']} | {r['detected']} "
                     f"| {'yes' if r['match'] else 'NO'} | {r['cases']} "
                     f"| {', '.join(r['ids'])} |")
    if unreported:
        lines.append(f"| (any) | (not reported) | n/a | "
                     f"{len(unreported)} | {', '.join(unreported)} |")
    lines.append("")
    return lines


def _guard_sweep_lines(sweep: dict[str, Any] | None) -> list[str]:
    lines = ["## Guard sweep", ""]
    if not sweep:
        lines.append("Not computed: no **train**-labeled cases carried a "
                     "guard score in this run. Held-out cases never enter "
                     "a sweep — thresholds are tuned on train only "
                     "(corpus-spec §6).")
        lines.append("")
        return lines
    lines.append(f"Train-only convention ({sweep['cases']} cases swept): "
                 "flag when `guard_score >= threshold`; the held-out "
                 "split is never swept (corpus-spec §6).")
    lines.append("")
    lines.append("| threshold | flagged | TP | TN | FP | FN |")
    lines.append("|---|---|---|---|---|---|")
    for r in sweep.get("rows", []):
        lines.append(f"| {r['threshold']:.2f} | {r['flagged']} "
                     f"| {r['tp']} | {r['tn']} | {r['fp']} | {r['fn']} |")
    lines.append("")
    return lines


def _negatives_lines(neg: dict[str, Any]) -> list[str]:
    lines = ["## Negative cases", ""]
    rows = neg.get("cases", [])
    if not rows:
        lines.append("No negative (empty-reference) cases in this run.")
        lines.append("")
        return lines
    lines.append("Empty-reference (N1–N5) cases: any produced word is a "
                 "hallucination; words/s is the hallucination word rate.")
    lines.append("")
    lines.append("| id | taxonomy | tokens | audio s | words/s | guard |")
    lines.append("|---|---|---|---|---|---|")
    for r in rows:
        guard = _GUARD_SHORT.get(r["guard_category"],
                                 r["guard_category"] or "n/a")
        lines.append(
            f"| {r['id']} | {_txt(r['taxonomy'])} | {r['tokens']} "
            f"| {_num(r['audio_duration_s'], 1)} "
            f"| {_num(r['hallucination_word_rate'])} | {guard} |")
    lines.append("")
    by_tax = neg.get("hallucination_word_rate_by_taxonomy", {})
    if by_tax:
        lines.append("Hallucination words/s by noise type: "
                     + ", ".join(f"{t} {_num(v)}"
                                 for t, v in by_tax.items()) + ".")
        lines.append("")
    return lines


def _worst_cases_lines(worst: list[dict[str, Any]]) -> list[str]:
    lines = ["## Worst cases", ""]
    if not worst:
        lines.append("No scored cases.")
        lines.append("")
        return lines
    lines.append("The 10 highest-WER cases — failures are named, not "
                 "averaged away:")
    lines.append("")
    lines.append("| id | speaker | lang | taxonomy | WER |")
    lines.append("|---|---|---|---|---|")
    for c in worst:
        lines.append(f"| {c['id']} | {_txt(c['speaker'])} "
                     f"| {c['language']} | {_txt(c['taxonomy'])} "
                     f"| {_num(c['wer'])} |")
    lines.append("")
    return lines


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
    split = report.get("split")
    if split:
        lines.append(f"Split: **{split}**"
                     + (" (guard tuning only ever happens on train runs — "
                        "corpus-spec §6)" if split == "heldout" else ""))
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

    lines.extend(_subgroup_tables(report.get("subgroups", [])))
    lines.extend(_language_confusion_lines(
        report.get("language_confusion", {})))
    lines.extend(_guard_sweep_lines(report.get("guard_sweep")))
    lines.extend(_negatives_lines(report.get("negatives", {})))
    lines.extend(_worst_cases_lines(report.get("worst_cases", [])))

    lines.append("## Cases")
    lines.append("")
    lines.append("| id | lang | WER | omissions | CER | hotwords | RTF "
                 "| preview s | final s | guard |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for c in report.get("cases", []):
        m = c.get("metrics") or {}
        guard = _GUARD_SHORT.get(c.get("guard_category"),
                                 c.get("guard_category") or "n/a")
        if c.get("error"):
            lines.append(f"| {c['id']} | {c.get('language', '?')} | "
                         f"error | error | error | error | error | error "
                         f"| error | error: {c['error']} |")
            continue
        lines.append(
            f"| {c['id']} | {c.get('language', '?')} "
            f"| {_num(m.get('wer'))} | {_num(m.get('omission'))} "
            f"| {_num(m.get('cer'))} "
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
