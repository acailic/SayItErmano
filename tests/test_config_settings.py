"""Config persistence + the settings validation layer (apply_settings).

Migrated from the retired web UI's test suite (test_webui.py): the
save/permission tests were always config-level; the validator tests keep
guarding the same semantics through the one source of truth the daemon's
set-config socket action uses.
"""
from __future__ import annotations

import copy
import os

import pytest

from fluidvoice.config import (
    DEFAULTS,
    RESTART_REQUIRED,
    TEMPLATE,
    apply_settings,
    coerce_setting,
    load_config,
    save_config,
)


class TestSaveConfig:
    def test_roundtrip(self, tmp_path, monkeypatch):
        from fluidvoice import paths as p
        monkeypatch.setattr(p, "config_file", lambda: tmp_path / "c.toml")
        cfg = copy.deepcopy(DEFAULTS)
        cfg["hotkey"]["key"] = "F9"
        cfg["ai"]["enabled"] = True
        cfg["processing"]["dictionary"] = [
            {"triggers": ["fluid voice"], "replacement": "FluidVoice"}]
        save_config(cfg)
        loaded = load_config(tmp_path / "c.toml")
        assert loaded["hotkey"]["key"] == "F9"
        assert loaded["ai"]["enabled"] is True
        assert loaded["processing"]["dictionary"][0]["triggers"] == ["fluid voice"]

    def test_api_key_carried_over(self, tmp_path, monkeypatch):
        from fluidvoice import paths as p
        target = tmp_path / "c.toml"
        target.write_text('[ai]\napi_key = "sk-secret"\nenabled = false\n')
        monkeypatch.setattr(p, "config_file", lambda: target)
        cfg = copy.deepcopy(DEFAULTS)
        cfg["ai"]["enabled"] = True
        save_config(cfg)
        text = target.read_text()
        assert 'api_key = "sk-secret"' in text  # not lost by the save
        assert "enabled = true" in text

    def test_special_characters_escaped(self, tmp_path, monkeypatch):
        from fluidvoice import paths as p
        target = tmp_path / "c.toml"
        monkeypatch.setattr(p, "config_file", lambda: target)
        cfg = copy.deepcopy(DEFAULTS)
        cfg["processing"]["punctuation_prefix"] = 'we"ird\\prefix'
        save_config(cfg)
        assert load_config(target)["processing"]["punctuation_prefix"] == 'we"ird\\prefix'

    def test_retired_server_section_stripped(self, tmp_path):
        target = tmp_path / "old.toml"
        target.write_text('[server]\nport = 47735\nenabled = true\n'
                          '[sounds]\nenabled = false\n')
        cfg = load_config(target)
        assert "server" not in cfg  # old configs carry it; loader drops it
        assert cfg["sounds"]["enabled"] is False  # other sections unaffected


class TestConfigPermissions:
    def test_saved_config_is_0600(self, tmp_path, monkeypatch):
        from fluidvoice import paths as p
        target = tmp_path / "c.toml"
        monkeypatch.setattr(p, "config_file", lambda: target)
        save_config(copy.deepcopy(DEFAULTS))
        assert os.stat(target).st_mode & 0o777 == 0o600

    def test_write_template_is_0600(self, tmp_path, monkeypatch):
        from fluidvoice import paths as p
        from fluidvoice.config import write_template
        target = tmp_path / "c.toml"
        monkeypatch.setattr(p, "config_file", lambda: target)
        write_template()
        assert os.stat(target).st_mode & 0o777 == 0o600


@pytest.fixture()
def cfg():
    return copy.deepcopy(DEFAULTS)


class TestApplySettings:
    def test_full_surface_roundtrip(self, cfg):
        changed, rejected = apply_settings(cfg, {
            "recording": {"preview_overlay_size": "large",
                          "preview_bottom_offset": 128,
                          "spoken_send_enabled": True,
                          "spoken_send_phrase": "send it now",
                          "spoken_send_key": "shift+enter",
                          "pause_media": False},
            "hotkey": {"rewrite_key": "F8", "modifiers": ["ctrl"],
                       "paste_key": "F7"},
            "general": {"tray_enabled": False},
            "model": {"eager_warmup": False},
            "processing": {"gaav_enabled": True},
        })
        assert rejected == [] and len(changed) == 12
        assert cfg["recording"]["preview_overlay_size"] == "large"
        assert cfg["hotkey"]["rewrite_key"] == "F8"
        assert cfg["hotkey"]["paste_key"] == "F7"
        assert cfg["model"]["eager_warmup"] is False

    def test_garbage_rejected_nothing_half_applied(self, cfg):
        before = copy.deepcopy(cfg)
        changed, rejected = apply_settings(cfg, {
            "recording": {"max_seconds": "abc"},
            "insertion": {"type_delay_ms": "8; rm"},
            "hotkey": {"mode": "explode"},
            "sounds": {"volume": 0.5},
        })
        assert sorted(rejected) == ["hotkey.mode",
                                    "insertion.type_delay_ms",
                                    "recording.max_seconds"]
        assert changed == ["sounds.volume"]  # the valid one still applies
        assert cfg["recording"]["max_seconds"] == before["recording"]["max_seconds"]

    def test_unknown_and_retired_sections_ignored(self, cfg):
        changed, rejected = apply_settings(cfg, {
            "server": {"port": 1}, "nope": {"x": 1},
            "ai": {"api_key": "should-not-set"}})
        assert changed == [] and rejected == []

    def test_mic_device_empty_is_system_default(self, cfg):
        # the mic dropdown's "Auto" option saves "" — the documented
        # default, not a bad value (regression: it toasted as rejected
        # on every save)
        changed, rejected = apply_settings(
            cfg, {"recording": {"device": "alsa_input.pci-0000_00_1f.3"}})
        assert rejected == [] and changed == ["recording.device"]
        changed, rejected = apply_settings(cfg, {"recording": {"device": ""}})
        assert rejected == [] and changed == ["recording.device"]
        assert cfg["recording"]["device"] == ""
        _, rejected = apply_settings(cfg, {"recording": {"device": 7}})
        assert rejected == ["recording.device"]
        _, rejected = apply_settings(
            cfg, {"recording": {"device": "x" * 257}})
        assert rejected == ["recording.device"]

    def test_per_app_prompts_validated(self, cfg):
        ok_rules = [{"apps": ["zed"], "instructions": "be terse"}]
        changed, rejected = apply_settings(
            cfg, {"ai": {"per_app_prompts": ok_rules}})
        assert rejected == [] and cfg["ai"]["per_app_prompts"] == ok_rules
        for bad in ("nope", [{"apps": [], "instructions": "x"}],
                    [{"apps": ["a"], "instructions": ""}]):
            _, rejected = apply_settings(
                cfg, {"ai": {"per_app_prompts": bad}})
            assert rejected == ["ai.per_app_prompts"], bad
        # over-long instructions are truncated (webui-endpoint semantics)
        _, rejected = apply_settings(
            cfg, {"ai": {"per_app_prompts":
                         [{"apps": ["a"], "instructions": "x" * 3000}]}})
        assert rejected == []
        assert len(cfg["ai"]["per_app_prompts"][0]["instructions"]) == 2000

    def test_model_name_aliasing(self, cfg):
        changed, rejected = apply_settings(cfg, {"model": {"name": "turbo"}})
        assert rejected == [] and cfg["model"]["name"] == "large-v3-turbo"
        _, rejected = apply_settings(cfg, {"model": {"name": "gpt-4o"}})
        assert rejected == ["model.name"]

    def test_backend_enum_accepts_parakeet_not_alias(self, cfg):
        changed, rejected = apply_settings(
            cfg, {"model": {"backend": "parakeet"}})
        assert rejected == [] and cfg["model"]["backend"] == "parakeet"
        _, rejected = apply_settings(
            cfg, {"model": {"backend": "parakeet-onnx"}})
        assert rejected == ["model.backend"]

    def test_model_name_accepts_parakeet_catalog(self, cfg):
        from fluidvoice import model_catalog
        for name in model_catalog.PARAKEET_CATALOG:
            changed, rejected = apply_settings(
                cfg, {"model": {"name": name}})
            assert rejected == [] and cfg["model"]["name"] == name
        _, rejected = apply_settings(
            cfg, {"model": {"name": "gpt-4o-audio"}})
        assert rejected == ["model.name"]

    def test_model_name_change_is_an_engine_key(self):
        # the parakeet Use-flow sends backend+name; once the backend is
        # parakeet only `name` changes and must still reload the engine
        from fluidvoice.config import ENGINE_KEYS
        assert "model.name" in ENGINE_KEYS

    def test_restart_required_is_only_model_warmup_now(self):
        # the [server] section retired with the web UI
        assert RESTART_REQUIRED == {"model.eager_warmup"}


class TestMicPriority:
    """recording.mic_priority: ordered patterns for auto mic switching."""

    def test_defaults_and_template_documented(self):
        assert DEFAULTS["recording"]["mic_priority"] == []
        assert "mic_priority" in TEMPLATE  # cheap doc-guard

    def test_clean_strips_empties_and_keeps_order(self):
        ok, cleaned = coerce_setting(
            "recording", "mic_priority", [" bluez ", "", "USB-Cam"])
        assert ok is True and cleaned == ["bluez", "USB-Cam"]

    def test_dedupes_case_insensitively_first_wins(self):
        ok, cleaned = coerce_setting(
            "recording", "mic_priority", ["BlueZ", "bluez", " BLUEZ "])
        assert ok is True and cleaned == ["BlueZ"]

    @pytest.mark.parametrize("bad", [
        "bluez",            # not a list
        [42],                # non-str entry
        ["x" * 65],          # single entry over 64 chars
        [f"p{i}" for i in range(21)],  # 21 entries (max 20)
    ])
    def test_rejects_bad_values(self, bad):
        ok, out = coerce_setting("recording", "mic_priority", bad)
        assert ok is False and out == bad

    def test_twenty_entries_accepted(self):
        ok, cleaned = coerce_setting(
            "recording", "mic_priority", [f"p{i}" for i in range(20)])
        assert ok is True and len(cleaned) == 20

    def test_empty_list_is_valid(self):
        ok, cleaned = coerce_setting("recording", "mic_priority", [])
        assert ok is True and cleaned == []

    def test_apply_settings_applies_and_reports(self, cfg):
        changed, rejected = apply_settings(
            cfg, {"recording": {"mic_priority": ["bluez"]}})
        assert rejected == [] and "recording.mic_priority" in changed
        assert cfg["recording"]["mic_priority"] == ["bluez"]

    def test_save_whitelist_roundtrip(self, tmp_path, monkeypatch):
        from fluidvoice import paths as p
        monkeypatch.setattr(p, "config_file", lambda: tmp_path / "c.toml")
        cfg = copy.deepcopy(DEFAULTS)
        cfg["recording"]["mic_priority"] = ["bluez", "usb-cam"]
        save_config(cfg)
        loaded = load_config(tmp_path / "c.toml")
        assert loaded["recording"]["mic_priority"] == ["bluez", "usb-cam"]


class TestDestructivePatterns:
    """command.destructive_patterns: user additions to the built-in list."""

    def test_defaults_and_template_documented(self):
        assert DEFAULTS["command"]["destructive_patterns"] == []
        assert "destructive_patterns" in TEMPLATE  # cheap doc-guard

    def test_clean_strips_empties_and_keeps_order(self):
        ok, cleaned = coerce_setting(
            "command", "destructive_patterns", [" git push ", "", "shutdown"])
        assert ok is True and cleaned == ["git push", "shutdown"]

    def test_dedupes_case_insensitively_first_wins(self):
        ok, cleaned = coerce_setting(
            "command", "destructive_patterns", ["Git Push", "git push"])
        assert ok is True and cleaned == ["Git Push"]

    @pytest.mark.parametrize("bad", [
        "git push",                     # not a list
        [42],                            # non-str entry
        ["x" * 129],                    # single entry over 128 chars
        [f"p{i}" for i in range(33)],   # 33 entries (max 32)
    ])
    def test_rejects_bad_values(self, bad):
        ok, out = coerce_setting("command", "destructive_patterns", bad)
        assert ok is False and out == bad

    def test_empty_list_is_valid(self):
        ok, cleaned = coerce_setting("command", "destructive_patterns", [])
        assert ok is True and cleaned == []

    def test_apply_settings_applies_and_reports(self, cfg):
        changed, rejected = apply_settings(
            cfg, {"command": {"destructive_patterns": ["git push"]}})
        assert rejected == [] and "command.destructive_patterns" in changed
        assert cfg["command"]["destructive_patterns"] == ["git push"]

    def test_apply_settings_rejects_garbage(self, cfg):
        changed, rejected = apply_settings(
            cfg, {"command": {"destructive_patterns": "git push"}})
        assert rejected == ["command.destructive_patterns"]
        assert cfg["command"]["destructive_patterns"] == []

    def test_save_whitelist_roundtrip(self, tmp_path, monkeypatch):
        from fluidvoice import paths as p
        monkeypatch.setattr(p, "config_file", lambda: tmp_path / "c.toml")
        cfg = copy.deepcopy(DEFAULTS)
        cfg["command"]["destructive_patterns"] = ["git push", "shutdown"]
        save_config(cfg)
        loaded = load_config(tmp_path / "c.toml")
        assert loaded["command"]["destructive_patterns"] == \
            ["git push", "shutdown"]


class TestNewFormattingKeys:
    """Chat/terminal formatting keys: terminal_apps + the two booleans."""

    def test_defaults(self, cfg):
        assert cfg["processing"]["slash_mention_squeeze"] is True
        assert cfg["insertion"]["terminal_autocomplete_space"] is True
        apps = cfg["general"]["terminal_apps"]
        assert apps  # non-empty
        assert "kitty" in apps and "warp" in apps  # warp: upstream blocks it

    def test_defaults_documented_in_template(self):
        for key in ("terminal_apps", "slash_mention_squeeze",
                    "terminal_autocomplete_space"):
            assert key in TEMPLATE

    def test_booleans_accepted_and_rejected(self, cfg):
        for section, key in (("processing", "slash_mention_squeeze"),
                             ("insertion", "terminal_autocomplete_space")):
            changed, rejected = apply_settings(
                cfg, {section: {key: False}})
            assert rejected == [] and cfg[section][key] is False
            _, rejected = apply_settings(
                cfg, {section: {key: "true"}})  # non-bool rejects
            assert rejected == [f"{section}.{key}"]

    def test_terminal_apps_cleaned(self):
        ok, cleaned = coerce_setting(
            "general", "terminal_apps",
            [" Kitty ", "", "GHOSTTY", "kitty"])
        assert ok is True and cleaned == ["Kitty", "GHOSTTY"]

    @pytest.mark.parametrize("bad", [
        "kitty",                       # not a list
        [42],                          # non-str entry
        ["x" * 65],                    # single entry over 64 chars
        [f"t{i}" for i in range(33)],  # 33 entries (max 32)
    ])
    def test_terminal_apps_rejects_bad_values(self, bad):
        ok, out = coerce_setting("general", "terminal_apps", bad)
        assert ok is False and out == bad

    def test_terminal_apps_empty_list_is_valid(self):
        ok, cleaned = coerce_setting("general", "terminal_apps", [])
        assert ok is True and cleaned == []

    def test_apply_settings_applies_and_reports(self, cfg):
        changed, rejected = apply_settings(
            cfg, {"general": {"terminal_apps": ["contour"]}})
        assert rejected == [] and "general.terminal_apps" in changed
        assert cfg["general"]["terminal_apps"] == ["contour"]

    def test_save_whitelist_roundtrip(self, tmp_path, monkeypatch):
        from fluidvoice import paths as p
        monkeypatch.setattr(p, "config_file", lambda: tmp_path / "c.toml")
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["terminal_apps"] = ["kitty", "contour"]
        cfg["processing"]["slash_mention_squeeze"] = False
        cfg["insertion"]["terminal_autocomplete_space"] = False
        save_config(cfg)
        loaded = load_config(tmp_path / "c.toml")
        assert loaded["general"]["terminal_apps"] == ["kitty", "contour"]
        assert loaded["processing"]["slash_mention_squeeze"] is False
        assert loaded["insertion"]["terminal_autocomplete_space"] is False


class TestInsertionHardeningKeys:
    """insertion.verify_paste + insertion.terminal_paste_key."""

    def test_defaults(self, cfg):
        assert cfg["insertion"]["verify_paste"] is True
        assert cfg["insertion"]["terminal_paste_key"] == "ctrl+shift+v"

    def test_defaults_documented_in_template(self):
        for key in ("verify_paste", "terminal_paste_key"):
            assert key in TEMPLATE

    def test_verify_paste_bool_accepted_and_rejected(self, cfg):
        changed, rejected = apply_settings(
            cfg, {"insertion": {"verify_paste": False}})
        assert rejected == [] and cfg["insertion"]["verify_paste"] is False
        _, rejected = apply_settings(
            cfg, {"insertion": {"verify_paste": "false"}})
        assert rejected == ["insertion.verify_paste"]

    @pytest.mark.parametrize("key", ["ctrl+shift+v", "ctrl+v"])
    def test_terminal_paste_key_accepted(self, cfg, key):
        changed, rejected = apply_settings(
            cfg, {"insertion": {"terminal_paste_key": key}})
        assert rejected == []
        assert cfg["insertion"]["terminal_paste_key"] == key
        if key != DEFAULTS["insertion"]["terminal_paste_key"]:
            assert "insertion.terminal_paste_key" in changed

    @pytest.mark.parametrize("bad", [
        42,                   # not a str
        True,                 # not a str
        "",                   # empty
        "x" * 33,             # over the 32-char bound
        "-ctrl+v",            # leading dash (option injection)
    ])
    def test_terminal_paste_key_rejects_bad_values(self, cfg, bad):
        _, rejected = apply_settings(
            cfg, {"insertion": {"terminal_paste_key": bad}})
        assert rejected == ["insertion.terminal_paste_key"]

    def test_save_whitelist_roundtrip(self, tmp_path, monkeypatch):
        from fluidvoice import paths as p
        monkeypatch.setattr(p, "config_file", lambda: tmp_path / "c.toml")
        cfg = copy.deepcopy(DEFAULTS)
        cfg["insertion"].update(verify_paste=False,
                                 terminal_paste_key="ctrl+v")
        save_config(cfg)
        loaded = load_config(tmp_path / "c.toml")
        assert loaded["insertion"]["verify_paste"] is False
        assert loaded["insertion"]["terminal_paste_key"] == "ctrl+v"


class TestCommandSettings:
    """Command mode keys: hotkey.command_key + the [command] section."""

    def test_defaults(self, cfg):
        assert cfg["hotkey"]["command_key"] == ""
        assert cfg["command"] == {"max_turns": 4, "working_dir": "",
                                  "timeout_seconds": 60.0,
                                  "confirm_timeout_s": 120.0,
                                  "destructive_patterns": [],
                                  "context_window_s": 300.0}

    def test_apply_accepts_command_keys(self, cfg):
        changed, rejected = apply_settings(cfg, {
            "hotkey": {"command_key": "F9"},
            "command": {"max_turns": 6, "working_dir": "/tmp",
                        "timeout_seconds": 30, "confirm_timeout_s": 60},
        })
        assert rejected == []
        assert set(changed) == {"hotkey.command_key", "command.max_turns",
                                "command.working_dir",
                                "command.timeout_seconds",
                                "command.confirm_timeout_s"}
        assert cfg["hotkey"]["command_key"] == "F9"
        assert cfg["command"]["max_turns"] == 6
        assert cfg["command"]["working_dir"] == "/tmp"
        assert cfg["command"]["timeout_seconds"] == 30.0
        assert cfg["command"]["confirm_timeout_s"] == 60.0

    def test_apply_rejects_bad_values(self, cfg):
        before = copy.deepcopy(cfg["command"])
        _, rejected = apply_settings(cfg, {
            "command": {"max_turns": 0, "working_dir": "x" * 5000,
                        "timeout_seconds": 0}})
        _, rejected2 = apply_settings(cfg, {
            "command": {"max_turns": 21}})
        _, rejected3 = apply_settings(cfg, {
            "command": {"max_turns": "four"}})
        assert "command.max_turns" in rejected  # 0
        assert "command.working_dir" in rejected
        assert "command.timeout_seconds" in rejected
        _, rejected4 = apply_settings(cfg, {
            "command": {"context_window_s": -1}})
        _, rejected5 = apply_settings(cfg, {
            "command": {"context_window_s": 90000}})
        assert "command.max_turns" in rejected2  # 21
        assert "command.max_turns" in rejected3  # "four"
        assert "command.context_window_s" in rejected4  # < 0
        assert "command.context_window_s" in rejected5  # > 86400
        assert cfg["command"] == before  # untouched

    def test_save_whitelist_writes_command_section(self, tmp_path, monkeypatch):
        from fluidvoice import paths as p
        monkeypatch.setattr(p, "config_file", lambda: tmp_path / "c.toml")
        cfg = copy.deepcopy(DEFAULTS)
        cfg["hotkey"]["command_key"] = "F9"
        cfg["command"].update(max_turns=6, working_dir="/tmp",
                              timeout_seconds=30, confirm_timeout_s=60,
                              context_window_s=0.0)
        save_config(cfg)
        text = (tmp_path / "c.toml").read_text()
        assert "[command]" in text
        assert "command_key = \"F9\"" in text
        assert "max_turns = 6" in text
        assert "destructive_patterns = []" in text
        assert "context_window_s = 0.0" in text
        loaded = load_config(tmp_path / "c.toml")
        assert loaded["command"] == {"max_turns": 6, "working_dir": "/tmp",
                                     "timeout_seconds": 30.0,
                                     "confirm_timeout_s": 60.0,
                                     "destructive_patterns": [],
                                     "context_window_s": 0.0}


class TestParityPackKeys:
    """B1/B3/B4 settings: hotkey.mode 'both', extra_shortcuts,
    formatting_action_triggers."""

    def test_mode_both_accepted(self, cfg):
        changed, rejected = apply_settings(cfg, {"hotkey": {"mode": "both"}})
        assert rejected == [] and cfg["hotkey"]["mode"] == "both"

    def test_extra_shortcuts_valid_and_garbage(self, cfg):
        ok = [{"key": "F8", "modifiers": ["ctrl"], "profile": "Terse"},
              {"key": "button9"}]
        changed, rejected = apply_settings(
            cfg, {"hotkey": {"extra_shortcuts": ok}})
        assert rejected == []
        assert cfg["hotkey"]["extra_shortcuts"][1] == \
            {"key": "button9", "modifiers": []}
        changed, rejected = apply_settings(
            cfg, {"hotkey": {"extra_shortcuts": [{"key": ""}]}})
        assert rejected == ["hotkey.extra_shortcuts"]
        changed, rejected = apply_settings(
            cfg, {"hotkey": {"extra_shortcuts": [
                {"key": "F7"}, {"key": "F8"}, {"key": "F9"}]}})
        assert rejected == ["hotkey.extra_shortcuts"]  # max 2 extras

    def test_formatting_action_triggers_roundtrip(self, cfg):
        changed, rejected = apply_settings(cfg, {
            "processing": {"formatting_action_triggers":
                           {"new_line": ["nova vrstica", "naslednja vrstica"],
                            "space": ["presledek"]}}})
        assert rejected == []
        assert cfg["processing"]["formatting_action_triggers"] == {
            "new_line": ["nova vrstica", "naslednja vrstica"],
            "space": ["presledek"]}
        changed, rejected = apply_settings(cfg, {
            "processing": {"formatting_action_triggers":
                           {"nope": ["x"]}}})
        assert rejected == ["processing.formatting_action_triggers"]


class TestIdleUnloadSetting:
    """model.idle_unload_s: 0 (off, default) or 30..86400 seconds.
    The plain SETTING_RANGES table cannot express "0 or >= 30", so this is
    a custom coerce_setting branch (daemon-side live-apply in Phase 2)."""

    def test_default_off(self, cfg):
        assert cfg["model"]["idle_unload_s"] == 0

    def test_documented_in_template(self):
        assert "idle_unload_s" in TEMPLATE  # cheap doc-guard idiom

    @pytest.mark.parametrize("value", [30, 60, 3600, 86400])
    def test_accepts_valid(self, cfg, value):
        changed, rejected = apply_settings(
            cfg, {"model": {"idle_unload_s": value}})
        assert rejected == [] and changed == ["model.idle_unload_s"]
        assert cfg["model"]["idle_unload_s"] == value

    @pytest.mark.parametrize("value", [29, 1, 5, 86401, -60, 0 - 1,
                                       "abc", True, 30.5, None, []])
    def test_rejects_invalid(self, cfg, value):
        changed, rejected = apply_settings(
            cfg, {"model": {"idle_unload_s": value}})
        assert rejected == ["model.idle_unload_s"] and changed == []
        assert cfg["model"]["idle_unload_s"] == 0  # untouched

    def test_zero_is_a_real_change_when_turning_off(self, cfg):
        cfg["model"]["idle_unload_s"] = 300
        changed, rejected = apply_settings(
            cfg, {"model": {"idle_unload_s": 0}})
        assert rejected == [] and changed == ["model.idle_unload_s"]
        assert cfg["model"]["idle_unload_s"] == 0

    def test_no_change_reported_when_same_value(self, cfg):
        cfg["model"]["idle_unload_s"] = 300
        changed, rejected = apply_settings(
            cfg, {"model": {"idle_unload_s": 300}})
        assert rejected == [] and changed == []

    def test_persisted_by_save_config(self, tmp_path, monkeypatch):
        from fluidvoice import paths as p
        monkeypatch.setattr(p, "config_file", lambda: tmp_path / "c.toml")
        cfg = copy.deepcopy(DEFAULTS)
        cfg["model"]["idle_unload_s"] = 300
        save_config(cfg)
        assert load_config(tmp_path / "c.toml")["model"]["idle_unload_s"] == 300

    def test_not_restart_required_not_engine_key(self):
        assert "model.idle_unload_s" not in RESTART_REQUIRED
        from fluidvoice.config import ENGINE_KEYS
        assert "model.idle_unload_s" not in ENGINE_KEYS

    def test_coerce_direct(self):
        assert coerce_setting("model", "idle_unload_s", 300) == (True, 300)
        assert coerce_setting("model", "idle_unload_s", 0) == (True, 0)
        assert coerce_setting("model", "idle_unload_s", True)[0] is False
        assert coerce_setting("model", "idle_unload_s", 29)[0] is False
