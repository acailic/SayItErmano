"""Fast language switching + wrong-language hallucination guard.

Covers (per specs/3c8d6007_language-cycle-guard.md):
  - config keys: general.language_cycle, general.language_whitelist,
    hotkey.language_key (strict validation against KNOWN_LANGUAGES)
  - runtime precedence: cycle override > model.languages > general.language
  - the whitelist guard: one re-decode when auto-detection lands outside
  - the daemon cycle state machine (hotkey, announce, status, tray, CLI)
  - the Settings editors and the doctor section
"""
from __future__ import annotations

import copy

import pytest

from fluidvoice import backends, config as config_mod
from fluidvoice.config import DEFAULTS, KNOWN_LANGUAGES


def _codes(n: int) -> list[str]:
    """n distinct known language codes (for cap tests)."""
    return KNOWN_LANGUAGES[:n]


# ---------------------------------------------------------------------------
# Phase 1 - config validation
# ---------------------------------------------------------------------------

class TestConfigValidation:
    def test_cycle_accepts_known_codes_and_auto(self):
        ok, v = config_mod.coerce_setting("general", "language_cycle",
                                          ["auto", "en", "sl"])
        assert ok and v == ["auto", "en", "sl"]

    def test_cycle_rejects_unknown_code(self):
        ok, _ = config_mod.coerce_setting("general", "language_cycle",
                                          ["auto", "xx"])
        assert not ok

    def test_cycle_rejects_empty_entry(self):
        ok, _ = config_mod.coerce_setting("general", "language_cycle", [""])
        assert not ok

    def test_cycle_rejects_non_list(self):
        for bad in ("en", 42, {"en": 1}, None):
            ok, _ = config_mod.coerce_setting("general", "language_cycle", bad)
            assert not ok

    def test_cycle_rejects_non_str_entry(self):
        ok, _ = config_mod.coerce_setting("general", "language_cycle", ["en", 3])
        assert not ok

    def test_cycle_rejects_too_many_entries(self):
        ok, _ = config_mod.coerce_setting(
            "general", "language_cycle", ["auto"] + _codes(8))
        assert not ok

    def test_cycle_normalizes_case_and_dedupes(self):
        ok, v = config_mod.coerce_setting("general", "language_cycle",
                                          ["EN", "en", " Sl "])
        assert ok and v == ["en", "sl"]

    def test_whitelist_accepts_known_codes(self):
        ok, v = config_mod.coerce_setting("general", "language_whitelist",
                                          ["sl", "en"])
        assert ok and v == ["sl", "en"]

    def test_whitelist_rejects_auto(self):
        ok, _ = config_mod.coerce_setting("general", "language_whitelist",
                                          ["auto"])
        assert not ok

    def test_whitelist_rejects_unknown(self):
        ok, _ = config_mod.coerce_setting("general", "language_whitelist", ["xx"])
        assert not ok

    def test_whitelist_rejects_non_list(self):
        ok, _ = config_mod.coerce_setting("general", "language_whitelist", "sl")
        assert not ok

    def test_whitelist_rejects_too_many(self):
        ok, _ = config_mod.coerce_setting(
            "general", "language_whitelist", _codes(17))
        assert not ok

    def test_whitelist_dedupes_case_insensitively(self):
        ok, v = config_mod.coerce_setting("general", "language_whitelist",
                                          ["SL", "sl", "En"])
        assert ok and v == ["sl", "en"]

    def test_defaults_are_off(self):
        assert DEFAULTS["general"]["language_cycle"] == []
        assert DEFAULTS["general"]["language_whitelist"] == []
        assert DEFAULTS["hotkey"]["language_key"] == ""

    def test_apply_settings_round_trip_good(self):
        cfg = copy.deepcopy(DEFAULTS)
        body = {"general": {"language_cycle": ["auto", "en", "sl"],
                            "language_whitelist": ["sl", "en"]},
                "hotkey": {"language_key": "F7"}}
        changed, rejected = config_mod.apply_settings(cfg, body)
        assert not rejected
        assert {"general.language_cycle", "general.language_whitelist",
                "hotkey.language_key"} <= set(changed)
        assert cfg["general"]["language_cycle"] == ["auto", "en", "sl"]
        assert cfg["general"]["language_whitelist"] == ["sl", "en"]
        assert cfg["hotkey"]["language_key"] == "F7"

    def test_apply_settings_rejects_bad_values(self):
        cfg = copy.deepcopy(DEFAULTS)
        before = copy.deepcopy(cfg)
        body = {"general": {"language_cycle": ["xx"],
                            "language_whitelist": ["auto"]},
                "hotkey": {"language_key": "F7"}}
        changed, rejected = config_mod.apply_settings(cfg, body)
        assert set(rejected) == {"general.language_cycle",
                                 "general.language_whitelist"}
        assert changed == ["hotkey.language_key"]
        assert cfg["general"]["language_cycle"] == before["general"]["language_cycle"]
        assert cfg["general"]["language_whitelist"] == \
            before["general"]["language_whitelist"]
        assert cfg["hotkey"]["language_key"] == "F7"

    def test_apply_settings_no_change_when_equal(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = ["en"]
        changed, rejected = config_mod.apply_settings(
            cfg, {"general": {"language_cycle": ["en"]}})
        assert not changed and not rejected

    def test_save_and_load_round_trip(self, tmp_path):
        path = tmp_path / "config.toml"
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = ["auto", "en", "sl"]
        cfg["general"]["language_whitelist"] = ["sl", "en"]
        cfg["hotkey"]["language_key"] = "F7"
        config_mod.save_config(cfg, path)
        loaded = config_mod.load_config(path)
        assert loaded["general"]["language_cycle"] == ["auto", "en", "sl"]
        assert loaded["general"]["language_whitelist"] == ["sl", "en"]
        assert loaded["hotkey"]["language_key"] == "F7"

    def test_empty_lists_persist_as_off(self, tmp_path):
        # mic_priority semantics: [] is meaningful (removals round-trip)
        path = tmp_path / "config.toml"
        path.write_text('[general]\nlanguage_cycle = ["en"]\n'
                        'language_whitelist = ["sl"]\n')
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = ["en"]
        cfg["general"]["language_whitelist"] = ["sl"]
        cfg["general"]["language_cycle"] = []
        cfg["general"]["language_whitelist"] = []
        config_mod.save_config(cfg, path)
        text = path.read_text()
        assert "language_cycle = []" in text
        assert "language_whitelist = []" in text
        loaded = config_mod.load_config(path)
        assert loaded["general"]["language_cycle"] == []
        assert loaded["general"]["language_whitelist"] == []

    def test_load_without_keys_yields_defaults(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('[general]\nlanguage = "de"\n')
        loaded = config_mod.load_config(path)
        assert loaded["general"]["language_cycle"] == []
        assert loaded["general"]["language_whitelist"] == []
        assert loaded["hotkey"]["language_key"] == ""

    def test_unknown_language_key_rejected_by_str_range(self):
        # same rule as paste_key: only strings 1..64 chars; the value is
        # grab-time validated by HotkeyListener
        ok, _ = config_mod.coerce_setting("hotkey", "language_key", 42)
        assert not ok
