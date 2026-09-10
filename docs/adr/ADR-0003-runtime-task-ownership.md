# ADR-0003: Runtime tasks are owned, tracked and cancellable

Date: 2026-09-10
Status: Accepted

## Context

The daemon is full of long-lived asynchronous work: capture, preview
engine, watchdog timers (first-PCM, max-duration, stall, command
timeout), mic poller, update checker, and since P0.2 a fixed pool of
control-server worker threads. The 2026-09-10 baseline had two classes
of defect: (a) the first-PCM watchdog timer was fire-and-forget — it
was never cancelled on stop/cancel/shutdown and its callback validated
its take by touching the *current* recorder, raising inside the timer
thread (the suite's two standing warnings) and risking acting on a
recorder that was not its own; (b) unhandled thread exceptions were
printed, not failed. The improvement program (P0.4 "Close lifecycle and
protocol defects", and P1.2 `RuntimeTasks` ahead) requires every
supervised task to reach a terminal state.

## Decision

Every timer/thread the daemon creates is **owned by the state that
started it and cancelled at that state's end**; nothing asynchronous
outlives its take. As shipped in P0.4 (b05ebed, 412e1a9, e9a5364):

- The first-PCM timer is tracked (`self._first_pcm_timer`) and
  cancelled in every take-end path (stop, cancel, shutdown); a cancelled
  timer is the guarantee it never fires stale.
- Its callback validates recorder **identity through a safe interface**
  (a bound probe contract) instead of touching whatever recorder is
  current after a settings rebuild.
- Unhandled thread exceptions fail the test suite (error filter in
  `[tool.pytest.ini_options]`; expected test-server disconnect noise is
  silenced at the source).
- `ControlServer.shutdown()` joins its accept thread and all eight
  workers deterministically; recorder stop keeps the
  SIGINT→SIGTERM→SIGKILL escalation.

P1.2 will formalize this into a `RuntimeTasks` coordinator (named
threads/timers, cancellation, deadlines, exception reporting, shutdown
joins) — this ADR records the invariant it must preserve, not the
current mechanism.

## Consequences

- Timers can no longer fire after cancel/shutdown; every supervised
  task reaches a terminal state (test-enforced via the thread-exception
  gate).
- Take-end code must remember to cancel; the future `RuntimeTasks`
  seam exists so that rule lives in one place instead of per call site.
- Adding an unhandled-exception-producing background path now breaks
  CI immediately rather than shipping stderr noise.
