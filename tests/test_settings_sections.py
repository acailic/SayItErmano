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
