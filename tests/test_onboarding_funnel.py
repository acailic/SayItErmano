"""First-use funnel counters + failure hints (F-19) — headless, no GTK.

Local-only step counters: tests assert the JSON FILE contents (the
opt-out story and the step set are the contract). The hints tests pin
the per-cause actionable lines the onboarding "It didn't work" report
shows.
"""
from __future__ import annotations

import json

import pytest

from fluidvoice.gtkui.onboarding_funnel import STEPS, FunnelCounters
from fluidvoice.gtkui.onboarding_hints import insertion_failure_hints


@pytest.fixture()
def funnel(tmp_path):
    return FunnelCounters(path=tmp_path / "onboarding_funnel.json")


class TestFunnelCounters:
    def test_records_every_step_into_the_file(self, funnel):
        assert funnel.record("onboarding_started") is True
        assert funnel.record("model_downloaded", model="base",
                             kind="faster-whisper") is True
        assert funnel.record("tryout_ok") is True
        assert funnel.record("real_insertion_reported_ok") is True
        # the file on disk carries exactly the recorded steps + detail
        data = json.loads(funnel.path.read_text())
        assert set(k for k in data if k in STEPS) == {
            "onboarding_started", "model_downloaded", "tryout_ok",
            "real_insertion_reported_ok"}
        assert data["model_downloaded"]["detail"] == {
            "model": "base", "kind": "faster-whisper"}
        assert data["onboarding_started"]["ts"] > 0

    def test_failed_report_is_a_distinct_step(self, funnel):
        funnel.record("real_insertion_reported_failed")
        data = json.loads(funnel.path.read_text())
        assert "real_insertion_reported_failed" in data
        assert "real_insertion_reported_ok" not in data

    def test_first_timestamp_wins(self, funnel):
        funnel.record("tryout_ok")
        first = json.loads(funnel.path.read_text())["tryout_ok"]["ts"]
        funnel.record("tryout_ok")  # a repeated tryout never rewrites it
        again = json.loads(funnel.path.read_text())["tryout_ok"]["ts"]
        assert first == again

    def test_unknown_step_rejected(self, funnel):
        assert funnel.record("exited_funnel") is False
        assert funnel.record("clicked_button") is False
        assert funnel.path.exists() is False  # nothing written at all

    def test_opt_out_via_env_writes_nothing(self, funnel, monkeypatch):
        monkeypatch.setenv("SAYITERMANO_NO_FUNNEL", "1")
        assert funnel.opted_out() is True
        assert funnel.record("onboarding_started") is False
        assert funnel.path.exists() is False

    def test_opt_out_via_flag_persists_and_keeps_history(self, funnel):
        funnel.record("onboarding_started")
        funnel.opt_out()
        assert funnel.opted_out() is True
        assert funnel.record("tryout_ok") is False
        data = json.loads(funnel.path.read_text())
        assert data["opt_out"] is True
        assert "onboarding_started" in data  # kept, verifiable, deletable
        assert "tryout_ok" not in data

    def test_default_path_is_in_the_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        f = FunnelCounters()
        assert f.path == tmp_path / "sayit-ermano" / "onboarding_funnel.json"

    def test_corrupt_file_is_tolerated(self, funnel):
        funnel.path.parent.mkdir(parents=True, exist_ok=True)
        funnel.path.write_text("{not json")
        assert funnel.record("onboarding_started") is True
        assert funnel.steps() == {"onboarding_started":
                                  json.loads(funnel.path.read_text())
                                  ["onboarding_started"]}


def _cfg(**model):
    from fluidvoice.config import DEFAULTS
    cfg = json.loads(json.dumps(DEFAULTS))  # plain deep copy, no GTK
    cfg["model"].update(model)
    return cfg


class TestInsertionFailureHints:
    """Doctor-style per-cause lines for the onboarding self-report."""

    def _env(self, monkeypatch, session_type, desktop=""):
        monkeypatch.setenv("XDG_SESSION_TYPE", session_type)
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
        monkeypatch.setenv("DISPLAY", ":0" if session_type == "x11" else "")
        monkeypatch.setenv("WAYLAND_DISPLAY",
                           "wayland-0" if session_type == "wayland" else "")

    def test_daemon_down_first_hint(self, monkeypatch):
        self._env(monkeypatch, "x11")
        hints = insertion_failure_hints(None, _cfg())
        assert any("daemon is not answering" in h for h in hints)

    def test_x11_missing_typing_tools_name_install_commands(
            self, monkeypatch):
        self._env(monkeypatch, "x11")
        missing = {"xdotool", "xclip"}
        which = lambda t: None if t in missing else f"/usr/bin/{t}"
        hints = insertion_failure_hints({"ok": True}, _cfg(), which=which)
        assert any("sudo apt install xdotool" in h for h in hints)
        assert any("sudo apt install xclip" in h for h in hints)
        assert any("History" in h for h in hints)  # the recovery path
        assert hints[-1] == "full report: sayit-ermano doctor"

    def test_x11_hotkey_blocked_hint(self, monkeypatch):
        self._env(monkeypatch, "x11")
        hints = insertion_failure_hints(
            {"ok": True, "hotkey_grabbed": False}, _cfg(),
            which=lambda t: f"/usr/bin/{t}")
        assert any("held by another app" in h for h in hints)

    def test_wayland_unavailable_insertion_names_the_tools(
            self, monkeypatch):
        self._env(monkeypatch, "wayland", "ubuntu:GNOME")
        hints = insertion_failure_hints({"ok": True}, _cfg(),
                                        which=lambda t: None)
        assert any("no typing tool" in h for h in hints)
        assert any("ydotool" in h for h in hints)

    def test_wayland_includes_bind_steps_and_wl_copy_only(
            self, monkeypatch):
        self._env(monkeypatch, "wayland", "ubuntu:GNOME")
        have = {"wl-copy", "wl-paste"}
        which = lambda t: f"/usr/bin/{t}" if t in have else None
        hints = insertion_failure_hints({"ok": True}, _cfg(), which=which)
        assert any("wl-clipboard-only" in h or "paste manually" in h
                   for h in hints)
        assert any("Custom Shortcuts" in h for h in hints)  # doctor's steps
        assert any("sayit-ermano-toggle" in h for h in hints)

    def test_cold_model_hint_before_any_download_finishes(self, monkeypatch):
        self._env(monkeypatch, "x11")
        hints = insertion_failure_hints({"ok": True}, _cfg(name="base"),
                                        which=lambda t: f"/usr/bin/{t}")
        assert any("not on disk yet" in h for h in hints)

    def test_no_recorder_named(self, monkeypatch):
        self._env(monkeypatch, "x11")
        missing = {"pw-record", "parecord"}
        which = lambda t: None if t in missing else f"/usr/bin/{t}"
        hints = insertion_failure_hints({"ok": True}, _cfg(), which=which)
        assert any("pipewire-audio-utils" in h for h in hints)
