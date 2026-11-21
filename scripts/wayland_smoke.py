#!/usr/bin/env python3
"""Live Wayland/X11 smoke-matrix harness (issue #13).

The Wayland parity gate (docs/dev/wayland-smoke-matrix.md,
REQUIRED-BEFORE-PARITY) needs real logins a CI runner does not have.
Division of labor (the 2026-11-21 reclassification of #13):

* agent/CI — this harness, its offline tests (tests/test_wayland_smoke.py:
  faked session env vars and command runners; no display, no daemon, no
  network), and the generated row/invoke table in
  docs/dev/wayland-smoke-matrix.md (`--print-rows`).
* owner — enable a real Wayland login (GDM `WaylandEnable` or a sway VM),
  log into GNOME Wayland first, then sway, plus the X11 control session,
  run this script ONCE PER SESSION, and commit the evidence tree under
  docs/quality/wayland/.

Per session it: detects the session from the environment (same
precedence as fluidvoice/session.py), selects the applicable matrix rows
(the Session x Hotkey x Insertion axes, the lifecycle rows, and the
doc's context matrices A/B/C), executes the automated checks (daemon
status, doctor, autostart enabled, insertion-tool preflights), records
MANUAL rows with their exact invoke line and GAP rows (backend not
implemented in this build — never silently dropped), and writes evidence
(JSON + markdown) under docs/quality/wayland/<date>-<session>/.
Exit code 1 iff an executed row failed (a red row); skips and gaps carry
reasons, they are not hidden.

Usage:
  wayland_smoke.py --dry-run [--session KEY]    print the plan, run nothing
  wayland_smoke.py [--session KEY] [--out DIR] [--row ID ...]
                                                run + record evidence
  wayland_smoke.py --print-rows                 print the doc row table
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping

OUT_DEFAULT = Path("docs/quality/wayland")
REPO_URL = "https://github.com/acailic/SayItErmano/issues/13"
DOC_URL = "https://github.com/acailic/SayItErmano/blob/linux/docs/dev/wayland-smoke-matrix.md"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
MANUAL, GAP = "MANUAL", "GAP"
GROUPS = ("baseline", "hotkey", "insertion", "lifecycle", "context",
          "surface", "regression")

SESSION_KEYS = ("x11", "gnome-wayland", "kde-wayland", "sway", "hyprland",
                "wayland")
WAYLAND_KEYS = ("gnome-wayland", "kde-wayland", "sway", "hyprland")
PREFIX = {"x11": "x11", "gnome-wayland": "gnome", "kde-wayland": "kde",
          "sway": "sway", "hyprland": "hyprland", "wayland": "wayland"}
SESSION_LABELS = {
    "x11": "X11 (control)",
    "gnome-wayland": "GNOME Wayland (portal hotkeys need GNOME 48+)",
    "kde-wayland": "KDE Plasma Wayland 6.x (6.7.4 shortcut-description"
                   " changes)",
    "sway": "sway (wlroots)",
    "hyprland": "Hyprland (wlroots)",
    "wayland": "Wayland, unidentified compositor",
}
TOOLS = ("sayit-ermano", "systemctl", "xdotool", "xclip", "wtype",
         "ydotool", "wl-copy", "wl-paste")

# Desktop tokens that mean GNOME Shell regardless of distro branding
# (mirrors fluidvoice/session.py: "ubuntu:GNOME" reports first token
# "ubuntu", Pop!_OS "pop:GNOME", Unity-era "unity").
GNOME_DESKTOPS = ("gnome", "unity", "ubuntu", "pop")


# ---------------------------------------------------------------------------
# session detection (pure env reading — injectable for tests)
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Session:
    key: str | None        # matrix session id, None when undetectable
    stype: str             # x11 | wayland | unknown (probe type)
    desktop: str           # lower-cased first token of XDG_CURRENT_DESKTOP
    desktop_all: str       # full lower-cased XDG_CURRENT_DESKTOP
    forced: bool = False   # set when --session overrode detection

    @property
    def label(self) -> str:
        return SESSION_LABELS[self.key or "wayland"]


def detect_session(env: Mapping[str, str] | None = None) -> Session:
    """Resolve the matrix session from env (defaults to os.environ).

    Same precedence as fluidvoice/session.py: explicit
    XDG_SESSION_TYPE=wayland/x11 wins over display vars; then
    WAYLAND_DISPLAY; then DISPLAY; then unknown. The compositor comes
    from XDG_CURRENT_DESKTOP tokens (wayland sessions only — an x11
    login is the control row whatever the desktop).
    """
    e = dict(os.environ if env is None else env)
    wayland_display = bool((e.get("WAYLAND_DISPLAY") or "").strip())
    x11_display = bool((e.get("DISPLAY") or "").strip())
    explicit = (e.get("XDG_SESSION_TYPE") or "").strip().lower()
    if explicit == "wayland":
        stype = "wayland"
    elif explicit == "x11":
        stype = "x11"
    elif wayland_display:
        stype = "wayland"
    elif x11_display:
        stype = "x11"
    else:
        stype = "unknown"
    desktop_all = (e.get("XDG_CURRENT_DESKTOP") or "").strip().lower()
    tokens = [t for t in re.split(r"[:;]", desktop_all) if t]
    key: str | None
    if stype == "x11":
        key = "x11"
    elif stype != "wayland":
        key = None
    elif any("gnome" in t or t in GNOME_DESKTOPS for t in tokens):
        key = "gnome-wayland"
    elif any(t in ("kde", "plasma") for t in tokens):
        key = "kde-wayland"
    elif "sway" in tokens:
        key = "sway"
    elif "hyprland" in tokens:
        key = "hyprland"
    else:
        key = "wayland"
    return Session(key=key, stype=stype,
                   desktop=desktop_all.split(":")[0].strip(),
                   desktop_all=desktop_all)


def probe_tools(names: tuple[str, ...] = TOOLS,
                which: Callable[[str], str | None] | None = None
                ) -> dict[str, bool]:
    if which is None:
        which = shutil.which
    return {name: which(name) is not None for name in names}


# ---------------------------------------------------------------------------
# the row registry (Session x Hotkey x Insertion + lifecycle + context)
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Row:
    id: str
    group: str
    case: str
    expected: str
    sessions: tuple[str, ...]
    kind: str                 # auto | manual | gap
    argv: tuple[str, ...] = ()   # auto rows: the command to run
    tools: tuple[str, ...] = ()  # auto rows: binaries that must exist
    on_missing: str = "skip"     # auto rows: skip | fail when tools missing
    invoke: str = ""             # exact line/steps a human runs
    note: str = ""               # gap reasons, version pins, install hints


BIND_INVOKE = {
    "gnome-wayland":
        "GNOME: Settings -> Keyboard -> View and Customize Shortcuts -> "
        "Custom Shortcuts -> Add, Command = sayit-ermano-toggle, bind a "
        "key; press it -> dictation toggles",
    "kde-wayland":
        "Plasma: System Settings -> Shortcuts -> Add New -> Command or "
        "Script... sayit-ermano-toggle, assign a key; press it -> "
        "dictation toggles",
    "sway":
        "sway config: bindsym $mod+d exec sayit-ermano-toggle, then "
        "swaymsg reload; press it -> dictation toggles",
    "hyprland":
        "hyprland.conf: bind = SUPER, D, exec, sayit-ermano-toggle; "
        "press it -> dictation toggles",
    "wayland":
        "bind sayit-ermano-toggle as a custom compositor shortcut; "
        "press it -> dictation toggles",
}

PORTAL_NOTE = {
    "gnome-wayland":
        "GlobalShortcuts portal hotkey backend not implemented in this "
        "build; needs GNOME 48+ and xdg-desktop-portal >= 1.21 (older "
        "portals differ in the consent flow) — compositor-bind --toggle "
        "is the primary path today",
    "kde-wayland":
        "GlobalShortcuts portal hotkey backend not implemented in this "
        "build; Plasma 6.7.4+ changed shortcut descriptions — re-pin "
        "when implementing — compositor-bind --toggle is the primary "
        "path today",
    "sway":
        "no GlobalShortcuts backend exists in xdg-desktop-portal-wlr "
        "(issue #240) — the compositor-bind + --toggle path IS the "
        "primary hotkey path on sway",
    "hyprland":
        "Hyprland implements the GlobalShortcuts portal; a portal "
        "hotkey backend is not implemented in this build — "
        "compositor-bind --toggle is the primary path today",
    "wayland":
        "GlobalShortcuts portal support varies by compositor and is "
        "not implemented in this build",
}

WTYPE_GAP = {
    "gnome-wayland":
        "wtype (zwp_virtual_keyboard) cannot work on GNOME Shell: "
        "mutter does not implement the protocol (mutter#1974) — "
        "ydotool (uinput) is the GNOME insertion path",
    "kde-wayland":
        "wtype (zwp_virtual_keyboard) cannot work on Plasma: KWin does "
        "not implement the protocol (KDE bug 512996) — ydotool "
        "(uinput) is the Plasma insertion path",
}

EIS_GAP = ("EIS keysym insertion (libei-class protocol) is not "
           "implemented in this build — recorded as a known gap on "
           "every session")

TERMINAL = {"x11": "gnome-terminal", "gnome-wayland": "gnome-terminal",
            "kde-wayland": "konsole", "sway": "foot", "hyprland": "kitty",
            "wayland": "a terminal"}

CONTEXT_MATRIX = {
    "x11": ("C1-C3", "Matrix C (X11 control)"),
    "gnome-wayland": ("A1-A15", "Matrix A (GNOME Wayland context)"),
    "sway": ("B1-B4 + the A2-A6 equivalents", "Matrix B (sway context)"),
}


def _baseline_rows(skey: str) -> list[Row]:
    p = PREFIX[skey]
    return [
        Row(
            id=f"{p}-baseline-daemon-status", group="baseline",
            case="daemon alive under the session",
            expected="daemon answers status and reports this session's "
                     "type + resolved capabilities",
            sessions=(skey,), kind="auto",
            argv=("sayit-ermano", "status"),
            tools=("sayit-ermano",), on_missing="skip",
            note="run from the session with the app installed "
                 "(pipx/deb) or the venv bin on PATH"),
        Row(
            id=f"{p}-baseline-doctor", group="baseline",
            case="doctor on the live session",
            expected="exit 0 — session matrix correct, insertion "
                     "resolved, per-tool found/missing rows honest",
            sessions=(skey,), kind="auto",
            argv=("sayit-ermano", "doctor"),
            tools=("sayit-ermano",), on_missing="skip"),
        Row(
            id=f"{p}-baseline-first-dictation", group="baseline",
            case="first dictation lands",
            expected="a spoken take transcribes, lands in history and "
                     "inserts into the focused app",
            sessions=(skey,), kind="manual",
            invoke="focus an editor, press the session hotkey (or "
                   "`sayit-ermano toggle`), dictate ~10 words, stop; "
                   "polished text lands at the caret and the take "
                   "shows in `sayit-ermano history`"),
    ]


def _hotkey_rows(skey: str) -> list[Row]:
    p = PREFIX[skey]
    rows: list[Row] = []
    if skey == "x11":
        rows.append(Row(
            id="x11-hotkey-xgrabkey", group="hotkey",
            case="XGrabKey global grab",
            expected="press -> recording; release -> polished text "
                     "inserted at the caret",
            sessions=(skey,), kind="manual",
            invoke="focus an editor, press/release the configured "
                   "hotkey (default Right Ctrl)"))
    else:
        rows.append(Row(
            id=f"{p}-hotkey-compositor-bind", group="hotkey",
            case="compositor-bind --toggle",
            expected="the bound key toggles dictation (primary hotkey "
                     "path on Wayland in this build)",
            sessions=(skey,), kind="manual",
            invoke=BIND_INVOKE[skey]))
        rows.append(Row(
            id=f"{p}-hotkey-portal", group="hotkey",
            case="GlobalShortcuts portal",
            expected="portal-activated hotkey works without a manual "
                     "DE bind",
            sessions=(skey,), kind="gap", note=PORTAL_NOTE[skey]))
        rows.append(Row(
            id=f"{p}-hotkey-evdev-ptt", group="hotkey",
            case="evdev push-to-talk (optional)",
            expected="hold-to-talk via /dev/input works when "
                     "hotkey.wayland_evdev=true and the user is in the "
                     "input group",
            sessions=(skey,), kind="manual",
            invoke="set hotkey.wayland_evdev=true, restart the daemon, "
                   "hold the key (default KEY_RIGHTCTRL); note the "
                   "matched device name in the log"))
    return rows


def _insertion_rows(skey: str) -> list[Row]:
    p = PREFIX[skey]
    rows: list[Row] = []
    if skey == "x11":
        rows.append(Row(
            id="x11-insert-xdotool-tools", group="insertion",
            case="xdotool typed path (preflight)",
            expected="xdotool installed for `xdotool type` insertion",
            sessions=(skey,), kind="auto",
            tools=("xdotool",), on_missing="fail",
            note="install xdotool"))
        rows.append(Row(
            id="x11-insert-xclip-tools", group="insertion",
            case="xclip clipboard path (preflight)",
            expected="xclip installed for the Ctrl+V paste path and "
                     "the clipboard snapshot/restore",
            sessions=(skey,), kind="auto",
            tools=("xclip",), on_missing="fail",
            note="install xclip"))
    else:
        if skey in ("sway", "hyprland", "wayland"):
            rows.append(Row(
                id=f"{p}-insert-wtype-tools", group="insertion",
                case="wtype / zwp_virtual_keyboard (preflight)",
                expected="wtype installed — the keysym typing path on "
                         "wlroots compositors",
                sessions=(skey,), kind="auto",
                tools=("wtype",),
                on_missing="fail" if skey != "wayland" else "skip",
                note="install wtype" if skey != "wayland" else
                     "wtype is wlroots-only and the compositor is "
                     "unidentified here — verify before relying on it"))
        if skey in ("gnome-wayland", "kde-wayland", "wayland"):
            rows.append(Row(
                id=f"{p}-insert-ydotool-tools", group="insertion",
                case="ydotool / uinput (preflight)",
                expected="ydotool + ydotoold available — the uinput "
                         "typing path",
                sessions=(skey,), kind="auto",
                tools=("ydotool",),
                on_missing="fail" if skey != "wayland" else "skip",
                note="install ydotool and enable ydotoold (uinput "
                     "permissions)" if skey != "wayland" else
                     "uinput fallback; install ydotool + ydotoold if "
                     "it is this session's path"))
        else:
            rows.append(Row(
                id=f"{p}-insert-ydotool-tools", group="insertion",
                case="ydotool / uinput (alternate preflight)",
                expected="ydotool available behind wtype",
                sessions=(skey,), kind="auto",
                tools=("ydotool",), on_missing="skip",
                note="alternate path — only needed when the wtype path "
                     "is unavailable"))
        rows.append(Row(
            id=f"{p}-insert-wl-clipboard-tools", group="insertion",
            case="clipboard + Ctrl+V path (preflight)",
            expected="wl-clipboard installed for the paste path and "
                     "the clipboard snapshot/restore",
            sessions=(skey,), kind="auto",
            tools=("wl-copy", "wl-paste"), on_missing="fail",
            note="install wl-clipboard"))
        if skey in WTYPE_GAP:
            rows.append(Row(
                id=f"{p}-insert-wtype", group="insertion",
                case="wtype / zwp_virtual_keyboard",
                expected="keysym insertion via the wlroots protocol",
                sessions=(skey,), kind="gap", note=WTYPE_GAP[skey]))
        rows.append(Row(
            id=f"{p}-insert-eis", group="insertion",
            case="EIS keysym insertion",
            expected="keysym insertion through the libinput EIS/libei "
                     "path",
            sessions=(skey,), kind="gap", note=EIS_GAP))
    rows.append(Row(
        id=f"{p}-insert-typed-live", group="insertion",
        case="typed insertion into an editor (live)",
        expected="dictated text lands once, at the caret, correct "
                 "capitalization/spacing",
        sessions=(skey,), kind="manual",
        invoke="dictate into a plain editor field (gedit/foot); text "
               "arrives at the caret and nowhere else"))
    rows.append(Row(
        id=f"{p}-insert-paste-live", group="insertion",
        case="paste path + clipboard restore (live)",
        expected="long text (>=300 chars) or leading-dash text pastes "
                 "in full, once; the pre-paste clipboard is restored",
        sessions=(skey,), kind="manual",
        invoke="copy something to the clipboard first, then dictate "
               ">=300 chars; verify the full text lands once and the "
               "clipboard is restored afterwards"))
    rows.append(Row(
        id=f"{p}-insert-terminal-live", group="insertion",
        case=f"terminal quirks in {TERMINAL[skey]} (live)",
        expected="trailing autocomplete space, Ctrl+Shift+V paste, "
                 "spoken-send Enter suppression behave as documented",
        sessions=(skey,), kind="manual",
        invoke=f"dictate into {TERMINAL[skey]}: a git-style phrase "
               "(trailing space), a long paste, and a spoken-send "
               "phrase; verify Enter is suppressed only in the "
               "terminal"))
    rows.append(Row(
        id=f"{p}-insert-non-us-layout", group="insertion",
        case="non-US keyboard layout",
        expected="inserted keysyms match the active layout (the "
                 "Plasma keysym-layout regression class)",
        sessions=(skey,), kind="manual",
        invoke="switch the session to a non-US layout (e.g. de), "
               "dictate symbols/punctuation-heavy text, verify the "
               "inserted characters match the layout"))
    return rows


def _lifecycle_rows(skey: str) -> list[Row]:
    p = PREFIX[skey]
    return [
        Row(
            id=f"{p}-lifecycle-sleep-wake", group="lifecycle",
            case="sleep/wake",
            expected="hotkey and insertion still work after wake; no "
                     "daemon restart needed",
            sessions=(skey,), kind="manual",
            invoke="run `systemctl suspend`, wake the machine, then "
                   "verify the hotkey still toggles and one dictation "
                   "inserts; re-run this harness on the same session"),
        Row(
            id=f"{p}-lifecycle-lock-unlock", group="lifecycle",
            case="lock/unlock",
            expected="hotkey paused while locked, working after unlock",
            sessions=(skey,), kind="manual",
            invoke="lock the session (super/L, swaylock, gdm), verify "
                   "the hotkey does NOT trigger while locked, unlock, "
                   "verify it works again"),
        Row(
            id=f"{p}-lifecycle-autostart", group="lifecycle",
            case="autostart enabled",
            expected="the systemd user unit is enabled so the daemon "
                     "(and hotkey) survive re-login",
            sessions=(skey,), kind="auto",
            argv=("systemctl", "--user", "is-enabled", "sayit-ermano"),
            tools=("systemctl",), on_missing="skip"),
    ]


def _context_rows(skey: str) -> list[Row]:
    if skey not in CONTEXT_MATRIX:
        return []
    rows_, label = CONTEXT_MATRIX[skey]
    return [Row(
        id=f"{PREFIX[skey]}-context-matrix", group="context",
        case=f"context/AT-SPI matrix ({label})",
        expected="every row of the doc matrix passes (degrade rows "
                 "degrade silently)",
        sessions=(skey,), kind="manual",
        invoke=f"with [context] enabled=true and provider=\"auto\" in "
               f"the config (restore afterwards!), work through "
               f"{rows_} of docs/dev/wayland-smoke-matrix.md and tick "
               f"them there")]


def _surface_rows(skey: str) -> list[Row]:
    if skey == "x11":
        return []
    p = PREFIX[skey]
    return [
        Row(
            id=f"{p}-surface-overlay-preview", group="surface",
            case="overlay = notification preview",
            expected="a take shows the notification preview; no "
                     "X11-pill attempt noise in the daemon log",
            sessions=(skey,), kind="manual",
            invoke="start a take and watch the notification preview; "
                   "grep the daemon log for x11-pill attempt noise "
                   "(there must be none)"),
        Row(
            id=f"{p}-surface-settings-script", group="surface",
            case="Settings -> Wayland shortcut assist",
            expected="the page renders and Copy yields a working "
                     "sayit-ermano-toggle script",
            sessions=(skey,), kind="manual",
            invoke="open Settings -> Wayland, press Copy, inspect the "
                   "script, bind it and toggle dictation via the "
                   "shortcut"),
    ]


def _regression_rows(skey: str) -> list[Row]:
    if skey != "x11":
        return []
    return [Row(
        id="x11-regression-zero-delta", group="regression",
        case="X11 regression pass on the same build",
        expected="full manual X11 pass — zero deltas vs pre-port "
                 "behavior (Matrix C)",
        sessions=(skey,), kind="manual",
        invoke="on the SAME build as the Wayland runs: hotkey grab, "
               "pill preview, verified paste, spoken-send, rewrite "
               "capture — record any delta vs Matrix C")]


def _build_rows() -> dict[str, Row]:
    rows: list[Row] = []
    for skey in SESSION_KEYS:
        rows += (_baseline_rows(skey) + _hotkey_rows(skey)
                 + _insertion_rows(skey) + _lifecycle_rows(skey)
                 + _context_rows(skey) + _surface_rows(skey)
                 + _regression_rows(skey))
    rows.sort(key=lambda r: (GROUPS.index(r.group), r.id))
    return {r.id: r for r in rows}


ROWS: dict[str, Row] = _build_rows()


def select_rows(session_key: str) -> list[Row]:
    return [r for r in ROWS.values() if session_key in r.sessions]


# ---------------------------------------------------------------------------
# execution (runner injectable)
# ---------------------------------------------------------------------------

Runner = Callable[[tuple[str, ...]], tuple[int, str]]


def real_run(argv: tuple[str, ...]) -> tuple[int, str]:
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=120)
    except subprocess.TimeoutExpired:
        return 124, f"timeout: {' '.join(argv)}"
    except OSError as exc:
        return 127, f"cannot execute: {exc}"[:200]
    tail = ((proc.stdout or "") + (proc.stderr or ""))[-200:].strip()
    return proc.returncode, tail


@dataclasses.dataclass
class RowResult:
    row: Row
    status: str
    detail: str = ""

    @property
    def red(self) -> bool:
        return self.status == FAIL


def execute_row(row: Row, tools: dict[str, bool],
                run: Runner) -> RowResult:
    if row.kind == "gap":
        return RowResult(row, GAP, row.note)
    if row.kind == "manual":
        return RowResult(row, MANUAL, row.note)
    missing = [t for t in row.tools if not tools.get(t)]
    if missing:
        what = ", ".join(missing)
        if row.on_missing == "fail":
            return RowResult(row, FAIL, f"required tool(s) missing: "
                              f"{what}"
                              + (f" — {row.note}" if row.note else ""))
        return RowResult(row, SKIP, f"{what} not available here"
                         + (f" — {row.note}" if row.note else ""))
    if not row.argv:
        return RowResult(row, PASS, "present: " + ", ".join(row.tools))
    rc, tail = run(row.argv)
    if rc != 0:
        return RowResult(row, FAIL, f"`{' '.join(row.argv)}` exited {rc}"
                         + (f": {tail}" if tail else ""))
    return RowResult(row, PASS, f"`{' '.join(row.argv)}` ok"
                     + (f": {tail}" if tail else ""))


def run_matrix(session: Session, rows: list[Row], tools: dict[str, bool],
               run: Runner) -> list[RowResult]:
    return [execute_row(r, tools, run) for r in rows]


def summarize(results: list[RowResult]) -> dict:
    counts = {s: 0 for s in (PASS, FAIL, SKIP, MANUAL, GAP)}
    for r in results:
        counts[r.status] += 1
    return {**counts, "red": counts[FAIL] > 0}


# ---------------------------------------------------------------------------
# evidence (docs/quality/wayland/<date>-<session>/)
# ---------------------------------------------------------------------------

def evidence_dir(out: Path, session: Session,
                 now: _dt.datetime) -> Path:
    return out / f"{now.date().isoformat()}-{session.key}"


def repo_sha() -> str:
    root = Path(__file__).resolve().parent.parent
    try:
        proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=str(root), capture_output=True,
                              text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _session_env_lines(env: Mapping[str, str]) -> str:
    keys = ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "WAYLAND_DISPLAY",
            "DISPLAY")
    return " ".join(f"{k}={env.get(k) or '-'}" for k in keys)


def _report_header(session: Session) -> str:
    return f"""# Wayland smoke matrix evidence — {session.key}

Generated by `scripts/wayland_smoke.py` (issue
[#13]({REPO_URL})); row definitions and invoke lines live in
[docs/dev/wayland-smoke-matrix.md]({DOC_URL}). Housekeeping: if the
production daemon runs on this machine, use the documented two-step
(stop the unit, run the repo daemon, restart the unit afterwards) and
restore every config key you flip. This evidence directory covers one
session: {session.label}.

Legend: PASS/FAIL = executed by the harness (FAIL = red row, needs a
fix or a known-issues entry). SKIP = not applicable here, reason
recorded. MANUAL = owner ticks the box below after doing the invoke
line. GAP = backend not implemented in this build, recorded so the
axis cell is never silently dropped.
"""


def _run_section(session: Session, results: list[RowResult], env: Mapping,
                 tools: dict[str, bool], host: str, sha: str,
                 now: _dt.datetime) -> str:
    when = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    counts = summarize(results)
    lines = [
        f"## Run {when} — {host} — commit {sha or 'unknown'}", "",
        f"Session: `{session.key}` ({session.label}) — probe: "
        + _session_env_lines(env), "",
        "Tools: " + ", ".join(f"{k} {'ok' if v else 'MISSING'}"
                              for k, v in tools.items()), "",
        "| id | group | status | detail |", "|---|---|---|---|",
    ]
    for r in results:
        detail = r.detail.replace("|", "\\|")
        lines.append(f"| {r.row.id} | {r.row.group} | {r.status} "
                     f"| {detail} |")
    lines += ["", "Manual rows (owner ticks, [ ] -> [x]):", ""]
    for r in results:
        if r.status == MANUAL:
            lines.append(f"- [ ] **{r.row.id}** — {r.row.expected}. "
                         f"Invoke: {r.row.invoke}")
    verdict = ("RED — fix every failed row or give it a known-issues "
               "entry" if counts["red"] else
               "GREEN — no red rows in the executed set")
    lines += ["", f"Verdict: {verdict} "
              f"(PASS {counts[PASS]} / FAIL {counts[FAIL]} / "
              f"SKIP {counts[SKIP]} / MANUAL {counts[MANUAL]} / "
              f"GAP {counts[GAP]})", ""]
    return "\n".join(lines)


def record_evidence(out: Path, session: Session,
                    results: list[RowResult], env: Mapping,
                    tools: dict[str, bool], *, host: str = "",
                    sha: str = "", now: _dt.datetime | None = None,
                    ) -> Path:
    """Write results.json (latest run) and append the run section to
    report.md under <out>/<date>-<session>/; returns the directory."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    run_dir = evidence_dir(out, session, now)
    run_dir.mkdir(parents=True, exist_ok=True)
    report = run_dir / "report.md"
    if not report.exists():
        report.write_text(_report_header(session), encoding="utf-8")
    with report.open("a", encoding="utf-8") as fh:
        fh.write("\n" + _run_section(session, results, env, tools, host,
                                     sha, now))
    payload = {
        "ran_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": host,
        "commit": sha,
        "session": {"key": session.key, "type": session.stype,
                    "desktop": session.desktop,
                    "desktop_all": session.desktop_all,
                    "forced": session.forced,
                    "label": session.label},
        "env": {k: (env.get(k) or "") for k in
                ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
                 "WAYLAND_DISPLAY", "DISPLAY")},
        "tools": dict(tools),
        "summary": summarize(results),
        "rows": [{"id": r.row.id, "group": r.row.group,
                  "case": r.row.case, "expected": r.row.expected,
                  "status": r.status, "detail": r.detail,
                  "invoke": r.row.invoke} for r in results],
    }
    (run_dir / "results.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return run_dir


# ---------------------------------------------------------------------------
# the generated doc table (docs/dev/wayland-smoke-matrix.md embeds it)
# ---------------------------------------------------------------------------

ROWS_BEGIN = ("<!-- wayland-smoke rows BEGIN — generated by "
              "scripts/wayland_smoke.py --print-rows; do not edit "
              "between the sentinels -->")
ROWS_END = "<!-- wayland-smoke rows END -->"


def _row_check_line(row: Row) -> str:
    if row.kind == "manual":
        return row.invoke
    if row.kind == "gap":
        return "(gap — recorded, not run) " + row.note
    if row.argv:
        return "`" + " ".join(row.argv) + "`"
    return "present: " + ", ".join(row.tools)


def rows_markdown() -> str:
    lines = []
    for skey in SESSION_KEYS:
        lines += [f"### {skey} — {SESSION_LABELS[skey]}", "",
                  "| row id | kind | exact invoke line / check |",
                  "|---|---|---|"]
        for row in select_rows(skey):
            check = _row_check_line(row).replace("|", "\\|")
            lines.append(f"| `{row.id}` | {row.kind} | {check} |")
        lines.append("")
    return "\n".join(lines)


def print_rows_doc() -> str:
    return f"{ROWS_BEGIN}\n{rows_markdown()}\n{ROWS_END}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _plan_mark(row: Row, tools: dict[str, bool]) -> str:
    if row.kind == "gap":
        return "GAP"
    if row.kind == "manual":
        return "MANUAL"
    missing = [t for t in row.tools if not tools.get(t)]
    if missing:
        return "RUN-RED" if row.on_missing == "fail" else "SKIP"
    return "RUN"


def _print_plan(session: Session, rows: list[Row],
                tools: dict[str, bool]) -> None:
    print(f"session: {session.key} ({session.label})")
    print("tools: " + ", ".join(f"{k} {'ok' if v else 'MISSING'}"
                                for k, v in tools.items()))
    for row in rows:
        missing = [t for t in row.tools if not tools.get(t)]
        extra = (f"  (missing: {', '.join(missing)})"
                 if row.kind == "auto" and missing else "")
        print(f"  {_plan_mark(row, tools):<7} {row.id}{extra}")
    print(f"{len(rows)} row(s) selected for {session.key}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wayland_smoke.py",
        description="Live Wayland/X11 smoke matrix (issue #13): detect "
                    "the session, run the applicable rows, record "
                    "evidence under docs/quality/wayland/.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan only — nothing executed, "
                             "nothing recorded (no session needed with "
                             "--session)")
    parser.add_argument("--session", choices=SESSION_KEYS,
                        help="override session detection (evidence "
                             "records it as forced)")
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT,
                        help="evidence root (default "
                             "%(default)s)")
    parser.add_argument("--row", action="append", metavar="ID",
                        help="restrict to this row id (repeatable)")
    parser.add_argument("--print-rows", action="store_true",
                        help="print the generated row/invoke table for "
                             "docs/dev/wayland-smoke-matrix.md")
    args = parser.parse_args(argv)

    if args.print_rows:
        print(print_rows_doc())
        return 0

    if args.session:
        stype = "x11" if args.session == "x11" else "wayland"
        session = Session(key=args.session, stype=stype,
                          desktop=args.session, desktop_all=args.session,
                          forced=True)
    else:
        session = detect_session()
    if session.key is None:
        print("no session detected (no XDG_SESSION_TYPE / "
              "WAYLAND_DISPLAY / DISPLAY) — log into the desktop "
              "session, or pass --session {"
              + ",".join(SESSION_KEYS) + "}", file=sys.stderr)
        return 2

    rows = select_rows(session.key)
    if args.row:
        known = {r.id for r in rows}
        unknown = [r for r in args.row if r not in known]
        if unknown:
            print(f"unknown row id(s) for {session.key}: "
                  f"{', '.join(unknown)}; valid: "
                  + ", ".join(sorted(known)), file=sys.stderr)
            return 2
        rows = [r for r in rows if r.id in set(args.row)]

    tools = probe_tools()
    _print_plan(session, rows, tools)
    if args.dry_run:
        print("dry run — nothing executed, nothing recorded")
        return 0

    env = {k: (os.environ.get(k) or "")
           for k in ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
                     "WAYLAND_DISPLAY", "DISPLAY")}
    results = run_matrix(session, rows, tools, real_run)
    for r in results:
        mark = "ok" if r.status == PASS else (
            "RED" if r.red else r.status)
        print(f"{r.row.id}: {mark}"
              + (f" — {r.detail[:120]}" if r.detail else ""))
    run_dir = record_evidence(args.out, session, results, env, tools,
                              host=platform.node() or "unknown-host",
                              sha=repo_sha())
    counts = summarize(results)
    print(f"{session.key}: PASS {counts[PASS]} / FAIL {counts[FAIL]} / "
          f"SKIP {counts[SKIP]} / MANUAL {counts[MANUAL]} / "
          f"GAP {counts[GAP]} — evidence: {run_dir}")
    return 1 if counts["red"] else 0


if __name__ == "__main__":
    sys.exit(main())
