#!/usr/bin/env python3
"""Q9 user-space upgrade/rollback rehearsal — no root, no apt, no network.

Quality-plan Q9 asks for proof that a paying user's upgrade keeps
config and history intact and that rollback works. apt/deb routes need
root + the signed repo; this rehearsal drives the REAL one-shot
installer (scripts/install-one-shot.sh) inside a sandboxed fake machine
(throwaway $HOME + stub systemctl/dpkg/curl — the same technique as
tests/test_installer_update.py) through the full user-space lifecycle:

  1. build v0 (a real venv carrying THIS tree's fluidvoice/, packaged
     as a deb with the one-shot layout) and install it via
     `install-one-shot.sh <deb>`;
  2. seed the user state exactly where the app keeps it
     (~/.config/sayit-ermano/config.toml, history via the app's own
     history API);
  3. build v1 = the same tree with a bumped fluidvoice/__init__.py and
     run the `--update` route against it (locally-built "new release");
  4. assert: binary reports v1, config keys preserved (and bytes
     untouched), history rows byte-identical, and the upgraded venv
     actually RUNS the daemon (spawn → socket status → clean exit);
  5. rollback: `--update` back to v0, assert version reverts and the
     user state is still byte-identical.

Evidence: docs/quality/upgrade/<ISO-date>.json + .md. Exit 1 on any
red step. `--work DIR` keeps the sandbox for debugging.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from soak_run import (  # noqa: E402
    REPO,
    sock_request,
    spawn_daemon,
    stop_daemon,
)

OUT_DEFAULT = REPO / "docs" / "quality" / "upgrade"
INSTALLER = REPO / "scripts" / "install-one-shot.sh"

SEED_CONFIG = """\
# user-authored config — an upgrade must never touch this file
[general]
language = "en"

[hotkey]
key = "F9"
cancel_key = "F12"

[model]
name = "tiny"

[history]
save = true
"""

SEED_HISTORY_ROWS = [
    {"ts": 1700000000 + i * 60, "text": f"dictation row {i}", "ai": False}
    for i in range(5)
]


class RehearsalError(RuntimeError):
    """A red step: carries the step name for the evidence report."""


# ---------------------------------------------------------------------------
# pure assertion helpers (unit-tested with fakes)
# ---------------------------------------------------------------------------

def bump_version(version: str) -> str:
    """Last numeric component +1 ('0.8.3.1' -> '0.8.3.2', '0.9' -> '0.10')."""
    m = re.match(r"^(\d+(?:\.\d+)*)(.*)$", version.strip())
    if not m:
        raise ValueError(f"unparsable version {version!r}")
    parts = [int(p) for p in m.group(1).split(".")]
    parts[-1] += 1
    return ".".join(str(p) for p in parts) + m.group(2)


def patch_version_in_source(source: str, new_version: str) -> str:
    """Rewrite the __version__ assignment in fluidvoice/__init__.py."""
    patched, n = re.subn(r'__version__\s*=\s*"[^"]*"',
                         f'__version__ = "{new_version}"', source, count=1)
    if n != 1:
        raise ValueError("no __version__ assignment found")
    return patched


def _flatten(d: dict, prefix: str = "") -> dict:
    """('a', {'b': 1}) -> {'a.b': 1} — TOML table keys, flat for diffing."""
    out: dict = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def config_preserved(before_text: str, after_text: str) -> dict:
    """Gate: every config key/value the user had survives the upgrade.

    Byte-identical is reported too (the one-shot route never rewrites
    the file, so bytes SHOULD be identical), but the gate is key/value
    preservation — a future installer that normalizes formatting must
    still pass.
    """
    before = _flatten(tomllib.loads(before_text))
    after = _flatten(tomllib.loads(after_text))
    changed = {k for k in before if after.get(k) != before[k]}
    missing = set(before) - set(after)
    added = set(after) - set(before)
    return {
        "ok": not changed and not missing,
        "byte_identical": before_text == after_text,
        "keys_before": sorted(before),
        "keys_after": sorted(after),
        "changed": sorted(changed),
        "missing": sorted(missing),
        "added": sorted(added),
    }


def history_unchanged(before_bytes: bytes, after_bytes: bytes) -> dict:
    """Gate: history rows byte-identical across the upgrade."""
    return {
        "ok": before_bytes == after_bytes,
        "rows_before": before_bytes.count(b"\n"),
        "rows_after": after_bytes.count(b"\n"),
        "sha256_before": hashlib.sha256(before_bytes).hexdigest(),
        "sha256_after": hashlib.sha256(after_bytes).hexdigest(),
    }


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def evidence_paths(out_dir: Path, when: datetime) -> tuple[Path, Path]:
    stem = when.date().isoformat()
    json_path = out_dir / f"{stem}.json"
    n = 2
    while json_path.exists():
        json_path = out_dir / f"{stem}-{n}.json"
        n += 1
    return json_path, json_path.with_suffix(".md")


# ---------------------------------------------------------------------------
# sandbox (fake machine) — self-contained twin of the test Sandbox
# ---------------------------------------------------------------------------

class Sandbox:
    """A fake machine for the one-shot installer: $HOME, PATH stubs, log."""

    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.home.mkdir(parents=True)
        self.bin = root / "stubs"
        self.bin.mkdir()
        self.log = root / "calls.log"
        self.log.write_text("")
        self.marker = root / "sayit-ermano.installed"
        self._stubs()

    def _stub(self, name: str, body: str) -> None:
        p = self.bin / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(0o755)

    def _stubs(self) -> None:
        common = (f'CALLS="{self.log}"\n'
                  'log() { printf "%s\\n" "$*" >> "$CALLS"; }\n')
        self._stub("systemctl", common + "log systemctl $*\nexit 0\n")
        self._stub("pkill", common + "log pkill $*\nexit 0\n")
        self._stub("sudo", common + 'log "sudo $*"\nexec "$@"\n')
        self._stub("apt", common + 'log "apt $*"\nexit 0\n')
        self._stub("pipx", common + 'log "pipx $*"\nexit 0\n')
        self._stub("pw-record", common + "exit 0\n")
        # dpkg: -s sayit-ermano follows the marker file (system-deb
        # detection); every other package counts as installed so the
        # dependency prompt stays out of the way; arch is amd64
        self._stub("dpkg", f'''{common}MARKER="{self.marker}"
if [ "$1" = "-s" ] && [ "$2" = "sayit-ermano" ]; then
    [ -e "$MARKER" ] && exit 0
    exit 1
fi
if [ "$1" = "--print-architecture" ]; then echo amd64; exit 0; fi
exit 0
''')
        # curl stub: the rehearsal always passes a local .deb, so curl
        # must merely exist; if it ever fires, record it loudly
        self._stub("curl", common
                   + 'log "curl $*"\nexit 22\n')
        # NOTE: dpkg-deb stays REAL — both build and install use it

    # -- run -----------------------------------------------------------------

    def run_installer(self, *args: str,
                      timeout: int = 300) -> subprocess.CompletedProcess:
        env = {
            **{k: v for k, v in os.environ.items()
               if not k.startswith(("LC_", "LANG"))},
            "HOME": str(self.home),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "SAYITERMANO_APT_LIST": str(
                self.root / "apt-list.d" / "sayit-ermano.list"),
        }
        return subprocess.run(
            ["bash", str(INSTALLER), *args], env=env,
            capture_output=True, text=True, timeout=timeout)

    def calls(self) -> str:
        return self.log.read_text()

    # -- machine state ---------------------------------------------------------

    @property
    def app_dir(self) -> Path:
        return self.home / ".local" / "share" / "sayit-ermano"

    @property
    def venv_python(self) -> Path:
        return self.app_dir / "venv" / "bin" / "python"

    @property
    def launcher(self) -> Path:
        return self.home / ".local" / "bin" / "sayit-ermano"

    @property
    def config_file(self) -> Path:
        return self.home / ".config" / "sayit-ermano" / "config.toml"

    @property
    def history_file(self) -> Path:
        return self.home / ".local" / "share" / "sayit-ermano" / "history.jsonl"

    def run_binary(self, *args: str,
                   timeout: int = 60) -> subprocess.CompletedProcess:
        """Run the installed launcher exactly like a user would.

        cwd = the sandbox $HOME: `python -m fluidvoice` puts the cwd
        first on sys.path, so running from a checkout would shadow the
        venv's copy — the one thing this rehearsal must not do.
        """
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("LC_", "LANG", "PYTHON"))}
        env.update(HOME=str(self.home),
                   SAYITERMANO_SKIP_UPDATE_CHECK="1")
        return subprocess.run([str(self.launcher), *args], env=env,
                              capture_output=True, text=True,
                              timeout=timeout, cwd=str(self.home))


# ---------------------------------------------------------------------------
# payload build (real venv + package copy -> one-shot-layout deb)
# ---------------------------------------------------------------------------

def build_payload(work: Path, version: str, repo: Path,
                  base_python: str) -> tuple[Path, Path]:
    """A venv carrying `version` of the app + its deb (one-shot layout).

    The venv is real (python -m venv --without-pip — no network, no
    pip); the app is installed by copying the package tree into
    site-packages, which is exactly how the launcher uses it
    (`venv/bin/python -m fluidvoice`). Returns (venv_python, deb).
    """
    pkg_src = repo / "fluidvoice"
    if not (pkg_src / "__init__.py").exists():
        raise RehearsalError(f"no fluidvoice package under {repo}")
    tree = work / f"tree-{version}" / "fluidvoice"
    tree.parent.mkdir(parents=True)
    shutil.copytree(pkg_src, tree)
    init = tree / "__init__.py"
    init.write_text(patch_version_in_source(init.read_text(), version))

    venv_dir = work / f"venv-{version}"
    # --system-site-packages: the payload inherits the base
    # interpreter's dependencies (python-xlib, …) exactly like the real
    # deb's bundled venv carries them; the copied package lands in the
    # venv's OWN site-packages, which shadows any system copy.
    r = subprocess.run([base_python, "-m", "venv", "--without-pip",
                        "--system-site-packages", str(venv_dir)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RehearsalError(f"venv build failed: {r.stderr[-500:]}")
    venv_python = venv_dir / "bin" / "python"
    purelib = subprocess.run(
        [str(venv_python), "-c",
         "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True, text=True, check=True).stdout.strip()
    shutil.copytree(tree, Path(purelib) / "fluidvoice")
    out = subprocess.run([str(venv_python), "-m", "fluidvoice", "--version"],
                         capture_output=True, text=True, cwd=str(work))
    if out.returncode != 0 or out.stdout.strip() != version:
        raise RehearsalError(
            f"payload venv reports {out.stdout.strip()!r}, "
            f"expected {version!r} (stderr: {out.stderr[-300:]})")

    stage = work / f"deb-{version}" / "stage"
    sv = stage / "opt" / "sayit-ermano" / "venv"
    sv.parent.mkdir(parents=True)
    shutil.copytree(venv_dir, sv, symlinks=True)
    for rel in ("usr/share/applications", "etc/xdg/autostart",
                "usr/share/icons/hicolor", "DEBIAN"):
        (stage / rel).mkdir(parents=True)
    for rel in ("usr/share/applications", "etc/xdg/autostart"):
        (stage / rel / "sayit-ermano.desktop").write_text(
            "[Desktop Entry]\nExec=/usr/bin/sayit-ermano\n")
    (stage / "DEBIAN" / "control").write_text(
        f"Package: sayit-ermano\nVersion: {version}-1\n"
        "Architecture: amd64\nMaintainer: q9-rehearsal\n"
        "Description: Q9 upgrade rehearsal payload\n")
    deb = work / f"sayit-ermano_{version}-1_amd64.deb"
    r = subprocess.run(["dpkg-deb", "--build", "--root-owner-group",
                        str(stage), str(deb)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RehearsalError(f"dpkg-deb build failed: {r.stderr[-500:]}")
    return venv_python, deb


def seed_user_state(box: Sandbox) -> None:
    """The user's accumulated state, exactly where the app keeps it.

    Config is hand-written (users edit TOML); history rows go through
    the app's own API so the file shape is the real one.
    """
    box.config_file.parent.mkdir(parents=True, exist_ok=True)
    box.config_file.write_text(SEED_CONFIG)
    code = ("from fluidvoice import history\n"
            + "".join(f"history.append({row!r})\n"
                      for row in SEED_HISTORY_ROWS))
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("LC_", "LANG", "PYTHON"))}
    env.update(HOME=str(box.home),
               XDG_DATA_HOME=str(box.home / ".local" / "share"),
               SAYITERMANO_SKIP_UPDATE_CHECK="1")
    r = subprocess.run([str(box.venv_python), "-c", code],
                       capture_output=True, text=True, env=env,
                       timeout=120, cwd=str(box.home))
    if r.returncode != 0 or not box.history_file.exists():
        raise RehearsalError(
            f"history seeding failed: {r.stderr[-500:]}")


def binary_version(box: Sandbox) -> str:
    out = box.run_binary("--version")
    if out.returncode != 0:
        raise RehearsalError(f"installed binary --version failed: "
                             f"{out.stderr[-300:]}")
    return out.stdout.strip()


# ---------------------------------------------------------------------------
# the rehearsal itself
# ---------------------------------------------------------------------------

def run_rehearsal(args: argparse.Namespace) -> int:
    from soak_run import repo_sha
    if shutil.which("bash") is None or shutil.which("dpkg-deb") is None:
        raise RehearsalError("bash + dpkg-deb are required for the "
                             "user-space rehearsal")
    init_src = (args.repo / "fluidvoice" / "__init__.py").read_text()
    m = re.search(r'__version__\s*=\s*"([^"]*)"', init_src)
    if not m:
        raise RehearsalError("cannot read the repo's __version__")
    old_version, new_version = m.group(1), bump_version(m.group(1))
    work = args.work
    work.mkdir(parents=True, exist_ok=True)
    steps: list[dict] = []
    checks: dict = {"after_upgrade": {}, "after_rollback": {}}
    box: Sandbox | None = None
    ok = True

    def step(name: str, fn) -> object:
        t = time.monotonic()
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001 — recorded, then re-raised
            steps.append({"step": name, "status": "FAIL",
                          "detail": f"{type(e).__name__}: {e}"[:500],
                          "ms": int((time.monotonic() - t) * 1000)})
            print(f"  FAIL {name}: {e}", flush=True)
            raise
        steps.append({"step": name, "status": "PASS",
                      "detail": str(result)[:300],
                      "ms": int((time.monotonic() - t) * 1000)})
        print(f"  ok  {name} ({steps[-1]['ms']} ms)", flush=True)
        return result

    print(f"rehearsal: {old_version} -> {new_version} (work {work})",
          flush=True)
    payloads: dict[str, Path] = {}

    def _build(version: str) -> str:
        _, deb = build_payload(work, version, args.repo, args.python)
        payloads[version] = deb
        return f"payload venv reports {version}; deb {deb.name}"

    try:
        step("build v0 payload (this tree)", lambda: _build(old_version))
        step("build v1 payload (bumped __init__.py)",
             lambda: _build(new_version))
        old_deb, new_deb = payloads[old_version], payloads[new_version]
        box = Sandbox(work / "machine")

        def _install_old():
            r = box.run_installer(str(old_deb))
            assert r.returncode == 0, (f"installer rc={r.returncode}: "
                                       f"{r.stdout[-400:]}{r.stderr[-400:]}")
            assert box.launcher.exists(), "launcher missing"
            got = binary_version(box)
            assert got == old_version, f"fresh install reports {got!r}"
            return f"installed {old_version} at {box.app_dir}"

        def _seed():
            seed_user_state(box)
            return (f"config.toml ({len(box.config_file.read_text())} B) "
                    f"+ {len(SEED_HISTORY_ROWS)} history rows via the "
                    f"app API")

        def _upgrade():
            r = box.run_installer("--update", str(new_deb))
            assert r.returncode == 0, (f"--update rc={r.returncode}: "
                                       f"{r.stdout[-400:]}{r.stderr[-400:]}")
            got = binary_version(box)
            assert got == new_version, (f"upgraded binary reports {got!r}, "
                                        f"expected {new_version!r}")
            return f"binary now reports {new_version}"

        def _daemon_smoke():
            # cwd = work (no top-level fluidvoice there): the UPGRADED
            # venv's site-packages copy is the code under test, not the
            # checkout — that is the point of the smoke. PYTHONPATH
            # exposes the base interpreter's deps (python-xlib & co) to
            # the payload venv, standing in for the deps the real deb
            # bundles (the package itself still resolves to the venv's
            # own site-packages first).
            base_purelib = subprocess.run(
                [args.python, "-c",
                 "import sysconfig; "
                 "print(sysconfig.get_paths()['purelib'])"],
                capture_output=True, text=True,
                check=True).stdout.strip()
            env = {**os.environ, "PYTHONPATH": base_purelib}
            d = spawn_daemon(str(box.venv_python), work / "daemon-smoke",
                             repo=work, env=env)
            try:
                resp = sock_request(d.socket, "status")
                assert (resp.get("ok") and resp.get("version") == new_version
                        ), f"upgraded daemon status: {resp}"
                return (f"upgraded daemon answered status with "
                        f"version={resp.get('version')}")
            finally:
                stop_daemon(d)

        def _rollback():
            r = box.run_installer("--update", str(old_deb))
            assert r.returncode == 0, f"rollback rc={r.returncode}"
            got = binary_version(box)
            assert got == old_version, (f"rolled-back binary reports {got!r},"
                                        f" expected {old_version!r}")
            return f"binary back to {old_version}"

        def _state_gate(phase: str) -> str:
            cfg = config_preserved(cfg_before, box.config_file.read_text())
            hist = history_unchanged(hist_before,
                                     box.history_file.read_bytes())
            checks[phase] = {"config": cfg, "history": hist}
            assert cfg["ok"], (f"config gate red: changed={cfg['changed']} "
                               f"missing={cfg['missing']}")
            assert hist["ok"], (f"history gate red: rows "
                                f"{hist['rows_before']} -> "
                                f"{hist['rows_after']}")
            return (f"config keys preserved (byte-identical: "
                    f"{cfg['byte_identical']}), history byte-identical "
                    f"({hist['rows_before']} rows)")

        step("install v0 (one-shot, user-space)", _install_old)
        step("seed config + history", _seed)
        cfg_before = box.config_file.read_text()
        hist_before = box.history_file.read_bytes()
        step("run --update against the locally-built v1", _upgrade)
        step("user state survives the upgrade",
             lambda: _state_gate("after_upgrade"))
        step("upgraded venv runs the daemon", _daemon_smoke)
        step("rollback (reinstall v0 via --update)", _rollback)
        step("user state survives the rollback",
             lambda: _state_gate("after_rollback"))
    except Exception:  # noqa: BLE001 — every failure is evidence, not a crash
        ok = False
    report = {
        "schema": "sayit-upgrade-rehearsal/1",
        "started_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "repo_sha": repo_sha(args.repo),
        "versions": {"old": old_version, "new": new_version},
        "config": {"python": args.python, "argv": sys.argv[1:]},
        "steps": steps,
        "state_checks": checks,
        "installer_calls": (
            {"daemon_stopped_before_file_swap":
             "systemctl --user stop sayit-ermano.service" in box.calls()}
            if box is not None else None),
        "verdict": {"ok": bool(ok)},
    }
    if box is not None:
        report["sandbox_log_tail"] = box.calls()[-2000:]
    json_path, md_path = write_evidence(args.out_dir, report)
    print(f"verdict: {'PASS' if ok else 'FAIL'}\n"
          f"evidence: {json_path}\n          {md_path}", flush=True)
    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


def write_evidence(out_dir: Path, report: dict) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    when = datetime.now(timezone.utc)
    json_path, md_path = evidence_paths(out_dir, when)
    report["written_utc"] = when.isoformat(timespec="seconds")
    json_path.write_text(json.dumps(report, indent=1) + "\n",
                         encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def render_markdown(r: dict) -> str:
    v_old = r["versions"]["old"]
    v_new = r["versions"]["new"]
    status = "PASS" if r["verdict"]["ok"] else "FAIL"
    lines = [
        f"# Upgrade/rollback rehearsal {r['started_utc']} — {status}",
        "",
        f"User-space lifecycle via the real one-shot installer in a "
        f"sandboxed $HOME (no root, no apt, no network): "
        f"install **v{v_old}** → seed config/history → `--update` to a "
        f"locally-built **v{v_new}** → rollback to v{v_old}. "
        f"Source SHA `{r['repo_sha']}`.",
        "",
        "| Step | Status | Detail | ms |",
        "|---|---|---|---|",
    ]
    for s in r["steps"]:
        detail = str(s["detail"]).replace("|", "\\|")[:120]
        lines.append(f"| {s['step']} | {s['status']} | {detail} "
                     f"| {s['ms']} |")
    lines += ["", "## User state", ""]
    for phase, label in (("after_upgrade", "across the upgrade"),
                         ("after_rollback", "across the rollback")):
        c = r["state_checks"].get(phase) or {}
        cfg, hist = c.get("config"), c.get("history")
        if not cfg or not hist:
            lines.append(f"- {label}: not reached (see the step table)")
            continue
        lines.append(
            f"- {label}: {len(cfg['keys_before'])} config keys "
            f"(changed `{cfg['changed']}`, missing `{cfg['missing']}`; "
            f"byte-identical **{cfg['byte_identical']}**); history "
            f"{hist['rows_before']} rows "
            f"(byte-identical **{hist['ok']}**)")
    lines += [
        "",
        "Nothing here needs root, a display or a model download; the "
        "same rehearsal runs in the process lane "
        "(`tests/integration/test_q9_harness.py`).",
        "",
    ]
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Q9 user-space upgrade/rollback rehearsal "
                    "(one-shot installer, sandboxed HOME, evidence + gate).")
    ap.add_argument("--out-dir", type=Path, default=OUT_DEFAULT,
                    help=f"evidence directory (default {OUT_DEFAULT})")
    ap.add_argument("--work", type=Path,
                    default=Path(os.environ.get("TMPDIR", "/tmp"))
                    / f"sayit-upgrade-rehearsal-{os.getpid()}",
                    help="scratch dir for payloads + the sandbox machine "
                         "(removed unless --keep-work)")
    ap.add_argument("--python", default=sys.executable,
                    help="base interpreter for the payload venvs "
                         "(default: this one)")
    ap.add_argument("--repo", type=Path, default=REPO,
                    help="the tree to install as v0/v1 (default: the "
                         "checkout holding this script)")
    ap.add_argument("--keep-work", action="store_true",
                    help="keep the scratch dir for debugging")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run_rehearsal(args)
    except RehearsalError as e:
        print(f"upgrade rehearsal: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
