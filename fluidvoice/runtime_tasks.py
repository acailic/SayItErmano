"""RuntimeTasks: the daemon's named, supervised timers and threads.

daemon.py used to spawn ~16 ad-hoc ``threading.Timer`` /
``threading.Thread`` objects scattered across methods. Every one of those
sites now routes through a single ``RuntimeTasks`` instance, which adds
what the raw primitives lack:

* **Names.** One live registration per name (re-registering a name first
  cancels its pending timer predecessor; a still-running predecessor
  thread stays supervised and is joined at shutdown). ``cancel(name)``,
  ``cancel_all()``, ``state(name)`` and ``join(name)`` address tasks
  without holding the handle.
* **Exception reporting.** Every callback runs inside a guard: an
  exception is passed to the ``on_exception`` reporter (the daemon logs
  it) and NEVER propagates unhandled out of the worker thread.
* **Cancel-before-fire.** A registered timer's callback can never START
  once ``cancel()``/``shutdown()`` decided to cancel it: the fire
  decision and the cancel decision both happen under the task's state
  lock, so there is no half-cancelled window (the identity-token idea
  from the P0.4 first-PCM fix, centralized). When firing and cancelling
  genuinely race, fire wins only if the callback already started;
  ``cancel()`` then returns False and the callback runs to completion
  (fire-then-cancel is safe).
* **Deadlines and shutdown joins.** ``deadline`` is the hard wall-clock
  bound ``shutdown()`` spends JOINING that task's worker thread; it does
  not (and cannot) abort the callback. ``shutdown(timeout)`` cancels all
  pending timers first, then joins every live task, each bounded by
  min(remaining overall timeout, task deadline). It is idempotent, safe
  to call from any thread (it never joins the calling thread), waits for
  an already-running callback (bounded by the deadline), and reports -
  never kills - tasks that miss their deadline. Scheduling after
  shutdown is refused (returns None) so late work cannot outlive the
  join sweep unnoticed.

Task states: ``pending`` -> ``running`` -> ``done`` for fired timers and
spawned threads; a cancelled timer goes ``pending`` -> ``cancelled``.
``done`` and ``cancelled`` are terminal. ``shutdown()`` returns a report
dict with ``cancelled``, ``joined`` and ``timed_out`` name lists plus the
elapsed seconds.

The handles are deliberately ``threading.Timer`` / ``threading.Thread``
subclasses: call sites (and the first-PCM lifecycle tests) keep doing
identity checks, field swaps, ``.args`` unpacking and raw ``.cancel()``
calls on them exactly as before.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from typing import TYPE_CHECKING, Any, Callable

__all__ = ["RuntimeTasks", "PENDING", "RUNNING", "CANCELLED", "DONE",
           "TERMINAL_STATES"]

PENDING = "pending"      # registered; timer waiting out its interval
RUNNING = "running"      # callback executing on its worker thread
CANCELLED = "cancelled"  # timer cancelled before its callback could start
DONE = "done"            # callback returned (or raised into the reporter)
TERMINAL_STATES = (CANCELLED, DONE)

Reporter = Callable[[str, BaseException], None]
Logger = Callable[[str], None]


def _stderr_log(msg: str) -> None:
    print(f"fluidvoice-runtime-tasks: {msg}", file=sys.stderr)


class _TaskState:
    """State bookkeeping shared by timer and thread handles.

    Lock discipline: ``_st_lock`` guards the state/exc transitions. The
    runtime is notified (``_task_terminal``) only with NO task lock held,
    and the runtime only ever acquires a task lock while already holding
    its registry lock (one direction, no cycles).

    Mixin contract: every host (``_TaskTimer``/``_TaskThread``) is a
    ``threading.Thread`` subclass; the members below belong to the host
    and are declared here for the type checker only.
    """

    if TYPE_CHECKING:
        _started: threading.Event

        def join(self, timeout: float | None = None) -> None: ...
        def is_alive(self) -> bool: ...

    def _init_task_state(self, task_name: str, deadline: float) -> None:
        self.task_name = task_name
        self.deadline = deadline
        self._st_lock = threading.Lock()
        self._state = PENDING
        self.exc: BaseException | None = None

    @property
    def state(self) -> str:
        with self._st_lock:
            return self._state

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES


class _TaskTimer(_TaskState, threading.Timer):
    """Named one-shot timer handle (see module docstring for semantics)."""

    def __init__(self, runtime: "RuntimeTasks", name: str,
                 interval: float, fn: Callable[..., Any],
                 args: tuple, kwargs: dict, deadline: float):
        threading.Timer.__init__(self, interval, fn, args=args,
                                 kwargs=kwargs)
        self.name = f"fluidvoice-timer-{name}"
        self._runtime = runtime
        self._init_task_state(name, deadline)

    def run(self) -> None:  # overrides Timer.run; self.function is ours
        self.finished.wait(self.interval)
        with self._st_lock:
            if self._state != PENDING or self.finished.is_set():
                if self._state == PENDING:  # cancelled before firing
                    self._state = CANCELLED
                skip = self._state == CANCELLED
            else:
                self._state = RUNNING
                skip = False
        if skip:
            self._runtime._task_terminal(self)
            return
        try:
            self.function(*self.args, **self.kwargs)
        except Exception as e:  # noqa: BLE001 - reported, never unhandled
            with self._st_lock:
                self.exc = e
            self._runtime._report_exception(self, e)
        finally:
            with self._st_lock:
                self._state = DONE
            self.finished.set()
            self._runtime._task_terminal(self)

    def cancel(self) -> bool:  # type: ignore[override]  # bool beats None
        """Timer-compatible cancel(): sets ``finished`` and marks the
        task cancelled. True iff a pending callback was actually stopped
        (False once it fired or was already cancelled)."""
        with self._st_lock:
            if self._state != PENDING:
                return False
            self._state = CANCELLED
            self.finished.set()
        self._runtime._task_terminal(self)
        return True


class _TaskThread(_TaskState, threading.Thread):
    """Named supervised thread handle (see module docstring)."""

    def __init__(self, runtime: "RuntimeTasks", name: str,
                 fn: Callable[..., Any], args: tuple, kwargs: dict,
                 thread_name: str, daemon: bool, deadline: float):
        threading.Thread.__init__(self, name=thread_name, daemon=daemon)
        self._runtime = runtime
        self._fn = fn
        self._fn_args = args
        self._fn_kwargs = kwargs
        self._init_task_state(name, deadline)

    def run(self) -> None:  # overrides Thread.run
        with self._st_lock:
            self._state = RUNNING
        try:
            self._fn(*self._fn_args, **self._fn_kwargs)
        except Exception as e:  # noqa: BLE001 - reported, never unhandled
            with self._st_lock:
                self.exc = e
            self._runtime._report_exception(self, e)
        finally:
            with self._st_lock:
                self._state = DONE
            self._runtime._task_terminal(self)


class RuntimeTasks:
    """Registry of named one-shot timers and supervised threads.

    One instance is owned by the Daemon (composition root). All methods
    are safe to call from any thread.
    """

    def __init__(self, *, on_exception: Reporter | None = None,
                 log: Logger | None = None,
                 default_deadline: float = 5.0) -> None:
        self._on_exception = on_exception
        self._log = log or _stderr_log
        self._default_deadline = default_deadline
        self._lock = threading.RLock()  # registry -> task direction only
        self._tasks: dict[str, _TaskState] = {}  # name -> latest handle
        self._live: list[_TaskState] = []  # every not-yet-reaped handle
        self._shutting_down = False
        self._report: dict[str, Any] | None = None
        self._done = threading.Event()

    # -- registration --------------------------------------------------------

    def prepare_timer(self, name: str, fn: Callable[..., Any], delay: float,
                      *, args: Any = (), kwargs: dict | None = None,
                      deadline: float | None = None) -> _TaskTimer | None:
        """Register a named one-shot timer WITHOUT starting it: create,
        assign the handle to its field, then ``handle.start()``. This
        restores the raw primitives' publish-before-start ordering - a
        concurrently-reading thread must never see the callback already
        running while the handle field is still unset. Re-registering a
        name cancels its pending predecessor. Refused (None) after
        shutdown."""
        with self._lock:
            if self._shutting_down:
                self._refuse("prepare", name)
                return None
            old = self._tasks.get(name)
            if isinstance(old, _TaskTimer):
                old.cancel()
            self._prune_locked()
            timer = _TaskTimer(self, name, float(delay), fn, tuple(args),
                               dict(kwargs or {}), self._deadline(deadline))
            self._tasks[name] = timer
            self._live.append(timer)
            return timer

    def schedule(self, name: str, fn: Callable[..., Any], delay: float, *,
                 args: Any = (), kwargs: dict | None = None,
                 deadline: float | None = None) -> _TaskTimer | None:
        """Register and START a named one-shot timer firing
        ``fn(*args, **kwargs)`` after ``delay`` seconds (daemon thread).
        For handles published into a field another thread may read,
        prefer ``prepare_timer`` + ``handle.start()``. Re-registering a
        name cancels its pending predecessor. Returns the timer handle
        (a threading.Timer) or None when the runtime is shut down."""
        timer = self.prepare_timer(name, fn, delay, args=args,
                                   kwargs=kwargs, deadline=deadline)
        if timer is not None:
            timer.start()
        return timer

    def prepare(self, name: str, fn: Callable[..., Any], *,
                args: Any = (), kwargs: dict | None = None,
                thread_name: str | None = None, daemon: bool = True,
                deadline: float | None = None) -> _TaskThread | None:
        """Register a named supervised thread WITHOUT starting it (see
        ``prepare_timer`` for why: publish the handle, then ``
        handle.start()``). Refused (None) after shutdown."""
        with self._lock:
            if self._shutting_down:
                self._refuse("prepare", name)
                return None
            self._prune_locked()
            thread = _TaskThread(self, name, fn, tuple(args),
                                 dict(kwargs or {}),
                                 thread_name or f"fluidvoice-{name}",
                                 daemon, self._deadline(deadline))
            self._tasks[name] = thread
            self._live.append(thread)
            return thread

    def spawn(self, name: str, fn: Callable[..., Any], *,
              args: Any = (), kwargs: dict | None = None,
              thread_name: str | None = None, daemon: bool = True,
              deadline: float | None = None) -> _TaskThread | None:
        """Register and START a named supervised thread running
        ``fn(*args, **kwargs)``. The thread is named ``thread_name``
        (default ``fluidvoice-<name>``); re-registering a name repoints
        the named slot while a still-running predecessor stays supervised
        for the shutdown join. For handles published into a field
        another thread may read, prefer ``prepare`` + ``
        handle.start()``. Returns the thread handle or None when the
        runtime is shut down."""
        thread = self.prepare(name, fn, args=args, kwargs=kwargs,
                              thread_name=thread_name, daemon=daemon,
                              deadline=deadline)
        if thread is not None:
            thread.start()
        return thread

    # -- queries ---------------------------------------------------------------

    def handle(self, name: str) -> _TaskState | None:
        """The latest handle registered under ``name`` (None if never)."""
        with self._lock:
            return self._tasks.get(name)

    def state(self, name: str) -> str | None:
        """pending/running/cancelled/done, or None if never registered."""
        h = self.handle(name)
        return h.state if h is not None else None

    @property
    def shut_down(self) -> bool:
        """True once shutdown() has been requested."""
        return self._shutting_down

    # -- cancellation ------------------------------------------------------------

    def cancel(self, name: str) -> bool:
        """Cancel the pending timer registered under ``name``. True iff a
        pending callback was stopped; False for unknown names, already
        fired/cancelled timers, and threads (which cannot be cancelled -
        use join/shutdown)."""
        with self._lock:
            h = self._tasks.get(name)
        if isinstance(h, _TaskTimer):
            return h.cancel()
        return False

    def cancel_all(self) -> list[str]:
        """Cancel every pending timer (threads are never killed).
        Returns the sorted names that were actually cancelled."""
        with self._lock:
            pending = [(n, h) for n, h in self._tasks.items()
                       if isinstance(h, _TaskTimer)]
        return sorted(n for n, h in pending if h.cancel())

    def join(self, name: str, timeout: float | None = None) -> bool:
        """Join the current task under ``name`` (bounded by ``timeout``).
        True when it is absent, already dead, never started, or the
        calling thread itself; False when the join timed out with it
        still alive."""
        with self._lock:
            h = self._tasks.get(name)
        if h is None or h is threading.current_thread() \
                or not h._started.is_set():
            return True
        h.join(timeout)
        return not h.is_alive()

    # -- shutdown --------------------------------------------------------------

    def shutdown(self, timeout: float | None = None) -> dict[str, Any]:
        """Deterministic teardown: cancel every pending timer, then join
        every live task.

        ``timeout`` bounds the WHOLE sweep (None = unbounded overall;
        each task is still capped by its own deadline). Per task the
        join waits at most min(remaining overall, task deadline). The
        calling thread is never joined (safe to call from a supervised
        worker); concurrent callers wait for the owner's report. Returns
        ``{"cancelled": [...], "joined": [...], "timed_out": [...],
        "elapsed_s": float}`` - idempotent: later calls return the
        stored report.
        """
        with self._lock:
            if self._done.is_set():
                return self._report or {}
            owner = not self._shutting_down
            self._shutting_down = True
        if not owner:
            self._done.wait(timeout)
            with self._lock:
                return self._report or {}
        started = time.monotonic()
        cancelled = self.cancel_all()
        with self._lock:
            snapshot = list(self._live)
        joined: set[str] = set()
        timed_out: set[str] = set()
        for h in snapshot:
            if h is threading.current_thread():
                continue  # never join the caller (deadlock)
            if not h._started.is_set() or \
                    (h.terminal and not h.is_alive()):
                # never started (prepared, abandoned) or already dead
                joined.add(h.task_name)
                continue
            remaining = None if timeout is None else \
                timeout - (time.monotonic() - started)
            if remaining is not None and remaining <= 0:
                timed_out.add(h.task_name)
                continue
            cap = h.deadline if remaining is None \
                else min(remaining, h.deadline)
            h.join(cap)
            if h.is_alive():
                timed_out.add(h.task_name)
            else:
                joined.add(h.task_name)
        report: dict[str, Any] = {
            "cancelled": cancelled,
            "joined": sorted(joined),
            "timed_out": sorted(timed_out),
            "elapsed_s": round(time.monotonic() - started, 3),
        }
        with self._lock:
            self._report = report
        self._done.set()
        return report

    # -- internals (called by the handles) ------------------------------------

    def _report_exception(self, h: _TaskState, exc: BaseException) -> None:
        name = getattr(h, "task_name", "?")
        if self._on_exception is not None:
            try:
                self._on_exception(name, exc)
            except Exception as e:  # noqa: BLE001 - reporter must not kill
                tb = "".join(traceback.format_exception(exc)).rstrip()
                self._safe_log(f"task {name!r} raised {exc!r} and the "
                               f"reporter failed too ({e!r}):\n{tb}")
        else:
            tb = "".join(traceback.format_exception(exc)).rstrip()
            self._safe_log(f"task {name!r} raised "
                           f"{exc.__class__.__name__}: {exc}\n{tb}")

    def _task_terminal(self, h: _TaskState) -> None:
        """Handle reached a terminal state: drop dead siblings."""
        with self._lock:
            self._prune_locked()

    def _refuse(self, verb: str, name: str) -> None:
        self._safe_log(f"WARN refusing to {verb} task {name!r}: "
                       "runtime is shut down")

    def _safe_log(self, msg: str) -> None:
        try:
            self._log(msg)
        except Exception:  # noqa: BLE001 - logging must never raise here
            pass

    def _deadline(self, d: float | None) -> float:
        return self._default_deadline if d is None else float(d)

    def _prune_locked(self) -> None:
        # keep: anything not terminal, or terminal but still joinable;
        # a never-started handle is kept (its caller may still start it)
        self._live = [h for h in self._live
                      if not h.terminal or h.is_alive()
                      or not h._started.is_set()]
