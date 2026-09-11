"""Deterministic lifecycle + failure-sequence tests (Q6).

Cross-coordinator sequences the unit modules pin individually but that
matter at the Daemon seam, driven with EVENT BARRIERS (a blocking stub
backend released by the test, deadline polls on observable state) — no
guessed sleeps anywhere:

* stop → processing (busy) → a new take CANNOT start until the previous
  one's result landed — a stale late result can never enter a newer take
  because the newer take never overlaps it;
* cancel during recording discards; cancel during PROCESSING is today's
  documented no-op (Q10 will redefine this contract — this test makes
  that change deliberate, not accidental);
* duplicate stop transcribes exactly once;
* shutdown with a pending transcription joins it and leaves no tasks,
  releasing busy on the terminal path;
* session lock mid-take cancels the dictation and gates the hotkey;
* mic disappearance mid-take deliberately defers reselection to idle
  (mid-dictation safety) and then reselects.

Existing per-module tests (test_capture_coord, test_engine_manager,
test_daemon, test_runtime_tasks) are not duplicated here: this module
only crosses their seams. Insertion is intercepted through a spy
`pipeline_factory` (the daemon's own injection point), not by patching
module attributes the pipeline binds at construction.
"""
from __future__ import annotations

import copy
import threading
import time

import pytest

from fluidvoice import daemon as dm
from fluidvoice.config import DEFAULTS
from tests.test_daemon import StubRecorder  # shared harness


class BlockingBackend:
    """Stub backend whose transcribe() blocks until the test releases it —
    the barrier that makes every sequence below deterministic."""

    name = "blocking-stub"
    surfaces_detected_language = False

    def __init__(self, text="late result"):
        self.text = text
        self.gate = threading.Event()
        self.calls = 0

    def transcribe(self, wav, language=None):
        self.calls += 1
        self.gate.wait(timeout=30)  # a barrier, not a timeout: tests release
        return {"text": self.text, "language": "en", "duration": 1.0}


@pytest.fixture()
def cfg():
    return copy.deepcopy(DEFAULTS)


@pytest.fixture()
def quiet_ui(tmp_path, monkeypatch):
    calls = {"notify": [], "inserted": []}

    def fake_notify(title, body="", **k):
        calls["notify"].append((title, body))

    monkeypatch.setattr(dm.ui, "notify", fake_notify)
    monkeypatch.setattr(dm.ui, "play_sound", lambda *a, **k: None)
    # never query the REAL focused window (xdotool against the live
    # desktop: slow, order-dependent, and reads a real session)
    monkeypatch.setattr(dm.insertion, "active_window_class",
                        lambda: "TestApp")
    monkeypatch.setattr(dm.history_mod.paths, "history_file",
                        lambda: tmp_path / "h.jsonl")
    return calls


def make_daemon(cfg, backend, calls):
    """Daemon with a spy inserter (the pipeline_factory seam) and the
    test's own recorder instance for direct assertions."""
    from fluidvoice.pipeline import DictationPipeline

    def spy_inserter(text, c):
        calls["inserted"].append(text)
        return "typed"

    def factory(c, b, **kw):
        return DictationPipeline(c, b, inserter=spy_inserter, **kw)

    rec = StubRecorder()
    d = dm.Daemon(cfg, recorder=rec,
                  backend_factory=lambda c: backend,
                  pipeline_factory=factory,
                  use_hotkey=False, use_sounds=False)
    d.backend = backend
    return d, rec


def wait_for(cond, timeout=8.0, what="condition"):
    """Deadline poll on observable state (the only 'wait' allowed here)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


class TestTakeProcessingSequences:
    def test_busy_blocks_newer_take_so_late_results_cannot_leak_across(
            self, cfg, quiet_ui):
        backend = BlockingBackend()
        d, rec = make_daemon(cfg, backend, quiet_ui)
        assert d.toggle() is True           # take A starts
        assert d.toggle() is False          # stop -> A processing (blocked)
        wait_for(lambda: d.busy, what="take A processing")
        # a newer take cannot START while A's result is pending: this is
        # what prevents a late A result ever landing in take B's context
        assert d.toggle() is False
        assert d.recording is False
        wait_for(lambda: backend.calls == 1, what="take A transcribing")
        backend.gate.set()                  # release A's result
        wait_for(lambda: not d.busy and not d._process_thread.is_alive(),
                 what="take A finished")
        assert d.last_result.get("text") == "late result"
        assert quiet_ui["inserted"] == ["late result"]
        # NOW a new take is possible
        assert d.toggle() is True
        d.cancel()

    def test_cancel_during_recording_discards_and_never_processes(
            self, cfg, quiet_ui):
        backend = BlockingBackend()
        d, rec = make_daemon(cfg, backend, quiet_ui)
        assert d.toggle() is True
        d.cancel()
        assert d.recording is False
        assert rec.cancelled == 1
        assert backend.calls == 0           # nothing handed to processing
        assert quiet_ui["inserted"] == []
        assert dm.history_mod.tail(5) == []

    def test_cancel_during_processing_is_the_documented_noop(
            self, cfg, quiet_ui):
        """TODAY'S contract (daemon.cancel -> CaptureCoordinator.cancel,
        which returns when not recording): a take that already finished
        recording completes and inserts. Q10 will define a processing-
        cancel contract; when it does, THIS test must change with it."""
        backend = BlockingBackend()
        d, rec = make_daemon(cfg, backend, quiet_ui)
        d.toggle()
        d.toggle()
        wait_for(lambda: d.busy, what="processing")
        d.cancel()                          # arrives during processing
        backend.gate.set()
        wait_for(lambda: not d.busy and not d._process_thread.is_alive(),
                 what="processing finished")
        assert d.last_result.get("text") == "late result"  # completed
        assert quiet_ui["inserted"] == ["late result"]

    def test_duplicate_stop_transcribes_exactly_once(self, cfg, quiet_ui):
        backend = BlockingBackend()
        d, rec = make_daemon(cfg, backend, quiet_ui)
        d.toggle()
        assert d.toggle() is False          # first stop: hands off
        wait_for(lambda: d.busy, what="processing")
        assert d.toggle() is False          # duplicate stop: busy-refused
        assert d.toggle() is False
        backend.gate.set()
        wait_for(lambda: not d.busy, what="done")
        assert backend.calls == 1
        assert quiet_ui["inserted"] == ["late result"]


class TestShutdownWithPendingCallbacks:
    def test_shutdown_joins_inflight_processing_and_releases_everything(
            self, cfg, quiet_ui):
        backend = BlockingBackend()
        d, rec = make_daemon(cfg, backend, quiet_ui)
        d.toggle()
        d.toggle()
        wait_for(lambda: d.busy, what="processing")
        # the barrier: release the backend a beat INTO shutdown, so the
        # join below demonstrably waits for the in-flight callback
        threading.Timer(0.1, backend.gate.set).start()
        d.shutdown()
        assert d._process_thread is None or not d._process_thread.is_alive()
        assert d.busy is False              # busy released on terminal path
        assert d.last_result.get("text") == "late result"
        assert dm.history_mod.tail(5)       # the row committed

    def test_shutdown_while_recording_discards_without_processing(
            self, cfg, quiet_ui):
        backend = BlockingBackend()
        d, rec = make_daemon(cfg, backend, quiet_ui)
        d.toggle()
        d.shutdown()
        assert d.recording is False
        assert backend.calls == 0
        assert quiet_ui["inserted"] == []


class TestLockAndMicTransitions:
    def test_lock_mid_take_cancels_and_gates_the_hotkey(self, cfg, quiet_ui,
                                                        monkeypatch):
        backend = BlockingBackend()
        d, rec = make_daemon(cfg, backend, quiet_ui)
        monkeypatch.setattr(d, "_refresh_tray", lambda: None)
        assert d.toggle() is True
        d._on_locked(True)                  # lockmon transition callback
        assert d.recording is False         # active dictation cancelled
        assert rec.cancelled == 1
        assert backend.calls == 0
        # hotkey presses are swallowed while locked (stuck-key safe)
        assert d.toggle() is False
        assert rec.started == 1
        d._on_locked(False)                 # unlock
        assert d.toggle() is True
        d.cancel()

    def test_lock_with_pending_command_cancels_it(self, cfg, quiet_ui,
                                                  monkeypatch):
        """Lock clears a pending (unconfirmed) command proposal too —
        nothing awaits confirmation on a locked screen."""
        backend = BlockingBackend()
        d, rec = make_daemon(cfg, backend, quiet_ui)
        cancelled = []
        monkeypatch.setattr(d, "_refresh_tray", lambda: None)
        monkeypatch.setattr(d._commands, "cancel_pending",
                            lambda: cancelled.append(1))
        d._commands.pending = True          # plain attribute (coordinator)
        d._on_locked(True)
        assert cancelled == [1]
        d._commands.pending = False

    def test_mic_disappearance_mid_take_defers_reselect_to_idle(
            self, cfg, quiet_ui, monkeypatch):
        """A pulled USB mic must not yank the ACTIVE take (audio already
        flows); reselection is retried once idle. The take itself is
        allowed to finish on its own terms."""
        backend = BlockingBackend()
        d, rec = make_daemon(cfg, backend, quiet_ui)
        reselects = []
        monkeypatch.setattr(d, "_mic_reselect",
                            lambda names: reselects.append(tuple(names)))
        assert d.toggle() is True
        d._on_sources_changed(added=(), removed=("USB Mic",),
                              current=("Built-in",))
        assert d.recording is True          # mid-dictation safety held
        assert reselects == []              # nothing reselected mid-take
        assert d.toggle() is False          # stop -> processing
        backend.gate.set()
        wait_for(lambda: not d.busy, what="take finished")
        # the next watcher poll (idle now) performs the deferred reselect
        d._on_sources_changed(added=(), removed=(),
                              current=("Built-in",))
        assert reselects == [("Built-in",)]
