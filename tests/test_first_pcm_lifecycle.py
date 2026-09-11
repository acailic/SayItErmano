"""P0.4 first-PCM timer lifecycle: the timer is tracked and cancelled on
stop, cancel, and shutdown, and its callback validates recorder identity
through a safe interface — a stale timer (or one whose recorder was
replaced, or a recorder without .path) can never touch the current take
and never raises inside the timer thread."""
from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from fluidvoice import daemon as dm
from fluidvoice.config import DEFAULTS


class BareRecorder:
    """Records start/stop/cancel and exposes NOTHING else — not even
    .path. The pre-fix callback crashed on recorders like this inside the
    timer thread (the two baseline suite warnings)."""

    def __init__(self):
        self.started = 0
        self.stopped = 0
        self.cancelled = 0

    def start(self, path):
        self.started += 1

    def stop(self):
        self.stopped += 1
        return None

    def cancel(self):
        self.cancelled += 1


class PathRecorder(BareRecorder):
    """Same stub, but tracks the take path like the real Recorder."""

    def start(self, path):
        super().start(path)
        self.path = Path(path)

    def stop(self):
        self.stopped += 1
        self.path = None
        return None

    def cancel(self):
        super().cancel()
        self.path = None


def _make(tmp_path, monkeypatch, recorder_cls=BareRecorder):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["recording"]["first_pcm_timeout"] = 30.0  # never fires on its own
    notes = []
    monkeypatch.setattr(dm.ui, "notify",
                        lambda t, b="", **k: notes.append((t, b)))
    monkeypatch.setattr(dm.ui, "play_sound", lambda *a, **k: None)
    monkeypatch.setattr(dm.insertion, "active_window_class", lambda: "T")
    monkeypatch.setattr(dm.history_mod.paths, "history_file",
                        lambda: tmp_path / "h.jsonl")
    rec = recorder_cls()
    d = dm.Daemon(cfg, recorder=rec, backend_factory=lambda c: None,
                  use_hotkey=False, use_sounds=False)
    return SimpleNamespace(d=d, rec=rec, notes=notes)


@pytest.fixture()
def h(tmp_path, monkeypatch):
    return _make(tmp_path, monkeypatch)


def _start(h):
    with h.d._lock:
        h.d._capture.start_locked()
    assert h.d.recording is True
    assert h.d._capture.first_pcm_timer is not None


class TestFirstPcmTimerLifecycle:
    def test_start_tracks_the_timer(self, h):
        _start(h)
        assert h.rec.started == 1
        assert h.d._capture.first_pcm_timer.is_alive()
        h.d.cancel()  # hygiene: never leave the take's timers pending

    def test_stop_cancels_the_timer(self, h):
        _start(h)
        timer = h.d._capture.first_pcm_timer
        with h.d._lock:
            h.d._capture.stop_locked()
        assert h.d._capture.first_pcm_timer is None
        assert timer.finished.is_set()  # cancel() fired: it can never run

    def test_cancel_cancels_the_timer(self, h):
        _start(h)
        timer = h.d._capture.first_pcm_timer
        h.d.cancel()  # public path (Escape / socket cancel)
        assert h.d._capture.first_pcm_timer is None
        assert timer.finished.is_set()

    def test_shutdown_cancels_the_timer(self, h):
        _start(h)
        timer = h.d._capture.first_pcm_timer
        h.d.shutdown()
        assert h.d._capture.first_pcm_timer is None
        assert timer.finished.is_set()

    def test_new_take_replaces_the_old_timer(self, h):
        _start(h)
        first = h.d._capture.first_pcm_timer
        with h.d._lock:
            h.d._capture.stop_locked()
        _start(h)
        assert h.d._capture.first_pcm_timer is not first
        assert first.finished.is_set()
        h.d.cancel()  # hygiene: never leave the take's timers pending


class TestStaleCallbackSafety:
    """Even when the timer wins the race and runs, the callback must be a
    silent no-op for anything but its own live take."""

    def test_firing_after_cancel_is_noop(self, h):
        _start(h)
        recorder, wav = h.d._capture.first_pcm_timer.args
        h.d.cancel()
        h.d._capture.check_first_pcm(recorder, wav)  # the lost race
        assert h.rec.cancelled == 1  # only cancel(), nothing extra
        assert h.notes == [("SayItErmano", "Cancelled")]

    def test_bare_recorder_never_raises(self, h):
        # the live defect: a recorder without .path raised AttributeError
        # in the timer thread (unhandled -> the 2 baseline warnings)
        _start(h)
        recorder, wav = h.d._capture.first_pcm_timer.args
        h.d._capture.check_first_pcm(recorder, wav)
        assert h.d.recording is True  # live take, healthy: untouched
        assert h.rec.cancelled == 0
        assert h.notes == []
        h.d.cancel()  # hygiene: never leave the take's timers pending

    def test_replaced_recorder_is_ignored(self, h):
        _start(h)
        recorder, wav = h.d._capture.first_pcm_timer.args
        h.d._rebuild_recorder()  # settings change swapped self.recorder
        h.d._capture.check_first_pcm(recorder, wav)
        assert h.d.recording is True
        assert h.rec.cancelled == 0
        h.d.cancel()  # hygiene: never leave the take's timers pending

    def test_other_takes_wav_is_ignored(self, tmp_path, monkeypatch):
        h = _make(tmp_path, monkeypatch, recorder_cls=PathRecorder)
        _start(h)
        _old_rec, wav1 = h.d._capture.first_pcm_timer.args
        with h.d._lock:
            h.d._capture.stop_locked()
        _start(h)  # new take: same recorder, different wav
        h.d._capture.check_first_pcm(h.rec, wav1)  # stale wav: must not match
        assert h.d.recording is True
        assert h.rec.cancelled == 0
        h.d.cancel()  # hygiene: never leave the take's timers pending

    def test_silent_mic_still_stops_its_own_take(self, tmp_path, monkeypatch):
        # the watchdog's actual job still works when identity is valid:
        # a live-but-silent source is cancelled with the clear message
        class SilentRecorder(PathRecorder):
            def start(self, path):
                super().start(path)
                Path(path).write_bytes(b"\0" * 100)  # header only

        h = _make(tmp_path, monkeypatch, recorder_cls=SilentRecorder)
        _start(h)
        recorder, wav = h.d._capture.first_pcm_timer.args
        h.d._capture.check_first_pcm(recorder, wav)  # valid identity, silent file
        assert h.d.recording is False
        assert h.rec.cancelled == 1
        assert any("no audio" in (t + b).lower() for t, b in h.notes)
        assert h.d._capture.first_pcm_timer is None  # consumed by its own firing
