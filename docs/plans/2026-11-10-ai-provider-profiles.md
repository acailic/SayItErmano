# AI Provider Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Named AI-polish provider profiles (e.g. "GLM") stored in a sidecar JSON, switchable from Settings → AI and the tray menu, hot-applied via the existing set-config path.

**Architecture:** Sidecar store mirrors `fluidvoice/ai/profiles.py` (prompt profiles) discipline; switching writes the profile's endpoint values + a new `ai.profile` pointer through `set-config` (no `[ai]` key is RESTART_REQUIRED, `AIClient` is constructed per dictation in `rewrite.py:114`, so swaps hot-apply). API key VALUES are never stored — only env-var names.

**Tech Stack:** Python 3.12 stdlib (json/os/pathlib), PyGObject/Adw 1.5, existing control-socket routes. No new dependencies.

## Global Constraints

- Tests: canonical unit tier only — `SAYIT_PY=/home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python just test` from the worktree root. Never bare `pytest`, never real network in tests.
- Store discipline (from `requests/ai-provider-profiles.md`): missing/corrupt `ai-profiles.json` → `{}` + exactly ONE warning; writes atomic (tmp + `os.replace`) and `0600`; names 1–64 chars after trim; only keys `base_url`, `model`, `api_key_env`, `temperature` are kept.
- Never store api_key values; `ai.api_key` stays `_reject` for socket sets.
- Menu/UI copy follows existing style (sentence case, no trailing periods in row titles).
- Commit + push `origin/linux` after every task; re-check `git status` + `git log origin/linux..linux` before each commit (user sweeps mid-session).

---

### Task 1: Provider-profile store + `paths.ai_profiles_file()`

**Files:**
- Modify: `fluidvoice/paths.py` (after `prompt_profiles_file()`, line ~75)
- Create: `fluidvoice/ai/provider_profiles.py`
- Test: `tests/test_ai_provider_profiles.py`

**Interfaces:**
- Produces: `load_profiles(path=None) -> dict[str, dict]`, `save_profiles(profiles, path=None)`, `save_named(name, profile: dict, path=None) -> dict`, `rename_profile(old, new, path=None) -> dict`, `delete_profile(name, path=None) -> dict`, `switch_patch(name, path=None) -> dict` (returns the `{"ai": {...}}` set-config body), `TEMPLATES: dict[str, dict]`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_ai_provider_profiles.py`:

```python
"""AI provider-profile store: round-trip, corruption fallback, name rules,
key allow-list, template shapes, and the set-config switch patch."""
from __future__ import annotations

import json
import os

import pytest

from fluidvoice.ai import provider_profiles as pp


def _glm():
    return {"base_url": "https://api.z.ai/api/paas/v4", "model": "glm-4.7",
            "api_key_env": "ZAI_API_KEY"}


class TestStore:
    def test_roundtrip(self, tmp_path):
        p = tmp_path / "ai-profiles.json"
        pp.save_named("GLM", _glm(), path=p)
        assert pp.load_profiles(path=p) == {"GLM": _glm()}

    def test_missing_file_is_empty(self, tmp_path):
        assert pp.load_profiles(path=tmp_path / "nope.json") == {}

    def test_corrupt_degrades_to_empty(self, tmp_path):
        p = tmp_path / "ai-profiles.json"
        p.write_text("{not json", encoding="utf-8")
        assert pp.load_profiles(path=p) == {}

    def test_wrong_shape_degrades_to_empty(self, tmp_path):
        p = tmp_path / "ai-profiles.json"
        p.write_text('["GLM"]', encoding="utf-8")
        assert pp.load_profiles(path=p) == {}

    def test_unknown_keys_and_secrets_dropped(self, tmp_path):
        p = tmp_path / "ai-profiles.json"
        p.write_text(json.dumps(
            {"x": {"base_url": "u", "model": "m", "api_key_env": "K",
                   "api_key": "SECRET", "junk": 1}}), encoding="utf-8")
        assert pp.load_profiles(path=p) == \
            {"x": {"base_url": "u", "model": "m", "api_key_env": "K"}}

    def test_atomic_write_0600(self, tmp_path):
        p = tmp_path / "ai-profiles.json"
        pp.save_profiles({"GLM": _glm()}, path=p)
        assert not list(tmp_path.glob("*.tmp"))
        assert (os.stat(p).st_mode & 0o777) == 0o600

    def test_name_rules(self, tmp_path):
        p = tmp_path / "ai-profiles.json"
        with pytest.raises(ValueError):
            pp.save_named("   ", _glm(), path=p)
        with pytest.raises(ValueError):
            pp.save_named("x" * 65, _glm(), path=p)

    def test_rename_and_delete(self, tmp_path):
        p = tmp_path / "ai-profiles.json"
        pp.save_named("GLM", _glm(), path=p)
        assert "GLM 2" in pp.rename_profile("GLM", "GLM 2", path=p)
        assert pp.delete_profile("GLM 2", path=p) == {}
        with pytest.raises(ValueError):
            pp.delete_profile("GLM 2", path=p)


class TestSwitchPatch:
    def test_patch_has_pointer_and_values(self, tmp_path):
        p = tmp_path / "ai-profiles.json"
        pp.save_named("GLM", _glm(), path=p)
        assert pp.switch_patch("GLM", path=p) == {"ai": {**_glm(), "profile": "GLM"}}

    def test_temperature_only_when_present(self, tmp_path):
        p = tmp_path / "ai-profiles.json"
        pp.save_named("T", {"base_url": "u", "model": "m",
                            "api_key_env": "", "temperature": 0.4}, path=p)
        assert pp.switch_patch("T", path=p)["ai"]["temperature"] == 0.4

    def test_missing_profile_raises(self, tmp_path):
        with pytest.raises(KeyError):
            pp.switch_patch("nope", path=tmp_path / "ai-profiles.json")


class TestTemplates:
    def test_templates_are_valid_shapes(self):
        for name, prof in pp.TEMPLATES.items():
            assert set(prof) <= {"base_url", "model", "api_key_env",
                                 "temperature"}
            assert prof["base_url"].startswith("http")

    def test_glm_template(self):
        assert pp.TEMPLATES["GLM"] == _glm()
```

- [ ] **Step 2: Run, verify failure** — `SAYIT_PY=/home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python just test tests/test_ai_provider_profiles.py` — expected: collection error / ImportError (`provider_profiles` does not exist).
- [ ] **Step 3: Implement** — `fluidvoice/paths.py`, add after `prompt_profiles_file()`:

```python
def ai_profiles_file() -> Path:
    """Named AI-polish provider profiles ({name: {base_url, model, ...}})."""
    return config_dir() / "ai-profiles.json"
```

Create `fluidvoice/ai/provider_profiles.py` (mirrors `ai/profiles.py` discipline):

```python
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
```

- [ ] **Step 4: Run, verify pass** — same command; expected: all green.
- [ ] **Step 5: Commit** — `git add fluidvoice/paths.py fluidvoice/ai/provider_profiles.py tests/test_ai_provider_profiles.py && git commit -m "feat(ai): provider-profile store (ai-profiles.json) + templates" && git push origin linux`

---

### Task 2: `ai.profile` config key

**Files:**
- Modify: `fluidvoice/config.py` (after the `ai.refusal_guard` `_spec` block, ~line 875)
- Test: append to `tests/test_ai_provider_profiles.py`

**Interfaces:**
- Produces: `DEFAULTS["ai"]["profile"] == ""`; `apply_settings` accepts `{"ai": {"profile": "<name>"}}`; NOT in `RESTART_REQUIRED`.

- [ ] **Step 1: Failing test** — append:

```python
class TestConfigKey:
    def test_registered_with_empty_default(self):
        import copy

        from fluidvoice import config
        assert config.DEFAULTS["ai"]["profile"] == ""
        assert "ai.profile" not in config.RESTART_REQUIRED

    def test_apply_settings_accepts_pointer(self):
        import copy

        from fluidvoice import config
        cfg = copy.deepcopy(config.DEFAULTS)
        changed, rejected = config.apply_settings(
            cfg, {"ai": {"profile": "GLM"}})
        assert changed == ["ai.profile"]
        assert not rejected
        assert cfg["ai"]["profile"] == "GLM"
```

- [ ] **Step 2: Run, verify failure** (KeyError: 'profile').
- [ ] **Step 3: Implement** — in `config.py` after the `refusal_guard` spec:

```python
_spec("ai", "profile", "", _str(64),
      description="Active AI provider profile name (\"\" = custom endpoint\n"
      "settings). Switching via Settings -> AI or the tray's AI profile\n"
      "submenu writes the profile's endpoint values into [ai] and points\n"
      "here; editing the endpoint by hand clears it back to \"\".")
```

- [ ] **Step 4: Run, verify pass** — full new test file + `tests/test_config*.py` (registry-size/count fixtures may assert key totals; fix any that enumerate).
- [ ] **Step 5: Commit** — `feat(config): ai.profile active-provider pointer key`

---

### Task 3: Tray "AI profile" submenu

**Files:**
- Modify: `fluidvoice/daemon.py` `_build_tray_menu` (menu assembled ~lines 270–310)
- Test: append to `tests/test_tray.py` (new class `TestAIProfileMenu`)

**Interfaces:**
- Consumes: `provider_profiles.load_profiles/switch_patch` (Task 1), `Daemon._set_config(body)` (`daemon.py:996`, takes the config dict directly).
- Produces: menu item `{"kind": "item", "label": "AI profile", "children": [...]}` inserted right after the "Microphone" item, only when the store is non-empty; children are `{"kind": "check", "checked": name == cfg["ai"]["profile"], "action": apply}`.

- [ ] **Step 1: Failing tests** — append to `tests/test_tray.py` (harness mirrors `test_menu_orders_mics_by_priority`: real `Daemon` + `StubRecorder`; store is a tmp file via monkeypatched `paths.ai_profiles_file`):

```python
class TestAIProfileMenu:
    def _daemon(self, cfg=None):
        import copy

        import fluidvoice.daemon as dm
        from fluidvoice.config import DEFAULTS
        from tests.test_daemon import StubRecorder
        return dm.Daemon(cfg or copy.deepcopy(DEFAULTS),
                         recorder=StubRecorder(), backend_factory=lambda c: None,
                         use_hotkey=False, use_sounds=False)

    def test_hidden_when_store_empty(self, monkeypatch, tmp_path):
        from fluidvoice import paths
        monkeypatch.setattr(paths, "ai_profiles_file",
                            lambda: tmp_path / "ai-profiles.json")
        d = self._daemon()
        labels = [i.get("label") for i in d._build_tray_menu()]
        assert "AI profile" not in labels

    def test_lists_profiles_with_active_checkmark(self, monkeypatch, tmp_path):
        from fluidvoice import paths
        from fluidvoice.ai import provider_profiles as pp
        p = tmp_path / "ai-profiles.json"
        pp.save_named("GLM", {"base_url": "https://api.z.ai/api/paas/v4",
                              "model": "glm-4.7", "api_key_env": "K"}, path=p)
        pp.save_named("Local", {"base_url": "http://x", "model": "m",
                                "api_key_env": ""}, path=p)
        monkeypatch.setattr(paths, "ai_profiles_file", lambda: p)
        cfg = self._daemon().cfg
        cfg["ai"]["profile"] = "Local"
        d = self._daemon(cfg)
        sub = next(i for i in d._build_tray_menu()
                   if i.get("label") == "AI profile")
        assert [c["label"] for c in sub["children"]] == ["GLM", "Local"]
        checked = [c for c in sub["children"] if c.get("checked")]
        assert [c["label"] for c in checked] == ["Local"]
        mics = next(i for i in d._build_tray_menu()
                    if i.get("label") == "Microphone")
        assert d._build_tray_menu().index(sub) == \
            d._build_tray_menu().index(mics) + 1  # directly after Microphone

    def test_switch_action_applies_set_config(self, monkeypatch, tmp_path):
        import fluidvoice.config as config_mod
        from fluidvoice import paths
        from fluidvoice.ai import provider_profiles as pp
        p = tmp_path / "ai-profiles.json"
        pp.save_named("GLM", {"base_url": "https://api.z.ai/api/paas/v4",
                              "model": "glm-4.7", "api_key_env": "K"},
                      path=p)
        monkeypatch.setattr(paths, "ai_profiles_file", lambda: p)
        monkeypatch.setattr(config_mod, "save_config",
                            lambda c, path=None: None)
        d = self._daemon()
        sub = next(i for i in d._build_tray_menu()
                   if i.get("label") == "AI profile")
        glm = next(c for c in sub["children"] if c["label"] == "GLM")
        glm["action"]()
        assert d.cfg["ai"]["profile"] == "GLM"
        assert d.cfg["ai"]["base_url"] == "https://api.z.ai/api/paas/v4"
        assert d.cfg["ai"]["model"] == "glm-4.7"
```

Note: `load_profiles()` in the daemon must resolve via `paths.ai_profiles_file()` at call time (the module-level default `_resolve` does this — verify the monkeypatch above actually reaches it; if `provider_profiles` binds the path at import, fix `_resolve` accordingly).

- [ ] **Step 2: Run, verify failure** (`KeyError: 'AI profile'` from `next()`).
- [ ] **Step 3: Implement** — in `daemon.py::_build_tray_menu`, after the `mics` loop is built and before the `return [...]` literal, build the submenu and capture the list:

```python
        ai_profile_items: list[dict] = []
        try:
            from .ai.provider_profiles import load_profiles, switch_patch
            active_profile = self.cfg["ai"].get("profile", "")
            for name in sorted(load_profiles()):
                def _apply_profile(n=name):
                    try:
                        self._set_config(switch_patch(n))
                    except Exception as e:  # store changed since menu build
                        logging.getLogger(__name__).warning(
                            "AI profile switch failed: %s", e)
                ai_profile_items.append(
                    {"kind": "check", "label": name,
                     "checked": name == active_profile,
                     "action": _apply_profile})
        except Exception:
            ai_profile_items = []
```

Change the final `return [` … `]` to `menu = [` … `]`, then:

```python
        if ai_profile_items:
            mic_idx = next(i for i, it in enumerate(menu)
                           if it.get("label") == "Microphone")
            menu.insert(mic_idx + 1,
                        {"kind": "item", "label": "AI profile",
                         "children": ai_profile_items})
        return menu
```

(`logging` is already imported in `daemon.py`; if not, add the import.)

- [ ] **Step 4: Run, verify pass** — `... just test tests/test_tray.py`.
- [ ] **Step 5: Commit** — `feat(tray): AI profile submenu switching provider profiles live`

---

### Task 4: Settings → AI "Provider profiles" group

**Files:**
- Modify: `fluidvoice/gtkui/settings_pages/ai.py` (`_build_ai`, new helpers `_provider_profiles_refresh`, `_provider_apply`, `_provider_add_dialog`, `_provider_add_template`, actions `provider.rename` / `provider.delete`)
- Modify: `fluidvoice/gtkui/settings_window.py` only if the save collector needs the pending-pointer-clear hook (see Step 3b)
- Test: `tests/gtkui/test_ai_provider_profiles.py` (new; follows the mirrored `tests/gtkui/` conventions — Xvfb-offscreen safe, no network)

**Interfaces:**
- Consumes: `provider_profiles.*` (Task 1), `ai.profile` key (Task 2), `self._client.set_config(body)` (`gtkui/client.py:107`), the existing radio-row pattern from the Prompt profiles group (`settings_pages/ai.py:58-96`), `self.save()` collector (`settings_window.py:241`).
- Produces: first group on the AI page titled "Provider profiles"; radio select = instant `set_config(switch_patch(name))`; "Custom" row (checked when pointer is `""`) clears the pointer only; hand-edited base_url/model queue `ai.profile=""` into the next save.

- [ ] **Step 1: Failing tests** — create `tests/gtkui/test_ai_provider_profiles.py` exercising the headless logic (row-model building + apply semantics through a stub client), not pixel rendering:

```python
"""Provider-profile settings group: row model (radios, Custom, active
pointer), apply-through-socket semantics, pointer clear on hand edits."""
from __future__ import annotations

import pytest

from fluidvoice.ai import provider_profiles as pp


class FakeClient:
    def __init__(self):
        self.calls: list[dict] = []

    def set_config(self, body):
        self.calls.append(body)
        return {"ok": True, "changed": ["ai.profile"], "rejected": [],
                "restart_required": [], "applied": [], "errors": [],
                "note": ""}


def test_switch_patch_is_what_apply_sends(tmp_path):
    p = tmp_path / "ai-profiles.json"
    pp.save_named("GLM", {"base_url": "https://api.z.ai/api/paas/v4",
                          "model": "glm-4.7", "api_key_env": "K"}, path=p)
    client = FakeClient()
    body = pp.switch_patch("GLM", path=p)
    client.set_config(body)
    assert client.calls == [{"ai": {"base_url": "https://api.z.ai/api/paas/v4",
                                    "model": "glm-4.7", "api_key_env": "K",
                                    "profile": "GLM"}}]


def test_custom_patch_only_clears_pointer():
    client = FakeClient()
    client.set_config({"ai": {"profile": ""}})
    assert client.calls == [{"ai": {"profile": ""}}]
```

Plus one widget-level test in the established `tests/gtkui/` style (mirror the closest existing page test — see `ls tests/gtkui/`): construct the AI page with a stub client and a tmp store, assert the group exists, radio rows match store names + "Custom", and clicking a profile row calls `set_config` with the switch patch (monkeypatch `paths.ai_profiles_file`). If the existing page tests require a display, they already run under the repo's Xvfb default — no special handling.

- [ ] **Step 2: Run, verify failure.**
- [ ] **Step 3: Implement** — in `_build_ai`, insert BEFORE the "AI polish" `grp` construction (group becomes the first on the page):

```python
        # provider profiles: switch the whole endpoint (base_url/model/
        # key-env) with one click; values land in [ai] via set-config
        prov_grp = Adw.PreferencesGroup(
            title="Provider profiles",
            description="Named endpoint presets — selecting one switches "
            "the AI polish provider instantly",
        )
        self._prov_rows: dict[str, Adw.ActionRow] = {}
        self._prov_checks: dict[str, Gtk.CheckButton] = {}
        self._prov_radio_root = None  # first CheckButton joins this group
        self._prov_group = prov_grp
        page.add(prov_grp)
```

Row builder (mirrors the prompt-profile rows):

```python
    def _provider_profiles_refresh(self) -> None:
        """Rebuild provider radio rows: Custom (pointer "") + store names."""
        from ..ai import provider_profiles as pp  # noqa: PLC2701 (mixin)
        for row in self._prov_rows.values():
            self._prov_group.remove(row)
        self._prov_rows.clear()
        self._prov_checks.clear()
        self._prov_radio_root = None
        active = self._cfg["ai"].get("profile", "")
        names = ["", *sorted(pp.load_profiles())]  # "" renders as Custom
        for name in names:
            label = "Custom (hand-set endpoint)" if not name else name
            check = Gtk.CheckButton()
            if self._prov_radio_root is None:
                self._prov_radio_root = check
            else:
                check.set_group(self._prov_radio_root)
            check.set_active(name == active)
            row = Adw.ActionRow(title=label, activatable=True)
            row.add_prefix(check)
            row.connect("activated", lambda *_,
                        n=name: self._provider_apply(n))
            if name:
                row.add_suffix(self._prov_menu_btn(name))
            self._prov_group.add(row)
            self._prov_rows[name] = row
            self._prov_checks[name] = check
```

Apply + clear-on-edit + dialogs:

```python
    def _provider_apply(self, name: str) -> None:
        """Instant switch: name == "" just clears the pointer (Custom)."""
        from ..ai import provider_profiles as pp
        try:
            body = ({"ai": {"profile": ""}} if not name
                    else pp.switch_patch(name))
            resp = self._client.set_config(body)
            if not resp.get("ok"):
                raise RuntimeError(str(resp.get("rejected")))
            self._cfg["ai"].update(body["ai"])
        except Exception as e:
            print(f"provider switch failed: {e}")  # matches page error style
        self._provider_profiles_refresh()

    def _provider_queue_pointer_clear(self) -> None:
        """Hand-edits to base_url/model make the active profile stale."""
        if self._cfg["ai"].get("profile"):
            self._pending_provider_clear = True
```

Wire `url_row.connect("changed", ...)` and the model entry to `_provider_queue_pointer_clear` (in addition to the existing `_touch` connections). In the save path (wherever `self.save()` assembles the body from touched rows — `settings_window.py:241` routes here): if `getattr(self, "_pending_provider_clear", False)`, add `ai.profile=""` to the outgoing body and reset the flag after a successful save.

Add-row + template dialog (Adw 1.5 `Adw.AlertDialog`):

```python
    def _provider_add_dialog(self) -> None:
        # four entries: name, base_url, model, api_key_env; on confirm:
        # pp.save_named(name, {...}) then _provider_profiles_refresh()
        ...
    def _provider_add_template(self) -> None:
        # dialog listing pp.TEMPLATES; confirm copies TEMPLATES[name] into
        # the store via save_named (suffix " 2" on collision), then refresh
        ...
```

Fill both dialogs following `_profile_add()`'s existing dialog pattern in this file (same Adw widgets, same threading rules). Register actions at page scope exactly like the prompt ones:

```python
        self.install_action("provider.rename", "s", self._provider_rename_action)
        self.install_action("provider.delete", "s", self._provider_delete_action)
```

`_provider_delete_action` deletes from the store; when the deleted name was active it also `self._client.set_config({"ai": {"profile": ""}})`. `_provider_rename_action` renames in the store; when active, `set_config({"ai": {"profile": new}})`.

Also call `self._provider_profiles_refresh()` once at the end of `_build_ai` (after `self._cfg` is loaded) and after get-config refresh if the window re-pulls config.

- [ ] **Step 4: Run** — `SAYIT_PY=... just test` (unit tier covers `tests/gtkui/`) and `just test-ui` for the GUI lane; both green.
- [ ] **Step 5: Commit** — `feat(gtkui): Provider profiles group on the AI settings page`

---

### Task 5: GLM provider logo

**Files:**
- Modify: `fluidvoice/gtkui/logos.py` (`PROVIDERS` list, first entry)
- Create: `fluidvoice/assets/providers/glm.png`, `glm-light.png`
- Create: `tools/gen-glm-logo.py` (one-off generator; commit it next to `gen-app-icon.py`)
- Test: extend `tests/test_icons.py`

**Interfaces:**
- Consumes: existing `provider_for`/`logo_path` contract.
- Produces: `provider_for("https://api.z.ai/api/paas/v4") == "glm"`; both PNGs 128×128 RGBA.

- [ ] **Step 1: Failing test** — in `tests/test_icons.py` add:

```python
def test_glm_provider_detected():
    from fluidvoice.gtkui.logos import provider_for
    assert provider_for("https://api.z.ai/api/paas/v4") == "glm"
    assert provider_for("https://open.bigmodel.cn/api/paas/v4") == "glm"


def test_glm_logo_assets_exist():
    from fluidvoice.gtkui.logos import logo_path
    assert logo_path("glm") and logo_path("glm", dark=True)
```

- [ ] **Step 2: Run, verify failure.**
- [ ] **Step 3: Implement** — `logos.py` `PROVIDERS` first entry: `("glm", ("z.ai", "bigmodel")),`. Generate assets (standalone process — the daemon never runs this; avoids the Pillow/Pango first-touch trap):

```python
# tools/gen-glm-logo.py — regenerate: .venv/bin/python tools/gen-glm-logo.py
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path("fluidvoice/assets/providers")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

for dark, suffix in ((False, ""), (True, "-light")):
    img = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (255, 255, 255, 255) if dark else (15, 15, 15, 255)
    d.rounded_rectangle([6, 6, 122, 122], radius=30, outline=color, width=7)
    d.text((64, 64), "GLM", font=ImageFont.truetype(FONT, 46),
           fill=color, anchor="mm")
    img.save(OUT / f"glm{suffix}.png")
```

Run it, `git add` both PNGs + the script.

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat(assets): GLM provider logo + detection`

---

### Task 6: Docs

**Files:**
- Modify: `docs/guides/configuration.md` (AI section)

- [ ] **Step 1:** Add a "Provider profiles" subsection: what's stored where (`~/.config/sayit-ermano/ai-profiles.json`), switching from Settings/tray, env-only key rule (`api_key_env` per profile; never the key itself), GLM example (base_url `https://api.z.ai/api/paas/v4`, model `glm-4.7`, export `ZAI_API_KEY`), pointer semantics (hand-edit clears to Custom).
- [ ] **Step 2:** Commit — `docs: provider profiles in the configuration guide`.

---

### Task 7: Full suite + live verify + spec status

- [ ] **Step 1:** `SAYIT_PY=/home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python just test` — full unit tier green.
- [ ] **Step 2:** Live smoke (two-step daemon ritual from AGENTS.md): stop `sayit-ermano`, run the repo daemon, add the GLM profile via the socket (`set-config` with the template values + `ZAI_API_KEY` exported by the user), confirm the tray "AI profile" submenu shows GLM with a checkmark, Settings → AI shows radios; then `systemctl --user start sayit-ermano` to restore production. Only "Test connection" touches the network — run it once against `api.z.ai` to answer the open spec item (Coding Plan token vs paas/v4).
- [ ] **Step 3:** Flip `requests/ai-provider-profiles.md` STATUS to SHIPPED with the commit hash; commit + push.
