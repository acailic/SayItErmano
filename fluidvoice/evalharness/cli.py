"""CLI for the evaluation harness: ``python -m fluidvoice.evalharness``.

Also reachable as ``sayit-ermano eval-run <args>`` (fluidvoice/cli.py
passes its arguments straight through to :func:`main`).

Subcommands:

- ``run``       execute cases with a transcriber, write report.json/.md
- ``check``     validate manifest(s) (and audio presence) only
- ``fixtures``  materialize the synthetic wavs a manifest describes
- ``soak``      summarize a scripts/soak.py CSV
- ``polish-compare``  score AI-polish variants (local vs cloud) against
                the corpus references + regression gate (#21)

``run`` needs no model for a smoke pass (a built-in stub transcriber
prints a loud warning); real evaluation plugs a backend adapter with
``--transcriber module:factory`` — the harness never imports backends
itself (see docs/eval/README.md).
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Callable

from .manifest import (
    Case,
    ManifestError,
    default_manifest_path,
    load_manifests,
    resolve_manifest_path,
)
from .polish_gate import Polisher, PolishGate, run_polish_eval
from .runner import Transcriber, run_eval
from .soak import read_soak_csv, summarize_soak
from .synth import ensure_case_audio


def _add_corpus_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--manifest", type=Path, action="append", default=[],
                   metavar="PATH",
                   help="manifest file or directory (repeatable; "
                        "default: the packaged synthetic corpus)")
    p.add_argument("--external", type=Path, action="append", default=[],
                   metavar="PATH",
                   help="additional private manifest to merge "
                        "(repeatable; keep private corpora out of git)")


def _load_corpus(args: argparse.Namespace) -> tuple[list[Case], list[Path]]:
    paths = [resolve_manifest_path(p) for p in args.manifest] or \
        [default_manifest_path()]
    for ext in args.external:
        paths.append(resolve_manifest_path(ext))
    return load_manifests(paths), paths


def _load_component(spec: str, what: str) -> Callable:
    """Resolve ``module:factory`` to a factory()'d callable."""
    try:
        mod_name, _, attr = spec.partition(":")
        if not mod_name or not attr:
            raise ValueError("expected 'module:factory'")
        factory: Callable[[], Any] = getattr(
            importlib.import_module(mod_name), attr)
        obj = factory()
        if not callable(obj):
            raise ValueError(f"{spec} produced a non-callable {what}")
        return obj
    except (ImportError, AttributeError, ValueError) as e:
        raise SystemExit(
            f"error: cannot load {what} {spec!r}: {e}\n"
            "(format: 'module:factory' where factory() returns the "
            f"{what} — see docs/eval/README.md)") from e


def _load_transcriber(spec: str) -> Transcriber:
    """Resolve ``module:factory`` to a transcriber (factory() -> callable)."""
    return _load_component(spec, "transcriber")


def _load_polisher(spec: str) -> Polisher:
    """Resolve ``module:factory`` to a polish variant callable."""
    return _load_component(spec, "polisher")


def _stub_transcriber(audio_path: Path, case: Case) -> dict:
    return {"text": ""}


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        cases, paths = _load_corpus(args)
    except (ManifestError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    transcriber: Transcriber
    if args.transcriber:
        transcriber = _load_transcriber(args.transcriber)
    else:
        print("warning: no --transcriber given — using the built-in STUB "
              "(empty transcripts). This only smoke-tests the harness; it "
              "is NOT an evaluation. See docs/eval/README.md.",
              file=sys.stderr)
        transcriber = _stub_transcriber
    soak = None
    if args.soak_csv:
        try:
            soak = summarize_soak(read_soak_csv(args.soak_csv),
                                  source=str(args.soak_csv))
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    report = run_eval(cases, transcriber, out_dir=args.out, manifests=paths,
                      soak=soak, allow_synth=not args.no_synth,
                      split=args.split)
    s = report["summary"]
    print(f"{s['cases']} cases ({s['errors']} errored) -> "
          f"{args.out / 'report.json'} + {args.out / 'report.md'}")
    print(f"  WER mean {_fmt(s['wer_mean'])} · omissions "
          f"{_fmt(s['omission_mean'])} · CER mean "
          f"{_fmt(s['cer_mean'])} · hotword recall "
          f"{_fmt(s['hotword_recall_mean'])} · RTF "
          f"{_fmt(s['real_time_factor_mean'])}")
    g = s["guard"]
    print(f"  guard: {g['false_positive']} false positives / "
          f"{g['false_negative']} false negatives")
    return 1 if s["errors"] else 0


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.3f}"


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        cases, paths = _load_corpus(args)
    except (ManifestError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    missing = [c for c in cases if not c.audio.exists() and c.synth is None]
    for c in missing:
        print(f"error: case '{c.id}': audio not found and no synth recipe: "
              f"{c.audio}", file=sys.stderr)
    print(f"ok: {len(cases)} cases in {len(paths)} manifest(s) valid"
          + (f" ({len(missing)} missing audio)" if missing else ""))
    return 1 if missing else 0


def _cmd_fixtures(args: argparse.Namespace) -> int:
    try:
        cases, _ = _load_corpus(args)
    except (ManifestError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for case in cases:
        if case.synth is None:
            print(f"{case.id}: no synth recipe (audio: {case.audio})")
            continue
        path = ensure_case_audio(case, allow_synth=True)
        print(f"{case.id}: {path}")
    return 0


def _cmd_soak(args: argparse.Namespace) -> int:
    try:
        summary = summarize_soak(read_soak_csv(args.csv),
                                 source=str(args.csv))
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{summary['samples']} samples over {summary['duration_h']:.2f} h")
    print(f"  RSS {summary['rss_start_mb']} -> {summary['rss_end_mb']} MB "
          f"(drift {summary['rss_drift_mb']:+.1f} MB, max "
          f"{summary['rss_max_mb']} MB)")
    cpu_h = summary["cpu_s_per_hour"]
    print(f"  CPU {summary['cpu_total_s']} s total"
          + (f" ({cpu_h:.1f} s/hour)" if cpu_h is not None else ""))
    return 0


def _cmd_polish_compare(args: argparse.Namespace) -> int:
    try:
        cases, paths = _load_corpus(args)
    except (ManifestError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    transcriber = None
    if args.transcriber:
        transcriber = _load_transcriber(args.transcriber)
    else:
        print("warning: no --transcriber given — running in SMOKE mode "
              "(reference passthrough). This only proves the plumbing; "
              "it is NOT an evaluation. See docs/eval/README.md.",
              file=sys.stderr)
    variants: dict[str, Polisher] = {}
    if args.local:
        variants["local"] = _load_polisher(args.local)
    if args.cloud:
        variants["cloud"] = _load_polisher(args.cloud)
    gate = PolishGate(max_wer_degradation=args.max_wer_degradation,
                      max_recall_loss=args.max_recall_loss)
    report = run_polish_eval(cases, variants, transcriber=transcriber,
                             out_dir=args.out, manifests=paths, gate=gate,
                             allow_synth=not args.no_synth)
    s = report["summary"]
    for name, v in s.items():
        print(f"  {name}: WER mean {_fmt(v['wer_mean'])} · "
              f"omission {_fmt(v['omission_mean'])} · "
              f"hotword recall {_fmt(v['hotword_recall_mean'])} · "
              f"{v['errors']} errored")
    g = report["gate"]
    print(f"  gate: {g['status']}"
          + (f" ({g['reason']})" if "reason" in g else ""))
    failed_checks = [c["check"] for c in g.get("checks", ())
                     if not c["passed"]]
    if failed_checks:
        print("  failed checks: " + ", ".join(failed_checks), file=sys.stderr)
    print(f"-> {args.out / 'polish-report.json'} + "
          f"{args.out / 'polish-report.md'}")
    if g["status"] == "failed":
        return 1
    errored = sum(v["errors"] for v in s.values())
    return 1 if errored else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m fluidvoice.evalharness",
        description="Local speech-pipeline evaluation harness "
                    "(WER/CER/hotwords/latency/guard; docs/eval/README.md)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="execute cases, write reports")
    _add_corpus_args(run_p)
    run_p.add_argument("--out", type=Path, default=Path("eval-report"),
                       metavar="DIR", help="output directory "
                       "(default: ./eval-report)")
    run_p.add_argument("--transcriber", metavar="MODULE:FACTORY",
                       help="transcriber factory, e.g. "
                            "'mymodule:make_transcriber' (default: "
                            "stub — harness smoke only)")
    run_p.add_argument("--soak-csv", type=Path, metavar="PATH",
                       help="embed a scripts/soak.py CSV summary in the "
                            "report")
    run_p.add_argument("--split", choices=("train", "heldout"),
                       default=None, metavar="SPLIT",
                       help="label the run's split identity (train/"
                            "heldout; default: full/unlabeled). Guard "
                            "threshold sweeps are computed on train "
                            "runs only — corpus-spec §6")
    run_p.add_argument("--no-synth", action="store_true",
                       help="never generate synthetic fixtures (missing "
                            "audio becomes a case error)")
    run_p.set_defaults(func=_cmd_run)

    check_p = sub.add_parser("check", help="validate manifests only")
    _add_corpus_args(check_p)
    check_p.set_defaults(func=_cmd_check)

    fx_p = sub.add_parser("fixtures",
                          help="materialize synthetic fixture wavs")
    _add_corpus_args(fx_p)
    fx_p.set_defaults(func=_cmd_fixtures)

    soak_p = sub.add_parser("soak", help="summarize a soak CSV")
    soak_p.add_argument("csv", type=Path, help="CSV written by "
                        "scripts/soak.py")
    soak_p.set_defaults(func=_cmd_soak)

    pc_p = sub.add_parser(
        "polish-compare",
        help="compare AI polish variants (local vs cloud) + regression "
             "gate (docs/eval/README.md)")
    _add_corpus_args(pc_p)
    pc_p.add_argument("--out", type=Path, default=Path("eval-polish"),
                      metavar="DIR", help="output directory "
                      "(default: ./eval-polish)")
    pc_p.add_argument("--transcriber", metavar="MODULE:FACTORY",
                      help="transcriber factory producing the RAW "
                      "transcripts (default: none — smoke mode, reference "
                      "passthrough)")
    pc_p.add_argument("--local", metavar="MODULE:FACTORY",
                      help="local polish variant factory (the managed "
                      "llama.cpp path — see docs/eval/README.md)")
    pc_p.add_argument("--cloud", metavar="MODULE:FACTORY",
                      help="cloud polish variant factory (the baseline)")
    pc_p.add_argument("--max-wer-degradation", type=float, default=0.02,
                      metavar="DELTA",
                      help="gate: wer_mean(local) may exceed "
                      "wer_mean(cloud) by at most this much "
                      "(default: %(default)s)")
    pc_p.add_argument("--max-recall-loss", type=float, default=0.0,
                      metavar="DELTA",
                      help="gate: hotword recall may drop vs cloud by at "
                      "most this much (default: %(default)s)")
    pc_p.add_argument("--no-synth", action="store_true",
                      help="never generate synthetic fixtures (missing "
                      "audio becomes a case error)")
    pc_p.set_defaults(func=_cmd_polish_compare)

    args = parser.parse_args(argv)
    return args.func(args)
