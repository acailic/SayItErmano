#!/usr/bin/env python3
"""Record a perf baseline for the dictation path (quality plan Q8).

Runs the committed synthetic corpus through a real backend in the
daemon process shape — a dedicated worker subprocess loads the model,
takes every case, and reports per-take wall time, audio-seconds/second
throughput, model load time and the worker's peak RSS — then records
``docs/quality/perf-baselines/<ISO-date>-<host-class>.json`` and
regenerates the Markdown summary table beside it (install-matrix
pattern: generated, never hand-edited).

Backend selection follows the app's preference (faster-whisper CPU
int8, falling back to the whisper.cpp binary + tiny ggml model baked
into reference runners); ``--backend`` pins one explicitly. Compare
recorded numbers against a fresh run with ``scripts/perf_gate.py``.

Usage: just perf-baseline   (or: python scripts/perf_baseline.py)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fluidvoice.evalharness import perf  # noqa: E402
from fluidvoice.evalharness.manifest import (  # noqa: E402
    default_manifest_path,
    resolve_manifest_path,
)

# generous: a full corpus pass is seconds; this only guards hangs
WORKER_TIMEOUT_S = 600


def _manifest_paths(args: argparse.Namespace) -> list[Path]:
    return [resolve_manifest_path(p) for p in args.manifest] or \
        [default_manifest_path()]


def _worker_command(backend: str | None, manifests: list[Path],
                    result: Path) -> list[str]:
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker",
           "--result", str(result)]
    if backend:
        cmd += ["--backend", backend]
    for m in manifests:
        cmd += ["--manifest", str(m)]
    return cmd


def run_fresh(manifests: list[Path], backend: str | None = None,
              cwd: Path = ROOT) -> dict:
    """Daemon process shape: a fresh worker subprocess holds the model,
    runs every take and dumps its own measurements (peak RSS included).

    Split so scripts/perf_gate.py compares exactly what this script
    records — one measurement code path, two consumers.
    """
    with tempfile.TemporaryDirectory(prefix="sayit-perf-") as td:
        result = Path(td) / "worker-report.json"
        proc = subprocess.run(
            _worker_command(backend, manifests, result),
            cwd=cwd, timeout=WORKER_TIMEOUT_S)
        if proc.returncode != 0:
            raise SystemExit(
                f"error: perf worker failed (exit {proc.returncode}) — "
                "see stderr above")
        return json.loads(result.read_text(encoding="utf-8"))


def _worker_main(args: argparse.Namespace) -> int:
    from fluidvoice.evalharness.perf_backends import run_perf_worker

    manifests = [resolve_manifest_path(p) for p in args.manifest] or \
        [default_manifest_path()]
    report = run_perf_worker(manifests, backend_name=args.backend)
    Path(args.result).write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="perf_baseline", description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, action="append",
                        default=[], metavar="PATH",
                        help="manifest file or directory (repeatable; "
                             "default: the packaged synthetic corpus)")
    parser.add_argument("--backend", choices=["faster-whisper",
                                              "whisper.cpp"],
                        help="pin the backend (default: app preference "
                             "order — faster-whisper, then whisper.cpp)")
    parser.add_argument("--dir", type=Path, default=perf.BASELINES_DIR,
                        metavar="DIR",
                        help="baselines directory "
                             "(default: docs/quality/perf-baselines)")
    parser.add_argument("--host-class", metavar="ID",
                        help="override the host class in the filename "
                             "(default: <machine>-<cpus>cpu)")
    # hidden: the daemon-shape worker re-invokes this very script
    parser.add_argument("--worker", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.worker:
        return _worker_main(args)

    manifests = _manifest_paths(args)
    report = run_fresh(manifests, backend=args.backend)
    host = args.host_class or report["host_class"]
    path = perf.write_baseline(report, args.dir, host=host)
    perf.regenerate_summary(args.dir)
    m = report["metrics"]
    print(f"perf-baseline: {report['backend']['name']} / "
          f"{report['backend'].get('model', '?')} on {host}")
    print(f"  model load    {m['model_load_s']:.3f} s")
    print(f"  mean take     {m['wall_time_mean_s']:.3f} s")
    print(f"  throughput    {m['audio_seconds_per_second']:.2f} "
          "audio s/s")
    print(f"  peak RSS      {m['peak_rss_mb']:.0f} MB")
    print(f"recorded {path} (summary table regenerated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
