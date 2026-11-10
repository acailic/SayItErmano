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
        assert pp.switch_patch("GLM", path=p) == \
            {"ai": {**_glm(), "profile": "GLM"}}

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


class TestConfigKey:
    def test_registered_with_empty_default(self):

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
