#!/usr/bin/env python3
"""Fresh-VM install matrix — the paid-launch QA gate (#14).

Charging money raises the cost of a broken first install to a refund
plus a bad review, so every route a paying user can take must work on
machines that are not the developer's. This harness runs each install
route (one-shot user-space, one-shot --system, the official .deb, AUR,
pipx, Flathub) through install -> first-run onboarding -> dictation ->
update WHERE the current environment can exercise it, and records a
SKIPPED row with an explicit reason everywhere it cannot (wrong distro,
no root, no display, route not published yet).

Evidence: one JSON per route x distro under docs/quality/install-matrix/
plus the generated README.md (summary table + the fresh-VM checklist
the owner executes on real Ubuntu 24.04 / Fedora / Arch VMs, GPU and
CPU machines).

Usage:
  scripts/qa_matrix.py               # probe + run + write evidence/README
  scripts/qa_matrix.py --check       # gate: all rows evidenced, zero FAILs
  scripts/qa_matrix.py --routes deb,pipx --distros ubuntu-24.04

Exit status: 1 when a row FAILed (--check additionally fails on missing
evidence); 0 otherwise. No root needed to run — root-only rows simply
record SKIP(needs root) for the owner's fresh-VM pass.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

SCHEMA = 1
REPO_OWNER = "acailic/SayItErmano"
FLATHUB_APP_ID = "io.github.acailic.SayItErmano"
APP = "sayit-ermano"
DEFAULT_OUT = Path("docs/quality/install-matrix")

# step statuses; route rollup adds PENDING (manual steps outstanding)
# and SKIPPED (whole row skipped). FAIL is the only red row.
PASS, FAIL, SKIP, MANUAL = "PASS", "FAIL", "SKIP", "MANUAL"
PENDING, SKIPPED = "PENDING", "SKIPPED"

ONESHOT_URL = (
    "https://raw.githubusercontent.com/acailic/SayItErmano/linux/"
    "scripts/install-one-shot.sh")

# tools probed by name; gates below key into this dict
TOOLS = ("bash", "curl", "dpkg", "dpkg-deb", "apt", "makepkg", "sudo",
         "pipx", "flatpak")


# ---------------------------------------------------------------------------
# matrix model
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Distro:
    key: str                      # matrix key ("ubuntu-24.04")
    title: str                    # human name ("Ubuntu 24.04")
    os_ids: tuple[str, ...]       # /etc/os-release ID matches
    version_ids: tuple[str, ...]  # VERSION_ID matches; empty = any


@dataclasses.dataclass(frozen=True)
class Env:
    distro_key: str | None        # matrix key of the host, None = other
    distro_pretty: str
    arch: str
    is_root: bool
    has_display: bool
    tools: dict[str, bool]
    flathub: str                  # "yes" | "no" | "unknown"
    git_head: str | None


@dataclasses.dataclass(frozen=True)
class ExecResult:
    returncode: int
    output_tail: str


# exec runners execute a step command; injectable so tests never spawn
# a real install (no root, no network in the unit tier)
ExecFn = Callable[[Sequence[str], int], ExecResult]

# (key, title, timeout_s); cmd resolution is lazy (route.install_cmd
# may hit the network — never called for skipped rows)
STEP_DEFS = (
    ("install", "Install via the route", 1800),
    ("onboard", "First-run onboarding (app opens onboarding once)", 300),
    ("dictate", "Real dictation — GPU machine AND CPU machine", 0),
    ("update", "Update to the next release", 1800),
)

MANUAL_DETAIL = (
    "human check on the fresh VM: one real dictation on a GPU machine "
    "and one on a CPU-only machine (Right Ctrl, speak, Right Ctrl — "
    "text lands in a focused app; first take on CPU downloads the "
    "model). Record the outcome in this file's notes and flip the "
    "status to PASS/FAIL.")


@dataclasses.dataclass(frozen=True)
class Route:
    key: str
    title: str
    distros: tuple[str, ...]
    install_cmd: Callable[[Path], tuple[str, ...] | None]
    update_cmd: Callable[[Path], tuple[str, ...]]
    install_hint: str   # checklist copy-paste (docs/guides/install.md)
    update_hint: str
    gates: Callable[[Env], str | None]  # None = runnable here


DISTROS: dict[str, Distro] = {
    d.key: d for d in (
        Distro("ubuntu-24.04", "Ubuntu 24.04", ("ubuntu",), ("24.04",)),
        Distro("fedora", "Fedora", ("fedora",), ()),
        Distro("arch", "Arch Linux", ("arch",), ()),
    )
}


# ---------------------------------------------------------------------------
# route commands (run on the VM where the row is available)
# ---------------------------------------------------------------------------

def _oneshot(repo: Path, *extra: str) -> tuple[str, ...]:
    return ("bash", str(repo / "scripts" / "install-one-shot.sh"), *extra)


def latest_release_deb() -> str | None:
    """Browser URL of the newest release's amd64 .deb — gh (reads
    GITHUB_TOKEN itself) first, plain GitHub API fallback. None when it
    cannot be resolved (offline / no release asset)."""
    if shutil.which("gh") is not None:
        try:
            proc = subprocess.run(
                ["gh", "release", "view", "-R", REPO_OWNER,
                 "--json", "tag_name,assets",
                 "--jq", r'.tag_name as $t | .assets[] '
                         r'| select(.name | endswith("_amd64.deb")) '
                         r'| "\($t) \(.name)"'],
                capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            proc = None
        if proc is not None and proc.returncode == 0:
            parts = proc.stdout.strip().split()
            if len(parts) == 2:
                tag, name = parts
                return (f"https://github.com/{REPO_OWNER}/releases/"
                        f"download/{tag}/{name}")
    try:
        req = urllib.request.urlopen(  # noqa: S310 — fixed https endpoint
            f"https://api.github.com/repos/{REPO_OWNER}/releases/latest",
            timeout=30)
        with req:
            data = json.loads(req.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    for asset in data.get("assets", []) if isinstance(data, dict) else []:
        url = asset.get("browser_download_url", "")
        if url.endswith("_amd64.deb"):
            return url
    return None


def _deb_install_cmd(repo: Path) -> tuple[str, ...] | None:
    url = latest_release_deb()
    if url is None:
        return None
    name = url.rsplit("/", 1)[-1]
    return ("sh", "-c", 'curl -fLO "$1" && sudo apt install -y "./$2"',
            "qa-deb-install", url, name)


def _deb_update_cmd(repo: Path) -> tuple[str, ...]:
    return ("sh", "-c",
            "sudo apt update && "
            "sudo apt install -y --only-upgrade sayit-ermano",
            "qa-deb-update")


def _aur_cmd(repo: Path) -> tuple[str, ...]:
    return ("bash", "-c", 'cd "$1" && makepkg -si --noconfirm --needed',
            "qa-aur", str(repo / "packaging" / "aur"))


def _pipx_install(repo: Path) -> tuple[str, ...]:
    return ("pipx", "install", APP)


def _pipx_update(repo: Path) -> tuple[str, ...]:
    return ("pipx", "upgrade", APP)


def _flatpak_install(repo: Path) -> tuple[str, ...]:
    return ("flatpak", "install", "-y", "flathub", FLATHUB_APP_ID)


def _flatpak_update(repo: Path) -> tuple[str, ...]:
    return ("flatpak", "update", "-y", FLATHUB_APP_ID)


# ---------------------------------------------------------------------------
# gates: ordered unmet-requirement checks, first failure wins
# ---------------------------------------------------------------------------

def _need(cond: bool, reason: str) -> str | None:
    return None if cond else reason


def _gates_oneshot(env: Env) -> str | None:
    return (
        _need(env.tools.get("bash") and env.tools.get("curl")
              and env.tools.get("dpkg") and env.tools.get("dpkg-deb"),
              "one-shot installer is deb-based — needs bash+curl+dpkg/"
              "dpkg-deb on the host")
        or _need(env.arch == "x86_64",
                 f"single-target amd64 packages; host arch is {env.arch}")
    )


def _gates_oneshot_system(env: Env) -> str | None:
    return (
        _gates_oneshot(env)
        or _need(env.is_root,
                 "needs root (sudo apt) — provision the VM and rerun as "
                 "root, or run with a passwordless-sudo user")
    )


def _gates_deb(env: Env) -> str | None:
    return (
        _need(env.tools.get("apt") and env.tools.get("dpkg-deb")
              and env.tools.get("curl") and env.tools.get("sudo"),
              "the .deb route needs apt+dpkg-deb+curl+sudo")
        or _need(env.arch == "x86_64",
                 f"single-target amd64 packages; host arch is {env.arch}")
        or _need(env.is_root,
                 "needs root (sudo apt install) — rerun as root on the VM")
    )


def _gates_aur(env: Env) -> str | None:
    return (
        _need(env.tools.get("makepkg") and env.tools.get("curl")
              and env.tools.get("sudo"),
              "the AUR route needs makepkg+curl+sudo on the host")
        or _need(not env.is_root,
                 "makepkg refuses to run as root — rerun as a normal "
                 "user with sudo")
    )


def _gates_pipx(env: Env) -> str | None:
    return _need(env.tools.get("pipx"), "pipx not found on PATH")


def _gates_flatpak(env: Env) -> str | None:
    return (
        _need(env.tools.get("flatpak"), "flatpak not found on PATH")
        or _need(env.flathub == "yes",
                 "Flathub listing pending — route blocked by #12"
                 if env.flathub == "no" else
                 "could not confirm the Flathub listing (probe came "
                 "back unknown — offline?)")
    )


ROUTES: dict[str, Route] = {
    r.key: r for r in (
        Route("oneshot-user", "one-shot installer (user-space)",
              ("ubuntu-24.04", "fedora"),
              lambda repo: _oneshot(repo),
              lambda repo: _oneshot(repo, "--update"),
              f"`curl -fsSL {ONESHOT_URL} | bash`",
              f"`curl -fsSL {ONESHOT_URL} | bash -s -- --update`",
              _gates_oneshot),
        Route("oneshot-system", "one-shot installer (--system)",
              ("ubuntu-24.04", "fedora"),
              lambda repo: _oneshot(repo, "--system"),
              lambda repo: _oneshot(repo, "--system", "--update"),
              f"`curl -fsSL {ONESHOT_URL} | bash -s -- --system`",
              f"`curl -fsSL {ONESHOT_URL} | bash -s -- --system --update`",
              _gates_oneshot_system),
        Route("deb", "official .deb + signed apt repo",
              ("ubuntu-24.04",),
              _deb_install_cmd, _deb_update_cmd,
              "`curl -LO <releases>/sayit-ermano_<ver>-1_amd64.deb && "
              "sudo apt install ./sayit-ermano_*.deb`",
              "`sudo apt update && sudo apt upgrade sayit-ermano`",
              _gates_deb),
        Route("aur", "AUR package",
              ("arch",),
              _aur_cmd, _aur_cmd,
              "`git clone https://aur.archlinux.org/sayit-ermano.git && "
              "cd sayit-ermano && makepkg -si`",
              "rebuild from the new release tag's `packaging/aur/` "
              "(bump pkgver, `makepkg -si`)",
              _gates_aur),
        Route("pipx", "pipx (any distro)",
              ("ubuntu-24.04", "fedora", "arch"),
              _pipx_install, _pipx_update,
              f"`pipx install {APP}`",
              f"`pipx upgrade {APP}`",
              _gates_pipx),
        Route("flatpak", "Flatpak from Flathub",
              ("ubuntu-24.04", "fedora", "arch"),
              _flatpak_install, _flatpak_update,
              f"`flatpak install flathub {FLATHUB_APP_ID}`",
              f"`flatpak update {FLATHUB_APP_ID}`",
              _gates_flatpak),
    )
}


# ---------------------------------------------------------------------------
# environment probing
# ---------------------------------------------------------------------------

def parse_os_release(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip().strip('"')
    return fields


def distro_key_for(fields: dict[str, str]) -> str | None:
    os_id = fields.get("ID", "")
    version = fields.get("VERSION_ID", "")
    for distro in DISTROS.values():
        if os_id in distro.os_ids and (
                not distro.version_ids or version in distro.version_ids):
            return distro.key
    return None


def probe_flathub(has_flatpak: bool) -> str:
    """Is the app actually listed on Flathub? yes/no/unknown (probe
    failed — never punished as a hard 'no' when offline)."""
    if not has_flatpak:
        return "unknown"
    try:
        proc = subprocess.run(
            ["flatpak", "remote-info", "flathub", FLATHUB_APP_ID],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if proc.returncode == 0:
        return "yes"
    low = (proc.stdout + proc.stderr).lower()
    if "network" in low or "offline" in low or "timed out" in low:
        return "unknown"
    return "no"


def git_head(repo: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             check=True, capture_output=True, text=True,
                             timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def probe_environment(repo: Path) -> Env:
    try:
        fields = parse_os_release(
            Path("/etc/os-release").read_text(encoding="utf-8"))
    except OSError:
        fields = {}
    return Env(
        distro_key=distro_key_for(fields),
        distro_pretty=(fields.get("PRETTY_NAME") or fields.get("ID")
                       or "unknown"),
        arch=platform.machine() or "unknown",
        is_root=os.geteuid() == 0,
        has_display=bool(os.environ.get("DISPLAY")
                         or os.environ.get("WAYLAND_DISPLAY")),
        tools={name: shutil.which(name) is not None for name in TOOLS},
        flathub=probe_flathub(shutil.which("flatpak") is not None),
        git_head=git_head(repo),
    )


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------

def real_exec(argv: Sequence[str], timeout: int) -> ExecResult:
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return ExecResult(124, f"timed out after {timeout}s")
    except OSError as exc:
        return ExecResult(127, f"cannot execute {argv[0]}: {exc}")
    tail = (proc.stdout + "\n" + proc.stderr).strip()[-2000:]
    return ExecResult(proc.returncode, tail)


def route_skip_reason(route: Route, distro: Distro, env: Env) -> str | None:
    """First unmet requirement for running this row HERE, or None."""
    if env.distro_key != distro.key:
        return (f"host is {env.distro_pretty} — this row needs a fresh "
                f"{distro.title} VM (owner fresh-VM pass)")
    return route.gates(env)


def _step_result(step_title: str, status: str, detail: str, *,
                 cmd: Sequence[str] | None = None,
                 returncode: int | None = None,
                 output_tail: str | None = None) -> dict:
    return {"status": status, "title": step_title, "detail": detail,
            "cmd": list(cmd) if cmd is not None else None,
            "returncode": returncode, "output_tail": output_tail}


def _resolve_cmd(route: Route, key: str, repo: Path) -> tuple[str, ...] | None:
    if key == "install":
        return route.install_cmd(repo)
    if key == "onboard":
        return (APP, "app", "--onboard")
    if key == "update":
        return route.update_cmd(repo)
    return None  # dictate: manual by design


def run_route(route: Route, distro: Distro, env: Env, *, repo: Path,
              exec_fn: ExecFn, now: str | None = None) -> dict:
    """One route x distro row -> evidence dict (see docs/quality/
    install-matrix/README.md for the status model)."""
    steps: list[dict] = []
    skip = route_skip_reason(route, distro, env)
    if skip is not None:
        for key, title, _ in STEP_DEFS:
            steps.append(_step_result(title, SKIP, skip))
        status = SKIPPED
    else:
        install_ok = False
        for key, title, timeout in STEP_DEFS:
            if key == "dictate":
                steps.append(_step_result(title, MANUAL, MANUAL_DETAIL))
                continue
            if key != "install" and not install_ok:
                steps.append(_step_result(
                    title, SKIP,
                    "install step did not pass — downstream steps not "
                    "attempted"))
                continue
            cmd = _resolve_cmd(route, key, repo)
            if cmd is None:
                detail = ("cannot resolve the install command — latest "
                          "release .deb lookup failed (offline? no "
                          "release asset?)")
                steps.append(_step_result(title, FAIL, detail))
                if key == "install":
                    install_ok = False
                continue
            if key == "onboard" and not env.has_display:
                steps.append(_step_result(
                    title, SKIP,
                    "no live display session (DISPLAY/WAYLAND_DISPLAY "
                    "unset) — onboarding needs the VM's desktop"))
                continue
            result = exec_fn(cmd, timeout)
            passed = result.returncode == 0
            steps.append(_step_result(
                title, PASS if passed else FAIL,
                "exit 0" if passed else f"exit {result.returncode}",
                cmd=cmd, returncode=result.returncode,
                output_tail=result.output_tail))
            if key == "install":
                install_ok = passed
        statuses = {s["status"] for s in steps}
        status = (FAIL if FAIL in statuses
                  else PENDING if MANUAL in statuses
                  else PASS)
    return {
        "schema": SCHEMA,
        "generated_at": now or _dt.datetime.now(
            _dt.timezone.utc).isoformat(timespec="seconds"),
        "route": route.key,
        "distro": distro.key,
        "status": status,
        "skip_reason": skip,
        "steps": steps,
        "environment": dataclasses.asdict(env),
        "notes": [],  # owner fills during the fresh-VM pass
    }


# ---------------------------------------------------------------------------
# evidence + README
# ---------------------------------------------------------------------------

def evidence_path(out_dir: Path, route_key: str, distro_key: str) -> Path:
    return out_dir / f"{route_key}-{distro_key}.json"


def write_evidence(out_dir: Path, evidence: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_path(out_dir, evidence["route"], evidence["distro"])
    path.write_text(json.dumps(evidence, indent=2) + "\n",
                    encoding="utf-8")
    return path


_CELLS = {PASS: "✅ pass", FAIL: "❌ FAIL", SKIP: "➖ skip",
          MANUAL: "⏳ manual", PENDING: "⏳ pending",
          SKIPPED: "➖ skipped", None: "—"}


def render_readme(evidences: list[dict]) -> str:
    by_combo = {(ev["route"], ev["distro"]): ev for ev in evidences}
    lines: list[str] = [
        "# Fresh-VM install matrix — paid-launch QA gate (#14)",
        "",
        "Generated by `scripts/qa_matrix.py` — do not edit by hand; "
        "rerun the harness (on the VM) to refresh. One evidence JSON "
        "sits next to this page per route x distro; SKIPPED rows carry "
        "the reason and are the owner's fresh-VM worklist.",
        "",
        "## How a row is run",
        "",
        "1. Provision a FRESH VM of the distro (no prior SayItErmano "
        "install) — GPU machine and a CPU-only machine per route.",
        "2. Clone the repo at the release commit and run "
        "`python3 scripts/qa_matrix.py` — it runs every route the VM "
        "can exercise and writes evidence next to this page.",
        "3. Do the manual steps (dictation on both machines) and fill "
        "the row's `notes`.",
        "4. Update to the NEXT release when it ships (the update "
        "command per route is in the checklist below), rerun, commit.",
        "",
        "## Summary",
        "",
        "| Route | Distro | Install | Onboard | Dictate | Update | Row |"
        " Evidence |",
        "|---|---|---|---|---|---|---|---|",
    ]
    missing = False
    for route in ROUTES.values():
        for dk in route.distros:
            ev = by_combo.get((route.key, dk))
            cells = [_CELLS[None]] * 5
            if ev is None:
                missing = True
            else:
                by_key = dict(zip(("install", "onboard", "dictate",
                                   "update"), ev["steps"]))
                cells = [_CELLS.get(by_key[k]["status"], "—")
                         for k in ("install", "onboard", "dictate",
                                   "update")]
                cells.append(_CELLS.get(ev["status"], "—"))
            lines.append(
                f"| {route.title} | {DISTROS[dk].title} | {cells[0]} | "
                f"{cells[1]} | {cells[2]} | {cells[3]} | {cells[4]} | "
                f"`{route.key}-{dk}.json` |")
    lines += [
        "",
        "Legend: ✅ pass · ❌ FAIL (**red row**) · ⏳ manual (owner "
        "pending) · ➖ skip (reason in the evidence JSON). A fully "
        "skipped row means the harness was not run on that distro's "
        "fresh VM yet.",
        "",
        "## Red-row policy",
        "",
        "Zero ❌ rows at a paid launch. Every red row must be fixed or "
        "documented as a known issue **with a workaround** in the "
        "install guide ([docs/guides/install.md]"
        "(../../guides/install.md)) before release. "
        "`scripts/qa_matrix.py --check` fails while a red row or a "
        "missing evidence file exists.",
        "",
        "## Fresh-VM checklist (owner)",
        "",
        "Per row: install → onboarding opens once on first "
        "`sayit-ermano app` launch (finish the 3-second tryout) → "
        "dictate on the GPU machine AND the CPU machine → update to "
        "the next release → rerun the harness and commit evidence.",
        "",
    ]
    for dk in DISTROS:
        routes_here = [r for r in ROUTES.values() if dk in r.distros]
        lines.append(f"### {DISTROS[dk].title}")
        lines.append("")
        for route in routes_here:
            lines.append(f"- **{route.title}** — install: "
                         f"{route.install_hint}; update: "
                         f"{route.update_hint}")
        lines.append("")
    if missing:
        lines.append("> Some route x distro rows have no evidence in "
                     "this directory — rerun the harness unfiltered to "
                     "regenerate the full matrix.")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------

def check_matrix(out_dir: Path) -> list[str]:
    """--check: every route x distro evidenced, fresh schema, zero FAIL
    rows, README present. Returns the problem list (empty = gate green)."""
    problems: list[str] = []
    for route in ROUTES.values():
        for dk in route.distros:
            path = evidence_path(out_dir, route.key, dk)
            if not path.exists():
                problems.append(f"missing evidence {path.name}")
                continue
            try:
                ev = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                problems.append(f"{path.name}: unreadable ({exc})")
                continue
            if ev.get("schema") != SCHEMA:
                problems.append(f"{path.name}: schema "
                                f"{ev.get('schema')!r} != {SCHEMA} — "
                                "stale evidence, rerun the harness")
            if ev.get("status") == FAIL:
                problems.append(f"{path.name}: red row — fix it or "
                                "document it as a known issue with a "
                                "workaround in docs/guides/install.md")
    if not (out_dir / "README.md").exists():
        problems.append("missing README.md — rerun the harness")
    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_matrix(env: Env, *, repo: Path, out_dir: Path, exec_fn: ExecFn,
               route_filter: set[str] | None = None,
               distro_filter: set[str] | None = None) -> list[dict]:
    evidences: list[dict] = []
    for route in ROUTES.values():
        if route_filter and route.key not in route_filter:
            continue
        for dk in route.distros:
            if distro_filter and dk not in distro_filter:
                continue
            evidence = run_route(route, DISTROS[dk], env, repo=repo,
                                 exec_fn=exec_fn)
            write_evidence(out_dir, evidence)
            evidences.append(evidence)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "README.md").write_text(render_readme(evidences),
                                       encoding="utf-8")
    return evidences


def _parse_filter(arg: str, valid: set[str], what: str) -> set[str]:
    keys = {k.strip() for k in arg.split(",") if k.strip()}
    unknown = keys - valid
    if unknown:
        raise SystemExit(f"unknown {what}: {', '.join(sorted(unknown))} "
                         f"(valid: {', '.join(sorted(valid))})")
    return keys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qa_matrix.py",
        description="fresh-VM install matrix harness (#14)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT,
                        help=f"evidence directory (default: {DEFAULT_OUT})")
    parser.add_argument("--repo", type=Path,
                        default=Path(__file__).resolve().parents[1],
                        help="repo checkout the routes install from")
    parser.add_argument("--routes", default="",
                        help="comma-separated route keys (default: all)")
    parser.add_argument("--distros", default="",
                        help="comma-separated distro keys (default: all)")
    parser.add_argument("--check", action="store_true",
                        help="gate mode: all rows evidenced, zero FAILs")
    args = parser.parse_args(argv)

    if args.check:
        problems = check_matrix(args.out_dir)
        for problem in problems:
            print(f"check: {problem}")
        if problems:
            print("check: FAILED")
            return 1
        print(f"check: all {sum(len(r.distros) for r in ROUTES.values())} "
              "route x distro rows evidenced, zero red rows")
        return 0

    route_filter = (_parse_filter(args.routes, set(ROUTES), "route")
                    if args.routes else None)
    distro_filter = (_parse_filter(args.distros, set(DISTROS), "distro")
                     if args.distros else None)
    env = probe_environment(args.repo)
    print(f"host: {env.distro_pretty} ({env.arch}), "
          f"root={env.is_root}, display={env.has_display}, "
          f"flathub={env.flathub}, head={env.git_head or '?'}")
    evidences = run_matrix(env, repo=args.repo, out_dir=args.out_dir,
                           exec_fn=real_exec, route_filter=route_filter,
                           distro_filter=distro_filter)
    red = 0
    for ev in evidences:
        mark = {"FAIL": "❌", "PENDING": "⏳", "PASS": "✅",
                "SKIPPED": "➖"}[ev["status"]]
        print(f"  {mark} {ev['route']:14} {ev['distro']:12} {ev['status']}"
              + (f" — {ev['skip_reason']}" if ev["skip_reason"] else ""))
        red += ev["status"] == FAIL
    print(f"{len(evidences)} rows -> {args.out_dir} "
          f"({red} red; manual steps are the owner's fresh-VM pass)")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
