"""Per-app behavior profiles (P2): the canonical `[profiles]` config
section consulted whenever the insertion target's app is known.

One rule:

.. code-block:: toml

    profiles.rules = [
      { match = ["gnome-terminal", "kgx"], terminal = true,
        spoken_send = "off" },
      { match = ["zed"], instructions = "Bullet style, no greetings.",
        insertion_mode = "typed", formatting_mode = "gaav" },
    ]

`match` patterns are case-insensitive substrings of the app identity
(same convention as `general.terminal_apps` / `ai.per_app_prompts`);
"*" matches everything; first match wins. Empty policy fields inherit
the global settings.

Resolution precedence (until v1.0):

1. the first matching canonical rule;
2. otherwise the LEGACY keys (`general.terminal_apps`,
   `ai.per_app_prompts`) — still read as fallback, exactly today's
   matching semantics;
3. otherwise the all-inherit default.

`migrate_legacy_profiles` materializes legacy prompts + a customized
terminal list into canonical rules on the first settings save
(idempotent; see its docstring).
"""
from __future__ import annotations

from dataclasses import dataclass

INSERTION_MODES = ("", "typed", "paste", "auto")
FORMATTING_MODES = ("", "gaav")
SPOKEN_SEND_MODES = ("", "on", "off")
MAX_RULES = 50
MAX_PATTERNS = 32


@dataclass(frozen=True)
class AppProfile:
    """Resolved per-app behavior. Empty fields mean 'inherit global'."""

    terminal: bool = False
    prompt_profile: str = ""
    instructions: str = ""
    insertion_mode: str = ""
    formatting_mode: str = ""
    spoken_send: str = ""


#: The all-inherit profile (no rule matched, no legacy data).
DEFAULT_PROFILE = AppProfile()


def _patterns_match(patterns, app: str) -> bool:
    app = app.lower()
    for raw in patterns:
        pattern = str(raw).strip().lower()
        if pattern == "*" or (pattern and pattern in app):
            return True
    return False


def match_rule(rules: list, app_id: str | None):
    """The first canonical rule matching `app_id` (None when nothing
    matches / no identity). Malformed entries are skipped."""
    if not app_id or not isinstance(rules, list):
        return None
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        patterns = rule.get("match")
        if isinstance(patterns, list) and _patterns_match(patterns, app_id):
            return rule
    return None


def _field(rule: dict, key: str,
           allowed: tuple[str, ...] | None = None) -> str:
    """A stripped string field; off-spec values degrade to ""."""
    value = rule.get(key, "")
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if allowed is not None and value not in allowed:
        return ""
    return value


def rule_profile(rule: dict) -> AppProfile:
    """AppProfile built from one canonical rule (fields are validated
    by the config coercer on the way in; anything off-spec degrades to
    inherit — belt and braces for hand-edited files)."""
    return AppProfile(
        terminal=bool(rule.get("terminal", False)),
        prompt_profile=_field(rule, "prompt_profile"),
        instructions=_field(rule, "instructions"),
        insertion_mode=_field(rule, "insertion_mode", INSERTION_MODES),
        formatting_mode=_field(rule, "formatting_mode", FORMATTING_MODES),
        spoken_send=_field(rule, "spoken_send", SPOKEN_SEND_MODES),
    )


def _legacy_terminal(app_id: str, cfg: dict) -> bool:
    """The pre-P2 check, verbatim: case-insensitive substring over
    general.terminal_apps."""
    apps = cfg.get("general", {}).get("terminal_apps") or []
    app = app_id.lower()
    return any(str(p).lower() in app for p in apps if p)


def resolve_profile(cfg: dict, app_id: str | None) -> AppProfile:
    """The active behavior profile for one app identity."""
    if not app_id:
        return DEFAULT_PROFILE
    rules = (cfg.get("profiles", {}) or {}).get("rules") or []
    rule = match_rule(rules, app_id)
    if rule is not None:
        return rule_profile(rule)
    if _legacy_terminal(app_id, cfg):
        return AppProfile(terminal=True)
    return DEFAULT_PROFILE


def prompt_instructions_for(cfg: dict, app_hint: str | None) -> str | None:
    """AI-polish instructions for the target app: canonical rule
    (inline `instructions`, else the named `prompt_profile` preset),
    then the legacy `ai.per_app_prompts` rules — identical to today's
    result when no canonical rules exist."""
    if not app_hint:
        return None
    rule = match_rule((cfg.get("profiles", {}) or {}).get("rules") or [],
                      app_hint)
    if rule is not None:
        instructions = rule.get("instructions")
        if isinstance(instructions, str) and instructions.strip():
            return instructions.strip()
        name = rule.get("prompt_profile")
        if isinstance(name, str) and name.strip():
            from .ai.profiles import load_profiles
            preset = load_profiles().get(name.strip())
            if preset and preset.strip():
                return preset.strip()
    from .processing.per_app import match_app_prompt
    return match_app_prompt(cfg.get("ai", {}).get("per_app_prompts", []),
                            app_hint)


def spoken_send_allowed(profile: AppProfile, *, default_enabled: bool,
                        phrase_present: bool) -> tuple[bool, str]:
    """Resolve the spoken-send policy under one profile.

    Returns (press_enter, why). `default_enabled` is the global
    recording.spoken_send_enabled; `phrase_present` is whether the
    trailing phrase was spoken this take. Profiles: "on" = allow Enter
    even in a terminal (explicit override); "off" = never press Enter
    for this app (what migrated terminal rules carry)."""
    if profile.spoken_send == "off":
        return False, "off (profile)"
    if profile.spoken_send == "on":
        return bool(default_enabled and phrase_present), "on (profile)"
    # inherit: the legacy terminal suppression, exactly as before
    if profile.terminal:
        return False, "terminal app"
    return bool(default_enabled and phrase_present), "global"


# ---------------------------------------------------------------------------
# Legacy migration (first settings save; idempotent)
# ---------------------------------------------------------------------------

def migrate_legacy_profiles(cfg: dict, *,
                            default_terminal_apps: list | None = None,
                            ) -> list[dict]:
    """Materialize legacy per-app prompts and a CUSTOMIZED terminal list
    into canonical `profiles.rules` — one-time per rules-empty state.

    - Guarded by `profiles.migrated_from_legacy`: once a migration has
      written rules, it never runs again (idempotent saves).
    - Legacy prompts -> rules `{match: apps, instructions: ...}`.
    - A terminal list that differs from the shipped default -> one rule
      `{match: [...], terminal: true, spoken_send = "off"}` (the default
      list keeps applying through the legacy read fallback — migrating
      it would just duplicate shipped behavior for every user).
    - Existing canonical rules are NEVER overwritten.
    - Legacy keys are left untouched in cfg (they remain the read
      fallback until v1.0).

    Mutates cfg["profiles"]; returns the rules it wrote ([] = nothing
    to migrate / already migrated).
    """
    prof = cfg.setdefault("profiles", {})
    if prof.get("migrated_from_legacy"):
        return []
    existing = prof.get("rules") or []
    made: list[dict] = []
    for raw in ((cfg.get("ai", {}) or {}).get("per_app_prompts") or []):
        if not isinstance(raw, dict):
            continue
        apps = [str(a).strip() for a in raw.get("apps", [])
                if str(a).strip()]
        instructions = str(raw.get("instructions", "")).strip()
        if apps and instructions:
            made.append({"match": apps, "instructions": instructions})
    tapps = list(cfg.get("general", {}).get("terminal_apps") or [])
    if tapps and tapps != list(default_terminal_apps or []):
        made.append({"match": tapps, "terminal": True,
                     "spoken_send": "off"})
    if not made or existing:
        return []
    prof["rules"] = made
    prof["migrated_from_legacy"] = True
    return made
