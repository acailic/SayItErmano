#!/usr/bin/env python3
"""Fresh-VM install-route QA matrix (issue #14, the paid-launch gate).

Charging money raises the cost of a broken first install to a refund
plus a bad review. This harness runs the paying-user funnel — install
-> first-run onboarding -> dictation -> update check — down EVERY
install route (one-shot user-space, one-shot --system, official .deb,
AUR, pipx, Flatpak) wherever the current environment actually offers
that route, and records SKIPPED + reason where it does not (no root,
no GUI session, wrong distro, tool missing). A route only executes
when its probe passes; nothing is silently skipped.

Division of labor (the 2026-11-21 reclassification of #14):

* agent/CI — this harness, its offline tests (injected fake runners;
  no root, no network, no installs), and the generated checklist tree
  under docs/quality/install-matrix/.
* owner — run `qa_matrix.py check --execute` on the fresh VMs
  (Ubuntu 24.04, Fedora, Arch; a GPU machine and a CPU machine), tick
  the manual checklists, and turn every red row into either a fix or a
  documented known issue + workaround in docs/guides/install.md.

Layout under --out (default docs/quality/install-matrix/):

  README.md            generated summary table, one row per route per
                       distro, regenerated from the cell files
  <route>/<distro>.md  one evidence file per route per distro: a
                       STATUS header the OWNER owns (PENDING|PASS|FAIL),
                       the manual checklist, and an append-only block
                       of automated-run entries between sentinels that
                       only this harness touches.

The harness never overwrites manual content; the only STATUS change it
makes is lowering a cell to FAIL when an executed stage fails.

Every step is a pure-ish function with injectable environment probes
and command runners (tests/test_qa_matrix.py); the CLI only wires real
subprocesses in.

Usage:
  qa_matrix.py init [--out DIR]        write missing cell files + README
  qa_matrix.py check [--out DIR] [--execute] [--route R ...]
                                       probe this machine and print the
                                       plan; --execute also RUNS the
                                       routes whose probes pass (real
                                       installs — fresh VM only) and
                                       records evidence. Exit 1 iff an
                                       executed stage failed.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

OUT_DEFAULT = Path("docs/quality/install-matrix")
REPO = "acailic/SayItErmano"
ISSUE_URL = f"https://github.com/{REPO}/issues/14"
FLATPAK_ID = "io.github.acailic.SayItErmano"
INSTALLER_URL = ("https://raw.githubusercontent.com/acailic/SayItErmano/"
                 "linux/scripts/install-one-shot.sh")
# Resolve the newest release .deb the same way install-one-shot.sh does
# (GitHub latest-release API -> browser_download_url), then apt-install
# it. Runs only on the owner's Ubuntu 24.04 VM, as root, with network.
LATEST_DEB_FETCH = (
    "set -e; url=$(curl -fsSL "
    f"https://api.github.com/repos/{REPO}/releases/latest "
    "| grep -o 'https[^\"]*_amd64\\.deb' | head -1); "
    'curl -fL -o sayit-ermano_latest.deb "$url"; '
    "apt install -y ./sayit-ermano_latest.deb"
)

STAGES = ("install", "onboarding", "dictation", "update")
PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
MAX_RUN_ENTRIES = 5  # per cell, newest first; older entries trimmed

BEGIN = "<!-- qa-matrix automated runs BEGIN (harness-owned; do not edit) -->"
END = "<!-- qa-matrix automated runs END -->"

DISTROS: dict[str, str] = {
    "ubuntu-24.04": "Ubuntu 24.04",
    "fedora": "Fedora (current Workstation)",
    "arch": "Arch Linux",
}
TOOLS = ("apt", "curl", "flatpak", "git", "makepkg", "pipx")


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Cmd:
    """One shell command of a stage; empty cwd means the run's scratch dir."""
    argv: tuple[str, ...]
    cwd: str = ""


@dataclasses.dataclass(frozen=True)
class Route:
    id: str
    name: str
    distros: tuple[str, ...]      # matrix distros this route applies to
    needs_root: bool
    guide_command: str            # what a human copy-pastes (install guide)
    install: tuple[Cmd, ...]
    onboarding: tuple[Cmd, ...]
    dictation: tuple[Cmd, ...]
    update: tuple[Cmd, ...]
    tool: str = ""                # extra required binary, probed by name
    note: str = ""                # caveat line in the cell checklist


def _cli(*args: str) -> tuple[Cmd, ...]:
    """Post-install stages go through the installed CLI on PATH."""
    return (Cmd(args),)


ROUTES: dict[str, Route] = {r.id: r for r in (
    Route(
        id="oneshot-user",
        name="One-shot installer (user-space)",
        distros=("ubuntu-24.04", "fedora", "arch"),
        needs_root=False,
        tool="curl",
        guide_command=f"curl -fsSL {INSTALLER_URL} | bash",
        install=(Cmd(("bash", "-c",
                      f"curl -fsSL {INSTALLER_URL} | bash")),),
        onboarding=_cli("sayit-ermano", "app", "--onboard"),
        dictation=_cli("sayit-ermano", "doctor"),
        update=(Cmd(("bash", "-c",
                     f"curl -fsSL {INSTALLER_URL} | bash -s -- --update")),),
    ),
    Route(
        id="oneshot-system",
        name="One-shot installer (--system)",
        distros=("ubuntu-24.04", "fedora"),
        needs_root=True,
        guide_command=f"curl -fsSL {INSTALLER_URL} | bash -s -- --system",
        note=("The installer's --system path is apt-based; on Fedora it is "
              "expected to stop at the package step — record the outcome "
              "and the pipx fallback if so."),
        install=(Cmd(("bash", "-c",
                      f"curl -fsSL {INSTALLER_URL} | bash -s -- --system")),),
        onboarding=_cli("sayit-ermano", "app", "--onboard"),
        dictation=_cli("sayit-ermano", "doctor"),
        update=(Cmd(("bash", "-c",
                     f"curl -fsSL {INSTALLER_URL} | bash -s -- --update")),),
    ),
    Route(
        id="deb",
        name="Official .deb + signed apt repo",
        distros=("ubuntu-24.04",),
        needs_root=True,
        guide_command=("sudo apt install ./sayit-ermano_<ver>-1_amd64.deb"
                       "   # grab it from the releases page first"),
        install=(Cmd(("bash", "-c", LATEST_DEB_FETCH)),),
        onboarding=_cli("sayit-ermano", "app", "--onboard"),
        dictation=_cli("sayit-ermano", "doctor"),
        update=(Cmd(("apt", "update")),
                Cmd(("apt", "install", "-y", "--only-upgrade",
                     "sayit-ermano"))),
    ),
    Route(
        id="pipx",
        name="pipx",
        distros=("ubuntu-24.04", "fedora", "arch"),
        needs_root=False,
        tool="pipx",
        guide_command="pipx install sayit-ermano",
        install=(Cmd(("pipx", "install", "sayit-ermano")),),
        onboarding=_cli("sayit-ermano", "app", "--onboard"),
        dictation=_cli("sayit-ermano", "doctor"),
        update=(Cmd(("pipx", "upgrade", "sayit-ermano")),),
    ),
    Route(
        id="aur",
        name="AUR package",
        distros=("arch",),
        needs_root=False,
        tool="makepkg",
        guide_command="git clone https://aur.archlinux.org/sayit-ermano.git "
                      "&& cd sayit-ermano && makepkg -si",
        install=(Cmd(("git", "clone",
                      "https://aur.archlinux.org/sayit-ermano.git", "aur")),
                 Cmd(("makepkg", "-si", "--noconfirm"), cwd="aur")),
        onboarding=_cli("sayit-ermano", "app", "--onboard"),
        dictation=_cli("sayit-ermano", "doctor"),
        update=(Cmd(("git", "pull", "--ff-only"), cwd="aur"),
                Cmd(("makepkg", "-si", "--noconfirm"), cwd="aur")),
    ),
    Route(
        id="flatpak",
        name="Flatpak (Flathub)",
        distros=("ubuntu-24.04", "fedora", "arch"),
        needs_root=False,
        tool="flatpak",
        guide_command=f"flatpak install flathub {FLATPAK_ID}",
        note=("Flathub publication is issue #12 — this cell stays PENDING "
              "until the route ships (the install stage will fail on the "
              "remote lookup until then)."),
        install=(Cmd(("flatpak", "install", "-y", "flathub", FLATPAK_ID)),),
        onboarding=(Cmd(("flatpak", "run", FLATPAK_ID,
                         "app", "--onboard")),),
        dictation=(Cmd(("flatpak", "run", FLATPAK_ID, "doctor")),),
        update=(Cmd(("flatpak", "update", "-y", FLATPAK_ID)),),
    ),
)}


@dataclasses.dataclass(frozen=True)
class Env:
    """Facts about the current machine the probes reduce to."""
    distro: str | None            # matrix distro id, or None off-matrix
    euid: int
    display: bool
    tools: dict[str, bool]
    host: str


@dataclasses.dataclass
class StageResult:
    stage: str
    status: str
    detail: str = ""


@dataclasses.dataclass
class RunResult:
    route: str
    distro: str | None
    status: str                   # PASS / FAIL / SKIP (route level)
    reason: str = ""              # route-level skip reason
    stages: list[StageResult] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# environment probe (injectable)
# ---------------------------------------------------------------------------

def parse_os_release(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def matrix_distro(os_release: dict[str, str]) -> str | None:
    """Map an os-release dict onto a matrix distro id (or None)."""
    distro_id = os_release.get("ID", "").lower()
    version = os_release.get("VERSION_ID", "")
    if distro_id == "ubuntu" and version == "24.04":
        return "ubuntu-24.04"
    if distro_id == "fedora":
        return "fedora"
    if distro_id == "arch":
        return "arch"
    return None


def probe_environment(
    *,
    os_release_text: str | None = None,
    euid: int | None = None,
    display: bool | None = None,
    tools: dict[str, bool] | None = None,
    host: str | None = None,
) -> Env:
    if os_release_text is None:
        os_release_text = Path("/etc/os-release").read_text(encoding="utf-8")
    if euid is None:
        euid = os.geteuid()
    if display is None:
        display = bool(os.environ.get("DISPLAY")
                       or os.environ.get("WAYLAND_DISPLAY"))
    if tools is None:
        tools = {name: shutil.which(name) is not None for name in TOOLS}
    if host is None:
        host = platform.node() or "unknown-host"
    return Env(distro=matrix_distro(parse_os_release(os_release_text)),
               euid=euid, display=display, tools=dict(tools), host=host)


def route_availability(route: Route, env: Env) -> tuple[bool, str]:
    """Can this route run HERE? Returns (available, skip-reason)."""
    if env.distro is None:
        return False, ("host distro is not a matrix target (matrix: "
                       + ", ".join(f"{key} ({label})"
                                   for key, label in DISTROS.items()) + ")")
    if env.distro not in route.distros:
        return False, (f"{route.name} is not a {DISTROS[env.distro]} route "
                       f"(applies to: {', '.join(route.distros)})")
    if route.needs_root and env.euid != 0:
        return False, ("needs root — on the VM run: sudo python3 "
                       "scripts/qa_matrix.py check --execute --route "
                       f"{route.id}")
    if not route.needs_root and env.euid == 0:
        return False, ("run as the regular user, not root (the user-space "
                       "layout must not be root-owned; makepkg also "
                       "refuses root)")
    if route.tool and not env.tools.get(route.tool):
        return False, f"{route.tool} is not installed on this machine"
    return True, ""


# ---------------------------------------------------------------------------
# execution (runner injectable)
# ---------------------------------------------------------------------------

Runner = Callable[[Cmd, Path], "tuple[int, str]"]


def real_run(cmd: Cmd, scratch: Path) -> tuple[int, str]:
    cwd = Path(cmd.cwd) if cmd.cwd else scratch
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(list(cmd.argv), cwd=str(cwd),
                              capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return 124, f"timeout: {' '.join(cmd.argv)}"
    except OSError as exc:
        return 127, f"cannot execute: {exc}"[:400]
    tail = ((proc.stdout or "") + (proc.stderr or ""))[-400:].strip()
    return proc.returncode, tail


def _run_stage(stage: str, cmds: tuple[Cmd, ...], run: Runner,
               scratch: Path) -> StageResult:
    for cmd in cmds:
        rc, tail = run(cmd, scratch)
        if rc != 0:
            return StageResult(stage, FAIL,
                               f"`{' '.join(cmd.argv)}` exited {rc}"
                               + (f": {tail}" if tail else ""))
    return StageResult(stage, PASS, f"{len(cmds)} command(s) ok")


def run_route(route: Route, env: Env, run: Runner, scratch: Path) -> RunResult:
    """One route down the funnel on THIS env; skips carry reasons."""
    available, reason = route_availability(route, env)
    if not available:
        return RunResult(route.id, env.distro, SKIP, reason)
    result = RunResult(route.id, env.distro, PASS)
    install_status: str | None = None
    for stage in STAGES:
        if stage in ("onboarding", "dictation") and not env.display:
            result.stages.append(StageResult(
                stage, SKIP, "needs a GUI session (DISPLAY/WAYLAND_DISPLAY) "
                "— run on the desktop VM, not headless"))
            continue
        if stage != "install" and install_status != PASS:
            what = "failed" if install_status == FAIL else "was skipped"
            result.stages.append(StageResult(
                stage, SKIP,
                f"install stage {what} on this machine — not attempted"))
            continue
        stage_result = _run_stage(stage, getattr(route, stage), run, scratch)
        if stage == "install":
            install_status = stage_result.status
        result.stages.append(stage_result)
    if any(s.status == FAIL for s in result.stages):
        result.status = FAIL
    return result


# ---------------------------------------------------------------------------
# evidence files (harness owns only the sentinel block + FAIL lowering)
# ---------------------------------------------------------------------------

def cell_path(out: Path, route_id: str, distro: str) -> Path:
    return out / route_id / f"{distro}.md"


def read_status(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("STATUS: "):
            return line.removeprefix("STATUS: ").strip()
    return None


def cell_template(route: Route, distro: str) -> str:
    note = f"\n{route.note}\n" if route.note else ""
    return f"""# {route.name} — {DISTROS[distro]}

STATUS: PENDING
<!-- ^ owner-maintained: PENDING | PASS | FAIL. The harness only ever
     LOWERS this to FAIL when an executed stage fails; only the owner
     marks PASS after the manual checklist below is complete. -->

Paid-launch install gate
([issue #14]({ISSUE_URL})): every route a paying user can take must
work on a machine that is not the developer's. This cell covers
**{route.name}** on **{DISTROS[distro]}**.
{note}
Human install command (see the
[install guide](../../../guides/install.md)):

```bash
{route.guide_command}
```

## Manual checklist — run on a fresh VM, per machine (GPU + CPU)

Automated part first: `python3 scripts/qa_matrix.py check --execute`
on the VM (add `--route {route.id}`; the root routes need
`sudo python3 scripts/qa_matrix.py check --execute --route {route.id}`).
Then tick, once per machine class:

- [ ] GPU machine: install succeeds end to end
- [ ] first-run onboarding window appears once; the 3-second tryout works
- [ ] real dictation: Right Ctrl, speak, Right Ctrl — polished text
      lands in another app
- [ ] update check: `sayit-ermano update` prints the right command for
      this route; updating to the NEXT release works
- [ ] CPU machine: install + onboarding + one real dictation
      (transcription falls back to CPU int8, `doctor` confirms)

Record date + commit SHA + machine per run, appended at the bottom
(house rule from [desktop-matrix](../../../dev/desktop-matrix.md)).

## Automated harness runs

{BEGIN}
_No automated run recorded yet._
{END}
"""


def init_tree(out: Path) -> list[Path]:
    """Write any missing cell files; existing files are never touched."""
    written: list[Path] = []
    for route in ROUTES.values():
        for distro in route.distros:
            path = cell_path(out, route.id, distro)
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(cell_template(route, distro),
                                encoding="utf-8")
                written.append(path)
    return written


def _entry_markdown(result: RunResult, env: Env) -> str:
    when = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    lines = [f"### {when} — {env.host} — result={result.status}", ""]
    if result.status == SKIP:
        lines.append(f"route skipped: {result.reason}")
    else:
        lines += ["| stage | status | detail |", "|---|---|---|"]
        for s in result.stages:
            detail = s.detail.replace("|", "\\|")
            lines.append(f"| {s.stage} | {s.status} | {detail} |")
    lines.append("")
    return "\n".join(lines)


def _split_entries(block: str) -> list[str]:
    """Whole-entry strings (each starting at its `### ` header)."""
    entries = []
    for chunk in block.split("\n### "):
        chunk = chunk.strip()
        if not chunk or chunk.startswith("_No automated run"):
            continue
        entries.append(chunk if chunk.startswith("### ") else "### " + chunk)
    return entries


def record_result(path: Path, result: RunResult, env: Env) -> None:
    """Prepend one automated-run entry inside the sentinels; trim to
    MAX_RUN_ENTRIES; lower STATUS to FAIL iff the run failed. Manual
    content is never touched."""
    text = path.read_text(encoding="utf-8")
    begin_i = text.index(BEGIN)
    end_i = text.index(END)
    entries = _split_entries(text[begin_i + len(BEGIN):end_i])
    entries.insert(0, _entry_markdown(result, env).strip())
    block = "\n\n".join(entries[:MAX_RUN_ENTRIES])
    text = text[:begin_i + len(BEGIN)] + f"\n{block}\n" + text[end_i:]
    if result.status == FAIL and read_status(text) != FAIL:
        text = "\n".join(
            f"STATUS: {FAIL}" if line.startswith("STATUS: ") else line
            for line in text.splitlines()) + "\n"
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# generated README (the human checklist index)
# ---------------------------------------------------------------------------

def _latest_automated(text: str) -> tuple[str, str]:
    """(result, when) of the newest harness entry, or ("—", "")."""
    begin_i = text.find(BEGIN)
    end_i = text.find(END)
    if begin_i == -1 or end_i == -1:
        return "—", ""
    block = text[begin_i:end_i]
    for chunk in block.split("\n### ")[1:]:
        header = chunk.splitlines()[0]
        result, when = SKIP, ""
        for part in header.split("—"):
            part = part.strip()
            if part.startswith("result="):
                result = part.removeprefix("result=")
            elif "T" in part and part.endswith("Z"):
                when = part
        if result == SKIP:
            reason = ""
            for line in chunk.splitlines():
                if line.startswith("route skipped:"):
                    reason = line.removeprefix("route skipped: ")[:60]
                    break
            return (f"SKIPPED ({reason}…)" if reason else "SKIPPED"), when
        return result, when
    return "—", ""


def build_readme(out: Path) -> str:
    lines = [
        "# Fresh-VM install matrix — the paid-launch QA gate (#14)",
        "",
        "Charging money raises the cost of a broken first install to a",
        "refund plus a bad review. Every route a paying user can take must",
        "work on a machine that is not the developer's. One evidence file",
        "per route per distro lives beside this page; this README is the",
        "summary table, generated by `scripts/qa_matrix.py` — regenerate",
        "with `python3 scripts/qa_matrix.py init` (never edit it by hand;",
        "edit the cell files).",
        "",
        "## Summary",
        "",
        "| Route | Distro | Manual | Automated (last) | Evidence |",
        "|---|---|---|---|---|",
    ]
    for route in ROUTES.values():
        for distro in route.distros:
            path = cell_path(out, route.id, distro)
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            manual = read_status(text) or "MISSING"
            auto, when = _latest_automated(text)
            when_s = f" @ {when}" if when else ""
            rel = f"{route.id}/{distro}.md"
            lines.append(f"| {route.name} | {DISTROS[distro]} | {manual} "
                         f"| {auto}{when_s} | [{rel}]({rel}) |")
    lines += [
        "",
        "Legend — Manual: the owner-maintained cell `STATUS` "
        "(PENDING / PASS / FAIL). Automated: the last "
        "`qa_matrix.py check --execute` result on a VM, or the skip "
        "reason. A stage can be SKIPPED with a reason (headless host, "
        "route not for this distro, tool missing) — a skip is never "
        "silently dropped.",
        "",
        "## Owner workflow (per distro: Ubuntu 24.04, Fedora, Arch)",
        "",
        "1. Fresh VM, GPU machine and CPU machine (a CPU VM is fine:",
        "   transcription must fall back to int8 and still work).",
        "2. On the VM desktop: `python3 scripts/qa_matrix.py check",
        "   --execute` (probes first; runs only what this machine",
        "   offers). For the root routes (`deb`, `oneshot-system`):",
        "   `sudo python3 scripts/qa_matrix.py check --execute",
        "   --route <id>`. Copy the touched cell files back and commit.",
        "3. Do the manual part of every cell's checklist (onboarding",
        "   window, one REAL dictation into another app, update to the",
        "   NEXT release once it exists).",
        "4. Mark the cell `STATUS: PASS` (or leave FAIL) at its file",
        "   top; regenerate this table with `qa_matrix.py init`.",
        "",
        "## Red-row policy",
        "",
        "Zero red rows is the gate. A FAIL row must become either a fix",
        "or a **documented known issue with a workaround** in the",
        "[install guide](../../guides/install.md) — the row then links",
        "the guide anchor from its file. Until then the gate is red.",
        "",
        "## Route order in the guide",
        "",
        "The [install guide](../../guides/install.md) lists routes",
        "best-converting first: the zero-sudo one-shot user install,",
        "then the .deb with its signed apt update channel, then",
        "--system, pipx, AUR, source. Keep that order — this gate",
        "(#14) owns it.",
        "",
    ]
    return "\n".join(lines)


def write_readme(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "README.md").write_text(build_readme(out), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_plan(routes: list[Route], env: Env) -> None:
    print(f"host {env.host}: distro={env.distro or 'off-matrix'} "
          f"euid={env.euid} display={env.display} "
          f"tools={','.join(k for k, v in sorted(env.tools.items()) if v)}")
    for route in routes:
        ok, reason = route_availability(route, env)
        print(f"  {route.id:<15} {'RUN' if ok else f'SKIP ({reason})'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qa_matrix.py",
        description="Fresh-VM install-route QA matrix (issue #14).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser(
        "init", help="write missing cell files + regenerate the README")
    p_init.add_argument("--out", type=Path, default=OUT_DEFAULT)

    p_check = sub.add_parser(
        "check", help="probe this machine; --execute also runs the "
        "available routes (real installs — fresh VM only)")
    p_check.add_argument("--out", type=Path, default=OUT_DEFAULT)
    p_check.add_argument("--execute", action="store_true",
                         help="actually run installs/updates (side effects)")
    p_check.add_argument("--route", action="append", choices=list(ROUTES),
                         help="restrict to this route (repeatable)")

    args = parser.parse_args(argv)

    if args.cmd == "init":
        written = init_tree(args.out)
        write_readme(args.out)
        print(f"install-matrix: {len(written)} cell file(s) written, tree "
              f"of {sum(len(r.distros) for r in ROUTES.values())} cells "
              f"under {args.out}, README regenerated")
        return 0

    env = probe_environment()
    routes = [ROUTES[r] for r in (args.route or list(ROUTES))]
    _print_plan(routes, env)
    if not args.execute:
        print("probe only — nothing executed, nothing recorded "
              "(pass --execute on the fresh VM)")
        return 0

    scratch = Path(tempfile.mkdtemp(prefix="qa-matrix-"))
    failed = False
    for route in routes:
        result = run_route(route, env, real_run, scratch / route.id)
        marker = {PASS: "ok", FAIL: "RED", SKIP: "skipped"}[result.status]
        print(f"{route.id}: {marker}"
              + (f" — {result.reason}" if result.reason else ""))
        for s in result.stages:
            print(f"    {s.stage:<10} {s.status}"
                  + (f" — {s.detail[:120]}" if s.detail else ""))
        if result.distro is not None:
            record_result(cell_path(args.out, route.id, result.distro),
                          result, env)
        else:
            print("    (off-matrix host — not recorded into a cell file)")
        failed = failed or result.status == FAIL
    write_readme(args.out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
