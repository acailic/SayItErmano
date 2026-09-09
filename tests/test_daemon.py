"""Daemon state machine + DictationPipeline, tested with stubs (no X11/audio/GPU)."""
from __future__ import annotations

import copy
import json
import math
import struct
import threading
import time
import wave
from pathlib import Path

import pytest

from fluidvoice import daemon as dm
from fluidvoice.ai.client import AIError
from fluidvoice.config import DEFAULTS
from fluidvoice.insertion import InsertError
from fluidvoice.recorder import RecorderError


def make_wav(path: Path, seconds: float = 1.0, loud: bool = True, rate: int = 16000) -> Path:
    n = int(rate * seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            v = int(12000 * math.sin(2 * math.pi * 440 * i / rate)) if loud else 0
            frames += struct.pack("<h", v)
        wf.writeframes(bytes(frames))
    # pad to >200 bytes so the daemon treats it as real audio
    if path.stat().st_size < 200:
        with open(path, "ab") as fh:
            fh.write(b"\0" * 300)
    return path


class StubRecorder:
    def __init__(self, fail_start=False):
        self.fail_start = fail_start
        self.path: Path | None = None
        self.started = 0
        self.stopped = 0
        self.cancelled = 0

    def start(self, path):
        if self.fail_start:
            raise RecorderError("no recorder found")
        self.path = make_wav(path)
        self.started += 1

    def stop(self):
        self.stopped += 1
        return self.path

    def cancel(self):
        self.cancelled += 1
        self.stopped += 1
        self.path = None

    def elapsed(self):
        return 0.0


class StubBackend:
    name = "stub"

    def __init__(self, text="hello world", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list = []

    def transcribe(self, wav, language=None):
        self.calls.append((str(wav), language))
        if self.error:
            raise self.error
        return {"text": self.text, "language": "en", "duration": 1.0}


@pytest.fixture()
def cfg():
    return copy.deepcopy(DEFAULTS)


@pytest.fixture()
def quiet_ui(tmp_path, monkeypatch):
    """Silence real notifications/sounds, isolate history, and record them."""
    calls = {"notify": [], "sound": []}

    def fake_notify(title, body="", timeout_ms=2500, enabled=True):
        if enabled:
            calls["notify"].append((title, body))

    def fake_sound(which, volume=1.0, enabled=True):
        if enabled:
            calls["sound"].append(which)

    monkeypatch.setattr(dm.ui, "notify", fake_notify)
    monkeypatch.setattr(dm.ui, "play_sound", fake_sound)
    monkeypatch.setattr(dm.insertion, "active_window_class", lambda: "TestApp")
    monkeypatch.setattr(dm.history_mod.paths, "history_file",
                        lambda: tmp_path / "test-history.jsonl")
    return calls


# ---------------------------------------------------------------------------
# DictationPipeline
# ---------------------------------------------------------------------------

class TestPipeline:
    def _run(self, tmp_path, cfg, backend, **kw):
        wav = make_wav(tmp_path / "utt.wav")
        pipe = dm.DictationPipeline(cfg, backend, **kw)
        return pipe, pipe.run(wav, "TestApp")

    def test_happy_path(self, tmp_path, cfg, quiet_ui):
        inserted, history = [], []
        pipe, out = self._run(
            tmp_path, cfg, StubBackend("um hello literal comma world"),
            inserter=lambda text, c: (inserted.append(text), "typed")[1],
            history_writer=lambda entry, wav: history.append(entry))
        assert out["strategy"] == "typed"
        assert inserted == ["hello, world"]  # filler removed + punctuation applied
        assert out["ai"] is False
        assert history and history[0]["app"] == "TestApp"
        assert history[0]["backend"] == "stub"
        assert not (tmp_path / "utt.wav").exists()  # temp cleaned up

    def test_empty_transcription(self, tmp_path, cfg, quiet_ui):
        inserted = []
        _, out = self._run(tmp_path, cfg, StubBackend("  "),
                           inserter=lambda t, c: inserted.append(t))
        assert out is None and inserted == []

    def test_backend_error_notifies(self, tmp_path, cfg, quiet_ui):
        _, out = self._run(tmp_path, cfg, StubBackend(error=RuntimeError("cuda blew up")),
                           inserter=lambda t, c: "typed")
        assert out is None
        assert any("Transcription failed" in (t + b) for t, b in quiet_ui["notify"])

    def test_silence_gate(self, tmp_path, cfg, quiet_ui):
        cfg["recording"]["skip_silent"] = True
        inserted = []
        wav = make_wav(tmp_path / "silence.wav", seconds=2.0, loud=False)
        pipe = dm.DictationPipeline(cfg, StubBackend("should not run"),
                                    inserter=lambda t, c: inserted.append(t))
        assert pipe.run(wav, None) is None
        assert inserted == []

    def test_loud_audio_passes_silence_gate(self, tmp_path, cfg, quiet_ui):
        cfg["recording"]["skip_silent"] = True
        _, out = self._run(tmp_path, cfg, StubBackend("words"))
        assert out is not None

    def test_ai_polish(self, tmp_path, cfg, quiet_ui):
        cfg["ai"]["enabled"] = True
        _, out = self._run(tmp_path, cfg, StubBackend("rough text"),
                           polisher=lambda t: "Polished!",
                           inserter=lambda t, c: inserted(t))
        assert out["text"] == "Polished!" and out["ai"] is True

    def test_ai_error_falls_back_to_raw(self, tmp_path, cfg, quiet_ui):
        cfg["ai"]["enabled"] = True

        def broken(_):
            raise AIError("ollama down")

        got = []
        _, out = self._run(tmp_path, cfg, StubBackend("raw words"),
                           polisher=broken,
                           inserter=lambda t, c: (got.append(t), "typed")[1])
        assert out["text"] == "raw words" and out["ai"] is False
        assert got == ["raw words"]

    def test_insertion_failure_uses_clipboard_fallback(self, tmp_path, cfg, quiet_ui, monkeypatch):
        fallback = []
        monkeypatch.setattr(dm.insertion, "clipboard_fallback", lambda t: fallback.append(t))

        def broken(_t, _c):
            raise InsertError("no display")

        _, out = self._run(tmp_path, cfg, StubBackend("help me"), inserter=broken)
        assert out["strategy"] == "clipboard-fallback"
        assert fallback == ["help me"]

    def test_paste_notices_flow_through_notifications(self, tmp_path, cfg,
                                                     quiet_ui, monkeypatch):
        # default-inserter path: DictationPipeline wraps insert_text with
        # on_notice -> the existing notification path (no UI change)
        def fake_paste(text, *, key="ctrl+v", verify=True, on_notice=None):
            if on_notice is not None:
                on_notice("Paste did not land - typing instead")
            raise InsertError("paste not verified: target did not read")

        monkeypatch.setattr(dm.insertion, "insert_paste", fake_paste)
        monkeypatch.setattr(dm.insertion, "copy_to_clipboard", lambda t: None)
        cfg["insertion"]["mode"] = "paste"
        _, out = self._run(tmp_path, cfg, StubBackend("hello there"))
        assert out["strategy"] == "clipboard-fallback"
        assert any("Paste did not land" in (t + b)
                   for t, b in quiet_ui["notify"])

    def test_history_disabled(self, tmp_path, cfg, quiet_ui):
        cfg["history"]["save"] = False
        writer = []
        _, out = self._run(tmp_path, cfg, StubBackend("x"),
                           history_writer=lambda e, w: writer.append(e))
        assert out is not None and writer == []

    def test_slash_squeeze_applied(self, tmp_path, cfg, quiet_ui):
        inserted = []
        _, out = self._run(tmp_path, cfg, StubBackend("/ fix the deploy"),
                           inserter=lambda t, c: (inserted.append(t), "typed")[1])
        assert inserted == ["/fix the deploy"]
        assert out["text"] == "/fix the deploy"

    def test_mention_squeeze_applied(self, tmp_path, cfg, quiet_ui):
        inserted = []
        _, out = self._run(tmp_path, cfg, StubBackend("ping @ John Smith now"),
                           inserter=lambda t, c: (inserted.append(t), "typed")[1])
        assert inserted == ["ping @John Smith now"]

    def test_squeeze_runs_after_ai_polish(self, tmp_path, cfg, quiet_ui):
        # post-AI position proof: the polisher would re-split "/fix" if the
        # squeeze ran pre-AI (upstream applies it after AI cleanup)
        cfg["ai"]["enabled"] = True
        inserted = []
        _, out = self._run(
            tmp_path, cfg, StubBackend("/ fix the deploy"),
            polisher=lambda t: f"[ai] {t}",
            inserter=lambda t, c: (inserted.append(t), "typed")[1])
        assert inserted == ["[ai] /fix the deploy"]
        assert out["ai"] is True

    def test_squeeze_disabled_by_config(self, tmp_path, cfg, quiet_ui):
        cfg["processing"]["slash_mention_squeeze"] = False
        inserted = []
        _, out = self._run(tmp_path, cfg, StubBackend("/ fix the deploy"),
                           inserter=lambda t, c: (inserted.append(t), "typed")[1])
        assert inserted == ["/ fix the deploy"]

    def test_rewrite_mode_not_squeezed(self, tmp_path, cfg, quiet_ui):
        # rewrite keeps today's behavior: the instruction text is untouched
        cfg["ai"]["enabled"] = True
        wav = make_wav(tmp_path / "r.wav")
        pipe = dm.DictationPipeline(
            cfg, StubBackend("/ make this shorter"),
            inserter=lambda t, c: "typed",
            rewriter=lambda instruction, context: f"RE({instruction})")
        out = pipe.run(wav, None, mode="rewrite", rewrite_context=None)
        assert out["text"] == "RE(/ make this shorter)"


def inserted(text):
    """Shared inserter stub: records nothing, reports 'typed'."""
    return "typed"


# ---------------------------------------------------------------------------
# Daemon state machine
# ---------------------------------------------------------------------------

class TestDaemon:
    def make(self, cfg, recorder, backend=None):
        backend = backend or StubBackend("typed text")
        d = dm.Daemon(cfg, recorder=recorder,
                      backend_factory=lambda c: backend,
                      use_hotkey=False, use_sounds=False)
        d.backend = backend  # simulate a successful startup load
        return d

    def wait_done(self, d, timeout=5.0) -> bool:
        """Wait for any in-flight processing thread to finish."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if d._process_thread is None or not d._process_thread.is_alive():
                return not d.busy
            time.sleep(0.02)
        return False

    def test_toggle_cycle(self, cfg, quiet_ui, tmp_path):
        rec = StubRecorder()
        d = self.make(cfg, rec)
        assert d.handle_request({"action": "toggle"})["recording"] is True
        assert rec.started == 1
        assert d.handle_request({"action": "toggle"})["recording"] is False
        assert self.wait_done(d)
        assert d.last_result.get("text") == "typed text"
        assert rec.stopped == 1

    def test_status_action(self, cfg, quiet_ui):
        d = self.make(cfg, StubRecorder())
        resp = d.handle_request({"action": "status"})
        assert resp["ok"] and resp["recording"] is False and resp["backend"] == "stub"

    def test_status_reports_cuda_from_live_backend(self, cfg, quiet_ui):
        """The UI's 'GPU yes/no' reads status["cuda"]: it must reflect the
        loaded backend's resolved device (post auto-pick and CPU
        fallback), not be silently absent (the pre-2026-09-10 bug that
        showed 'GPU no' on CUDA machines)."""
        d = self.make(cfg, StubRecorder())
        d.backend.device = "cuda"  # what faster-whisper resolves on a GPU box
        assert d.handle_request({"action": "status"})["cuda"] is True
        d.backend.device = "cpu"  # e.g. the cuDNN-missing CPU fallback
        assert d.handle_request({"action": "status"})["cuda"] is False

    def test_unknown_action(self, cfg, quiet_ui):
        d = self.make(cfg, StubRecorder())
        resp = d.handle_request({"action": "explode"})
        assert resp["ok"] is False and "unknown action" in resp["error"]

    def test_cancel(self, cfg, quiet_ui):
        rec = StubRecorder()
        d = self.make(cfg, rec)
        d.toggle()
        resp = d.handle_request({"action": "cancel"})
        assert resp["cancelled"] and not d.recording
        assert rec.cancelled == 1
        assert rec.path is None  # temp file discarded

    def test_cancel_when_idle_is_noop(self, cfg, quiet_ui):
        rec = StubRecorder()
        d = self.make(cfg, rec)
        d.cancel()
        assert rec.cancelled == 0

    def test_hotkey_informed_of_recording_state(self, cfg, quiet_ui):
        """Escape-cancel needs the grab only while recording; the daemon
        must tell the hotkey listener when dictation starts and stops."""
        rec = StubRecorder()
        d = self.make(cfg, rec)
        calls = []

        class FakeHotkey:
            def set_recording(self, active):
                calls.append(active)

        d._hotkey = FakeHotkey()
        d.toggle()
        d.cancel()
        assert calls == [True, False]

    def test_hotkey_informed_on_normal_stop(self, cfg, quiet_ui):
        rec = StubRecorder()
        d = self.make(cfg, rec)
        calls = []
        d._hotkey = type("H", (), {"set_recording":
                                   staticmethod(lambda a: calls.append(a))})()
        d.toggle()
        d.toggle()
        assert self.wait_done(d)
        assert calls == [True, False]

    def test_dictation_tryout(self, cfg, quiet_ui):
        """Onboarding tryout: records + transcribes, never types anywhere."""
        rec = StubRecorder()
        d = self.make(cfg, rec, StubBackend("tryout text"))
        result = d.test_dictation(seconds=1.0)
        assert result["ok"] and result["text"] == "tryout text"
        assert result["duration_s"] >= 0.5
        assert d.busy is False  # released even on success
        assert rec.stopped == 1

    def test_dictation_tryout_guarded_while_recording(self, cfg, quiet_ui):
        rec = StubRecorder()
        d = self.make(cfg, rec)
        d.toggle()
        result = d.test_dictation(seconds=1.0)
        assert not result["ok"] and "recording" in result["error"]
        d.cancel()
        assert d.busy is False

    def test_dictation_tryout_silence_reports_error(self, cfg, quiet_ui,
                                                    tmp_path):
        import wave as wave_mod

        class SilentRecorder(StubRecorder):
            def start(self, path):
                self.path = path
                with wave_mod.open(str(path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(b"\x00\x00" * 16000)
                self.started += 1

        d = self.make(cfg, SilentRecorder())
        result = d.test_dictation(seconds=1.0)
        assert not result["ok"] and "silent" in result["error"]
        assert d.busy is False

    def test_busy_guard_ignores_toggle(self, cfg, quiet_ui):
        rec = StubRecorder()
        d = self.make(cfg, rec)
        d.busy = True
        assert d.toggle() is False  # did not start recording
        assert rec.started == 0

    def test_recorder_start_failure_notifies(self, cfg, quiet_ui):
        rec = StubRecorder(fail_start=True)
        d = self.make(cfg, rec)
        assert d.toggle() is False
        assert any("Recording failed" in (t + b) for t, b in quiet_ui["notify"])
        assert not d.recording

    def test_watchdog_auto_stops(self, cfg, quiet_ui):
        cfg["recording"]["max_seconds"] = 0.2
        rec = StubRecorder()
        d = self.make(cfg, rec)
        d.toggle()
        assert d.recording
        deadline = time.monotonic() + 5
        while d.recording and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not d.recording
        assert self.wait_done(d)

    def test_shutdown_while_recording_cancels(self, cfg, quiet_ui):
        rec = StubRecorder()
        d = self.make(cfg, rec)
        d.toggle()
        d.shutdown()
        assert rec.cancelled == 1 and not d.recording

    def test_backend_factory_failure_is_lazy(self, cfg, quiet_ui, tmp_path):
        rec = StubRecorder()
        d = dm.Daemon(cfg, recorder=rec,
                      backend_factory=lambda c: (_ for _ in ()).throw(RuntimeError("no model")),
                      use_hotkey=False, use_sounds=False)
        d.toggle()
        d.toggle()
        assert self.wait_done(d)
        assert d.last_result == {}  # nothing typed, error notified
        assert any("Transcription failed" in (t + b) for t, b in quiet_ui["notify"])


class TestAutoStopRace:
    def test_auto_stop_after_cancel_starts_nothing(self, cfg, quiet_ui):
        rec = StubRecorder()
        d = dm.Daemon(cfg, recorder=rec,
                      backend_factory=lambda c: StubBackend("x"),
                      use_hotkey=False, use_sounds=False)
        d.backend = StubBackend("x")
        d.toggle()
        assert d.recording
        d.cancel()  # timer may still be pending
        d._watchdog = threading.Timer(0.05, d._auto_stop)  # simulate late fire
        d._watchdog.start()
        time.sleep(0.3)
        assert not d.recording  # and crucially:
        assert rec.started == 1  # no second recording began


class TestStaleTmpSweep:
    def test_old_tmp_files_removed(self, tmp_path, monkeypatch):
        import glob
        import os
        old = tmp_path / "sayitermano-old.wav"
        legacy = tmp_path / "fluidvoice-legacy.wav"  # pre-rename prefix
        new = tmp_path / "sayitermano-new.wav"
        for f in (old, legacy, new):
            f.write_bytes(b"x")
        far_past = time.time() - 2 * 86400
        os.utime(old, (far_past, far_past))
        os.utime(legacy, (far_past, far_past))
        monkeypatch.setattr(
            glob, "glob",
            lambda pattern: [str(old), str(new)] if pattern.startswith("/tmp/sayitermano-")
            else [str(legacy)] if pattern.startswith("/tmp/fluidvoice-") else [])
        dm.Daemon._sweep_stale_tmp()
        assert not old.exists() and not legacy.exists() and new.exists()


class TestFirstPcmWatchdog:
    class SilentRecorder(StubRecorder):
        """Simulates a live-but-mute source: file never grows."""
        def start(self, path):
            self.path = path
            path.write_bytes(b"\0" * 100)  # header only
            self.started += 1

    def test_silent_mic_stops_early(self, cfg, quiet_ui):
        cfg["recording"]["first_pcm_timeout"] = 0.2
        rec = self.SilentRecorder()
        d = dm.Daemon(cfg, recorder=rec,
                      backend_factory=lambda c: StubBackend("x"),
                      use_hotkey=False, use_sounds=False)
        d.toggle()
        deadline = time.monotonic() + 3
        # the watchdog flips recording=False before its notify lands - wait
        # for the full end state, not just the flip
        while (d.recording or not any("no audio" in (t + b).lower()
                                      for t, b in quiet_ui["notify"])) \
                and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not d.recording
        assert rec.stopped >= 1
        assert any("no audio" in (t + b).lower() for t, b in quiet_ui["notify"])

    def test_healthy_mic_not_stopped(self, cfg, quiet_ui):
        cfg["recording"]["first_pcm_timeout"] = 0.2
        rec = StubRecorder()  # writes a real 1s wav -> PCM is flowing
        d = dm.Daemon(cfg, recorder=rec,
                      backend_factory=lambda c: StubBackend("x"),
                      use_hotkey=False, use_sounds=False)
        d.toggle()
        time.sleep(0.6)
        assert d.recording  # watchdog did not fire
        d.cancel()

    def test_disabled_by_zero(self, cfg, quiet_ui):
        cfg["recording"]["first_pcm_timeout"] = 0
        rec = self.SilentRecorder()
        d = dm.Daemon(cfg, recorder=rec,
                      backend_factory=lambda c: StubBackend("x"),
                      use_hotkey=False, use_sounds=False)
        d.toggle()
        time.sleep(0.4)
        assert d.recording
        d.cancel()


class TestPasteLast:
    def test_pastes_last_result(self, cfg, quiet_ui, monkeypatch):
        pasted = []
        monkeypatch.setattr(dm.insertion, "insert_text",
                            lambda text, c: pasted.append(text) or "typed")
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: StubBackend("hello again"),
                      use_hotkey=False, use_sounds=False)
        d.backend = StubBackend("hello again")
        d.toggle(); d.toggle()
        assert self._wait(d)
        resp = d.handle_request({"action": "paste-last"})
        assert resp["ok"] and pasted == ["hello again"]

    def test_nothing_to_paste(self, cfg, quiet_ui):
        from fluidvoice import history
        monkeypatch_hist = []
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: StubBackend("x"),
                      use_hotkey=False, use_sounds=False)
        d.backend = StubBackend("x")
        import fluidvoice.history as h
        orig_tail = h.tail
        h.tail = lambda n=20: []
        try:
            resp = d.handle_request({"action": "paste-last"})
        finally:
            h.tail = orig_tail
        assert not resp["ok"] and "nothing" in resp["error"]

    @staticmethod
    def _wait(d, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if d._process_thread is None or not d._process_thread.is_alive():
                return not d.busy
            time.sleep(0.02)
        return False


class TestCopyToClipboard:
    def test_copies_when_enabled(self, tmp_path, cfg, quiet_ui, monkeypatch):
        cfg["general"]["copy_to_clipboard"] = True
        copied = []
        monkeypatch.setattr(dm.insertion, "copy_to_clipboard", lambda t: copied.append(t))
        pipe = dm.DictationPipeline(cfg, StubBackend("copy me"),
                                    inserter=lambda t, c: "typed")
        wav = make_wav(tmp_path / "c.wav")
        assert pipe.run(wav, None)["text"] == "copy me"
        assert copied == ["copy me"]

    def test_no_copy_by_default(self, tmp_path, cfg, quiet_ui, monkeypatch):
        copied = []
        monkeypatch.setattr(dm.insertion, "copy_to_clipboard", lambda t: copied.append(t))
        pipe = dm.DictationPipeline(cfg, StubBackend("do not copy"),
                                    inserter=lambda t, c: "typed")
        wav = make_wav(tmp_path / "n.wav")
        pipe.run(wav, None)
        assert copied == []


class TestShortAudioPadding:
    def test_half_second_padded(self, tmp_path, cfg, quiet_ui):
        from fluidvoice.audio_utils import duration_seconds
        wav = make_wav(tmp_path / "short.wav", seconds=0.5)
        seen = {}

        class CapBackend(StubBackend):
            def transcribe(self, w, language=None):
                seen["duration"] = duration_seconds(str(w))
                return {"text": "ok"}

        pipe = dm.DictationPipeline(cfg, CapBackend(), inserter=lambda t, c: "typed")
        pipe.run(wav, None)
        assert seen["duration"] >= 1.0


class TestApplyConfig:
    """Settings saves reach the running daemon: hotkeys re-grabbed, recorder
    rebuilt, tray toggled - failures surface as UI errors, not crashes."""

    def _daemon(self):
        return dm.Daemon(cfg=copy.deepcopy(DEFAULTS), recorder=StubRecorder(),
                         use_hotkey=False, use_sounds=False)

    def test_hotkey_restart_stops_old_and_restarts(self, monkeypatch):
        d = self._daemon()
        d.use_hotkey = True
        stopped = []

        class FakeListener:
            def stop(self):
                stopped.append(1)

        d._hotkey = FakeListener()
        d._rewrite_hotkey = FakeListener()
        started = []
        monkeypatch.setattr(d, "_start_hotkey", lambda: started.append(1) or None)
        out = d.apply_config(["hotkey.key", "hotkey.cancel_key"])
        assert stopped == [1, 1] and started == [1]
        assert out == {"applied": ["hotkeys"], "errors": []}

    def test_hotkey_restart_surfaces_failure(self, monkeypatch):
        d = self._daemon()
        d.use_hotkey = True
        monkeypatch.setattr(d, "_start_hotkey",
                            lambda: "unknown key name 'zz'")
        out = d.apply_config(["hotkey.key"])
        assert out["applied"] == []
        assert "re-bind failed" in out["errors"][0]

    def test_hotkey_noop_when_hotkeys_disabled(self):
        d = self._daemon()
        out = d.apply_config(["hotkey.key"])  # use_hotkey=False -> early return
        assert out == {"applied": ["hotkeys"], "errors": []}

    def test_recorder_rebuilt_on_command_change(self):
        d = self._daemon()
        old = d.recorder
        d.cfg["recording"]["command"] = "parecord"
        out = d.apply_config(["recording.command"])
        assert out["applied"] == ["recorder"]
        assert d.recorder is not old
        assert d.recorder.command == "parecord"

    def test_device_change_refused_while_dictating(self):
        d = self._daemon()
        d.cfg["recording"]["device"] = "alsa_input.pci"
        d.recording = True
        out = d.apply_config(["recording.device"])
        assert out["applied"] == []
        assert "dictating" in out["errors"][0]

    def test_tray_start_when_enabled(self, monkeypatch):
        d = self._daemon()
        d.cfg["general"]["tray_enabled"] = True
        calls = []
        monkeypatch.setattr(d, "_start_tray", lambda: calls.append(1))
        out = d.apply_config(["general.tray_enabled"])
        assert out["applied"] == ["tray"] and calls == [1]

    def test_tray_stop_when_disabled(self):
        d = self._daemon()
        d.cfg["general"]["tray_enabled"] = False
        stopped = []

        class FakeTray:
            def stop(self):
                stopped.append(1)

        d._tray = FakeTray()
        out = d.apply_config(["general.tray_enabled"])
        assert out["applied"] == ["tray"] and stopped == [1]
        assert d._tray is None


class TestSocketConfigActions:
    """Native-app spec: get/set-config + select-model over the control
    socket (validated by config.apply_settings - one source of truth)."""

    def make(self2, cfg, recorder):  # noqa: N805 - reuse TestDaemon.make style
        pass

    def test_get_config_masks_api_key(self, cfg, quiet_ui):
        cfg["ai"]["api_key"] = "super-secret"
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        resp = d.handle_request({"action": "get-config"})
        assert resp["ok"]
        assert resp["config"]["ai"]["api_key"] is True  # bool, never the value

    def test_set_config_validates_and_applies(self, cfg, quiet_ui, tmp_path,
                                              monkeypatch):
        import fluidvoice.config as config_mod
        monkeypatch.setattr(config_mod, "save_config",
                            lambda c, path=None: tmp_path / "c.toml")
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        resp = d.handle_request({"action": "set-config", "config": {
            "general": {"language": "de"},
            "recording": {"max_seconds": "not-a-number"},
            "bogus": {"x": 1},
        }})
        assert resp["ok"] is False
        assert "general.language" in resp["changed"]
        assert "recording.max_seconds" in resp["rejected"]
        assert d.cfg["general"]["language"] == "de"
        assert "bogus" not in resp["changed"] + resp["rejected"]  # silently ignored section

    def test_set_config_restart_note(self, cfg, quiet_ui, tmp_path, monkeypatch):
        import fluidvoice.config as config_mod
        monkeypatch.setattr(config_mod, "save_config",
                            lambda c, path=None: tmp_path / "c.toml")
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        resp = d.handle_request({"action": "set-config", "config": {
            "model": {"eager_warmup": False}}})
        assert "model.eager_warmup" in resp["restart_required"]
        assert "restart" in resp["note"]

    def test_select_model_unknown_rejected(self, cfg, quiet_ui):
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        resp = d.handle_request({"action": "select-model", "name": "gpt-5"})
        assert resp["ok"] is False and "unknown model" in resp["error"]

    def test_select_model_warms_and_hot_swaps(self, cfg, quiet_ui, tmp_path,
                                              monkeypatch):
        import time as _time

        made = []

        class FakeWarmable:
            name = "faster-whisper"

            def __init__(self, c):
                made.append(c["model"]["name"])

            def warmup(self):
                pass

        import fluidvoice.backends as backends_mod
        monkeypatch.setattr(backends_mod, "load_backend", lambda c: FakeWarmable(c))
        import fluidvoice.config as config_mod
        monkeypatch.setattr(config_mod, "save_config",
                            lambda c, path=None: tmp_path / "c.toml")
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        resp = d.handle_request({"action": "select-model", "name": "turbo"})
        assert resp["ok"] and resp["model"] == "large-v3-turbo"
        deadline = _time.monotonic() + 5
        while d.warmup["running"] and _time.monotonic() < deadline:
            _time.sleep(0.05)
        assert d.warmup["error"] is None
        assert made == ["large-v3-turbo"]
        assert d.cfg["model"]["name"] == "large-v3-turbo"
        assert d.backend.name == "faster-whisper"  # swapped instance

    def test_status_includes_warmup(self, cfg, quiet_ui):
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        d.backend = StubBackend("x")
        resp = d.handle_request({"action": "status"})
        assert "warmup" in resp and resp["warmup"]["running"] is False
        assert "active_model" in resp

    def test_status_includes_today(self, cfg, quiet_ui, tmp_path, monkeypatch):
        h = tmp_path / "today.jsonl"
        h.write_text(
            json.dumps({"ts": time.time(), "text": "now",
                        "duration_s": 2.0}) + "\n"
            + json.dumps({"ts": time.time() - 86400, "text": "old",
                          "duration_s": 5.0}) + "\n", encoding="utf-8")
        monkeypatch.setattr(dm.history_mod.paths, "history_file", lambda: h)
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        d.backend = StubBackend("x")
        resp = d.handle_request({"action": "status"})
        assert resp["today"] == {"dictations": 1, "seconds": 2.0, "words": 1}

    def test_mics_action(self, cfg, quiet_ui, monkeypatch):
        # daemon imports list_microphones inside the handler, so patch the source
        monkeypatch.setattr("fluidvoice.tray.list_microphones",
                            lambda: ["Default", "USB Mic"])
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        resp = d.handle_request({"action": "mics"})
        assert resp == {"ok": True, "mics": ["Default", "USB Mic"]}

    def test_select_model_failure_rolls_back(self, cfg, quiet_ui, tmp_path,
                                             monkeypatch):
        import time as _time

        class FakeBroken:
            name = "faster-whisper"

            def __init__(self, c):
                pass

            def warmup(self):
                raise RuntimeError("download exploded")

        import fluidvoice.backends as backends_mod
        monkeypatch.setattr(backends_mod, "load_backend", lambda c: FakeBroken(c))
        import fluidvoice.config as config_mod
        monkeypatch.setattr(config_mod, "save_config",
                            lambda c, path=None: tmp_path / "c.toml")
        cfg["model"]["name"] = "small"
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        keep = d.backend = StubBackend("x")
        resp = d.handle_request({"action": "select-model", "name": "turbo"})
        assert resp["ok"] and resp["model"] == "large-v3-turbo"
        deadline = _time.monotonic() + 5
        while d.warmup["running"] and _time.monotonic() < deadline:
            _time.sleep(0.05)
        assert d.warmup["running"] is False
        assert d.warmup["error"]  # failure surfaced to the UI
        assert d.cfg["model"]["name"] == "small"  # rolled back
        assert d.backend is keep  # running backend untouched


class StubClosingDisplay:
    """Records the pill lifecycle: finish()/close() vs state/badge churn."""

    using_overlay = True

    def __init__(self):
        self.events: list[tuple] = []

    def start(self):
        self.events.append(("start", None))

    def show(self, text):
        self.events.append(("show", text))

    def set_state(self, state):
        self.events.append(("state", state))

    def set_badge(self, badge):
        self.events.append(("badge", badge))

    def finish(self, badge=None, confidence=None):
        self.events.append(("finish", badge, confidence))

    def close(self):
        self.events.append(("close", None))


class TestDoneBeat:
    """Peak-end success beat: _process finishes the closing pill with a
    check badge on success (research §7), plain close on failure/command."""

    def _daemon(self, cfg, backend=None, **pipeline_kw):
        backend = backend or StubBackend("typed text")
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: backend,
                      pipeline_factory=lambda c, b: dm.DictationPipeline(
                          c, b, inserter=lambda t, c2: "typed", **pipeline_kw),
                      use_hotkey=False, use_sounds=False)
        d.backend = backend
        return d

    def test_success_finishes_with_check(self, tmp_path, cfg, quiet_ui):
        d = self._daemon(cfg)
        disp = StubClosingDisplay()
        d._closing_display = disp
        d._process(make_wav(tmp_path / "u.wav"), "App", "dictate", None)
        assert disp.events[-1] == ("finish", "✓", None)

    def test_ai_polish_finishes_with_ai_badge(self, tmp_path, cfg, quiet_ui):
        d = self._daemon(cfg, polisher=lambda t: "Polished!")
        cfg["ai"]["enabled"] = True
        disp = StubClosingDisplay()
        d._closing_display = disp
        d._process(make_wav(tmp_path / "u.wav"), "App", "dictate", None)
        assert disp.events[-1] == ("finish", "✓ AI", None)

    def test_failure_closes_without_beat(self, tmp_path, cfg, quiet_ui):
        d = self._daemon(cfg, StubBackend(error=RuntimeError("boom")))
        disp = StubClosingDisplay()
        d._closing_display = disp
        d._process(make_wav(tmp_path / "u.wav"), "App", "dictate", None)
        assert disp.events == [("close", None)]

    def test_command_mode_closes_panel_takes_over(self, tmp_path, cfg, quiet_ui):
        d = self._daemon(cfg)
        disp = StubClosingDisplay()
        d._closing_display = disp
        d._process(make_wav(tmp_path / "u.wav"), "App", "command", None)
        assert disp.events[-1] == ("close", None)

    def test_no_display_no_crash(self, tmp_path, cfg, quiet_ui):
        d = self._daemon(cfg)
        d._closing_display = None
        d._process(make_wav(tmp_path / "u.wav"), "App", "dictate", None)
        assert d.last_result.get("text") == "typed text"


class TestConfidenceBand:
    """Ordinal recognition confidence (research §5): honest 0-2 bands or
    None when the backend cannot say."""

    def test_bands_from_avg_logprob(self):
        assert dm.confidence_band({"segments": [{"avg_logprob": -0.1},
                                                {"avg_logprob": -0.3}]}) == 2
        assert dm.confidence_band({"segments": [{"avg_logprob": -0.5}]}) == 1
        assert dm.confidence_band({"segments": [{"avg_logprob": -1.2}]}) == 0

    def test_unknown_when_no_evidence(self):
        assert dm.confidence_band({}) is None
        assert dm.confidence_band({"segments": []}) is None
        assert dm.confidence_band({"segments": [{"text": "x"}]}) is None
        assert dm.confidence_band({"segments": [{"avg_logprob": "bad"}]}) is None

    def test_pipeline_persists_confidence(self, tmp_path, cfg, quiet_ui):
        class SegBackend(StubBackend):
            def transcribe(self, wav, language=None):
                return {"text": self.text, "language": "en", "duration": 1.0,
                        "segments": [{"text": self.text, "avg_logprob": -0.2}]}

        history = []
        pipe = dm.DictationPipeline(cfg, SegBackend("good words"),
                                    inserter=lambda t, c: "typed",
                                    history_writer=lambda e, w: history.append(e))
        out = pipe.run(make_wav(tmp_path / "c.wav"), "App")
        assert out["confidence"] == 2
        assert history[0]["confidence"] == 2

    def test_pipeline_omits_confidence_when_unknown(self, tmp_path, cfg, quiet_ui):
        history = []
        pipe = dm.DictationPipeline(cfg, StubBackend("plain words"),
                                    inserter=lambda t, c: "typed",
                                    history_writer=lambda e, w: history.append(e))
        out = pipe.run(make_wav(tmp_path / "c.wav"), "App")
        assert out["confidence"] is None
        assert "confidence" not in history[0]


class TestInsertTextAction:
    """History-window repair path: type arbitrary text via the daemon."""

    def _daemon(self, cfg):
        backend = StubBackend("x")
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: backend,
                      use_hotkey=False, use_sounds=False)
        d.backend = backend
        return d

    def test_inserts_and_reports_ok(self, cfg, quiet_ui, monkeypatch):
        typed = []
        monkeypatch.setattr(dm.insertion, "insert_text",
                            lambda t, c: typed.append(t))
        d = self._daemon(cfg)
        resp = d.handle_request({"action": "insert-text", "text": "hi there"})
        assert resp["ok"] is True
        assert typed == ["hi there"]

    def test_busy_refuses(self, cfg, quiet_ui, monkeypatch):
        monkeypatch.setattr(dm.insertion, "insert_text", lambda t, c: None)
        d = self._daemon(cfg)
        d.busy = True
        resp = d.handle_request({"action": "insert-text", "text": "x"})
        assert resp["ok"] is False and resp["error"] == "busy"

    def test_empty_text_refuses(self, cfg, quiet_ui, monkeypatch):
        monkeypatch.setattr(dm.insertion, "insert_text", lambda t, c: None)
        d = self._daemon(cfg)
        resp = d.handle_request({"action": "insert-text", "text": "   "})
        assert resp["ok"] is False

    def test_insert_error_surfaces(self, cfg, quiet_ui, monkeypatch):
        def boom(t, c):
            raise InsertError("no display")

        monkeypatch.setattr(dm.insertion, "insert_text", boom)
        d = self._daemon(cfg)
        resp = d.handle_request({"action": "insert-text", "text": "x"})
        assert resp["ok"] is False and "no display" in resp["error"]


class TestExtraShortcutProfiles:
    """B1: takes started by a profiled extra shortcut polish with that
    named prompt profile (upstream per-shortcut AI-prompt picker)."""

    @staticmethod
    def make(cfg, recorder, polisher):
        backend = StubBackend("raw words here")
        d = dm.Daemon(cfg, recorder=recorder,
                      backend_factory=lambda c: backend,
                      use_hotkey=False, use_sounds=False)
        d.backend = backend
        d.polisher = polisher
        return d

    def test_toggle_with_profile_sets_override_when_starting(self, cfg,
                                                             quiet_ui):
        d = self.make(cfg, StubRecorder(), lambda t, system_prompt=None: t)
        d.recording = False
        d._toggle_with_profile("Terse notes")
        assert d._profile_override == "Terse notes"
        assert d.recording is True
        # stopping via any shortcut must NOT clear it mid-take
        d._toggle_with_profile("Other")
        assert d.recording is False
        assert d._profile_override == "Terse notes"

    def test_polish_uses_profile_prompt(self, cfg, quiet_ui, tmp_path):
        from fluidvoice.ai import profiles
        # isolated XDG (conftest) keeps this out of the live sidecar
        profiles.save_named("TestProf", "TERSITY: be brief.")
        seen = {}

        def polisher(text, system_prompt=None):
            seen["prompt"] = system_prompt
            return "polished"

        cfg["ai"]["enabled"] = True
        pipe = dm.DictationPipeline(
            cfg, StubBackend("raw words"), polisher=polisher)
        pipe._profile_override = "TestProf"  # set by the daemon at handoff
        text, ai = pipe._polish("raw words")
        assert ai is True and text == "polished"
        assert seen["prompt"] == "TERSITY: be brief."

    def test_profile_override_wins_over_per_app_instructions(
            self, cfg, quiet_ui, monkeypatch):
        """#918 composition rule, pinned: a shortcut's prompt profile is
        'custom prompt only' by construction - it replaces BOTH the base
        prompt and any per-app instructions, never composes with them."""
        from fluidvoice.ai import profiles as profiles_mod
        monkeypatch.setattr(
            profiles_mod, "load_profiles",
            lambda path=None: {"Terse": "TERSITY: be brief."})
        seen = {}

        def polisher(text, system_prompt=None):
            seen["prompt"] = system_prompt
            return "polished"

        cfg["ai"]["enabled"] = True
        cfg["ai"]["per_app_prompts"] = [
            {"apps": ["zed"], "instructions": "mention Zed"}]
        pipe = dm.DictationPipeline(
            cfg, StubBackend("raw words"), polisher=polisher)
        pipe._profile_override = "Terse"
        text, ai = pipe._polish("raw words", app_hint="zed")
        assert ai is True
        assert seen["prompt"] == "TERSITY: be brief."   # alone, no base,
        # no per-app addendum
        assert "mention Zed" not in (seen["prompt"] or "")

    def test_polish_missing_profile_falls_back_to_base(self, cfg, quiet_ui):
        seen = {}

        def polisher(text, system_prompt=None):
            seen["prompt"] = system_prompt
            return "polished"

        cfg["ai"]["enabled"] = True
        pipe = dm.DictationPipeline(
            cfg, StubBackend("raw words"), polisher=polisher)
        pipe._profile_override = "Does Not Exist"
        text, ai = pipe._polish("raw words")
        assert ai is True
        # no per-app instructions: the default polisher path, prompt None
        assert "prompt" not in seen or seen["prompt"] is None

    def test_cancel_clears_override(self, cfg, quiet_ui):
        d = self.make(cfg, StubRecorder(), lambda t, system_prompt=None: t)
        d.recording = True
        d._watchdog = threading.Timer(999.0, d._auto_stop)
        d._profile_override = "Terse notes"
        d.cancel()
        assert d._profile_override is None
