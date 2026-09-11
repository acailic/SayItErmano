"""Text insertion into the focused app (X11 + Wayland): typed keystrokes
or clipboard paste.

Mirrors FluidVoice's TypingService strategies:
- typed: xdotool type (X11) / wtype / ydotool (Wayland) - keystroke
  simulation, "clipboard free insert"
- paste: clipboard + Ctrl+V with clipboard restore ("reliable paste"):
  xclip + verified selection read-observation on X11, wl-clipboard with a
  fixed settle delay on Wayland (cross-client selection reads are
  impossible there - see WAYLAND_PASTE_SETTLE_S).

Honest failure contract (first-use funnel F-05/F-18): insertion never
reports success falsely. Every insert_text failure raises InsertionFailure
carrying a machine-readable InsertResult (FailureKind enum, missing tools,
exact install command, clipboard outcome) - also broadcast to
add_result_listener subscribers (the daemon/pill seam). Before raising, an
automatic copy-to-clipboard attempt preserves the text; when even that is
impossible the message names History as the recovery path (the pipeline
records the take regardless). copy_to_clipboard/clipboard_fallback return
an honest ClipboardResult instead of silently no-oping.

Wayland is additive: every branch is taken ONLY via the session probe
(fluidvoice/session.py), never on "xdotool missing" - the X11 paths below
stay byte-identical for x11/unknown sessions (the capability pre-flight
enriches failures; it never gates the attempt).
"""
from __future__ import annotations

import enum
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, NoReturn

from . import session as session_mod


class InsertError(RuntimeError):
    pass


class ToolMissing(InsertError):
    """A required external tool is not on PATH (raised by _run)."""

    def __init__(self, tool: str):
        super().__init__(f"required tool not found: {tool}")
        self.tool = tool


# ---------------------------------------------------------------------------
# Machine-readable insertion outcomes (F-05/F-18): enums + result objects
# other components (pipeline, daemon, pill/overlay, tests) consume instead
# of parsing notification strings.
# ---------------------------------------------------------------------------

class FailureKind(enum.Enum):
    """Why an insertion failed."""

    TOOL_MISSING = "tool_missing"        # backend tool not on PATH
    TOOL_FAILED = "tool_failed"          # tool ran but exited nonzero
    FOCUS_LOST = "focus_lost"            # no/wrong focused window
    PASTE_UNVERIFIED = "paste_unverified"  # keystroke ok, target never read
    NO_BACKEND = "no_backend"            # session has no usable insert path


class ClipboardOutcome(enum.Enum):
    """Honest result of a copy-to-clipboard attempt (F-18: no silent
    no-ops - a fallback that could not write says so)."""

    WRITTEN = "written"
    TOOL_MISSING = "tool_missing"
    WRITE_FAILED = "write_failed"


@dataclass(frozen=True)
class ClipboardResult:
    outcome: ClipboardOutcome
    tool: str | None = None
    detail: str = ""

    @property
    def written(self) -> bool:
        return self.outcome is ClipboardOutcome.WRITTEN


@dataclass(frozen=True)
class InsertResult:
    """Outcome of one insert_text call. ok=True strategies: typed, paste,
    clipboard-fallback (text on the clipboard, user pastes manually).
    ok=False: strategy "failed", failure kind set, message actionable,
    clipboard carries the automatic-preservation outcome (None = not
    attempted), missing_tools/install_hint from the capability pre-flight."""

    ok: bool
    strategy: str
    failure: FailureKind | None = None
    message: str = ""
    install_hint: str = ""
    clipboard: ClipboardResult | None = None
    missing_tools: tuple[str, ...] = ()


class InsertionFailure(InsertError):
    """Structured insertion failure: the FailureKind and the full
    InsertResult ride on the exception, so callers consume machine-readable
    truth instead of string-matching messages."""

    def __init__(self, message: str, *, kind: FailureKind | None = None,
                 result: InsertResult | None = None,
                 missing_tools: tuple[str, ...] = (),
                 install_hint: str = ""):
        super().__init__(message)
        self.kind = kind
        self.result = result
        self.missing_tools = tuple(missing_tools)
        self.install_hint = install_hint


_result_listeners: list[Callable[[InsertResult], None]] = []


def add_result_listener(fn: Callable[[InsertResult], None]) -> None:
    """Subscribe to every insert_text outcome (ok and failed) - the hook
    the daemon/pill/overlay use to surface insertion state without
    touching this module's callers (F-05 hook seam)."""
    if fn not in _result_listeners:
        _result_listeners.append(fn)


def remove_result_listener(fn: Callable[[InsertResult], None]) -> None:
    if fn in _result_listeners:
        _result_listeners.remove(fn)


def _emit_result(result: InsertResult,
                 on_result: Callable[[InsertResult], None] | None) -> None:
    for fn in list(_result_listeners):
        try:
            fn(result)
        except Exception:
            pass  # a broken listener must never break insertion
    if on_result is not None:
        try:
            on_result(result)
        except Exception:
            pass


# stderr signatures of a tool that ran but had no (usable) focused window
_FOCUS_LOST_RE = re.compile(
    r"no active window|failed to (?:find|get|focus)|\bx error\b|badwindow",
    re.IGNORECASE)


def _tool_failure_kind(stderr: str) -> FailureKind:
    return (FailureKind.FOCUS_LOST if _FOCUS_LOST_RE.search(stderr or "")
            else FailureKind.TOOL_FAILED)


# ---------------------------------------------------------------------------
# Insertion capability pre-flight (F-05): the session's insertion strategy
# table vs what is actually installed, cached, with the exact install
# command for the detected distro. Surfaced once at daemon start
# (startup_capability_check) and consulted at insertion time to enrich
# failures (capability_status). Never gates the attempt itself - the X11
# paths stay byte-identical per the module contract.
# ---------------------------------------------------------------------------

_INSTALL_PATTERNS = {
    "apt": "sudo apt install {pkgs}",
    "dnf": "sudo dnf install {pkgs}",
    "pacman": "sudo pacman -S --needed {pkgs}",
    "zypper": "sudo zypper install {pkgs}",
    "apk": "sudo apk add {pkgs}",
}
_DISTRO_TO_PM = {
    "debian": "apt", "ubuntu": "apt", "linuxmint": "apt", "pop": "apt",
    "elementary": "apt", "zorin": "apt",
    "fedora": "dnf", "rhel": "dnf", "rocky": "dnf", "almalinux": "dnf",
    "arch": "pacman", "manjaro": "pacman", "endeavouros": "pacman",
    "garuda": "pacman",
    "opensuse-leap": "zypper", "opensuse-tumbleweed": "zypper",
    "opensuse": "zypper", "suse": "zypper",
    "alpine": "apk",
}
_ID_LIKE_TO_PM = {"debian": "apt", "fedora": "dnf", "rhel": "dnf",
                  "arch": "pacman", "suse": "zypper", "alpine": "apk"}
# executable -> distribution package that provides it (identity otherwise)
_PACKAGE_FOR_TOOL = {"wl-copy": "wl-clipboard", "wl-paste": "wl-clipboard"}


def _read_os_release(path: str = "/etc/os-release") -> dict[str, str]:
    try:
        out: dict[str, str] = {}
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    key, _, val = line.partition("=")
                    out[key.strip()] = val.strip().strip('"').strip("'")
        return out
    except OSError:
        return {}


def _detect_distro() -> str:
    """os-release ID, else the first ID_LIKE token, else "" (unknown)."""
    rel = _read_os_release()
    distro = (rel.get("ID") or "").lower()
    if not distro:
        distro = next((t for t in rel.get("ID_LIKE", "").lower().split()
                       if t), "")
    return distro


def install_hint(missing_tools, distro_id: str | None = None) -> str:
    """The exact install command for the missing tools on the detected
    distro (os-release ID, then ID_LIKE, else a generic instruction).
    Empty string when nothing is missing."""
    tools = [t for t in missing_tools if t]
    if not tools:
        return ""
    distro = (distro_id or _detect_distro()).lower()
    pm = _DISTRO_TO_PM.get(distro)
    if pm is None and distro_id is None:
        likes = _read_os_release().get("ID_LIKE", "").lower().split()
        pm = next((_ID_LIKE_TO_PM[t] for t in likes if t in _ID_LIKE_TO_PM),
                  None)
    pkgs: list[str] = []
    for tool in tools:
        pkg = _PACKAGE_FOR_TOOL.get(tool, tool)
        if pkg not in pkgs:
            pkgs.append(pkg)
    if pm is not None:
        return _INSTALL_PATTERNS[pm].format(pkgs=" ".join(pkgs))
    return ("install " + " ".join(pkgs)
            + " with your distribution's package manager")


@dataclass(frozen=True)
class CapabilityReport:
    """What the session's insertion strategy table needs vs reality.

    ok: the primary path (typed/paste via the typing tool) is available.
    fallback_ok: an emergency clipboard write is available - when False a
    failed insert strands the text in History only."""

    ok: bool
    fallback_ok: bool
    session_type: str
    typing_tool: str | None
    missing_tools: tuple[str, ...]
    install_hint: str
    detail: str


def _capability_detail(ok: bool, fallback_ok: bool,
                       typing_tool: str | None,
                       missing: tuple[str, ...], hint: str) -> str:
    if ok and fallback_ok:
        return f"insertion ready ({typing_tool} + clipboard)"
    fix = f"; fix: {hint}" if hint else ""
    if ok:
        return ("typing works but the clipboard fallback tool is missing "
                f"({', '.join(missing)}) - a failed insert would leave the "
                "text in History only" + fix)
    if fallback_ok:
        return ("no typing backend (missing " + ", ".join(missing)
                + ") - dictations degrade to a clipboard copy you paste "
                "manually" + fix)
    return ("insertion impossible: missing " + ", ".join(missing)
            + " - until installed, transcripts are only in History" + fix)


def check_insertion_capability(cfg: dict | None = None, *,
                               which: Callable[[str], str | None] | None = None,
                               info=None) -> CapabilityReport:
    """Pure capability check against the actual insertion strategy table:

    x11/unknown: typing needs xdotool; paste and the emergency clipboard
    need xclip. wayland: typing needs wtype/ydotool (auto-resolved, GNOME
    excludes wtype); paste and the fallback need wl-clipboard. No caching
    here - capability_status() adds it.
    """
    if which is None:
        which = shutil.which
    if info is None:
        info = session_mod.current()
    pref = str(((cfg or {}).get("insertion") or {}).get("wayland_tool",
                                                        "auto"))
    if info.is_wayland:
        typing_tool, _reason = session_mod.resolve_wayland_tool(
            pref, info.desktop_all, which)
        wanted = list(session_mod.WAYLAND_TYPE_TOOLS) + ["wl-copy", "wl-paste"]
        fallback_tool = "wl-copy"
    else:
        typing_tool = "xdotool" if which("xdotool") else None
        wanted = ["xdotool", "xclip"]
        fallback_tool = "xclip"
    missing = tuple(t for t in wanted if not which(t))
    ok = typing_tool is not None
    fallback_ok = bool(which(fallback_tool))
    hint = install_hint(missing) if missing else ""
    return CapabilityReport(ok=ok, fallback_ok=fallback_ok,
                            session_type=info.type,
                            typing_tool=typing_tool, missing_tools=missing,
                            install_hint=hint,
                            detail=_capability_detail(ok, fallback_ok,
                                                      typing_tool, missing,
                                                      hint))


_capability_cache: dict[tuple, CapabilityReport] = {}


def capability_status(cfg: dict | None = None, *,
                      refresh: bool = False) -> CapabilityReport:
    """Insertion-time pre-flight, cached per (session type, desktop, tool
    preference): the first insert_text call warms it, failures are
    enriched from it (missing tools + install command). refresh=True
    recomputes (startup, doctor, after an install)."""
    info = session_mod.current()
    pref = str(((cfg or {}).get("insertion") or {}).get("wayland_tool",
                                                        "auto"))
    key = (info.type, info.desktop_all, pref)
    if refresh:
        _capability_cache.pop(key, None)
    report = _capability_cache.get(key)
    if report is None:
        report = check_insertion_capability(cfg, info=info)
        _capability_cache[key] = report
    return report


def reset_capability_cache() -> None:
    """Forget cached reports (tests; a daemon re-check after an install)."""
    _capability_cache.clear()


def startup_capability_check(cfg: dict | None = None, *,
                             on_issue: Callable[[CapabilityReport], None] | None = None
                             ) -> CapabilityReport:
    """Run ONCE at daemon start (F-05 fix (a)): refresh the cache and hand
    the report to on_issue when primary insertion is unavailable, so the
    problem is announced at startup - not at the first stranded insert."""
    report = capability_status(cfg, refresh=True)
    if not report.ok and on_issue is not None:
        try:
            on_issue(report)
        except Exception:
            pass
    return report


# ---------------------------------------------------------------------------
# Paste timing constants (module-level, read at call time so tests can
# monkeypatch them; live-observed on a GNOME/X11 desktop running CopyQ 7.1.0
# + the clipboard-indicator GNOME extension - see docs/STATUS.md).
# ---------------------------------------------------------------------------
PASTE_QUIESCE_S = 0.25          # eager readers land here (observed +0.00..0.01)
PASTE_VERIFY_TIMEOUT_S = 0.60   # post-keystroke read cap
PASTE_POLL_INTERVAL_S = 0.025   # granularity of selection-event waits
RESTORE_SETTLE_S = 0.12         # xclip fork serve latency after a restore write
LEGACY_SETTLE_S = 0.25          # today's fixed sleep (insertion.verify_paste = false)
VERIFY_LADDER_S = (0.10, 0.20, 0.30)  # fallback ladder when ownership is unavailable
RESTORE_VERIFY_RETRIES = 1
# Clipboard-manager hygiene markers advertised alongside the dictation text
# while we own the selection: x-kde-passwordManagerHint is the Klipper/
# GPaste/KeePassXC convention; the two application/x-copyq-* markers are
# honored by CopyQ 7.1.0 (live-verified: the item is NOT stored). The GNOME
# shell clipboard-indicator ignores all of them - residual, documented.
HYGIENE_TARGETS = (
    ("x-kde-passwordManagerHint", b"secret"),
    ("application/x-copyq-secret", b"1"),
    ("application/x-copyq-hidden", b"1"),
)

# Wayland paste settle: the X11 read-observation (watch the target read the
# selection we own) CANNOT be replicated on Wayland - no client can observe
# another client's selection reads - so paste verification degrades to this
# fixed delay. Documented divergence; doctor repeats it.
WAYLAND_PASTE_SETTLE_S = 0.45
# wl-copy forks and serves the clipboard (like xclip); same settle need.
WAYLAND_CLIPBOARD_SETTLE_S = 0.15

# spec token -> linux key code for `ydotool key <code>:<state>`. Covers every
# spec the codebase can emit (paste keys, spoken-send, cancel) plus the
# common extras; unknown tokens fail LOUDLY (InsertError) instead of sending
# a wrong keystroke. Single source of truth for the wayland key mapping.
LINUX_KEY_CODES = {
    "ctrl": 29, "shift": 42, "alt": 56, "super": 125,
    "enter": 28, "return": 28, "esc": 1, "escape": 1, "tab": 15,
    "space": 57, "backspace": 14,
    "a": 30, "c": 46, "v": 47, "x": 45,
}


def _display_active() -> bool:
    return bool(os.environ.get("DISPLAY"))


def _run(args: list[str], timeout: float = 15.0, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, input=stdin, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise ToolMissing(args[0]) from None


def active_window_class() -> str | None:
    """WM_CLASS (or title) of the active window - the punctuation app hint.
    Always None on Wayland: xdotool under Xwayland would report some X11
    window's class while the real focus is elsewhere (misleading)."""
    if session_mod.current().is_wayland:
        return None
    if not (shutil.which("xdotool") and _display_active()):
        return None
    try:
        wid = _run(["xdotool", "getactivewindow"], timeout=3).stdout.decode().strip()
        if not wid:
            return None
        if shutil.which("xprop"):
            out = _run(["xprop", "-id", wid, "WM_CLASS"], timeout=3).stdout.decode()
            for part in reversed(re.findall(r'"([^"]*)"', out)):  # class is last
                if part:
                    return part
        name = _run(["xdotool", "getwindowname", wid], timeout=3).stdout.decode().strip()
        return name or None
    except Exception:
        return None


def insert_typed(text: str, delay_ms: int, *, tool: str | None = None) -> None:
    """Simulate typing. tool=None keeps today's xdotool path; "wtype"/
    "ydotool" are the Wayland tools (same leading-dash hazard: xdotool and
    wtype both parse leading '-' as an option, so such texts must paste)."""
    if text.startswith("-"):
        raise InsertError("text starts with '-' needs paste mode")
    if tool is None:
        cmd = ["xdotool", "type", "--delay", str(max(0, delay_ms)),
               "--clearmodifiers", text]
        name = "xdotool"
    elif tool == "wtype":
        cmd = _wtype_type_cmd(text, delay_ms)
        name = "wtype"
    elif tool == "ydotool":
        cmd = _ydotool_type_cmd(text, delay_ms)
        name = "ydotool"
    else:
        raise InsertError(f"unknown insertion tool: {tool!r}")
    proc = _run(cmd)
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace")[:200]
        raise InsertionFailure(f"{name} type failed: {stderr}",
                               kind=_tool_failure_kind(stderr))


def _clipboard_read() -> bytes | None:
    proc = _run(["xclip", "-o", "-selection", "clipboard"], timeout=5)
    if proc.returncode == 0:
        return proc.stdout
    return None  # empty clipboard


def _clipboard_snapshot() -> tuple[bytes | None, bool]:
    """The pre-paste clipboard as (bytes | None, is_text). is_text comes
    from one TARGETS probe (UTF8_STRING/text/plain/STRING) - a non-text
    previous (e.g. an image) is restored blind: never fail an insert over
    a clipboard we cannot read back."""
    previous = _clipboard_read()
    if previous is None:
        return None, False
    proc = _run(["xclip", "-o", "-selection", "clipboard", "-t", "TARGETS"],
                timeout=5)
    targets = proc.stdout.decode(errors="replace") if proc.returncode == 0 else ""
    is_text = any(t in targets for t in ("UTF8_STRING", "text/plain", "STRING"))
    return previous, is_text


def _make_hold(data: bytes, hygiene=HYGIENE_TARGETS):
    """A SelectionHold serving the paste payload + hygiene markers, or
    None when selection ownership is unavailable (no DISPLAY, X error,
    oversized payload) - the caller falls back to the legacy xclip flash."""
    if not _display_active():
        return None
    try:
        from .selection import SelectionHold, SelectionUnavailable
        return SelectionHold(data, hygiene)
    except SelectionUnavailable:
        return None
    except Exception:  # noqa: BLE001 - never let verification break pasting
        return None


def _clipboard_write(data: bytes) -> None:
    # xclip forks and serves the selection; give it a moment then detach.
    subprocess.Popen(["xclip", "-selection", "clipboard"],
                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL).communicate(data)
    time.sleep(0.15)


# ---------------------------------------------------------------------------
# Wayland insertion (additive): tool resolution + command builders, the
# single source of truth for every external-tool argv so a live-verified
# flag fix is a one-line change.
# ---------------------------------------------------------------------------

def _resolve_wayland_tool(cfg: dict,
                          which: Callable[[str], str | None] | None = None
                          ) -> tuple[str | None, str]:
    """insertion.wayland_tool (auto|wtype|ydotool) -> (tool, reason), via
    the shared resolver (GNOME excludes wtype in auto; missing tools fall
    through). The reason feeds doctor, not a user notification."""
    info = session_mod.current()
    pref = str((cfg.get("insertion", {}) or {}).get("wayland_tool", "auto"))
    return session_mod.resolve_wayland_tool(pref, info.desktop_all, which)


def _wtype_type_cmd(text: str, delay_ms: int) -> list[str]:
    return ["wtype", "-d", str(max(0, delay_ms)), text]


def _wtype_key_cmd(spec: str) -> list[str]:
    # wtype -k takes 'ctrl+v'-style combos (xkb keysym names)
    return ["wtype", "-k", spec]


def _ydotool_type_cmd(text: str, delay_ms: int) -> list[str]:
    return ["ydotool", "type", "-d", str(max(0, delay_ms)), text]


def _ydotool_key_cmd(spec: str) -> list[str]:
    """'ctrl+v' -> ydotool key 29:1 47:1 47:0 29:0 (mods press, key tap,
    mods release). Unknown tokens raise instead of mistyping."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise InsertError(f"empty key spec for ydotool: {spec!r}")
    codes = []
    for p in parts:
        if p not in LINUX_KEY_CODES:
            raise InsertError(f"no ydotool key code for {p!r} (spec {spec!r})")
        codes.append(LINUX_KEY_CODES[p])
    mods, key = codes[:-1], codes[-1]
    events = ([f"{m}:1" for m in mods] + [f"{key}:1", f"{key}:0"]
              + [f"{m}:0" for m in reversed(mods)])
    return ["ydotool", "key"] + events


def _key_cmd(tool: str | None, spec: str) -> list[str]:
    """Dispatch one key combo to the resolved wayland tool."""
    if tool == "wtype":
        return _wtype_key_cmd(spec)
    if tool == "ydotool":
        return _ydotool_key_cmd(spec)
    raise InsertError("no wayland typing tool for the keystroke "
                      "(install wtype or ydotool)")


def _wl_copy_args(type_: str | None = None) -> list[str]:
    return ["wl-copy"] + (["--type", type_] if type_ else [])


def _wl_paste_args() -> list[str]:
    return ["wl-paste", "--no-newline"]


def _wl_paste_types_args() -> list[str]:
    return ["wl-paste", "--list-types"]


def _wl_clipboard_write(data: bytes, type_: str | None = None) -> None:
    # wl-copy forks and serves (like xclip); same settle discipline
    subprocess.Popen(_wl_copy_args(type_),
                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL).communicate(data)
    time.sleep(WAYLAND_CLIPBOARD_SETTLE_S)


def _wl_clipboard_read() -> bytes | None:
    proc = _run(_wl_paste_args(), timeout=5)
    if proc.returncode == 0:
        return proc.stdout
    return None  # empty clipboard


def _wl_clipboard_snapshot() -> tuple[bytes | None, str | None]:
    """The pre-paste wayland clipboard as (bytes | None, mime). The mime
    comes from one `wl-paste --list-types` probe so a restore can put the
    ORIGINAL type back (text/plain;charset=utf-8, image/png, ...).
    Mirrors _clipboard_snapshot semantics: never fail an insert over a
    clipboard we cannot read back."""
    previous = _wl_clipboard_read()
    if previous is None:
        return None, None
    proc = _run(_wl_paste_types_args(), timeout=5)
    types = proc.stdout.decode(errors="replace") if proc.returncode == 0 else ""
    mime = next((t.strip() for t in types.splitlines()
                 if t.strip()), None)
    return previous, mime


def _wl_restore_clipboard(previous: bytes | None, mime: str | None,
                          on_notice: Callable[[str], None] | None) -> None:
    """Best-effort blind restore via wl-copy (with the original mime
    type). Never raises - a failed restore warns through on_notice (the
    paste already landed; raising would double-insert on a retry). No
    hygiene-marker support on wayland (documented divergence: clipboard
    managers will see the dictation flash)."""
    if previous is None:
        return  # clipboard was empty before the paste
    try:
        _wl_clipboard_write(previous, mime)
    except Exception:
        if on_notice is not None:
            on_notice("Clipboard restore failed - "
                      "your previous clipboard may be lost")


def _insert_paste_wayland(text: str, *, key: str = "ctrl+v",
                          tool: str | None = None,
                          on_notice: Callable[[str], None] | None = None) -> None:
    """Wayland clipboard paste: wl-copy the text, keystroke via the typing
    tool, fixed settle, wl-copy restore.

    Two deliberate divergences from the X11 verified-paste design (see
    docs/STATUS.md): the paste CANNOT be verified by observing the target
    read the selection (no cross-client observation on Wayland) - a fixed
    delay (WAYLAND_PASTE_SETTLE_S) replaces the read-observation - and no
    clipboard-manager hygiene markers can be advertised while we hold the
    selection, so managers will see the dictation flash."""
    if not (shutil.which("wl-copy") and shutil.which("wl-paste")):
        raise InsertionFailure("wl-clipboard is required for paste mode on "
                               "wayland (install wl-clipboard)",
                               kind=FailureKind.TOOL_MISSING,
                               missing_tools=("wl-copy", "wl-paste"))
    if tool is None:
        raise InsertionFailure("no wayland typing tool for the paste keystroke "
                               "(install wtype or ydotool)",
                               kind=FailureKind.TOOL_MISSING,
                               missing_tools=("wtype", "ydotool"))
    data = text.encode()
    previous, mime = _wl_clipboard_snapshot()
    try:
        try:
            _wl_clipboard_write(data)
        except Exception as e:  # spawn failures surface as InsertError so
            # the auto-mode ladder can fall through to typed insertion
            raise InsertionFailure(f"wl-copy failed: {e}",
                                   kind=FailureKind.TOOL_FAILED) from e
        proc = _run(_key_cmd(tool, key), timeout=10)
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace")[:200]
            raise InsertionFailure(f"paste keystroke failed: {stderr}",
                                   kind=_tool_failure_kind(stderr))
        time.sleep(WAYLAND_PASTE_SETTLE_S)  # verification impossible: settle
    finally:
        _wl_restore_clipboard(previous, mime, on_notice)


def _restore_clipboard(previous: bytes | None, prev_is_text: bool, *,
                       verify_text: bool, skip: bool,
                       on_notice: Callable[[str], None] | None) -> None:
    """Put the pre-paste clipboard bytes back. Blind unless verify_text
    (read-back compared, one retry). Never raises - an unverifiable restore
    warns via on_notice instead (the paste already landed; raising would
    make insert_text re-type and double-insert)."""
    if previous is None:
        return  # clipboard was empty before the paste
    if skip:
        return  # another client owns the clipboard now: their content wins
    for _attempt in range(1 + (RESTORE_VERIFY_RETRIES if verify_text else 0)):
        try:
            _clipboard_write(previous)
        except Exception:
            return  # restore itself failed; swallowed (today's behavior)
        time.sleep(RESTORE_SETTLE_S)
        if not verify_text or _clipboard_read() == previous:
            return
    if on_notice is not None:
        on_notice("Clipboard restore could not be verified - "
                  "your previous clipboard may be lost")


def insert_paste(text: str, *, key: str = "ctrl+v", verify: bool = True,
                 on_notice: Callable[[str], None] | None = None) -> None:
    """Clipboard paste with verify-then-restore.

    verify=True (insertion.verify_paste) owns the CLIPBOARD selection for
    the duration (python-xlib) instead of a blind xclip flash: hygiene
    marker targets are advertised so clipboard managers suppress the
    dictation, the paste keystroke is verified by observing the target
    read the selection, and only then is the previous clipboard restored
    (read-back checked, one retry). An unverified paste raises InsertError
    AFTER the restore so insert_text can fall back to typed insertion.
    verify=False keeps today's behavior exactly (fixed sleep + blind
    restore; terminal key still honored).
    """
    if not shutil.which("xclip"):
        raise InsertionFailure("xclip is required for paste mode "
                               "(sudo apt install xclip)",
                               kind=FailureKind.TOOL_MISSING,
                               missing_tools=("xclip",))
    data = text.encode()
    previous, prev_is_text = _clipboard_snapshot()
    hold = _make_hold(data) if verify else None
    verified = False
    try:
        if hold is not None:
            known = hold.quiesce(PASTE_QUIESCE_S, interval=PASTE_POLL_INTERVAL_S)
        else:
            _clipboard_write(data)  # legacy flash (managers snapshot it)
        proc = _run(["xdotool", "key", "--clearmodifiers", key], timeout=10)
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace")[:200]
            raise InsertionFailure(f"paste keystroke failed: {stderr}",
                                   kind=_tool_failure_kind(stderr))
        if hold is not None:
            # ICCCM: while we own the selection, every read is a
            # SelectionRequest naming its requestor window - a window not
            # seen during the quiesce reading AFTER the keystroke means the
            # target app took the clipboard = the paste landed.
            verified = hold.wait_read(PASTE_VERIFY_TIMEOUT_S,
                                      exclude_windows=known,
                                      interval=PASTE_POLL_INTERVAL_S) is not None
        elif verify:
            for settle in VERIFY_LADDER_S:
                time.sleep(settle)
        else:
            time.sleep(LEGACY_SETTLE_S)
    finally:
        skip_restore = False
        if hold is not None:
            skip_restore = hold.lost_ownership  # user's fresh copy wins
            try:
                hold.release()
            except Exception:
                pass
        _restore_clipboard(previous, prev_is_text,
                           verify_text=hold is not None and prev_is_text,
                           skip=skip_restore, on_notice=on_notice)
    if hold is not None and not verified:
        err = InsertionFailure("paste not verified: target did not read "
                               "the clipboard",
                               kind=FailureKind.PASTE_UNVERIFIED)
        err.not_verified = True  # type: ignore[attr-defined]
        raise err


def is_terminal_app(wm_class: str | None, cfg: dict) -> bool:
    """True when the app identity matches a terminal: a canonical
    profiles.rules match first (P2), then the legacy general.terminal_apps
    list (still read until v1.0 - identical to the pre-P2 check)."""
    from .profiles import resolve_profile
    return resolve_profile(cfg, wm_class).terminal


def _effective_insertion_mode(cfg: dict, wm: str | None) -> str:
    """insertion.mode with the per-app profile override (P2): a canonical
    rule carrying insertion_mode (typed|paste|auto) wins for its app;
    everything else inherits the global setting exactly as before."""
    mode = cfg["insertion"]["mode"]
    if wm:
        from .profiles import resolve_profile
        override = resolve_profile(cfg, wm).insertion_mode
        if override:
            return override
    return mode


def terminal_trailing_space(text: str) -> str:
    """One trailing space iff the text ends in a word character, so a
    terminal's autocomplete commits the last token (Linux adaptation —
    upstream strips trailing spaces in chat apps instead, :236-261).
    Idempotent: space/punctuation-ending and empty texts are unchanged."""
    if not text or not re.search(r"\w$", text):
        return text
    return text + " "


def _preserve_via_clipboard(text: str) -> ClipboardResult:
    """Automatic copy-to-clipboard preservation on failure (F-05): the
    text must survive somewhere even when insertion is impossible."""
    try:
        result = copy_to_clipboard(text)  # never raises (honest result)
    except Exception as e:  # noqa: BLE001 - belt and braces
        return ClipboardResult(ClipboardOutcome.WRITE_FAILED, None, str(e))
    # stubs/tests may monkeypatch copy_to_clipboard with a None-returning
    # fake; treat a non-ClipboardResult as a successful write
    return result if isinstance(result, ClipboardResult) \
        else ClipboardResult(ClipboardOutcome.WRITTEN, "clipboard")


def _failure_message(base: str, kind: FailureKind,
                     report: CapabilityReport,
                     clip: ClipboardResult | None) -> str:
    parts = [base]
    if report.missing_tools and report.install_hint and kind in (
            FailureKind.TOOL_MISSING, FailureKind.NO_BACKEND):
        parts.append(f"Fix: {report.install_hint}")
    if clip is not None:
        if clip.written:
            parts.append("Text copied to the clipboard - paste it with Ctrl+V")
        else:
            parts.append("Clipboard copy failed too - "
                         "your text is saved in History")
    return " - ".join(parts)


def _fail_insert(exc: InsertError, *, text: str, report: CapabilityReport,
                 on_notice: Callable[[str], None] | None = None,
                 on_result: Callable[[InsertResult], None] | None = None,
                 clipboard: ClipboardResult | None = None,
                 preserve: bool = True) -> NoReturn:
    """Convert any insertion failure into the honest, structured,
    machine-readable terminal state (F-05/F-18): classify, preserve the
    text on the clipboard when possible (History otherwise - the pipeline
    records the take regardless), notify the actionable message, emit the
    InsertResult, raise InsertionFailure carrying it."""
    if isinstance(exc, InsertionFailure) and exc.kind is not None:
        kind = exc.kind
    elif isinstance(exc, ToolMissing):
        kind = FailureKind.TOOL_MISSING
    else:
        kind = FailureKind.TOOL_FAILED
    clip = (clipboard if clipboard is not None or not preserve
            else _preserve_via_clipboard(text))
    message = _failure_message(str(exc), kind, report, clip)
    hint = report.install_hint if report.missing_tools else ""
    result = InsertResult(ok=False, strategy="failed", failure=kind,
                          message=message, install_hint=hint,
                          clipboard=clip,
                          missing_tools=report.missing_tools)
    if on_notice is not None:
        try:
            on_notice(message)
        except Exception:
            pass
    _emit_result(result, on_result)
    failure = InsertionFailure(message, kind=kind, result=result,
                               missing_tools=report.missing_tools,
                               install_hint=hint)
    if getattr(exc, "not_verified", False):
        failure.not_verified = True  # type: ignore[attr-defined]
    raise failure from exc


def _ok_result(strategy: str, report: CapabilityReport,
               on_result: Callable[[InsertResult], None] | None) -> None:
    """Emit the machine-readable success result for one insert_text."""
    _emit_result(InsertResult(ok=True, strategy=strategy,
                              missing_tools=report.missing_tools),
                 on_result)


def insert_text(text: str, cfg: dict, wm_class: str | None = None,
                on_notice: Callable[[str], None] | None = None,
                on_result: Callable[[InsertResult], None] | None = None
                ) -> str:
    """Insert `text` at the caret. Returns the strategy used.

    wm_class: the insertion target's app identity (None -> live lookup).
    In terminal apps (profiles.rules first, then general.terminal_apps)
    pastes use ctrl+shift+v (insertion.terminal_paste_key - X11 terminals
    pass ctrl+v to the app) and typed insertions ending in a word
    character gain one trailing space (insertion.terminal_autocomplete_space)
    so autocomplete commits; the space is typing-only - clipboard copy and
    history keep the text without it. A canonical profile may override the
    insertion mode for its app (P2). on_notice surfaces paste-fallback /
    restore warnings and, on terminal failure, the actionable error.
    on_result receives the machine-readable InsertResult (also broadcast
    to add_result_listener subscribers). On failure an InsertionFailure
    carrying the same result is raised AFTER an automatic clipboard
    preservation attempt - the text is on the clipboard when possible and
    always remains in History (F-05/F-18).

    Wayland sessions route to _insert_text_wayland (wm_class is None there
    UNLESS the P2 context seam supplied an identity at insertion time;
    without one, terminal quirks are inert, documented divergence)."""
    if session_mod.current().is_wayland:
        return _insert_text_wayland(text, cfg, wm_class=wm_class,
                                    on_notice=on_notice,
                                    on_result=on_result)
    return _insert_text_x11(text, cfg, wm_class=wm_class,
                            on_notice=on_notice, on_result=on_result)


def _insert_text_x11(text: str, cfg: dict,
                     wm_class: str | None = None,
                     on_notice: Callable[[str], None] | None = None,
                     on_result: Callable[[InsertResult], None] | None = None
                     ) -> str:
    """X11/unknown insert: mode/threshold/leading-dash routing over the
    xdotool/xclip backends, with the honest-failure terminal state.
    """
    report = capability_status(cfg)  # pre-flight (cached): enrich failures
    wm = active_window_class() if wm_class is None else wm_class
    terminal = bool(wm and is_terminal_app(wm, cfg))
    mode = _effective_insertion_mode(cfg, wm)
    threshold = cfg["insertion"].get("paste_threshold_chars", 1200)
    delay = cfg["insertion"].get("type_delay_ms", 8)
    use_paste = (mode == "paste" or len(text) > threshold or text.startswith("-"))
    if use_paste:
        key = (cfg["insertion"].get("terminal_paste_key", "ctrl+shift+v")
               if terminal else "ctrl+v")
        try:
            insert_paste(text, key=key,
                         verify=cfg["insertion"].get("verify_paste", True),
                         on_notice=on_notice)
            _ok_result("paste", report, on_result)
            return "paste"
        except InsertError as e:
            if mode == "paste":
                _fail_insert(e, text=text, report=report,
                             on_notice=on_notice, on_result=on_result)
            if getattr(e, "not_verified", False) and on_notice is not None:
                on_notice("Paste did not land - typing instead")
    if cfg["insertion"].get("terminal_autocomplete_space", True):
        if terminal:
            text = terminal_trailing_space(text)
    try:
        insert_typed(text, delay)
    except InsertError as e:
        _fail_insert(e, text=text, report=report,
                     on_notice=on_notice, on_result=on_result)
    _ok_result("typed", report, on_result)
    return "typed"


def _insert_text_wayland(text: str, cfg: dict,
                        wm_class: str | None = None,
                        on_notice: Callable[[str], None] | None = None,
                        on_result: Callable[[InsertResult], None] | None = None
                        ) -> str:
    """Wayland insert: the same mode/threshold/leading-dash routing as the
    X11 body, over the wtype/ydotool + wl-clipboard backends.

    Degradation ladder (each step only when the previous is impossible):
      tool+typed -> tool+wl-clipboard paste -> wl-copy + "paste manually"
      notice ("clipboard-fallback") -> honest structured InsertionFailure
      (the pipeline notifies; history still records the take). Terminal
    quirks apply ONLY when an app identity was supplied (P2: the context
    seam passes the AT-SPI identity at insertion time); wm_class None
    keeps today's behavior - without an identity ctrl+shift+v and the
    autocomplete space stay off (a wrong plain ctrl+v in a terminal is
    recoverable, a mistyped terminal-paste is not)."""
    report = capability_status(cfg)  # pre-flight (cached): enrich failures
    wm = wm_class
    terminal = bool(wm and is_terminal_app(wm, cfg))
    mode = _effective_insertion_mode(cfg, wm)
    threshold = cfg["insertion"].get("paste_threshold_chars", 1200)
    delay = cfg["insertion"].get("type_delay_ms", 8)
    tool, _reason = _resolve_wayland_tool(cfg)
    use_paste = (mode == "paste" or len(text) > threshold or text.startswith("-"))
    if use_paste:
        key = (cfg["insertion"].get("terminal_paste_key", "ctrl+shift+v")
               if terminal else "ctrl+v")
        try:
            _insert_paste_wayland(text, key=key, tool=tool,
                                  on_notice=on_notice)
            _ok_result("paste", report, on_result)
            return "paste"
        except InsertError as e:
            if mode == "paste":
                _fail_insert(e, text=text, report=report,
                             on_notice=on_notice, on_result=on_result)
            if on_notice is not None:
                on_notice("Paste did not land - trying to type instead")
    if tool is not None:
        try:
            if cfg["insertion"].get("terminal_autocomplete_space", True) \
                    and terminal:
                text = terminal_trailing_space(text)
            insert_typed(text, delay, tool=tool)
            _ok_result("typed", report, on_result)
            return "typed"
        except InsertError:
            if on_notice is not None:
                on_notice("Typing failed - falling back to the clipboard")
    clip: ClipboardResult | None = None
    if shutil.which("wl-copy"):
        try:
            clip = copy_to_clipboard(text, wayland=True)
        except Exception as e:  # noqa: BLE001 - report, don't raise
            clip = ClipboardResult(ClipboardOutcome.WRITE_FAILED, "wl-copy",
                                   str(e))
        if clip.written:
            if on_notice is not None:
                on_notice("Copied to clipboard - paste manually (install "
                          "wtype or ydotool to type automatically)")
            _ok_result("clipboard-fallback", report, on_result)
            return "clipboard-fallback"
    else:
        clip = ClipboardResult(ClipboardOutcome.TOOL_MISSING, "wl-copy")
    _fail_insert(
        InsertionFailure("no wayland insertion tool available - install "
                         "wtype or ydotool (plus wl-clipboard for paste "
                         "mode)", kind=FailureKind.NO_BACKEND),
        text=text, report=report, on_notice=on_notice,
        on_result=on_result, clipboard=clip, preserve=False)


def clipboard_fallback(text: str) -> ClipboardResult:
    """Last resort when neither typing nor pasting worked: leave text on
    the clipboard and return the honest outcome (F-18) - callers that
    ignore the return value keep today's behavior."""
    return copy_to_clipboard(text)  # auto-detects the session


def press_key(spec: str, *, tool: str | None = None) -> None:
    """Press a key combo (e.g. 'enter', 'shift+enter') in the focused window.
    tool=None keeps today's xdotool path on X11/unknown sessions and
    auto-resolves the wayland tool on a wayland session (no cfg at this
    call depth -> the "auto" preference; spoken-send, paste-last and
    command-mode reruns get wayland for free)."""
    if tool is None and session_mod.current().is_wayland:
        tool, _reason = _resolve_wayland_tool({})
        if tool is None:
            raise InsertError("no wayland typing tool for the keystroke "
                              "(install wtype or ydotool)")
    if tool is None:
        cmd = ["xdotool", "key", "--clearmodifiers", spec]
        name = "xdotool"
    else:
        cmd = _key_cmd(tool, spec)
        name = tool
    proc = _run(cmd, timeout=5)
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace")[:200]
        raise InsertionFailure(f"{name} key press failed: {stderr}",
                               kind=_tool_failure_kind(stderr))


def copy_to_clipboard(text: str, *, wayland: bool | None = None) -> ClipboardResult:
    """Put text on the clipboard without typing (upstream
    copyTranscriptionToClipboard). Returns the honest outcome (F-18):
    WRITTEN, TOOL_MISSING or WRITE_FAILED - never raises, so callers that
    ignore the result keep today's silent behavior, while the failure
    paths can tell the truth ("saved in History", not "copied").
    wayland=None auto-detects via the session probe (the daemon's
    always-copy call sites stay session-correct); True/False force it."""
    if wayland is None:
        wayland = session_mod.current().is_wayland
    if wayland:
        if not shutil.which("wl-copy"):
            return ClipboardResult(ClipboardOutcome.TOOL_MISSING, "wl-copy")
        try:
            _wl_clipboard_write(text.encode())
        except Exception as e:  # noqa: BLE001 - report, don't raise
            return ClipboardResult(ClipboardOutcome.WRITE_FAILED, "wl-copy",
                                   str(e))
        return ClipboardResult(ClipboardOutcome.WRITTEN, "wl-copy")
    if not shutil.which("xclip"):
        return ClipboardResult(ClipboardOutcome.TOOL_MISSING, "xclip")
    try:
        _clipboard_write(text.encode())
    except Exception as e:  # noqa: BLE001 - report, don't raise
        return ClipboardResult(ClipboardOutcome.WRITE_FAILED, "xclip", str(e))
    return ClipboardResult(ClipboardOutcome.WRITTEN, "xclip")
