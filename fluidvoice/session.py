"""Session-type probing + per-capability backend resolution (Wayland v0.3).

Wayland support is strictly additive: every X11 code path stays intact and
is gated ONLY on the session probe (never on "xdotool missing" or "DISPLAY
unset" — headless CI relies on the xdotool paths being taken whenever the
session is x11/unknown).

Probe precedence (the invariant the whole port rides on):

  1. explicit ``XDG_SESSION_TYPE`` ("wayland" / "x11" wins over everything;
     any other value — e.g. "tty" — is not decisive and falls through)
  2. ``WAYLAND_DISPLAY`` set -> wayland
  3. ``DISPLAY`` set -> x11
  4. neither -> unknown (behaves exactly like today: legacy X11 attempt)

This tolerates the systemd user unit's baked ``Environment=DISPLAY`` line
carrying a stale DISPLAY into a Wayland login: XDG_SESSION_TYPE /
WAYLAND_DISPLAY outrank it.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

# Tools each capability may resolve to (one list, no duplication between
# capabilities() and insertion.py — resolve_wayland_tool is the shared
# source of truth for the typing-tool order). "eis" is the
# RemoteDesktop-portal insertion path (ADR-0006): selected only when the
# runtime portal probe passes; wtype only when the runtime registry
# probe sees zwp_virtual_keyboard_manager_v1 (never by desktop name).
WAYLAND_TYPE_TOOLS = ("eis", "wtype", "ydotool")

# Desktop tokens that mean GNOME Shell regardless of distro branding:
# Ubuntu reports "ubuntu:GNOME" (first token "ubuntu"), Pop!_OS "pop:GNOME"
# or plain "pop"; Unity-era sessions kept "unity". All lack the zwp
# virtual-keyboard protocol wtype needs.
GNOME_DESKTOPS = ("gnome", "unity", "ubuntu", "pop")


def is_gnome_desktop(desktop: str) -> bool:
    tokens = [t for t in (desktop or "").lower().replace(";", ":").split(":")
              if t]
    return any("gnome" in t or t in GNOME_DESKTOPS for t in tokens)


@dataclass(frozen=True)
class SessionInfo:
    type: str                # "x11" | "wayland" | "unknown"
    desktop: str             # lower-cased first token of XDG_CURRENT_DESKTOP
    desktop_all: str = ""    # full lower-cased XDG_CURRENT_DESKTOP (matching)
    wayland_display: bool = False  # WAYLAND_DISPLAY set
    x11_display: bool = False      # DISPLAY set

    @property
    def is_wayland(self) -> bool:
        return self.type == "wayland"


def probe(env: Mapping[str, str] | None = None) -> SessionInfo:
    """Resolve the session type from env (defaults to os.environ; tests
    pass a dict). Pure env reading — no I/O."""
    e = dict(os.environ if env is None else env)
    wayland_display = bool((e.get("WAYLAND_DISPLAY") or "").strip())
    x11_display = bool((e.get("DISPLAY") or "").strip())
    explicit = (e.get("XDG_SESSION_TYPE") or "").strip().lower()
    if explicit == "wayland":
        stype = "wayland"
    elif explicit == "x11":
        stype = "x11"
    elif wayland_display:  # explicit value missing/"tty"/other: env probes
        stype = "wayland"
    elif x11_display:
        stype = "x11"
    else:
        stype = "unknown"
    desktop_raw = (e.get("XDG_CURRENT_DESKTOP") or "").strip().lower()
    desktop = desktop_raw.split(":")[0].strip()
    return SessionInfo(type=stype, desktop=desktop, desktop_all=desktop_raw,
                       wayland_display=wayland_display,
                       x11_display=x11_display)


def current() -> SessionInfo:
    """Cheap per-call probe (three env reads; intentionally uncached so
    monkeypatched env wins in tests and subprocess env changes are seen)."""
    return probe()


# ---------------------------------------------------------------------------
# Wayland typing-tool resolution (shared by capabilities(), insertion.py
# and doctor). Pure function of (preference, desktop, which).
# ---------------------------------------------------------------------------

def resolve_wayland_tool(preference: str = "auto", desktop: str = "",
                         which: Callable[[str], str | None] | None = None,
                         *, probes: Mapping | None = None,
                         ) -> tuple[str | None, str]:
    """Pick the text-insertion tool for a Wayland session.

    preference: insertion.wayland_tool ("auto" | "eis" | "wtype" |
    "ydotool"). Selection is decided by RUNTIME PROBES (ADR-0006),
    never by desktop name:

    - "eis" requires the RemoteDesktop portal probe to pass
      (probes["remote_desktop"] is True) plus an importable dbus-next;
    - "wtype" requires the registry probe to see
      zwp_virtual_keyboard_manager_v1
      (probes["virtual_keyboard"] is True). When the probe is
      INDETERMINATE (None), the documented legacy heuristic stays as
      the tiebreak (GNOME-brand sessions lack the protocol);
    - "ydotool" works anywhere it is installed.

    probes=None is the PURE legacy resolution (no eis candidate, the
    old GNOME-name behavior) — the default for one-shot callers and
    the unit tests. An EXPLICIT preference is honored (the user
    override wins, with a warning when its probe says no); a missing
    preferred tool falls through to the other candidates.

    Returns (tool, reason); tool is None when nothing is installed.
    The reason feeds doctor and the insertion-time notice.
    """
    if which is None:
        which = shutil.which
    pref = (preference or "auto").strip().lower()
    if pref not in WAYLAND_TYPE_TOOLS:
        pref = "auto"
    pure = probes is None
    has_eis = False if pure else bool(probes.get("remote_desktop"))
    vkb = None if pure else probes.get("virtual_keyboard")
    if pref == "auto":
        candidates = list(WAYLAND_TYPE_TOOLS)
    else:
        candidates = [pref] + [t for t in WAYLAND_TYPE_TOOLS if t != pref]
    notes: list[str] = []
    pref_skipped_by_probe = False
    for tool in candidates:
        if tool == "eis" and pure:
            continue  # pure legacy resolution never proposes the portal
        if tool == "eis":
            if not has_eis:
                notes.append("eis skipped: RemoteDesktop portal probe "
                             "failed (no ConnectToEIS here)")
                if tool == pref:
                    pref_skipped_by_probe = True
                continue
            if not _eis_importable():
                notes.append("eis skipped: dbus-next not installed")
                if tool == pref:
                    pref_skipped_by_probe = True
                continue
        if tool == "wtype":
            probe_says_no = vkb is False and pref != "wtype"
            heuristic_says_no = (vkb is None
                                 and is_gnome_desktop(desktop)
                                 and pref != "wtype")
            if probe_says_no or heuristic_says_no:
                if probe_says_no:
                    notes.append("wtype skipped: no zwp virtual-keyboard "
                                 "protocol (registry probe)")
                else:
                    notes.append("wtype skipped: GNOME has no "
                                 "virtual-keyboard protocol (registry "
                                 "probe indeterminate)")
                if tool == pref:
                    pref_skipped_by_probe = True
                continue
        available = _eis_importable() if tool == "eis" else bool(which(tool))
        if available:
            if tool == "wtype" and vkb is None \
                    and is_gnome_desktop(desktop):
                notes.append("wtype does NOT work on GNOME "
                             "(no virtual-keyboard protocol) - consider "
                             "ydotool")
            if tool != pref and pref in WAYLAND_TYPE_TOOLS:
                kind = "not usable" if pref_skipped_by_probe else "not found"
                notes.append(f"preferred tool '{pref}' {kind}, "
                             f"using {tool}")
            return tool, ("; ".join(notes) if notes else "")
        notes.append(f"{tool} not found")
    return None, "; ".join(notes) or "no wayland typing tool found"


def _eis_importable() -> bool:
    """The eis path needs dbus_next in-process (vendored in the Flatpak;
    a pip extra on the host). Cheap import probe, cached by Python."""
    try:
        import dbus_next  # noqa: F401
        return True
    except ImportError:
        return False


def capabilities(info: SessionInfo,
                 which: Callable[[str], str | None] | None = None,
                 cfg: Mapping | None = None) -> dict[str, str]:
    """Per-capability backend resolution — one entry per capability, the
    value naming the resolved backend. Pure function of (info, which, cfg)
    so daemon, doctor and the settings page share one truth.

    Capabilities: hotkey, insertion, clipboard, overlay, preview, tray,
    app-hint. Values: see the README "Wayland support" matrix.
    """
    if which is None:
        which = shutil.which
    ins_cfg = (cfg or {}).get("insertion", {})
    wl_clipboard = bool(which("wl-copy")) and bool(which("wl-paste"))
    caps: dict[str, str] = {}
    if info.is_wayland:
        hk_cfg = (cfg or {}).get("hotkey", {})
        portal_client_ok = (_eis_importable()
                            and _global_shortcuts_client_ok())
        if hk_cfg.get("wayland_portal", True) and portal_client_ok:
            caps["hotkey"] = "global-shortcuts-portal"
        else:
            caps["hotkey"] = "de-shortcut"
        probes = _wayland_probes()
        tool, _reason = resolve_wayland_tool(
            str(ins_cfg.get("wayland_tool", "auto")), info.desktop_all,
            which, probes=probes)
        if tool is not None:
            caps["insertion"] = tool
        elif wl_clipboard:
            # no keystroke tool: text can reach the clipboard (wl-copy)
            # but nothing can press ctrl+v - "paste manually" notices
            caps["insertion"] = "wl-clipboard-only"
        else:
            caps["insertion"] = "unavailable"
        caps["clipboard"] = "wl-clipboard" if wl_clipboard else "unavailable"
        caps["overlay"] = "notifications"  # layer-shell pill: future work
        caps["app-hint"] = "unavailable"   # AT-SPI: future work
    else:
        # x11 and unknown behave exactly like today (legacy X11 attempt)
        caps["hotkey"] = "x11-grab"
        caps["insertion"] = "xdotool" if which("xdotool") else "unavailable"
        caps["clipboard"] = "xclip" if which("xclip") else "unavailable"
        caps["overlay"] = "x11-pill"
        caps["app-hint"] = ("xdotool-wmclass" if which("xdotool")
                            else "unavailable")
    caps["preview"] = caps["overlay"]
    caps["tray"] = "sni"  # StatusNotifierItem over D-Bus: server-neutral
    return caps


def _global_shortcuts_client_ok() -> bool:
    """The GlobalShortcuts portal client side can load (the pure-Python
    bindings + dbus-next). Whether a BACKEND exists is only learnable
    by binding - the listener reports that at start()."""
    try:
        import global_shortcut_portal  # noqa: F401
        return True
    except ImportError:
        return False


def _wayland_probes() -> Mapping | None:
    """Runtime protocol probes for capabilities() (cached, never raise).
    None keeps the resolver on its documented legacy tiebreak."""
    try:
        from .input_probes import probe_input_backends
        return {
            "virtual_keyboard": probe_input_backends().virtual_keyboard,
            "remote_desktop": probe_input_backends().remote_desktop,
        }
    except Exception:  # noqa: BLE001 - capabilities must never raise
        return None


# ---------------------------------------------------------------------------
# DE-shortcut assist: the bindable command + per-compositor instructions
# (shared verbatim by doctor and Settings -> Wayland).
# ---------------------------------------------------------------------------

def toggle_command() -> str:
    """The command a DE shortcut should run. Resolution: a sayit-ermano
    binary next to the interpreter -> on PATH -> the module fallback."""
    sibling = Path(sys.executable).parent / "sayit-ermano"
    if sibling.is_file():
        return str(sibling)
    found = shutil.which("sayit-ermano")
    if found:
        return found
    return f"{sys.executable} -m fluidvoice"


def ensure_toggle_script(path: Path | None = None) -> Path | None:
    """Idempotent best-effort write of the bindable wrapper script (the
    command users point their DE custom shortcut at). Never raises; returns
    the script path or None when even the write failed."""
    try:
        from . import paths
        script = path if path is not None else paths.toggle_script()
        cmd = toggle_command()
        body = (f"#!/bin/sh\n"
                f"# Generated by SayItErmano - bind this script to a custom\n"
                f"# shortcut in your desktop environment to toggle dictation.\n"
                f"exec {cmd} toggle\n")
        script.parent.mkdir(parents=True, exist_ok=True)
        if not (script.exists() and script.read_text() == body):
            script.write_text(body)
        script.chmod(0o755)
        return script
    except Exception:
        return None


def de_shortcut_instructions(desktop: str, script_path: str) -> list[str]:
    """Per-compositor custom-shortcut steps for binding the toggle script
    (doctor prints these; Settings -> Wayland shows the same text)."""
    script_path = script_path or "<sayit-ermano-toggle script>"
    if is_gnome_desktop(desktop):
        return [
            "GNOME: Settings -> Keyboard -> View and Customize Shortcuts ->",
            f"  Custom Shortcuts -> Add: Command = {script_path}",
            "  (GNOME has no global-grab API; a custom shortcut is the way)",
        ]
    if any(t in ("kde", "plasma") for t in (desktop or "").lower().split(":")):
        return [
            "KDE Plasma: System Settings -> Shortcuts -> Add New ->",
            f"  Command or Script... -> select {script_path} -> assign a key",
        ]
    if "cosmic" in desktop:
        return [
            "COSMIC: Settings -> Keyboard -> Shortcuts -> Add Custom ->",
            f"  Command: {script_path}",
        ]
    return [
        f"Your desktop: add a custom command shortcut running {script_path}",
        "  (no global hotkey grabs exist on Wayland)",
    ]
