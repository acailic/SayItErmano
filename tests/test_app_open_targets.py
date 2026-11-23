"""`app --open <target>` deep links (correction discoverability, fix 1).

The tray's "Dictionary suggestions (N)…" item lands on the Dictation page,
so the target vocabulary is shared by three GTK-free call sites - the
daemon (`_open_settings`), the CLI parser and the app shell - and must
agree. The display-tier half (every sidebar page is a reachable target,
`open_page` really switches) lives in tests/test_settings_sections.py.
"""

from __future__ import annotations

import argparse

from fluidvoice.gtkui.application import (
    OPEN_TARGETS,
    SETTINGS_PAGE_TARGETS,
    normalize_open_target,
)


class TestNormalizeOpenTarget:
    def test_windows_pass_through(self):
        assert normalize_open_target("history") == "history"
        assert normalize_open_target("settings") == "settings"

    def test_settings_page_opens_settings_on_it(self):
        for page in SETTINGS_PAGE_TARGETS:
            assert normalize_open_target(page) == page

    def test_unknown_or_empty_opens_history(self):
        assert normalize_open_target(None) == "history"
        assert normalize_open_target("") == "history"
        assert normalize_open_target("nonsense") == "history"

    def test_slashes_case_and_spaces_are_tolerated(self):
        # the tray passes "/history"-style names; humans type on the CLI
        assert normalize_open_target("/Dictation ") == "dictation"
        assert normalize_open_target("/history") == "history"

    def test_target_vocabulary(self):
        assert OPEN_TARGETS[:2] == ("history", "settings")
        assert OPEN_TARGETS[2:] == SETTINGS_PAGE_TARGETS
        assert set(SETTINGS_PAGE_TARGETS) >= {
            "general", "dictation", "models", "ai", "wayland", "about"}
        # the Settings page named "history" is shadowed by the window
        assert OPEN_TARGETS.count("history") == 1


class TestCliParser:
    def test_open_choices_match_the_app(self):
        from fluidvoice.cli import _build_parser
        parser = _build_parser()
        sub = next(a for a in parser._actions
                   if isinstance(a, argparse._SubParsersAction))
        opts = {a.dest: a for a in sub.choices["app"]._actions}
        assert opts["open"].choices == list(OPEN_TARGETS)
        assert opts["open"].default == "history"
