"""CLI for the evaluation harness: ``python -m fluidvoice.evalharness``.

Also reachable as ``sayit-ermano eval-run <args>`` (fluidvoice/cli.py
passes its arguments straight through to :func:`main`).

Subcommands:

- ``run``       execute cases with a transcriber, write report.json/.md
- ``check``     validate manifest(s) (and audio presence) only
- ``fixtures``  materialize the synthetic wavs a manifest describes
- ``soak``      summarize a scripts/soak.py CSV
- ``polish-gate``  score local vs cloud AI polish over a text corpus
                (regression gate; docs/eval/README.md)

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
from .polish_gate import (
    DEFAULT_CORPUS,
    DEFAULT_TOLERANCE,
    PolishCorpusError,
    load_polish_corpus,
    run_polish_gate,
)
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


def _load_transcriber(spec: str) -> Transcriber:
    """Resolve ``module:factory`` to a transcriber (factory() -> callable)."""
    try:
        mod_name, _, attr = spec.partition(":")
        if not mod_name or not attr:
            raise ValueError("expected 'module:factory'")
        factory: Callable[[], Transcriber] = getattr(
            importlib.import_module(mod_name), attr)
        transcriber = factory()
        if not callable(transcriber):
            raise ValueError(f"{spec} produced a non-callable transcriber")
        return transcriber
    except (ImportError, AttributeError, ValueError) as e:
        raise SystemExit(
            f"error: cannot load --transcriber {spec!r}: {e}\n"
            "(format: 'module:factory' where factory() returns the "
            "transcriber callable — see docs/eval/README.md)") from e


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


def _load_polisher(spec: str):
    """Resolve ``module:factory`` to a polish callable (factory() ->
    polish(text) -> str) — same adapter convention as --transcriber."""
    try:
        mod_name, _, attr = spec.partition(":")
        if not mod_name or not attr:
            raise ValueError("expected 'module:factory'")
        factory = getattr(importlib.import_module(mod_name), attr)
        polisher = factory()
        if not callable(polisher):
            raise ValueError(f"{spec} produced a non-callable polisher")
        return polisher
    except (ImportError, AttributeError, ValueError) as e:
        raise SystemExit(
            f"error: cannot load polisher {spec!r}: {e}\n"
            "(format: 'module:factory' where factory() returns the "
            "polish callable — see docs/eval/README.md)") from e


def _cmd_polish_gate(args: argparse.Namespace) -> int:
    path = args.corpus or DEFAULT_CORPUS
    try:
        cases = load_polish_corpus(path)
    except PolishCorpusError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    local = _load_polisher(args.local)
    cloud = _load_polisher(args.cloud)
    report = run_polish_gate(cases, local, cloud, out_dir=args.out,
                             tolerance=args.tolerance, source=str(path))
    s, v = report["summary"], report["verdict"]
    print(f"{len(cases)} cases -> {args.out / 'polish-gate.json'} + "
          f"{args.out / 'polish-gate.md'}")
    for side in ("local", "cloud"):
        print(f"  {side}: WER mean "
              f"{_fmt(s[side]['wer_mean'])} · errors "
              f"{s[side]['errors']} · refusals {s[side]['refusals']}")
    if v.get("ok"):
        print(f"  gate: PASS (local within {v['tolerance']} of cloud, "
              f"delta {v.get('delta')})")
        return 0
    print("  gate: REGRESSION "
          + (f"(local WER {v.get('local_wer_mean'):.3f} > cloud "
             f"{v.get('cloud_wer_mean'):.3f} + {v['tolerance']})"
             if v.get("local_wer_mean") is not None
             else f"({v.get('reason')})"), file=sys.stderr)
    return 1


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

    pg_p = sub.add_parser(
        "polish-gate", help="local vs cloud AI polish quality gate")
    pg_p.add_argument("--corpus", type=Path, default=None, metavar="PATH",
                      help="polish corpus JSON (default: the packaged "
                           "CC0 text corpus)")
    pg_p.add_argument("--local", required=True, metavar="MODULE:FACTORY",
                      help="local polisher factory (e.g. an adapter over "
                           "the bundled llama.cpp server)")
    pg_p.add_argument("--cloud", required=True, metavar="MODULE:FACTORY",
                      help="cloud-baseline polisher factory")
    pg_p.add_argument("--out", type=Path, default=Path("eval-polish-gate"),
                      metavar="DIR", help="output directory "
                      "(default: ./eval-polish-gate)")
    pg_p.add_argument("--tolerance", type=float,
                      default=DEFAULT_TOLERANCE,
                      metavar="WER", help="absolute WER head-room the local "
                      "polisher gets vs the cloud baseline "
                      "(default 0.02)")
    pg_p.set_defaults(func=_cmd_polish_gate)

    args = parser.parse_args(argv)
    return args.func(args)
