# Plan: lock-watch session resolution under the systemd user unit (ListSessions fallback)

Request: `requests/lock-watch-userslice.md` (verbatim in this session's prompt).
Spec: `specs/aa633fbd_lock-watch-userslice.md` (this file).

## Problem

Live bug (2026-09-05 02:50, daily-driver): daemon started via
`systemctl --user start sayit-ermano` logs

```
lock watch: session lookup failed (DBusException: org.freedesktop.login1.NoSessionForPID: ...)
lock watch unavailable (no logind session for this process - headless?)
```

and pause-when-locked is inert. Cause: processes under the user manager live
in `user.slice` (`user@1000.service`), **not** in a `session-XX.scope`, so
`GetSessionByPID(os.getpid())` finds nothing. Resolution by own PID only works
for session-scoped launches (the old GNOME autostart). The daemon may be
started both ways; lock suppression must work in both.

Where the code actually lives today (the request names "fluidvoice/daemon.py
lock-watch section from commit c2a95f6", but that block has since been
extracted): **`fluidvoice/lockmon.py`** holds `LockMonitor` — resolution
(`_session_path`), bus wiring (`_run`), the reconcile poll; `fluidvoice/daemon.py`
`_start_lockmon`/`_on_locked`/`_apply_lock_setting` only start and gate on it
(daemon.py:544-628). Tests: `tests/test_lock_suppression.py`.

Baseline: `.venv/bin/python -m pytest -q tests --ignore=tests/integration` →
**1432 passed** (verified on this machine). Every phase below must leave it
green.

## Target behavior

Resolution chain for the session object path (first hit wins, each hit
validated against the live bus):

1. `$XDG_SESSION_ID` → `/org/freedesktop/login1/session/<id>` (unchanged),
   now **validated** (see below) — a stale imported env var after
   logout/login must not pin a dead session.
2. `Manager.GetSessionByPID(os.getpid())` (unchanged) — works for
   session-scoped launches. `NoSessionForPID` is now an *expected* outcome
   under the user unit: swallow it quietly and continue the chain; it must
   NOT log a WARN when a later step rescues the lookup.
3. **NEW — `Manager.ListSessions()`** → array of
   `(session_id, uid, username, seat, object_path)`. Filter to
   `int(uid) == os.getuid()` and logind `Class == "user"` (greeter/background
   rows skipped). Keep only **graphical** sessions: `Type` in
   `{"x11", "wayland"}`. Rank survivors by
   `(STATE_RANK[state], FIFO(session_id), list_index)` where
   `STATE_RANK = {"active": 0, "online": 1, else: 2}` and
   `FIFO(id)` = numeric value of the trailing digits of the session id
   (logind ids are monotonic counters, `c1 < c2 < …`; regex `(\d+)$`,
   string compare as fallback) — i.e. prefer active, then online, ties to the
   earliest-created. Pick the first.

   *Documented deviation from the request's literal ordering* ("prefer
   state=active, then type=desktop/x11/wayland"): graphical type is a hard
   **filter**, not a secondary rank — an *active tty* session of the same UID
   must never shadow an *online graphical* session, because a tty session has
   no lock surface to watch. "type=desktop" is not a logind `Type` value
   (real values: x11/wayland/tty/mir/unspecified); graphical == x11|wayland.

Validation of a resolved path (steps 1-3): one property read on the candidate
session object (`Properties.Get(SESSION_IFACE, "State")`, timeout=5) — an
`UnknownObject`/DBus error means the path is dead; continue the chain. Needed
because `XDG_SESSION_ID` in the user manager can survive a logout.

- Chain finds a session → subscribe exactly as today: session `Lock`/`Unlock`
  (`connect_to_signal`), path-scoped `PropertiesChanged` for `LockedHint`,
  plus the existing sources. Log ONE info line, e.g.
  `lock watch: session c3 via ListSessions (active, x11, uid 1000)`. **No WARN
  lines on the user unit anymore.**
- Chain finds nothing (genuinely headless) → **sleep-only mode**: keep the
  current WARN line `lock watch unavailable (no logind session for this
  process - headless?)`, but the monitor still runs with the **manager-level
  `PrepareForSleep`** signal wired (belt-and-braces: suspend still gates),
  `start()` returns True, and the daemon logs
  `lock watch active (suspend-only: no graphical session)`.

Manager-level wiring is reordered to happen FIRST in `_run` (it is
session-independent): `PrepareForSleep` (existing) plus **NEW**
`SessionRemoved(s, o)` and `SessionNew(s, o)` on
`org.freedesktop.login1.Manager`.

Re-resolve (logout/login swaps the watched session):

- `_on_session_removed(sid, path)`: if `str(path) == self.session_path` →
  log, detach the session subscriptions, re-run the full resolution chain; a
  hit → watch the new session (reconcile once; `_apply` dedups so no spurious
  flip), a miss → sleep-only mode.
- `_on_session_new(sid, path)`: if currently in sleep-only mode → opportunistic
  re-resolve (the new session may be ours).
- Re-entrancy guard (`self._resolving` bool) so overlapping signals cannot
  double-subscribe.
- Belt-and-braces: in `_reconcile`, a failed `LockedHint` read whose error
  looks like `UnknownObject` triggers the same re-resolve (covers a missed
  SessionRemoved).
- Detach: store receiver tokens and call
  `bus.remove_signal_receiver(handler, signal_name, dbus_interface, path=old)`
  per receiver inside try/except. Correctness does not depend on removal
  succeeding: the old receivers are path-scoped to a dead path and can never
  fire — removal is hygiene.

Out of scope (request): logind inhibitory locks, multi-seat policy beyond the
order above, Wayland-specific lock surfaces, any change to the `_apply`
transition state machine.

## Files to touch

| File | Change |
|---|---|
| `fluidvoice/lockmon.py` | Resolution chain + validation, `pick_graphical_session`, manager-first wiring helpers, sleep-only mode, re-resolve handlers, `status()`, module-docstring correction (its claim that GetSessionByPID "works for systemd-user daemons" is the bug's twin — rewrite the session-resolution paragraph) |
| `fluidvoice/daemon.py` | `_start_lockmon`: success log names the session + `via`; sleep-only log line. `handle_request` `"status"`: add flat `"lock_watch"` dict (additive key) |
| `fluidvoice/doctor.py` | NEW `_lock_watch_lines(cfg)` printed in the control-socket block of `run()` (after the mouse-PTT lines) |
| `tests/test_lock_suppression.py` | New test classes + fake bus/manager/session objects (see tests) |
| `docs/STATUS.md` | Lock-suppression section: user-unit resolution, sleep-only mode, re-resolve, doctor line; live-verification result note |
| `docs/BEHAVIOR-SPEC.md` | One-line touch of the locked-screen suppression entry (§ line ~280) if it names the resolution constraint |
| `README.md` | Only if the lock-suppression blurb implies autostart-only resolution — check and adjust one sentence |

## Code shape (lockmon.py)

```python
GRAPHICAL_TYPES = ("x11", "wayland")
STATE_RANK = {"active": 0, "online": 1}          # else 2

def _fifo_key(sid: str):
    m = re.search(r"(\d+)$", str(sid))
    return (int(m.group(1)), str(sid))

def pick_graphical_session(entries, uid, props_of, log=None):
    """entries: [(sid, uid, user, seat, path)] as returned by ListSessions.
    props_of(path) -> {"State":…, "Type":…, "Class":…} (bus-backed, test-seam).
    Returns (path, meta) for the best same-UID active graphical user
    session, else None. Pure ranking — no imports, no bus."""
```

`LockMonitor` additions/changes:

- `_session_path(bus=None, manager=None)` — keep the existing test seam
  signature; the fake/existing manager now also implements `ListSessions`
  (real code builds the manager proxy from `bus` exactly as today). Chain:
  env (validated) → pid (quiet on NoSessionForPID) → ListSessions pick
  (validated). Sets `self._via` ∈ `{"env", "pid", "list"}` and returns the
  path or None. On total failure logs `lock watch: session lookup failed
  (…)` **once** with the first underlying error (keeps the existing test
  assertion `test_pid_fallback_error_returns_none` meaningful); when a later
  step rescues an earlier failure, nothing is logged.
- `_wire_manager(manager)` — PrepareForSleep + SessionRemoved + SessionNew
  (extracted from `_run`; plain method so tests call it with a fake proxy).
- `_watch_session(bus, path, via)` — get_object, Lock/Unlock subscription,
  path-scoped PropertiesChanged, store tokens + `session_path`/`_via`,
  initial `_reconcile()`. Shared by `_run` and re-resolve.
- `_detach_session(bus)` — remove_signal_receiver per token, best-effort;
  clear `session_path`/`_session_obj`.
- `_on_session_removed(sid, path)`, `_on_session_new(sid, path)` — guarded
  re-resolve as described.
- `_run` — reorder: imports → `DBusGMainLoop(set_as_default=True)` →
  `SystemBus` → `_wire_manager` → resolve → `_watch_session` OR headless WARN
  → screensaver fallback (unchanged) → reconcile timeout (keep it
  unconditional; `_reconcile` no-ops without a session object) →
  `started=True` in **both** session and sleep-only modes.
- `status()` → `{"active": bool, "mode": "session"|"sleep-only"|"off",
  "session": path|None, "via": "env"|"pid"|"list"|None, "locked": bool}`.

daemon.py `handle_request` status addition:

```python
"lock_watch": (self._lockmon.status() if self._lockmon is not None else
               {"active": False, "mode": "off", "session": None,
                "via": None, "locked": self._locked}),
```

doctor.py (same pattern as `_hotkey_grab_line`/`_mouse_ptt_lines` — control
socket `status` query, injectable-safe):

```python
def _lock_watch_lines(cfg: dict) -> list[str]:
    # disabled -> "lock watch: disabled (general.pause_when_locked = false)"
    # daemon down -> "lock watch: unknown (daemon down)"
    # mode=session -> "lock watch: ok (watching session c3 via ListSessions)"
    # mode=sleep-only -> "lock watch: suspend-only (no graphical session)"
    # missing key (older daemon) -> "lock watch: unknown (older daemon)"
```

## Tests (tests/test_lock_suppression.py; fake bus objects, no real D-Bus)

Fakes (module-level, reused): `_FakeManager` (configurable
`GetSessionByPID` raising/returning, `ListSessions` rows),
`_FakeSessionObj` (records `connect_to_signal`, serves
`props` dict incl. State/Type/Class/LockedHint),
`_FakeBus` (records `add_signal_receiver`/`remove_signal_receiver`,
`get_object` routes manager vs session paths), plus a `_fake_dbus_modules`
monkeypatch helper injecting `sys.modules` fakes for `dbus`,
`dbus.mainloop.glib`, `gi.repository` (MainLoop whose `run()` returns
immediately) for the one `_run`-level test.

Request letter → test mapping (all in new classes; existing classes keep
passing untouched):

- **(a)** own-PID resolution succeeds → current behavior: existing
  `TestSessionPath.test_pid_fallback` already pins it; add
  `test_pid_hit_skips_list` (manager raises AssertionError if ListSessions
  called).
- **(b)** pid fails → ListSessions picks the active graphical same-UID
  session: `TestListSessionsFallback.test_no_session_for_pid_falls_back`
  asserts the picked path, `via == "list"`, and **no** "lookup failed" /
  "unavailable" logs; plus uid-mismatch row ignored, greeter/background row
  ignored, tty row ignored.
- **(c)** multiple sessions → active beats online:
  `test_active_preferred_over_online`,
  `test_fifo_tiebreak` (two active x11 rows → lower session id wins),
  `test_closing_state_ranked_last` / excluded.
- Pure ranking table: `TestPickGraphicalSession` (rank/filter/FIFO/empty).
- Env validation: `test_stale_env_id_falls_through` (env path's property read
  raises UnknownObject-like → chain continues to pid/list).
- **(d)** no sessions → sleep-only: `TestRunWiring.test_headless_still_wires_sleep`
  — fake manager with empty ListSessions: assert the "unavailable …
  headless" WARN, `PrepareForSleep` subscribed on the manager
  (via `_wire_manager(fake_manager)` + receiver records), `started=True`
  (sleep-only), `status()["mode"] == "sleep-only"`.
- **(e)** session closes → re-resolve: `TestReResolve` —
  `test_removed_other_path_noop`,
  `test_removed_watched_path_resubscribes` (session_path updated, Lock/Unlock
  subscribed again on the new fake session, no spurious on_change),
  `test_session_new_attaches_in_sleep_only`,
  `test_reentrancy_guard_prevents_double_subscribe`,
  `test_reconcile_unknown_object_triggers_reresolve`.
- Full `_run` with fake dbus modules (the request's done-criteria scenario):
  `TestRunWithFakeBus.test_userslice_daemon_subscribes_listed_session` —
  GetSessionByPID raises a NoSessionForPID-shaped error, ListSessions
  returns one active x11 row with the test uid → assert the session fake's
  Lock/Unlock subscriptions, `session_path`, `started is True`, then drive
  `mon._on_session_lock()` / `_on_session_unlock()` and assert the flips
  list `[True, False]` — the pause-on-lock state machine behaves
  identically.
- Daemon: extend `TestDaemonLockmonWiring` — stub mon with `status()`; assert
  the "lock watch active (session … via …)" vs suspend-only log lines and the
  `lock_watch` dict in `handle_request({"action": "status"})`.
- Doctor: `TestDoctorLockLine` — monkeypatch `fluidvoice.doctor.control`
  (and `paths.socket_path`) with the four outcomes (session / sleep-only /
  disabled / daemon-down); assert exact line texts.

## Phases (each: suite green, one commit)

**Phase 0 — baseline.** Run the suite (expect 1432 passed). Capture the
current user-unit WARN lines from `journalctl --user -u sayit-ermano` on the
daily driver for the before/after diff. Branch `feat/lock-watch-userslice`.

**Phase 1 — pure selection + resolution chain.** `lockmon.py`: add
`GRAPHICAL_TYPES`/`STATE_RANK`/`_fifo_key`, `pick_graphical_session`, extend
`_session_path` (validation, quiet NoSessionForPID, ListSessions fallback,
`_via`, chain-end-only "lookup failed" log); update the module docstring's
session-resolution paragraph. Tests: `TestPickGraphicalSession`,
`TestListSessionsFallback` (a/b/c + stale-env). Existing tests must stay
green (in particular `test_pid_fallback_error_returns_none` still sees the
log because its manager fake has no `ListSessions` → AttributeError → chain
ends → log + None).
Commit: `feat(lockmon): resolve session via ListSessions for user-slice daemons`.

**Phase 2 — lifecycle rewire.** `lockmon.py`: `_wire_manager` (PrepareForSleep
first + SessionRemoved/SessionNew), `_watch_session`/`_detach_session`,
sleep-only mode, guarded re-resolve, reconcile UnknownObject re-resolve,
`status()`. `daemon.py`: `_start_lockmon` log lines + `lock_watch` status
key. Tests: `TestRunWiring` (d), `TestReResolve` (e),
`TestRunWithFakeBus`, daemon-wiring additions.
Commit: `feat(lockmon): manager-first wiring, sleep-only mode, session re-resolve`.

**Phase 3 — doctor line + docs.** `doctor.py` `_lock_watch_lines` + `run()`
placement; `tests/test_lock_suppression.py` `TestDoctorLockLine`.
`docs/STATUS.md` (section + note that live verification is pending), README
sentence if needed, BEHAVIOR-SPEC touch.
Commit: `feat(doctor): lock-watch line reporting the watched session`.

**Phase 4 — live verification (daily driver).** Restart the user-unit daemon
(`systemctl --user restart sayit-ermano`), then:
1. `journalctl --user -u sayit-ermano -b | grep -i lock` → the two WARN
   lines are gone; expect `lock watch: session <id> via ListSessions
   (active, x11|wayland, uid <uid>)` and `lock watch active (session …)`.
2. `sayit-ermano doctor` → lock watch line names the real session id.
3. Existing manual check (docs/STATUS.md lock-flow row): start a dictation,
   `loginctl lock-session` → `screen locked - hotkeys paused`, recording
   cancelled, tooltip `paused (locked)`; hotkey dead while locked; unlock →
   resumed.
4. Suspend path still gates (PrepareForSleep) — existing behavior.
5. Optional: logout/login → journal shows the swap (re-resolve) once
   re-verified.
Record the result in docs/STATUS.md (replace "pending" note) and amend the
"verification method" table row for lock suppression. Final full suite run.
Commit: `docs: lock-watch user-unit verification on the daily driver`.

## Risks / notes for the builder

- dbus-python quirk preserved: bus connections are created on the monitor
  thread AFTER `DBusGMainLoop(set_as_default=True)` — do not move bus
  creation earlier.
- `ListSessions` uid arrives as `uint32`; compare with `int(...) ==
  os.getuid()`. Session props come via `Properties.GetAll(SESSION_IFACE)`
  (fetch once per candidate; fetch lazily only until the first survivor
  ranks, to keep startup cheap — ≤ a few roundtrips at daemon start).
- `connect_to_signal`/`add_signal_receiver` return-value shapes vary across
  dbus-python versions — detach through `bus.remove_signal_receiver(handler,
  signal_name=…, dbus_interface=…, path=…)` only, wrapped in try/except.
- `start()` returning True in sleep-only mode is a contract change the
  daemon log line explains; `status()` disambiguates for doctor/CLI.
- All timeouts stay at 5 s, matching the existing GetSessionByPID call.
- Suite command is the repo standard:
  `.venv/bin/python -m pytest -q tests --ignore=tests/integration`
  (integration/live-X11 suites are desktop-marked and out of this change's
  loop).
