#!/usr/bin/env python3
"""One-command Wayland/X11 smoke-matrix runner (#13).

Live-session counterpart of the offline suite: detects the session it is
logged into (XDG_SESSION_TYPE / WAYLAND_DISPLAY / DISPLAY — the same
probe precedence as fluidvoice/session.py), selects the applicable rows
of docs/dev/wayland-smoke-matrix.md (matrices A/B/C plus the D session
axes), runs the automated rows (doctor, daemon status, privacy sweep)
and prompts the operator for the manual dictation rows, then records
evidence under docs/quality/wayland/<date>-<profile>/ (results.json +
results.md). Exit code 1 on any red row; 2 on usage errors.

  python3 scripts/wayland_smoke.py --dry-run   # plan only, no session needed
  python3 scripts/wayland_smoke.py             # live run, once per session
  python3 scripts/wayland_smoke.py --rows A3   # re-run single rows
  python3 scripts/wayland_smoke.py --answers A2=pass,A3=fail:wrong-case

The owner runs this per logged-in session (GNOME Wayland first, then
sway, then the X11 control pass); housekeeping (daemon two-step, config
restore) is documented in the matrix doc.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # run as `python3 scripts/wayland_smoke.py`
    sys.path.insert(0, str(REPO_ROOT))

from fluidvoice import session as session_mod  # noqa: E402

SCHEMA = 1
DOCTOR_TIMEOUT_S = 180.0
STATUS_TIMEOUT_S = 30.0
VERSION_TIMEOUT_S = 15.0

# Session profiles (the evidence dir name and the row selector key).
PROFILE_X11 = "x11"
PROFILE_GNOME_WL = "gnome-wayland"
PROFILE_SWAY = "sway"
PROFILE_KDE_WL = "kde-wayland"
PROFILE_HYPRLAND = "hyprland"
PROFILE_WAYLAND = "wayland"  # generic/other compositor (river, …)
WAYLAND_PROFILES = (PROFILE_GNOME_WL, PROFILE_SWAY, PROFILE_KDE_WL,
                    PROFILE_HYPRLAND, PROFILE_WAYLAND)
ALL_PROFILES = (PROFILE_X11,) + WAYLAND_PROFILES

RUN_CMD = "python3 scripts/wayland_smoke.py"

# Compositor versions worth dating next to the evidence (version
# minimums pinned by the matrix: GNOME 48 for portal hotkeys, Plasma
# 6.7.4 for the shortcut description change, xdg-desktop-portal 1.21 vs
# the older consent flow). Best-effort: a missing binary records
# "unknown", never fails a row.
VERSION_PROBES = {
    PROFILE_GNOME_WL: {"gnome-shell": ["gnome-shell", "--version"],
                       "xdg-desktop-portal": ["xdg-desktop-portal",
                                              "--version"]},
    PROFILE_SWAY: {"sway": ["sway", "--version"],
                   "xdg-desktop-portal": ["xdg-desktop-portal", "--version"]},
    PROFILE_KDE_WL: {"plasmashell": ["plasmashell", "--version"],
                     "xdg-desktop-portal": ["xdg-desktop-portal",
                                            "--version"]},
    PROFILE_HYPRLAND: {"hyprctl": ["hyprctl", "version"],
                       "xdg-desktop-portal": ["xdg-desktop-portal",
                                              "--version"]},
    PROFILE_WAYLAND: {"xdg-desktop-portal": ["xdg-desktop-portal",
                                             "--version"]},
    PROFILE_X11: {},
}

ENV_SNAPSHOT_KEYS = ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
                     "XDG_SESSION_DESKTOP", "DESKTOP_SESSION",
                     "WAYLAND_DISPLAY", "DISPLAY")


# ---------------------------------------------------------------------------
# Rows — the single registry mirrors docs/dev/wayland-smoke-matrix.md
# (tests/test_wayland_smoke.py keeps the two in lockstep).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    id: str
    matrix: str                       # "A" | "B" | "C" | "D"
    case: str
    expected: Mapping[str, str]       # profile -> text, "*" = default
    kind: str                         # "auto" | "manual"
    profiles: frozenset[str]
    auto: str | None = None           # automated check name

    def expected_for(self, profile: str) -> str:
        return self.expected.get(profile) or self.expected["*"]


def _row(rid: str, matrix: str, case: str, expected, profiles, kind="manual",
         auto=None) -> Row:
    if isinstance(expected, str):
        expected = {"*": expected}
    return Row(id=rid, matrix=matrix, case=case, expected=expected,
               kind=kind, profiles=frozenset(profiles), auto=auto)


_WL = WAYLAND_PROFILES  # every row below lists its applicable profiles
_ROWS = [
    # Matrix A — GNOME Wayland (docs §Matrix A)
    _row("A1", "A", "doctor context section",
         "names the atspi provider, no crash", [PROFILE_GNOME_WL],
         kind="auto", auto="doctor"),
    _row("A2", "A", "Dictate into empty gedit field",
         "text typed; daemon log line `context: atspi (role=text, …)`",
         [PROFILE_GNOME_WL]),
    _row("A3", "A", "Dictate after typing `mid sentence` (no trailing space)",
         "one leading space inserted, no capitalization", [PROFILE_GNOME_WL]),
    _row("A4", "A", "Dictate after a `.` + newline",
         "no leading space, first letter capitalized", [PROFILE_GNOME_WL]),
    _row("A5", "A", "Select text in gedit, dictate",
         "selection read does NOT disturb the paste/typing; dictation "
         "lands at caret", [PROFILE_GNOME_WL]),
    _row("A6", "A", "Nautilus search field (role=search)",
         "GAAV: lowercase first letter, trailing period stripped",
         [PROFILE_GNOME_WL]),
    _row("A7", "A", "gnome-terminal, typed insertion",
         "trailing autocomplete space; paste (long text) uses "
         "ctrl+shift+v", [PROFILE_GNOME_WL]),
    _row("A8", "A", "gnome-terminal, spoken-send phrase",
         "Enter suppressed (log: `spoken-send: Enter suppressed at "
         "insert`)", [PROFILE_GNOME_WL]),
    _row("A9", "A", "Non-terminal app, spoken-send phrase",
         "Enter pressed after typing", [PROFILE_GNOME_WL]),
    _row("A10", "A",
         'Canonical rule {match=["gnome-terminal"], spoken_send="on"}',
         "Enter IS pressed in the terminal", [PROFILE_GNOME_WL]),
    _row("A11", "A", "Accessibility bus off (kill at-spi2 bus), dictate",
         "take succeeds, exactly pre-P2 behavior (no context line), "
         "no crash", [PROFILE_GNOME_WL]),
    _row("A12", "A", "context.provider = \"none\"",
         "identical to A11", [PROFILE_GNOME_WL]),
    _row("A13", "A", "Privacy sweep after A2–A10",
         "history rows contain no preceding/selection text, no AT-SPI "
         "app ids beyond the usual take-start field", [PROFILE_GNOME_WL],
         kind="auto", auto="privacy"),
    _row("A14", "A", "Latency: insertion-time read",
         "no perceptible delay between stop and typing (< ~150 ms extra)",
         [PROFILE_GNOME_WL]),
    _row("A15", "A",
         "Focus change mid-take (start in gedit, click terminal before stop)",
         "identity/quirks follow the FOCUS AT INSERTION (terminal quirks "
         "apply), no wrong-window typing", [PROFILE_GNOME_WL]),
    # Matrix B — sway (docs §Matrix B)
    _row("B1", "B", "A2–A6 equivalents (foot/kitty + editor)",
         "same as GNOME matrix", [PROFILE_SWAY]),
    _row("B2", "B", "wtype insertion + context identity",
         "terminal quirks active in foot/kitty via AT-SPI identity",
         [PROFILE_SWAY]),
    _row("B3", "B", "No AT-SPI bus in the session (minimal sway)",
         "degrade: no context, exactly pre-P2 behavior", [PROFILE_SWAY]),
    _row("B4", "B", "Privacy sweep", "as A13", [PROFILE_SWAY],
         kind="auto", auto="privacy"),
    # Matrix C — X11 control / regression (docs §Matrix C)
    _row("C1", "C", "Default config (context off)",
         "byte-identical pre-P2 behavior", [PROFILE_X11]),
    _row("C2", "C", "context.enabled=true, provider=\"auto\"",
         "x11 identity provider; WM_CLASS quirks exactly as before "
         "(identity-only: no continuation/GAAV changes)", [PROFILE_X11]),
    _row("C3", "C", "provider=\"atspi\" on X11",
         "role/selection/preceding available in GTK apps; A3/A4/A6 pass "
         "on X11 too", [PROFILE_X11]),
    # Matrix D — session axes: hotkey × insertion × lifecycle (docs §Matrix D)
    _row("D1", "D", "Baseline: daemon alive under this session",
         "sayit-ermano status answers (daemon alive in this session; "
         "no-tool session surfaces the honest notification)", _WL,
         kind="auto", auto="status"),
    _row("D2", "D", "Hotkey axis", {
        PROFILE_X11: "XGrabKey global grab works: hotkey toggles dictation",
        PROFILE_GNOME_WL: "GNOME 48+: GlobalShortcuts portal available OR "
                          "Settings custom shortcut bound to the toggle "
                          "script toggles dictation (product path: DE "
                          "shortcut + `--toggle`)",
        PROFILE_KDE_WL: "Plasma 6.7.4+: custom shortcut (or GlobalShortcuts) "
                        "toggles dictation",
        PROFILE_SWAY: "no GlobalShortcuts backend (xdg-desktop-portal-wlr"
                      "#240): compositor `bindsym` to the toggle script "
                      "(`--toggle`) IS the primary path — verify it fires",
        PROFILE_HYPRLAND: "GlobalShortcuts portal exists; compositor `bind` "
                          "to the toggle script — verify it fires",
        "*": "bind a compositor custom shortcut to the toggle script — "
             "verify it fires",
    }, ALL_PROFILES),
    _row("D3", "D", "Insertion axis", {
        PROFILE_X11: "xdotool typed insertion + verified paste (xclip), "
                     "clipboard restored",
        PROFILE_GNOME_WL: "ydotool (uinput) typed insertion — wtype/"
                          "zwp_virtual_keyboard does NOT work on GNOME "
                          "(mutter#1974); paste via wl-clipboard + Ctrl+V",
        PROFILE_SWAY: "wtype (zwp_virtual_keyboard) typed insertion — the "
                      "expected wlroots path; paste via wl-clipboard + "
                      "Ctrl+V",
        PROFILE_KDE_WL: "wtype or ydotool; Klipper snapshots paste text "
                        "(KDE bug 512996 for wtype) — note it",
        PROFILE_HYPRLAND: "wtype (zwp_virtual_keyboard) or ydotool; paste "
                          "via wl-clipboard + Ctrl+V",
        "*": "wtype/ydotool typed insertion; paste via wl-clipboard + "
             "Ctrl+V",
    }, ALL_PROFILES),
    _row("D4", "D", "Overlay/preview",
         "recording shows the notification preview; no X11-pill attempt "
         "noise in the daemon log", _WL),
    _row("D5", "D", "evdev push-to-talk (needs input-group access)",
         "hold-to-talk works; note the matched device name (skip if no "
         "/dev/input access)", _WL),
    _row("D6", "D", "Lifecycle: sleep/wake",
         "after suspend+resume, hotkey and insertion still work; daemon "
         "needed no manual restart (or autorecovers)", ALL_PROFILES),
    _row("D7", "D", "Lifecycle: lock/unlock",
         "after screen lock+unlock, hotkey and insertion still work; "
         "recording was paused while locked (no take across lock)",
         ALL_PROFILES),
    _row("D8", "D", "Lifecycle: autostart",
         "daemon autostarts at login (systemd user unit enabled) and is "
         "alive in THIS session", ALL_PROFILES),
    _row("D9", "D", "Non-US keyboard layout",
         "dictation with a non-US layout active lands correct text; "
         "keysym layout regressions (the KDE keysym class) noted",
         _WL),
]
ROWS = {r.id: r for r in _ROWS}
assert len(ROWS) == len(_ROWS), "duplicate row id in the registry"


# ---------------------------------------------------------------------------
# Session -> profile -> rows
# ---------------------------------------------------------------------------

def profile_for(info: session_mod.SessionInfo) -> str | None:
    """Map a session probe onto a matrix profile; None when no display
    session is detectable (live runs refuse; dry-run lists all)."""
    if info.type == "unknown":
        return None
    if not info.is_wayland:
        return PROFILE_X11  # explicit x11 (or DISPLAY-only) = X11 control pass
    if session_mod.is_gnome_desktop(info.desktop_all):
        return PROFILE_GNOME_WL
    tokens = {t for t in (info.desktop_all or "")
              .replace(";", ":").split(":") if t}
    if "sway" in tokens:
        return PROFILE_SWAY
    if tokens & {"kde", "plasma"}:
        return PROFILE_KDE_WL
    if "hyprland" in tokens:
        return PROFILE_HYPRLAND
    return PROFILE_WAYLAND


def rows_for(profile: str) -> list[Row]:
    """The applicable rows for one profile, in matrix order (A, B, C, D)."""
    return [r for r in _ROWS if profile in r.profiles]


def parse_row_filter(spec: str) -> list[str]:
    """Expand `A1,A3-A5,D2` into registry ids; raises ValueError on junk."""
    ids: list[str] = []
    for part in spec.split(","):
        part = part.strip().upper()
        if not part:
            continue
        m = re.fullmatch(r"([A-D])(\d+)-([A-D]?)(\d*)", part)
        if m and (not m.group(3) or m.group(3) == m.group(1)):
            lo, hi = int(m.group(2)), int(m.group(4) or m.group(2))
            if hi < lo:
                raise ValueError(f"bad range {part!r}: hi < lo")
            ids += [f"{m.group(1)}{n}" for n in range(lo, hi + 1)]
        elif part in ROWS:
            ids.append(part)
        else:
            raise ValueError(f"unknown row id {part!r}")
    seen, out = set(), []
    for i in ids:  # dedupe, keep order
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


# ---------------------------------------------------------------------------
# Automated checks (runner + history path injectable for tests)
# ---------------------------------------------------------------------------

@dataclass
class Ctx:
    profile: str
    info: session_mod.SessionInfo
    runner: Callable[[list[str], float], subprocess.CompletedProcess]
    history_file: Path

    def run(self, argv: list[str], timeout: float
            ) -> subprocess.CompletedProcess:
        return self.runner(argv, timeout)


def _default_runner(argv: list[str], timeout: float
                    ) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=str(REPO_ROOT), capture_output=True,
                          text=True, timeout=timeout)


def check_doctor(ctx: Ctx) -> tuple[str, str]:
    """A1: `sayit-ermano doctor` exits 0 and names the session/provider."""
    proc = ctx.run([sys.executable, "-m", "fluidvoice", "doctor"],
                   DOCTOR_TIMEOUT_S)
    out = (proc.stdout or "") + (proc.stderr or "")
    lines = [ln.strip() for ln in out.splitlines()
             if ln.strip().startswith(("session:", "insertion:", "context"))]
    if proc.returncode == 0:
        return "pass", "; ".join(lines[:4]) or "doctor exit 0"
    return "fail", (f"doctor exit {proc.returncode}: "
                    + ("; ".join(lines[:4]) or out.strip()[-300:]))


def check_status(ctx: Ctx) -> tuple[str, str]:
    """D1: the daemon answers `status` over its control socket."""
    try:
        proc = ctx.run([sys.executable, "-m", "fluidvoice", "status",
                        "--json"], STATUS_TIMEOUT_S)
    except Exception as e:  # unreachable socket, launch failure
        return "fail", f"status failed: {e}"
    if proc.returncode == 0:
        return "pass", "daemon status answered: " + (proc.stdout or "")[:120]
    return "fail", (f"status exit {proc.returncode}: "
                    + ((proc.stdout or "") + (proc.stderr or ""))[:200])


def check_privacy(ctx: Ctx) -> tuple[str, str]:
    """A13/B4: history rows carry no context-derived text (preceding/
    selection) and no AT-SPI ids beyond the usual take-start field."""
    path = ctx.history_file
    if not path.exists():
        return "fail", (f"{path} not found — run the manual dictation "
                        "rows first, then re-run this row")
    bad: list[str] = []
    rows = 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return "fail", f"cannot read {path}: {e}"
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        rows += 1
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            bad.append(f"line {n}: not JSON")
            continue
        if not isinstance(entry, dict):
            continue
        for key, val in entry.items():
            k = key.lower()
            if (("preceding" in k or "selection" in k
                 or k.startswith(("atspi", "a11y"))) and val):
                bad.append(f"line {n}: {key}={str(val)[:60]!r}")
    if bad:
        return "fail", f"context leakage in history: {'; '.join(bad[:5])}"
    return "pass", (f"{rows} history rows clean "
                    "(no preceding/selection/atspi text)")


CHECKS: dict[str, Callable[[Ctx], tuple[str, str]]] = {
    "doctor": check_doctor,
    "status": check_status,
    "privacy": check_privacy,
}


# ---------------------------------------------------------------------------
# Environment snapshot (best-effort; recorded, never gating)
# ---------------------------------------------------------------------------

def git_sha() -> str:
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse",
                              "HEAD"], capture_output=True, text=True,
                             timeout=15)
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def probe_versions(profile: str,
                   runner: Callable[[list[str], float],
                                    subprocess.CompletedProcess] | None = None
                   ) -> dict[str, str]:
    versions = {}
    for name, argv in VERSION_PROBES.get(profile, {}).items():
        try:
            proc = (runner or _default_runner)(argv, VERSION_TIMEOUT_S)
            first = next((ln.strip() for ln
                          in (proc.stdout or "").splitlines() if ln.strip()),
                         "")
            versions[name] = first[:80] if proc.returncode == 0 and first \
                else "unknown"
        except Exception:  # noqa: S110 — version probes are best-effort only
            versions[name] = "unknown"
    return versions


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

@dataclass
class Result:
    row: Row
    status: str          # "pass" | "fail" | "skip"
    evidence: str = ""
    note: str = ""

    def to_json(self, profile: str) -> dict:
        return {"id": self.row.id, "matrix": self.row.matrix,
                "case": self.row.case, "expected": self.row.expected_for(
                    profile),
                "kind": self.row.kind, "status": self.status,
                "evidence": self.evidence, "note": self.note}


def _md_cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")[:200]


def write_evidence(out_dir: Path, payload: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    md = [f"# Wayland smoke matrix — {payload['date']} — {payload['profile']}",
          "",
          f"- Session: `{payload['session']['type']}` "
          f"(desktop `{payload['session'].get('desktop', '')}`)"
          f" — commit `{payload['commit']}` — host `{payload['host']}`",
          "- Env: " + ", ".join(f"{k}={v!r}" for k, v
                                 in payload["env"].items()),
          "- Versions: " + (", ".join(f"{k} {v}" for k, v
                                       in payload["versions"].items())
                             or "n/a"),
          ""]
    results = payload["rows"]
    md.append("| # | Case | Expected | Result | Evidence |")
    md.append("|---|------|----------|--------|----------|")
    for r in results:
        md.append(f"| {r['id']} | {_md_cell(r['case'])} "
                  f"| {_md_cell(r['expected'])} "
                  f"| **{r['status'].upper()}** "
                  f"| {_md_cell(r['evidence'] or r['note'])} |")
    s = payload["summary"]
    verdict = "RED" if s["fail"] else "GREEN"
    md += ["",
           f"**Verdict: {verdict}** — {s['pass']} pass, {s['fail']} fail, "
           f"{s['skip']} skip (harness exit {'1' if s['fail'] else '0'})",
           ""]
    (out_dir / "results.md").write_text("\n".join(md), encoding="utf-8")


def next_run_dir(out_root: Path, date: str, profile: str) -> Path:
    """First free dir; never clobber a previous same-day run's evidence."""
    base = out_root / f"{date}-{profile}"
    n, cand = 1, base
    while cand.exists():
        n += 1
        cand = out_root / f"{date}-{profile}-{n}"
    return cand


# ---------------------------------------------------------------------------
# Manual-row answers
# ---------------------------------------------------------------------------

VALID_ANSWERS = ("pass", "fail", "skip")


def parse_answers(spec: str) -> dict[str, tuple[str, str]]:
    """`A2=pass,A3=fail:wrong-case` -> {id: (result, comment)}."""
    out: dict[str, tuple[str, str]] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"bad answer {part!r} (want ID=pass|fail|skip"
                             "[:comment])")
        rid, rest = part.split("=", 1)
        rid = rid.strip().upper()
        result, _, comment = rest.strip().partition(":")
        result = result.strip().lower()
        if rid not in ROWS or result not in VALID_ANSWERS:
            raise ValueError(f"bad answer {part!r}")
        out[rid] = (result, comment.strip())
    return out


def ask_manual(row: Row, expected: str, answers: dict, tty: bool,
               prompt=input) -> tuple[str, str, str]:
    """Resolve a manual row: scripted answer > interactive > skip."""
    if row.id in answers:
        result, comment = answers[row.id]
        return result, comment, "answered via --answers"
    if not tty:
        return "skip", "", "no answer (non-interactive stdin)"
    while True:
        reply = prompt(f"[{row.id}] {row.case}\n  expect: {expected}\n"
                       "  pass/fail/skip [+ comment]: ").strip()
        word, _, comment = reply.partition(" ")
        word = word.lower().rstrip(":")
        if word in VALID_ANSWERS:
            return word, comment.strip(), "interactive"
        print("  answer 'pass', 'fail', 'skip' (optionally + comment)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wayland_smoke.py", description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan for the detected session and exit "
                        "(no session needed, nothing executed or written)")
    p.add_argument("--rows", metavar="IDS",
                   help="run only these rows (comma list / ranges, e.g. "
                        "A1,A3-A5,D2)")
    p.add_argument("--answers", metavar="SPEC",
                   help="pre-scripted manual-row answers, e.g. "
                        "A2=pass,A3=fail:wrong-case (else interactive "
                        "prompt; non-tty stdin records skips)")
    p.add_argument("--out-root", type=Path,
                   default=REPO_ROOT / "docs" / "quality" / "wayland",
                   help="evidence root (default docs/quality/wayland)")
    return p


def _plan_lines(profile: str, rows: list[Row]) -> list[str]:
    lines = [f"profile: {profile} — {len(rows)} rows applicable"]
    for r in rows:
        lines.append(f"  {r.id:<4} [{r.kind:<6}] {r.case}")
        lines.append(f"        expect: {r.expected_for(profile)}")
        lines.append(f"        rerun:  {RUN_CMD} --rows {r.id}")
    return lines


def main(argv: list[str] | None = None, *, runner=None,
         history_file: Path | None = None,
         now: Callable[[], _dt.date] = _dt.date.today,
         prompt=input) -> int:
    args = _build_parser().parse_args(argv)
    try:
        answers = parse_answers(args.answers) if args.answers else {}
        wanted = parse_row_filter(args.rows) if args.rows else None
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    info = session_mod.probe()
    profile = profile_for(info)

    if args.dry_run:
        if profile is None:
            print("no display session detected "
                  "(XDG_SESSION_TYPE/DISPLAY/WAYLAND_DISPLAY all unset) — "
                  "plan per profile:")
            for prof in ALL_PROFILES:
                print("\n".join(_plan_lines(prof, rows_for(prof))))
        else:
            print(f"session: {info.type} (desktop={info.desktop or '?'})")
            print("\n".join(_plan_lines(profile, rows_for(profile))))
        print("dry-run: nothing executed, no evidence written")
        return 0

    if profile is None:
        print("error: no display session detected — log into the session "
              "under test first (this runner executes live rows); use "
              "--dry-run for the plan", file=sys.stderr)
        return 2

    rows = rows_for(profile)
    if wanted is not None:
        applicable = {r.id for r in rows}
        for i in wanted:
            if i not in applicable:
                print(f"note: {i} is not applicable to profile {profile} "
                      "— not run")
        rows = [ROWS[i] for i in wanted if i in applicable]
        if not rows:
            print("error: no applicable rows selected", file=sys.stderr)
            return 2

    ctx = Ctx(profile, info, runner or _default_runner,
              history_file or _history_default())
    tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
    results: list[Result] = []
    print(f"session: {info.type} (desktop={info.desktop or '?'}) "
          f"-> profile {profile}: {len(rows)} rows")
    for row in rows:
        expected = row.expected_for(profile)
        if row.kind == "auto":
            status, evidence = CHECKS[row.auto](ctx)
            note = ""
            print(f"[{row.id}] {row.case}: {status.upper()} — {evidence}")
        else:
            status, evidence, note = ask_manual(row, expected, answers, tty,
                                                prompt)
            print(f"[{row.id}] {row.case}: {status.upper()} "
                  + (f"— {evidence}" if evidence else ""))
        results.append(Result(row, status, evidence, note))

    summary = {"pass": sum(1 for r in results if r.status == "pass"),
               "fail": sum(1 for r in results if r.status == "fail"),
               "skip": sum(1 for r in results if r.status == "skip")}
    out_dir = next_run_dir(args.out_root, today := now().isoformat(),
                           profile)
    payload = {
        "schema": SCHEMA,
        "date": today,
        "profile": profile,
        "session": {"type": info.type, "desktop": info.desktop,
                    "desktop_all": info.desktop_all,
                    "wayland_display": info.wayland_display,
                    "x11_display": info.x11_display},
        "commit": git_sha(),
        "host": os.uname().nodename,
        "env": {k: os.environ.get(k, "") for k in ENV_SNAPSHOT_KEYS},
        "versions": probe_versions(profile, ctx.runner),
        "rows": [r.to_json(profile) for r in results],
        "summary": summary,
    }
    write_evidence(out_dir, payload)
    print(f"evidence: {out_dir}/ (results.json + results.md)")
    print(f"summary: {summary['pass']} pass, {summary['fail']} fail, "
          f"{summary['skip']} skip")
    return 1 if summary["fail"] else 0


def _history_default() -> Path:
    from fluidvoice import paths
    return paths.history_file()


if __name__ == "__main__":
    sys.exit(main())
