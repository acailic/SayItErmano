"""P2 per-app behavior profiles: matching, resolution precedence,
spoken-send policy, legacy migration and the config-registry contract."""
from __future__ import annotations

import copy

import pytest

from fluidvoice import config
from fluidvoice.profiles import (
    DEFAULT_PROFILE,
    AppProfile,
    match_rule,
    migrate_legacy_profiles,
    prompt_instructions_for,
    resolve_profile,
    rule_profile,
    spoken_send_allowed,
)

TERMINAL_RULE = {"match": ["gnome-terminal", "kgx"], "terminal": True,
                 "prompt_profile": "", "instructions": "",
                 "insertion_mode": "", "formatting_mode": "",
                 "spoken_send": "off"}
ZED_RULE = {"match": ["zed"], "terminal": False,
            "prompt_profile": "", "instructions": "Bullet style.",
            "insertion_mode": "paste", "formatting_mode": "gaav",
            "spoken_send": ""}


@pytest.fixture()
def cfg():
    return copy.deepcopy(config.DEFAULTS)


# -- matching ---------------------------------------------------------------

class TestMatchRule:
    def test_case_insensitive_substring_first_match_wins(self):
        rules = [ZED_RULE, TERMINAL_RULE,
                 {"match": ["zed"], "instructions": "second"}]
        assert match_rule(rules, "dev.zed.Zed") is ZED_RULE
        assert match_rule(rules, "GNOME-terminal-server") is TERMINAL_RULE

    def test_star_matches_everything(self):
        rules = [{"match": ["*"], "instructions": "always"}]
        assert match_rule(rules, "anything") is rules[0]

    def test_no_identity_or_no_match(self):
        assert match_rule([ZED_RULE], None) is None
        assert match_rule([ZED_RULE], "firefox") is None
        assert match_rule(["junk", {"match": []}], "zed") is None

    def test_malformed_entries_skipped(self):
        rules = ["junk", {"no_match": True},
                 {"match": ["zed"], "instructions": "ok"}]
        assert match_rule(rules, "zed") is rules[2]


# -- resolution precedence ----------------------------------------------------

class TestResolveProfile:
    def test_canonical_rule_fields(self, cfg):
        cfg["profiles"]["rules"] = [ZED_RULE]
        prof = resolve_profile(cfg, "dev.zed.Zed")
        assert prof == AppProfile(terminal=False, prompt_profile="",
                                  instructions="Bullet style.",
                                  insertion_mode="paste",
                                  formatting_mode="gaav", spoken_send="")

    def test_no_match_falls_back_to_legacy_terminal_list(self, cfg):
        cfg["general"]["terminal_apps"] = ["kitty"]
        assert resolve_profile(cfg, "kitty").terminal is True
        assert resolve_profile(cfg, "firefox") == DEFAULT_PROFILE

    def test_canonical_wins_over_legacy(self, cfg):
        cfg["profiles"]["rules"] = [dict(TERMINAL_RULE, terminal=False,
                                         spoken_send="on")]
        cfg["general"]["terminal_apps"] = ["gnome-terminal"]
        assert resolve_profile(cfg, "gnome-terminal-server").terminal is False

    def test_no_identity_is_default(self):
        assert resolve_profile(cfg, None) == DEFAULT_PROFILE

    def test_rule_profile_degrades_off_spec_fields(self):
        prof = rule_profile({"match": ["x"], "insertion_mode": "typed?",
                             "spoken_send": "maybe", "terminal": "yes",
                             "instructions": 5})
        assert prof.insertion_mode == "" and prof.spoken_send == ""
        assert prof.terminal is True  # truthy, as bool(...) before
        assert prof.instructions == ""


# -- prompt instructions -------------------------------------------------------

class TestPromptInstructions:
    def test_canonical_instructions_win(self, cfg):
        cfg["profiles"]["rules"] = [ZED_RULE]
        cfg["ai"]["per_app_prompts"] = [
            {"apps": ["zed"], "instructions": "legacy zed"}]
        assert prompt_instructions_for(cfg, "Zed") == "Bullet style."

    def test_canonical_prompt_profile_preset(self, cfg, monkeypatch):
        import fluidvoice.ai.profiles as preset_mod
        monkeypatch.setattr(preset_mod, "load_profiles",
                            lambda: {"Terse": "Be terse."})
        cfg["profiles"]["rules"] = [
            {"match": ["zed"], "prompt_profile": "Terse"}]
        assert prompt_instructions_for(cfg, "zed") == "Be terse."

    def test_unknown_preset_falls_through_to_legacy(self, cfg):
        cfg["profiles"]["rules"] = [
            {"match": ["zed"], "prompt_profile": "Missing"}]
        cfg["ai"]["per_app_prompts"] = [
            {"apps": ["zed"], "instructions": "legacy zed"}]
        assert prompt_instructions_for(cfg, "zed") == "legacy zed"

    def test_rule_without_prompt_fields_uses_legacy(self, cfg):
        cfg["profiles"]["rules"] = [TERMINAL_RULE]
        cfg["ai"]["per_app_prompts"] = [
            {"apps": ["gnome-terminal"], "instructions": "shell style"}]
        assert prompt_instructions_for(cfg, "gnome-terminal") == \
            "shell style"

    def test_no_rules_is_exactly_legacy(self, cfg):
        from fluidvoice.processing.per_app import match_app_prompt
        legacy = [{"apps": ["zed"], "instructions": "legacy"}]
        cfg["ai"]["per_app_prompts"] = legacy
        for hint in ("dev.zed.Zed", "firefox", None):
            assert prompt_instructions_for(cfg, hint) \
                == match_app_prompt(legacy, hint)


# -- spoken-send policy --------------------------------------------------------

class TestSpokenSendPolicy:
    def test_matrix(self):
        on = AppProfile(spoken_send="on")
        off = AppProfile(spoken_send="off")
        term = AppProfile(terminal=True)
        term_on = AppProfile(terminal=True, spoken_send="on")
        plain = DEFAULT_PROFILE
        # (profile, expected_allowed, expected_why_contains)
        assert spoken_send_allowed(off, default_enabled=True,
                                   phrase_present=True) == (False, "off (profile)")
        assert spoken_send_allowed(on, default_enabled=True,
                                   phrase_present=True)[0] is True
        assert spoken_send_allowed(term, default_enabled=True,
                                   phrase_present=True) == (False,
                                                            "terminal app")
        assert spoken_send_allowed(term_on, default_enabled=True,
                                   phrase_present=True)[0] is True
        assert spoken_send_allowed(plain, default_enabled=True,
                                   phrase_present=True) == (True, "global")
        # feature globally off / phrase absent -> no Enter either way
        assert spoken_send_allowed(plain, default_enabled=False,
                                   phrase_present=True)[0] is False
        assert spoken_send_allowed(plain, default_enabled=True,
                                   phrase_present=False)[0] is False


# -- legacy migration ----------------------------------------------------------

class TestMigration:
    def test_prompts_and_custom_terminal_list_migrate(self, cfg):
        cfg["ai"]["per_app_prompts"] = [
            {"apps": ["zed", "Code"], "instructions": "Bullet style."}]
        cfg["general"]["terminal_apps"] = ["myterm", "kitty"]
        made = migrate_legacy_profiles(
            cfg, default_terminal_apps=config.DEFAULTS["general"]
            ["terminal_apps"])
        assert made == [
            {"match": ["zed", "Code"], "instructions": "Bullet style."},
            {"match": ["myterm", "kitty"], "terminal": True,
             "spoken_send": "off"},
        ]
        assert cfg["profiles"]["rules"] == made
        assert cfg["profiles"]["migrated_from_legacy"] is True
        # legacy keys untouched (read fallback until v1.0)
        assert cfg["ai"]["per_app_prompts"][0]["apps"] == ["zed", "Code"]
        assert cfg["general"]["terminal_apps"] == ["myterm", "kitty"]

    def test_default_terminal_list_not_migrated(self, cfg):
        # an UNcustomized list keeps applying via the legacy fallback;
        # migrating shipped defaults would duplicate noise for everyone
        made = migrate_legacy_profiles(
            cfg, default_terminal_apps=config.DEFAULTS["general"]
            ["terminal_apps"])
        assert made == []
        assert "rules" not in cfg["profiles"] or not cfg["profiles"]["rules"]

    def test_idempotent_after_first_migration(self, cfg):
        cfg["ai"]["per_app_prompts"] = [
            {"apps": ["zed"], "instructions": "x"}]
        first = migrate_legacy_profiles(cfg, default_terminal_apps=[])
        assert first
        cfg["ai"]["per_app_prompts"] = [
            {"apps": ["other"], "instructions": "changed later"}]
        assert migrate_legacy_profiles(cfg, default_terminal_apps=[]) == []
        assert cfg["profiles"]["rules"] == first

    def test_existing_canonical_rules_never_clobbered(self, cfg):
        cfg["profiles"]["rules"] = [{"match": ["mine"],
                                     "instructions": "hand-written"}]
        cfg["ai"]["per_app_prompts"] = [
            {"apps": ["zed"], "instructions": "legacy"}]
        assert migrate_legacy_profiles(cfg, default_terminal_apps=[]) == []
        assert cfg["profiles"]["rules"] == [{"match": ["mine"],
                                             "instructions": "hand-written"}]

    def test_malformed_legacy_entries_skipped(self, cfg):
        cfg["ai"]["per_app_prompts"] = ["junk", {"apps": [],
                                                 "instructions": "x"}]
        assert migrate_legacy_profiles(
            cfg,
            default_terminal_apps=config.DEFAULTS["general"]["terminal_apps"]) == []

    def test_save_config_runs_migration_once(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        monkeypatch.setattr(paths, "config_file",
                            lambda: tmp_path / "c.toml")
        local = copy.deepcopy(config.DEFAULTS)
        local["ai"]["per_app_prompts"] = [
            {"apps": ["zed"], "instructions": "Bullet style."}]
        config.save_config(local)
        text = (tmp_path / "c.toml").read_text()
        assert "Bullet style." in text
        assert "migrated_from_legacy = true" in text
        loaded = config.load_config(tmp_path / "c.toml")
        assert loaded["profiles"]["migrated_from_legacy"] is True
        assert loaded["profiles"]["rules"][0]["match"] == ["zed"]
        # a second save does not duplicate rules
        local["ai"]["per_app_prompts"].append(
            {"apps": ["later"], "instructions": "late addition"})
        config.save_config(local)
        reloaded = config.load_config(tmp_path / "c.toml")
        assert len(reloaded["profiles"]["rules"]) == 1  # idempotent

    def test_save_config_without_legacy_writes_no_marker(self, tmp_path,
                                                         monkeypatch):
        from fluidvoice import paths
        monkeypatch.setattr(paths, "config_file",
                            lambda: tmp_path / "c.toml")
        config.save_config(copy.deepcopy(config.DEFAULTS))
        loaded = config.load_config(tmp_path / "c.toml")
        assert loaded["profiles"]["migrated_from_legacy"] is False
        assert loaded["profiles"]["rules"] == []


# -- config registry contract ---------------------------------------------------

class TestConfigRegistry:
    def test_valid_rules_coerce_and_normalize(self):
        ok, value = config.coerce_setting("profiles", "rules", [
            {"match": [" Kitty ", "kitty"], "terminal": True,
             "spoken_send": "off"},
            {"match": ["zed"], "instructions": "Bullets."},
            {"match": ["f"], "insertion_mode": "auto",
             "formatting_mode": "gaav"}])
        assert ok
        assert value[0]["match"] == ["Kitty"]  # stripped + deduped (ci)
        assert value[0]["terminal"] is True
        assert value[1]["instructions"] == "Bullets."
        assert value[2]["insertion_mode"] == "auto"

    @pytest.mark.parametrize("bad", [
        "not-a-list",
        [{"match": []}],
        [{"match": ["ok"], "unknown_field": 1}],
        [{"match": ["ok"], "terminal": "yes"}],
        [{"match": ["ok"], "insertion_mode": "telepathy"}],
        [{"match": ["ok"], "spoken_send": "sometimes"}],
        [{"match": ["ok"], "formatting_mode": "fancy"}],
        [{"match": ["x" * 65]}],
        [{"match": ["ok"], "prompt_profile": "p", "instructions": "i"}],
        [{"match": ["ok"], "instructions": "x" * 2001}],
        [{"match": [""]}],  # only an empty pattern
    ])
    def test_garbage_rejects(self, bad):
        assert config.coerce_setting("profiles", "rules", bad)[0] is False

    def test_more_than_50_rules_reject(self):
        rules = [{"match": [f"app{i}"]} for i in range(51)]
        assert config.coerce_setting("profiles", "rules", rules)[0] is False

    def test_context_keys_registered(self):
        assert config.default("context", "enabled") is False
        assert config.default("context", "provider") == "auto"
        assert config.default("context", "max_preceding_chars") == 120
        assert ("context", "provider") in config.SETTING_ENUMS
        assert config.validate("context", "provider", "atspi")
        assert not config.validate("context", "provider", "spirit")

    def test_rules_roundtrip_through_save_and_load(self, tmp_path,
                                                   monkeypatch):
        from fluidvoice import paths
        monkeypatch.setattr(paths, "config_file",
                            lambda: tmp_path / "c.toml")
        local = copy.deepcopy(config.DEFAULTS)
        local["profiles"]["rules"] = [
            {"match": ["gnome-terminal", "kgx"], "terminal": True,
             "spoken_send": "off"}]
        config.save_config(local)
        loaded = config.load_config(tmp_path / "c.toml")
        assert loaded["profiles"]["rules"] == local["profiles"]["rules"]
        assert resolve_profile(loaded, "kgx").terminal is True
