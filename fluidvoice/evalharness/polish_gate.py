"""Local-vs-cloud AI polish quality gate (issue #21).

The speech eval harness scores audio -> text; this gate scores the
POLISH step: the same raw transcripts pushed through two polishers —
the bundled local one (llama.cpp + chat GGUF) and a cloud baseline —
against expected polished references. It reuses the harness metric
math (WER/CER, same normalization) so polish quality and decode
quality are comparable numbers.

Product-faithful scoring: when a polisher raises or returns a refusal
(the pipeline types the RAW transcript in both cases, see
DictationPipeline._polish), the case's raw text is scored as the
effective output and the event is recorded — the gate measures what
would land in the document, not what an ideal transport would deliver.

Verdict: local polish passes while its mean WER stays within
`tolerance` (default 0.02) of the cloud baseline's — "local polish
must not degrade output vs the cloud baseline corpus". Both a local
and a cloud error on a case are visible per-row; a polisher that
errors on EVERY case degenerates to raw-transcript scoring and will
not pass by accident.

Like representative-model evaluation, running this with real models is
a manual step (docs/eval/README.md §Polish quality gate); the CLI
takes ``module:factory`` adapters, exactly like ``run --transcriber``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .. import __version__
from .metrics import cer, mean, wer

#: default absolute WER head-room the local polisher gets vs the cloud
DEFAULT_TOLERANCE = 0.02

Polisher = Callable[[str], str]

#: default corpus (packaged, CC0-1.0 — self-authored pairs, no
#: recorded speech, nothing third-party)
DEFAULT_CORPUS = Path(__file__).parent / "corpus" / "polish-corpus.json"


class PolishCorpusError(ValueError):
    """A polish corpus file is not a list of {id, raw, expected} cases."""


@dataclass(frozen=True)
class PolishCase:
    id: str
    raw: str
    expected: str
    note: str = ""


def load_polish_corpus(path: Path) -> list[PolishCase]:
    """Parse a polish corpus JSON: [{"id", "raw", "expected", "note"?}].
    Duplicate ids and missing fields are errors, not silent skips."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PolishCorpusError(f"cannot read polish corpus {path}: {e}")
    if not isinstance(data, list) or not data:
        raise PolishCorpusError(
            f"polish corpus {path} must be a non-empty JSON list")
    cases: list[PolishCase] = []
    seen: set[str] = set()
    for i, item in enumerate(data):
        if not isinstance(item, Mapping):
            raise PolishCorpusError(f"case #{i} is not an object")
        cid = str(item.get("id") or "").strip()
        raw = str(item.get("raw") or "")
        expected = str(item.get("expected") or "")
        if not cid or cid in seen:
            raise PolishCorpusError(
                f"case #{i} has a missing or duplicate id ({cid!r})")
        if not raw.strip() or not expected.strip():
            raise PolishCorpusError(
                f"case {cid!r}: 'raw' and 'expected' must be non-empty")
        seen.add(cid)
        cases.append(PolishCase(id=cid, raw=raw, expected=expected,
                                note=str(item.get("note") or "")))
    return cases


def _is_refusal(text: str) -> bool:
    """The product's refusal guardrail signal (processing.refusal),
    applied to gate outputs exactly like the pipeline applies it."""
    from ..processing.refusal import is_refusal
    return bool(text) and is_refusal(text)


def _effective(case: PolishCase, polished: str) -> str:
    """What the pipeline would TYPE: the polish when it passes the
    guardrails, the raw transcript otherwise."""
    if _is_refusal(polished):
        return case.raw
    return polished


def _score(case: PolishCase, polished: str) -> dict:
    eff = _effective(case, polished)
    return {
        "polished": polished,
        "effective": eff,
        "refused": _is_refusal(polished) or None,
        "wer": wer(case.expected, eff),
        "cer": cer(case.expected, eff),
    }


def _run_side(cases: list[PolishCase], polish: Polisher) -> list[dict]:
    rows: list[dict] = []
    for case in cases:
        row: dict = {"id": case.id, "error": None}
        try:
            polished = str(polish(case.raw) or "")
        except Exception as e:  # noqa: BLE001 - the product types raw on error
            row["error"] = f"{type(e).__name__}: {e}"
            polished = case.raw  # pipeline fallback: raw transcript
        row.update(_score(case, polished))
        rows.append(row)
    return rows


def _side_summary(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["error"] is None]
    return {
        "cases": len(rows),
        "errors": len(rows) - len(ok),
        "refusals": sum(1 for r in rows if r["refused"]),
        "wer_mean": mean(r["wer"] for r in rows),
        "cer_mean": mean(r["cer"] for r in rows),
    }


def run_polish_gate(cases: list[PolishCase], local: Polisher,
                    cloud: Polisher, *, out_dir: Path,
                    tolerance: float = DEFAULT_TOLERANCE,
                    source: str = "") -> dict:
    """Score both polishers over the corpus, write ``polish-gate.json``
    + ``polish-gate.md`` to ``out_dir``, and return the report dict
    (``report["verdict"]["ok"]`` is the regression gate)."""
    local_rows = _run_side(cases, local)
    cloud_rows = _run_side(cases, cloud)
    local_sum = _side_summary(local_rows)
    cloud_sum = _side_summary(cloud_rows)
    lw, cw = local_sum["wer_mean"], cloud_sum["wer_mean"]
    verdict: dict = {"tolerance": tolerance}
    if lw is None or cw is None:
        verdict.update(ok=False,
                       reason="no scoreable cases on one of the sides")
    elif local_sum["errors"] == local_sum["cases"] and cases:
        # a completely broken local polisher must not pass by accident
        # (its raw-transcript fallback could tie an easy corpus)
        verdict.update(ok=False,
                       local_wer_mean=lw, cloud_wer_mean=cw,
                       delta=round(lw - cw, 4),
                       reason="the local polisher errored on every case "
                              "(raw transcripts were scored instead)")
    else:
        verdict.update(ok=bool(lw <= cw + tolerance),
                       local_wer_mean=lw, cloud_wer_mean=cw,
                       delta=round(lw - cw, 4))
    report = {
        "harness_version": __version__,
        "generated_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "source": source,
        "conventions": {
            "wer": "word-level levenshtein / expected words over the "
                   "EFFECTIVE typed text (a refused/errored polish scores "
                   "the raw transcript, mirroring DictationPipeline)",
            "gate": f"pass while local wer_mean <= cloud wer_mean + "
                    f"{tolerance} (absolute head-room)",
        },
        "summary": {"local": local_sum, "cloud": cloud_sum},
        "verdict": verdict,
        "cases": [
            {"id": c.id, "raw": c.raw, "expected": c.expected,
             "note": c.note, "local": lr, "cloud": cr}
            for c, lr, cr in zip(cases, local_rows, cloud_rows)
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "polish-gate.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (out_dir / "polish-gate.md").write_text(
        render_polish_markdown(report) + "\n", encoding="utf-8")
    return report


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.3f}"


def render_polish_markdown(report: dict) -> str:
    """Readable rendering of a polish-gate report."""
    s = report["summary"]
    v = report["verdict"]
    lines = [
        "# AI polish quality gate — local vs cloud",
        "",
        f"_generated {report['generated_utc']} · harness "
        f"{report['harness_version']}"
        + (f" · corpus {report['source']}" if report.get("source") else "")
        + "_",
        "",
        "| side | cases | errors | refusals | WER mean | CER mean |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| local | {s['local']['cases']} | {s['local']['errors']} | "
        f"{s['local']['refusals']} | {_fmt(s['local']['wer_mean'])} | "
        f"{_fmt(s['local']['cer_mean'])} |",
        f"| cloud | {s['cloud']['cases']} | {s['cloud']['errors']} | "
        f"{s['cloud']['refusals']} | {_fmt(s['cloud']['wer_mean'])} | "
        f"{_fmt(s['cloud']['cer_mean'])} |",
        "",
        f"**verdict: {'PASS' if v.get('ok') else 'REGRESSION'}** — "
        f"local WER {_fmt(v.get('local_wer_mean'))} vs cloud "
        f"{_fmt(v.get('cloud_wer_mean'))} (tolerance {v['tolerance']}, "
        f"delta {v.get('delta', 'n/a')})"
        + (f" — {v['reason']}" if v.get("reason") else ""),
        "",
        "## Per-case (WER vs expected)",
        "",
        "| id | local WER | cloud WER | local | cloud | flags |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for c in report["cases"]:
        lr, cr = c["local"], c["cloud"]
        flags = []
        if lr["error"]:
            flags.append("local-error")
        if cr["error"]:
            flags.append("cloud-error")
        if lr["refused"]:
            flags.append("local-refusal")
        if cr["refused"]:
            flags.append("cloud-refusal")
        lines.append(
            f"| {c['id']} | {lr['wer']:.3f} | {cr['wer']:.3f} "
            f"| {lr['polished'][:60]!r} | {cr['polished'][:60]!r} "
            f"| {', '.join(flags) or '—'} |")
    return "\n".join(lines)
