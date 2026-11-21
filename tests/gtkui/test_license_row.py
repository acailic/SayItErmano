"""Settings → About → License row (issue #16 UI slice): renders the
offline license state, refreshes without a restart. Follows the
tests/gtkui stub-client pattern (needs_display lane, skips headless).
"""
from __future__ import annotations

import copy
import datetime as dt
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

from fluidvoice.config import DEFAULTS  # noqa: E402
from fluidvoice.gtkui.client import Client  # noqa: E402
from fluidvoice.license import LicenseInfo  # noqa: E402


class _StubClient(Client):
    """Config surface only — the About page needs nothing else."""

    def __init__(self):
        super().__init__()
        self.saved: list[dict] = []

    def get_config(self):
        return copy.deepcopy(DEFAULTS), True

    def set_config(self, body):
        self.saved.append(copy.deepcopy(body))
        return {"ok": True, "changed": [f"{s}.{k}" for s, ks in body.items() for k in ks],
                "rejected": [], "restart_required": [], "errors": [], "note": ""}


@pytest.fixture()
def loop():
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    while ctx.pending():
        ctx.iteration(False)
    yield ctx
    while ctx.pending():
        ctx.iteration(False)


def _pump(loop, ms=150):
    from gi.repository import GLib
    deadline = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < deadline:
        loop.iteration(True)


def _window(loop):
    from fluidvoice.gtkui.settings_window import SettingsWindow
    w = SettingsWindow(client=_StubClient())
    w.present()
    _pump(loop)
    return w


def test_row_exists_and_shows_honest_none(loop, monkeypatch):
    from fluidvoice import license as license_mod
    monkeypatch.setattr(license_mod, "status", lambda: None)
    w = _window(loop)
    assert w.about_license_row.get_title() == "License"
    assert "every feature works without one" in w.about_license_row.get_subtitle()
    w.close()


def test_row_renders_imported_license(loop, monkeypatch):
    from fluidvoice import license as license_mod
    info = LicenseInfo(email="buyer@example.com",
                       purchased=dt.date(2026, 11, 21), max_version="1", key_id=0)
    monkeypatch.setattr(license_mod, "status", lambda: info)
    w = _window(loop)
    sub = w.about_license_row.get_subtitle()
    assert "buyer@example.com" in sub
    assert "major 1 and below" in sub
    w.close()


def test_refresh_updates_without_restart(loop, monkeypatch):
    from fluidvoice import license as license_mod
    monkeypatch.setattr(license_mod, "status", lambda: None)
    w = _window(loop)
    assert "without one" in w.about_license_row.get_subtitle()
    info = LicenseInfo(email="late@import.dev",
                       purchased=dt.date(2026, 11, 21), max_version="forever", key_id=0)
    monkeypatch.setattr(license_mod, "status", lambda: info)
    w.refresh_license_row()
    sub = w.about_license_row.get_subtitle()
    assert "late@import.dev" in sub
    assert "all releases" in sub
    w.close()
