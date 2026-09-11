"""Configuration: TOML file at ~/.config/fluidvoice/config.toml.

Every key has a default; the file only needs to contain overrides.

P1.3 — one configuration registry. ``REGISTRY`` is the single source of
truth for every setting: its default, validation/coercion, persistence
(saved to file? settable over the socket?), apply mode (immediate /
engine reload / daemon restart), secret masking, and the commented
template are all DERIVED from it. The public surface stays compatible:
``DEFAULTS``, ``load_config``'s dict shape, ``save_config`` whitelisting,
``apply_settings`` semantics, and the legacy ``SETTING_*`` tables keep
their names and behavior — they are just derived from the registry now.
"""
from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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

# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

#: A coercer validates AND normalizes one candidate value:
#: ``coerce(value) -> (ok, coerced)``. Factory-made coercers carry
#: metadata attributes (``kind`` and ``bounds``/``range_rule``/``options``)
#: from which the legacy SETTING_* tables and widget bounds are derived.
Coercer = Callable[[Any], tuple[bool, Any]]

#: persistence modes: "save" = written by save_config (settings UI owns
#: the key); "settable" = accepted by apply_settings (socket / UI edits).
PERSIST = frozenset({"save", "settable"})
PERSIST_SAVE_ONLY = frozenset({"save"})       # file-carried, never socket-set
PERSIST_NONE = frozenset()                    # in DEFAULTS, managed by nobody


def _bool() -> Coercer:
    def coerce(value: Any) -> tuple[bool, Any]:
        return (isinstance(value, bool), value)
    coerce.kind = "bool"  # type: ignore[attr-defined]
    return coerce


def _enum(*options: str) -> Coercer:
    allowed = frozenset(options)
    order = tuple(options)

    def coerce(value: Any) -> tuple[bool, Any]:
        return (isinstance(value, str) and value in allowed, value)
    coerce.kind = "enum"  # type: ignore[attr-defined]
    coerce.options = allowed  # type: ignore[attr-defined]
    coerce.option_order = order  # type: ignore[attr-defined]
    return coerce


def _str(max_len: int, allow_empty: bool = False) -> Coercer:
    def coerce(value: Any) -> tuple[bool, Any]:
        if not isinstance(value, str) or len(value) > max_len:
            return (False, value)
        if not allow_empty and (not value or value.startswith("-")):
            return (False, value)  # empty / option-injection dash reject
        return (True, value)
    coerce.kind = "str"  # type: ignore[attr-defined]
    coerce.range_rule = ("str", max_len)  # type: ignore[attr-defined]
    return coerce


def _num(kind: str, lo: float, hi: float) -> Coercer:
    def coerce(value: Any) -> tuple[bool, Any]:
        try:
            num = float(value) if kind == "float" else int(value)
            if isinstance(value, bool) or not (lo <= num <= hi):
                return (False, value)
            return (True, num)
        except (TypeError, ValueError):
            return (False, value)
    coerce.kind = kind  # type: ignore[attr-defined]
    coerce.bounds = (lo, hi)  # type: ignore[attr-defined]
    coerce.range_rule = (kind, (lo, hi))  # type: ignore[attr-defined]
    return coerce


def _reject(reason: str) -> Coercer:
    """For settings that are neither socket-settable nor UI-owned: the
    registry still knows their default (DEFAULTS must stay complete), but
    no caller may change them through the validated paths."""
    def coerce(value: Any) -> tuple[bool, Any]:
        return (False, value)
    coerce.reject_reason = reason  # type: ignore[attr-defined]
    return coerce


def _modifiers() -> Coercer:
    allowed = ("ctrl", "alt", "shift", "super")

    def coerce(value: Any) -> tuple[bool, Any]:
        if not isinstance(value, list) \
                or any(m not in allowed for m in value):
            return (False, value)
        return (True, value)
    coerce.kind = "list"  # type: ignore[attr-defined]
    return coerce


def _filler_words() -> Coercer:
    def coerce(value: Any) -> tuple[bool, Any]:
        if not isinstance(value, list):
            return (False, value)
        if any(not isinstance(w, str) or len(w) > 64 or not w.strip()
               for w in value):
            return (False, value)
        return (True, value)
    coerce.kind = "list"  # type: ignore[attr-defined]
    return coerce


def _dictionary() -> Coercer:
    def coerce(value: Any) -> tuple[bool, Any]:
        if not isinstance(value, list):
            return (False, value)
        for entry in value:
            if (not isinstance(entry, dict)
                    or not isinstance(entry.get("triggers", []), list)
                    or not isinstance(entry.get("replacement", ""), str)
                    or len(entry.get("replacement", "")) > 512):
                return (False, value)
        return (True, value)
    coerce.kind = "list"  # type: ignore[attr-defined]
    return coerce


def _coerce_language(value: Any) -> tuple[bool, Any]:
    """general.language: "auto" or a Whisper code — permissive grammar on
    purpose (a saved out-of-table code stays legal; the Settings picker
    appends it as "(saved)")."""
    import re as _re
    ok = isinstance(value, str) and bool(
        _re.fullmatch(r"auto|[a-z]{2,3}(-[A-Za-z0-9]{2,8})?", value.strip()))
    return (ok, value.strip() if ok else value)


def _coerce_model_name(value: Any) -> tuple[bool, Any]:
    """model.name: "auto", a faster-whisper catalog key (aliases apply) or
    a Parakeet catalog name (catalogs checked at call time — importing
    backends/model_catalog at module import would be circular)."""
    from . import backends, model_catalog
    value = backends.ALIASES.get(str(value).strip().lower(),
                                 str(value).strip().lower())
    ok = (value in backends.FW_MODEL_REPOS or value == "auto"
          or value in model_catalog.PARAKEET_CATALOG)
    return (ok, value)


def _coerce_max_retries(value: Any) -> tuple[bool, Any]:
    """ai.max_retries: 0..10; ints and floats both accepted, normalized
    to int (bool rejected — it is an int subclass)."""
    try:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) \
            and 0 <= int(value) <= 10
        return (ok, int(value))
    except (TypeError, ValueError):
        return (False, value)
_coerce_max_retries.bounds = (0, 10)  # type: ignore[attr-defined]


def _coerce_idle_unload(value: Any) -> tuple[bool, Any]:
    """model.idle_unload_s: 0 (never unload) or 30..86400 s; bool is an
    int subclass - reject."""
    ok = isinstance(value, int) and not isinstance(value, bool) \
        and (value == 0 or 30 <= value <= 86400)
    return (ok, value)
_coerce_idle_unload.bounds = (0, 86400)  # type: ignore[attr-defined]


def _coerce_spoken_send_countdown(value: Any) -> tuple[bool, Any]:
    """recording.spoken_send_countdown_s: 0 (feature off) or 0.3..5.0 s of
    countdown after quiet + phrase."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return (False, value)
    return (f == 0 or 0.3 <= f <= 5.0, f)
_coerce_spoken_send_countdown.bounds = (0.0, 5.0)  # type: ignore[attr-defined]


def _coerce_base_prompt(value: Any) -> tuple[bool, Any]:
    # empty IS valid here (clearing the editor restores the built-in
    # prompt), unlike the str-range rule which rejects ""
    return (isinstance(value, str) and len(value) <= 8000, value)


def _coerce_remote_api_key(value: Any) -> tuple[bool, Any]:
    # any string incl. "" (clearing is done by editing the file or
    # setting remote_url empty); never logged, masked in socket reads
    return (isinstance(value, str) and len(value) <= 4096, value)


def _coerce_remote_url(value: Any) -> tuple[bool, Any]:
    """model.remote_url: "" (off) or an http(s) URL with a non-empty
    host. Stripped first; embedded whitespace, other schemes, missing
    hosts and >2048-char values reject the whole setting."""
    if not isinstance(value, str):
        return (False, value)
    url = value.strip()
    if url == "":
        return (True, "")
    if len(url) > 2048 or any(ch.isspace() for ch in url):
        return (False, value)
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return (False, value)
    return (True, url)


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
_coerce_mic_priority.kind = "list"  # type: ignore[attr-defined]


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


def _coerce_hotwords(value: Any) -> tuple[bool, Any]:
    """model.hotwords: vocabulary-biasing words (upstream #916 - words to
    ADD, unlike the dictionary's replacements). Stripped, 1..64 chars
    each, no duplicates, <=128 entries; any non-str/empty/too-long entry
    rejects the whole value."""
    if not isinstance(value, list):
        return (False, value)
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            return (False, value)
        word = raw.strip()
        if not word or len(word) > 64:
            return (False, value)
        if word.lower() in seen:
            continue  # duplicates are dropped, not fatal
        seen.add(word.lower())
        cleaned.append(word)
        if len(cleaned) > 128:
            return (False, value)
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
    destructive-command list, matched case-insensitively anywhere in
    the command (same convention as general.terminal_apps). Entries are
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


def _coerce_profile_rules(value: Any) -> tuple[bool, Any]:
    """[profiles].rules: canonical per-app behavior profiles, one dict
    per app matcher. Fields: match (1..32 substrings, required),
    terminal, prompt_profile, instructions, insertion_mode
    (""/typed/paste/auto), formatting_mode (""/gaav), spoken_send
    (""/on/off). Unknown fields reject the whole value so hand-edit
    typos surface at save time."""
    if not isinstance(value, list) or len(value) > 50:
        return (False, value)
    allowed = {"match", "terminal", "prompt_profile", "instructions",
               "insertion_mode", "formatting_mode", "spoken_send"}
    cleaned: list[dict] = []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) - allowed:
            return (False, value)
        patterns_raw = raw.get("match")
        if (not isinstance(patterns_raw, list)
                or not 1 <= len(patterns_raw) <= 32
                or any(not isinstance(p, str) for p in patterns_raw)):
            return (False, value)
        patterns: list[str] = []
        seen: set[str] = set()
        for p in patterns_raw:
            pattern = p.strip()
            if not pattern or len(pattern) > 64 or pattern.lower() in seen:
                continue
            seen.add(pattern.lower())
            patterns.append(pattern)
        if not patterns:
            return (False, value)
        terminal = raw.get("terminal", False)
        if not isinstance(terminal, bool):
            return (False, value)
        prompt_profile = raw.get("prompt_profile", "")
        instructions = raw.get("instructions", "")
        if (not isinstance(prompt_profile, str)
                or len(prompt_profile) > 64
                or not isinstance(instructions, str)
                or len(instructions) > 2000):
            return (False, value)
        if prompt_profile.strip() and instructions.strip():
            return (False, value)  # pick one prompt source per rule
        for key, options in (("insertion_mode",
                              ("", "typed", "paste", "auto")),
                             ("formatting_mode", ("", "gaav")),
                             ("spoken_send", ("", "on", "off"))):
            v = raw.get(key, "")
            if not isinstance(v, str) or v not in options:
                return (False, value)
        rule = {"match": patterns, "terminal": terminal,
                "prompt_profile": prompt_profile,
                "instructions": instructions,
                "insertion_mode": raw.get("insertion_mode", ""),
                "formatting_mode": raw.get("formatting_mode", ""),
                "spoken_send": raw.get("spoken_send", "")}
        cleaned.append(rule)
    return (True, cleaned)
_coerce_profile_rules.kind = "list"  # type: ignore[attr-defined]


@dataclass(frozen=True)
class SettingSpec:
    """One configuration key: everything the daemon, the file writer, the
    socket API and the GTK widgets need to know about it. All derived
    policy (defaults dict, save whitelist, allowed keys, validation
    tables, restart/reload sets, secret masking, template docs) comes
    from REGISTRY — never from a second list.

    - persistence: frozenset over {"save", "settable"} (see PERSIST*).
    - apply_mode: "immediate" (live), "engine" (speech engine reloads on
      change), "restart" (needs a daemon restart to take effect).
    - secret: masked (bool) in every config read that crosses a boundary.
    - empty_meaningful: an EMPTY value is a real setting (not "keep the
      saved file value") when the settings UI saves.
    """

    section: str
    key: str
    default: Any
    coercer: Coercer
    persistence: frozenset[str] = PERSIST
    apply_mode: str = "immediate"
    secret: bool = False
    description: str = ""
    empty_meaningful: bool = False


#: The one registry, in template/save order: (section, key) -> spec.
REGISTRY: dict[tuple[str, str], SettingSpec] = {}


def _spec(section: str, key: str, default: Any, coercer: Coercer, **kw: Any
          ) -> None:
    REGISTRY[(section, key)] = SettingSpec(section, key, default, coercer,
                                           **kw)


# -- [general] ---------------------------------------------------------------

_spec("general", "language", "auto", _coerce_language,
      description='Whisper language code ("auto" detects, or "en", "de", ...)')
_spec("general", "language_cycle", [], _coerce_language_cycle,
      description='Ordered language codes the cycle hotkey steps through, e.g.\n'
                  '["auto", "en", "sl"] - may include "auto"; empty = the cycle\n'
                  'key is off. The cycle override is RUNTIME daemon state and\n'
                  'never persisted.')
_spec("general", "language_whitelist", [], _coerce_language_whitelist,
      description='Wrong-language guard: when language = "auto" detects a\n'
                  'language NOT in this list, the take is re-decoded once with\n'
                  'the first entry, e.g. ["sl", "en"]. Empty = off.')
_spec("general", "copy_to_clipboard", False, _bool(),
      description="Also copy every transcription to the clipboard")
_spec("general", "tray_enabled", True, _bool(),
      description="Panel/tray icon while the daemon runs")
_spec("general", "terminal_apps", [
    "gnome-terminal", "kgx", "konsole", "xterm", "alacritty",
    "kitty", "wezterm", "ghostty", "foot", "tilix", "terminator",
    "guake", "yakuake", "st-256color", "warp",
], _coerce_terminal_apps,
      description="Case-insensitive WM_CLASS substrings identifying\n"
                  "terminals. In these apps spoken-send never presses Enter\n"
                  "(a half-typed shell line would EXECUTE) and typed\n"
                  "insertions gain one trailing space so autocomplete\n"
                  "commits.")
_spec("general", "pause_when_locked", True, _bool(),
      description="Ignore hotkeys and cancel an active dictation while the\n"
                  "session is locked/suspended (logind lock watch; the tray\n"
                  'notes "paused (locked)")')

# -- [hotkey] ----------------------------------------------------------------

_spec("hotkey", "key", "Right_Control", _str(64),
      description='X11 keysym name of the dictation hotkey, e.g.\n'
                  '"Right_Control", "F9", "space", "Pause". Modifier-only\n'
                  'keys (Right_Control / Right_Alt / Right_Shift / Super_R)\n'
                  'only work with mode = "toggle".')
_spec("hotkey", "modifiers", [], _modifiers(),
      description='Extra modifiers to require, e.g. ["ctrl", "shift"]')
_spec("hotkey", "mode", "toggle", _enum("toggle", "hold", "both"),
      description='"toggle": tap to start, tap again to stop & transcribe.\n'
                  '"hold": push-to-talk (non-modifier key only). Other keys\n'
                  "typed during the hold pass through to the focused app\n"
                  "natively (the keyboard is freed for the hold's duration).\n"
                  '"both": a quick tap toggles, a held key talks (250 ms\n'
                  "disambiguation window).")
_spec("hotkey", "cancel_key", "Escape", _str(64),
      description="macOS parity: this key cancels an in-progress dictation\n"
                  '(discards, nothing typed). Grabbed ONLY while recording;\n'
                  '"none" disables.')
_spec("hotkey", "rewrite_key", "", _str(64),
      description="Optional keysym for Rewrite mode (needs [ai])")
_spec("hotkey", "command_key", "", _str(64),
      description="Optional keysym for Command mode (needs [ai])")
_spec("hotkey", "paste_key", "", _str(64),
      description="Optional keysym: re-type the last transcription")
_spec("hotkey", "language_key", "", _str(64),
      description="Optional keysym that cycles the runtime language override\n"
                  "through general.language_cycle (the cycle state itself is\n"
                  "never persisted)")
_spec("hotkey", "extra_shortcuts", [], _coerce_extra_shortcuts,
      description="Up to 2 EXTRA dictation shortcuts (3 total with the\n"
                  "primary), each optionally bound to a named prompt profile\n"
                  '(ai/profiles), e.g. extra_shortcuts = [{key = "F8",\n'
                  'profile = "Terse notes"}]')
_spec("hotkey", "wayland_evdev", False, _bool(),
      description="Wayland (no global grabs): optional physical push-to-talk\n"
                  "read straight from /dev/input (PRIVILEGED: needs the input\n"
                  "group and python-evdev). Off by default; the DE-shortcut\n"
                  "assist is the primary wayland hotkey.")
_spec("hotkey", "wayland_evdev_device", "", _str(128),
      description='Device-name substring matching /dev/input devices, e.g.\n'
                  '"Keyboard"')
_spec("hotkey", "wayland_evdev_key", "KEY_RIGHTCTRL", _str(64),
      description="ecodes KEY_* name held for evdev push-to-talk")

# -- [recording] -------------------------------------------------------------

_spec("recording", "command", "auto", _enum("auto", "pw-record", "parecord"),
      description="auto | pw-record | parecord")
_spec("recording", "device", "", _str(256, allow_empty=True),
      description="Optional PipeWire node target (pw-record --target) /\n"
                  'PulseAudio source; "" = system default (the mic\n'
                  'dropdown\'s "Auto" option)')
_spec("recording", "mic_priority", [], _coerce_mic_priority,
      description="Ordered microphone priority patterns - case-insensitive\n"
                  "substrings of the source name, first match wins, e.g.\n"
                  '["bluez", "usb-cam"] (Bluetooth headset first, then a USB\n'
                  "webcam). When the configured `device` above disappears,\n"
                  "FluidVoice switches to the first available match and\n"
                  "notifies you; switching never happens mid-dictation.")
_spec("recording", "max_seconds", 300, _num("float", 1, 86400),
      description="Maximum recording duration in seconds")
_spec("recording", "skip_silent", False, _bool(),
      description="Skip recordings <= 4s that are pure silence")
_spec("recording", "first_pcm_timeout", 2.0, _num("float", 0.0, 60.0),
      description="Stop early when the microphone sends no audio at all\n"
                  "(muted/wrong device); 0 = off")
_spec("recording", "stall_timeout_s", 8.0, _num("float", 0.0, 300.0),
      description="Cancel the take when the capture stream freezes\n"
                  "mid-dictation for this many seconds (device glitch/route\n"
                  "change) - 0 = off")
_spec("recording", "sample_rate", 16000,
      _reject("fixed hardware constant (16 kHz mono FLAC)"),
      persistence=PERSIST_NONE,
      description="Fixed hardware capture rate - the whisper models want\n"
                  "16 kHz mono; not UI-settable")
_spec("recording", "spoken_send_enabled", False, _bool(),
      description="Spoken-send: a trailing phrase strips and presses Enter\n"
                  "after typing")
_spec("recording", "spoken_send_phrase", "send it", _str(64),
      description="Trailing phrase that strips and presses Enter")
_spec("recording", "spoken_send_key", "enter",
      _enum("enter", "shift+enter", "ctrl+enter"),
      description="Key pressed by spoken-send: enter | shift+enter |\n"
                  "ctrl+enter")
_spec("recording", "spoken_send_countdown_s", 1.2,
      _coerce_spoken_send_countdown,
      description="Quiet countdown after the phrase: with the phrase at the\n"
                  "end and 0.5 s of silence, a countdown of this many seconds\n"
                  "finishes the take by itself (speak again to cancel);\n"
                  "0 = off. Needs spoken_send_enabled.")
_spec("recording", "preview_enabled", True, _bool(),
      description="Live transcription preview while recording")
_spec("recording", "preview_mode", "auto",
      _enum("auto", "overlay", "notify"),
      description="auto (pill, falls back) | overlay | notify")
_spec("recording", "preview_interval", 1.2, _num("float", 0.3, 10.0),
      description="Seconds between partial passes")
_spec("recording", "preview_min_audio", 1.0, _num("float", 0.3, 10.0),
      description="Seconds before the first partial")
_spec("recording", "preview_bottom_offset", 64, _num("int", 0, 400),
      description="Pill px above the screen bottom edge")
_spec("recording", "preview_overlay_size", "medium",
      _enum("pill", "small", "medium", "large"),
      description="pill | small | medium | large (macOS sizes)")
_spec("recording", "preview_segmented", True, _bool(),
      description="Segmented streaming preview: fixed windows (50% hop), one\n"
                  "decode per tick instead of re-decoding the whole take")
_spec("recording", "preview_segment_s", 2.0, _num("float", 1.0, 6.0),
      description="Segment window length in seconds (larger windows track the\n"
                  "final text more closely - measured F1 vs the final decode\n"
                  "0.26/0.49/0.57 at 2/3/4 s on hard audio - at higher\n"
                  "per-window decode cost)")
_spec("recording", "preview_conf_gate", True, _bool(),
      description="Hide preview text the model scored below its low-confidence\n"
                  "band (wrong-word suppression on hard audio; the pill's level\n"
                  "bars still show activity)")
_spec("recording", "preview_vad_silence_s", 2.0, _num("float", 0.0, 10.0),
      description="Trailing-silence VAD auto-stops the take after this many\n"
                  "seconds of quiet; 0 disables the auto-stop (the hotkey\n"
                  "stops every take)")
_spec("recording", "overlay_chips", True, _bool(),
      description="Hover action chips above the pill (X11)")
_spec("recording", "pause_media", True, _bool(),
      description="Pause MPRIS players while dictating (resume after)")
_spec("recording", "push_to_talk_button", "", _coerce_button_spec,
      empty_meaningful=True,
      description="Mouse push-to-talk: hold this button to dictate (always\n"
                  'hold-style, independent of hotkey.mode). "button8"/"b8"/"8"\n'
                  "- buttons 6-255 only; 1-5 (click/scroll) are refused, they\n"
                  "would break the desktop. Thumb buttons are usually 8/9\n"
                  '(6/7 on some mice). Empty = off.')
_spec("recording", "push_to_talk_modifiers", [], _modifiers(),
      description='Extra modifiers to require for the button, e.g. ["ctrl"]')

# -- [model] -----------------------------------------------------------------

_spec("model", "backend", "auto",
      _enum("auto", "faster-whisper", "whisper-torch", "whisper.cpp",
            "parakeet", "remote"),
      apply_mode="engine",
      description="auto | faster-whisper | whisper-torch | whisper.cpp |\n"
                  "parakeet")
_spec("model", "name", "auto", _coerce_model_name,
      apply_mode="engine",
      description='auto -> "small" when CUDA is available, "base" otherwise;\n'
                  "or one of tiny, base, small, medium, large-v3,\n"
                  'large-v3-turbo; with backend="parakeet": parakeet catalog\n'
                  "names")
_spec("model", "device", "auto", _enum("auto", "cuda", "cpu"),
      apply_mode="engine", description="auto | cuda | cpu")
_spec("model", "compute", "auto", _enum("auto", "float16", "int8"),
      apply_mode="engine", description="auto | float16 | int8")
_spec("model", "whispercpp_model", "", _str(4096),
      apply_mode="engine",
      description="ggml/gguf model for the whisper.cpp backend: a catalog\n"
                  "name (ggml-base.bin, ggml-small.en.bin, ...) or a path to\n"
                  "a file")
_spec("model", "eager_warmup", True, _bool(),
      apply_mode="restart",
      description="Load the model at daemon start (preview-ready). Changing\n"
                  "this needs a daemon restart.")
_spec("model", "idle_unload_s", 0, _coerce_idle_unload,
      description="Unload the speech model after this many idle seconds to\n"
                  "free RAM/VRAM (0 = keep it loaded forever; range 30..86400\n"
                  "when set). The next dictation after an unload pays the\n"
                  "model load time again.")
_spec("model", "languages", {}, _coerce_model_languages,
      description='Per-model language overrides, e.g.\n'
                  'languages = { small = "de", "ggml-base.en.bin" = "en" }.\n'
                  '"auto" = always detect for that model; a missing key\n'
                  "follows general.language (read per-dictation, applies\n"
                  "live).")
_spec("model", "remote_url", "", _coerce_remote_url,
      apply_mode="engine", empty_meaningful=True,
      description="Remote STT server (OpenAI-compatible\n"
                  "/v1/audio/transcriptions) - local-first: nothing leaves\n"
                  'this machine while remote_url is empty. Point it at a LAN\n'
                  "GPU box (vLLM/whisper.cpp server/NIM/DGX Spark) or any\n"
                  'compatible cloud, e.g. "http://192.168.1.50:8000".')
_spec("model", "remote_model", "whisper-large-v3", _str(256),
      apply_mode="engine", description="Model name sent in the remote form")
_spec("model", "remote_api_key", "", _coerce_remote_api_key,
      apply_mode="engine", secret=True,
      description="Optional bearer token for the remote server (masked,\n"
                  "never logged)")
_spec("model", "remote_timeout_s", 30, _num("float", 5.0, 600.0),
      apply_mode="engine", description="Per-request timeout (5..600)")
_spec("model", "hotwords", [], _coerce_hotwords,
      apply_mode="engine",
      description="Custom vocabulary biasing (upstream #916 - words to ADD,\n"
                  "unlike the dictionary's replacements): fed to the decoder\n"
                  'as hints, e.g. ["SayItErmano", "PipeWire"] - <=20 focused\n'
                  "words: long lists over-bias the decoder. Changing this key\n"
                  "reloads the speech engine.")

# -- [processing] ------------------------------------------------------------

_spec("processing", "remove_filler_words", True, _bool(),
      description="Remove filler words (um, uh, hmm...) before punctuation\n"
      "formatting")
_spec("processing", "filler_words", list(DEFAULT_FILLERS), _filler_words(),
      description="Filler words removed before punctuation formatting")
_spec("processing", "punctuation_enabled", True, _bool(),
      description="Spoken punctuation commands enabled")
_spec("processing", "punctuation_prefix", "literal", _str(32),
      description='Spoken commands require this prefix word: "literal comma"\n'
                  '-> ","')
_spec("processing", "formatting_action_triggers", {}, _coerce_action_triggers,
      description="Extra spoken formatting-action trigger aliases (action\n"
                  "name -> aliases, on top of the built-ins), e.g.\n"
                  'formatting_action_triggers = {new_line = ["nova vrstica"]}')
_spec("processing", "dictionary", [], _dictionary(),
      description='Custom dictionary: phrases replaced on insert, e.g.\n'
                  '[[{ triggers = ["miro board"], replacement = "Miro board" }]]')
_spec("processing", "gaav_enabled", False, _bool(),
      description="GAAV: casual/search-field formatting of the final text")
_spec("processing", "gaav_lowercase_first", True, _bool(),
      description="Lowercase the first letter (GAAV)")
_spec("processing", "gaav_remove_trailing_period", True, _bool(),
      description="Remove the trailing period (GAAV)")
_spec("processing", "slash_mention_squeeze", True, _bool(),
      description='Chat-app literal squeeze: "/ fix the deploy" -> "/fix the\n'
                  'deploy", "@ John Smith" -> "@John Smith" (runs after AI\n'
                  "cleanup, before GAAV)")

# -- [ai] --------------------------------------------------------------------

_spec("ai", "enabled", False, _bool(),
      description="Optional AI polish of the raw transcript (FluidVoice's\n"
      "headline feature). Any OpenAI-compatible /v1/chat/completions\n"
      "endpoint works: OpenAI, Groq, Ollama, LM Studio, ...")
_spec("ai", "base_url", "http://localhost:11434/v1", _str(2048),
      description="OpenAI-compatible chat endpoint, e.g.\n"
      "http://localhost:11434/v1 (Ollama) or http://localhost:1234/v1\n"
      "(LM Studio)")
_spec("ai", "model", "", _str(256),
      description="Chat model name, e.g. qwen3:8b")
_spec("ai", "api_key", "", _reject("env/file only - never socket-settable"),
      persistence=PERSIST_SAVE_ONLY, secret=True,
      description="API key (preferred: leave empty and export the env var\n"
      "below). Socket-settable it is NOT: edit the file or use the env\n"
      "var - a save carries the stored key over.")
_spec("ai", "api_key_env", "SAYITERMANO_API_KEY", _str(128),
      description="Environment variable holding the API key")
_spec("ai", "temperature", 0.2, _num("float", 0.0, 2.0),
      description="Sampling temperature")
_spec("ai", "timeout_seconds", 120, _num("float", 1, 3600),
      description="Request timeout")
_spec("ai", "max_retries", 3, _coerce_max_retries,
      description="Retries per request")
_spec("ai", "per_app_prompts", [], _coerce_per_app_prompts,
      description='Upstream per-app prompt sets: [{"apps": ["zed"],\n'
                  '"instructions": "..."}] (first match wins)')
_spec("ai", "base_prompt", "", _coerce_base_prompt,
      empty_meaningful=True,
      description="Custom base prompt for AI polish (empty = the built-in\n"
      "dictation prompt; Settings -> AI can save named presets of it)")
_spec("ai", "refusal_guard", True, _bool(),
      description="Refusal guardrail: a polish reply that reads as a model\n"
      'refusal ("I\'m sorry, I can\'t assist...") never reaches the doc -\n'
      "the raw transcript is typed instead, with a notification. false =\n"
      "trust the model blindly.")

# -- [insertion] -------------------------------------------------------------

_spec("insertion", "mode", "typed", _enum("auto", "typed", "paste"),
      description="typed: simulate keystrokes (xdotool type). paste:\n"
      "clipboard + Ctrl+V (restores your clipboard afterwards). auto:\n"
      "typed, falling back to paste for very long texts")
_spec("insertion", "type_delay_ms", 8, _num("int", 0, 1000),
      description="Delay between simulated keystrokes")
_spec("insertion", "paste_threshold_chars", 1200,
      _num("int", 1, 1_000_000),
      description="Longer texts use clipboard paste")
_spec("insertion", "terminal_autocomplete_space", True, _bool(),
      description="One trailing space after typed insertions in terminal\n"
      "apps (general.terminal_apps) so the shell's autocomplete commits\n"
      "the last token")
_spec("insertion", "verify_paste", True, _bool(),
      description="Verify the paste landed (selection read by the target)\n"
      "before restoring the clipboard; false = legacy fixed-delay restore")
_spec("insertion", "terminal_paste_key", "ctrl+shift+v", _str(32),
      description="Keystroke used to paste in terminal apps\n"
      "(general.terminal_apps); X11 terminals pass ctrl+v through to the\n"
      "app, they need ctrl+shift+v")
_spec("insertion", "wayland_tool", "auto", _enum("auto", "wtype", "ydotool"),
      description="Wayland typing tool: auto | wtype | ydotool (wtype needs\n"
      "wlroots/KDE; ydotool works everywhere but needs ydotoold +\n"
      "/dev/uinput access). Ignored on X11 sessions.")

# -- [context] / [profiles] (P2: insertion-time context + behavior profiles)

_spec("context", "enabled", False, _bool(),
      description="Insertion-time focused-field context (P2 seam): app\n"
                  "identity, accessible role, selection and bounded\n"
                  "preceding text, read ONCE right before typing and never\n"
                  "stored (nothing reaches history/config/logs). Off by\n"
                  "default - prototype-grade; complete the Wayland smoke\n"
                  "matrix (docs/dev/wayland-smoke-matrix.md) before\n"
                  "relying on it.")
_spec("context", "provider", "auto",
      _enum("auto", "x11", "atspi", "none"),
      description="Context backend: auto (wayland -> atspi, x11 -> x11) |\n"
                  "x11 (WM_CLASS identity only) | atspi (identity + role +\n"
                  "selection + preceding text; works on x11 too) | none.\n"
                  "Missing dependencies degrade to no context, never crash.")
_spec("context", "max_preceding_chars", 120, _num("int", 0, 500),
      description="Hard cap on the focused field text preceding the caret\n"
                  "read for sentence continuation (0..500; 0 disables the\n"
                  "continuation read). Enforced again inside FocusContext,\n"
                  "whatever the provider returns.")
_spec("profiles", "rules", [], _coerce_profile_rules,
      description="Canonical per-app behavior profiles, first match wins\n"
                  '(case-insensitive substrings of the app identity, "*"\n'
                  "matches everything): rules = [{ match =\n"
                  '["gnome-terminal", "kgx"], terminal = true, spoken_send\n'
                  '= "off" }]. Fields: terminal, prompt_profile (a named\n'
                  "AI prompt preset), instructions (inline prompt text),\n"
                  "insertion_mode (typed|paste|auto), formatting_mode\n"
                  "(gaav), spoken_send (on|off); empty = inherit the global\n"
                  "setting. Legacy general.terminal_apps and\n"
                  "ai.per_app_prompts stay read as fallback until v1.0 and\n"
                  "migrate into rules on the first settings save.")
_spec("profiles", "migrated_from_legacy", False, _bool(),
      persistence=PERSIST_SAVE_ONLY,
      description="Set once the first settings save migrated legacy\n"
                  "per-app prompts / terminal lists into profiles.rules\n"
                  "(migration marker; not socket-settable).")

# -- [sounds] / [notifications] / [updates] ----------------------------------

_spec("sounds", "enabled", True, _bool(),
      description="Play start/stop sounds")
_spec("sounds", "volume", 1.0, _num("float", 0.0, 1.0),
      description="0.0 - 1.0")

_spec("notifications", "enabled", True, _bool(),
      description="Desktop notifications")

_spec("updates", "check", True, _bool(),
      description="Check GitHub releases for a newer version (once per\n"
      "daemon start + daily). The updater NEVER installs anything - it\n"
      "notifies and prints the upgrade command. Set check = false to\n"
      "disable every probe (SAYITERMANO_SKIP_UPDATE_CHECK=1 does the same\n"
      "per-run).")
_spec("updates", "notify", True, _bool(),
      description="Desktop notification when a newer release is first seen")

# -- [history] ---------------------------------------------------------------

_spec("history", "save", True, _bool(),
      description="Save transcriptions to history")
_spec("history", "save_audio", False, _bool(),
      description="Store the recording with each entry")
_spec("history", "audio_budget_gb", 4.0, _num("float", 0.0, 1024.0),
      description="Retained-audio budget in GB")

# -- [command] ---------------------------------------------------------------

_spec("command", "max_turns", 4, _num("int", 1, 20),
      description="Command-mode agent loop bound (upstream: 20)")
_spec("command", "working_dir", "", _str(4096),
      description="Working directory for commands (empty = $HOME)")
_spec("command", "timeout_seconds", 60.0, _num("float", 1, 3600),
      description="Per-command subprocess timeout")
_spec("command", "confirm_timeout_s", 120.0, _num("float", 5, 600),
      description="Auto-cancel a pending confirmation after this many\n"
      "seconds")
_spec("command", "destructive_patterns", [], _coerce_destructive_patterns,
      description="Additions to the built-in destructive-command list (rm,\n"
      "mv, sudo, kill, chmod, chown, dd, mkfs, truncate, shred, pipes into\n"
      "rm/sudo, ...): commands MATCHING any of these substrings\n"
      "case-insensitively need the strong confirmation (the command hotkey\n"
      'twice). e.g. destructive_patterns = ["git push", "shutdown"]')
_spec("command", "context_window_s", 300.0, _num("float", 0, 86400),
      description="Follow-up context: seconds the last 5 executed command\n"
      "results stay available to the next voice command in the SAME\n"
      'focused app (0 disables). Say "new session" to clear it\n'
      "immediately; nothing is persisted, a daemon restart starts cold.")


# ---------------------------------------------------------------------------
# Registry lookups
# ---------------------------------------------------------------------------

def default(section: str, key: str) -> Any:
    """The registered default for one setting (None if unknown)."""
    spec = REGISTRY.get((section, key))
    return None if spec is None else spec.default


def coerce_setting(section: str, key: str, value: Any) -> tuple[bool, Any]:
    """Validate one value through its registry spec.
    Returns (ok, coerced); unknown keys reject."""
    spec = REGISTRY.get((section, key))
    if spec is None:
        return (False, value)
    return spec.coercer(value)


#: Registry-style alias used by new callers (coerce_setting keeps its
#: historical name for compatibility).
coerce = coerce_setting


def validate(section: str, key: str, value: Any) -> bool:
    """ok-only form of coerce_setting."""
    return coerce_setting(section, key, value)[0]


def is_secret(section: str, key: str) -> bool:
    spec = REGISTRY.get((section, key))
    return spec is not None and spec.secret


def enum_options(section: str, key: str) -> tuple[str, ...] | None:
    """The ordered enum options for a setting (None if not an enum) -
    widgets derive their combo values from this instead of hardcoding."""
    spec = REGISTRY.get((section, key))
    if spec is None:
        return None
    return getattr(spec.coercer, "option_order", None)


def ui_range(section: str, key: str) -> tuple[float, float] | None:
    """Numeric bounds for a setting (widgets derive spin-button ranges
    from this instead of hardcoding). None when the setting has no
    numeric range (bools, enums, structured values)."""
    spec = REGISTRY.get((section, key))
    if spec is None:
        return None
    return getattr(spec.coercer, "bounds", None)


def save_keys() -> dict[str, list[str]]:
    """Ordered section -> keys the settings UI owns (persists to file)."""
    out: dict[str, list[str]] = {}
    for (section, key), spec in REGISTRY.items():
        if "save" in spec.persistence:
            out.setdefault(section, []).append(key)
    return out


def restart_keys() -> set[str]:
    """'section.key' strings that need a daemon restart to take effect."""
    return {f"{s}.{k}" for (s, k), spec in REGISTRY.items()
            if spec.apply_mode == "restart"}


def reload_keys() -> set[str]:
    """'section.key' strings whose change hot-reloads the speech engine."""
    return {f"{s}.{k}" for (s, k), spec in REGISTRY.items()
            if spec.apply_mode == "engine"}


def masked(cfg: dict) -> dict:
    """Deep copy with every secret replaced by a bool (api-key rule)."""
    safe = copy.deepcopy(cfg)
    for (section, key) in SECRET_KEYS:
        val = safe.get(section, {}).get(key, "")
        safe.setdefault(section, {})[key] = bool(val)
    return safe


# Historical alias (the daemon and client call mask_secrets).
mask_secrets = masked


# ---------------------------------------------------------------------------
# Tables DERIVED from the registry (names kept for compatibility)
# ---------------------------------------------------------------------------

DEFAULTS: dict[str, Any] = {}
for _spec_obj in REGISTRY.values():
    DEFAULTS.setdefault(_spec_obj.section, {})[_spec_obj.key] = _spec_obj.default
del _spec_obj

#: Keys the socket API / settings UI may set (apply_settings whitelist).
ALLOWED_SETTINGS: dict[str, set] = {}
for (_sec, _key), _sp in REGISTRY.items():
    if "settable" in _sp.persistence:
        ALLOWED_SETTINGS.setdefault(_sec, set()).add(_key)
del _sec, _key, _sp

#: Ordered section -> keys save_config writes (policy: values for keys
#: the UI does not manage are carried over from the existing file).
_SAVE_WHITELIST: dict[str, list[str]] = save_keys()

#: (section, key) -> ("float"|"int"|"str", bound-or-range) for every
#: factory-made range coercer in the registry.
SETTING_RANGES: dict[tuple[str, str], Any] = {
    (s, k): c.range_rule
    for (s, k), spec in REGISTRY.items()
    for c in [spec.coercer] if hasattr(c, "range_rule")
}

SETTING_ENUMS: dict[tuple[str, str], set] = {
    (s, k): set(spec.coercer.options)
    for (s, k), spec in REGISTRY.items()
    if getattr(spec.coercer, "kind", None) == "enum"
}

SETTING_BOOLS = {(s, k) for (s, k), spec in REGISTRY.items()
                 if getattr(spec.coercer, "kind", None) == "bool"}

#: list-valued pass-through keys the UI owns
SETTING_LISTS = tuple(
    (s, k) for (s, k), spec in REGISTRY.items()
    if getattr(spec.coercer, "kind", None) == "list"
)

#: settings whose change requires a daemon restart
RESTART_REQUIRED: set[str] = restart_keys()

#: settings whose change reloads the speech engine (live)
ENGINE_KEYS: set[str] = reload_keys()

#: Keys where an EMPTY value is meaningful (not "keep the saved value"):
#: e.g. ai.base_prompt = "" restores the built-in prompt, so a cleared
#: editor must actually clear the file instead of carrying the old value.
_EMPTY_IS_MEANINGFUL = {(s, k) for (s, k), spec in REGISTRY.items()
                        if spec.empty_meaningful}

#: (section, key) pairs masked in every config read crossing a boundary
SECRET_KEYS = frozenset((s, k) for (s, k), spec in REGISTRY.items()
                        if spec.secret)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# The commented template — DERIVED from the registry
# ---------------------------------------------------------------------------

def _toml_value(value: Any) -> str:
    import json
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # JSON escaping is valid TOML basic-string escaping EXCEPT DEL
        # (U+007F), which JSON happily leaves raw and TOML forbids — a
        # value carrying it made the saved config unparseable (found by
        # the Q5 save->load->save property test)
        out = json.dumps(value, ensure_ascii=False)
        if "\x7f" in value:
            out = out.replace("\x7f", "\\u007F")
        return out
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


def template_text() -> str:
    """Render the fully commented example config from the registry: one
    [section] per registry section, every key with its description as
    comments and its registered default as the value. The result is
    valid TOML (every line active) — deleting lines falls back to the
    same defaults."""
    lines = [
        "# SayItErmano configuration.",
        "# Delete any line to fall back to the built-in default.",
        "",
    ]
    by_section: dict[str, list[SettingSpec]] = {}
    for spec in REGISTRY.values():
        by_section.setdefault(spec.section, []).append(spec)
    for section, specs in by_section.items():
        lines.append(f"[{section}]")
        for spec in specs:
            if spec.description:
                for ln in spec.description.splitlines():
                    lines.append(f"# {ln}".rstrip())
            lines.append(f"{spec.key} = {_toml_value(spec.default)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


#: The commented template (write_template writes this; derived — do not
#: edit by hand, change the registry descriptions/defaults instead).
TEMPLATE = template_text()


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
# existing file so a save never loses them. The whitelist is derived from
# the registry (save_keys()).
# ---------------------------------------------------------------------------

def save_config(cfg: dict, path: Path | None = None) -> Path:
    """Persist the whitelisted config keys as TOML, carrying over api_key.

    P2: the first save also migrates legacy per-app prompts and a
    customized terminal list into canonical profiles.rules (one-time,
    idempotent - see profiles.migrate_legacy_profiles)."""
    path = path or paths.config_file()
    from .profiles import migrate_legacy_profiles  # lazy: no import cycle
    migrate_legacy_profiles(
        cfg, default_terminal_apps=DEFAULTS["general"]["terminal_apps"])
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
# Settings validation - single source of truth for the socket API, the
# GTK app and file-only saves (moved from webui.py per the native-app
# spec; registry-driven since P1.3).
# ---------------------------------------------------------------------------

def apply_settings(cfg: dict, body: dict) -> tuple[list[str], list[str]]:
    """Whitelisted, validated merge of {section: {key: value}} into cfg
    (mutates cfg; no save). Returns (changed, rejected) as 'section.key'
    strings. Unknown keys and bad types are rejected, never half-applied."""
    changed: list[str] = []
    rejected: list[str] = []
    for (section, key), spec in REGISTRY.items():
        if "settable" not in spec.persistence:
            continue  # e.g. ai.api_key (env/file only), recording.sample_rate
        if section in body and key in body[section]:
            ok, value = spec.coercer(body[section][key])
            if not ok:
                rejected.append(f"{section}.{key}")
                continue
            if cfg.get(section, {}).get(key) != value:
                changed.append(f"{section}.{key}")
            cfg.setdefault(section, {})[key] = value
    return changed, rejected
