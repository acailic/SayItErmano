"""Suite-wide process-state guards.

Loaded before any test module is imported (pytest imports conftest first),
which is exactly what the Pillow warm-up below needs — and what the XDG
isolation below needs too: every paths.py resolution must already point
into the session tmp root by the time any test module runs.
"""
from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest


def _warm_pillow_freetype() -> None:
    """Load one Pillow truetype face before GTK/Pango can render text.

    Pillow wheels vendor their own FreeType/HarfBuzz (pillow.libs). If the
    first face load happens AFTER Pango has rendered in this process, every
    later Pillow text measurement returns garbage (negative or huge
    advances) - pill widths collapse and test_overlay renders
    hundred-megapixel canvases, but only when a gtkui test ran first, so it
    reads as an unexplained order dependency. Loading any face (and walking
    one glyph advance) before the first test pins the vendored library into
    a good state for the whole session. No-op when no system font exists
    (headless CI): overlay falls back to load_default there and no gtkui
    test runs without a display anyway.
    """
    try:
        from fluidvoice.overlay import _load_font

        font = _load_font(13, bold=True)
        if font is not None:
            font.getlength("warm")
    except Exception:
        pass  # measurement guards must never break collection


_warm_pillow_freetype()


# ---------------------------------------------------------------------------
# Session XDG isolation — the suite must NEVER write into the live data dir.
# ---------------------------------------------------------------------------
# History of the bug this guards against: command-mode tests built real
# CommandSessions whose default history appender fell through to
# history.append(), and paths.py resolved history.jsonl under
# ~/.local/share/sayit-ermano — 768 test rows polluted the production file
# (~192 suite runs x 4 rows), inflating `status` today-counts, the History
# window, exports and dictionary learning.
#
# paths.py reads the XDG env vars lazily on every call and nothing in the
# package caches a resolved path at import, so setting the env HERE (conftest
# import time, before any test module) is a complete seam — no production
# code change needed. Import-time (not a session fixture) so module-level
# path use in test modules and any spawned subprocess env are covered too.
# Hard assignment, not setdefault: a leaked outer XDG var must still lose.
# Per-test monkeypatch.setenv/monkeypatch.setattr overrides keep winning
# (they run later). tests/integration/conftest.py isolates its own env per
# test on top of this; it is untouched.
from fluidvoice import paths as _paths
from fluidvoice import runtime_tasks as _rt_mod

# Snapshot the REAL resolved paths BEFORE the override: these are the
# production locations the guard below watches (and what
# tests/test_conftest_isolation.py asserts stay untouched).
REAL_HISTORY_FILE = _paths.history_file()
REAL_SUGGESTIONS_FILE = _paths.dictionary_suggestions_file()
REAL_CONFIG_FILE = _paths.config_file()

TEST_XDG_ROOT = Path(tempfile.mkdtemp(prefix="sayit-test-xdg-"))
os.environ["XDG_DATA_HOME"] = str(TEST_XDG_ROOT / "data")
os.environ["XDG_CONFIG_HOME"] = str(TEST_XDG_ROOT / "config")
os.environ["XDG_CACHE_HOME"] = str(TEST_XDG_ROOT / "cache")
atexit.register(shutil.rmtree, TEST_XDG_ROOT, ignore_errors=True)

# Pin the session type to X11 for the whole suite: the Wayland port gates
# ONLY on this probe (fluidvoice/session.py), and the pre-existing tests
# exercise the xdotool/xclip paths — pinning means they take the X11
# branch no matter what display server the dev machine/CI runner sits on
# (headless runner: "unknown" also behaves as X11, but pinning makes it
# explicit and immune to a runner with a stray WAYLAND_DISPLAY). Per-test
# monkeypatch.setenv("XDG_SESSION_TYPE", "wayland") keeps winning.
os.environ["XDG_SESSION_TYPE"] = "x11"
os.environ.pop("WAYLAND_DISPLAY", None)

# Unit-tier network guard (quality plan Q1): under the canonical tier
# runner (FLUIDVOICE_TEST_TIER=unit), any outbound non-loopback socket
# raises before bytes hit the wire. Import-time install covers module
# scope and every thread the suite spawns; loopback fake servers are
# exempt. See tests/_network_guard.py for the full contract.
from tests import _network_guard as _net_guard

_net_guard._install()


def _fingerprint(p: Path):
    """None when the file is missing, else (mtime_ns, size, sha256) —
    catches appends (size/mtime) AND size-preserving rewrites (hash)."""
    try:
        data = p.read_bytes()
    except OSError:
        return None
    return (p.stat().st_mtime_ns, p.stat().st_size,
            hashlib.sha256(data).hexdigest())


_GUARDED_REAL_FILES = {
    "history": REAL_HISTORY_FILE,
    "suggestions": REAL_SUGGESTIONS_FILE,
    "config": REAL_CONFIG_FILE,
}


@pytest.fixture(scope="session", autouse=True)
def _real_data_untouched():
    """Tripwire: the whole suite must leave the real data/config files
    byte-identical (a missing file must stay missing — non-creation).
    Failing here raises during session teardown, so pytest reports it as a
    session ERROR even when every test passed — intended: a green run that
    mutated production is exactly the failure this exists to catch."""
    before = {name: _fingerprint(p) for name, p in _GUARDED_REAL_FILES.items()}
    yield
    after = {name: _fingerprint(p) for name, p in _GUARDED_REAL_FILES.items()}
    for name, was in before.items():
        assert after[name] == was, (
            f"suite wrote to the real {name} file "
            f"({_GUARDED_REAL_FILES[name]}): {was} -> {after[name]}")


# ---------------------------------------------------------------------------
# Runner hygiene — nothing a test starts may outlive the test.
# ---------------------------------------------------------------------------
# Phase-0 baseline (docs/research/2026-09-11-phase0-baseline-run.md): the
# suite printed "3310 passed" and then stayed alive for ~5 minutes before
# exiting 0. Tests that started a take through a real RuntimeTasks and
# never shut it down left the capture watchdog pending — a NON-daemon
# threading.Timer (default recording.max_seconds = 300 s) — so Python's
# threading._shutdown blocked interpreter exit until every leaked
# watchdog fired, each printing "max duration reached, stopping" / "no
# audio captured" AFTER the summary line, and the last one walked a
# stop→transcribe path against a None backend:
#     transcription failed: 'NoneType' object has no attribute 'name'
#
# Both leak classes are constructed inside production code under test
# (Daemon.__init__ builds its RuntimeTasks; recorder pipes and test
# servers are subprocess.Popen objects), so a per-test fixture written in
# any single test module cannot see them from the outside. The
# import-time seam below (same pattern as the XDG isolation above:
# conftest loads before any test module) tracks every RuntimeTasks
# instance and every real Popen the session creates; the autouse sweep
# after each test then guarantees teardown:
#   * RuntimeTasks → shutdown(timeout=5): cancels pending timers, joins
#     supervised threads (already-shut-down runtimes are no-ops);
#   * Popen → kill()+wait() when still running at teardown.
# A test that leaked live tasks is reaped AND recorded; the session-end
# gate in tests/test_runner_hygiene.py turns the list into a failing
# run, so a future leak reads as a red test instead of a 5-minute
# post-exit hang with mystery output.
_HYGIENE_LOCK = threading.Lock()
RUNTIME_TASKS: list = []   # every RuntimeTasks constructed this session
SPAWNED_PROCS: list = []   # every real subprocess.Popen this session
TEST_LEAKS: list = []      # (test node id, what was still alive)

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


@pytest.fixture(autouse=True)
def _reap_test_processes(request):
    """Guaranteed per-test teardown of every RuntimeTasks instance and
    real subprocess the test created. Autouse with no dependencies, so
    it sets up first and tears down LAST — after the test body and every
    other function-scoped fixture finalizer; code under test never
    observes it. Leaks are reaped, not forgiven: each is recorded for
    the session-end gate (tests/test_runner_hygiene.py)."""
    tasks_from = len(RUNTIME_TASKS)
    procs_from = len(SPAWNED_PROCS)
    yield
    node = request.node.nodeid
    for rt in RUNTIME_TASKS[tasks_from:]:
        # A leak is a task that would have outlived the test on its own:
        # a timer still PENDING at teardown (shutdown() had to cancel it
        # — left alone it would fire after the test, the baseline's 300 s
        # watchdog class) or a handle that missed the join deadline. A
        # thread that is merely winding down (stall monitor between
        # checks, a background reload) and joins cleanly is NOT a leak.
        report = rt.shutdown(timeout=5.0) if not rt.shut_down else {}
        offenders = sorted(set(report.get("cancelled", ()))
                           | set(report.get("timed_out", ())))
        for handle in live_task_handles(rt):
            if handle.task_name not in offenders:
                offenders.append(handle.task_name)
        if offenders:
            TEST_LEAKS.append(
                (node, f"RuntimeTasks tasks outlived the test: {offenders}"))
    for proc in SPAWNED_PROCS[procs_from:]:
        if proc.poll() is None:
            TEST_LEAKS.append((node, f"subprocess pid={proc.pid} alive"))
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    # prune reaped entries so the registries stay small over 3300+ tests
    with _HYGIENE_LOCK:
        RUNTIME_TASKS[:] = [rt for rt in RUNTIME_TASKS
                            if not rt.shut_down or live_task_handles(rt)]
        SPAWNED_PROCS[:] = [p for p in SPAWNED_PROCS if p.poll() is None]
