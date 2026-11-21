"""UI translations — GNU gettext wiring for every user-facing string.

Source language is English (the msgids); compiled catalogs live in
fluidvoice/locale/<code>/LC_MESSAGES/sayit-ermano.mo. The catalogs are
built from po/*.po by scripts/build_translations.py and committed, so
wheels/debs ship translations without a build-time gettext dependency.

Language resolution order (first match wins):

1. SAYITERMANO_UI_LANGUAGE env var (tests, screenshots, power users)
2. the [general] ui_language config key ("system" = follow the desktop)
3. the system locale (LANGUAGE/LC_ALL/LC_MESSAGES/LANG; "sr" resolves to
   Cyrillic unless the locale names Latin explicitly)

set_language() swaps the active catalog at runtime and notifies change
listeners — the GTK app rebuilds its windows, the daemon rebuilds the
tray menu/tooltip on the next refresh, notifications translate at send
time. No restart is needed anywhere.

This module is deliberately GTK-free and dependency-free: the daemon,
the GTK app, tests and the extraction tooling all import it.
"""
from __future__ import annotations

import gettext as _gettext
import os
from pathlib import Path
from threading import Lock
from typing import Callable

DOMAIN = "sayit-ermano"
LOCALE_DIR = Path(__file__).parent / "locale"

#: The "follow the desktop" sentinel (stored in the config).
SYSTEM = "system"

#: Shipped UI languages: code -> native endonym (never translated —
#: every user reads their own language's name in its own script).
LANGUAGES: dict[str, str] = {
    "en": "English",
    "sr-Latn": "Srpski (latinica)",
    "sr": "Српски (ћирилица)",
    "bs": "Bosanski",
}

#: Selectable settings values, in combo order.
LANGUAGE_OPTIONS: list[str] = [SYSTEM, *LANGUAGES]

ENV_OVERRIDE = "SAYITERMANO_UI_LANGUAGE"

_lock = Lock()
_current: str = "en"          # resolved code (never SYSTEM)
_configured: str = SYSTEM     # what init chose before resolution
_translation: _gettext.NullTranslations | None = None
_listeners: list[Callable[[str], None]] = []


def available_codes() -> list[str]:
    """Language codes with a compiled catalog on disk (source included)."""
    codes = ["en"]
    for code in LANGUAGES:
        if (LOCALE_DIR / code / "LC_MESSAGES" / f"{DOMAIN}.mo").exists():
            codes.append(code)
    return codes


def _load(code: str) -> _gettext.NullTranslations:
    mo = LOCALE_DIR / code / "LC_MESSAGES" / f"{DOMAIN}.mo"
    if mo.exists():
        try:
            with open(mo, "rb") as fh:
                return _gettext.GNUTranslations(fh)
        except Exception:  # noqa: S110 — corrupt catalog must not kill the app
            pass
    return _gettext.NullTranslations()


def _normalize(raw: str) -> str | None:
    """One locale token -> our code (None when not a shipped language).

    Accepts the usual spellings: sr, sr_RS, sr-Latn, sr-Latn-RS,
    sr@latin, bs_BA.UTF-8 …"""
    token = raw.strip().split(".", 1)[0]
    modifier = ""
    if "@" in token:
        token, modifier = token.split("@", 1)
        modifier = modifier.lower()
    parts = [p for p in token.replace("-", "_").lower().split("_") if p]
    if not parts:
        return None
    base = parts[0]
    rest = "_".join(parts[1:])
    if base == "en":
        return "en"
    if base == "bs":
        return "bs"
    if base == "sr":
        # plain "sr" (GNU/ISO default) is Cyrillic; Latin must be named
        latin = "latn" in rest or "latin" in rest or modifier in ("latn",
                                                                 "latin")
        return "sr-Latn" if latin else "sr"
    return None


def resolve_system_language() -> str:
    """The system-locale preference mapped onto a shipped code."""
    for var in (ENV_OVERRIDE, "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        # ENV_OVERRIDE is exact ("sr-Latn"); the locale vars are lists
        for token in os.environ.get(var, "").split(":"):
            code = _normalize(token)
            if code is not None:
                return code
    return "en"


def init_i18n(cfg: dict | None = None) -> str:
    """Resolve + install the startup language. Returns the resolved code."""
    value = os.environ.get(ENV_OVERRIDE, "")
    if not value:
        value = str((cfg or {}).get("general", {}).get("ui_language", SYSTEM))
    return set_language(value)


def set_language(value: str) -> str:
    """Activate `value` ("system" or a LANGUAGES code). Returns the
    resolved code; change listeners fire only when it actually moved."""
    global _current, _configured, _translation
    value = str(value or SYSTEM)
    _configured = value
    resolved = resolve_system_language() if value == SYSTEM else value
    if resolved not in LANGUAGES:
        resolved = "en"
    with _lock:
        changed = resolved != _current
        _current = resolved
        _translation = _load(resolved)
    if changed:
        for cb in list(_listeners):
            try:
                cb(resolved)
            except Exception:  # noqa: S110 — a broken listener must not block
                pass
    return resolved


def register_on_change(cb: Callable[[str], None]) -> None:
    """Notify `cb(resolved_code)` whenever the language changes."""
    _listeners.append(cb)


def current_language() -> str:
    """The resolved code in effect ("en", "sr-Latn", ...)."""
    return _current


def configured_language() -> str:
    """The raw configured value (may be "system")."""
    return _configured


def _(msgid: str) -> str:
    """Translate one user-facing string (English source)."""
    t = _translation
    return t.gettext(msgid) if t is not None else msgid


def _n(singular: str, plural: str, n: int) -> str:
    """Plural-aware translation (Serbian/Bosnian have three forms)."""
    t = _translation
    if t is None:
        return singular if n == 1 else plural
    return t.ngettext(singular, plural, n)


def combo_choices() -> list[tuple[str, str]]:
    """(label, value) rows for the Settings language selector: the
    translated "System default" plus every shipped language by its
    native name."""
    return [(_("System default"), SYSTEM)] + [
        (LANGUAGES[c], c) for c in LANGUAGES
    ]
