"""Settings/history section builders — org plan 5.5 (D3).

The page builders are split into per-section methods
(_section_hotkeys … _build_views); these tests pin each section's
structure so a section edit that drops or rewires a registry key
fails here, and exercise the MainWindow stats paths the smoke tests
don't reach (span switch, activity-chart drawing).
"""

from __future__ import annotations

import os
import threading

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

from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from fluidvoice import paths  # noqa: E402
from tests.test_gtkui import StubClient  # noqa: E402


@pytest.fixture
def settings_win():
    from fluidvoice.gtkui.settings_window import SettingsWindow
    w = SettingsWindow(client=StubClient())
    yield w
    w.destroy()


def _walk(widget, kind) -> list:
    """Descendants of `widget` that are `kind`, in display order."""
    found = []

    def visit(w) -> None:
        child = w.get_first_child()
        while child is not None:
            if isinstance(child, kind):
                found.append(child)
            else:
                visit(child)
            child = child.get_next_sibling()

    visit(widget)
    return found


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
    """Settings → AI "Provider profiles" group (ai-provider-profiles plan,
    Task 4): it leads the page, renders Custom plus the stored names, and
    drives the registry-backed endpoint rows below it. Switch semantics
    beyond the section wiring live in
    tests/gtkui/test_ai_provider_profiles.py."""

    GLM = {"base_url": "https://api.z.ai/api/paas/v4",
           "model": "glm-4.7", "api_key_env": "ZAI_API_KEY"}

    @staticmethod
    def _ai_page(settings_win) -> Adw.PreferencesPage:
        return next(p for p in settings_win._iter_pages()
                    if p.get_name() == "ai")

    @staticmethod
    def _use_store(monkeypatch, tmp_path, profiles: dict) -> None:
        """Point the sidecar at tmp_path (the client resolves
        paths.ai_profiles_file() on every read)."""
        from fluidvoice.ai import provider_profiles as pp
        path = tmp_path / "ai-profiles.json"
        if profiles:
            pp.save_profiles(profiles, path=path)
        monkeypatch.setattr(paths, "ai_profiles_file", lambda: path)

    def test_group_leads_the_ai_page(self, settings_win, monkeypatch,
                                     tmp_path) -> None:
        self._use_store(monkeypatch, tmp_path, {})
        settings_win._provider_profiles_refresh()
        groups = _walk(self._ai_page(settings_win), Adw.PreferencesGroup)
        assert groups[0] is settings_win._prov_group
        assert [g.get_title() for g in groups[:2]] == \
            ["Provider profiles", "AI polish"]

    def test_endpoint_rows_wired_and_registry_backed(self, settings_win) -> None:
        from fluidvoice import config
        rows = settings_win._rows
        wired = {("ai", "base_url"), ("ai", "model"),
                 ("ai", "api_key_env")}
        missing = sorted(str(k) for k in wired if k not in rows)
        assert not missing, f"provider endpoint wiring lost: {missing}"
        # the rows and the profile pointer answer to one validator
        for key in ("base_url", "model", "api_key_env", "profile"):
            assert config.validate("ai", key, "GLM"), key
            assert not config.validate("ai", key, None), key

    def test_radios_render_custom_plus_the_store(self, settings_win,
                                                 monkeypatch, tmp_path) -> None:
        local = {"base_url": "http://localhost:1234/v1", "model": "m",
                 "api_key_env": ""}
        self._use_store(monkeypatch, tmp_path,
                        {"Local": local, "GLM": self.GLM})
        settings_win._provider_profiles_refresh()
        assert list(settings_win._prov_rows) == ["", "GLM", "Local"]
        assert settings_win._prov_rows[""].get_title() == \
            "Custom (hand-set endpoint)"

        def active() -> list[str]:
            return [n for n, c in settings_win._prov_checks.items()
                    if c.get_active()]

        # a pointer naming no stored profile (hand-edited JSON) is Custom
        settings_win.cfg["ai"]["profile"] = "Ghost"
        settings_win._provider_profiles_refresh()
        assert active() == [""]
        settings_win.cfg["ai"]["profile"] = "GLM"
        settings_win._provider_profiles_refresh()
        assert active() == ["GLM"]

    def test_selecting_a_stored_profile_sends_the_switch_patch(
            self, settings_win, monkeypatch, tmp_path) -> None:
        self._use_store(monkeypatch, tmp_path, {"GLM": self.GLM})
        settings_win._provider_profiles_refresh()
        settings_win._prov_checks["GLM"].set_active(True)
        assert settings_win.c.saved[-1] == {"ai": {**self.GLM, "profile": "GLM"}}
        assert settings_win.cfg["ai"]["profile"] == "GLM"
        # the endpoint rows follow the switch
        assert settings_win._rows[("ai", "base_url")].get_text() == \
            self.GLM["base_url"]

    def test_test_connection_reads_the_endpoint_rows(self, settings_win) -> None:
        calls: list[tuple] = []
        done = threading.Event()

        def fake_test_ai(url, model):
            calls.append((url, model))
            done.set()
            return {"ok": False, "error": "offline"}
        settings_win.c.test_ai = fake_test_ai
        settings_win._rows[("ai", "base_url")].set_text("http://127.0.0.1:11434/v1")
        settings_win._rows[("ai", "model")].set_text("qwen3:8b")
        polish = _walk(self._ai_page(settings_win), Adw.PreferencesGroup)[1]
        row = next(r for r in _walk(polish, Adw.ActionRow)
                   if r.get_title() == "Test connection")
        btn = next(b for b in _walk(row, Gtk.Button) if b.get_label() == "Test")
        btn.emit("clicked")
        assert done.wait(2.0), "Test never reached the client"
        assert calls == [("http://127.0.0.1:11434/v1", "qwen3:8b")]
        loop = GLib.MainLoop()  # let the worker's inline result land
        GLib.timeout_add(80, loop.quit)
        loop.run()
        assert settings_win.test_out.get_text() == "offline"


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


class TestCorrectionCrossLinks:
    """Survey 2026-11-23 (excellence bet 3): the four correction surfaces
    (history edits / dictionary suggestions / vocabulary hints / per-app
    prompt profiles) live on three different settings pages with no
    signposts. Each correction-adjacent group now carries a cross-link
    row whose chevron navigates to the sibling page — a wrong door has a
    pointer to the right one, in one click."""

    #: page name -> {link row title: target page name}
    EXPECTED = {
        "history": {"Suggested words": "dictation"},
        "dictation": {"Vocabulary boosting": "models",
                      "Prompt profiles": "ai"},
        "models": {"Custom dictionary": "dictation"},
        "ai": {"Shortcut prompt profiles": "dictation"},
    }

    def _page(self, settings_win, name):
        for page in settings_win._iter_pages():
            if page.get_name() == name:
                return page
        return None

    def _chevron(self, row) -> Gtk.Button:
        btns = _walk(row, Gtk.Button)
        assert btns, f"link row '{row.get_title()}' has no chevron button"
        return btns[0]

    def test_link_rows_on_the_right_pages(self, settings_win) -> None:
        for page_name, wanted in self.EXPECTED.items():
            page = self._page(settings_win, page_name)
            assert page is not None, page_name
            titles = {r.get_title() for r in _walk(page, Adw.ActionRow)}
            for title in wanted:
                assert title in titles, f"{page_name}: no '{title}' link row"

    def test_chevron_navigates_to_the_target_page(self, settings_win) -> None:
        for page_name, wanted in self.EXPECTED.items():
            page = self._page(settings_win, page_name)
            for row in _walk(page, Adw.ActionRow):
                target = wanted.get(row.get_title())
                if target is None:
                    continue
                self._chevron(row).click()
                visible = settings_win._page_stack.get_visible_child_name()
                assert visible == target, (
                    f"'{row.get_title()}' on {page_name} went to {visible}, "
                    f"wanted {target}")
                # back for the next iteration so failures report their link
                settings_win._show_page(page_name)

    def test_row_body_activation_navigates(self, settings_win) -> None:
        row = next(r for r in _walk(self._page(settings_win, "history"),
                                    Adw.ActionRow)
                   if r.get_title() == "Suggested words")
        row.emit("activate")
        assert settings_win._page_stack.get_visible_child_name() == "dictation"
