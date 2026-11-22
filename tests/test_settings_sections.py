"""Settings/history section builders — org plan 5.5 (D3).

The page builders are split into per-section methods
(_section_hotkeys … _build_views); these tests pin each section's
structure so a section edit that drops or rewires a registry key
fails here, and exercise the MainWindow stats paths the smoke tests
don't reach (span switch, activity-chart drawing).
"""

from __future__ import annotations

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

from gi.repository import Adw  # noqa: E402

Adw.init()

from tests.test_gtkui import StubClient  # noqa: E402


@pytest.fixture
def settings_win():
    from fluidvoice.gtkui.settings_window import SettingsWindow
    w = SettingsWindow(client=StubClient())
    yield w
    w.destroy()


def _walk(widget):
    """Depth-first widget-tree walk (first_child / next_sibling)."""
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _walk(child)
        child = child.get_next_sibling()


class TestDictationSections:
    def test_every_section_builds_in_isolation(self, settings_win) -> None:
        # a fresh page per section: builders must not depend on siblings
        for name in ("hotkeys", "languages", "mic", "preview", "polish",
                     "dictionary", "insertion", "commands"):
            page = Adw.PreferencesPage()
            getattr(settings_win, f"_section_{name}")(page)
            assert page.get_first_child() is not None, name

    def test_registry_keys_wired(self, settings_win) -> None:
        rows = settings_win._rows
        wired = {
            ("hotkey", "key"), ("hotkey", "mode"), ("hotkey", "cancel_key"),
            ("hotkey", "extra_shortcuts"),
            ("general", "language_whitelist"),
            ("recording", "command"), ("recording", "max_seconds"),
            ("recording", "preview_enabled"),
            ("recording", "preview_segmented"),
            ("recording", "preview_vad_silence_s"),
            ("recording", "spoken_send_countdown_s"),
            ("processing", "filler_words"),
            ("processing", "formatting_action_triggers"),
            ("processing", "gaav_enabled"),
            ("insertion", "mode"), ("insertion", "type_delay_ms"),
            ("command", "max_turns"), ("command", "timeout_seconds"),
        }
        missing = sorted(str(k) for k in wired if k not in rows)
        assert not missing, f"sections lost their wiring: {missing}"

    def test_dictionary_groups_exist(self, settings_win) -> None:
        assert settings_win.dict_group is not None
        # suggestions start hidden until the first (non-empty) load
        assert settings_win.suggest_group.get_visible() is False


class TestModelsSections:
    def test_every_section_builds_in_isolation(self, settings_win) -> None:
        for name in ("catalogs", "state", "memory", "engine", "remote",
                     "hotwords", "lang_overrides", "disk_usage"):
            page = Adw.PreferencesPage()
            getattr(settings_win, f"_section_{name}")(page)
            assert page.get_first_child() is not None, name

    def test_registry_keys_wired(self, settings_win) -> None:
        rows = settings_win._rows
        wired = {
            ("model", "backend"), ("model", "device"),
            ("model", "compute"),
            ("model", "remote_url"), ("model", "remote_model"),
            ("model", "remote_api_key"), ("model", "hotwords"),
        }
        missing = sorted(str(k) for k in wired if k not in rows)
        assert not missing, f"sections lost their wiring: {missing}"
        # model.name lives on the catalog rows (Use button), idle_unload_s
        # on its dedicated minutes SpinRow — both must exist as widgets
        assert settings_win._idle_unload_row is not None


class TestLocalPolishSections:
    """Local polish (#21): the AI page toggle + status rows and the
    Models page chat-GGUF catalog group."""

    def test_ai_keys_wired(self, settings_win) -> None:
        rows = settings_win._rows
        wired = {("ai", "local_polish"), ("ai", "local_model"),
                 ("ai", "local_idle_s"), ("ai", "local_server_binary")}
        missing = sorted(str(k) for k in wired if k not in rows)
        assert not missing, f"local polish wiring lost: {missing}"

    def test_toggle_implies_master_switch(self, settings_win) -> None:
        master = settings_win._rows[("ai", "enabled")]
        local = settings_win._rows[("ai", "local_polish")]
        master.set_active(False)
        local.set_active(True)
        assert master.get_active() is True  # one-click contract
        # disabling local polish leaves the master switch alone
        local.set_active(False)
        assert master.get_active() is True

    def test_status_row_exists_and_reports_off(self, settings_win) -> None:
        row = settings_win._local_polish_status_row
        assert row is not None
        assert "off" in row.get_subtitle()

    def test_stop_button_wired(self, settings_win) -> None:
        assert settings_win._local_polish_stop_btn is not None
        from fluidvoice.gtkui.client import ClientError

        def boom():  # daemon down: surfaces as a toast, never a crash
            raise ClientError("down")
        settings_win.c.local_polish_stop = boom
        settings_win._local_polish_stop_btn.emit("clicked")

    def test_chat_catalog_group_built(self, settings_win) -> None:
        from fluidvoice import model_catalog
        assert settings_win.chat_group is not None
        titles = [r.get_title() for r in settings_win._chat_rows]
        assert sorted(model_catalog.CHAT_GGUF_CATALOG) == sorted(titles)

    def test_chat_use_button_saves_local_model(self, settings_win) -> None:
        from fluidvoice import model_catalog
        model_catalog.chat_model_path(
            model_catalog.CHAT_MODEL_DEFAULT).parent.mkdir(
            parents=True, exist_ok=True)
        model_catalog.chat_model_path(
            model_catalog.CHAT_MODEL_DEFAULT).write_bytes(b"x" * 1024)
        settings_win._load()
        try:
            settings_win._use_chat(None, model_catalog.CHAT_MODEL_DEFAULT)
            body = settings_win.c.saved[-1]
            assert body["ai"]["local_model"] == model_catalog.CHAT_MODEL_DEFAULT
        finally:
            model_catalog.chat_model_path(
                model_catalog.CHAT_MODEL_DEFAULT).unlink(missing_ok=True)


class TestProviderProfilesSections:
    """Provider profiles group (plan Task 4, ai-provider-profiles): the
    AI page leads with it, Custom is the fallback row for an empty or
    hand-corrupted store, the active pointer marks the radio, and the
    endpoint registry rows a switch writes stay wired like the other
    AI rows."""

    def test_ai_endpoint_keys_wired(self, settings_win) -> None:
        # the registry rows a provider-profile switch writes via
        # set-config must exist exactly like every other AI row
        rows = settings_win._rows
        wired = {("ai", "enabled"), ("ai", "base_url"), ("ai", "model"),
                 ("ai", "api_key_env"), ("ai", "temperature"),
                 ("ai", "timeout_seconds"), ("ai", "max_retries"),
                 ("ai", "refusal_guard")}
        missing = sorted(str(k) for k in wired if k not in rows)
        assert not missing, f"AI endpoint wiring lost: {missing}"

    def test_group_leads_the_ai_page(self, settings_win) -> None:
        page = settings_win._page_stack.get_child_by_name("ai")
        groups = [w for w in _walk(page)
                  if isinstance(w, Adw.PreferencesGroup)]
        assert groups and groups[0] is settings_win._prov_group
        assert settings_win._prov_group.get_title() == "Provider profiles"

    def test_empty_store_renders_custom_only(self, settings_win) -> None:
        # XDG-isolated conftest: no sidecar, pointer "" -> just Custom,
        # checked
        assert set(settings_win._prov_rows) == {""}
        assert settings_win._prov_rows[""].get_title() \
            == "Custom (hand-set endpoint)"
        assert settings_win._prov_checks[""].get_active() is True

    def test_corrupt_store_degrades_to_custom(self, settings_win,
                                              monkeypatch, tmp_path) -> None:
        # hand-edited JSON must not take the group down (store
        # discipline: {} with one warning, never a crash)
        from fluidvoice import paths
        monkeypatch.setattr(paths, "ai_profiles_file",
                            lambda: tmp_path / "ai-profiles.json")
        (tmp_path / "ai-profiles.json").write_text("{not json",
                                                   encoding="utf-8")
        settings_win._provider_profiles_refresh()
        assert set(settings_win._prov_rows) == {""}

    def test_stored_profiles_render_checked_by_pointer(
            self, settings_win, monkeypatch, tmp_path) -> None:
        from fluidvoice import paths
        from fluidvoice.ai import provider_profiles as pp
        p = tmp_path / "ai-profiles.json"
        pp.save_named("GLM", {"base_url": "https://api.z.ai/api/paas/v4",
                              "model": "glm-4.7", "api_key_env": "K"}, path=p)
        monkeypatch.setattr(paths, "ai_profiles_file", lambda: p)
        settings_win.cfg.setdefault("ai", {})["profile"] = "GLM"
        settings_win._provider_profiles_refresh()
        assert set(settings_win._prov_rows) == {"", "GLM"}
        assert settings_win._prov_rows["GLM"].get_title() == "GLM"
        assert settings_win._prov_checks["GLM"].get_active() is True
        assert settings_win._prov_checks[""].get_active() is False


class TestMainWindowSections:
    @pytest.fixture
    def win(self):
        from fluidvoice.gtkui.main_window import HistoryWindow
        w = HistoryWindow(client=StubClient())
        yield w
        w.destroy()

    def test_views_stack_has_three_pages(self, win) -> None:
        pages = [p.get_name() for p in win.view_stack.get_pages()]
        assert pages == ["transcripts", "commands", "stats"]

    def test_header_and_status_widgets_exist(self, win) -> None:
        for attr in ("mic_btn", "title_widget", "state_dot", "state_lbl",
                     "backend_lbl", "gpu_lbl", "model_lbl", "warmup_spinner",
                     "today_lbl", "update_lbl", "down_banner", "search",
                     "listbox", "cmd_listbox", "chart", "count_lbl",
                     "toast_overlay"):
            assert getattr(win, attr, None) is not None, attr

    def test_span_toggle_switches_chart_window(self, win) -> None:
        assert win._stats_span == 7
        btn = win._span_btns[30]
        btn.set_active(True)
        win._on_span(btn, 30)
        assert win._stats_span == 30

    def test_activity_chart_draws_without_crashing(self, win) -> None:
        import time as _time

        class FakeCairo:
            """Records the drawing calls — exercises the path logic
            without pycairo (the CI gtk env installs PyGObject against
            the system cairo but not the Python bindings, so a real
            Context cannot be built there)."""

            def __init__(self) -> None:
                self.calls: list[str] = []

            def __getattr__(self, name):
                def _record(*_a, **_kw):
                    self.calls.append(name)

                return _record

        by_day = {_time.strftime("%Y-%m-%d", _time.localtime(_time.time() - i * 86400)):
                 {"dictations": (i % 5) + 1} for i in range(30)}
        win._stats = {"by_day": by_day, "best_streak": 3}
        ctx = FakeCairo()
        for span in (7, 30):  # both code paths: recent-only and long tail
            win._stats_span = span
            win._draw_activity(win.chart, ctx, 320, 160)
        assert ctx.calls.count("fill") == 7 + 30  # one bar per day
        assert "stroke" in ctx.calls  # baseline hairline
        assert "show_text" in ctx.calls  # day labels + peak
        # and with no data at all: early return, no draws
        ctx.calls.clear()
        win._stats = None
        win._draw_activity(win.chart, ctx, 320, 160)
        assert ctx.calls == []
