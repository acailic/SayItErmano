"""Runner hygiene: the suite must leave nothing running behind it.

Phase-0 baseline (docs/research/2026-09-11-phase0-baseline-run.md): the
pytest process stayed alive ~5 minutes after the summary line. Tests
that started a take through a real RuntimeTasks and never shut it down
left the capture watchdog pending — a NON-daemon threading.Timer
(default recording.max_seconds = 300 s) — so threading._shutdown blocked
interpreter exit until every leaked watchdog fired, printing
"max duration reached, stopping" / "no audio captured" pairs after the
run, and the last one walked a stop→transcribe path against a None
backend:

    [sayit-ermano] transcription failed: 'NoneType' object has no
    attribute 'name'

tests/conftest.py now tracks every RuntimeTasks instance and every real
subprocess.Popen the session creates (import-time seam — both are built
inside production code under test) and reaps them after each test. The
session-end gate lives in the conftest TOO (Q2/E2: a gate inside a test
module is only loaded when that module is collected — focused and
loadfile-distributed runs could leak silently). This module pins the
machinery:

* mid-session, the registries never hold a live task or a live process
  (the per-test sweeps keep them clean, so these pass wherever they run
  in a single session — they do not rely on running last);
* tests/test_leak_gate_meta.py drives REAL pytest subprocesses to pin the
  exit-status behaviour of the conftest gate (single-file and -n 2 runs);
* the per-test sweep finalizes AFTER every other function-scoped fixture
  (so nothing tears down into a half-reaped world);
* ensure_backend() is None-safe for None-returning factories — the
  exact code path behind the baseline's error line (load_backend never
  returns None in production; test stubs do).
"""
from __future__ import annotations

import copy
import os
import subprocess
import sys
import threading

import pytest

from fluidvoice import engine_manager
from fluidvoice.config import DEFAULTS
from fluidvoice.runtime_tasks import RuntimeTasks
from tests.conftest import RUNTIME_TASKS, SPAWNED_PROCS, live_task_handles


def _child_pids() -> list[int]:
    """Direct children of this interpreter, straight from /proc (no ps)."""
    me = os.getpid()
    children = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", encoding="utf-8") as fh:
                fields = fh.read().rsplit(") ", 1)[1].split()
        except OSError:
            continue  # process died between listdir and read
        if int(fields[1]) == me:
            children.append(int(entry))
    return children


class TestRegistryCleanMidSession:
    """The conftest sweep runs after EVERY test, so the registries it
    maintains are clean at any point mid-session — not just at the end.
    These assertions hold wherever this module lands in the run order."""

    def test_no_runtime_tasks_hold_live_handles(self):
        for rt in RUNTIME_TASKS:
            live = live_task_handles(rt)
            assert not live, (
                f"RuntimeTasks with live tasks outlived its test: "
                f"{[h.task_name for h in live]}")

    def test_no_tracked_subprocess_is_alive(self):
        for proc in SPAWNED_PROCS:
            assert proc.poll() is not None, (
                f"subprocess pid={proc.pid} still alive after its test")

    def test_no_child_process_of_this_interpreter(self):
        assert _child_pids() == [], (
            f"orphaned child processes of the pytest process: "
            f"{_child_pids()}")


@pytest.fixture()
def _order_probe_child():
    """Q2: spawn a child that lives through the test body, and whose
    teardown asserts the conftest reap sweep has NOT yet run."""
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; time.sleep(60); sys.exit(0)"])
    yield proc
    try:
        assert proc.poll() is None, (
            "conftest reap sweep ran BEFORE this fixture's teardown — "
            "fixture finalization order is wrong; the sweep must be last")
    finally:
        proc.kill()
        proc.wait()


def test_reap_sweep_finalizes_after_other_function_fixtures(
        _order_probe_child):
    """The conftest sweep (`_reap_test_processes`, autouse) must tear down
    AFTER every other function-scoped fixture. This test's fixture holds a
    live child through the body; if the sweep finalized first, the child
    would be reaped (and recorded as a leak) before this fixture's own
    teardown — verified there, not just promised by a comment."""
    assert _order_probe_child.poll() is None


class TestNoneBackendGuard:
    """Regression for the baseline's post-run error line.

    The leaked watchdog's stop→transcribe path reached
    SpeechEngineManager.ensure_backend() with a factory that returns
    None (test stubs; load_backend itself never returns None — it
    raises), and the load-success log line read ``self.backend.name``
    unconditionally, raising AttributeError('NoneType' object has no
    attribute 'name') — surfaced by Daemon._process as
    "transcription failed: 'NoneType' object has no attribute 'name'".
    ensure_backend must survive a None factory without that crash."""

    def test_ensure_backend_survives_a_none_factory(self):
        logs: list[str] = []
        mgr = engine_manager.SpeechEngineManager(
            copy.deepcopy(DEFAULTS), RuntimeTasks(),
            backend_factory=lambda cfg: None,
            lock=threading.Lock(), log=logs.append)
        assert mgr.ensure_backend() is None  # returned, not raised
        assert mgr.backend is None
        # no misleading "loaded" line for a backend that is not there
        assert not any("speech backend:" in m for m in logs)
        assert mgr.ensure_backend() is None  # retry path is stable too
