"""Local polish UI (Settings → AI + Settings → Models): the toggle
group, the chat-model one-click download & use, and the server status
row — stub-client offscreen windows (tests/gtkui pattern; skips
headless).
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

from fluidvoice import model_catalog  # noqa: E402
from fluidvoice.config import DEFAULTS  # noqa: E402
from fluidvoice.gtkui.client import Client  # noqa: E402


class _LocalAIStubClient(Client):
    """Client surface scoped to local polish: config, set-config, the
    local-ai routes, and a scriptable daemon status."""

    def __init__(self, cfg_overrides=None, local_ai=None):
        super().__init__()
        self.saved: list[dict] = []
        self.overrides = cfg_overrides or {}
        self.local_ai_started = 0
        self.local_ai_stopped = 0
        self._local_ai = local_ai

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

    def status(self):
        st = {"ok": True, "recording": False, "busy": False,
              "warmup": {"running": False, "error": None}}
        if self._local_ai is not None:
            st["local_ai"] = dict(self._local_ai)
        return st

    def mics(self):
        return []

    # -- local-ai routes (mirrors Client.local_ai_start/stop) -----------------

    def local_ai_start(self):
        self.local_ai_started += 1
        if self._local_ai is None:
            return {"ok": False, "error": "model not downloaded",
                    "status": {}}
        self._local_ai.update({"running": True, "toggle": True,
                               "enabled": True})
        return {"ok": True,
                "base_url": "http://127.0.0.1:8931/v1",
                "model": self.overrides.get("ai", {}).get(
                    "local_model", model_catalog.CHAT_DEFAULT_MODEL),
                "status": dict(self._local_ai)}

    def local_ai_stop(self):
        self.local_ai_stopped += 1
        if self._local_ai is not None:
            self._local_ai.update({"running": False})
        return {"ok": True, "status": dict(self._local_ai or {})}


def pump(loop, ms=60):
    GLib.timeout_add(ms, loop.quit)
    loop.run()


@pytest.fixture()
def loop():
    return GLib.MainLoop()


def make_window(client):
    from fluidvoice.gtkui.settings_window import SettingsWindow
    w = SettingsWindow(client=client)
    w.present()
    return w


class TestLocalPolishGroup:
    def test_rows_exist_and_collect(self, loop, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        c = _LocalAIStubClient()
        w = make_window(c)
        pump(loop)
        assert ("ai", "local_polish") in w._rows
        assert ("ai", "local_model") in w._rows
        # toggle on + a different idle → collected into the save body
        w._rows[("ai", "local_polish")].set_active(True)
        w._local_idle_row.set_value(2)
        body = w._collect()
        assert body["ai"]["local_polish"] is True
        assert body["ai"]["local_idle_stop_s"] == 120
        assert body["ai"]["local_model"] == \
            model_catalog.CHAT_DEFAULT_MODEL
        w.close()

    def test_status_row_running(self, loop, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        c = _LocalAIStubClient(
            local_ai={"toggle": True, "running": True, "port": 8931,
                      "model_downloaded": True,
                      "runtime_downloaded": True, "rss_kb": 4_800_000})
        w = make_window(c)
        pump(loop)
        w._local_ai_update_status_row()
        sub = w._local_status_row.get_subtitle()
        assert "running" in sub and "8931" in sub
        w.close()

    def test_status_row_off_zero_footprint(self, loop, monkeypatch,
                                           tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        c = _LocalAIStubClient()
        w = make_window(c)
        pump(loop)
        w._local_ai_update_status_row()
        assert "zero footprint" in w._local_status_row.get_subtitle()
        w.close()

    def test_start_button_roundtrip(self, loop, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        c = _LocalAIStubClient(local_ai={"toggle": True,
                                         "running": False})
        w = make_window(c)
        pump(loop)
        w._local_ai_start(None)
        assert pump_until(loop, lambda: c.local_ai_started == 1)
        w._local_ai_stop(None)
        assert pump_until(loop, lambda: c.local_ai_stopped == 1)
        w.close()


def pump_until(loop, cond, timeout_s=2.0):
    import time
    deadline = time.monotonic() + timeout_s
    while not cond() and time.monotonic() < deadline:
        pump(loop, ms=20)
    return cond()


class TestModelsChatGroup:
    def test_download_and_use_one_click(self, loop, monkeypatch, tmp_path):
        """Download & use = download_chat_stack + set-config turning
        local polish (and the master switch) on — one click, no more."""
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        c = _LocalAIStubClient()
        w = make_window(c)
        pump(loop)
        assert not model_catalog.chat_model_downloaded(
            model_catalog.CHAT_DEFAULT_MODEL)

        downloaded = []

        def fake_stack(name, progress=None, cancel=None):
            downloaded.append(name)
            p = model_catalog.chat_model_path(name)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"gguf")
            return p

        monkeypatch.setattr(
            "fluidvoice.gtkui.settings_pages.models.model_download."
            "download_chat_stack", fake_stack)
        w._download_chat(None, model_catalog.CHAT_DEFAULT_MODEL)
        assert pump_until(
            loop, lambda: w._chat_dl[
                model_catalog.CHAT_DEFAULT_MODEL].get("done"))
        assert pump_until(loop, lambda: c.saved and
                          any(b.get("ai", {}).get("local_polish") is True
                              for b in c.saved))
        body = next(b for b in c.saved
                    if b.get("ai", {}).get("local_polish") is True)
        assert body["ai"]["enabled"] is True  # master switch flipped too
        assert body["ai"]["local_model"] == model_catalog.CHAT_DEFAULT_MODEL
        w.close()

    def test_use_button_enables_without_download(self, loop, monkeypatch,
                                                 tmp_path):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        p = model_catalog.chat_model_path(
            model_catalog.CHAT_DEFAULT_MODEL)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"gguf")
        c = _LocalAIStubClient()
        w = make_window(c)
        pump(loop)
        w._use_chat(None, model_catalog.CHAT_DEFAULT_MODEL)
        assert pump_until(loop, lambda: c.saved)
        body = next(b for b in c.saved if "ai" in b)
        assert body["ai"]["local_polish"] is True
        w.close()
