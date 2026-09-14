"""Widget proxy marshaling + page action paths — display tier.

Covers the gtkui gaps the smoke tests leave dark (org plan follow-up
to 5.5): the settings_pages/common.py value proxies
(_ExtraShortcutsProxy, _ActionTriggersProxy), the History page's
clear-all confirmation flow, and the Wayland page's copy/open actions.
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

from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from test_gtkui import StubClient  # noqa: E402  (tests/ on sys.path)

import fluidvoice.gtkui.settings_pages.wayland as wl  # noqa: E402
from fluidvoice.gtkui.settings_pages.common import (  # noqa: E402
    _ActionTriggersProxy,
    _ExtraShortcutsProxy,
)


def _combo_pair(names: list[str]) -> tuple:
    key_row = Adw.EntryRow(title="k")
    combo = Adw.ComboRow(title="p")
    combo.set_model(Gtk.StringList(strings=names))
    return key_row, combo


class TestExtraShortcutsProxy:
    def test_roundtrip_two_entries(self) -> None:
        pairs = [_combo_pair(["(none)", "Work", "Casual"]) for _ in range(2)]
        p = _ExtraShortcutsProxy(pairs)
        p.set_value([
            {"key": "F9", "profile": "Work"},
            {"key": "<ctrl>F2"},  # no profile -> (none)
        ])
        assert p.get_value() == [{"key": "F9", "profile": "Work"},
                                 {"key": "<ctrl>F2"}]

    def test_empty_and_partial_values(self) -> None:
        pairs = [_combo_pair(["(none)", "Work"]) for _ in range(2)]
        p = _ExtraShortcutsProxy(pairs)
        p.set_value([])  # nothing saved
        assert p.get_value() == []
        p.set_value([{"key": "F9", "profile": "nonexistent"}])  # unknown name
        assert p.get_value() == [{"key": "F9"}]
        # blank keys are dropped entirely
        p.set_value([{}, {"key": " Pause "}])
        assert p.get_value() == [{"key": "Pause"}]

    def test_extra_entries_beyond_two_are_dropped(self) -> None:
        pairs = [_combo_pair(["(none)"]) for _ in range(2)]
        p = _ExtraShortcutsProxy(pairs)
        p.set_value([{"key": "a"}, {"key": "b"}, {"key": "c"}])
        assert p.get_value() == [{"key": "a"}, {"key": "b"}]


class TestActionTriggersProxy:
    def _proxy(self) -> _ActionTriggersProxy:
        rows = {a: Adw.EntryRow(title=a) for a in
                ("new_line", "new_paragraph", "tab", "space")}
        return _ActionTriggersProxy(rows)

    def test_roundtrip(self) -> None:
        p = self._proxy()
        p.set_value({"new_line": ["nova vrstica", "naslednja"],
                     "tab": ["zamik"]})
        assert p.get_value() == {"new_line": ["nova vrstica", "naslednja"],
                                 "tab": ["zamik"]}

    def test_none_value_and_alias_cleanup(self) -> None:
        p = self._proxy()
        p.set_value(None)
        assert p.get_value() == {}
        p.set_value({"space": ["presledek", ""]})
        assert p.get_value() == {"space": ["presledek"]}
        # sloppy commas never produce empty aliases
        for row in p.rows.values():
            row.set_text(" a ,, b ,")
        assert p.get_value() == {
            "new_line": ["a", "b"], "new_paragraph": ["a", "b"],
            "tab": ["a", "b"], "space": ["a", "b"]}


@pytest.fixture
def settings_win():
    from fluidvoice.gtkui.settings_window import SettingsWindow
    w = SettingsWindow(client=StubClient())
    yield w
    w.destroy()


class TestHistoryPageClear:
    def _open_and_find_dialog(self, settings_win, monkeypatch):
        created = []
        orig = Adw.MessageDialog

        class Capturing(orig):
            def __init__(self, **kw):
                super().__init__(**kw)
                created.append(self)

        import fluidvoice.gtkui.settings_pages.history as hp
        monkeypatch.setattr(hp.Adw, "MessageDialog", Capturing)
        return created

    def test_clear_confirmed_calls_client_and_toasts(self, settings_win,
                                                     monkeypatch) -> None:
        created = self._open_and_find_dialog(settings_win, monkeypatch)
        settings_win.c.history_clear = lambda: 7
        toasts: list[str] = []
        monkeypatch.setattr(settings_win, "toast", toasts.append)
        settings_win._confirm_clear_history(None)
        created[0].emit("response", "clear")
        assert toasts == ["Removed 7 entries"]

    def test_clear_cancelled_touches_nothing(self, settings_win,
                                             monkeypatch) -> None:
        created = self._open_and_find_dialog(settings_win, monkeypatch)
        cleared: list[bool] = []

        def _boom() -> None:  # pragma: no cover - must not run
            cleared.append(True)

        settings_win.c.history_clear = _boom
        monkeypatch.setattr(settings_win, "toast", lambda *_: None)
        settings_win._confirm_clear_history(None)
        created[0].emit("response", "cancel")
        assert cleared == []


class TestWaylandPageActions:
    def test_copy_toggle_script_puts_subtitle_on_clipboard(self, settings_win,
                                                           monkeypatch) -> None:
        class FakeClipboard:
            def __init__(self) -> None:
                self.text: str | None = None

            def set_text(self, text: str) -> None:
                self.text = text

        fake = FakeClipboard()
        fake_display = type("D", (), {
            "get_clipboard": staticmethod(lambda *_: fake)})
        fake_display.get_default = staticmethod(lambda: fake_display)
        monkeypatch.setattr(wl.Gdk, "Display", fake_display)
        settings_win.wayland_script_row.set_subtitle("flatpak run org.app")
        toasts: list[str] = []
        monkeypatch.setattr(settings_win, "toast", toasts.append)
        settings_win._copy_toggle_script()
        assert fake.text == "flatpak run org.app"
        assert toasts == ["Copied the command to bind"]

    def test_open_de_panel_gnome(self, settings_win, monkeypatch) -> None:
        opened: list[list[str]] = []
        monkeypatch.setattr("subprocess.Popen",
                            lambda cmd, **kw: opened.append(cmd))
        toasts: list[str] = []
        monkeypatch.setattr(settings_win, "toast", toasts.append)
        monkeypatch.setattr("fluidvoice.session.probe",
                            lambda: type("S", (), {
                                "desktop_all": "gnome:x11"})())
        monkeypatch.setattr("fluidvoice.session.is_gnome_desktop",
                            lambda d: True)
        settings_win._open_de_shortcut_settings()
        assert opened and opened[0][0] == "gnome-control-center"
        assert toasts == ["Opening shortcut settings"]
