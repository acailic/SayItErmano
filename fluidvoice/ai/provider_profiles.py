"""Named AI-polish provider profiles (sidecar JSON, not config).

Shape: {"<name>": {"base_url": str, "model": str, "api_key_env": str,
"temperature"?: float}} in ai-profiles.json beside the config. Switching a
profile writes its values into [ai] via set-config - config.toml stays the
single source of truth and ai.profile is the pointer ("" = custom).
api_key VALUES are never stored, only env-var names (the env/file-only
rule). TEMPLATES are built-in presets copied into the store on "Add from
template"; they are never active themselves. Store discipline mirrors
ai/profiles.py: missing/corrupt file degrades to {} with one warning,
writes are atomic and 0600.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .. import paths

log = logging.getLogger(__name__)

MAX_NAME_LEN = 64
_WARN = "ai-profiles.json unreadable - starting empty"
_ALLOWED_KEYS = ("base_url", "model", "api_key_env", "temperature")

#: built-in presets offered via Settings -> AI -> "Add from template";
#: editable strings, not commitments (copy-on-add)
TEMPLATES: dict[str, dict] = {
    "GLM": {"base_url": "https://api.z.ai/api/paas/v4", "model": "glm-4.7",
            "api_key_env": "ZAI_API_KEY"},
    "OpenAI": {"base_url": "https://api.openai.com/v1", "model": "gpt-5",
               "api_key_env": "OPENAI_API_KEY"},
    "Groq": {"base_url": "https://api.groq.com/openai/v1",
             "model": "llama-3.3-70b-versatile", "api_key_env": "GROQ_API_KEY"},
    "OpenRouter": {"base_url": "https://openrouter.ai/api/v1",
                   "model": "openrouter/auto",
                   "api_key_env": "OPENROUTER_API_KEY"},
    "Ollama": {"base_url": "http://localhost:11434/v1", "model": "",
               "api_key_env": ""},
    "LM Studio": {"base_url": "http://localhost:1234/v1", "model": "",
                  "api_key_env": ""},
}


def _resolve(path) -> Path:
    return Path(path) if path is not None else paths.ai_profiles_file()


def _clean_name(name: str) -> str:
    name = str(name).strip()
    if not name or len(name) > MAX_NAME_LEN:
        raise ValueError(
            f"profile name must be 1-{MAX_NAME_LEN} characters (got "
            f"{len(name)} after trim)")
    return name


def _clean_profile(profile: dict) -> dict:
    return {k: profile[k] for k in _ALLOWED_KEYS if k in profile}


def load_profiles(path=None) -> dict[str, dict]:
    """All profiles; a missing file is {}. Unreadable/malformed content
    degrades to {} with exactly one warning, never raises."""
    p = _resolve(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning(_WARN)
        return {}
    if not isinstance(data, dict):
        log.warning(_WARN)
        return {}
    out: dict[str, dict] = {}
    for k, v in data.items():
        if isinstance(v, dict) and str(k).strip():
            out[str(k)] = _clean_profile(v)
    return out


def save_profiles(profiles: dict[str, dict], path=None) -> None:
    """Atomic write (tmp + os.replace) with 0600 - config.py discipline."""
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({k: _clean_profile(v) for k, v in profiles.items()},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def save_named(name: str, profile: dict, path=None) -> dict:
    """Upsert one profile; returns the full store after the write."""
    name = _clean_name(name)
    profiles = load_profiles(path)
    profiles[name] = _clean_profile(profile)
    save_profiles(profiles, path)
    return profiles


def rename_profile(old: str, new: str, path=None) -> dict:
    """Rename in place (order preserved); ValueError if `old` is missing."""
    old, new = _clean_name(old), _clean_name(new)
    profiles = load_profiles(path)
    if old not in profiles:
        raise ValueError(f"no AI provider profile named {old!r}")
    out = {new if k == old else k: v for k, v in profiles.items()}
    save_profiles(out, path)
    return out


def delete_profile(name: str, path=None) -> dict:
    """Delete one profile; ValueError if missing."""
    name = _clean_name(name)
    profiles = load_profiles(path)
    if name not in profiles:
        raise ValueError(f"no AI provider profile named {name!r}")
    del profiles[name]
    save_profiles(profiles, path)
    return profiles


def switch_patch(name: str, path=None) -> dict:
    """set-config body activating `name`: the ai.profile pointer plus the
    profile's endpoint values (temperature only when it carries one).
    KeyError when the profile does not exist."""
    profile = load_profiles(path).get(name)
    if profile is None:
        raise KeyError(f"no AI provider profile named {name!r}")
    return {"ai": {**profile, "profile": name}}
