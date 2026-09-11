"""Settings depth pack: base prompt + prompt profiles (Phase 1/2 plan).

Phase 1: the editable ai.base_prompt key (config validation, AI client /
pipeline consumption, the Settings → AI editor). Phase 2: named presets of
it (sidecar CRUD + the profile bar). GTK tests follow tests/test_gtkui.py
(offscreen windows driven by a stub client); the module skips headless.
"""
from __future__ import annotations

import copy
import os

import pytest

# GUI lane (Q1): provisioned tier — unit tier deselects this marker
pytestmark = pytest.mark.gtk

gi = pytest.importorskip("gi", reason="PyGObject not installed")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
except ValueError as e:  # pragma: no cover - depends on the machine
    pytest.skip(f"GTK4/Adw unavailable: {e}", allow_module_level=True)
if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    pytest.skip("no display for GTK tests", allow_module_level=True)  # pragma: no cover

from gi.repository import GLib  # noqa: E402

from fluidvoice.config import (  # noqa: E402
    DEFAULTS,
    apply_settings,
    coerce_setting,
    load_config,
    save_config,
)
from fluidvoice.gtkui.client import Client  # noqa: E402


class TestBasePromptConfig:
    """ai.base_prompt: empty = the built-in dictation prompt."""

    def test_default_is_empty(self):
        assert DEFAULTS["ai"]["base_prompt"] == ""

    def test_coerce_accepts_empty_and_long(self):
        ok, out = coerce_setting("ai", "base_prompt", "")
        assert ok is True and out == ""
        ok, out = coerce_setting("ai", "base_prompt", "x" * 7999)
        assert ok is True and out == "x" * 7999

    @pytest.mark.parametrize("bad", ["x" * 8001, 42, None, ["p"], True])
    def test_coerce_rejects_bad(self, bad):
        ok, out = coerce_setting("ai", "base_prompt", bad)
        assert ok is False and out == bad

    def test_apply_settings_roundtrip(self):
        cfg = copy.deepcopy(DEFAULTS)
        changed, rejected = apply_settings(
            cfg, {"ai": {"base_prompt": "CUSTOM"}})
        assert rejected == [] and cfg["ai"]["base_prompt"] == "CUSTOM"
        assert "ai.base_prompt" in changed
        # clearing is meaningful too, not a rejection
        _, rejected = apply_settings(cfg, {"ai": {"base_prompt": ""}})
        assert rejected == [] and cfg["ai"]["base_prompt"] == ""

    def test_save_writes_and_clear_drops_out(self, tmp_path, monkeypatch):
        from fluidvoice import paths as p
        target = tmp_path / "c.toml"
        monkeypatch.setattr(p, "config_file", lambda: target)
        cfg = copy.deepcopy(DEFAULTS)
        cfg["ai"]["base_prompt"] = "CUSTOM"
        save_config(cfg)
        assert load_config(target)["ai"]["base_prompt"] == "CUSTOM"
        # clearing the editor must actually clear the file (no carry-over
        # of the old value, unlike unmanaged keys such as ai.api_key)
        cfg["ai"]["base_prompt"] = ""
        save_config(cfg)
        assert "base_prompt" not in target.read_text()
        assert load_config(target)["ai"]["base_prompt"] == ""

    def test_template_documents_base_prompt(self):
        from fluidvoice.config import TEMPLATE
        assert "base_prompt" in TEMPLATE  # cheap doc-guard


class TestBasePromptConsumption:
    def _cfg(self, base_prompt):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["ai"]["base_prompt"] = base_prompt
        return cfg

    def test_ai_client_uses_custom_base(self):
        from fluidvoice.ai.client import AIClient
        assert AIClient(self._cfg("X")).system_prompt == "X"

    def test_ai_client_empty_uses_builtin(self):
        from fluidvoice.ai.client import AIClient
        from fluidvoice.ai.prompts import default_dictation_prompt
        assert AIClient(self._cfg("")).system_prompt == default_dictation_prompt()
        assert AIClient(self._cfg("")).system_prompt.startswith(
            "You are a voice-to-text dictation cleaner.")

    def test_pipeline_per_app_compose_uses_custom_base(self, monkeypatch):
        """system_prompt_for must compose the CONFIG base, not the built-in."""
        from fluidvoice import daemon as dm
        cfg = self._cfg("CUSTOM BASE")
        cfg["ai"]["enabled"] = True
        cfg["ai"]["per_app_prompts"] = [
            {"apps": ["zed"], "instructions": "be terse"}]
        captured: list = []

        class FakeAIClient:
            def __init__(self, c):
                self.cfg = c

            def polish(self, text, system_prompt=None):
                captured.append(system_prompt)
                return text

        monkeypatch.setattr(dm, "AIClient", FakeAIClient)
        pipe = dm.DictationPipeline(cfg, type("B", (), {
            "name": "stub", "transcribe": lambda self, w, language=None: {}})())
        out, ai_used = pipe._polish("txt", app_hint="zed")
        assert ai_used is True and out == "txt"
        assert len(captured) == 1
        assert captured[0].startswith("CUSTOM BASE")
        assert "be terse" in captured[0]

    def test_base_prompt_for_helper(self):
        from fluidvoice.ai.prompts import base_prompt_for, default_dictation_prompt
        assert base_prompt_for(self._cfg("X")) == "X"
        assert base_prompt_for(self._cfg("")) == default_dictation_prompt()
        assert base_prompt_for({}) == default_dictation_prompt()


# ---------------------------------------------------------------------------
# GTK: Settings → AI base-prompt editor
# ---------------------------------------------------------------------------

class _StubClient(Client):
    """Same surface as tests/test_gtkui.py's StubClient, scoped to here."""

    def __init__(self, cfg_overrides=None):
        super().__init__()
        self.saved: list[dict] = []
        self.overrides = cfg_overrides or {}
        self.profile_store: dict[str, str] = {}
        self.profile_calls: list[tuple] = []

    def get_config(self):
        cfg = copy.deepcopy(DEFAULTS)
        for sec, keys in self.overrides.items():
            cfg[sec].update(keys)
        return cfg, True

    def set_config(self, body):
        self.saved.append(body)
        changed = [f"{s}.{k}" for s, keys in body.items() for k in keys]
        return {"ok": True, "changed": changed, "rejected": [],
                "restart_required": [], "errors": [], "note": ""}

    # -- prompt profiles (mirrors Client's four methods) ----------------------

    def prompt_profiles(self):
        return dict(self.profile_store)

    def prompt_profile_save(self, name, prompt):
        self.profile_calls.append(("save", name, prompt))
        self.profile_store[name] = prompt
        return {"ok": True, "error": None,
                "profiles": dict(self.profile_store)}

    def prompt_profile_rename(self, old, new):
        self.profile_calls.append(("rename", old, new))
        if old not in self.profile_store:
            return {"ok": False, "error": f"no profile named {old!r}",
                    "profiles": dict(self.profile_store)}
        self.profile_store = {(new if k == old else k): v
                              for k, v in self.profile_store.items()}
        return {"ok": True, "error": None,
                "profiles": dict(self.profile_store)}

    def prompt_profile_delete(self, name):
        self.profile_calls.append(("delete", name))
        if name not in self.profile_store:
            return {"ok": False, "error": f"no profile named {name!r}",
                    "profiles": dict(self.profile_store)}
        del self.profile_store[name]
        return {"ok": True, "error": None,
                "profiles": dict(self.profile_store)}


def pump(loop, ms=150):
    GLib.timeout_add(ms, loop.quit)
    loop.run()


def pump_until(loop, cond, timeout_s=2.0):
    import time
    deadline = time.monotonic() + timeout_s
    while not cond() and time.monotonic() < deadline:
        pump(loop, ms=20)
    return cond()


@pytest.fixture()
def loop():
    return GLib.MainLoop()


# ---------------------------------------------------------------------------
# Phase 2: prompt profiles (sidecar CRUD)
# ---------------------------------------------------------------------------

class TestProfilesCrud:
    def test_save_load_roundtrip_and_survives_restart(self, tmp_path):
        from fluidvoice.ai import profiles
        p = tmp_path / "prompt-profiles.json"
        profiles.save_named("Terse", "be terse", path=p)
        profiles.save_named("Verbose", "expand nicely", path=p)
        # "restart": a fresh load of the same path keeps everything
        assert profiles.load_profiles(p) == {"Terse": "be terse",
                                             "Verbose": "expand nicely"}

    def test_file_is_0600_and_valid_json(self, tmp_path):
        import json
        import os

        from fluidvoice.ai import profiles
        p = tmp_path / "prompt-profiles.json"
        profiles.save_named("n", "p", path=p)
        assert os.stat(p).st_mode & 0o777 == 0o600
        assert json.loads(p.read_text()) == {"n": "p"}

    def test_upsert_overwrites(self, tmp_path):
        from fluidvoice.ai import profiles
        p = tmp_path / "p.json"
        profiles.save_named("a", "one", path=p)
        out = profiles.save_named("a", "two", path=p)
        assert out == {"a": "two"}
        assert profiles.load_profiles(p) == {"a": "two"}

    def test_rename_preserves_order_and_removes_old(self, tmp_path):
        from fluidvoice.ai import profiles
        p = tmp_path / "p.json"
        profiles.save_named("one", "1", path=p)
        profiles.save_named("two", "2", path=p)
        profiles.save_named("three", "3", path=p)
        out = profiles.rename_profile("two", "TWO", path=p)
        assert list(out) == ["one", "TWO", "three"]
        assert out["TWO"] == "2"
        assert "two" not in profiles.load_profiles(p)

    def test_rename_missing_raises(self, tmp_path):
        from fluidvoice.ai import profiles
        with pytest.raises(ValueError, match="no profile"):
            profiles.rename_profile("nope", "x", tmp_path / "p.json")

    def test_delete_removes_only_named(self, tmp_path):
        from fluidvoice.ai import profiles
        p = tmp_path / "p.json"
        profiles.save_named("a", "1", path=p)
        profiles.save_named("b", "2", path=p)
        out = profiles.delete_profile("a", path=p)
        assert out == {"b": "2"}

    def test_delete_missing_raises(self, tmp_path):
        from fluidvoice.ai import profiles
        with pytest.raises(ValueError, match="no profile"):
            profiles.delete_profile("nope", tmp_path / "p.json")

    @pytest.mark.parametrize("bad", ["", "   ", "x" * 65])
    def test_bad_names_raise(self, tmp_path, bad):
        from fluidvoice.ai import profiles
        p = tmp_path / "p.json"
        with pytest.raises(ValueError):
            profiles.save_named(bad, "x", path=p)
        with pytest.raises(ValueError):
            profiles.delete_profile(bad, path=p)
        assert not p.exists()  # nothing written on a bad name

    def test_missing_file_is_empty_no_warning(self, tmp_path, caplog):
        from fluidvoice.ai import profiles
        with caplog.at_level("WARNING"):
            assert profiles.load_profiles(tmp_path / "nope.json") == {}
        assert len(caplog.records) == 0

    @pytest.mark.parametrize("content", [
        "{not json", "[1, 2]", "\"just a string\"", "42",
    ])
    def test_malformed_file_one_warning(self, tmp_path, caplog, content):
        from fluidvoice.ai import profiles
        p = tmp_path / "p.json"
        p.write_text(content)
        with caplog.at_level("WARNING"):
            assert profiles.load_profiles(p) == {}
        warnings = [r for r in caplog.records
                    if "prompt-profiles" in r.getMessage()]
        assert len(warnings) == 1

    def test_non_str_values_dropped(self, tmp_path):
        import json

        from fluidvoice.ai import profiles
        p = tmp_path / "p.json"
        p.write_text(json.dumps({"ok": "yes", "num": 5, "": "empty name"}))
        assert profiles.load_profiles(p) == {"ok": "yes"}

    def test_default_path_uses_config_dir(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        from fluidvoice.ai import profiles
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert paths.prompt_profiles_file() == \
            tmp_path / "sayit-ermano" / "prompt-profiles.json"
        profiles.save_named("d", "v")
        assert profiles.load_profiles() == {"d": "v"}


class TestProfileBar:
    """v0.8 macOS-parity surface: profiles render as radio rows (one per
    profile) with per-row Rename/Delete menus; Save writes the editor to
    the selected profile."""

    def _w(self, loop, c):
        from fluidvoice.gtkui.settings_window import SettingsWindow
        w = SettingsWindow(client=c)
        w.present()
        pump(loop)
        return w

    def test_rows_render_from_stub_profiles(self, loop):
        c = _StubClient()
        c.profile_store = {"Terse": "be terse"}
        w = self._w(loop, c)
        assert w._profiles == {"Terse": "be terse"}
        assert set(w._profile_rows) == {"Terse"}
        assert w._profile_rows["Terse"].get_title() == "Terse"
        assert w._selected_profile() == "Terse"
        assert w._profile_checks["Terse"].get_active() is True
        w.close()

    def test_empty_store_shows_placeholder(self, loop):
        w = self._w(loop, _StubClient())
        assert w._profile_rows == {}
        assert w._selected_profile() is None
        assert w._profile_none_row.get_visible() is True
        w.close()

    def test_selecting_copies_to_editor_and_marks_dirty(self, loop):
        c = _StubClient()
        c.profile_store = {"Terse": "be terse", "Wordy": "be wordy"}
        w = self._w(loop, c)
        assert w._dirty is False
        w._profile_checks["Wordy"].set_active(True)  # radio select
        assert w._rows[("ai", "base_prompt")].get_value() == "be wordy"
        assert w._dirty is True
        w.close()

    def test_save_writes_current_editor_text(self, loop):
        c = _StubClient()
        c.profile_store = {"Old": "old text"}
        w = self._w(loop, c)
        w._rows[("ai", "base_prompt")].set_value("current editor text")
        w._profile_save()
        assert c.profile_calls[-1] == ("save", "Old", "current editor text")
        assert c.profile_store["Old"] == "current editor text"
        w.close()

    def test_save_without_selection_toasts(self, loop):
        c = _StubClient()
        w = self._w(loop, c)
        toasts: list[str] = []
        w.toast = lambda text, timeout=5: toasts.append(text)
        w._profile_save()
        assert c.profile_calls == []
        assert any("Select a profile" in t for t in toasts)
        w.close()

    def test_rename_posts_old_and_new(self, loop):
        c = _StubClient()
        c.profile_store = {"Old": "txt"}
        w = self._w(loop, c)
        w._profile_rename_action(None, None, GLib.Variant.new_string("Old"))
        # drive the name dialog: type the new name, respond OK
        w._name_entry.set_text("Fresh")
        w._name_dlg.emit("response", "ok")
        assert c.profile_calls[-1] == ("rename", "Old", "Fresh")
        assert list(c.profile_store) == ["Fresh"]
        w.close()

    def test_rename_cancel_keeps_store(self, loop):
        c = _StubClient()
        c.profile_store = {"Old": "txt"}
        w = self._w(loop, c)
        w._profile_rename_action(None, None, GLib.Variant.new_string("Old"))
        w._name_entry.set_text("Fresh")
        w._name_dlg.emit("response", "cancel")
        assert c.profile_calls == []
        assert list(c.profile_store) == ["Old"]
        w.close()

    def test_add_profile_saves_editor_text(self, loop):
        c = _StubClient()
        w = self._w(loop, c)
        w._rows[("ai", "base_prompt")].set_value("fresh preset")
        w._profile_add()
        w._name_entry.set_text("New preset")
        w._name_dlg.emit("response", "ok")
        assert c.profile_calls[-1] == ("save", "New preset", "fresh preset")
        assert set(w._profile_rows) == {"New preset"}
        w.close()

    def test_save_does_not_clobber_editor(self, loop):
        """The post-save row rebuild is programmatic: it must not fire the
        selection handler and overwrite the editor."""
        c = _StubClient()
        c.profile_store = {"A": "aaa", "B": "bbb"}
        w = self._w(loop, c)
        w._rows[("ai", "base_prompt")].set_value("my text")
        w._profile_save()
        assert w._rows[("ai", "base_prompt")].get_value() == "my text"
        w.close()

    def test_delete_rebuild_keeps_editor(self, loop):
        c = _StubClient()
        c.profile_store = {"A": "aaa", "B": "bbb"}
        w = self._w(loop, c)
        w._rows[("ai", "base_prompt")].set_value("my text")
        w._on_delete_profile_response(None, "delete", "A")
        assert c.profile_store == {"B": "bbb"}
        assert w._rows[("ai", "base_prompt")].get_value() == "my text"
        w.close()

    def test_delete_confirmed_response_deletes(self, loop):
        c = _StubClient()
        c.profile_store = {"Gone": "x", "Keep": "y"}
        w = self._w(loop, c)
        w._on_delete_profile_response(None, "delete", "Gone")
        assert c.profile_calls[-1] == ("delete", "Gone")
        assert c.profile_store == {"Keep": "y"}
        w.close()

    def test_delete_cancel_response_does_not_delete(self, loop):
        c = _StubClient()
        c.profile_store = {"Gone": "x"}
        w = self._w(loop, c)
        w._on_delete_profile_response(None, "cancel", "Gone")
        assert c.profile_calls == []
        assert c.profile_store == {"Gone": "x"}
        w.close()


class TestBasePromptEditor:
    def test_row_renders_with_cfg_value(self, loop):
        from fluidvoice.gtkui.settings_window import SettingsWindow, _TextProxy
        c = _StubClient({"ai": {"base_prompt": "CUSTOM PROMPT"}})
        w = SettingsWindow(client=c)
        w.present()
        pump(loop)
        assert isinstance(w._rows[("ai", "base_prompt")], _TextProxy)
        assert w._rows[("ai", "base_prompt")].get_value() == "CUSTOM PROMPT"
        w.close()

    def test_editing_marks_dirty_and_save_posts(self, loop):
        from fluidvoice.gtkui.settings_window import SettingsWindow
        c = _StubClient()
        w = SettingsWindow(client=c)
        w.present()
        pump(loop)
        w._rows[("ai", "base_prompt")].set_value("typed prompt")
        assert w._dirty is True
        w.save()
        assert c.saved and c.saved[-1]["ai"]["base_prompt"] == "typed prompt"
        w.close()

    def test_clearing_posts_empty_string(self, loop):
        from fluidvoice.gtkui.settings_window import SettingsWindow
        c = _StubClient({"ai": {"base_prompt": "OLD"}})
        w = SettingsWindow(client=c)
        w.present()
        pump(loop)
        w._rows[("ai", "base_prompt")].set_value("")
        w.save()
        assert c.saved[-1]["ai"].get("base_prompt") == ""  # POSTs even empty
        w.close()

    def test_collect_without_touch_includes_empty(self, loop):
        """Even an untouched save round-trips the (empty) value: collect
        never falls into EntryRow's keep-saved-value rule."""
        from fluidvoice.gtkui.settings_window import SettingsWindow
        c = _StubClient()
        w = SettingsWindow(client=c)
        w.present()
        pump(loop)
        assert w._collect()["ai"]["base_prompt"] == ""
        w.close()

    def test_insert_builtin_fills_editor(self, loop):
        from fluidvoice.ai.prompts import default_dictation_prompt
        from fluidvoice.gtkui.settings_window import SettingsWindow
        w = SettingsWindow(client=_StubClient())
        w.present()
        pump(loop)
        w._insert_builtin_prompt(None)
        assert w._rows[("ai", "base_prompt")].get_value() == \
            default_dictation_prompt()
        assert w._dirty is True
        w.close()
