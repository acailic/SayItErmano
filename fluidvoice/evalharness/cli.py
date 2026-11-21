"""CLI for the evaluation harness: ``python -m fluidvoice.evalharness``.

Also reachable as ``sayit-ermano eval-run <args>`` (fluidvoice/cli.py
passes its arguments straight through to :func:`main`).

Subcommands:

- ``run``       execute cases with a transcriber, write report.json/.md
- ``polish``    polish-quality bench: providers over the raw-transcript
                corpus, local-vs-cloud regression gate (issue #21)
- ``check``     validate manifest(s) (and audio presence) only
- ``fixtures``  materialize the synthetic wavs a manifest describes
- ``soak``      summarize a scripts/soak.py CSV

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
from .polish import DEFAULT_GATE_MARGIN as DEFAULT_GATE_MARGIN_CLI
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


def _cmd_polish(args: argparse.Namespace) -> int:
    from .polish import (
        CLOUD,
        LOCAL,
        NO_POLISH,
        identity_polisher,
        load_polish_corpus,
        make_endpoint_polisher,
        make_local_polisher,
        run_polish_bench,
    )
    try:
        cases = load_polish_corpus(args.manifest)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    providers: dict = {NO_POLISH: identity_polisher}
    gate = None
    if args.local:
        try:
            providers[LOCAL] = make_local_polisher()
        except Exception as e:  # noqa: BLE001 - actionable CLI error
            print(f"error: local polish provider unavailable: {e}\n"
                  "(download the model in Settings → Models, or run the "
                  "daemon once with local polish on)",
                  file=sys.stderr)
            return 1
    if args.cloud_base_url:
        try:
            providers[CLOUD] = make_endpoint_polisher(
                args.cloud_base_url, args.cloud_model,
                args.cloud_api_key_env)
        except Exception as e:  # noqa: BLE001 - actionable CLI error
            print(f"error: cloud polish provider unavailable: {e}",
                  file=sys.stderr)
            return 1
    if LOCAL in providers and CLOUD in providers:
        gate = (LOCAL, CLOUD)
    elif args.require_gate:
        print("error: the gate needs --local AND --cloud-base-url "
              "(or drop --require-gate for a one-sided report)",
              file=sys.stderr)
        return 1
    report = run_polish_bench(
        cases, providers, out_dir=args.out, gate=gate,
        margin=args.gate_margin,
        provenance={"manifest": str(args.manifest),
                    "gate_margin": args.gate_margin})
    print(f"{len(cases)} cases -> {args.out / 'polish-report.json'} + "
          f"{args.out / 'polish-report.md'}")
    for r in report["providers"]:
        s = r["summary"]
        print(f"  {r['name']}: similarity {s['similarity_mean']:.3f} · "
              f"content recall {s['content_recall_mean']:.3f} · "
              f"{s['exact_matches']}/{s['cases']} exact · "
              f"{s['hard_failures']} hard failures")
    g = report.get("gate")
    if g and g.get("verdict") != "SKIPPED":
        for c in g["checks"]:
            print(f"  gate {c['metric']}: {c['detail']}")
        print(f"gate: {g['verdict']}")
        return 0 if g["verdict"] == "PASS" else 1
    if g:
        print(f"gate: {g.get('verdict')} — {g.get('reason')}")
    return 0 if all(r["summary"]["errors"] == 0
                    for r in report["providers"]) else 1


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

    pol_p = sub.add_parser(
        "polish", help="polish-quality bench + local-vs-cloud gate")
    pol_p.add_argument("--manifest", type=Path, default=None, metavar="TOML",
                       help="polish corpus (default: the packaged "
                            "polish-baseline corpus)")
    pol_p.add_argument("--out", type=Path, default=Path("polish-report"),
                       metavar="DIR", help="output directory "
                       "(default: ./polish-report)")
    pol_p.add_argument("--local", action="store_true",
                       help="run the bundled llama.cpp local polish "
                            "(the daemon's own stack: spawn + health + "
                            "chat GGUF)")
    pol_p.add_argument("--cloud-base-url", metavar="URL",
                       help="cloud/OpenAI-compatible endpoint for the "
                            "cloud baseline side of the gate")
    pol_p.add_argument("--cloud-model", default="", metavar="MODEL",
                       help="cloud model name")
    pol_p.add_argument("--cloud-api-key-env", default="", metavar="ENV",
                       help="env var holding the cloud API key")
    pol_p.add_argument("--gate-margin", type=float,
                       default=DEFAULT_GATE_MARGIN_CLI, metavar="M",
                       help=f"local may trail cloud by this much on "
                            f"similarity/content-recall means "
                            f"(default {DEFAULT_GATE_MARGIN_CLI})")
    pol_p.add_argument("--require-gate", action="store_true",
                       help="fail unless both gate providers ran")
    pol_p.set_defaults(func=_cmd_polish)

    args = parser.parse_args(argv)
    return args.func(args)
