"""CLI for the recording-corpus tooling: ``python -m scripts.corpus``.

Subcommands (exit codes: 0 = ok, warnings allowed; 1 = validation
error or guard violation; 2 = usage):

- ``init``         scaffold a recording batch manifest from the template
- ``validate``     check a batch manifest (fields, taxonomy counts,
                   duplicates, audio existence/placement, consent refs)
- ``export``       bridge a batch manifest to the harness manifest.toml
- ``split``        compute and seal the deterministic held-out split
- ``check-split``  assert train manifests contain ZERO held-out ids

The recording flow: ``init`` → collect consent (docs/eval/consent-text.md)
→ record → fill rows → ``validate`` → ``export`` → ``split`` once, then
``check-split`` before any tuning run. See docs/eval/corpus-spec.md.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .exporter import export_harness_manifest
from .model import ManifestError, load_batch_manifest
from .scaffold import DEFAULT_ROOT, MANIFEST_NAME, scaffold_batch_manifest
from .split import (
    SEALED_FILENAME,
    SplitError,
    assert_no_held_out,
    load_sealed,
    seal,
    write_sealed,
)
from .validator import format_findings, validate_batch


def _cmd_init(args: argparse.Namespace) -> int:
    out_dir = Path(args.root) / args.name
    try:
        path = scaffold_batch_manifest(args.name, out_dir, force=args.force,
                                       created=args.date)
    except ManifestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"scaffolded {path} (audio dir: {out_dir / 'audio'})")
    print("next: read the file's _read_me, collect signed consent "
          "(docs/eval/consent-text.md), record, then:")
    print(f"  python -m scripts.corpus validate {path}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    status = 0
    for path in args.manifest:
        try:
            batch = load_batch_manifest(Path(path))
        except ManifestError as e:
            print(f"error: {e}", file=sys.stderr)
            status = 1
            continue
        findings = validate_batch(batch)
        print(format_findings(findings, Path(path)))
        if any(f.is_error for f in findings):
            status = 1
    return status


def _cmd_export(args: argparse.Namespace) -> int:
    try:
        batch = load_batch_manifest(Path(args.manifest))
        out = export_harness_manifest(batch, args.out,
                                      include_planned=args.all)
    except (ManifestError, ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"exported {out} (load with: python -m fluidvoice.evalharness "
          f"check --external {out})")
    return 0


def _cmd_split(args: argparse.Namespace) -> int:
    out = (Path(args.out) if args.out
           else Path(args.manifest).resolve().parent / SEALED_FILENAME)
    try:
        batch = load_batch_manifest(Path(args.manifest))
        cases = [c for c in batch.cases if c.status != "rejected"]
        if out.exists() and not args.force:
            raise SplitError(
                f"{out} already exists — a sealed split is written "
                "once and never edited (spec §6.1); pass --force only for "
                "a deliberate re-seal of a not-yet-used split")
        sealed = seal(cases, corpus=str(batch.path),
                      fraction=args.fraction,
                      whole_speakers=tuple(args.whole_speaker),
                      sealed_date=args.date, allow_under=args.allow_under)
        write_sealed(sealed, out)
    except (ManifestError, SplitError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"sealed {out}: {sealed.heldout_count} held out of "
          f"{sealed.heldout_count + sealed.train_count} "
          f"({sealed.heldout_count / (sealed.heldout_count + sealed.train_count):.1%}),"
          f" method {sealed.method}, digest {sealed.ids_digest[:12]}…")
    if len(sealed.whole_speakers) < 2:
        print("warning: fewer than 2 whole speakers held out — spec §6.2 "
              "wants ≥2 (one English L1, one other) for speaker "
              "generalization", file=sys.stderr)
    print("tuning tools: guard with scripts.corpus.split.is_held_out / "
          "assert_no_held_out before reading any case")
    return 0


def _cmd_check_split(args: argparse.Namespace) -> int:
    try:
        sealed = load_sealed(Path(args.splits))
    except SplitError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    status = 0
    for path in args.manifest:
        try:
            batch = load_batch_manifest(Path(path))
        except ManifestError as e:
            print(f"error: {e}", file=sys.stderr)
            status = 1
            continue
        try:
            assert_no_held_out([c.id for c in batch.cases], sealed,
                               source=str(path))
        except SplitError as e:
            print(f"error: {e}", file=sys.stderr)
            status = 1
            continue
        print(f"ok: {path}: {len(batch.cases)} cases, zero held-out ids "
              f"(sealed split {sealed.corpus})")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.corpus",
        description="Recording-corpus tooling for the real-speech eval "
                    "corpus (docs/eval/corpus-spec.md): batch manifests, "
                    "consent flow, deterministic held-out split, provenance "
                    "validation.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init_p = sub.add_parser(
        "init", help="scaffold a recording batch manifest from the "
                     "template")
    init_p.add_argument("name", metavar="BATCH-NAME",
                        help="batch name (a path segment, e.g. "
                             "corpus-v1-batch-01)")
    init_p.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        metavar="DIR", help=f"root directory (default: "
                        f"{DEFAULT_ROOT})")
    init_p.add_argument("--date", metavar="YYYY-MM-DD",
                        help="creation date override (default: today)")
    init_p.add_argument("--force", action="store_true",
                        help="overwrite an existing manifest")
    init_p.set_defaults(func=_cmd_init)

    val_p = sub.add_parser("validate", help="validate batch manifest(s)")
    val_p.add_argument("manifest", type=Path, nargs="+", metavar="JSON",
                       help=f"batch manifest(s) ({MANIFEST_NAME} under "
                            "eval-private/…)")
    val_p.set_defaults(func=_cmd_validate)

    exp_p = sub.add_parser("export", help="export to harness manifest.toml")
    exp_p.add_argument("manifest", type=Path, metavar="JSON",
                       help="batch manifest to export")
    exp_p.add_argument("--out", type=Path, metavar="PATH",
                       help="output path (default: manifest.toml next to "
                            "the batch manifest)")
    exp_p.add_argument("--all", action="store_true",
                       help="include planned cases (smoke tests only)")
    exp_p.set_defaults(func=_cmd_export)

    split_p = sub.add_parser(
        "split", help="compute and seal the deterministic held-out split")
    split_p.add_argument("manifest", type=Path, metavar="JSON",
                         help="batch manifest sealing the split")
    split_p.add_argument("--out", type=Path, default=None, metavar="PATH",
                         help="sealed split file (default: "
                              f"{SEALED_FILENAME} in the manifest's "
                              "directory)")
    split_p.add_argument("--whole-speaker", action="append", default=[],
                         metavar="SID", help="speaker held out entirely "
                         "(repeatable; spec §6.2 wants ≥2)")
    split_p.add_argument("--fraction", type=float, default=0.2,
                         metavar="F", help="held-out fraction target "
                         "(default 0.2)")
    split_p.add_argument("--date", metavar="YYYY-MM-DD",
                         help="sealing date override (default: today)")
    split_p.add_argument("--allow-under", action="store_true",
                         help="seal even if the overall fraction falls "
                         "short of the target (recorded in the file)")
    split_p.add_argument("--force", action="store_true",
                         help="re-seal over an existing split file")
    split_p.set_defaults(func=_cmd_split)

    chk_p = sub.add_parser(
        "check-split", help="assert train manifest(s) contain ZERO "
        "held-out ids")
    chk_p.add_argument("splits", type=Path, metavar="SPLITS.TOML",
                       help="sealed split file")
    chk_p.add_argument("manifest", type=Path, nargs="+", metavar="JSON",
                       help="train batch manifest(s) to check")
    chk_p.set_defaults(func=_cmd_check_split)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
