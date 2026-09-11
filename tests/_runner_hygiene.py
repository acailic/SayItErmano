"""Globally-loaded runner-hygiene plugin (quality plan Q2, finding E2).

Phase-0 baseline (docs/research/2026-09-11-phase0-baseline-run.md): the
suite printed "3310 passed" and then stayed alive for ~5 minutes before
exiting 0 — leaked non-daemon capture watchdogs blocked interpreter exit.
The fix tracked every RuntimeTasks/Popen and gated leaks at session end,
but the gate lived in tests/test_runner_hygiene.py, so it only ran when
that module was collected: a focused single-file run, or a pytest-xdist
worker that never executed it, reported GREEN despite leaks (audit
repro, finding E2: 0/5 passed, exit 0 under `-n 2 --dist loadfile`).

This module is a PLUGIN, loaded by the repository-root conftest.py for
every run (and loadable standalone via `-p tests._runner_hygiene`), so
the verdict cannot be dodged by test selection:

* import-time seam: every RuntimeTasks instance and every real Popen the
  process creates is tracked (both are constructed inside production
  code under test, so per-module fixtures cannot see them);
* `_reap_test_processes` (autouse, per-test): guarantees teardown of
  everything the test created, records what had to be reaped;
* `_session_leak_gate` (autouse, session): last-resort sweep for
  collection-time leftovers, then the recorded leak list must be empty —
  a session teardown ERROR, which pytest reports (and exits nonzero) in
  serial runs AND in each xdist worker, whatever the selection was.
"""
from __future__ import annotations

import subprocess
import threading
import time

import pytest

from fluidvoice import runtime_tasks as _rt_mod

_HYGIENE_LOCK = threading.Lock()
RUNTIME_TASKS: list = []   # every RuntimeTasks constructed this session
SPAWNED_PROCS: list = []   # every real subprocess.Popen this session
TEST_LEAKS: list = []      # (test node id, what was still alive)
REAP_TIMES: dict[str, float] = {}  # node id -> monotonic() of its sweep

_RT_ORIG_INIT = _rt_mod.RuntimeTasks.__init__
_POPEN_ORIG_INIT = subprocess.Popen.__init__


def _tracked_rt_init(self, *args, **kwargs) -> None:
    _RT_ORIG_INIT(self, *args, **kwargs)
    with _HYGIENE_LOCK:
        RUNTIME_TASKS.append(self)


def _tracked_popen_init(self, *args, **kwargs) -> None:
    _POPEN_ORIG_INIT(self, *args, **kwargs)
    with _HYGIENE_LOCK:
        SPAWNED_PROCS.append(self)


_rt_mod.RuntimeTasks.__init__ = _tracked_rt_init
subprocess.Popen.__init__ = _tracked_popen_init


def live_task_handles(runtime) -> list:
    """The still-live (joinable) task handles of one RuntimeTasks."""
    return [h for h in getattr(runtime, "_live", []) if h.is_alive()]


def _sweep_runtime(rt, node: str, leaks: list) -> None:
    """Shutdown one runtime; record pending/late tasks as leaks."""
    report = rt.shutdown(timeout=5.0) if not rt.shut_down else {}
    offenders = sorted(set(report.get("cancelled", ()))
                       | set(report.get("timed_out", ())))
    for handle in live_task_handles(rt):
        if handle.task_name not in offenders:
            offenders.append(handle.task_name)
    if offenders:
        leaks.append(
            (node, f"RuntimeTasks tasks outlived the test: {offenders}"))


def _sweep_proc(proc, node: str, leaks: list) -> None:
    """Kill one still-running child; record it as a leak."""
    if proc.poll() is None:
        leaks.append((node, f"subprocess pid={proc.pid} alive"))
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


@pytest.fixture(autouse=True)
def _reap_test_processes(request):
    """Guaranteed per-test teardown of every RuntimeTasks instance and
    real subprocess the test created. Autouse with no dependencies, so
    it sets up first and tears down LAST — after the test body and every
    other function-scoped fixture finalizer; code under test never
    observes it (tests/test_runner_hygiene.py verifies that ordering
    dynamically instead of trusting this comment). Leaks are reaped, not
    forgiven: each is recorded for the session-end gate. The sweep also
    runs for tests that FAIL or ERROR in setup — teardown is unconditional."""
    tasks_from = len(RUNTIME_TASKS)
    procs_from = len(SPAWNED_PROCS)
    yield
    node = request.node.nodeid
    for rt in RUNTIME_TASKS[tasks_from:]:
        _sweep_runtime(rt, node, TEST_LEAKS)
    for proc in SPAWNED_PROCS[procs_from:]:
        _sweep_proc(proc, node, TEST_LEAKS)
    # prune reaped entries so the registries stay small over 3300+ tests
    with _HYGIENE_LOCK:
        RUNTIME_TASKS[:] = [rt for rt in RUNTIME_TASKS
                            if not rt.shut_down or live_task_handles(rt)]
        SPAWNED_PROCS[:] = [p for p in SPAWNED_PROCS if p.poll() is None]
    REAP_TIMES[node] = time.monotonic()


@pytest.fixture(scope="session", autouse=True)
def _session_leak_gate():
    """Session-end gate: after the last test (and every per-test sweep),
    nothing the suite started may still be alive. A last-resort sweep
    reaps stragglers created OUTSIDE any test (e.g. at import/collection
    time, before the first per-test window opens), then the recorded
    leak list must be empty — a leaking test fails the session here with
    the full list instead of the baseline's silent post-exit hang."""
    yield
    for rt in list(RUNTIME_TASKS):
        if not rt.shut_down:
            rt.shutdown(timeout=5.0)
        for handle in live_task_handles(rt):
            TEST_LEAKS.append(
                ("<session-end>",
                 f"RuntimeTasks task {handle.task_name!r} still live"))
    for proc in list(SPAWNED_PROCS):
        if proc.poll() is None:
            TEST_LEAKS.append(
                ("<session-end>", f"subprocess pid={proc.pid} still alive"))
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 - the assert below is the gate
                pass
    assert not TEST_LEAKS, (
        "tests leaked processes that outlived them (reaped by the "
        "hygiene sweep; fix the test teardown):\n  "
        + "\n  ".join(f"{node}: {what}" for node, what in TEST_LEAKS))
