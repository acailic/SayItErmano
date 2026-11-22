"""Perf-baseline harness (quality plan Q8, perf half).

Records how fast the dictation path runs the committed synthetic
corpus on a given host class, and gates future runs against the newest
recorded baseline — the same evidence-file pattern as the install
matrix (docs/quality/install-matrix/).

Split, straight from the eval-harness design:

- **All math and file logic here is model-free** (aggregation, the
  tolerance comparison, baseline validation/resolution, the generated
  Markdown summary) and unit-tested offline with fake backends.
- **Models only produce the timings**: :func:`measure_corpus` times a
  backend object through the daemon process shape (one model load,
  one timed take per corpus case, the worker's peak RSS). Real
  backend adapters live in :mod:`.perf_backends`; the recording CLIs
  are ``scripts/perf_baseline.py`` / ``scripts/perf_gate.py``.

Baseline files are ``docs/quality/perf-baselines/<ISO-date>-<host>.json``
(schema ``perf-baseline/1``). The gate fails (exit 1) when any gated
metric regresses by more than the tolerance (default 25 %, per metric):
``model_load_s``, ``peak_rss_mb``, ``wall_time_mean_s`` (lower is
better) and ``audio_seconds_per_second`` (higher is better — same
direction convention as the harness RTF: > 1 is faster than realtime).
"""
from __future__ import annotations

import json
import math
import os
import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .manifest import Case
from .synth import ensure_case_audio, wav_duration_seconds

#: stamped on every baseline file; bump on breaking format changes
SCHEMA = "perf-baseline/1"

#: default per-metric regression tolerance (0.25 = fail beyond +25 %)
DEFAULT_TOLERANCE = 0.25

#: gated metric -> which direction is a regression
GATED_METRICS: dict[str, str] = {
    "model_load_s": "lower",
    "peak_rss_mb": "lower",
    "wall_time_mean_s": "lower",
    # audio seconds processed per wall second (> 1 = faster than
    # realtime, the same direction as the harness real_time_factor)
    "audio_seconds_per_second": "higher",
}

#: where dated evidence files + the generated summary README live
BASELINES_DIR = Path("docs/quality/perf-baselines")


class PerfBaselineError(ValueError):
    """A baseline (or candidate report) is malformed/ unusable."""


@dataclass(frozen=True)
class Take:
    """One corpus case run through the backend (daemon shape)."""

    case_id: str
    audio_duration_s: float
    wall_time_s: float


def host_class() -> str:
    """Coarse host identity for baseline files (stable per machine).

    Deliberately not a hostname: baselines record the *class* of
    machine (arch + logical CPUs) so runs on identical hardware
    compare, and different hardware lands in different files.
    """
    cpus = os.cpu_count() or 1
    return f"{platform.machine()}-{cpus}cpu"


def environment_info() -> dict[str, Any]:
    """Reproducibility context stamped into every baseline."""
    return {
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


def aggregate(takes: Sequence[Take], *, model_load_s: float,
              peak_rss_mb: float, backend: Mapping[str, Any],
              host: str, corpus: Mapping[str, Any] | None = None,
              environment: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build a baseline dict (schema ``perf-baseline/1``) from takes.

    Aggregation conventions (pinned by unit tests):

    - ``wall_time_mean_s`` — arithmetic mean of per-take wall time;
    - ``audio_seconds_per_second`` — total audio seconds / total wall
      seconds (the honest throughput: one slow take on long audio
      weighs what it weighs, not 1/N);
    - every take also lands verbatim under ``takes`` so a report can
      be re-analyzed without rerunning.
    """
    if not takes:
        raise PerfBaselineError(
            "cannot aggregate an empty take set — record needs at least "
            "one corpus case")
    audio_total = sum(t.audio_duration_s for t in takes)
    wall_total = sum(t.wall_time_s for t in takes)
    return {
        "schema": SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "host_class": host,
        "backend": dict(backend),
        "corpus": dict(corpus or {}),
        "metrics": {
            "model_load_s": float(model_load_s),
            "peak_rss_mb": float(peak_rss_mb),
            "wall_time_mean_s": wall_total / len(takes),
            "audio_seconds_per_second": (
                audio_total / wall_total if wall_total > 0 else 0.0),
        },
        "totals": {
            "cases": len(takes),
            "audio_s": audio_total,
            "wall_s": wall_total,
        },
        "takes": [dict(t.__dict__) for t in takes],
        "environment": dict(environment or environment_info()),
    }


# ── the backend seam ─────────────────────────────────────────────────
#
# measure_corpus() drives any object with:
#   load()                 -> None        one-time model load (timed by
#                                          the caller); backends whose
#                                          shape reloads per take
#                                          (whisper.cpp) no-op here and
#                                          fill .model_load_s during
#                                          transcribe() instead
#   transcribe(wav, case)  -> None        one take, timed by the caller
#   peak_rss_mb()          -> float       worker-process peak RSS
#   describe()             -> dict        backend identity for evidence
#   model_load_s           -> float|None  read AFTER the take loop;
#                                          set = overrides the timed
#                                          load() (per-take mean)


def measure_corpus(cases: Sequence[Case], backend: Any, *,
                   host: str | None = None,
                   manifests: Sequence[Path] = ()) -> dict[str, Any]:
    """Run cases through ``backend`` in the daemon process shape.

    Materializes each case's audio (synthetic fixtures regenerate
    deterministically), times one load + one take per case, then
    aggregates into a baseline dict: ``model_load_s`` prefers the
    adapter's own measurement (``.model_load_s`` — e.g. whisper.cpp's
    parsed per-take load-time mean) and falls back to the wall clock
    around ``load()``. The caller is expected to run this in a
    dedicated worker process so ``peak_rss_mb`` reflects the process
    that actually holds the model.
    """
    import time

    t0 = time.perf_counter()
    backend.load()
    timed_load_s = time.perf_counter() - t0

    takes: list[Take] = []
    for case in cases:
        audio = ensure_case_audio(case)
        duration = wav_duration_seconds(audio) or 0.0
        t1 = time.perf_counter()
        backend.transcribe(audio, case)
        takes.append(Take(case_id=case.id,
                          audio_duration_s=float(duration),
                          wall_time_s=time.perf_counter() - t1))

    model_load_s = getattr(backend, "model_load_s", None)
    if model_load_s is None:
        model_load_s = timed_load_s

    return aggregate(
        takes, model_load_s=float(model_load_s),
        peak_rss_mb=float(backend.peak_rss_mb()),
        backend=backend.describe(), host=host or host_class(),
        corpus={
            "manifests": [str(p) for p in manifests],
            "cases": len(cases),
            "audio_s": sum(t.audio_duration_s for t in takes),
        })


# ── validation, resolution, I/O ──────────────────────────────────────


def validate_baseline(data: Any, source: str = "baseline") -> dict[str, Any]:
    """Check a loaded baseline against schema ``perf-baseline/1``.

    Raises :class:`PerfBaselineError` naming ``source`` on any
    malformation; returns the dict unchanged when valid.
    """
    if not isinstance(data, dict):
        raise PerfBaselineError(f"{source}: not a JSON object")
    schema = data.get("schema")
    if schema != SCHEMA:
        raise PerfBaselineError(
            f"{source}: schema {schema!r} != {SCHEMA!r}")
    for key in ("generated_utc", "host_class", "metrics", "backend"):
        if key not in data:
            raise PerfBaselineError(f"{source}: missing '{key}'")
    if not isinstance(data["host_class"], str) or not data["host_class"]:
        raise PerfBaselineError(f"{source}: 'host_class' must be a "
                                "non-empty string")
    metrics = data["metrics"]
    if not isinstance(metrics, dict):
        raise PerfBaselineError(f"{source}: 'metrics' is not an object")
    for name in GATED_METRICS:
        v = metrics.get(name)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise PerfBaselineError(
                f"{source}: metric '{name}' missing or not a number "
                f"(got {v!r})")
        if not math.isfinite(v) or v < 0:
            raise PerfBaselineError(
                f"{source}: metric '{name}' must be finite and >= 0 "
                f"(got {v!r})")
    return data


def load_baseline(path: Path) -> dict[str, Any]:
    """Read + validate one baseline file (errors name the file)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise PerfBaselineError(f"{path}: cannot read baseline: {e}") from e
    return validate_baseline(data, source=str(path))


def _date_prefix(path: Path) -> str:
    """Leading ``YYYY-MM-DD`` of the stem ('' when absent)."""
    return path.stem[:10] if len(path.stem) >= 10 and \
        path.stem[4] == "-" and path.stem[7] == "-" else ""


def newest_baseline(directory: Path = BASELINES_DIR) -> Path | None:
    """Newest valid baseline in ``directory``, newest-first scan.

    Ordering: the stem's ISO date, then mtime, then name — so a
    same-day re-record (mtime newer) still wins. The newest file is
    validated before anything older is considered: a malformed newest
    baseline is an ERROR, never silently skipped past — corrupted
    evidence must fail loudly, not quietly compare against last week.
    """
    candidates = [p for p in directory.glob("*.json") if p.is_file()]
    candidates.sort(
        key=lambda p: (_date_prefix(p), p.stat().st_mtime, p.name),
        reverse=True)
    for path in candidates:
        load_baseline(path)  # raises on malformed newest
        return path
    return None


def write_baseline(report: Mapping[str, Any], directory: Path = BASELINES_DIR,
                   *, date: str | None = None,
                   host: str | None = None) -> Path:
    """Validate + write a baseline as ``<ISO-date>-<host>.json``.

    ``date``/``host`` default to the report's own stamps. A same-day
    re-record for the same host overwrites the file (the filename IS
    the identity); the caller prints that it did.
    """
    data = validate_baseline(dict(report), source="fresh report")
    day = date or data["generated_utc"][:10]
    who = host or data["host_class"]
    if not day or not who:
        raise PerfBaselineError(f"cannot name baseline from {day!r}/{who!r}")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day}-{who}.json"
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return path


# ── the gate ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricComparison:
    """One gated metric, fresh vs baseline."""

    metric: str
    direction: str            # "lower" | "higher" (better direction)
    baseline: float
    fresh: float
    ratio: float | None       # fresh / baseline (None when baseline == 0)
    regressed: bool

    @property
    def delta_pct(self) -> float | None:
        """Signed change in the *worse* direction, as a fraction.

        +0.3 means "30 % worse" for either direction (lower-metric
        grew / higher-metric shrank); negative = improvement.
        """
        if self.ratio is None:
            return None
        return self.ratio - 1 if self.direction == "lower" \
            else 1 - self.ratio


def compare(fresh: Mapping[str, Any], baseline: Mapping[str, Any], *,
            tolerance: float = DEFAULT_TOLERANCE
            ) -> list[MetricComparison]:
    """Compare two baseline dicts metric by metric.

    Fails on strictly-worse-by-more-than-``tolerance``: a metric
    exactly at the tolerance passes ("> 25 % regresses", not ">= ").
    A zero baseline never regresses for higher-is-better metrics
    (nothing can be below zero) and regresses on ANY nonzero fresh
    value for lower-is-better metrics (infinite relative regression).
    """
    if not 0 <= tolerance < math.inf:
        raise PerfBaselineError(f"tolerance must be >= 0, got {tolerance!r}")
    base = validate_baseline(dict(baseline), source="baseline")["metrics"]
    new = validate_baseline(dict(fresh), source="fresh run")["metrics"]
    out: list[MetricComparison] = []
    for metric, direction in GATED_METRICS.items():
        b, f = float(base[metric]), float(new[metric])
        ratio = f / b if b > 0 else None
        if b == 0:
            regressed = direction == "lower" and f > 0
        elif direction == "lower":
            regressed = f > b * (1 + tolerance)
        else:
            regressed = f < b * (1 - tolerance)
        out.append(MetricComparison(metric=metric, direction=direction,
                                    baseline=b, fresh=f, ratio=ratio,
                                    regressed=regressed))
    return out


# ── generated summary (same pattern as the install matrix) ───────────


def render_markdown_summary(baselines: Sequence[Mapping[str, Any]],
                            *, tolerance: float = DEFAULT_TOLERANCE
                            ) -> str:
    """Markdown summary table over baselines, newest first.

    Generated by ``scripts/perf_baseline.py`` after every record —
    never edit by hand (install-matrix convention).
    """
    lines = [
        "# Perf baselines — dictation path on the synthetic corpus (Q8)",
        "",
        "One evidence file per record date + host class. Generated by",
        "`scripts/perf_baseline.py` (with `just perf-baseline`) — never",
        "edit this table by hand; rerun the recipe after a record.",
        "`just perf-gate` compares a fresh run against the newest file",
        f"and fails on any metric regressing by more than"
        f" {tolerance:.0%}.",
        "",
        "| Date | Host | Backend | Model | load (s) | mean take (s) | "
        "audio s/s | peak RSS (MB) | Evidence |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    rows = sorted(
        baselines,
        key=lambda b: (str(b.get("generated_utc", "")),
                       str(b.get("host_class", ""))), reverse=True)
    for b in rows:
        m = b["metrics"]
        backend = b.get("backend", {})
        lines.append(
            f"| {b.get('generated_utc', '')[:10]} "
            f"| {b.get('host_class', '?')} "
            f"| {backend.get('name', '?')} "
            f"| {backend.get('model', '?')} "
            f"| {m['model_load_s']:.3f} "
            f"| {m['wall_time_mean_s']:.3f} "
            f"| {m['audio_seconds_per_second']:.2f} "
            f"| {m['peak_rss_mb']:.0f} "
            f"| [{b.get('generated_utc', '')[:10]}-"
            f"{b.get('host_class', '?')}.json]"
            f"({b.get('generated_utc', '')[:10]}-"
            f"{b.get('host_class', '?')}.json) |")
    lines += [
        "",
        "Metric directions: load/take/RSS lower is better; audio s/s",
        "higher is better (> 1 = faster than realtime). Peak RSS is the",
        "worker process that holds the model (see the evidence file's",
        "`backend.process_shape`).",
        "",
    ]
    return "\n".join(lines)


def regenerate_summary(directory: Path = BASELINES_DIR) -> bool:
    """Rewrite ``directory/README.md`` from every baseline present.

    Returns True when a file was (re)written; False when there are no
    baselines yet (nothing to summarize, nothing written).
    """
    files = sorted(
        (p for p in directory.glob("*.json") if p.is_file()),
        key=lambda p: (_date_prefix(p), p.stat().st_mtime, p.name),
        reverse=True)
    if not files:
        return False
    baselines = [load_baseline(p) for p in files]
    (directory / "README.md").write_text(
        render_markdown_summary(baselines) + "\n", encoding="utf-8")
    return True
