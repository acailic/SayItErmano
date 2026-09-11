"""Onboarding window first-use behavior (F-02/F-19) — offscreen GTK.

The engine row is honest about the model's real state, the tryout waits
out a cold download visibly and cancel-safely, and the guided
real-insertion step records self-reports. Skipped without GTK4/Adw or a
display, like test_gtkui.
"""
from __future__ import annotations

import os
import time

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

from fluidvoice import model_download  # noqa: E402
from fluidvoice.config import DEFAULTS  # noqa: E402
from fluidvoice.gtkui.client import Client  # noqa: E402
from fluidvoice.gtkui.onboarding_funnel import FunnelCounters  # noqa: E402

try:
    from gi.repository import Adw
    Adw.init()
except Exception:  # pragma: no cover
    pass


def pump(ms=150):
    loop = GLib.MainLoop()
    GLib.timeout_add(ms, loop.quit)
    loop.run()


def pump_until(cond, timeout_s=3.0):
    deadline = time.monotonic() + timeout_s
    while not cond() and time.monotonic() < deadline:
        pump(ms=20)
    return cond()


class TryClient(Client):
    """Deterministic client: canned tryout responses, no daemon."""

    def __init__(self, try_resp=None):
        super().__init__()
        self.try_resp = try_resp or {"ok": True, "text": "hello world",
                                     "duration_s": 3.1}
        self.try_calls: list[float] = []

    def masked_config(self):
        import copy
        return copy.deepcopy(DEFAULTS)

    def mics(self):
        return [{"description": "Test Mic", "default": True}]

    def daemon_alive(self):
        return True

    def status(self):
        return {"ok": True, "recording": False, "busy": False,
                "warmup": {"running": False, "error": None, "model": None}}

    def test_dictation(self, seconds=3.0):
        self.try_calls.append(seconds)
        return dict(self.try_resp)


def _ready(downloaded=False, progress=None, kind="faster-whisper",
           name="base"):
    return {"kind": kind, "name": name, "downloaded": downloaded,
            "progress": progress}


def _prog(done=50, total=100, label="base"):
    return model_download.DownloadProgress(
        label=label, kind="faster-whisper", done_bytes=done,
        total_bytes=total, elapsed_s=12.0)


@pytest.fixture()
def win(monkeypatch, tmp_path):
    """A window whose engine row never touches the real models cache and
    whose funnel counters land in a per-test file."""
    import fluidvoice.gtkui.onboarding as ob

    monkeypatch.setattr(model_download, "model_readiness",
                        lambda cfg: _ready(downloaded=True))
    monkeypatch.setattr(ob, "FunnelCounters",
                        lambda: FunnelCounters(path=tmp_path / "funnel.json"))
    w = ob.OnboardingWindow(client=TryClient())
    yield w
    w.close()


class TestEngineRowHonesty:
    """F-02: the row shows downloaded / downloading / missing - never a
    bare model NAME that reads as ready."""

    def test_missing_model_warns_with_size(self, win, monkeypatch):
        monkeypatch.setattr(model_download, "model_readiness",
                            lambda cfg: _ready(downloaded=False))
        win._refresh_engine_row()
        assert "not downloaded yet" in win.model_lbl.get_text()
        assert "~" in win.model_lbl.get_text()  # the size blurb
        assert win.model_row.status.get_icon_name() == \
            "dialog-warning-symbolic"

    def test_downloading_shows_live_progress(self, win, monkeypatch):
        monkeypatch.setattr(model_download, "model_readiness",
                            lambda cfg: _ready(progress=_prog(37, 100)))
        win._refresh_engine_row()
        text = win.model_lbl.get_text()
        assert "downloading base" in text and "37%" in text
        assert win.model_row.status.get_icon_name() == \
            "emblem-synchronizing-symbolic"

    def test_download_finish_records_funnel_step(self, monkeypatch, tmp_path):
        import fluidvoice.gtkui.onboarding as ob
        state = {"r": _ready(progress=_prog(90, 100))}
        monkeypatch.setattr(model_download, "model_readiness",
                            lambda cfg: state["r"])
        monkeypatch.setattr(ob, "FunnelCounters",
                            lambda: FunnelCounters(path=tmp_path / "f.json"))
        w = ob.OnboardingWindow(client=TryClient())
        try:
            assert "model_downloaded" not in w.funnel.steps()
            state["r"] = _ready(downloaded=True)  # the download finished
            w._first_use_tick()
            assert w.funnel.steps().get("model_downloaded", {}) \
                .get("detail", {}).get("model") == "base"
        finally:
            w.close()

    def test_already_downloaded_at_open_records_once(self, win):
        # the fixture window opened with a ready model: the step counts
        assert "model_downloaded" in win.funnel.steps()

    def test_readiness_crash_degrades_to_warning(self, win, monkeypatch):
        def boom(cfg):
            raise RuntimeError("probe failed")

        monkeypatch.setattr(model_download, "model_readiness", boom)
        win._refresh_engine_row()
        assert "unknown" in win.model_lbl.get_text()
        assert win.model_row.status.get_icon_name() == \
            "dialog-warning-symbolic"


class TestTryoutWaitsOutColdModel:
    """F-02: the tryout never shows a cryptic hang/timeout during the
    first-use download - it waits visibly and cancels cleanly."""

    def test_cold_download_waits_without_calling_daemon(self, win,
                                                        monkeypatch):
        monkeypatch.setattr(model_download, "model_readiness",
                            lambda cfg: _ready(progress=_prog(40, 100)))
        win._try_dictation()
        assert win._try_state == "waiting"
        assert win.c.try_calls == []  # no socket call while downloading
        assert "downloading base" in win.try_out.get_text()
        assert win.try_cancel_btn.get_visible() is True
        assert win.try_btn.get_sensitive() is False

    def test_cancel_restores_clean_state(self, win, monkeypatch):
        monkeypatch.setattr(model_download, "model_readiness",
                            lambda cfg: _ready(progress=_prog(40, 100)))
        win._try_dictation()
        win._try_cancel_clicked()
        assert win._try_state == "idle"
        assert win.try_btn.get_sensitive() is True
        assert win.try_cancel_btn.get_visible() is False
        assert "nothing was half-written" in win.try_out.get_text()

    def test_timeout_during_download_returns_to_waiting(self, win,
                                                        monkeypatch):
        monkeypatch.setattr(model_download, "model_readiness",
                            lambda cfg: _ready(progress=_prog(60, 100)))
        win._try_state = "busy"
        win._on_tryout_result({"ok": False, "error": "timed out"},
                              win._try_gen)
        assert win._try_state == "waiting"
        assert "downloading base" in win.try_out.get_text()
        assert win.c.try_calls == []

    def test_late_response_after_cancel_is_dropped(self, win, monkeypatch):
        monkeypatch.setattr(model_download, "model_readiness",
                            lambda cfg: _ready(downloaded=True))
        stale_gen = win._try_gen
        win._try_cancel_clicked()
        win._on_tryout_result({"ok": True, "text": "late"}, stale_gen)
        assert "late" not in win.try_out.get_text()
        assert win.final_box.get_visible() is False

    def test_failure_with_no_model_shows_actionable_hint(self, win,
                                                         monkeypatch):
        monkeypatch.setattr(model_download, "model_readiness",
                            lambda cfg: _ready(downloaded=False))
        win._try_state = "busy"
        win._on_tryout_result(
            {"ok": False, "error": "no speech backend available"},
            win._try_gen)
        assert "failed" in win.try_out.get_text()
        assert "not on disk yet" in win.try_out.get_text()
        assert "doctor" in win.try_out.get_text()
        assert win.try_btn.get_sensitive() is True


class TestTryoutSuccess:
    def test_success_reveals_final_step_and_records(self, win, monkeypatch):
        c = win.c
        c.try_resp = {"ok": True, "text": "hello world", "duration_s": 3.1}
        win._try_dictation()
        assert pump_until(lambda: win._try_state == "idle")
        assert c.try_calls == [3.0]
        assert "hello world" in win.try_out.get_text()
        assert win.final_box.get_visible() is True
        assert "tryout_ok" in win.funnel.steps()
        assert win.try_btn.get_sensitive() is True


class TestGuidedRealInsertion:
    """F-19: after the tryout, one guided real dictation with self-report
    buttons; failure routes to the doctor-style hints."""

    def _reveal(self, win):
        win._show_tryout({"ok": True, "text": "hi", "duration_s": 3.0})
        assert win.final_box.get_visible() is True

    def test_final_text_explains_the_hotkey(self, win, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        self._reveal(win)
        win._final_step_text()  # re-render under the probed session
        text = win.final_lbl.get_text()
        assert "Right_Control" in text  # the configured dictate key
        assert "any app" in text

    def test_final_text_wayland_names_the_toggle_script(self, win,
                                                        monkeypatch):
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.delenv("DISPLAY", raising=False)
        self._reveal(win)
        win._final_step_text()
        text = win.final_lbl.get_text()
        assert "toggle" in text  # DE-bound script / CLI, not the X11 grab

    def test_ok_report_records_and_congratulates(self, win):
        self._reveal(win)
        win._report_ok()
        steps = win.funnel.steps()
        assert "real_insertion_reported_ok" in steps
        assert "real_insertion_reported_failed" not in steps
        assert "whole loop" in win.final_out.get_text()

    def test_fail_report_shows_hints_and_records(self, win, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        monkeypatch.setenv("DISPLAY", ":0")
        self._reveal(win)
        win._report_fail()
        assert "real_insertion_reported_failed" in win.funnel.steps()
        text = win.final_out.get_text()
        assert "fix" in text
        assert "sayit-ermano doctor" in text

    def test_funnel_optout_checkbox_stops_recording(self, win):
        assert "onboarding_started" in win.funnel.steps()
        win.funnel_check.set_active(False)
        assert win.funnel.opted_out() is True
        assert win.funnel.record("tryout_ok") is False
        assert "tryout_ok" not in win.funnel.steps()
