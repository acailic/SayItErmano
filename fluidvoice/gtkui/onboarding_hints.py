"""Actionable hints for the onboarding "It didn't work" report (F-19).

Doctor-style triage of a failed first REAL dictation: per-cause, naming
the exact fix (install command, bind step, retry path). Reuses the same
sources doctor reads - ``session.capabilities`` / ``de_shortcut_instructions``,
``shutil.which`` over doctor's tool list, the daemon ``status`` surface,
``model_download.model_readiness`` - instead of inventing a second
diagnostic truth. No GTK imports (headless-testable).
"""
from __future__ import annotations

import shutil

from .. import model_download, paths
from .. import session as session_mod

# doctor.run()'s tools table, insertion-relevant subset: (tool, why)
TOOLS = (
    ("pw-record", "PipeWire recording (preferred)"),
    ("parecord", "PulseAudio recording (fallback)"),
    ("xdotool", "typing text into apps (X11)"),
    ("xclip", "clipboard paste mode + restore (X11)"),
    ("wtype", "typing text into apps (Wayland; wlroots/KDE - not GNOME)"),
    ("ydotool", "typing on any compositor via uinput (Wayland; needs "
                "ydotoold running + /dev/uinput access)"),
    ("wl-copy", "clipboard on Wayland (wl-clipboard)"),
    ("wl-paste", "clipboard read/restore on Wayland (wl-clipboard)"),
)


def insertion_failure_hints(status: dict | None, cfg: dict,
                            which=None) -> list[str]:
    """Ordered, actionable lines for "my first real dictation did not
    appear". `status` is the daemon status dict (None = daemon down);
    `cfg` the (masked) config; `which` injectable for tests."""
    if which is None:
        which = shutil.which
    out: list[str] = []

    if status is None:
        out.append("the daemon is not answering - restart it "
                   "(systemctl --user restart sayit-ermano) and try again")

    ready = model_download.model_readiness(cfg)
    if ready["kind"] != "remote" and not ready["downloaded"]:
        prog = ready.get("progress")
        if prog is not None:
            out.append(f"the speech model is still downloading - {prog.describe()}; "
                       "wait for it, then dictate again")
        else:
            out.append(f"the speech model ({ready['name']}) is not on disk yet - "
                       "the first dictation downloads it; check the network "
                       "and retry")

    info = session_mod.probe()
    caps = session_mod.capabilities(info, which=which, cfg=cfg)
    if info.is_wayland:
        if caps.get("insertion") == "unavailable":
            out.append("Wayland: no typing tool and no wl-clipboard - "
                       "install ydotool (needs ydotoold running and "
                       "/dev/uinput access) or wl-clipboard for manual "
                       "paste")
        elif caps.get("insertion") == "wl-clipboard-only":
            out.append("Wayland: text reaches the clipboard (wl-copy) but "
                       "nothing can press ctrl+v - install ydotool to type "
                       "into apps, or paste manually after each dictation")
        if status is not None:
            out.append("Wayland has no global hotkey grabs - bind the "
                       "toggle script in your desktop settings, then press "
                       "that key to dictate:")
            out.extend("  " + line for line in session_mod.de_shortcut_instructions(
                info.desktop_all, str(paths.toggle_script())))
    else:
        if not which("xdotool"):
            out.append("install the typing tool: sudo apt install xdotool "
                       "(text goes to History meanwhile - copy it from "
                       "there)")
        if not which("xclip"):
            out.append("install the clipboard tool: sudo apt install xclip")
        grab = (status or {}).get("hotkey_grabbed")
        if grab is False:
            out.append("the dictate hotkey is held by another app - the "
                       "daemon keeps retrying; change hotkey.key in "
                       "Settings → Hotkeys")

    if not which("pw-record") and not which("parecord"):
        out.append("no recorder found - sudo apt install pipewire-audio-utils")

    out.append("every dictation is saved in History - copy or re-insert "
               "text from there any time")
    out.append("full report: sayit-ermano doctor")
    return out
