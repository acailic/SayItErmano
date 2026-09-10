"""Text insertion into the focused app (X11 + Wayland): typed keystrokes
or clipboard paste.

Mirrors FluidVoice's TypingService strategies:
- typed: xdotool type (X11) / wtype / ydotool (Wayland) - keystroke
  simulation, "clipboard free insert"
- paste: clipboard + Ctrl+V with clipboard restore ("reliable paste"):
  xclip + verified selection read-observation on X11, wl-clipboard with a
  fixed settle delay on Wayland (cross-client selection reads are
  impossible there - see WAYLAND_PASTE_SETTLE_S).

Wayland is additive: every branch is taken ONLY via the session probe
(fluidvoice/session.py), never on "xdotool missing" - the X11 paths below
stay byte-identical for x11/unknown sessions.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from typing import Callable

from . import session as session_mod


class InsertError(RuntimeError):
    pass


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
        raise InsertError(f"required tool not found: {args[0]}") from None


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
        raise InsertError(f"{name} type failed: {proc.stderr.decode()[:200]}")


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
        raise InsertError("wl-clipboard is required for paste mode on "
                          "wayland (install wl-clipboard)")
    if tool is None:
        raise InsertError("no wayland typing tool for the paste keystroke "
                          "(install wtype or ydotool)")
    data = text.encode()
    previous, mime = _wl_clipboard_snapshot()
    try:
        try:
            _wl_clipboard_write(data)
        except Exception as e:  # spawn failures surface as InsertError so
            # the auto-mode ladder can fall through to typed insertion
            raise InsertError(f"wl-copy failed: {e}") from e
        proc = _run(_key_cmd(tool, key), timeout=10)
        if proc.returncode != 0:
            raise InsertError(f"paste keystroke failed: "
                              f"{proc.stderr.decode()[:200]}")
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
        raise InsertError("xclip is required for paste mode (sudo apt install xclip)")
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
            raise InsertError(f"paste keystroke failed: {proc.stderr.decode()[:200]}")
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
        err = InsertError("paste not verified: target did not read the clipboard")
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


def insert_text(text: str, cfg: dict, wm_class: str | None = None,
                on_notice: Callable[[str], None] | None = None) -> str:
    """Insert `text` at the caret. Returns the strategy used.

    wm_class: the insertion target's app identity (None -> live lookup).
    In terminal apps (profiles.rules first, then general.terminal_apps)
    pastes use ctrl+shift+v (insertion.terminal_paste_key - X11 terminals
    pass ctrl+v to the app) and typed insertions ending in a word
    character gain one trailing space (insertion.terminal_autocomplete_space)
    so autocomplete commits; the space is typing-only - clipboard copy and
    history keep the text without it. A canonical profile may override the
    insertion mode for its app (P2). on_notice surfaces paste-fallback /
    restore warnings.

    Wayland sessions route to _insert_text_wayland (wm_class is None there
    UNLESS the P2 context seam supplied an identity at insertion time;
    without one, terminal quirks are inert, documented divergence)."""
    if session_mod.current().is_wayland:
        return _insert_text_wayland(text, cfg, wm_class=wm_class,
                                    on_notice=on_notice)
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
            return "paste"
        except InsertError as e:
            if mode == "paste":
                raise
            if getattr(e, "not_verified", False) and on_notice is not None:
                on_notice("Paste did not land - typing instead")
    if cfg["insertion"].get("terminal_autocomplete_space", True):
        if terminal:
            text = terminal_trailing_space(text)
    insert_typed(text, delay)
    return "typed"


def _insert_text_wayland(text: str, cfg: dict,
                        wm_class: str | None = None,
                        on_notice: Callable[[str], None] | None = None) -> str:
    """Wayland insert: the same mode/threshold/leading-dash routing as the
    X11 body, over the wtype/ydotool + wl-clipboard backends.

    Degradation ladder (each step only when the previous is impossible):
      tool+typed -> tool+wl-clipboard paste -> wl-copy + "paste manually"
      notice ("clipboard-fallback") -> InsertError (the pipeline notifies;
      history still records the take). Terminal quirks apply ONLY when an
    app identity was supplied (P2: the context seam passes the AT-SPI
    identity at insertion time); wm_class None keeps today's behavior -
    without an identity ctrl+shift+v and the autocomplete space stay off
    (a wrong plain ctrl+v in a terminal is recoverable, a mistyped
    terminal-paste is not)."""
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
            return "paste"
        except InsertError:
            if mode == "paste":
                raise
            if on_notice is not None:
                on_notice("Paste did not land - trying to type instead")
    if tool is not None:
        try:
            if cfg["insertion"].get("terminal_autocomplete_space", True) \
                    and terminal:
                text = terminal_trailing_space(text)
            insert_typed(text, delay, tool=tool)
            return "typed"
        except InsertError:
            if on_notice is not None:
                on_notice("Typing failed - falling back to the clipboard")
    if shutil.which("wl-copy"):
        try:
            copy_to_clipboard(text, wayland=True)
        except Exception:
            pass
        else:
            if on_notice is not None:
                on_notice("Copied to clipboard - paste manually (install "
                          "wtype or ydotool to type automatically)")
            return "clipboard-fallback"
    raise InsertError("no wayland insertion tool available - install "
                      "wtype or ydotool (plus wl-clipboard for paste mode)")


def clipboard_fallback(text: str) -> None:
    """Last resort when neither typing nor pasting worked: leave text on the clipboard."""
    copy_to_clipboard(text)  # auto-detects the session (xclip / wl-copy)


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
        raise InsertError(f"{name} key press failed: "
                          f"{proc.stderr.decode()[:200]}")


def copy_to_clipboard(text: str, *, wayland: bool | None = None) -> None:
    """Put text on the clipboard without typing (upstream copyTranscriptionToClipboard).
    wayland=None auto-detects via the session probe (the daemon's
    always-copy call sites stay session-correct); True/False force it."""
    if wayland is None:
        wayland = session_mod.current().is_wayland
    if wayland:
        if not shutil.which("wl-copy"):
            return
        try:
            _wl_clipboard_write(text.encode())
        except Exception:
            pass
        return
    if not shutil.which("xclip"):
        return
    try:
        _clipboard_write(text.encode())
    except Exception:
        pass
