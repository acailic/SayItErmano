"""Idle model unload (model.idle_unload_s): lifecycle of the loaded
backend around the daemon's idle timer.

No real clock sleeping except the thread-spawn tests (reload-on-take);
the unload decision is driven through the injectable `now` argument of
Daemon._maybe_idle_unload, mirroring the fake-clock approach the plan
settled on (no sleep-based flakiness).
"""
from __future__ import annotations

import copy
import math
import struct
import threading
import time
import wave

import pytest

from fluidvoice import daemon as dm
from fluidvoice.config import DEFAULTS


def make_wav(path, seconds: float = 1.0) -> "object":
    n = int(16000 * seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        frames = bytearray()
        for i in range(n):
            v = int(12000 * math.sin(2 * math.pi * 440 * i / 16000))
            frames += struct.pack("<h", v)
        wf.writeframes(bytes(frames))
    if path.stat().st_size < 200:
        with open(path, "ab") as fh:
            fh.write(b"\0" * 300)
    return path


class StubRecorder:
    def __init__(self):
        self.path = None
        self.started = 0
        self.stopped = 0

    def start(self, path):
        self.path = make_wav(path)
        self.started += 1

    def stop(self):
        self.stopped += 1
        return self.path

    def cancel(self):
        self.stopped += 1
        self.path = None


class CountingBackend:
    """Stub backend that records loads (via the factory), transcriptions
    and close() calls - the whole lifecycle surface the policy touches."""

    name = "counting"

    def __init__(self, text="idle ok"):
        self.text = text
        self.transcribes = 0
        self.close_calls = 0

    def transcribe(self, wav, language=None):
        self.transcribes += 1
        return {"text": self.text}

    def warmup(self):
        pass

    def close(self):
        self.close_calls += 1


class StubPipeline:
    """Minimal honest pipeline: transcribes through the backend, types
    nothing - only the backend lifecycle matters."""

    def __init__(self, cfg, backend):
        self.backend = backend

    def run(self, wav, app_hint, mode="dictate", rewrite_context=None):
        result = self.backend.transcribe(wav, language=None) or {}
        try:
            wav.unlink(missing_ok=True)
        except OSError:
            pass
        return {"text": result.get("text", ""), "mode": mode}


def make_factory(fail_after=None):
    """Counting factory; `fail_after=n` makes every call after n
    successful loads raise (the reload-error path)."""
    loads: list[CountingBackend] = []

    def factory(c):
        if fail_after is not None and len(loads) >= fail_after:
            raise RuntimeError("no model")
        b = CountingBackend()
        loads.append(b)
        return b

    return factory, loads


@pytest.fixture()
def cfg():
    return copy.deepcopy(DEFAULTS)


@pytest.fixture()
def quiet_ui(tmp_path, monkeypatch):
    calls = {"notify": []}

    def fake_notify(title, body="", timeout_ms=2500, enabled=True):
        if enabled:
            calls["notify"].append((title, body))

    monkeypatch.setattr(dm.ui, "notify", fake_notify)
    monkeypatch.setattr(dm.ui, "play_sound", lambda *a, **k: None)
    monkeypatch.setattr(dm.insertion, "active_window_class",
                        lambda: "TestApp")
    monkeypatch.setattr(dm.history_mod.paths, "history_file",
                        lambda: tmp_path / "test-history.jsonl")
    return calls


def make_daemon(cfg, factory, recorder=None):
    d = dm.Daemon(cfg, recorder=recorder or StubRecorder(),
                  backend_factory=factory,
                  pipeline_factory=StubPipeline,
                  use_hotkey=False, use_sounds=False)
    # eager_warmup defaults True: join the stub warm thread so the idle
    # asserts can't race maybe_idle_unload's `warm.is_alive()` gate —
    # lost that race only on slow 2-core CI runners (first CI dispatch
    # 2026-09-12), never locally on 16 cores.
    warm = getattr(d._engines, "start_warm_thread", None)
    if warm is not None:
        warm.join(timeout=5.0)
    return d


def wait_done(d, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if d._process_thread is None or not d._process_thread.is_alive():
            return not d.busy
        time.sleep(0.02)
    return False


def wait_backend(d, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if d.backend is not None:
            return True
        time.sleep(0.01)
    return False


def _gate_state(d) -> str:
    """Full maybe_idle_unload gate snapshot for assert messages (the
    injected-time unload tests flake ONLY on very slow CI runners;
    when one refuses, this names the gate)."""
    e = d._engines
    warm = e.start_warm_thread
    return (f"threshold={e.idle_threshold()} active={d._is_take_active()} "
            f"(rec={d.recording} busy={d.busy}) warmup={dict(e.warmup)} "
            f"warm_thread={'alive' if warm is not None and warm.is_alive() else warm} "
            f"backend={type(d.backend).__name__}")


def take(d):
    """One full dictation: toggle on, toggle off, wait for processing."""
    d.toggle()
    d.toggle()
    assert wait_done(d)
    # wait_done watches busy/_process_thread only; `recording` clears on
    # the capture stop path. On very slow CI runners it could still be
    # True here — and maybe_idle_unload's take-active gate would then
    # (correctly!) refuse to unload. Wait for the full stop seam.
    deadline = time.monotonic() + 5.0
    while d.recording and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not d.recording


# ---------------------------------------------------------------------------
# Core lifecycle
# ---------------------------------------------------------------------------

class TestIdleUnloadCore:
    def test_load_on_first_take_exactly_once(self, cfg, quiet_ui):
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        assert d.backend is None  # lazy
        take(d)
        assert len(loads) == 1
        assert d.backend is loads[0]
        assert d.last_result.get("text") == "idle ok"
        assert d._engines.idle_unloaded_at is None

    def test_no_unload_while_recording_or_busy(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 60
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        base = d._engines.last_activity
        d.recording = True
        d._engines.maybe_idle_unload(now=base + 10_000)
        assert d.backend is not None
        d.recording = False
        d.busy = True
        d._engines.maybe_idle_unload(now=base + 10_000)
        assert d.backend is not None
        d.busy = False

    def test_no_unload_while_model_switch_in_flight(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 60
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        base = d._engines.last_activity
        d.warmup = {"running": True, "error": None, "model": "small"}
        d._engines.maybe_idle_unload(now=base + 10_000)
        assert d.backend is not None

    def test_no_unload_while_startup_warmup_thread_alive(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 60
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        base = d._engines.last_activity
        warm = threading.Thread(target=lambda: time.sleep(0.5), daemon=True)
        warm.start()
        d._engines.start_warm_thread = warm
        try:
            d._engines.maybe_idle_unload(now=base + 10_000)
            assert d.backend is not None
        finally:
            warm.join()

    def test_unload_at_threshold(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 60
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        base = d._engines.last_activity
        d._engines.maybe_idle_unload(now=base + 59)
        assert d.backend is not None and d._engines.idle_unloaded_at is None
        d._engines.maybe_idle_unload(now=base + 60)
        assert d.backend is None, _gate_state(d)
        assert d._engines.idle_unloaded_at == base + 60
        assert loads[0].close_calls == 1  # the close() seam fired

    def test_unload_below_threshold_never(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 3600
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        d._engines.maybe_idle_unload(now=d._engines.last_activity + 3599)
        assert d.backend is not None

    def test_policy_off_never_unloads(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 0
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        d._engines.maybe_idle_unload(now=d._engines.last_activity + 999_999)
        assert d.backend is not None
        assert d._engines.idle_unloaded_at is None

    def test_policy_off_starts_no_watcher_thread(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 0
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        d._engines.start_idle_watch()
        assert d._engines.idle_thread is None  # byte-identical: nothing runs

    def test_no_backend_no_crash(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 60
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        d._engines.maybe_idle_unload(now=d._engines.last_activity + 10_000)
        assert d.backend is None and d._engines.idle_unloaded_at is None


# ---------------------------------------------------------------------------
# Reload on next take
# ---------------------------------------------------------------------------

class TestReloadOnNextTake:
    def test_reload_success_and_transcribe(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 60
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        assert len(loads) == 1
        d._engines.maybe_idle_unload(now=d._engines.last_activity + 60)
        assert d.backend is None
        d.toggle()  # start: spawns the background reload thread
        assert wait_backend(d)  # loads while the user speaks
        d.toggle()  # stop -> _process -> transcribe
        assert wait_done(d)
        assert len(loads) == 2  # exactly one reload
        assert d._engines.idle_unloaded_at is None
        assert d.last_result.get("text") == "idle ok"
        assert loads[1].transcribes == 1

    def test_reload_failure_is_graceful(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 60
        factory, loads = make_factory(fail_after=1)
        d = make_daemon(cfg, factory)
        take(d)
        assert len(loads) == 1
        d._engines.maybe_idle_unload(now=d._engines.last_activity + 60)
        assert d.backend is None, _gate_state(d)
        d.toggle()  # background reload fails (logged, not raised)
        time.sleep(0.2)  # let the reload thread die
        assert d.backend is None
        d.toggle()  # stop -> _process's _ensure_backend fails gracefully
        prev_result = dict(d.last_result)
        assert wait_done(d)
        assert d.last_result == prev_result  # nothing new typed
        assert any("Transcription failed" in (t + b)
                   for t, b in quiet_ui["notify"])
        # daemon still fully functional afterwards
        d.toggle()
        assert d.recording
        d.toggle()
        assert wait_done(d)


# ---------------------------------------------------------------------------
# Idle clock: what resets it (and what must NOT)
# ---------------------------------------------------------------------------

class TestIdleClock:
    def test_read_only_calls_do_not_reset_idle(self, cfg, quiet_ui,
                                               monkeypatch):
        cfg["model"]["idle_unload_s"] = 60
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        base = d._engines.last_activity
        st = d.handle_request({"action": "status"})
        assert st["ok"] and st["model_state"]["policy_s"] == 60
        assert st["model_state"]["loaded"] is True
        assert st["model_state"]["idle_s"] >= 0
        d.last_result = {"text": "seeded"}
        monkeypatch.setattr(dm.insertion, "insert_text",
                            lambda text, c: "typed")
        assert d.handle_request({"action": "paste-last"})["ok"]
        assert d.handle_request({"action": "insert-text",
                                 "text": "hi"})["ok"]
        assert d.handle_request({"action": "get-config"})["ok"]
        d._engines.maybe_idle_unload(now=base + 60)
        assert d.backend is None  # none of those reads reset the clock

    def test_take_start_resets_idle(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 60
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        base = d._engines.last_activity
        d._engines.maybe_idle_unload(now=base + 60)
        assert d.backend is None, _gate_state(d)
        # a new take reloads + touches; even a huge synthetic age cannot
        # fire while idle-tracking resumes from the fresh take
        take(d)
        assert d.backend is not None
        assert d._engines.last_activity > base
        d._engines.maybe_idle_unload(now=d._engines.last_activity + 59)
        assert d.backend is not None


# ---------------------------------------------------------------------------
# Live policy change + shutdown
# ---------------------------------------------------------------------------

class TestPolicyApplyAndShutdown:
    def test_apply_config_starts_and_stops_watcher(self, cfg, quiet_ui):
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        assert d._engines.idle_thread is None  # default off: no watcher
        d.cfg["model"]["idle_unload_s"] = 60
        resp = d.apply_config(["model.idle_unload_s"])
        assert resp["applied"] == ["idle unload"]
        assert resp["errors"] == []
        assert d._engines.idle_thread is not None and d._engines.idle_thread.is_alive()
        watcher = d._engines.idle_thread
        assert watcher.name == "fluidvoice-idle-unload"
        d.cfg["model"]["idle_unload_s"] = 0
        resp = d.apply_config(["model.idle_unload_s"])
        assert resp["applied"] == ["idle unload"]
        assert d._engines.idle_thread is None
        watcher.join(timeout=2)
        assert not watcher.is_alive()

    def test_shutdown_with_watcher_running(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 60
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        d._engines.start_idle_watch()
        watcher = d._engines.idle_thread
        assert watcher is not None and watcher.is_alive()
        d.shutdown()
        assert not watcher.is_alive()
        d.shutdown()  # idempotent


# ---------------------------------------------------------------------------
# Surfacing: tray tooltip + status payload (doctor lines in Phase 3 style
# live in TestDoctorModelStateLines below)
# ---------------------------------------------------------------------------

class TestTrayTooltipSuffix:
    def test_no_suffix_while_loaded(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 60
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        assert "model unloaded" not in d._tray_tooltip()

    def test_suffix_after_unload(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 60
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        d._engines.maybe_idle_unload(now=d._engines.last_activity + 3661)
        assert "model unloaded (idle" in d._tray_tooltip()

    def test_no_suffix_when_policy_off(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 0
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        d.backend = None  # lazy first use, policy off
        assert "model unloaded" not in d._tray_tooltip()


class TestStatusModelState:
    def test_loaded(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 300
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        ms = d.handle_request({"action": "status"})["model_state"]
        assert ms["policy_s"] == 300 and ms["loaded"] is True
        assert ms["idle_s"] < 300

    def test_unloaded(self, cfg, quiet_ui):
        cfg["model"]["idle_unload_s"] = 300
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        d._engines.maybe_idle_unload(now=d._engines.last_activity + 300)
        ms = d.handle_request({"action": "status"})["model_state"]
        assert ms["policy_s"] == 300 and ms["loaded"] is False

    def test_default_off(self, cfg, quiet_ui):
        factory, loads = make_factory()
        d = make_daemon(cfg, factory)
        take(d)
        ms = d.handle_request({"action": "status"})["model_state"]
        assert ms["policy_s"] == 0


# ---------------------------------------------------------------------------
# Doctor lines
# ---------------------------------------------------------------------------

class TestDoctorModelStateLines:
    def _doctor(self, monkeypatch, tmp_path, status=None, socket_exists=True):
        from fluidvoice import control, doctor
        socket = tmp_path / "fluidvoice.sock"
        if socket_exists:
            socket.touch()
        monkeypatch.setattr(doctor.paths, "socket_path", lambda: socket)
        if not socket_exists:
            monkeypatch.setattr(control, "request",
                                lambda *a, **k: pytest.fail("must not query"))
            return doctor
        monkeypatch.setattr(control, "request",
                            lambda *a, **k: (status
                                             if not isinstance(status, Exception)
                                             else (_ for _ in ()).throw(status)))
        return doctor

    def test_policy_off_loaded(self, monkeypatch, tmp_path):
        doctor = self._doctor(monkeypatch, tmp_path, status={
            "model_state": {"policy_s": 0, "loaded": True, "idle_s": 12.0}})
        lines = doctor._model_state_lines({"model": {"idle_unload_s": 0}})
        assert lines == ["  model: loaded (idle unload off)"]

    def test_policy_off_not_loaded_lazy(self, monkeypatch, tmp_path):
        doctor = self._doctor(monkeypatch, tmp_path, status={
            "model_state": {"policy_s": 0, "loaded": False, "idle_s": 0.0}})
        lines = doctor._model_state_lines({})
        assert lines == ["  model: not loaded (lazy first use)"]

    def test_policy_on_loaded(self, monkeypatch, tmp_path):
        doctor = self._doctor(monkeypatch, tmp_path, status={
            "model_state": {"policy_s": 300, "loaded": True,
                            "idle_s": 720.5}})
        lines = doctor._model_state_lines({"model": {"idle_unload_s": 300}})
        assert lines == ["  model: loaded (idle 12m; policy 300s)"]

    def test_policy_on_unloaded(self, monkeypatch, tmp_path):
        doctor = self._doctor(monkeypatch, tmp_path, status={
            "model_state": {"policy_s": 300, "loaded": False,
                            "idle_s": 720.5}})
        lines = doctor._model_state_lines({"model": {"idle_unload_s": 300}})
        assert lines == ["  model: unloaded (idle 12m; policy 300s)"]

    def test_daemon_down_reports_config_policy(self, monkeypatch, tmp_path):
        doctor = self._doctor(monkeypatch, tmp_path, socket_exists=False)
        lines = doctor._model_state_lines({"model": {"idle_unload_s": 300}})
        assert lines == ["  model: daemon down (policy from config: 300s)"]

    def test_older_daemon_without_model_state(self, monkeypatch, tmp_path):
        doctor = self._doctor(monkeypatch, tmp_path, status={"ok": True})
        lines = doctor._model_state_lines({"model": {"idle_unload_s": 300}})
        assert lines == ["  model: unknown (older daemon; "
                         "policy from config: 300s)"]
