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
# source of truth for the typing-tool order).
WAYLAND_TYPE_TOOLS = ("wtype", "ydotool")

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
                         virtual_keyboard: bool | None = None
                         ) -> tuple[str | None, str]:
    """Pick the text-insertion tool for a Wayland session.

    preference: insertion.wayland_tool ("auto" | "wtype" | "ydotool").
    virtual_keyboard is the RUNTIME protocol probe verdict (does the
    compositor advertise zwp_virtual_keyboard_manager_v1, see
    wayland_probe.probe_virtual_keyboard): True -> wtype eligible on
    any desktop, False -> wtype skipped with the honest reason. None =
    no probe was run; the old desktop-name heuristic then applies as a
    documented guess (back-compat for pure calls; the daemon and
    insertion always pass a probe result).

    A missing preferred tool falls through to the other one
    (wtype -> ydotool -> None). Returns (tool, reason); the reason
    feeds doctor and the insertion-time notice.
    """
    if which is None:
        which = shutil.which
    pref = (preference or "auto").strip().lower()
    if pref not in WAYLAND_TYPE_TOOLS:
        pref = "auto"
    if pref == "auto":
        candidates = list(WAYLAND_TYPE_TOOLS)  # wtype first, ydotool behind
    else:
        candidates = [pref] + [t for t in WAYLAND_TYPE_TOOLS if t != pref]
    unprobed_gnome = virtual_keyboard is None and is_gnome_desktop(desktop)
    notes: list[str] = []
    for tool in candidates:
        if tool == "wtype" and pref != "wtype":
            # an EXPLICIT wtype choice always overrides (the user may
            # know better than the probe); auto is probe-gated
            if virtual_keyboard is False:
                notes.append("wtype skipped: compositor does not "
                             "advertise zwp_virtual_keyboard_manager_v1 "
                             "(runtime probe)")
                continue
            if unprobed_gnome:
                # no probe ran (pure call): keep the documented guess
                notes.append("wtype skipped: GNOME has no virtual-keyboard "
                             "protocol")
                continue
        if which(tool):
            if tool == "wtype":
                if virtual_keyboard is False:
                    notes.append("wtype does NOT work here "
                                 "(no virtual-keyboard protocol) - "
                                 "consider ydotool")
                elif unprobed_gnome:
                    notes.append("wtype does NOT work on GNOME "
                                 "(no virtual-keyboard protocol) - consider "
                                 "ydotool")
            if tool != pref and pref in WAYLAND_TYPE_TOOLS:
                notes.append(f"preferred tool '{pref}' not found, "
                             f"using {tool}")
            return tool, ("; ".join(notes) if notes else "")
        notes.append(f"{tool} not found")
    return None, "; ".join(notes) or "no wayland typing tool found"


def capabilities(info: SessionInfo,
                 which: Callable[[str], str | None] | None = None,
                 cfg: Mapping | None = None, *,
                 virtual_keyboard: bool | None = None,
                 eis_available: bool | None = None,
                 portal_hotkey: bool | None = None) -> dict[str, str]:
    """Per-capability backend resolution — one entry per capability, the
    value naming the resolved backend. Pure function of (info, which,
    cfg, probe verdicts) so daemon, doctor and the settings page share
    one truth.

    Probe verdicts (None = not probed, the honest back-compat default):
    virtual_keyboard gates wtype (zwp registry probe), eis_available
    promotes the RemoteDesktop/EIS portal path over the external tools,
    portal_hotkey marks the GlobalShortcuts portal as the hotkey backend
    (else the DE-shortcut fallback). Callers that HAVE a wayland session
    should pass the live verdicts (daemon, doctor); pure/tests may omit.

    Capabilities: hotkey, insertion, clipboard, overlay, preview, tray,
    app-hint. Values: see the README "Wayland support" matrix.
    """
    if which is None:
        which = shutil.which
    ins_cfg = (cfg or {}).get("insertion", {})
    wl_clipboard = bool(which("wl-copy")) and bool(which("wl-paste"))
    caps: dict[str, str] = {}
    if info.is_wayland:
        caps["hotkey"] = ("portal-shortcut" if portal_hotkey
                          else "de-shortcut")
        pref = str(ins_cfg.get("wayland_tool", "auto"))
        if eis_available and pref in ("auto", "eis"):
            caps["insertion"] = "eis"
        else:
            tool, _reason = resolve_wayland_tool(
                pref, info.desktop_all, which,
                virtual_keyboard=virtual_keyboard)
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
