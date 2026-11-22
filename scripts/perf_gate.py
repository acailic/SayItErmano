#!/usr/bin/env python3
"""Gate a fresh perf run against the newest recorded baseline (Q8).

Runs the same measurement ``scripts/perf_baseline.py`` records (same
worker, same corpus) and compares it metric by metric with the newest
baseline in ``docs/quality/perf-baselines/``. Fails (exit 1) when any
gated metric regresses by more than the tolerance — default 25 %:

  lower is better   model_load_s, peak_rss_mb, wall_time_mean_s
  higher is better  audio_seconds_per_second

Exit codes: 0 clean, 1 regression, 2 setup/error (no baseline, worker
failure, malformed baseline — a corrupted newest baseline is never
silently skipped past).

Usage: just perf-gate   (or: python scripts/perf_gate.py [--baseline
PATH] [--tolerance 0.25])
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# sibling-script import (same pattern as license_webhook.py)
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from perf_baseline import run_fresh  # noqa: E402

from fluidvoice.evalharness import perf  # noqa: E402
from fluidvoice.evalharness.manifest import (  # noqa: E402
    default_manifest_path,
    resolve_manifest_path,
)

#: the fresh run lands here for post-mortem (build/ is gitignored)
LAST_RUN = Path("build/perf-gate-last.json")


def _print_table(comparisons: list[perf.MetricComparison]) -> None:
    print(f"{'metric':<24} {'baseline':>10} {'fresh':>10} "
          f"{'delta':>8}  verdict")
    for c in comparisons:
        delta = "n/a" if c.delta_pct is None else \
            f"{c.delta_pct:+.0%}"
        verdict = "REGRESSED" if c.regressed else "ok"
        print(f"{c.metric:<24} {c.baseline:>10.3f} {c.fresh:>10.3f} "
              f"{delta:>8}  {verdict}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="perf_gate", description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, action="append",
                        default=[], metavar="PATH",
                        help="manifest file or directory (repeatable; "
                             "default: the packaged synthetic corpus)")
    parser.add_argument("--backend", choices=["faster-whisper",
                                              "whisper.cpp"],
                        help="pin the backend (default: app preference "
                             "order — must match the recorded baseline's)")
    parser.add_argument("--baseline", type=Path, metavar="PATH",
                        help="compare against this baseline (default: "
                             "newest in --dir)")
    parser.add_argument("--dir", type=Path, default=perf.BASELINES_DIR,
                        metavar="DIR",
                        help="baselines directory "
                             "(default: docs/quality/perf-baselines)")
    parser.add_argument("--tolerance", type=float,
                        default=perf.DEFAULT_TOLERANCE, metavar="FRAC",
                        help="max per-metric regression before failing "
                             "(default: %(default)s = 25%%)")
    args = parser.parse_args(argv)

    try:
        baseline_path = args.baseline or perf.newest_baseline(args.dir)
        if baseline_path is None:
            print(f"error: no recorded baseline in {args.dir} — record "
                  "one first (just perf-baseline)", file=sys.stderr)
            return 2
        baseline = perf.load_baseline(baseline_path)
        manifests = [resolve_manifest_path(p) for p in args.manifest] or \
            [default_manifest_path()]
        fresh = run_fresh(manifests, backend=args.backend)
        comparisons = perf.compare(fresh, baseline,
                                   tolerance=args.tolerance)
    except (perf.PerfBaselineError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    LAST_RUN.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN.write_text(json.dumps(fresh, indent=2) + "\n",
                        encoding="utf-8")

    b = baseline["backend"]
    print(f"perf-gate: fresh run vs {baseline_path.name} "
          f"(baseline {b.get('name', '?')}/{b.get('model', '?')} on "
          f"{baseline['host_class']}, tolerance "
          f"{args.tolerance:.0%})")
    _print_table(comparisons)
    if any(c.regressed for c in comparisons):
        regressed = ", ".join(c.metric for c in comparisons
                              if c.regressed)
        print(f"FAIL: regression beyond tolerance: {regressed}")
        return 1
    print("perf-gate: clean — no metric regressed beyond tolerance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
