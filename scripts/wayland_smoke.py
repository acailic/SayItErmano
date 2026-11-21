#!/usr/bin/env python3
"""Live Wayland/X11 smoke-matrix harness (issue #13, REQUIRED-BEFORE-PARITY).

docs/dev/wayland-smoke-matrix.md is the runbook: the session x hotkey x
insertion axes (Matrix S), the context matrices A (GNOME Wayland) / B
(sway) / C (X11 control), version minimums, layout and lifecycle rows.
What it has never had is a real Wayland login to run on — so this
harness makes execution ONE COMMAND per session:

  log into the session, then from the repo root:
      python3 scripts/wayland_smoke.py            # run the applicable rows
      python3 scripts/wayland_smoke.py --dry-run  # just print the plan
      python3 scripts/wayland_smoke.py --row s2-hotkey-portal   # one row

Division of labor (the 2026-11-21 reclassification of #13):

* agent/CI — this harness, its offline tests (tests/test_wayland_smoke.py:
  fake session env + fake runners; no display, no network, no daemon),
  and the extended runbook.
* owner — enable a real Wayland login (GDM WaylandEnable or a sway VM),
  log into each session, run the harness once per session, do the manual
  (dictation) part of the checklist it prints, and commit the evidence
  tree under docs/quality/wayland/<date>-<session>/.

What a row can check automatically it does (tool presence, doctor's
session matrix, the GlobalShortcuts portal version, GNOME/Plasma
minimums); everything that needs a human voice or a compositor gesture
(dictation, sleep/wake, lock/unlock, autostart) is printed as a dated
MANUAL checklist the owner ticks. Exit code 1 iff any automated row is
red; MANUAL rows never redden a run by themselves.

The harness never writes outside --out (default
docs/quality/wayland/); reruns on the same day merge into the same
evidence directory (row results are replaced, history kept).
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

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from fluidvoice import session as session_mod  # noqa: E402 — one truth for the session probe

OUT_DEFAULT = Path("docs/quality/wayland")
MATRIX_DOC = "docs/dev/wayland-smoke-matrix.md"
ISSUE_URL = "https://github.com/acailic/SayItErmano/issues/13"

PASS, FAIL, MANUAL = "PASS", "FAIL", "MANUAL"

# Matrix sessions, run order (GNOME Wayland first, then sway; X11 is the
# same-build regression control — ROADMAP "Per compositor" list).
SESSIONS: dict[str, str] = {
    "x11": "X11 (regression control)",
    "gnome-wl": "GNOME Wayland",
    "kde-wl": "KDE Plasma Wayland",
    "sway": "sway (wlroots)",
    "hyprland": "Hyprland (wlroots)",
}
ALL_SESSIONS = tuple(SESSIONS)

# The GlobalShortcuts portal probe argv (read-only D-Bus property read;
# the version gate is >= 1 — GNOME 48+/Plasma 6.7.4+ ship it, wlroots
# portals do not, xdg-desktop-portal-wlr#240).
PORTAL_PROBE = (
    "gdbus", "call", "--session",
    "--dest", "org.freedesktop.portal.Desktop",
    "--object-path", "/org/freedesktop/portal/desktop",
    "--method", "org.freedesktop.DBus.Properties.Get",
    "org.freedesktop.portal.GlobalShortcuts", "version",
)


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Probe:
    """One subprocess probe; expectations are data, not buried code."""
    label: str
    argv: tuple[str, ...]
    tool: str = ""                # required binary (probed via which first)
    rc_fail: bool = False         # True: rc != 0 is the PASSING outcome
    needle: str = ""              # with rc 0: output must contain this
    min_version: tuple[int, ...] = ()  # first version token must be >= this
    info_only: bool = False       # never fails; output recorded as evidence


@dataclasses.dataclass(frozen=True)
class Row:
    id: str
    doc: str                      # cell in docs/dev/wayland-smoke-matrix.md
    sessions: tuple[str, ...]     # matrix sessions this row applies to
    axis: str                     # session | hotkey | insertion | versions |
                                  # layout | lifecycle | context
    case: str
    expected: str
    tools: tuple[str, ...] = ()   # binaries that must be on PATH here
    probes: tuple[Probe, ...] = ()
    manual: str = ""              # owner action; nonempty => checklist item


@dataclasses.dataclass
class Check:
    label: str
    kind: str                     # "tool" | "cmd"
    status: str                   # PASS | FAIL | INFO
    detail: str = ""


@dataclasses.dataclass
class RowResult:
    row: Row
    status: str                   # PASS | FAIL | MANUAL
    detail: str = ""
    checks: list[Check] = dataclasses.field(default_factory=list)

    @property
    def automated(self) -> bool:
        return bool(self.row.tools or self.row.probes)


@dataclasses.dataclass(frozen=True)
class SessionInfo:
    """What detect() reduced the environment to."""
    id: str | None                # matrix session id, or None
    detail: str                   # why (for the report header)
    env: dict[str, str]           # the deciding env vars, verbatim


# ---------------------------------------------------------------------------
# session detection (reuse fluidvoice.session — one truth)
# ---------------------------------------------------------------------------

_DECIDING_ENV = ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
                 "WAYLAND_DISPLAY", "DISPLAY", "SWAYSOCK",
                 "HYPRLAND_INSTANCE_SIGNATURE")


def detect_session(env: Mapping[str, str] | None = None) -> SessionInfo:
    e = dict(os.environ if env is None else env)
    facts = {k: e.get(k, "") for k in _DECIDING_ENV}
    info = session_mod.probe(e)
    if info.type == "x11":
        return SessionInfo("x11", "XDG_SESSION_TYPE/DISPLAY say x11", facts)
    if not info.is_wayland:
        return SessionInfo(
            None, "no graphical session detected (XDG_SESSION_TYPE,"
            " WAYLAND_DISPLAY, DISPLAY all missing or not decisive) — log"
            " into a session first, or pass --session/--dry-run", facts)
    desktop = info.desktop_all
    if e.get("HYPRLAND_INSTANCE_SIGNATURE") or "hyprland" in desktop:
        return SessionInfo("hyprland", "Wayland, Hyprland", facts)
    if e.get("SWAYSOCK") or "sway" in desktop:
        return SessionInfo("sway", "Wayland, sway", facts)
    if any(t in ("kde", "plasma") for t in desktop.replace(";", ":").split(":")):
        return SessionInfo("kde-wl", "Wayland, KDE Plasma", facts)
    if session_mod.is_gnome_desktop(desktop):
        return SessionInfo("gnome-wl", "Wayland, GNOME", facts)
    return SessionInfo(
        None, f"Wayland session on non-matrix desktop '{desktop or '?'}'"
        " — override with --session if it should count", facts)


# ---------------------------------------------------------------------------
# the matrix (mirrors docs/dev/wayland-smoke-matrix.md; the doc is the
# runbook, this is the executable form — tests pin the two together)
# ---------------------------------------------------------------------------

def _doctor_probe(needle: str = "session:") -> Probe:
    return Probe("doctor", (sys.executable, "-m", "fluidvoice", "doctor"),
                 needle=needle)


ROWS: tuple[Row, ...] = (
    # -- Matrix S: session x hotkey x insertion axes, versions, layout,
    #    lifecycle (the 2026-11-21 pinned axes; ROADMAP rows 1-8 folded in)
    Row(
        id="s1-session-doctor", doc="S1", sessions=ALL_SESSIONS,
        axis="session",
        case="session detection + doctor session matrix agree, exit 0",
        expected="doctor exits 0 and its session line names this session"
                 " type/desktop (ROADMAP row 5)",
        probes=(_doctor_probe(),),
    ),
    Row(
        id="s2-hotkey-portal", doc="S2",
        sessions=("gnome-wl", "kde-wl", "hyprland"), axis="hotkey",
        case="GlobalShortcuts portal exists (version >= 1)",
        expected="portal D-Bus call returns a version (GNOME 48+,"
                 " Plasma 6.7.4+; Hyprland ships it)",
        probes=(Probe("portal-version", PORTAL_PROBE, tool="gdbus",
                      min_version=(1,)),),
        manual="bind the portal shortcut + accept the consent dialog, then"
               " toggle a dictation with it",
    ),
    Row(
        id="s3-hotkey-portal-absent", doc="S3", sessions=("sway",),
        axis="hotkey",
        case="no GlobalShortcuts backend on sway (xdg-desktop-portal-wlr#240)",
        expected="portal D-Bus call FAILS — the compositor-bind --toggle"
                 " path is the primary hotkey here",
        probes=(Probe("portal-version", PORTAL_PROBE, tool="gdbus",
                      rc_fail=True),),
    ),
    Row(
        id="s4-hotkey-x11-grab", doc="S4", sessions=("x11",), axis="hotkey",
        case="X11 global hotkey grab resolves",
        expected="doctor session matrix reports hotkey: x11-grab",
        probes=(_doctor_probe("hotkey: x11-grab"),),
        manual="press the global hotkey in an editor: dictation starts and"
               " stops",
    ),
    Row(
        id="s5-hotkey-compositor-toggle", doc="S5",
        sessions=("gnome-wl", "kde-wl", "sway", "hyprland"), axis="hotkey",
        case="compositor-bind --toggle path is surfaced",
        expected="doctor points at the DE-shortcut toggle script (no global"
                 " grabs on Wayland); on sway this IS the primary path",
        probes=(_doctor_probe("bind a DE shortcut"),),
        manual="bind the toggle script (sway bindsym / DE custom shortcut),"
               " press it: dictation toggles (ROADMAP row 6)",
    ),
    Row(
        id="s6-insert-wtype", doc="S6", sessions=("sway", "hyprland"),
        axis="insertion",
        case="zwp_virtual_keyboard typed insertion (wtype)",
        expected="wtype on PATH; dictation types into a terminal and an"
                 " editor, spoken-send Enter, paste-last, clipboard restored"
                 " (ROADMAP row 2; wlroots-only — mutter#1974, KDE 512996)",
        tools=("wtype",),
        manual="dictate into foot/kitty AND an editor; verify spoken-send"
               " Enter, paste-last, paste mode via wl-clipboard, pre-paste"
               " clipboard restored, leading-dash text takes the paste path",
    ),
    Row(
        id="s7-insert-uinput", doc="S7", sessions=("gnome-wl", "kde-wl"),
        axis="insertion",
        case="uinput typed insertion (ydotool + ydotoold)",
        expected="ydotool on PATH (GNOME/KDE have no zwp virtual keyboard);"
                 " same insertion set as S6 (ROADMAP row 3)",
        tools=("ydotool",),
        manual="with ydotoold running + uinput perms: dictate the ROADMAP"
               " row-2 set; note whether key-duration tuning is needed",
    ),
    Row(
        id="s8-insert-clipboard", doc="S8",
        sessions=("gnome-wl", "kde-wl", "sway", "hyprland"),
        axis="insertion",
        case="clipboard + Ctrl+V paste path",
        expected="wl-copy AND wl-paste on PATH; long-text dictation takes"
                 " the paste path and the pre-paste clipboard is restored",
        tools=("wl-copy", "wl-paste"),
        manual="dictate a long text into an editor: it pastes via"
               " clipboard+Ctrl+V and the previous clipboard content is"
               " restored afterwards",
    ),
    Row(
        id="s9-insert-xdotool", doc="S9", sessions=("x11",),
        axis="insertion",
        case="xdotool typed insertion",
        expected="xdotool on PATH; dictation types into an editor",
        tools=("xdotool",),
        manual="dictate into gedit: text lands at the caret",
    ),
    Row(
        id="s10-version-gnome", doc="S10", sessions=("gnome-wl",),
        axis="versions",
        case="GNOME >= 48 (GlobalShortcuts portal hotkeys)",
        expected="gnome-shell --version reports 48 or newer",
        probes=(Probe("gnome-shell", ("gnome-shell", "--version"),
                      tool="gnome-shell", min_version=(48,)),),
    ),
    Row(
        id="s11-version-plasma", doc="S11", sessions=("kde-wl",),
        axis="versions",
        case="Plasma >= 6.7.4 (shortcut description changes)",
        expected="plasmashell --version reports 6.7.4 or newer",
        probes=(Probe("plasmashell", ("plasmashell", "--version"),
                      tool="plasmashell", min_version=(6, 7, 4)),),
    ),
    Row(
        id="s12-version-portal", doc="S12",
        sessions=("gnome-wl", "kde-wl", "hyprland"), axis="versions",
        case="portal version recorded (>= 1.21 changed the consent flow)",
        expected="exact xdg-desktop-portal version captured as evidence;"
                 " older portals use the pre-1.21 consent flow",
        probes=(Probe("portal-version", PORTAL_PROBE, tool="gdbus",
                      info_only=True),),
    ),
    Row(
        id="s13-layout-nonus", doc="S13", sessions=("kde-wl",),
        axis="layout",
        case="non-US keyboard layout (KDE keysym layout regression class)",
        expected="with e.g. a German layout active, dictated punctuation"
                 " inserts the right keysyms for that layout",
        probes=(Probe("localectl", ("localectl", "status"),
                      tool="localectl", info_only=True),),
        manual="switch to a non-US layout, dictate punctuation and umlauts"
               " into kate/konsole, verify correct symbols",
    ),
    Row(
        id="s14-lifecycle-suspend", doc="S14", sessions=ALL_SESSIONS,
        axis="lifecycle",
        case="sleep/wake: hotkey + insertion still work",
        expected="after suspend+resume, toggle a dictation and have it"
                 " insert normally",
        manual="suspend the machine, wake it, run the harness again or"
               " verify by hand: hotkey toggles, text inserts",
    ),
    Row(
        id="s15-lifecycle-lock", doc="S15", sessions=ALL_SESSIONS,
        axis="lifecycle",
        case="lock/unlock: hotkey suppressed while locked, works after",
        expected="hotkey does not fire on the lock screen; after unlock,"
                 " dictation works without a daemon restart",
        manual="lock the session (super+L / swaylock), press the hotkey"
               " (nothing captures), unlock, dictate",
    ),
    Row(
        id="s16-lifecycle-autostart", doc="S16", sessions=ALL_SESSIONS,
        axis="lifecycle",
        case="autostart: daemon comes up on login, hotkey + insertion work",
        expected="after a fresh login, the daemon autostarts and one"
                 " dictation round-trip works",
        manual="log out and back in, wait for the daemon, dictate",
    ),
    Row(
        id="s17-baseline-bare", doc="S17", sessions=ALL_SESSIONS,
        axis="session",
        case="baseline with no insertion tools (ROADMAP row 1)",
        expected="daemon starts foreground AND under the systemd user unit;"
                 " dictation transcribes into history; the no-insertion-tool"
                 " notification appears",
        manual="hide wtype/ydotool/xdotool off PATH, restart the daemon"
                 " (documented two-step), dictate, check history + the"
                 " notification, restore",
    ),
    Row(
        id="s18-overlay-notifications", doc="S18",
        sessions=("gnome-wl", "kde-wl", "sway", "hyprland"),
        axis="session",
        case="overlay degrades to the notification preview (ROADMAP row 4)",
        expected="recording shows the notification preview; no X11-pill"
                 " attempt noise in the daemon log",
        manual="start a dictation: notification preview appears; grep the"
               " daemon log for pill/X11 errors (should be none)",
    ),
    Row(
        id="s19-evdev-ptt", doc="S19",
        sessions=("gnome-wl", "kde-wl", "sway", "hyprland"),
        axis="hotkey",
        case="evdev push-to-talk (hotkey.wayland_evdev, ROADMAP row 7)",
        expected="hold-to-talk works via the evdev device; device-name"
                 " match noted",
        manual="enable hotkey.wayland_evdev (+ input-group access), hold"
               " the key: recording while held",
    ),
    Row(
        id="s20-x11-regression", doc="S20", sessions=("x11",),
        axis="session",
        case="X11 regression, same build, zero deltas (ROADMAP row 8)",
        expected="full manual pass — hotkey grab, pill preview, verified"
                 " paste, spoken-send, rewrite — identical to the last"
                 " recorded X11 run",
        manual="run the full manual X11 pass on THIS build and diff"
               " behavior against the last recorded run (zero deltas)",
    ),
    # -- Matrix A: GNOME Wayland context rows (doc cells A1-A15) --------
    Row(
        id="a1-doctor-context", doc="A1", sessions=("gnome-wl",),
        axis="context",
        case="doctor context section: no crash",
        expected="doctor exits 0 with the context config enabled; names the"
                 " atspi provider",
        probes=(_doctor_probe(),),
        manual="check the doctor context section names the atspi provider",
    ),
    Row(
        id="a2-empty-field", doc="A2", sessions=("gnome-wl",),
        axis="context",
        case="dictate into empty gedit field",
        expected="text typed; daemon log line 'context: atspi (role=text,…)'",
        manual="per the doc cell A2 (context.enabled=true config)",
    ),
    Row(
        id="a3-mid-sentence", doc="A3", sessions=("gnome-wl",),
        axis="context",
        case="dictate after typing 'mid sentence' (no trailing space)",
        expected="one leading space inserted, no capitalization",
        manual="per the doc cell A3",
    ),
    Row(
        id="a4-after-period", doc="A4", sessions=("gnome-wl",),
        axis="context",
        case="dictate after a '.' + newline",
        expected="no leading space, first letter capitalized",
        manual="per the doc cell A4",
    ),
    Row(
        id="a5-selection", doc="A5", sessions=("gnome-wl",),
        axis="context",
        case="select text in gedit, dictate",
        expected="selection read does not disturb paste/typing; dictation"
                 " lands at caret",
        manual="per the doc cell A5",
    ),
    Row(
        id="a6-search-field", doc="A6", sessions=("gnome-wl",),
        axis="context",
        case="nautilus search field (role=search)",
        expected="GAAV: lowercase first letter, trailing period stripped",
        manual="per the doc cell A6",
    ),
    Row(
        id="a7-terminal-typed", doc="A7", sessions=("gnome-wl",),
        axis="context",
        case="gnome-terminal, typed insertion",
        expected="trailing autocomplete space; long text pastes via"
                 " ctrl+shift+v",
        manual="per the doc cell A7",
    ),
    Row(
        id="a8-spoken-send-terminal", doc="A8", sessions=("gnome-wl",),
        axis="context",
        case="gnome-terminal, spoken-send phrase",
        expected="Enter suppressed (log: 'spoken-send: Enter suppressed at"
                 " insert')",
        manual="per the doc cell A8",
    ),
    Row(
        id="a9-spoken-send-app", doc="A9", sessions=("gnome-wl",),
        axis="context",
        case="non-terminal app, spoken-send phrase",
        expected="Enter pressed after typing",
        manual="per the doc cell A9",
    ),
    Row(
        id="a10-spoken-send-rule", doc="A10", sessions=("gnome-wl",),
        axis="context",
        case="rule {match=[\"gnome-terminal\"], spoken_send=\"on\"}",
        expected="Enter IS pressed in the terminal",
        manual="per the doc cell A10",
    ),
    Row(
        id="a11-no-bus", doc="A11", sessions=("gnome-wl",),
        axis="context",
        case="accessibility bus off, dictate",
        expected="take succeeds, exactly pre-P2 behavior (no context line),"
                 " no crash",
        manual="per the doc cell A11 (kill the at-spi2 bus first)",
    ),
    Row(
        id="a12-provider-none", doc="A12", sessions=("gnome-wl",),
        axis="context",
        case="context.provider = \"none\"",
        expected="identical to A11",
        manual="per the doc cell A12 (flip config, restore afterwards)",
    ),
    Row(
        id="a13-privacy-sweep", doc="A13", sessions=("gnome-wl",),
        axis="context",
        case="privacy sweep after A2-A10",
        expected="history.jsonl rows contain no preceding/selection text,"
                 " no extra AT-SPI app ids",
        manual="per the doc cell A13",
    ),
    Row(
        id="a14-latency", doc="A14", sessions=("gnome-wl",),
        axis="context",
        case="latency: insertion-time context read",
        expected="no perceptible delay between stop and typing"
                 " (< ~150 ms extra)",
        manual="per the doc cell A14",
    ),
    Row(
        id="a15-focus-change", doc="A15", sessions=("gnome-wl",),
        axis="context",
        case="focus change mid-take",
        expected="identity/quirks follow the focus AT INSERTION; no"
                 " wrong-window typing",
        manual="per the doc cell A15",
    ),
    # -- Matrix B: sway context rows (doc cells B1-B4) -------------------
    Row(
        id="b1-atspi-context", doc="B1", sessions=("sway",),
        axis="context",
        case="A2-A6 equivalents on foot/kitty + editor",
        expected="same as the GNOME matrix",
        manual="per the doc cell B1",
    ),
    Row(
        id="b2-wtype-identity", doc="B2", sessions=("sway",),
        axis="context",
        case="wtype insertion + context identity",
        expected="terminal quirks active in foot/kitty via AT-SPI identity",
        manual="per the doc cell B2",
    ),
    Row(
        id="b3-no-atspi", doc="B3", sessions=("sway",),
        axis="context",
        case="no AT-SPI bus in the session (minimal sway)",
        expected="degrade: no context, exactly pre-P2 behavior",
        manual="per the doc cell B3",
    ),
    Row(
        id="b4-privacy-sweep", doc="B4", sessions=("sway",),
        axis="context",
        case="privacy sweep",
        expected="as A13",
        manual="per the doc cell B4",
    ),
    # -- Matrix C: X11 control rows (doc cells C1-C3) --------------------
    Row(
        id="c1-default-config", doc="C1", sessions=("x11",),
        axis="context",
        case="default config (context off)",
        expected="byte-identical pre-P2 behavior",
        manual="per the doc cell C1",
    ),
    Row(
        id="c2-context-auto", doc="C2", sessions=("x11",),
        axis="context",
        case="context.enabled=true, provider=\"auto\"",
        expected="x11 identity provider; WM_CLASS quirks exactly as before"
                 " (identity-only)",
        manual="per the doc cell C2",
    ),
    Row(
        id="c3-provider-atspi", doc="C3", sessions=("x11",),
        axis="context",
        case="provider=\"atspi\" on X11",
        expected="role/selection/preceding available in GTK apps; A3/A4/A6"
                 " pass on X11 too (2026-09-14 fixes verified, rerun to"
                 " close the cell)",
        manual="per the doc cell C3",
    ),
)

ROW_IDS = tuple(r.id for r in ROWS)


def rows_for_session(session: str) -> list[Row]:
    return [r for r in ROWS if session in r.sessions]


# ---------------------------------------------------------------------------
# evaluation (runner + which injectable; tests drive fakes)
# ---------------------------------------------------------------------------

Runner = Callable[[tuple[str, ...]], "tuple[int, str]"]
Which = Callable[[str], "str | None"]


def real_run(argv: tuple[str, ...]) -> tuple[int, str]:
    try:
        proc = subprocess.run(list(argv), cwd=str(REPO), capture_output=True,
                              text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return 124, f"timeout: {' '.join(argv)}"
    except OSError as exc:
        return 127, f"cannot execute: {exc}"[:400]
    tail = ((proc.stdout or "") + (proc.stderr or ""))[-400:].strip()
    return proc.returncode, tail


def _version_tuple(text: str) -> tuple[int, ...] | None:
    m = re.search(r"\d+(?:\.\d+)*", text)
    return tuple(int(p) for p in m.group(0).split(".")) if m else None


def _ge(v: tuple[int, ...], minimum: tuple[int, ...]) -> bool:
    width = max(len(v), len(minimum))
    a = v + (0,) * (width - len(v))
    b = minimum + (0,) * (width - len(minimum))
    return a >= b


def _eval_probe(probe: Probe, run: Runner, which: Which) -> Check:
    if probe.tool and which(probe.tool) is None:
        return Check(probe.label, "cmd", FAIL,
                     f"{probe.tool} is not installed — cannot probe")
    rc, out = run(probe.argv)
    if probe.info_only:
        return Check(probe.label, "cmd", PASS, f"rc={rc} {out[:200]}".strip())
    if probe.rc_fail:
        if rc != 0:
            return Check(probe.label, "cmd", PASS,
                         f"failed as expected (rc={rc}): {out[:200]}")
        return Check(probe.label, "cmd", FAIL,
                     f"expected failure but succeeded: {out[:200]}")
    if rc != 0:
        return Check(probe.label, "cmd", FAIL,
                     f"`{' '.join(probe.argv[:3])}…` exited {rc}: {out[:200]}")
    if probe.needle and probe.needle not in out:
        return Check(probe.label, "cmd", FAIL,
                     f"output does not contain {probe.needle!r}: {out[:200]}")
    detail = out[:200]
    if probe.min_version:
        got = _version_tuple(out)
        if got is None:
            return Check(probe.label, "cmd", FAIL,
                         f"no version parsed from: {out[:200]}")
        if not _ge(got, probe.min_version):
            return Check(probe.label, "cmd", FAIL,
                         f"version {'.'.join(map(str, got))} < minimum"
                         f" {'.'.join(map(str, probe.min_version))}")
        detail = f"version {'.'.join(map(str, got))}"
    return Check(probe.label, "cmd", PASS, detail or "rc=0")


def eval_row(row: Row, run: Runner, which: Which) -> RowResult:
    result = RowResult(row, MANUAL)
    for tool in row.tools:
        ok = which(tool) is not None
        result.checks.append(Check(f"tool:{tool}", "tool",
                                   PASS if ok else FAIL,
                                   "" if ok else f"{tool} is not on PATH"))
    for probe in row.probes:
        result.checks.append(_eval_probe(probe, run, which))
    if not result.checks:
        result.detail = "manual row — tick it in the checklist"
        return result
    failed = [c for c in result.checks if c.status == FAIL]
    if failed:
        result.status = FAIL
        result.detail = "; ".join(f"{c.label}: {c.detail}" for c in failed)
    else:
        result.status = PASS
        result.detail = "automated checks green"
    return result


# ---------------------------------------------------------------------------
# evidence tree: docs/quality/wayland/<date>-<session>/{results.json,report.md}
# ---------------------------------------------------------------------------

def evidence_dir(out: Path, date: str, session: str) -> Path:
    return out / f"{date}-{session}"


def _row_json(res: RowResult) -> dict:
    return {
        "id": res.row.id, "doc": res.row.doc, "axis": res.row.axis,
        "case": res.row.case, "expected": res.row.expected,
        "status": res.status, "detail": res.detail,
        "checks": [{"label": c.label, "kind": c.kind, "status": c.status,
                    "detail": c.detail} for c in res.checks],
    }


def _row_md_line(res: RowResult) -> str:
    case = res.row.case.replace("|", "\\|")
    detail = res.detail.replace("|", "\\|")[:160]
    return f"| {res.row.id} ({res.row.doc}) | {res.row.axis} | " \
           f"{res.status} | {case} | {detail} |"


def report_markdown(summary: dict, results: list[RowResult],
                    applicable: int) -> str:
    n_fail = sum(1 for r in results if r.status == FAIL)
    n_manual = sum(1 for r in results if res_manual_pending(r))
    verdict = FAIL if n_fail else PASS
    lines = [
        f"# Wayland smoke — {summary['session_label']} — {summary['date']}",
        "",
        f"RESULT: {verdict}"
        + (f" ({n_fail} red automated row(s))" if n_fail else " (automated)"),
        "",
        f"Manual checklist: {n_manual} item(s) pending below — the parity"
        " gate needs both green automated rows AND the manual ticks.",
        "",
        f"- harness: `{summary['invoke']}` (issue"
          f" [#13]({ISSUE_URL}), runbook: {MATRIX_DOC})",
        f"- host: {summary['host']}  commit: {summary['commit']}",
        f"- detected session: **{summary['session']}** —"
        f" {summary['session_detail']}",
        "- env: " + " ".join(f"{k}={v!r}" for k, v in
                             sorted(summary["session_env"].items()) if v),
        f"- rows run: {len(results)} of {applicable} applicable"
        + (" (partial run — rerun without --row for the full set)"
           if len(results) < applicable else ""),
        "",
        "## Automated rows",
        "",
        "| row (doc) | axis | status | case | detail |",
        "|---|---|---|---|---|",
    ]
    lines += [_row_md_line(r)
              for r in results if r.status in (PASS, FAIL)]
    lines += [
        "",
        "## Manual checklist (owner ticks; the doc cells carry the full"
        " procedure)",
        "",
    ]
    for r in results:
        if res_manual_pending(r):
            note = r.row.manual or r.row.case
            lines.append(f"- [ ] **{r.row.id}** ({r.row.doc},"
                         f" {r.row.axis}): {note}"
                         f" — expect: {r.row.expected}")
    lines += [
        "",
        "---",
        "",
        "Append a dated note to the Runs section of"
        f" {MATRIX_DOC} linking this directory; a red row needs a fix or a"
        " known-issues entry users can find before the gate can close.",
        "",
    ]
    return "\n".join(lines)


def res_manual_pending(res: RowResult) -> bool:
    return bool(res.row.manual) and res.status != FAIL


def run_matrix(session: str, rows: list[Row], env: Mapping[str, str],
               detail: str, run: Runner, which: Which, out: Path,
               *, date: str, host: str = "", commit: str = "",
               invoke: str = "") -> tuple[list[RowResult], Path, bool]:
    """Evaluate `rows` here, merge into today's evidence dir for the
    session, return (results, dir, any_red)."""
    results = [eval_row(r, run, which) for r in rows]
    target = evidence_dir(out, date, session)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "results.json"
    merged: dict[str, dict] = {}
    if json_path.exists():
        try:
            old = json.loads(json_path.read_text(encoding="utf-8"))
            merged = {r["id"]: r for r in old.get("rows", [])}
        except (ValueError, OSError):
            merged = {}
    for res in results:
        merged[res.row.id] = _row_json(res)
    ordered = [merged[r.id] for r in rows_for_session(session)
               if r.id in merged]
    any_red = any(r["status"] == FAIL for r in ordered)
    summary = {
        "date": date, "session": session,
        "session_label": SESSIONS.get(session, session),
        "session_detail": detail,
        "session_env": dict(env),
        "host": host, "commit": commit, "invoke": invoke,
        "result": FAIL if any_red else PASS,
        "rows": ordered,
    }
    json_path.write_text(json.dumps(summary, indent=2) + "\n",
                         encoding="utf-8")
    by_id = {r.id: r for r in rows_for_session(session)}
    restored = [RowResult(by_id[j["id"]], j["status"], j["detail"],
                          [Check(c["label"], c["kind"], c["status"],
                                 c["detail"]) for c in j["checks"]])
                for j in ordered if j["id"] in by_id]
    (target / "report.md").write_text(
        report_markdown(summary, restored, len(rows_for_session(session))),
        encoding="utf-8")
    return results, target, any_red


# ---------------------------------------------------------------------------
# plan / CLI
# ---------------------------------------------------------------------------

def print_plan(session: SessionInfo, row_filter: set[str] | None) -> None:
    if session.id is None:
        print(f"session: NONE — {session.detail}")
        print("full matrix map (log into a session or pass --session):")
        for sid in ALL_SESSIONS:
            ids = [r.id for r in rows_for_session(sid)]
            print(f"  {sid:<10} {len(ids):>2} rows: {' '.join(ids)}")
        return
    rows = rows_for_session(session.id)
    if row_filter:
        rows = [r for r in rows if r.id in row_filter]
    print(f"session: {session.id} ({SESSIONS[session.id]}) —"
          f" {session.detail}")
    for env_key, val in sorted(session.env.items()):
        if val:
            print(f"  {env_key}={val}")
    print(f"plan ({len(rows)} rows):")
    for r in rows:
        kind = "auto+manual" if (r.tools or r.probes) and r.manual else \
               "auto" if (r.tools or r.probes) else "manual"
        print(f"  {r.id:<26} {kind:<10} {r.case}")


def _git_commit() -> str:
    try:
        proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=str(REPO), capture_output=True, text=True,
                              timeout=10)
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None,
         run: Runner = real_run, which: Which = shutil.which,
         host: str | None = None, commit: str | None = None,
         today: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wayland_smoke.py",
        description="Live Wayland/X11 smoke matrix (issue #13); evidence"
                    " under docs/quality/wayland/<date>-<session>/")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan, execute nothing, write"
                             " nothing (works headless)")
    parser.add_argument("--session", choices=list(SESSIONS),
                        help="override session detection")
    parser.add_argument("--row", action="append", metavar="ID",
                        choices=list(ROW_IDS),
                        help="restrict to this row (repeatable)")
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    args = parser.parse_args(argv)

    detected = detect_session(env)
    session_id = args.session or detected.id
    row_filter = set(args.row) if args.row else None
    if row_filter and session_id:
        applicable = {r.id for r in rows_for_session(session_id)}
        not_here = row_filter - applicable
        if not_here:
            print(f"note: row(s) not applicable to {session_id}:"
                  f" {', '.join(sorted(not_here))}")
    if args.dry_run:
        print_plan(SessionInfo(
            session_id,
            f"overridden ({SESSIONS[session_id]})" if args.session
            else detected.detail,
            detected.env), row_filter)
        print("dry-run — nothing executed, nothing written")
        return 0
    if session_id is None:
        print(f"error: {detected.detail}", file=sys.stderr)
        return 2
    rows = rows_for_session(session_id)
    if row_filter:
        rows = [r for r in rows if r.id in row_filter]
    if not rows:
        print(f"error: no applicable rows for session {session_id}",
              file=sys.stderr)
        return 2
    invoke = "python3 scripts/wayland_smoke.py" + (
        "".join(f" --row {r}" for r in sorted(row_filter)) if row_filter else "")
    date = today or _dt.date.today().isoformat()
    results, target, any_red = run_matrix(
        session_id, rows, detected.env, detected.detail, run, which,
        args.out, date=date,
        host=host or platform.node() or "unknown-host",
        commit=commit or _git_commit(), invoke=invoke)
    for res in results:
        marker = {PASS: "ok", FAIL: "RED", MANUAL: "manual"}[res.status]
        print(f"{res.row.id:<26} {marker}"
              + (f" — {res.detail[:140]}" if res.detail else ""))
    print(f"evidence: {target}/ ({len(results)} rows,"
          f" {'RED' if any_red else 'green'}; manual checklist in"
          " report.md)")
    return 1 if any_red else 0


if __name__ == "__main__":
    sys.exit(main())
