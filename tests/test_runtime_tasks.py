"""RuntimeTasks (P1.2): named timers/threads, cancellation, deadlines,
exception reporting and shutdown joins. The handles stay raw
threading.Timer/threading.Thread objects (args/finished/is_alive/cancel)
so daemon call sites - and the first-PCM lifecycle tests - keep their
identity-check semantics unchanged."""
from __future__ import annotations

import threading
import time

import pytest

from fluidvoice.runtime_tasks import (
    CANCELLED,
    DONE,
    PENDING,
    RUNNING,
    RuntimeTasks,
)


def make(**kw) -> tuple[RuntimeTasks, list]:
    """A runtime whose exception reports land in a list."""
    reported: list = []
    tasks = RuntimeTasks(on_exception=lambda n, e: reported.append((n, e)),
                         **kw)
    return tasks, reported


def wait_state(tasks: RuntimeTasks, name: str, *want: str,
               timeout: float = 5.0) -> str:
    """Poll state(name) until it matches (or fail the test)."""
    deadline = time.monotonic() + timeout
    state = None
    while time.monotonic() < deadline:
        state = tasks.state(name)
        if state in want:
            return state
        time.sleep(0.005)
    pytest.fail(f"task {name!r} never reached {want} (last: {state})")


# ---------------------------------------------------------------------------
# Timers: schedule / cancel / fire
# ---------------------------------------------------------------------------

class TestTimers:
    def test_cancel_before_fire_never_runs(self):
        tasks, _ = make()
        fired = []
        t = tasks.schedule("t", fired.append, 0.4, args=("x",))
        assert t.is_alive()  # started, waiting out the interval
        assert tasks.cancel("t") is True
        assert tasks.cancel("t") is False  # already cancelled
        time.sleep(0.6)  # past the original fire time
        assert fired == []
        assert tasks.state("t") == CANCELLED
        assert t.finished.is_set()  # raw-Timer surface: can never run
        t.join(timeout=2)
        assert not t.is_alive()

    def test_timer_fires_once_and_reaches_done(self):
        tasks, _ = make()
        fired = []
        t = tasks.schedule("t", fired.append, 0.03, args=(1,))
        wait_state(tasks, "t", DONE)
        t.join(timeout=2)
        assert fired == [1]
        assert tasks.cancel("t") is False  # fired: nothing left to cancel
        assert isinstance(t, threading.Timer)  # raw surface preserved
        assert t.args == (1,)

    def test_fire_then_cancel_is_safe(self):
        """A cancel that loses the race reports False and the callback
        still runs to completion - never a half-run."""
        tasks, _ = make()
        done = threading.Event()
        t = tasks.schedule("slow", done.set, 0.01)
        wait_state(tasks, "slow", RUNNING, DONE)
        assert tasks.cancel("slow") is False  # already firing/fired
        t.join(timeout=2)
        assert done.is_set()
        assert tasks.state("slow") == DONE

    def test_reschedule_same_name_cancels_predecessor(self):
        tasks, _ = make()
        fired = []
        first = tasks.schedule("t", lambda: fired.append("first"), 0.4)
        second = tasks.schedule("t", lambda: fired.append("second"), 0.4)
        assert first is not second
        assert first.finished.is_set()  # auto-cancelled by the re-schedule
        assert tasks.handle("t") is second
        time.sleep(0.6)
        assert fired == ["second"]  # predecessor can never run

    def test_cancel_all_cancels_only_pending_timers(self):
        tasks, _ = make()
        fired = []
        tasks.schedule("quick", fired.append, 0.01, args=(1,))  # fires
        tasks.schedule("p1", fired.append, 5.0)
        tasks.schedule("p2", fired.append, 5.0)
        wait_state(tasks, "quick", DONE)
        assert tasks.cancel_all() == ["p1", "p2"]
        assert tasks.cancel_all() == []
        time.sleep(0.1)
        assert len(fired) == 1  # only the quick one ran

    def test_cancel_unknown_or_thread_name_is_false(self):
        tasks, _ = make()
        assert tasks.cancel("nope") is False
        tasks.spawn("th", lambda: None)
        assert tasks.cancel("th") is False  # threads cannot be cancelled


# ---------------------------------------------------------------------------
# Exception reporting
# ---------------------------------------------------------------------------

class TestExceptionReporting:
    def test_timer_exception_is_reported_not_raised(self):
        tasks, reported = make()

        def boom():
            raise ValueError("boom")

        t = tasks.schedule("bad", boom, 0.01)
        wait_state(tasks, "bad", DONE)
        t.join(timeout=2)
        assert len(reported) == 1
        name, exc = reported[0]
        assert name == "bad" and isinstance(exc, ValueError)
        assert t.exc is exc  # recorded on the handle too

    def test_thread_exception_is_reported_not_raised(self):
        tasks, reported = make()

        def boom():
            raise KeyError("nope")

        t = tasks.spawn("bad-thread", boom)
        t.join(timeout=2)
        wait_state(tasks, "bad-thread", DONE)
        assert len(reported) == 1
        assert reported[0][0] == "bad-thread"
        assert isinstance(reported[0][1], KeyError)
        # the suite's thread-exception gate is proof no exception escaped

    def test_broken_reporter_does_not_break_the_worker(self):
        def bad_reporter(name, exc):
            raise RuntimeError("reporter itself is broken")

        tasks = RuntimeTasks(on_exception=bad_reporter)

        def boom():
            raise ValueError("boom")

        t = tasks.schedule("bad", boom, 0.01)
        t.join(timeout=2)
        wait_state(tasks, "bad", DONE)  # worker survived the reporter

    def test_default_reporter_logs_to_stderr(self, capsys):
        tasks = RuntimeTasks()  # no on_exception -> stderr fallback

        def boom():
            raise ValueError("kaput")

        t = tasks.schedule("loud", boom, 0.01)
        t.join(timeout=2)
        wait_state(tasks, "loud", DONE)
        err = capsys.readouterr().err
        assert "loud" in err and "kaput" in err


# ---------------------------------------------------------------------------
# Threads: spawn / states / joins
# ---------------------------------------------------------------------------

class TestThreads:
    def test_spawn_returns_named_daemon_thread(self):
        tasks, _ = make()
        done = threading.Event()
        t = tasks.spawn("idle-unload", done.wait, args=(2.0,))
        try:
            assert isinstance(t, threading.Thread)
            assert t.name == "fluidvoice-idle-unload"  # daemon compat name
            assert t.daemon is True
            assert tasks.state("idle-unload") in (PENDING, RUNNING)
        finally:
            done.set()
        t.join(timeout=2)
        assert tasks.state("idle-unload") == DONE

    def test_respawn_repoints_slot_and_keeps_predecessor_supervised(self):
        tasks, _ = make()
        gate = threading.Event()
        first = tasks.spawn("dup", gate.wait, args=(3.0,))
        second = tasks.spawn("dup", lambda: None)
        assert tasks.handle("dup") is second
        assert first.is_alive()  # still running, still joinable
        gate.set()
        first.join(timeout=2)
        second.join(timeout=2)
        assert tasks.state("dup") == DONE

    def test_join_by_name(self):
        tasks, _ = make()
        assert tasks.join("nope") is True  # absent: trivially joined
        t = tasks.spawn("sleeper", time.sleep, args=(0.1,))
        assert tasks.join("sleeper", timeout=2.0) is True
        assert not t.is_alive()
        long_ = tasks.spawn("stuck", time.sleep, args=(3.0,))
        try:
            assert tasks.join("stuck", timeout=0.05) is False
        finally:
            long_.join(timeout=3.0)

    def test_prepare_publishes_handle_before_start(self):
        """The daemon regression this guards: a worker started inside
        spawn() could run its first lines (e.g. busy = True) before the
        caller's field assignment landed. prepare() returns the handle
        unstarted so the field can be published first, exactly like the
        raw Timer/Thread code did."""
        tasks, _ = make()
        fired = []
        t = tasks.prepare_timer("pub", fired.append, 0.03, args=(1,))
        assert isinstance(t, threading.Timer)
        assert not t.is_alive()  # not started yet: safe to publish
        published = [t]          # caller assigns the field HERE
        assert published[0] is t
        t.start()
        wait_state(tasks, "pub", DONE)
        t.join(timeout=2)
        assert fired == [1]

    def test_prepared_but_never_started_handle_survives_shutdown(self):
        tasks, _ = make()
        t = tasks.prepare("ghost", time.sleep, args=(1.0,))
        assert t is not None and not t.is_alive()
        report = tasks.shutdown(timeout=1.0)  # must not join()/raise
        assert "ghost" in report["joined"]

    def test_cancel_between_prepare_and_start_prevents_the_run(self):
        tasks, _ = make()
        fired = []
        t = tasks.prepare_timer("late", fired.append, 0.01, args=(1,))
        assert tasks.cancel("late") is True
        t.start()  # a cancelled handle may still be started: it no-ops
        t.join(timeout=2)
        assert fired == []
        assert tasks.state("late") == CANCELLED

    def test_join_from_inside_the_task_skips_itself(self):
        tasks, _ = make()
        result: list = []
        t = tasks.spawn("self", lambda: result.append(
            tasks.join("self", timeout=0.1)))
        t.join(timeout=2)
        assert result == [True]  # no self-join deadlock


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

class TestShutdown:
    def test_shutdown_joins_all_threads(self):
        tasks, _ = make()
        threads = [tasks.spawn(f"w{i}", time.sleep, args=(0.05,))
                   for i in range(3)]
        report = tasks.shutdown(timeout=5.0)
        assert not report["timed_out"]
        assert set(report["joined"]) >= {"w0", "w1", "w2"}
        assert all(not t.is_alive() for t in threads)

    def test_shutdown_cancels_pending_timers_and_is_fast(self):
        tasks, _ = make()
        fired = []
        tasks.schedule("a", fired.append, 2.0)
        tasks.schedule("b", fired.append, 2.0)
        started = time.monotonic()
        report = tasks.shutdown(timeout=2.0)
        elapsed = time.monotonic() - started
        assert elapsed < 1.0  # cancelled, not waited out
        assert report["cancelled"] == ["a", "b"]
        time.sleep(0.2)
        assert fired == []  # timers cannot fire after shutdown

    def test_shutdown_waits_for_running_callback_bounded_by_deadline(self):
        tasks, _ = make()
        finished = threading.Event()

        def slow():
            time.sleep(0.3)
            finished.set()

        t = tasks.schedule("slow", slow, 0.01, deadline=2.0)
        wait_state(tasks, "slow", RUNNING)
        started = time.monotonic()
        report = tasks.shutdown(timeout=5.0)
        elapsed = time.monotonic() - started
        assert finished.is_set()  # the in-flight callback completed
        assert "slow" in report["joined"]
        assert "slow" not in report["timed_out"]
        assert elapsed < 2.0  # bounded by the deadline, not the timeout
        t.join(timeout=1.0)

    def test_deadline_enforced_on_join(self):
        tasks, _ = make()
        t = tasks.spawn("stuck", time.sleep, args=(1.5,), deadline=0.1)
        started = time.monotonic()
        report = tasks.shutdown(timeout=5.0)
        elapsed = time.monotonic() - started
        assert report["timed_out"] == ["stuck"]
        assert elapsed < 1.0  # the 0.1 s deadline capped the join
        assert t.is_alive()  # reported, never killed
        t.join(timeout=2.0)  # cleanup

    def test_overall_timeout_caps_the_whole_sweep(self):
        tasks, _ = make()
        stuck = [tasks.spawn(f"s{i}", time.sleep, args=(5.0,))
                 for i in range(3)]
        started = time.monotonic()
        report = tasks.shutdown(timeout=0.3)
        elapsed = time.monotonic() - started
        assert elapsed < 1.5
        assert report["timed_out"]  # at least one missed its join
        for t in stuck:
            t.join(timeout=6.0)

    def test_shutdown_idempotent(self):
        tasks, _ = make()
        tasks.spawn("w", time.sleep, args=(0.05,))
        first = tasks.shutdown(timeout=5.0)
        started = time.monotonic()
        second = tasks.shutdown(timeout=5.0)
        assert time.monotonic() - started < 0.2  # returns at once
        assert second == first

    def test_schedule_after_shutdown_is_refused(self):
        tasks, _ = make()
        fired = []
        tasks.shutdown(timeout=1.0)
        assert tasks.schedule("late", fired.append, 0.01) is None
        assert tasks.spawn("late-thread", fired.append) is None
        assert tasks.shut_down is True
        time.sleep(0.1)
        assert fired == []

    def test_shutdown_from_a_supervised_thread_skips_itself(self):
        tasks, _ = make()
        inner: list = []

        def worker():
            inner.append(tasks.shutdown(timeout=2.0))

        t = tasks.spawn("inner", worker)
        t.join(timeout=5.0)
        assert inner and not inner[0]["timed_out"]
        assert tasks.state("inner") == DONE  # reached terminal state
        # a later shutdown is idempotent: same stored report, no re-join
        outer = tasks.shutdown(timeout=2.0)
        assert outer == inner[0]

    def test_concurrent_shutdown_callers_all_get_the_report(self):
        tasks, _ = make()
        tasks.spawn("w", time.sleep, args=(0.05,))
        results: list = []
        threads = [threading.Thread(
            target=lambda: results.append(tasks.shutdown(timeout=5.0)))
            for _ in range(3)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=5.0)
        assert len(results) == 3
        assert not any(r["timed_out"] for r in results)

    def test_every_supervised_task_reaches_a_terminal_state(self):
        tasks, _ = make()
        tasks.schedule("fires", lambda: None, 0.02)
        tasks.spawn("runs", time.sleep, args=(0.02,))
        tasks.schedule("never", lambda: None, 5.0)
        wait_state(tasks, "fires", DONE)
        wait_state(tasks, "runs", DONE)
        tasks.shutdown(timeout=5.0)
        assert tasks.state("fires") == DONE
        assert tasks.state("never") == CANCELLED
        assert tasks.state("runs") == DONE


# ---------------------------------------------------------------------------
# Concurrent schedule/cancel races
# ---------------------------------------------------------------------------

class TestRaces:
    def test_schedule_cancel_storm(self):
        """Hammering one name with schedule+cancel from many threads must
        never double-fire a single handle, never raise, and leave the
        runtime usable afterwards."""
        tasks, reported = make()
        fires = {"n": 0}
        lock = threading.Lock()
        scheduled = {"n": 0}
        stop = threading.Event()

        def _fire():
            with lock:
                fires["n"] += 1

        def worker():
            while not stop.is_set():
                with lock:
                    scheduled["n"] += 1
                t = tasks.schedule("racy", _fire, 0.005)
                if t is not None:
                    tasks.cancel("racy")

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for th in threads:
            th.start()
        time.sleep(0.25)
        stop.set()
        for th in threads:
            th.join(timeout=5.0)
        # quiesce: nothing new may fire after shutdown
        report = tasks.shutdown(timeout=5.0)
        time.sleep(0.1)
        assert reported == []  # no exception ever escaped a guard
        with lock:
            assert fires["n"] <= scheduled["n"]  # a handle fires at most once
        assert tasks.state("racy") in (CANCELLED, DONE)
        assert not report["timed_out"]

    def test_cancel_during_callback_returns_false_and_callback_completes(self):
        tasks, _ = make()
        release = threading.Event()
        runs: list = []

        def slow():
            runs.append(1)
            release.wait(timeout=5.0)
            runs.append(2)

        t = tasks.schedule("c", slow, 0.01)
        wait_state(tasks, "c", RUNNING)
        assert tasks.cancel("c") is False  # already running: cannot stop it
        release.set()
        t.join(timeout=2)
        assert runs == [1, 2]  # ran exactly once, to completion
        assert tasks.state("c") == DONE
