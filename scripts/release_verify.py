#!/usr/bin/env python3
"""Release artifact provenance: manifest + publish verification (Q4/E5).

release-prepare builds the deb BEFORE the version-bump commit exists, so
the old publish gate compared workflow head_shas and artifact names — it
could never prove the downloaded deb contains the reviewed source. The
chain is now explicit and machine-readable:

  prepare: build deb from the (sed'd, pre-commit) worktree
        -> manifest-v<version>.json { package sha256/size/control version,
           tracked-source digest of the worktree, lock sha256, version,
           build target, prepare run id + head sha }
        -> uploaded IN THE SAME ARTIFACT as the deb

  publish: verify the intended prepare run (path + success + head sha ==
           the PARENT of the release SHA), resolve the artifact by that
           run id (never by name/index), then verify manifest -> deb
           bytes (sha256, size, dpkg control) -> source (tracked-source
           digest of the release checkout, lock sha256) -> evidence.

Every step is a pure function unit-tested with fixtures
(tests/test_release_verify.py); the workflows only wire environment and
`gh api` JSON into this CLI.

Usage:
  release_verify.py manifest --repo . --deb D --version V --run-id N \
                     --head-sha S [--out M]
  release_verify.py verify-publish --repo . --manifest M --deb D \
                     --version V [--evidence-file F]
  release_verify.py select-prepare-run --runs-f Runs.json --parent-sha S
  release_verify.py select-artifact --artifacts-f Arts.json --name N \
                     --run-id N
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

SCHEMA = 1
PACKAGE_NAME = "sayit-ermano"
BUILD_TARGET = "ubuntu-24.04/amd64/python-3.12"
PREPARE_WORKFLOW = ".github/workflows/release-prepare.yml"
CI_WORKFLOW = ".github/workflows/ci.yml"


class ReleaseError(Exception):
    """Publish-blocking provenance failure; str() is user-facing."""


# ---------------------------------------------------------------------------
# digests
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tracked_source_digest(repo: Path) -> str:
    """Digest over EVERY TRACKED file's path + worktree bytes.

    Deliberately the WORKTREE content (not the git index): prepare builds
    from the post-`sed` worktree before the bump commit exists, so index
    shas would not see the bump. Untracked/ignored files (dist/, .venv)
    cannot influence it. Deterministic across machines: no smudge filters
    exist in this repo, and path order comes from `git ls-files -z`
    (sorted)."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"],
                             check=True, capture_output=True, timeout=60)
    except subprocess.CalledProcessError as e:
        raise ReleaseError(f"git ls-files failed: {e.stderr.decode()[:400]}")
    files = [f for f in out.stdout.decode().split("\0") if f]
    if not files:
        raise ReleaseError(f"no tracked files under {repo} — wrong repo?")
    h = hashlib.sha256()
    for rel in files:  # ls-files output is sorted
        h.update(rel.encode())
        h.update(b"\0")
        with open(repo / rel, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

def _dpkg_field(deb: Path, field: str) -> str:
    try:
        out = subprocess.run(["dpkg-deb", "-f", str(deb), field],
                             check=True, capture_output=True, text=True,
                             timeout=60)
    except FileNotFoundError:
        raise ReleaseError(
            "dpkg-deb not available — package metadata cannot be verified")
    except subprocess.CalledProcessError as e:
        raise ReleaseError(f"dpkg-deb -f {field} failed on {deb}: {e.stderr}")
    return out.stdout.strip()


def build_manifest(repo: Path, deb: Path, *, version: str, run_id: int,
                   head_sha: str) -> dict:
    control_version = _dpkg_field(deb, "Version")
    if control_version != f"{version}-1":
        raise ReleaseError(
            f"deb control Version {control_version!r} != expected "
            f"'{version}-1' — refusing to write a manifest for a "
            f"mismatched package")
    manifest = {
        "schema": SCHEMA,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "version": version,
        "build_target": BUILD_TARGET,
        "package": {
            "name": _dpkg_field(deb, "Package"),
            "file": deb.name,
            "sha256": sha256_file(deb),
            "size": deb.stat().st_size,
            "control_version": control_version,
        },
        "source": {
            "prepare_head": head_sha,
            "source_digest": tracked_source_digest(repo),
            "lock_sha256": sha256_file(repo / "packaging/deb/constraints.txt"),
        },
        "workflow": {
            "name": "release-prepare",
            "path": PREPARE_WORKFLOW,
            "run_id": int(run_id),
            "head_sha": head_sha,
        },
    }
    verify_manifest(manifest, version=version)
    return manifest


def load_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ReleaseError(f"cannot read manifest {path}: {e}")
    if not isinstance(manifest, dict):
        raise ReleaseError(f"manifest {path} is not a JSON object")
    return manifest


def verify_manifest(m: dict, *, version: str) -> None:
    def need(cond: bool, what: str) -> None:
        if not cond:
            raise ReleaseError(f"manifest invalid: {what}")

    need(m.get("schema") == SCHEMA, f"schema {m.get('schema')!r} != {SCHEMA}")
    need(m.get("version") == version,
         f"version {m.get('version')!r} != expected {version!r}")
    pkg = m.get("package")
    need(isinstance(pkg, dict) and pkg.get("name") == PACKAGE_NAME,
         "package.name missing/wrong")
    for key in ("file", "sha256", "size", "control_version"):
        need(isinstance(pkg, dict) and pkg.get(key),
             f"package.{key} missing")
    need(pkg["control_version"] == f"{version}-1",
         f"package.control_version {pkg.get('control_version')!r} wrong")
    src = m.get("source")
    need(isinstance(src, dict)
         and all(src.get(k) for k in ("prepare_head", "source_digest",
                                      "lock_sha256")),
         "source block incomplete")
    wf = m.get("workflow")
    need(isinstance(wf, dict) and wf.get("path") == PREPARE_WORKFLOW
         and wf.get("run_id") and wf.get("head_sha"),
         "workflow block incomplete or not release-prepare")
    need(wf["head_sha"] == src["prepare_head"],
         "workflow.head_sha disagrees with source.prepare_head")
    need(m.get("build_target") == BUILD_TARGET,
         f"build_target {m.get('build_target')!r} != {BUILD_TARGET!r}")


# ---------------------------------------------------------------------------
# publish-time verification
# ---------------------------------------------------------------------------

def verify_deb_against_manifest(deb: Path, m: dict) -> None:
    pkg = m["package"]
    if deb.name != pkg["file"]:
        raise ReleaseError(
            f"deb filename {deb.name!r} != manifest {pkg['file']!r}")
    actual = sha256_file(deb)
    if actual != pkg["sha256"]:
        raise ReleaseError(
            f"deb sha256 {actual} != manifest {pkg['sha256']} — the "
            f"package bytes are not what prepare built (altered or "
            f"wrong artifact)")
    size = deb.stat().st_size
    if size != pkg["size"]:
        raise ReleaseError(
            f"deb size {size} != manifest {pkg['size']}")
    for field, expected in (("Package", pkg["name"]),
                            ("Version", pkg["control_version"]),
                            ("Architecture", "amd64")):
        got = _dpkg_field(deb, field)
        if got != expected:
            raise ReleaseError(
                f"deb control {field} {got!r} != expected {expected!r}")


def verify_source_against_manifest(repo: Path, m: dict) -> None:
    # the named lock file first: a moved lock gets a precise message, not
    # the whole-tree digest mismatch it would also cause
    lock = sha256_file(repo / "packaging/deb/constraints.txt")
    if lock != m["source"]["lock_sha256"]:
        raise ReleaseError(
            "packaging/deb/constraints.txt digest differs from the "
            "manifest — the dependency lock moved between build and "
            "publish. Re-run release-prepare.")
    digest = tracked_source_digest(repo)
    if digest != m["source"]["source_digest"]:
        raise ReleaseError(
            "tracked-source digest of this checkout "
            f"({digest[:12]}…) != manifest ({m['source']['source_digest'][:12]}…): "
            "the release commit's source is NOT what the artifact was "
            "built from. Re-run release-prepare from the current branch "
            "head.")


def verify_green_ci_run(runs: list[dict], *, head_sha: str) -> None:
    """A completed, successful ci.yml run whose head_sha IS the release
    SHA must exist (manual dispatch is the only way CI runs here)."""
    for run in runs:
        if (run.get("path") == CI_WORKFLOW
                and run.get("head_sha") == head_sha
                and run.get("status") == "completed"
                and run.get("conclusion") == "success"):
            return
    raise ReleaseError(
        f"no successful {CI_WORKFLOW} run for commit {head_sha} — "
        "dispatch CI manually on that exact commit and wait for green")


def select_prepare_run(runs: list[dict], *, parent_sha: str) -> int:
    """The successful release-prepare run whose head_sha is the PARENT of
    the release SHA. prepare checks out the pre-bump branch head and
    pushes the bump commit afterwards, so its head_sha is the parent —
    comparing it to the release SHA directly (the old gate) could never
    match."""
    candidates = [r for r in runs
                  if r.get("path") == PREPARE_WORKFLOW
                  and r.get("head_sha") == parent_sha
                  and r.get("status") == "completed"]
    if not candidates:
        found = ", ".join(f"{r.get('name')}@{str(r.get('head_sha'))[:10]}"
                          f"({r.get('status')}/{r.get('conclusion')})"
                          for r in runs[:5])
        raise ReleaseError(
            f"no completed {PREPARE_WORKFLOW} run for the release "
            f"commit's parent {parent_sha[:12]}… — re-run release-prepare "
            f"from the current branch head. Runs seen: {found or 'none'}")
    succeeded = [r for r in candidates if r.get("conclusion") == "success"]
    if not succeeded:
        raise ReleaseError(
            f"the release-prepare run(s) for parent {parent_sha[:12]}… did "
            "not succeed — a failed prepare cannot back a release")
    # latest first (GitHub orders runs newest-first)
    run = succeeded[0]
    return int(run["id"])


def select_artifact(artifacts: list[dict], *, name: str,
                    run_id: int) -> dict:
    """Resolve the artifact by VALIDATED RUN IDENTITY — not by name and
    not artifacts[0]: a same-version artifact from a failed/superseded
    prepare must never be publishable just because it sorts first."""
    matching = [a for a in artifacts if a.get("name") == name]
    if not matching:
        raise ReleaseError(
            f"no artifact named {name!r} — run release-prepare first")
    from_run = [a for a in matching
                if (a.get("workflow_run") or {}).get("id") == run_id]
    if not from_run:
        have = [(a.get("id"),
                 (a.get("workflow_run") or {}).get("id")) for a in matching]
        raise ReleaseError(
            f"artifact {name!r} does not come from prepare run {run_id} "
            f"(candidates id/run: {have}) — it is from another (stale or "
            f"foreign) workflow run; re-run release-prepare")
    art = from_run[0]
    if art.get("expired"):
        raise ReleaseError(
            f"artifact {name!r} from run {run_id} expired — re-run "
            "release-prepare")
    return art


def verify_evidence(text: str, m: dict) -> None:
    """Required release evidence (Q4): the operator records what was
    verified against THIS artifact — the manifest's package sha256 must
    appear (proof the evidence refers to the built bytes), or an explicit
    EVIDENCE-SKIP waiver with a reason. Empty evidence fails the release;
    nothing is silently skipped."""
    text = (text or "").strip()
    if not text:
        raise ReleaseError(
            "missing release evidence: pass the `evidence` input "
            "describing what was verified against this artifact (install "
            "smoke, desktop matrix, eval report) and quoting the deb "
            "sha256, or an explicit 'EVIDENCE-SKIP: <why>' waiver")
    sha = m["package"]["sha256"]
    if sha in text or sha[:16] in text:
        return
    if "EVIDENCE-SKIP:" in text:
        return
    raise ReleaseError(
        "evidence text does not reference this artifact (expected the "
        f"deb sha256 {sha[:16]}… or an explicit 'EVIDENCE-SKIP: <why>') — "
        "evidence must be bound to the exact bytes being published")


def verify_publish(repo: Path, manifest_path: Path, deb: Path, *,
                   version: str, evidence: str = "") -> dict:
    m = load_manifest(manifest_path)
    verify_manifest(m, version=version)
    verify_deb_against_manifest(deb, m)
    verify_source_against_manifest(repo, m)
    verify_evidence(evidence, m)
    return m


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="release_verify.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("manifest", help="build+verify a prepare manifest")
    m.add_argument("--repo", type=Path, default=Path("."))
    m.add_argument("--deb", type=Path, required=True)
    m.add_argument("--version", required=True)
    m.add_argument("--run-id", type=int, required=True)
    m.add_argument("--head-sha", required=True)
    m.add_argument("--out", type=Path, required=True)

    v = sub.add_parser("verify-publish",
                       help="the full publish-time chain (no side effects)")
    v.add_argument("--repo", type=Path, default=Path("."))
    v.add_argument("--manifest", type=Path, required=True)
    v.add_argument("--deb", type=Path, required=True)
    v.add_argument("--version", required=True)
    v.add_argument("--evidence-file", type=Path)

    s = sub.add_parser("select-prepare-run")
    s.add_argument("--runs-f", type=Path, required=True,
                   help="JSON file of workflow runs (gh api …/runs)")
    s.add_argument("--parent-sha", required=True)

    a = sub.add_parser("select-artifact")
    a.add_argument("--artifacts-f", type=Path, required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--run-id", type=int, required=True)

    args = p.parse_args(argv)
    try:
        if args.cmd == "manifest":
            manifest = build_manifest(args.repo, args.deb,
                                      version=args.version,
                                      run_id=args.run_id,
                                      head_sha=args.head_sha)
            args.out.write_text(json.dumps(manifest, indent=2) + "\n",
                                encoding="utf-8")
            print(f"manifest written: {args.out} "
                  f"(deb sha256 {manifest['package']['sha256'][:16]}…, "
                  f"source {manifest['source']['source_digest'][:16]}…)")
        elif args.cmd == "verify-publish":
            evidence = (args.evidence_file.read_text(encoding="utf-8")
                        if args.evidence_file else "")
            m = verify_publish(args.repo, args.manifest, args.deb,
                               version=args.version, evidence=evidence)
            print(f"publish chain verified for v{args.version}: package "
                  f"{m['package']['sha256'][:16]}… == built bytes, source "
                  f"digest matches this checkout, lock matches")
        elif args.cmd == "select-prepare-run":
            runs = json.loads(args.runs_f.read_text(encoding="utf-8"))
            runs = runs.get("workflow_runs", runs)
            run_id = select_prepare_run(runs, parent_sha=args.parent_sha)
            print(run_id)
        elif args.cmd == "select-artifact":
            arts = json.loads(args.artifacts_f.read_text(encoding="utf-8"))
            arts = arts.get("artifacts", arts)
            art = select_artifact(arts, name=args.name, run_id=args.run_id)
            print(json.dumps({"id": art["id"],
                              "expired": art["expired"],
                              "run_id": art["workflow_run"]["id"]}))
    except ReleaseError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
