"""AI provider profiles (Settings → AI): radio rows (Custom + stored
names), instant switch through set-config, pointer clear on hand edits,
rename/delete pointer upkeep. GTK tests follow tests/test_settings_profiles.py
(offscreen windows driven by a stub client); the module skips headless.
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

from fluidvoice.config import DEFAULTS  # noqa: E402
from fluidvoice.gtkui.client import Client  # noqa: E402

GLM_PROFILE = {"base_url": "https://api.z.ai/api/paas/v4",
               "model": "glm-4.7", "api_key_env": "ZAI_API_KEY"}


class _ProviderStubClient(Client):
    """Client surface scoped to provider profiles + set-config."""

    def __init__(self, profiles=None, cfg_overrides=None):
        super().__init__()
        self.store: dict[str, dict] = {k: dict(v)
                                       for k, v in (profiles or {}).items()}
        self.saved: list[dict] = []
        self.overrides = cfg_overrides or {}

    def get_config(self):
        cfg = copy.deepcopy(DEFAULTS)
        for sec, keys in self.overrides.items():
            cfg[sec].update(keys)
        return cfg, True

    def set_config(self, body):
        self.saved.append(copy.deepcopy(body))
        for sec, keys in body.items():
            self.overrides.setdefault(sec, {}).update(keys)
        changed = [f"{s}.{k}" for s, keys in body.items() for k in keys]
        return {"ok": True, "changed": changed, "rejected": [],
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
