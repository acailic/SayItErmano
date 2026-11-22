"""AI provider profiles (Settings → AI): radio rows (Custom + stored
names), instant switch through set-config, endpoint rows mirroring the
switch, pointer clear on hand edits, rename/delete pointer upkeep. GTK
tests follow tests/test_settings_profiles.py (offscreen windows driven by
a stub client); the module skips headless.
"""
from __future__ import annotations

import copy
import os

import pytest

pytestmark = pytest.mark.needs_display  # display/GTK lane (Q1)

gi = pytest.importorskip("gi", reason="PyGObject not installed")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:  # pragma: no cover - depends on the machine
    pytest.skip(f"GTK4/Adw unavailable: {e}", allow_module_level=True)
if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    pytest.skip("no display for GTK tests", allow_module_level=True)  # pragma: no cover

from gi.repository import GLib  # noqa: E402

from fluidvoice import config  # noqa: E402
from fluidvoice.config import DEFAULTS  # noqa: E402
from fluidvoice.gtkui.client import Client, ClientError  # noqa: E402

GLM_PROFILE = {"base_url": "https://api.z.ai/api/paas/v4",
               "model": "glm-4.7", "api_key_env": "ZAI_API_KEY"}
#: the shipped Ollama template shape: endpoint only, no model, no key env
OLLAMA_PROFILE = {"base_url": "http://localhost:11434/v1",
                  "model": "", "api_key_env": ""}


class _ProviderStubClient(Client):
    """Client surface scoped to provider profiles + set-config. set_config
    validates through the real registry (config.apply_settings - what the
    daemon's set-config route and the file-only fallback both run), so a
    body the endpoint rows could not save is rejected here too."""

    def __init__(self, profiles=None, cfg_overrides=None):
        super().__init__()
        self.store: dict[str, dict] = {k: dict(v)
                                        for k, v in (profiles or {}).items()}
        self.saved: list[dict] = []
        self.state = copy.deepcopy(DEFAULTS)
        for sec, keys in (cfg_overrides or {}).items():
            self.state[sec].update(keys)

    def get_config(self):
        return copy.deepcopy(self.state), True

    def set_config(self, body):
        self.saved.append(copy.deepcopy(body))
        changed, rejected = config.apply_settings(self.state, body)
        return {"ok": not rejected, "changed": changed, "rejected": rejected,
                "restart_required": [], "errors": [], "note": ""}

    # -- provider profiles (mirrors Client's four methods) --------------------

    def provider_profiles(self):
        return {k: dict(v) for k, v in self.store.items()}

    def provider_profile_save(self, name, profile):
        self.store[name] = dict(profile)
        return {"ok": True, "error": None, "profiles": self.provider_profiles()}

    def provider_profile_rename(self, old, new):
        if old not in self.store:
            return {"ok": False, "error": f"no profile named {old!r}",
                    "profiles": self.provider_profiles()}
        self.store = {(new if k == old else k): v
                      for k, v in self.store.items()}
        return {"ok": True, "error": None, "profiles": self.provider_profiles()}

    def provider_profile_delete(self, name):
        if name not in self.store:
            return {"ok": False, "error": f"no profile named {name!r}",
                    "profiles": self.provider_profiles()}
        del self.store[name]
        return {"ok": True, "error": None, "profiles": self.provider_profiles()}


def pump(loop, ms=150):
    GLib.timeout_add(ms, loop.quit)
    loop.run()


@pytest.fixture()
def loop():
    return GLib.MainLoop()


def _window(loop, client):
    from fluidvoice.gtkui.settings_window import SettingsWindow
    w = SettingsWindow(client=client)
    w.present()
    pump(loop)
    return w


class TestProviderRows:
    def test_custom_row_always_present(self, loop):
        w = _window(loop, _ProviderStubClient())
        assert set(w._prov_rows) == {""}
        assert w._prov_rows[""].get_title() == "Custom (hand-set endpoint)"
        assert w._prov_checks[""].get_active() is True
        w.close()

    def test_rows_render_from_stub_profiles(self, loop):
        c = _ProviderStubClient({"GLM": GLM_PROFILE})
        w = _window(loop, c)
        assert set(w._prov_rows) == {"", "GLM"}
        assert w._prov_rows["GLM"].get_title() == "GLM"
        assert w._prov_checks["GLM"].get_active() is False  # pointer ""
        w.close()

    def test_active_pointer_marks_radio(self, loop):
        c = _ProviderStubClient({"GLM": GLM_PROFILE},
                                {"ai": {"profile": "GLM"}})
        w = _window(loop, c)
        assert w._prov_checks["GLM"].get_active() is True
        assert w._prov_checks[""].get_active() is False
        w.close()

    def test_dangling_pointer_falls_back_to_custom(self, loop):
        """A pointer naming no stored profile (hand-edited or deleted
        JSON) shows as Custom - one active radio, never none."""
        c = _ProviderStubClient({"GLM": GLM_PROFILE},
                                {"ai": {"profile": "Ghost"}})
        w = _window(loop, c)
        active = [n for n, check in w._prov_checks.items()
                  if check.get_active()]
        assert active == [""]
        w.close()


class TestSwitch:
    def test_toggle_sends_switch_patch(self, loop):
        c = _ProviderStubClient({"GLM": GLM_PROFILE})
        w = _window(loop, c)
        w._prov_checks["GLM"].set_active(True)
        pump(loop)
        assert c.saved == [{"ai": {**GLM_PROFILE, "profile": "GLM"}}]
        assert w.cfg["ai"]["profile"] == "GLM"
        w.close()

    def test_custom_toggle_only_clears_pointer(self, loop):
        c = _ProviderStubClient({"GLM": GLM_PROFILE},
                                {"ai": {"profile": "GLM"}})
        w = _window(loop, c)
        w._prov_checks[""].set_active(True)
        pump(loop)
        assert c.saved == [{"ai": {"profile": ""}}]
        w.close()

    def test_switch_updates_endpoint_rows(self, loop):
        """The rows show what the daemon now holds: base URL, model, key
        env - "Test connection" reads exactly these."""
        c = _ProviderStubClient({"GLM": GLM_PROFILE})
        w = _window(loop, c)
        w._prov_checks["GLM"].set_active(True)
        pump(loop)
        assert w._get_text("ai", "base_url") == GLM_PROFILE["base_url"]
        assert w._get_text("ai", "model") == "glm-4.7"
        assert w._get_text("ai", "api_key_env") == "ZAI_API_KEY"
        w.close()

    def test_save_after_switch_keeps_the_profile(self, loop):
        """Regression: the rows used to keep the pre-switch endpoint, so
        the next Save sent it back and silently reverted the switch while
        the pointer still named the profile."""
        c = _ProviderStubClient({"GLM": GLM_PROFILE})
        w = _window(loop, c)
        w._prov_checks["GLM"].set_active(True)
        pump(loop)
        w.save()
        pump(loop)
        sent = c.saved[-1]["ai"]
        assert (sent["base_url"], sent["model"], sent["api_key_env"]) == (
            GLM_PROFILE["base_url"], "glm-4.7", "ZAI_API_KEY")
        assert "profile" not in sent  # a switch is not a hand edit
        assert c.state["ai"]["profile"] == "GLM"
        w.close()

    def test_template_without_model_switches_endpoint_only(self, loop):
        """Empty preset values mean "keep the saved value" (the registry
        rejects "" for model/key env): no rejection, pointer moves, the
        model row keeps the model to be picked by hand."""
        c = _ProviderStubClient({"Ollama": OLLAMA_PROFILE},
                                {"ai": {"model": "qwen3:8b"}})
        w = _window(loop, c)
        toasts: list[str] = []
        w.toast = lambda text, timeout=5: toasts.append(text)
        w._prov_checks["Ollama"].set_active(True)
        pump(loop)
        assert c.saved == [{"ai": {"base_url": "http://localhost:11434/v1",
                                   "profile": "Ollama"}}]
        assert toasts == ["AI profile: Ollama"]
        assert c.state["ai"]["model"] == "qwen3:8b"
        assert w._get_text("ai", "model") == "qwen3:8b"
        assert w._prov_checks["Ollama"].get_active() is True
        w.close()

    def test_rejected_value_is_reported_and_rest_applies(self, loop):
        """The daemon applies what validates and rejects the rest; the
        page mirrors exactly that and names the rejected keys."""
        hot = {**GLM_PROFILE, "temperature": 9.0}
        c = _ProviderStubClient({"Hot": hot})
        w = _window(loop, c)
        toasts: list[str] = []
        w.toast = lambda text, timeout=5: toasts.append(text)
        w._prov_checks["Hot"].set_active(True)
        pump(loop)
        assert toasts == ["AI profile: Hot",
                          "Rejected (bad values): ai.temperature"]
        assert c.state["ai"]["profile"] == "Hot"
        assert c.state["ai"]["temperature"] == DEFAULTS["ai"]["temperature"]
        assert w._prov_checks["Hot"].get_active() is True
        assert w._get_text("ai", "base_url") == GLM_PROFILE["base_url"]
        w.close()

    def test_failed_switch_leaves_the_radio_alone(self, loop):
        c = _ProviderStubClient({"GLM": GLM_PROFILE})

        def refused(body):
            raise ClientError("daemon down")
        c.set_config = refused
        w = _window(loop, c)
        toasts: list[str] = []
        w.toast = lambda text, timeout=5: toasts.append(text)
        w._prov_checks["GLM"].set_active(True)
        pump(loop)
        assert toasts == ["Profile switch failed: daemon down"]
        assert w._prov_checks[""].get_active() is True
        assert w._get_text("ai", "base_url") == DEFAULTS["ai"]["base_url"]
        w.close()


class TestPointerClear:
    def test_endpoint_edit_queues_clear_and_save_writes_it(self, loop):
        c = _ProviderStubClient({"GLM": GLM_PROFILE},
                                {"ai": {"profile": "GLM"}})
        w = _window(loop, c)
        row = w._rows[("ai", "base_url")]
        row.set_text("https://example.test/v1")
        pump(loop, ms=20)
        assert w._pending_provider_clear is True
        w.save()
        pump(loop)
        sent = c.saved[-1]
        assert sent.get("ai", {}).get("profile") == ""
        assert w._pending_provider_clear is False
        w.close()

    def test_no_clear_without_active_pointer(self, loop):
        c = _ProviderStubClient({"GLM": GLM_PROFILE})
        w = _window(loop, c)
        row = w._rows[("ai", "base_url")]
        row.set_text("https://example.test/v1")
        pump(loop, ms=20)
        assert w._pending_provider_clear is False
        w.close()

    def test_discard_drops_the_pending_clear(self, loop):
        """A reload (the Discard button) drops the queued pointer clear
        together with the edit that queued it."""
        c = _ProviderStubClient({"GLM": GLM_PROFILE},
                                {"ai": {"profile": "GLM"}})
        w = _window(loop, c)
        w._rows[("ai", "base_url")].set_text("https://example.test/v1")
        pump(loop, ms=20)
        assert w._pending_provider_clear is True
        w._load()
        assert w._pending_provider_clear is False
        w.save()
        pump(loop)
        assert "profile" not in c.saved[-1]["ai"]
        assert c.state["ai"]["profile"] == "GLM"
        w.close()


class TestRenameDeleteUpkeep:
    def test_delete_active_clears_pointer(self, loop):
        c = _ProviderStubClient(
            {"GLM": GLM_PROFILE, "Local": {"base_url": "http://x",
                                           "model": "m", "api_key_env": ""}},
            {"ai": {"profile": "GLM"}})
        w = _window(loop, c)
        w._provider_delete_confirmed("GLM")
        pump(loop)
        assert c.saved == [{"ai": {"profile": ""}}]
        assert "GLM" not in c.store
        w.close()

    def test_rename_active_moves_pointer(self, loop):
        c = _ProviderStubClient({"GLM": GLM_PROFILE},
                                {"ai": {"profile": "GLM"}})
        w = _window(loop, c)
        w._provider_rename_confirmed("GLM", "GLM cloud")
        pump(loop)
        assert c.saved == [{"ai": {"profile": "GLM cloud"}}]
        assert "GLM cloud" in c.store
        w.close()
