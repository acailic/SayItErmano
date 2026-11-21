"""Polish-quality bench: compare AI-polish providers over a raw-transcript
corpus — the issue #21 regression gate ("local polish must not degrade
output vs the cloud baseline").

Corpus (``corpus/polish-baseline.toml``): ``{id, raw, expected}`` triplets
— `raw` is a realistic disfluent dictation transcript, `expected` the
cloud-baseline polish written to the FluidVoice dictation-prompt
behavior. The corpus is the shared yardstick: BOTH providers are scored
against the same references, so reference-wording bias cancels in the
gate comparison.

Metrics per case (pure functions, hand-verifiable; normalization and
Levenshtein reused from :mod:`fluidvoice.evalharness.metrics`):

- **similarity** = 1 − CER(expected, output), clamped at 0 — character
  closeness to the reference polish.
- **content_recall** = fraction of normalized reference words present in
  the output (exact token membership). Meaning loss (a dropped "not", a
  lost number, swapped names) is the failure mode that matters for
  dictation; refusals are already caught by the pipeline guardrail but
  gate here too, as hard failures.
- **exact** = normalized equality. **Hard failures**: empty or
  refusal-like outputs.

Gate (``evaluate_gate``): local vs cloud on the same corpus —
PASS iff local's content-recall and similarity means are each within
``margin`` (default 0.05) of cloud's, and local has zero hard failures.
"""
from __future__ import annotations

import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .metrics import cer, normalize_chars, normalize_words

#: default corpus packaged with the harness (beside manifest.toml)
DEFAULT_POLISH_CORPUS = Path(__file__).parent / "corpus" / \
    "polish-baseline.toml"

#: gate default: local may trail cloud by this much on either mean
DEFAULT_GATE_MARGIN = 0.05

Polisher = Callable[[str], str]


@dataclass(frozen=True)
class PolishCase:
    id: str
    raw: str
    expected: str
    tags: tuple[str, ...] = ()


def load_polish_corpus(path: Path | None = None) -> list[PolishCase]:
    """Load + validate the polish corpus (loud errors, manifest.py
    discipline): non-empty unique ids, non-empty raw/expected."""
    p = Path(path) if path is not None else DEFAULT_POLISH_CORPUS
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read polish corpus {p}: {e}") from e
    seen: set[str] = set()
    out: list[PolishCase] = []
    for i, entry in enumerate(data.get("case", [])):
        cid = str(entry.get("id", "")).strip()
        raw = str(entry.get("raw", ""))
        expected = str(entry.get("expected", ""))
        if not cid or cid in seen:
            raise ValueError(f"polish corpus case #{i}: empty/duplicate id")
        if not raw.strip() or not expected.strip():
            raise ValueError(f"polish corpus case {cid!r}: raw and expected "
                             "must be non-empty")
        seen.add(cid)
        out.append(PolishCase(id=cid, raw=raw, expected=expected,
                              tags=tuple(str(t) for t in
                                         entry.get("tags", []))))
    if not out:
        raise ValueError(f"polish corpus {p} has no cases")
    return out


# -- scoring ------------------------------------------------------------------


def _is_refusal(text: str) -> bool:
    from ..processing.refusal import is_refusal
    return is_refusal(text)


def polish_case_metrics(output: str, expected: str) -> dict:
    """Score one polished output against the reference polish."""
    empty = not (output or "").strip()
    return {
        "similarity": max(0.0, 1.0 - cer(expected, output or "")),
        "content_recall": _recall(expected, output or ""),
        "exact": normalize_chars(expected) == normalize_chars(output or ""),
        "empty": empty,
        "refusal": (not empty) and _is_refusal(output),
    }


def _recall(expected: str, output: str) -> float:
    """Fraction of reference words present in the output (exact token
    membership — a substring test would count "at" inside "that").
    Deliberately stricter than an alignment: a polish that replaces the
    meaning-bearing words scores 0 on them, whichever way Levenshtein
    breaks its ties."""
    ref = normalize_words(expected)
    if not ref:
        return 0.0
    have = set(normalize_words(output))
    return sum(1 for w in ref if w in have) / len(ref)


def run_provider(name: str, cases: list[PolishCase], polisher: Polisher,
                 baseline_raw: bool = False) -> dict:
    """Run one polish provider over the corpus. Per-case errors (provider
    exceptions) are recorded, never raised - a provider that fails every
    case scores as such instead of killing the bench. `baseline_raw`
    marks the no-polish identity row (raw transcript scored against the
    references: the floor any polish must beat)."""
    per_case: list[dict] = []
    hard = errors = 0
    for case in cases:
        row = {"id": case.id, "provider": name}
        try:
            output = case.raw if baseline_raw else polisher(case.raw)
            m = polish_case_metrics(output, case.expected)
            row.update(m, output=str(output))
            if m["empty"] or m["refusal"]:
                hard += 1
        except Exception as e:  # noqa: BLE001 - recorded per case
            errors += 1
            hard += 1
            row.update({"similarity": 0.0, "content_recall": 0.0,
                        "exact": False, "empty": False, "refusal": False,
                        "output": "", "error": f"{type(e).__name__}: {e}"})
        per_case.append(row)
    return {"name": name, "baseline_raw": baseline_raw,
            "per_case": per_case,
            "summary": _summary_of(per_case, hard=hard, errors=errors)}


def _summary_of(per_case: list[dict], *, hard: int, errors: int) -> dict:
    def mean(key: str) -> float:
        vals = [r[key] for r in per_case if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else 0.0

    return {
        "cases": len(per_case),
        "similarity_mean": round(mean("similarity"), 4),
        "content_recall_mean": round(mean("content_recall"), 4),
        "exact_matches": sum(1 for r in per_case if r.get("exact")),
        "hard_failures": hard,
        "errors": errors,
    }


def evaluate_gate(local: dict, cloud: dict,
                  margin: float = DEFAULT_GATE_MARGIN) -> dict:
    """The regression gate: local polish must not degrade output vs the
    cloud baseline on the same corpus. PASS requires (a) content-recall
    mean and (b) similarity mean each within `margin` of cloud's, and
    (c) zero hard failures (empty/refusal/errored cases) locally."""
    ls, cs = local["summary"], cloud["summary"]
    reasons = []

    def _check(metric: str) -> None:
        delta = round(cs[metric] - ls[metric], 4)
        ok = delta <= margin
        reasons.append({
            "metric": metric,
            "local": ls[metric],
            "cloud": cs[metric],
            "delta": delta,
            "within_margin": ok,
            "detail": f"local {ls[metric]:.3f} vs cloud {cs[metric]:.3f} "
                      f"(cloud − local = {delta:+.3f} ≤ {margin:.3f} "
                      f"required) — {'ok' if ok else 'REGRESSION'}",
        })

    _check("content_recall_mean")
    _check("similarity_mean")
    hard_ok = ls["hard_failures"] == 0
    if not hard_ok:
        reasons.append({
            "metric": "hard_failures",
            "local": ls["hard_failures"],
            "cloud": cs["hard_failures"],
            "delta": None,
            "within_margin": False,
            "detail": f"local had {ls['hard_failures']} hard failures "
                      "(empty/refusal/errored cases) — must be 0",
        })
    passed = all(r["within_margin"] for r in reasons)
    return {"verdict": "PASS" if passed else "FAIL",
            "margin": margin,
            "checks": reasons,
            "summary": f"local polish vs cloud baseline: "
                       f"{'PASS' if passed else 'FAIL'} "
                       f"(margin {margin:.3f})"}


# -- providers -----------------------------------------------------------------


def identity_polisher(text: str) -> str:
    """The no-polish baseline (the raw transcript itself)."""
    return text


def make_local_polisher(cfg: dict | None = None) -> Polisher:
    """Polish through the bundled llama.cpp server (the daemon's own
    path: LocalServer spawn/health + AIClient with the local override).
    Without an explicit cfg the loaded config is used with local polish
    forced ON — the caller asked for the local bench. Raises
    LocalServerError up front when the model is not downloaded."""
    from ..ai.client import AIClient
    from ..ai.local import LocalServer, local_ai_overrides
    from ..config import load_config
    if cfg is None:
        base = load_config()
        base["ai"] = {**base.get("ai", {}), "enabled": True,
                      "local_polish": True}
    else:
        base = cfg
    srv = LocalServer(base)
    srv.ensure_ready()  # fail fast with an actionable error
    effective = {"ai": {**base.get("ai", {})}}
    effective["ai"].update(local_ai_overrides(base))
    client = AIClient(effective)
    _touch = srv.touch_activity

    def polish(text: str) -> str:
        _touch()
        return client.polish(text)

    def _close() -> None:
        srv.stop("bench done")

    polish.close = _close  # type: ignore[attr-defined]
    return polish


def close_polisher(polisher: Polisher) -> None:
    """Stop a provider's resources (the local polisher's llama-server).
    Anything without a close hook (endpoint/identity) is a no-op."""
    close = getattr(polisher, "close", None)
    if close is not None:
        try:
            close()
        except Exception:  # noqa: BLE001,S110 - teardown is best-effort
            pass


def make_endpoint_polisher(base_url: str, model: str,
                           api_key_env: str = "") -> Polisher:
    """Polish through any OpenAI-compatible endpoint (the cloud side of
    the gate; also fits a self-hosted llama.cpp/Ollama for triage)."""
    import os

    from ..ai.client import AIClient
    cfg = {"ai": {"enabled": True, "base_url": base_url, "model": model,
                  "api_key_env": api_key_env, "api_key":
                  os.environ.get(api_key_env, "") if api_key_env else ""}}
    client = AIClient(cfg)
    if not client.configured:
        raise ValueError("cloud polisher needs a base URL and model")
    return client.polish


# -- bench + report --------------------------------------------------------------


def run_polish_bench(cases: list[PolishCase],
                     providers: dict[str, Polisher],
                     *, out_dir: Path,
                     gate: tuple[str, str] | None = None,
                     margin: float = DEFAULT_GATE_MARGIN,
                     provenance: dict | None = None) -> dict:
    """Run every provider over the corpus, evaluate the gate (when both
    its providers ran), write polish-report.json/.md, return the report."""
    results = []
    try:
        for name, polisher in providers.items():
            results.append(run_provider(name, cases, polisher,
                                        baseline_raw=name.startswith(
                                            NO_POLISH)))
    finally:
        for polisher in providers.values():
            close_polisher(polisher)  # the local server never outlives a bench
    report: dict = {
        "kind": "polish-bench",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "cases": len(cases),
        "gate": None,
        "provenance": provenance or {},
        "providers": results,
    }
    if gate is not None:
        lk, ck = gate
        by = {r["name"]: r for r in results}
        if lk in by and ck in by:
            report["gate"] = evaluate_gate(by[lk], by[ck], margin)
        else:
            report["gate"] = {
                "verdict": "SKIPPED",
                "reason": f"gate needs providers {lk!r} and {ck!r}; "
                          f"ran: {sorted(by)}",
            }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "polish-report.json").write_text(
        _render_json(report), encoding="utf-8")
    (out_dir / "polish-report.md").write_text(
        render_markdown(report), encoding="utf-8")
    return report


NO_POLISH = "no-polish (baseline)"
LOCAL = "local"
CLOUD = "cloud"


def _render_json(report: dict) -> str:
    import json
    return json.dumps(report, indent=2, ensure_ascii=False)


def render_markdown(report: dict) -> str:
    """Docs-style Markdown: provenance, summary table, gate verdict with
    per-check reasons, per-case table (report.py conventions)."""
    lines = ["# Polish quality report", ""]
    lines += [f"- generated: {report['generated']}",
              f"- cases: {report['cases']}",
              f"- providers: "
              f"{', '.join(r['name'] for r in report['providers'])}"]
    for k, v in (report.get("provenance") or {}).items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Summary", "",
              "| provider | similarity mean | content recall mean | "
              "exact | hard failures | errors |",
              "|---|---|---|---|---|---|"]
    for r in report["providers"]:
        s = r["summary"]
        lines.append(
            f"| {r['name']} | {s['similarity_mean']:.3f} "
            f"| {s['content_recall_mean']:.3f} "
            f"| {s['exact_matches']}/{s['cases']} "
            f"| {s['hard_failures']} | {s['errors']} |")
    gate = report.get("gate")
    lines += ["", "## Gate (local vs cloud)", ""]
    if not gate:
        lines.append("no gate requested")
    elif gate.get("verdict") == "SKIPPED":
        lines.append(f"SKIPPED — {gate.get('reason')}")
    else:
        lines.append(f"**{gate['verdict']}** (margin "
                     f"{gate['margin']:.3f}) — " + "; ".join(
                         c["detail"] for c in gate["checks"]))
    lines += ["", "## Per-case", "",
              "| case | provider | similarity | recall | exact | output |",
              "|---|---|---|---|---|---|"]
    for r in report["providers"]:
        for row in r["per_case"]:
            out = str(row.get("output", ""))
            out = out.replace("|", "\\|").replace("\n", " ")
            if len(out) > 60:
                out = out[:57] + "..."
            err = f" ⚠ {row['error']}" if row.get("error") else ""
            lines.append(
                f"| {row['id']} | {row['provider']} "
                f"| {row['similarity']:.3f} | {row['content_recall']:.3f} "
                f"| {'✓' if row.get('exact') else ''} | {out}{err} |")
    return "\n".join(lines) + "\n"
