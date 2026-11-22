#!/usr/bin/env python3
"""User-space upgrade/rollback rehearsal (quality plan Q9) — no root, no apt.

Paying users install v0.9.0 and later update to v0.9.1; this rehearsal
PROVES that path on every commit, entirely in user space:

1. exports the committed tree (``git archive HEAD``) twice — as-is
   (v0.9.0) and with a bumped ``fluidvoice/__init__.py`` (v0.9.1) — and
   packages each into a local .deb carrying a REAL venv
   (``/opt/sayit-ermano/venv`` layout, the same payload contract
   ``packaging/build-deb.sh`` ships; dependencies from PyPI because the
   release lock's hashes are py3.12-wheel-bound — the lock itself is
   gated by ``tests/test_deb_lock.py``);
2. installs the OLD deb through the REAL one-shot route
   (``scripts/install-one-shot.sh ./old.deb`` — the exact script the
   app prints for user installs; every systemctl/pkill call is
   ``|| true``-guarded there, so a CI runner without a session bus is
   fine; the missing-system-packages prompt is answered ``n``);
3. seeds a realistic user state (config.toml + history.jsonl via the
   app's own ``history.append``), snapshots both files;
4. runs ``install-one-shot.sh --update ./new.deb`` — the documented
   in-place user-venv upgrade — and asserts: dispatch picked the
   user-venv route, the launcher reports the NEW version, config keys
   are preserved (byte-identical file + still parseable), history rows
   are byte-identical;
5. rolls BACK by re-running ``--update`` with the old deb and asserts
   the same retention + the old version back.

Evidence lands in ``docs/quality/upgrade/<ISO-date>-v<old>-to-v<new>.json``
plus the generated README table. Exit code mirrors the verdict.

Usage: .venv/bin/python scripts/upgrade_rehearsal.py
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
EVIDENCE_DIR = ROOT / "docs" / "quality" / "upgrade"
ONE_SHOT = ROOT / "scripts" / "install-one-shot.sh"

#: the "next release" the rehearsal upgrades to (bumped locally only)
NEW_VERSION = "0.9.1"

USER_CONFIG = """\
[general]
language = "en"

[hotkey]
key = "F9"
cancel_key = "F12"

[model]
backend = "auto"
name = "base"

[recording]
preview_enabled = true

[sounds]
enabled = true
volume = 0.6

[history]
save = true
audio_retention_days = 30

[ai]
provider = "openai"
api_key_env = "SAYITERMANO_API_KEY"
"""

HISTORY_SEED = """\
import json, sys, time
from fluidvoice import history
for i, text in enumerate(("hello world", "upgrade rehearsal row two",
                          "third row with punctuation!")):
    history.append({"ts": time.time() - 3600 + i, "text": text,
                    "raw": text, "duration_s": 1.5 + i * 0.25,
                    "backend": "rehearsal", "words": len(text.split())})
print(json.dumps({"rows": len(history.read_all())}))
"""


# ── pure assertion helpers (unit-tested with fakes) ────────────────────────

def bump_version_src(source: str, new_version: str) -> str:
    """Rewrite the __version__ = "..." line in __init__.py source."""
    pattern = re.compile(r'(__version__\s*=\s*)"[^"]*"')
    out, n = pattern.subn(lambda m: f'{m.group(1)}"{new_version}"', source)
    if n != 1:
        raise ValueError(f"expected exactly one __version__ line, found {n}")
    return out


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def diff_snapshot(label: str, before: bytes, after: bytes) -> dict:
    """Byte-identity verdict for one user-state file across a step."""
    return {"label": label, "identical": before == after,
            "before_sha256": sha256(before), "after_sha256": sha256(after),
            "before_bytes": len(before), "after_bytes": len(after)}


def _flatten(prefix: str, value) -> dict:
    out = {}
    if isinstance(value, dict):
        for k, v in value.items():
            out.update(_flatten(f"{prefix}.{k}" if prefix else str(k), v))
    else:
        out[prefix] = value
    return out


def config_preserved(before_text: str, after_text: str) -> dict:
    """Every config leaf (dotted key path, value) present before an
    upgrade must survive it unchanged. Extra keys are tolerated (an
    upgrade may introduce defaults); a LOST or CHANGED user setting is
    a failure."""
    import tomllib
    before = _flatten("", tomllib.loads(before_text))
    after = _flatten("", tomllib.loads(after_text))
    missing = sorted(k for k, v in before.items()
                     if k not in after)
    changed = sorted(k for k, v in before.items()
                     if k in after and after[k] != v)
    return {"keys_before": len(before), "keys_after": len(after),
            "missing": missing, "changed": changed,
            "preserved": not missing and not changed}


def history_rows(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def parse_version_output(text: str) -> str:
    """`sayit-ermano --version` prints just the version."""
    m = re.search(r"\d+\.\d+\.\d+", text)
    if not m:
        raise ValueError(f"no version in output: {text!r}")
    return m.group(0)


# ── package building (real subprocess; injectable runner for tests) ────────

def default_run(cmd: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def export_tree(dest: Path) -> None:
    """The committed tree (git archive HEAD) — the rehearsal upgrades
    what is committed, never uncommitted WIP."""
    dest.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["git", "archive", "HEAD"], cwd=str(ROOT),
                          capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git archive failed: {proc.stderr!r}")
    with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as tar:
        tar.extractall(dest, filter="data")


def build_venv(venv_dir: Path, tree: Path, run=default_run,
               with_deps: bool = True) -> dict:
    """A real venv with the app installed from `tree` (the deb payload).

    Created with --without-pip (no ensurepip on the host) and filled via
    the DRIVER interpreter's pip (--python), matching the deb's
    app-from-committed-source semantics: --no-deps when with_deps is
    False (unit-test mode uses neither pip nor network).
    """
    py = run([sys.executable, "-m", "venv", "--without-pip", str(venv_dir)])
    if py.returncode != 0:
        raise RuntimeError(f"venv creation failed: {py.stderr}")
    venv_python = venv_dir / "bin" / "python"
    cmd = [sys.executable, "-m", "pip", "--python", str(venv_python),
           "install"]
    if not with_deps:
        cmd.append("--no-deps")
    cmd.append(str(tree))
    inst = run(cmd)
    if inst.returncode != 0:
        raise RuntimeError(f"pip install into staged venv failed: "
                           f"{getattr(inst, 'stderr', '')}")
    return {"python": str(venv_python)}


def build_deb(pkg_root: Path, tree: Path, version: str,
              run=default_run, venv_builder=build_venv,
              with_deps: bool = True) -> Path:
    """Stage the release payload contract and dpkg-deb --build it.

    Mirrors packaging/build-deb.sh's layout for everything the one-shot
    USER route consumes: /opt/sayit-ermano/venv (the real payload),
    the launcher, both .desktop files. Icons are optional for this
    route (the installer tolerates their absence).
    """
    pkg = pkg_root / "pkg"
    stage = pkg / "opt" / "sayit-ermano"
    venv_dir = stage / "venv"
    venv_builder(venv_dir, tree, run=run, with_deps=with_deps)
    (pkg / "usr" / "bin").mkdir(parents=True, exist_ok=True)
    (pkg / "usr" / "bin" / "sayit-ermano").write_text(
        "#!/bin/sh\n"
        "exec /opt/sayit-ermano/venv/bin/python -m fluidvoice \"$@\"\n",
        encoding="utf-8")
    (pkg / "usr" / "bin" / "sayit-ermano").chmod(0o755)
    for src, dst in ((ROOT / "packaging" / "sayit-ermano.desktop",
                      "usr/share/applications/sayit-ermano.desktop"),
                     (ROOT / "packaging" / "autostart.desktop",
                      "etc/xdg/autostart/sayit-ermano.desktop")):
        target = pkg / dst
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, target)
    debian = pkg / "DEBIAN"
    debian.mkdir(parents=True, exist_ok=True)
    (debian / "control").write_text(
        "Package: sayit-ermano\n"
        f"Version: {version}-1\n"
        "Section: sound\nPriority: optional\nArchitecture: amd64\n"
        "Maintainer: SayItErmano contributors\n"
        "Description: SayItErmanO user-space upgrade rehearsal build\n",
        encoding="utf-8")
    out = pkg_root / f"sayit-ermano_{version}-1_amd64.deb"
    proc = run(["dpkg-deb", "--build", "--root-owner-group", str(pkg),
                str(out)])
    if proc.returncode != 0:
        raise RuntimeError(f"dpkg-deb build failed: {getattr(proc, 'stderr', '')}")
    return out


def run_one_shot(home: Path, deb: Path, update: bool,
                 run=default_run) -> tuple[int, str]:
    """Drive the REAL one-shot installer against a sandbox HOME."""
    cmd = ["bash", str(ONE_SHOT)]
    if update:
        cmd.append("--update")
    cmd.append(str(deb))
    proc = run(cmd, env={**os.environ, "HOME": str(home)}, input="n\n")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ── the rehearsal (real run; helpers above are the unit-tested seams) ──────

class Step:
    def __init__(self, name: str):
        self.name = name
        self.ok = True
        self.details: dict = {}

    def check(self, condition: bool, what: str) -> bool:
        if not condition:
            self.ok = False
        self.details.setdefault("checks", []).append(
            {"what": what, "ok": bool(condition)})
        return bool(condition)


def launcher(home: Path) -> Path:
    return home / ".local" / "bin" / "sayit-ermano"


def installed_version(home: Path, run=default_run) -> str:
    proc = run([str(launcher(home)), "--version"])
    if proc.returncode != 0:
        raise RuntimeError(f"launcher failed: {proc.stderr}")
    return parse_version_output(proc.stdout)


def user_state(home: Path) -> dict:
    cfg = home / ".config" / "sayit-ermano" / "config.toml"
    hist = home / ".local" / "share" / "sayit-ermano" / "history.jsonl"
    return {"config": cfg, "history": hist}


def seed_user_state(home: Path, run=default_run) -> dict:
    state = user_state(home)
    state["config"].parent.mkdir(parents=True, exist_ok=True)
    state["config"].write_text(USER_CONFIG, encoding="utf-8")
    env = {**os.environ, "HOME": str(home),
           "XDG_CONFIG_HOME": str(home / ".config"),
           "XDG_DATA_HOME": str(home / ".local" / "share"),
           "XDG_CACHE_HOME": str(home / ".cache")}
    proc = run([sys.executable, "-c", HISTORY_SEED], cwd=str(ROOT), env=env)
    if proc.returncode != 0 or not state["history"].exists():
        raise RuntimeError(f"history seeding failed: {proc.stderr}")
    return {"config_text": state["config"].read_text(encoding="utf-8"),
            "history_text": state["history"].read_text(encoding="utf-8")}


def run_rehearsal(new_version: str = NEW_VERSION) -> dict:
    from fluidvoice import __version__ as old_version
    started = time.time()
    steps: list[Step] = []
    workdir = Path(tempfile.mkdtemp(prefix="sayit-upgrade-"))
    home = workdir / "home"
    home.mkdir()
    deb_paths: dict[str, Path] = {}
    snapshots: dict[str, dict] = {}
    seeded: dict[str, str] = {}
    # 1. both trees: committed HEAD as-is + a bumped copy
    old_tree = workdir / "tree-old"
    new_tree = workdir / "tree-new"
    export_tree(old_tree)
    shutil.copytree(old_tree, new_tree)
    init_py = new_tree / "fluidvoice" / "__init__.py"
    init_py.write_text(
        bump_version_src(init_py.read_text(encoding="utf-8"),
                         new_version), encoding="utf-8")

    # 2. packages with a real venv payload (the deb contract)
    deb_paths["old"] = build_deb(workdir / "pkg-old", old_tree,
                                 old_version)
    deb_paths["new"] = build_deb(workdir / "pkg-new", new_tree,
                                 new_version)

    # 3. fresh install through the real one-shot user route
    step = Step("install-old")
    rc, out = run_one_shot(home, deb_paths["old"], update=False)
    step.check(rc == 0, f"one-shot install exit 0 (got {rc})")
    step.check(launcher(home).exists(), "launcher ~/.local/bin exists")
    step.details["version"] = installed_version(home)
    step.check(step.details["version"] == old_version,
               f"launcher reports {old_version} (got "
               f"{step.details['version']})")
    unit = home / ".config" / "systemd" / "user" / "sayit-ermano.service"
    step.check(unit.exists(), "systemd user unit written")
    steps.append(step)

    # 4. seed user state, snapshot it
    step = Step("seed")
    seeded = seed_user_state(home)
    rows = history_rows(seeded["history_text"])
    step.check(len(rows) == 3, f"3 history rows seeded (got {len(rows)})")
    step.check(len(rows[0].get("text", "")) > 0,
               "history rows carry text")
    steps.append(step)

    # 5. the upgrade: --update against the locally-built new deb
    step = Step("upgrade")
    rc, out = run_one_shot(home, deb_paths["new"], update=True)
    step.check(rc == 0, f"one-shot --update exit 0 (got {rc})")
    step.check("User installation found" in out,
               "update dispatched to the user-venv route")
    step.details["version"] = installed_version(home)
    step.check(step.details["version"] == new_version,
               f"launcher reports {new_version} after upgrade (got "
               f"{step.details['version']})")
    state = user_state(home)
    snapshots["upgrade_config"] = diff_snapshot(
        "config.toml", seeded["config_text"].encode(),
        state["config"].read_bytes())
    snapshots["upgrade_history"] = diff_snapshot(
        "history.jsonl", seeded["history_text"].encode(),
        state["history"].read_bytes())
    step.check(snapshots["upgrade_config"]["identical"],
               "config.toml byte-identical across upgrade")
    step.check(snapshots["upgrade_history"]["identical"],
               "history.jsonl byte-identical across upgrade")
    step.details["config_preserved"] = config_preserved(
        seeded["config_text"],
        state["config"].read_text(encoding="utf-8"))
    step.check(step.details["config_preserved"]["preserved"],
               "every config key survived the upgrade")
    step.details["history_rows_after"] = len(history_rows(
        state["history"].read_text(encoding="utf-8")))
    step.check(step.details["history_rows_after"] == len(rows),
               "history row count unchanged")
    steps.append(step)

    # 6. rollback: --update with the OLD deb again
    step = Step("rollback")
    rc, out = run_one_shot(home, deb_paths["old"], update=True)
    step.check(rc == 0, f"rollback --update exit 0 (got {rc})")
    step.check("User installation found" in out,
               "rollback dispatched to the user-venv route")
    step.details["version"] = installed_version(home)
    step.check(step.details["version"] == old_version,
               f"launcher reports {old_version} after rollback (got "
               f"{step.details['version']})")
    state = user_state(home)
    snapshots["rollback_config"] = diff_snapshot(
        "config.toml", seeded["config_text"].encode(),
        state["config"].read_bytes())
    snapshots["rollback_history"] = diff_snapshot(
        "history.jsonl", seeded["history_text"].encode(),
        state["history"].read_bytes())
    step.check(snapshots["rollback_config"]["identical"],
               "config.toml byte-identical across rollback")
    step.check(snapshots["rollback_history"]["identical"],
               "history.jsonl byte-identical across rollback")
    steps.append(step)

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True,
                            cwd=str(ROOT)).stdout.strip()
    passed = all(s.ok for s in steps)
    return {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                         time.gmtime()),
            "from_version": old_version, "to_version": new_version,
            "commit": commit, "host": os.uname().nodename,
            "python": sys.version.split()[0],
            "route": "scripts/install-one-shot.sh (user-space, no root)",
            "steps": [{"name": s.name, "ok": s.ok, **s.details}
                      for s in steps],
            "snapshots": snapshots,
            "seeded_history_rows": len(history_rows(
                seeded.get("history_text", ""))),
            "workdir": str(workdir),
            "elapsed_s": round(time.time() - started, 1),
            "passed": passed}


def write_evidence(report: dict, evidence_dir: Path = EVIDENCE_DIR) -> Path:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    name = (f"{date.today().isoformat()}-v{report['from_version']}"
            f"-to-v{report['to_version']}.json")
    path = evidence_dir / name
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    runs = []
    for f in sorted(evidence_dir.glob("*.json"), reverse=True):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        checks = [c for s in r.get("steps", []) for c in s.get("checks", [])]
        runs.append({"date": r.get("started_utc", "?")[:10],
                     "from": r.get("from_version", "?"),
                     "to": r.get("to_version", "?"),
                     "commit": r.get("commit", "?"),
                     "checks": len(checks),
                     "passed": r.get("passed", False), "file": f.name})
    readme = evidence_dir / "README.md"
    rows = ["| Date | Upgrade | Commit | Checks | Verdict |",
            "|---|---|---|---|---|"]
    for r in runs:
        verdict = "PASS" if r["passed"] else "FAIL"
        rows.append(f"| {r['date']} | v{r['from']} -> v{r['to']} "
                    f"| `{r['commit']}` | {r['checks']} "
                    f"| {verdict} ([{r['file']}]({r['file']})) |")
    readme.write_text(
        "# Upgrade/rollback rehearsal evidence (Q9)\n\n"
        "Dated user-space upgrade rehearsals recorded by "
        "`scripts/upgrade_rehearsal.py`: real one-shot installer route, "
        "locally-built venv-payload debs, config/history retention + "
        "rollback asserted at every step. No root, no apt, no display. "
        "Table generated; do not hand-edit.\n\n" + "\n".join(rows) + "\n",
        encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="upgrade_rehearsal", description=__doc__.splitlines()[0])
    ap.add_argument("--to-version", default=NEW_VERSION,
                    help=f"the locally-bumped 'next' version "
                         f"(default {NEW_VERSION})")
    ap.add_argument("--keep-workdir", action="store_true",
                    help="keep the sandbox workdir referenced in the report")
    args = ap.parse_args(argv)

    report = run_rehearsal(new_version=args.to_version)
    if not args.keep_workdir and Path(report["workdir"]).exists():
        shutil.rmtree(report["workdir"], ignore_errors=True)
        report["workdir"] = "(removed)"
    path = write_evidence(report)
    for step in report["steps"]:
        mark = "ok" if step["ok"] else "FAIL"
        failed = [c["what"] for c in step.get("checks", []) if not c["ok"]]
        print(f"  {step['name']}: {mark}" + (f" — {failed}" if failed else ""))
    print(f"rehearsal verdict: {'PASS' if report['passed'] else 'FAIL'} "
          f"({report['elapsed_s']:.0f}s) -> {path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
