"""Configuration: TOML file at ~/.config/fluidvoice/config.toml.

Every key has a default; the file only needs to contain overrides.
"""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

from . import paths

DEFAULT_FILLERS = [
    "um", "uh", "er", "ah", "eh", "umm", "uhh", "err", "ahh", "ehh",
    "hmm", "hm", "mm", "mmm", "erm", "urm", "ugh",
]

# Single source of truth for language codes: the Settings picker AND the
# new-key validation (general.language_cycle / general.language_whitelist)
# both read it. general.language itself keeps its permissive regex grammar
# (a saved out-of-table code stays legal there, as it always was).
KNOWN_LANGUAGES: list[str] = [
    "en", "de", "es", "fr", "it", "nl", "pl", "pt", "ru", "uk",
    "sl", "sr", "hr", "bs", "cs", "sk", "sv", "da", "fi", "no",
    "hu", "ro", "bg", "el", "tr", "zh", "ja", "ko", "ar", "hi",
]

DEFAULTS: dict[str, Any] = {
    "general": {
        "language": "auto",  # whisper language code or "auto"
        # ordered codes the cycle hotkey steps through (may include "auto");
        # empty = the cycle feature is off even when hotkey.language_key
        # is bound. Read live by the daemon; the cycle STATE is runtime-only
        # and never persisted.
        "language_cycle": [],
        # wrong-language guard: when auto-detection lands outside these
        # codes, one re-decode with the first entry. Empty = off.
        "language_whitelist": [],
        "copy_to_clipboard": False,  # upstream copyTranscriptionToClipboard
        "tray_enabled": True,  # panel/tray icon while the daemon runs
        # case-insensitive WM_CLASS substrings: spoken-send never presses
        # Enter here and typed insertions gain a trailing autocomplete space
        "terminal_apps": [
            "gnome-terminal", "kgx", "konsole", "xterm", "alacritty",
            "kitty", "wezterm", "ghostty", "foot", "tilix", "terminator",
            "guake", "yakuake", "st-256color", "warp",
        ],
        # hotkeys ignored + active dictation cancelled while the session is
        # locked/suspended (logind lock watch, see fluidvoice/lockmon.py)
        "pause_when_locked": True,
    },
    "hotkey": {
        # X11 keysym name. Modifier-only keys (Right_Control, Right_Alt,
        # Right_Shift, Super_R...) work in "toggle" mode only.
        "key": "Right_Control",
        "modifiers": [],  # any of: ctrl, alt, shift, super
        # toggle | hold | both (hold/both need a non-modifier key; keys
        # typed during a hold pass through to the focused app natively -
        # the keyboard is freed for the duration; swallowed only if freeing
        # it fails). "both" = upstream Automatic: a quick tap toggles, a
        # held key talks (250 ms disambiguation window).
        "mode": "toggle",
        # macOS parity: Escape cancels an in-progress dictation (discards,
        # nothing typed). Grabbed ONLY while recording; "none" disables.
        "cancel_key": "Escape",
        "rewrite_key": "",  # optional keysym for Rewrite mode (needs [ai])
        "command_key": "",  # optional keysym for Command mode (needs [ai])
        "paste_key": "",  # optional keysym: re-type the last transcription
        # optional keysym: cycles the runtime language override through
        # general.language_cycle ("" = off; the cycle state itself is
        # never persisted)
        "language_key": "",
        # up to 2 EXTRA dictation shortcuts (3 total with the primary),
        # each optionally bound to a named prompt profile (ai/profiles):
        # extra_shortcuts = [{key = "F8", profile = "Terse notes"}]
        "extra_shortcuts": [],
        # Wayland (no global grabs): optional physical push-to-talk read
        # straight from /dev/input (PRIVILEGED: needs the input group and
        # python-evdev, `pip install 'sayit-ermano[wayland]'`). Off by
        # default; the DE-shortcut assist is the primary wayland hotkey.
        "wayland_evdev": False,
        "wayland_evdev_device": "",  # device-name substring, e.g. "Keyboard"
        "wayland_evdev_key": "KEY_RIGHTCTRL",  # ecodes KEY_* name
    },
    "recording": {
        "command": "auto",  # auto | pw-record | parecord
        "device": "",  # optional PipeWire target / PulseAudio device
        "mic_priority": [],  # ordered name patterns for auto mic switching
        "max_seconds": 300,
        "skip_silent": False,  # skip obviously-silent recordings <= 4s
        "first_pcm_timeout": 2.0,  # fail fast if the mic sends no audio (0 = off)
        "sample_rate": 16000,
        # Spoken-send: a trailing phrase strips and presses Enter after typing
        "spoken_send_enabled": False,
        "spoken_send_phrase": "send it",
        "spoken_send_key": "enter",  # enter | shift+enter | ctrl+enter
        # Live transcription preview while recording
        "preview_enabled": True,
        "preview_mode": "auto",   # auto (pill, falls back) | overlay | notify
        "preview_interval": 1.2,   # seconds between partial passes
        "preview_min_audio": 1.0,  # seconds before the first partial
        "preview_bottom_offset": 64,  # pill px above the screen bottom edge
        "preview_overlay_size": "medium",  # pill | small | medium | large (macOS sizes)
        # Segmented streaming preview: fixed windows (50% hop), one decode
        # per tick instead of re-decoding the whole take; the trailing-
        # silence VAD auto-stops the take after vad_silence_s of quiet.
        "preview_segmented": True,
        "preview_segment_s": 2.0,      # window length in seconds
        "preview_vad_silence_s": 2.0,  # 0 disables the auto-stop
        "overlay_chips": True,   # hover action chips above the pill (X11)
        "pause_media": True,  # pause MPRIS players while dictating (resume after)
        # mouse push-to-talk: "button8"/"b8" (6-255; 1-5 refused - they would
        # break the desktop). Empty = off. Independent of hotkey.mode - the
        # button is always hold-style.
        "push_to_talk_button": "",
        # extra modifiers to require for the button (any of ctrl/alt/shift/super)
        "push_to_talk_modifiers": [],
    },
    "model": {
        "backend": "auto",  # auto | faster-whisper | whisper-torch | whisper.cpp | parakeet
        "name": "auto",  # auto -> small (CUDA) / base (CPU); tiny...large-v3-turbo; parakeet catalog names for backend="parakeet"
        "device": "auto",  # auto | cuda | cpu
        "compute": "auto",  # auto | float16 | int8
        "whispercpp_model": "",  # catalog name (ggml-base.bin...) or path to a ggml/gguf model for whisper.cpp
        "eager_warmup": True,  # load the model at daemon start (preview-ready)
        # seconds with NO dictation activity before the loaded model is
        # released (RAM/VRAM freed); 0 = never unload. 30..86400 when set.
        # The first dictation after an unload pays the model load time.
        "idle_unload_s": 0,
        # per-model language overrides: {model_key: code} across all
        # catalogs; missing key / "" inherits general.language, "auto"
        # forces detection for that model (read per-dictation, applies live)
        "languages": {},
    },
    "processing": {
        "remove_filler_words": True,
        "filler_words": list(DEFAULT_FILLERS),
        "punctuation_enabled": True,
        "punctuation_prefix": "literal",  # spoken prefix, e.g. "literal comma"
        # extra spoken formatting-action trigger aliases (B4): action name
        # -> aliases, on top of the built-ins. Example:
        # formatting_action_triggers = {new_line = ["nova vrstica"]}
        "formatting_action_triggers": {},
        "dictionary": [],  # [{ triggers = ["miro board"], replacement = "Miro board" }]
        # GAAV: casual/search-field formatting of the final text
        "gaav_enabled": False,
        "gaav_lowercase_first": True,
        "gaav_remove_trailing_period": True,
        # chat-app literal squeeze: "/ fix" -> "/fix", "@ John Smith" ->
        # "@John Smith" (upstream DictationLiteralFormatting, literal forms)
        "slash_mention_squeeze": True,
    },
    "ai": {
        # OpenAI-compatible chat endpoint (OpenAI, Groq, Ollama /v1, LM Studio, llama.cpp server...)
        "enabled": False,
        "base_url": "http://localhost:11434/v1",
        "model": "",
        "api_key": "",  # preferred: leave empty and use api_key_env
        "api_key_env": "SAYITERMANO_API_KEY",
        "temperature": 0.2,
        "timeout_seconds": 120,
        "max_retries": 3,
        # upstream per-app prompt sets: [{"apps": ["zed"], "instructions": "..."}]
        "per_app_prompts": [],
        # custom base prompt for AI polish (empty = the built-in dictation
        # prompt; Settings → AI can save named presets of it)
        "base_prompt": "",
    },
    "insertion": {
        "mode": "typed",  # typed | paste | auto (typed, falls back to paste)
        "type_delay_ms": 8,
        "paste_threshold_chars": 1200,  # longer texts use clipboard paste
        "terminal_autocomplete_space": True,  # one trailing space in terminals
        # Verify the paste landed (selection read) before restoring the
        # clipboard; false = legacy fixed-delay restore
        "verify_paste": True,
        # Keystroke used to paste in terminal apps (general.terminal_apps);
        # X11 terminals need ctrl+shift+v
        "terminal_paste_key": "ctrl+shift+v",
        # Wayland typing tool: auto | wtype | ydotool. auto prefers wtype
        # (wlroots/KDE) and skips it on GNOME (no virtual-keyboard
        # protocol), falling back to ydotool (needs ydotoold + uinput).
        "wayland_tool": "auto",
    },
    "sounds": {
        "enabled": True,
        "volume": 1.0,  # 0.0 - 1.0
    },
    "notifications": {
        "enabled": True,
    },
    "updates": {
        # check GitHub releases for a newer version (once per daemon start
        # + daily; see fluidvoice/update.py). The updater NEVER installs
        # anything - it notifies and prints the upgrade command.
        "check": True,
        "notify": True,  # desktop notification when a newer release appears
    },
    "history": {
        "save": True,
        "save_audio": False,
        "audio_budget_gb": 4.0,
    },
    "command": {
        "max_turns": 4,           # agent loop bound (upstream: 20)
        "working_dir": "",       # "" -> $HOME
        "timeout_seconds": 60.0,  # per-command subprocess timeout
        "confirm_timeout_s": 120.0,  # auto-cancel a pending confirmation
        "destructive_patterns": [],  # user additions to the built-in list
        "context_window_s": 300.0,   # follow-up context window (0 = off)
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: Path | None = None) -> dict:
    """Load config, deep-merged over DEFAULTS. Unknown keys are kept."""
    path = path or paths.config_file()
    user: dict = {}
    if path.exists():
        with open(path, "rb") as fh:
            user = tomllib.load(fh)
    user.pop("server", None)  # retired web UI section (spec: strip silently)
    return _deep_merge(DEFAULTS, user)


TEMPLATE = """\
# SayItErmano configuration.
# Delete any line to fall back to the built-in default.

[general]
# Whisper language code ("auto" detects, or "en", "de", ...)
language = "auto"
# Ordered language codes the cycle hotkey steps through, e.g.
# ["auto", "en", "sl"] - may include "auto"; empty = cycle key off.
# The cycle override is RUNTIME daemon state and never persisted.
language_cycle = []
# Wrong-language guard: when language = "auto" detects a language NOT in
# this list, the take is re-decoded once with the first entry, e.g.
# ["sl", "en"]. Empty = off.
language_whitelist = []
# Also copy every transcription to the clipboard
copy_to_clipboard = false
# Case-insensitive WM_CLASS substrings identifying terminals. In these apps
# spoken-send never presses Enter (a half-typed shell line would EXECUTE)
# and typed insertions gain one trailing space so autocomplete commits.
terminal_apps = ["gnome-terminal", "kgx", "konsole", "xterm", "alacritty", "kitty", "wezterm", "ghostty", "foot", "tilix", "terminator", "guake", "yakuake", "st-256color", "warp"]
# Ignore hotkeys and cancel an active dictation while the session is
# locked/suspended (logind lock watch; tray notes "paused (locked)")
pause_when_locked = true

[hotkey]
# X11 keysym name of the dictation hotkey. Examples:
#   "Right_Control", "Right_Alt", "F9", "space", "Pause"
# Modifier-only keys (Right_Control / Right_Alt / Right_Shift / Super_R)
# only work with mode = "toggle".
key = "Right_Control"
# Extra modifiers to require, e.g. ["ctrl", "shift"]
modifiers = []
# "toggle": tap to start, tap again to stop & transcribe.
# "hold":   push-to-talk (non-modifier key only). Other keys typed during
#           the hold pass through to the focused app natively (the keyboard
#           is freed for the hold's duration; swallowed only if freeing it
#           fails). The held hotkey's auto-repeats also reach the app.
mode = "toggle"
# Optional extra key that cancels a running recording (keysym name, "" = off)
cancel_key = ""
# Optional keysym that cycles the language at runtime (steps through
# general.language_cycle; the cycle state is never persisted)
language_key = ""
# Wayland push-to-talk via evdev (privileged: needs the input group +
# python-evdev). wayland_evdev_device matches /dev/input device names
# by substring; wayland_evdev_key is an ecodes KEY_* name.
wayland_evdev = false
wayland_evdev_device = ""
wayland_evdev_key = "KEY_RIGHTCTRL"

[recording]
# auto | pw-record | parecord
command = "auto"
# Optional PipeWire node target (pw-record --target) / PulseAudio source.
device = ""
# Ordered microphone priority patterns — case-insensitive substrings of
# the PulseAudio/PipeWire source name, first match wins, e.g.
#   ["bluez", "usb-cam"]   # Bluetooth headset first, then a USB webcam
# When the configured `device` above disappears, FluidVoice switches to
# the first available match and notifies you. Switching never happens
# mid-dictation: the take finishes on the still-open stream and the
# fallback applies within a few seconds after it. With device = ""
# ("auto") the system default is followed and never overridden.
mic_priority = []
max_seconds = 300
# Skip recordings <= 4s that are pure silence
skip_silent = false
# Stop early when the microphone sends no audio at all (muted/wrong device)
first_pcm_timeout = 2.0
# Mouse push-to-talk: hold this button to dictate (always hold-style,
# independent of hotkey.mode). "button8"/"b8"/"8" - buttons 6-255 only;
# 1-5 (click/scroll) are refused, they would break the desktop. Thumb
# buttons are usually 8/9 (6/7 on some mice). Empty = off.
push_to_talk_button = ""
# Extra modifiers to require for the button, e.g. ["ctrl"]
push_to_talk_modifiers = []

[model]
# auto | faster-whisper | whisper-torch | whisper.cpp | parakeet
backend = "auto"
# auto -> "small" when CUDA is available, "base" otherwise.
# Or one of: tiny, base, small, medium, large-v3, large-v3-turbo
# with backend="parakeet": parakeet-tdt-0.6b-v2 | parakeet-tdt-0.6b-v3
name = "auto"
device = "auto"   # auto | cuda | cpu
compute = "auto"  # auto | float16 | int8
# ggml/gguf model for the whisper.cpp backend: a catalog name
# (ggml-base.bin, ggml-small.en.bin, ...) or a path to a file
whispercpp_model = ""
# Per-model language overrides, e.g. languages = { small = "de", "ggml-base.en.bin" = "en" }
# "auto" = always detect for that model; a missing key follows general.language
languages = {}
# Unload the speech model after this many idle seconds to free RAM/VRAM
# (0 = keep it loaded forever; range 30..86400 when set). The next
# dictation after an unload pays the model load time again.
# idle_unload_s = 300

[processing]
remove_filler_words = true
# Filler words removed before punctuation formatting
filler_words = ["um", "uh", "er", "ah", "eh", "umm", "uhh", "err", "ahh", "ehh", "hmm", "hm", "mm", "mmm", "erm", "urm", "ugh"]
punctuation_enabled = true
# Spoken commands require this prefix word: "literal comma" -> ","
punctuation_prefix = "literal"
# Custom dictionary: [[ { triggers = ["miro board"], replacement = "Miro board" } ]]
dictionary = []
# Chat-app literal squeeze: "/ fix the deploy" -> "/fix the deploy",
# "@ John Smith" -> "@John Smith" (runs after AI cleanup, before GAAV)
slash_mention_squeeze = true

[ai]
# Optional AI polish of the raw transcript (FluidVoice's headline feature).
# Any OpenAI-compatible /v1/chat/completions endpoint works:
#   OpenAI   https://api.openai.com/v1
#   Groq     https://api.groq.com/openai/v1
#   Ollama   http://localhost:11434/v1
#   LM Studio http://localhost:1234/v1
enabled = false
base_url = "http://localhost:11434/v1"
model = ""
api_key = ""             # preferred: leave empty and export the env var below
api_key_env = "SAYITERMANO_API_KEY"
temperature = 0.2
timeout_seconds = 120
max_retries = 3
# Custom base prompt for AI polish (empty = built-in). Settings → AI can
# save named presets of it (prompt profiles).
# base_prompt = ""

[insertion]
# typed: simulate keystrokes (xdotool type)
# paste: clipboard + Ctrl+V (restores your clipboard afterwards)
# auto: typed, falling back to paste for very long texts
mode = "auto"
type_delay_ms = 8
paste_threshold_chars = 1200
# One trailing space after typed insertions in terminal apps (general.
# terminal_apps) so the shell's autocomplete commits the last token
terminal_autocomplete_space = true
# Verify the paste landed (selection read by the target) before restoring
# the clipboard; false = legacy fixed-delay restore
verify_paste = true
# Keystroke used to paste in terminal apps (general.terminal_apps); X11
# terminals pass ctrl+v through to the app, they need ctrl+shift+v
terminal_paste_key = "ctrl+shift+v"
# Wayland typing tool: auto | wtype | ydotool (wtype needs wlroots/KDE;
# ydotool works everywhere but needs ydotoold + /dev/uinput access).
# Ignored on X11 sessions.
wayland_tool = "auto"

[sounds]
enabled = true
volume = 1.0

[notifications]
enabled = true

[updates]
# Check GitHub releases for a newer version (once per daemon start +
# daily) and notify when one appears. `sayit-ermano update` prints the
# copy-paste upgrade command for this install method; nothing is ever
# installed automatically. Set check = false to disable every probe
# (SAYITERMANO_SKIP_UPDATE_CHECK=1 does the same per-run).
check = true
# Desktop notification when a newer release is first seen
notify = true

[history]
save = true
save_audio = false
audio_budget_gb = 4.0

[command]
# Command mode additions to the built-in destructive-command list (rm, mv,
# sudo, kill, chmod, chown, dd, mkfs, truncate, shred, pipes into rm/sudo,
# ...). A command MATCHING any of these substrings case-insensitively needs
# the strong confirmation: the command hotkey twice (distinct amber warning)
# instead of once. Examples:
# destructive_patterns = ["git push", "shutdown"]
destructive_patterns = []
# Follow-up context: seconds the last 5 executed command results stay
# available to the next voice command in the SAME focused app (0 disables,
# 300 is the default). Say "new session" to clear it immediately; nothing
# is persisted, a daemon restart starts cold.
context_window_s = 300.0
"""


def _write_private(path: Path, text: str) -> None:
    """Atomic write with 0600 - the file may contain an API key."""
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".fluidvoice-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def write_template(path: Path | None = None) -> Path:
    path = path or paths.config_file()
    _write_private(path, TEMPLATE)
    return path


# ---------------------------------------------------------------------------
# Saving (used by the settings UI). Only whitelisted keys are written; values
# for keys the UI does not manage (like ai.api_key) are carried over from the
# existing file so a save never loses them.
# ---------------------------------------------------------------------------

_SAVE_WHITELIST: dict[str, list[str]] = {
    "general": ["language", "language_cycle", "language_whitelist",
                "copy_to_clipboard", "tray_enabled",
                "terminal_apps", "pause_when_locked"],
    "hotkey": ["key", "modifiers", "mode", "cancel_key", "rewrite_key", "paste_key",
                  "extra_shortcuts", "language_key",
                "command_key", "wayland_evdev", "wayland_evdev_device",
                "wayland_evdev_key"],
    "recording": ["command", "device", "mic_priority", "max_seconds",
                  "skip_silent",
                  "first_pcm_timeout", "spoken_send_enabled", "spoken_send_phrase",
                  "spoken_send_key", "preview_enabled", "preview_mode",
                  "preview_interval", "preview_min_audio",
                  "preview_bottom_offset", "preview_overlay_size",
                  "preview_segmented", "preview_segment_s",
                  "preview_vad_silence_s", "overlay_chips",
                  "pause_media", "push_to_talk_button",
                  "push_to_talk_modifiers"],
    "model": ["backend", "name", "device", "compute", "whispercpp_model",
              "eager_warmup", "idle_unload_s", "languages"],
    "processing": ["remove_filler_words", "filler_words", "punctuation_enabled",
                   "punctuation_prefix", "dictionary",
                   "formatting_action_triggers", "gaav_enabled",
                   "gaav_lowercase_first", "gaav_remove_trailing_period",
                   "slash_mention_squeeze"],
    "ai": ["enabled", "base_url", "model", "api_key", "api_key_env", "temperature",
           "timeout_seconds", "max_retries", "per_app_prompts", "base_prompt"],
    "insertion": ["mode", "type_delay_ms", "paste_threshold_chars",
                  "terminal_autocomplete_space", "verify_paste",
                  "terminal_paste_key", "wayland_tool"],
    "sounds": ["enabled", "volume"],
    "notifications": ["enabled"],
    "updates": ["check", "notify"],
    "history": ["save", "save_audio", "audio_budget_gb"],
    "command": ["max_turns", "working_dir", "timeout_seconds",
                "confirm_timeout_s", "destructive_patterns",
                "context_window_s"],
}


# Keys where an EMPTY value is meaningful (not "keep the saved value"):
# ai.base_prompt = "" restores the built-in prompt, so a cleared editor
# must actually clear the file instead of carrying the old value over.
_EMPTY_IS_MEANINGFUL = {("ai", "base_prompt"),
                         # an empty button spec turns mouse PTT off
                         ("recording", "push_to_talk_button")}


def _toml_value(value: Any) -> str:
    import json
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)  # JSON escaping is valid TOML
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        import re as _re
        parts = []
        for k, v in value.items():
            # bare TOML keys only for safe chars; anything else (e.g. the
            # dotted "ggml-base.bin") MUST be quoted or it round-trips
            # as a nested table
            k_str = str(k)
            key_repr = (k_str if _re.fullmatch(r"[A-Za-z0-9_-]+", k_str)
                        else json.dumps(k_str, ensure_ascii=False))
            parts.append(f"{key_repr} = {_toml_value(v)}")
        return "{ " + ", ".join(parts) + " }"
    raise ValueError(f"cannot serialize {type(value).__name__} to TOML")


def save_config(cfg: dict, path: Path | None = None) -> Path:
    """Persist the whitelisted config keys as TOML, carrying over api_key."""
    path = path or paths.config_file()
    carry: dict = {}
    if path.exists():
        try:
            with open(path, "rb") as fh:
                carry = tomllib.load(fh)
        except tomllib.TOMLDecodeError:
            carry = {}
    lines: list[str] = ["# SayItErmano configuration (managed by the settings UI)",
                        ""]
    for section, keys in _SAVE_WHITELIST.items():
        values = cfg.get(section, {})
        carried = carry.get(section, {})
        lines.append(f"[{section}]")
        for key in keys:
            value = values.get(key)
            if value in ("", None) and key in carried \
                    and (section, key) not in _EMPTY_IS_MEANINGFUL:
                value = carried[key]  # e.g. keep an existing api_key
            if value not in ("", None):
                lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    _write_private(path, "\n".join(lines).rstrip() + "\n")
    return path


# ---------------------------------------------------------------------------
# Settings validation - single source of truth for the web UI, the socket
# API and the native GTK app (moved from webui.py per the native-app spec).
# ---------------------------------------------------------------------------

# (section, key) -> ("float"|"int", (lo, hi)) | ("str", max_len)
SETTING_RANGES: dict[tuple[str, str], Any] = {
    ("recording", "first_pcm_timeout"): ("float", (0.0, 60.0)),
    ("recording", "preview_interval"): ("float", (0.3, 10.0)),
    ("recording", "preview_min_audio"): ("float", (0.3, 10.0)),
    ("recording", "preview_bottom_offset"): ("int", (0, 400)),
    ("recording", "preview_segment_s"): ("float", (1.0, 6.0)),
    ("recording", "preview_vad_silence_s"): ("float", (0.0, 10.0)),
    ("hotkey", "key"): ("str", 64),
    ("hotkey", "cancel_key"): ("str", 64),
    ("hotkey", "rewrite_key"): ("str", 64),
    ("hotkey", "paste_key"): ("str", 64),
    ("hotkey", "command_key"): ("str", 64),
    ("hotkey", "language_key"): ("str", 64),
    ("hotkey", "wayland_evdev_device"): ("str", 128),
    ("hotkey", "wayland_evdev_key"): ("str", 64),
    ("command", "max_turns"): ("int", (1, 20)),
    ("command", "working_dir"): ("str", 4096),
    ("command", "timeout_seconds"): ("float", (1, 3600)),
    ("command", "confirm_timeout_s"): ("float", (5, 600)),
    ("command", "context_window_s"): ("float", (0, 86400)),
    ("recording", "device"): ("str", 256),
    ("recording", "max_seconds"): ("float", (1, 86400)),
    ("recording", "spoken_send_phrase"): ("str", 64),
    ("model", "whispercpp_model"): ("str", 4096),
    ("processing", "punctuation_prefix"): ("str", 32),
    ("ai", "base_url"): ("str", 2048),
    ("ai", "model"): ("str", 256),
    ("ai", "api_key_env"): ("str", 128),
    ("ai", "temperature"): ("float", (0.0, 2.0)),
    ("ai", "timeout_seconds"): ("float", (1, 3600)),
    ("insertion", "type_delay_ms"): ("int", (0, 1000)),
    ("insertion", "paste_threshold_chars"): ("int", (1, 1_000_000)),
    ("insertion", "terminal_paste_key"): ("str", 32),
    ("sounds", "volume"): ("float", (0.0, 1.0)),
    ("history", "audio_budget_gb"): ("float", (0.0, 1024.0)),
}
SETTING_ENUMS: dict[tuple[str, str], set] = {
    ("recording", "preview_mode"): {"auto", "notify", "overlay"},
    ("recording", "preview_overlay_size"): {"pill", "small", "medium", "large"},
    ("hotkey", "mode"): {"toggle", "hold", "both"},
    ("model", "backend"): {"auto", "faster-whisper", "whisper-torch",
                           "whisper.cpp", "parakeet"},
    ("model", "device"): {"auto", "cuda", "cpu"},
    ("model", "compute"): {"auto", "float16", "int8"},
    ("insertion", "mode"): {"auto", "typed", "paste"},
    ("insertion", "wayland_tool"): {"auto", "wtype", "ydotool"},
    ("recording", "command"): {"auto", "pw-record", "parecord"},
    ("recording", "spoken_send_key"): {"enter", "shift+enter", "ctrl+enter"},
}
SETTING_BOOLS = {("general", "copy_to_clipboard"), ("general", "tray_enabled"),
                 ("general", "pause_when_locked"),
                 ("recording", "pause_media"), ("recording", "preview_enabled"),
                 ("recording", "preview_segmented"),
                 ("recording", "overlay_chips"),
                 ("recording", "skip_silent"),
                 ("recording", "spoken_send_enabled"),
                 ("processing", "remove_filler_words"),
                 ("processing", "punctuation_enabled"),
                 ("processing", "gaav_enabled"),
                 ("processing", "gaav_lowercase_first"),
                 ("processing", "gaav_remove_trailing_period"),
                 ("processing", "slash_mention_squeeze"),
                 ("ai", "enabled"), ("sounds", "enabled"),
                 ("notifications", "enabled"),
                 ("updates", "check"), ("updates", "notify"),
                 ("history", "save"), ("history", "save_audio"),
                 ("insertion", "terminal_autocomplete_space"),
                 ("insertion", "verify_paste"),
                 ("hotkey", "wayland_evdev"),
                 ("model", "eager_warmup")}
# list-valued pass-through keys the UI owns
SETTING_LISTS = (("processing", "filler_words"), ("processing", "dictionary"),
                 ("hotkey", "modifiers"), ("recording", "mic_priority"),
                 ("recording", "push_to_talk_modifiers"))
ALLOWED_SETTINGS: dict[str, set] = {
    "general": {"language", "language_cycle", "language_whitelist",
                "copy_to_clipboard", "tray_enabled",
                "terminal_apps", "pause_when_locked"},
    "hotkey": {"key", "modifiers", "mode", "cancel_key", "rewrite_key", "paste_key",
                 "extra_shortcuts", "language_key",
               "command_key", "wayland_evdev", "wayland_evdev_device",
               "wayland_evdev_key"},
    "recording": {"command", "device", "mic_priority", "max_seconds",
                  "skip_silent",
                  "first_pcm_timeout", "spoken_send_enabled",
                  "spoken_send_phrase", "spoken_send_key",
                  "preview_enabled", "preview_mode", "preview_interval",
                  "preview_min_audio", "preview_bottom_offset",
                  "preview_overlay_size", "pause_media",
                  "preview_segmented", "preview_segment_s",
                  "preview_vad_silence_s", "overlay_chips",
                  "push_to_talk_button", "push_to_talk_modifiers"},
    "model": {"backend", "name", "device", "compute", "whispercpp_model",
              "eager_warmup", "idle_unload_s", "languages"},
    "processing": {"remove_filler_words", "filler_words",
                   "punctuation_enabled", "punctuation_prefix", "dictionary",
                   "formatting_action_triggers",
                   "gaav_enabled", "gaav_lowercase_first",
                   "gaav_remove_trailing_period", "slash_mention_squeeze"},
    "ai": {"enabled", "base_url", "model", "api_key_env", "temperature",
           "timeout_seconds", "max_retries", "per_app_prompts", "base_prompt"},
    "insertion": {"mode", "type_delay_ms", "paste_threshold_chars",
                  "terminal_autocomplete_space", "verify_paste",
                  "terminal_paste_key", "wayland_tool"},
    "sounds": {"enabled", "volume"},
    "notifications": {"enabled"},
    "updates": {"check", "notify"},
    "history": {"save", "save_audio", "audio_budget_gb"},
    "command": {"max_turns", "working_dir", "timeout_seconds",
                "confirm_timeout_s", "destructive_patterns",
                "context_window_s"},
}
RESTART_REQUIRED = {"model.eager_warmup"}
ENGINE_KEYS = {"model.backend", "model.name", "model.device",
               "model.compute", "model.whispercpp_model"}


def coerce_setting(section: str, key: str, value: Any) -> tuple[bool, Any]:
    """Validate one value. Returns (ok, coerced)."""
    import re as _re

    if (section, key) in SETTING_BOOLS:
        return (isinstance(value, bool), value)
    if (section, key) in SETTING_ENUMS:
        enums = SETTING_ENUMS[(section, key)]
        return (isinstance(value, str) and value in enums, value)
    if (section, key) == ("general", "language"):
        ok = isinstance(value, str) and bool(
            _re.fullmatch(r"auto|[a-z]{2,3}(-[A-Za-z0-9]{2,8})?", value.strip()))
        return (ok, value.strip() if ok else value)
    if (section, key) == ("general", "language_cycle"):
        return _coerce_language_cycle(value)
    if (section, key) == ("general", "language_whitelist"):
        return _coerce_language_whitelist(value)
    if (section, key) == ("model", "idle_unload_s"):
        # 0 (never unload) or 30..86400 s; bool is an int subclass - reject
        ok = isinstance(value, int) and not isinstance(value, bool) \
            and (value == 0 or 30 <= value <= 86400)
        return (ok, value)
    if (section, key) == ("ai", "per_app_prompts"):
        return _coerce_per_app_prompts(value)
    if (section, key) == ("processing", "formatting_action_triggers"):
        return _coerce_action_triggers(value)
    if (section, key) == ("hotkey", "extra_shortcuts"):
        return _coerce_extra_shortcuts(value)
    if (section, key) == ("ai", "base_prompt"):
        # empty IS valid here (clearing the editor restores the built-in
        # prompt), unlike the str-range rule below which rejects ""
        return (isinstance(value, str) and len(value) <= 8000, value)
    if (section, key) == ("recording", "push_to_talk_button"):
        return _coerce_button_spec(value)
    rule = SETTING_RANGES.get((section, key))
    if rule:
        kind, bound = rule
        if kind == "str":
            ok = isinstance(value, str) and 0 < len(value) <= bound \
                and not value.startswith("-")
            return (ok, value)
        try:
            num = float(value) if kind == "float" else int(value)
            if isinstance(value, bool) or not (bound[0] <= num <= bound[1]):
                return (False, value)
            return (True, num)
        except (TypeError, ValueError):
            return (False, value)
    if (section, key) == ("recording", "mic_priority"):
        return _coerce_mic_priority(value)
    if (section, key) == ("model", "languages"):
        return _coerce_model_languages(value)
    if (section, key) == ("general", "terminal_apps"):
        return _coerce_terminal_apps(value)
    if (section, key) == ("command", "destructive_patterns"):
        return _coerce_destructive_patterns(value)
    if (section, key) in SETTING_LISTS:
        if not isinstance(value, list):
            return (False, value)
        if (section, key) in (("hotkey", "modifiers"),
                              ("recording", "push_to_talk_modifiers")) \
                and any(m not in ("ctrl", "alt", "shift", "super")
                        for m in value):
            return (False, value)
        if key == "filler_words" and any(not isinstance(w, str) or len(w) > 64
                                         or not w.strip() for w in value):
            return (False, value)
        if key == "dictionary":
            for entry in value:
                if (not isinstance(entry, dict)
                        or not isinstance(entry.get("triggers", []), list)
                        or not isinstance(entry.get("replacement", ""), str)
                        or len(entry.get("replacement", "")) > 512):
                    return (False, value)
        return (True, value)
    return (False, value)  # unknown key -> reject


def _coerce_button_spec(value: Any) -> tuple[bool, Any]:
    """recording.push_to_talk_button: "" (off) or a button spec such as
    "button8"/"b8"/"8". Normalized to the canonical "button<N>" form via
    hotkey.parse_button_spec; buttons 1-5 (click/scroll), > 255 and
    unparsable values reject the whole setting."""
    if not isinstance(value, str) or len(value) > 32:
        return (False, value)
    from .hotkey import HotkeyError, parse_button_spec
    try:
        button = parse_button_spec(value)
    except HotkeyError:
        return (False, value)
    return (True, "" if button is None else f"button{button}")


def _coerce_mic_priority(value: Any) -> tuple[bool, Any]:
    """recording.mic_priority: ordered case-insensitive source-name
    substrings. Entries are stripped and empties dropped; >64-char entries
    or >20 patterns reject the whole list; duplicates (case-insensitive)
    keep the first occurrence."""
    if not isinstance(value, list) \
            or any(not isinstance(p, str) for p in value):
        return (False, value)
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in value:
        pattern = raw.strip()
        if not pattern:
            continue
        if len(pattern) > 64:
            return (False, value)
        if pattern.lower() in seen:
            continue
        seen.add(pattern.lower())
        cleaned.append(pattern)
    if len(cleaned) > 20:
        return (False, value)
    return (True, cleaned)


def _coerce_extra_shortcuts(value: Any) -> tuple[bool, Any]:
    """hotkey.extra_shortcuts: up to 2 entries of
    {key, modifiers, profile} - additional dictation shortcuts with an
    optional named prompt profile (upstream multiple-shortcuts parity)."""
    if not isinstance(value, list) or len(value) > 2:
        return (False, value)
    cleaned = []
    for item in value:
        if not isinstance(item, dict):
            return (False, value)
        key = item.get("key")
        if not isinstance(key, str) or not key.strip() or len(key) > 64:
            return (False, value)
        mods = item.get("modifiers", [])
        if (not isinstance(mods, list)
                or any(m not in ("ctrl", "alt", "shift", "super") for m in mods)):
            return (False, value)
        profile = item.get("profile", "")
        if not isinstance(profile, str) or len(profile) > 64:
            return (False, value)
        entry = {"key": key.strip(), "modifiers": mods}
        if profile.strip():
            entry["profile"] = profile.strip()
        cleaned.append(entry)
    return (True, cleaned)


def _coerce_action_triggers(value: Any) -> tuple[bool, Any]:
    """processing.formatting_action_triggers: {action: [aliases]}.
    Actions are the four upstream spoken formatting actions; aliases are
    non-empty <=64-char strings, max 16 per action, 8 actions worth of
    entries overall. Garbage entries reject the whole value (the Settings
    editor shows per-action rows, so partial states should not save)."""
    if not isinstance(value, dict) or len(value) > 8:
        return (False, value)
    actions = {"new_line", "new_paragraph", "tab", "space"}
    cleaned: dict[str, list[str]] = {}
    for raw_k, raw_v in value.items():
        k = str(raw_k).strip()
        if k not in actions or not isinstance(raw_v, list) or len(raw_v) > 16:
            return (False, value)
        aliases = []
        for a in raw_v:
            if not isinstance(a, str) or not a.strip() or len(a) > 64:
                return (False, value)
            aliases.append(a.strip())
        if aliases:
            cleaned[k] = aliases
    return (True, cleaned)


def _coerce_language_cycle(value: Any) -> tuple[bool, Any]:
    """general.language_cycle: ORDERED codes the cycle hotkey steps through
    (the order IS the feature). Entries are stripped + lowercased, each must
    be "auto" or a KNOWN_LANGUAGES code (case-insensitive); duplicates
    (case-insensitive) keep the first occurrence; >8 entries or any
    unknown/empty/non-str entry rejects the whole value."""
    if not isinstance(value, list):
        return (False, value)
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            return (False, value)
        code = raw.strip().lower()
        if not code or (code != "auto" and code not in KNOWN_LANGUAGES):
            return (False, value)
        if code in seen:
            continue
        seen.add(code)
        cleaned.append(code)
    if len(cleaned) > 8:
        return (False, value)
    return (True, cleaned)


def _coerce_language_whitelist(value: Any) -> tuple[bool, Any]:
    """general.language_whitelist: codes the wrong-language guard accepts
    from auto-detection. "auto" is rejected (it means "no constraint");
    otherwise the same shape as language_cycle with a cap of 16."""
    if not isinstance(value, list):
        return (False, value)
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            return (False, value)
        code = raw.strip().lower()
        if not code or code == "auto" or code not in KNOWN_LANGUAGES:
            return (False, value)
        if code in seen:
            continue
        seen.add(code)
        cleaned.append(code)
    if len(cleaned) > 16:
        return (False, value)
    return (True, cleaned)


def _coerce_model_languages(value: Any) -> tuple[bool, Any]:
    """model.languages: {model_key: code} per-model language overrides.
    Keys are unique across all three catalogs (tiny...large-v3-turbo,
    ggml-*.bin, parakeet-*). Values follow the general.language code
    grammar; a missing key inherits general.language, "auto" forces
    detection for that model. Max 30 entries (one per catalog model +
    paths is plenty)."""
    import re as _re
    if not isinstance(value, dict) or len(value) > 30:
        return (False, value)
    cleaned: dict[str, str] = {}
    for raw_k, raw_v in value.items():
        k = str(raw_k).strip()
        v = raw_v.strip() if isinstance(raw_v, str) else raw_v
        if (not k or len(k) > 64 or not isinstance(raw_v, str)
                or not _re.fullmatch(r"auto|[a-z]{2,3}(-[A-Za-z0-9]{2,8})?", v)):
            return (False, value)
        cleaned[k] = v
    return (True, cleaned)


def _coerce_terminal_apps(value: Any) -> tuple[bool, Any]:
    """general.terminal_apps: case-insensitive WM_CLASS substrings
    identifying terminal apps (spoken-send blocklist + autocomplete
    spacing). Entries are stripped and empties dropped; >64-char entries or
    >32 patterns reject the whole list; duplicates (case-insensitive) keep
    the first occurrence."""
    if not isinstance(value, list) \
            or any(not isinstance(p, str) for p in value):
        return (False, value)
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in value:
        pattern = raw.strip()
        if not pattern:
            continue
        if len(pattern) > 64:
            return (False, value)
        if pattern.lower() in seen:
            continue
        seen.add(pattern.lower())
        cleaned.append(pattern)
    if len(cleaned) > 32:
        return (False, value)
    return (True, cleaned)


def _coerce_destructive_patterns(value: Any) -> tuple[bool, Any]:
    """command.destructive_patterns: user additions to the built-in
    destructive-command list, matched case-insensitively anywhere in the
    command (same convention as general.terminal_apps). Entries are
    stripped and empties dropped; >128-char entries or >32 patterns reject
    the whole list; duplicates (case-insensitive) keep the first."""
    if not isinstance(value, list) \
            or any(not isinstance(p, str) for p in value):
        return (False, value)
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in value:
        pattern = raw.strip()
        if not pattern:
            continue
        if len(pattern) > 128:
            return (False, value)
        if pattern.lower() in seen:
            continue
        seen.add(pattern.lower())
        cleaned.append(pattern)
    if len(cleaned) > 32:
        return (False, value)
    return (True, cleaned)


def _coerce_per_app_prompts(value: Any) -> tuple[bool, Any]:
    if not isinstance(value, list):
        return (False, value)
    rules = []
    for raw in value[:50]:
        if not isinstance(raw, dict):
            return (False, value)
        apps = [str(a).strip() for a in raw.get("apps", []) if str(a).strip()][:20]
        instructions = str(raw.get("instructions", "")).strip()[:2000]
        if not apps or not instructions:
            return (False, value)
        rules.append({"apps": apps, "instructions": instructions})
    return (True, rules)


def apply_settings(cfg: dict, body: dict) -> tuple[list[str], list[str]]:
    """Whitelisted, validated merge of {section: {key: value}} into cfg
    (mutates cfg; no save). Returns (changed, rejected) as 'section.key'
    strings. Unknown keys and bad types are rejected, never half-applied."""
    from . import backends, model_catalog

    changed: list[str] = []
    rejected: list[str] = []
    for section, keys in ALLOWED_SETTINGS.items():
        for key in keys:
            if section in body and key in body[section]:
                value = body[section][key]
                if (section, key) == ("model", "name"):
                    value = backends.ALIASES.get(str(value).strip().lower(),
                                                 str(value).strip().lower())
                    ok = (value in backends.FW_MODEL_REPOS or value == "auto"
                          or value in model_catalog.PARAKEET_CATALOG)
                elif (section, key) == ("ai", "max_retries"):
                    try:
                        ok = isinstance(value, (int, float)) \
                            and not isinstance(value, bool) \
                            and 0 <= int(value) <= 10
                        value = int(value)
                    except (TypeError, ValueError):
                        ok = False
                else:
                    ok, value = coerce_setting(section, key, value)
                if not ok:
                    rejected.append(f"{section}.{key}")
                    continue
                if cfg.get(section, {}).get(key) != value:
                    changed.append(f"{section}.{key}")
                cfg.setdefault(section, {})[key] = value
    return changed, rejected


def mask_secrets(cfg: dict) -> dict:
    """Deep copy with secrets replaced by booleans (api-key rule)."""
    safe = copy.deepcopy(cfg)
    key = safe.get("ai", {}).get("api_key", "")
    safe.setdefault("ai", {})["api_key"] = bool(key)
    return safe
