# Plan — Hotkey-grab self-healing (never "ready" with a dead hotkey)

Request: `requests/hotkey-grab-selfheal.md` (== docs/research/2026-09-04-product-proposals.md P1).
Session: `09aec0a6`. Baseline verified: `.venv/bin/python -m pytest -q tests --ignore=tests/integration`
→ 794 passed.

## Problem (verified against the code)

`HotkeyListener.setup()` (fluidvoice/hotkey.py) issues 8 `root.grab_key()` calls (one per
`_LOCK_MASKS` combo) with **no `onerror`** and never retries. python-xlib delivers the resulting
BadAccess to the *default* error handler — `Xlib/protocol/display.py:default_error_handler`
prints `X protocol error:` and **never raises** through `grab_key()` — so the refusal is
invisible. Note: `_sync_cancel_grab`'s `except Exception` (c720b25) can therefore never fire for
BadAccess either; it only catches connection errors. Startup logs "... ready" and the daemon sits
keyless until restarted.

Key python-xlib mechanics (read from the installed `.venv` source, verified):
- `root.grab_key(key, mods, owner, pmode, kmode, onerror=None)` accepts a per-request handler.
  When the error for that request is parsed off the socket, `req._set_error(e)` calls
  `onerror(error, request)`; a **truthy return marks the error handled** and the default
  printing handler is bypassed entirely (`Xlib/protocol/display.py:parse_error`).
- Errors are pumped asynchronously by any socket read: `sync()`, `pending_events()`,
  `next_event()`, `query_keymap()`. A single `sync()` after queuing grabs resolves all of them.
- `Xlib.error.CatchError` stores only the *last* error, so per-combo tracking needs one
  closure/catcher **per grab_key call**, keyed by (keycode, full modifier mask).
- This is per-request routing on the listener's own private `Display` connection; the default
  handler for overlay.py / insertion.py (separate `Display` objects) is untouched.

## Design decisions

- **Per-request `onerror` closures, not a display-wide `set_error_handler`**: the default-handler
  path receives `request=None`, so it cannot tell *which* combo was refused. Per-request
  closures give exact (keycode, mask) attribution and suppress the print. (This satisfies scope
  item 1 — "CatchError or a custom handler" — with the custom-handler flavor, and stays inside
  the listener's own display.)
- **Route ALL listener grabs through the tracker** (hotkey combos, cancel-key grabs in
  `_sync_cancel_grab`, the hold-cycle re-arm, the hold-only Escape grab): the refusal becomes
  data everywhere instead of stderr noise. The cancel-key *state machine* itself
  (`_want_cancel`/`_cancel_grabbed`) is untouched (out of scope).
- **Healthy = all 8 lock-mask combos of the hotkey held** (partial coverage means the hotkey
  only works in some Num/Caps/Scroll states — report and retry until complete).
- **Retry cadence**: every poll-loop tick (the same ~10 ms cadence `_sync_cancel_grab` already
  uses), but ONLY when combos are missing — the healthy steady state issues zero extra X
  traffic. Cap applies to the WARN, not the retry: retries continue every tick so release is
  re-taken within ~1 s.
- **Seams for testability**: `HotkeyListener.__init__` gains optional `log=` (default: the
  existing `_sync_cancel_grab`-style `[sayit-ermano]` print) and `on_grab_change=` callbacks;
  `fluidvoice.hotkey.Display` is module-level so tests monkeypatch it with a fake.

## Files

| File | Change |
|---|---|
| `fluidvoice/hotkey.py` | error routing, per-combo state, `_sync_hotkey_grab()` retry, `hotkey_grabbed` property, `log`/`on_grab_change` params |
| `fluidvoice/daemon.py` | status field `hotkey_grabbed`, startup WARN + `ui.notify` on refused initial grab, tooltip ` - hotkey blocked!` suffix, wire `on_grab_change` → tray refresh |
| `fluidvoice/tray.py` | public `TrayIcon.refresh()` (tooltip/pixmap re-push, best-effort) |
| `fluidvoice/doctor.py` | hotkey-grab line: query daemon socket, `ok` / `BLOCKED` / `disabled` / `unknown (daemon down)` |
| `tests/test_hotkey_grab.py` | NEW — unit: fake Display/root, retry state machine, warn cap, recovery log, daemon status/tooltip/notify |
| `tests/integration/conftest.py` | `_spawn_and_wait(..., log_to=)` (log-file mode), `daemon_blocked_hotkey` fixture |
| `tests/integration/test_live_x11.py` | NEW class `TestHotkeyGrabRecovery`: conflicting F9 holder → WARN + `hotkey_grabbed:false` → release → flip true + toggle works |

## Phase 1 — hotkey.py: routing, state, retry (core)

1. **State** (in `__init__`):
   - `_combo_ok: dict[tuple[int, int], bool]` — `(keycode, mods|lock_extra)` → believed-grabbed.
     Written optimistically `True` when a grab is issued; the error callback flips it `False`.
   - `_combo_attempts: dict[tuple[int, int], int]` — distinct failed attempts per combo.
   - `_refuse_warned: bool` — cap latch; `_MAX_GRAB_ATTEMPTS = 10` module constant.
   - `_was_healthy: bool | None = None` — `None` until the initial sync resolves.
2. **Routing**: `_grab(keycode)` keeps issuing the 8 `grab_key` calls, but each now passes
   `onerror=` a closure `(err, req) -> int` that: sets `_combo_ok[key] = False`,
   `_combo_attempts[key] += 1`, and if `_combo_attempts[key] >= _MAX_GRAB_ATTEMPTS and not
   _refuse_warned`: `_refuse_warned = True` + `log("WARN hotkey grab still refused after N "
   "attempts - held by another client?")`. Returns `1` (handled → no print). Also pass a
   swallowing `onerror` to the hold-cycle Escape `grab_key` and keep `ungrab_key` calls as-is.
3. **`_sync_hotkey_grab(self) -> None`** (new, mirrors `_sync_cancel_grab` naming):
   - Guard: `self._display is None or not self._keycode` → return. Compute
     `missing = [mods|m for m in _LOCK_MASKS if not _combo_ok.get((self._keycode, mods|m), False)]`;
     if empty → return (zero steady-state traffic).
   - `healthy_before = self.hotkey_grabbed`; `self._grab(self._keycode)` (the tracker + closures
     handle the rest); then `self._display.sync()` so all catchers resolve *now*.
   - `healthy_after = self.hotkey_grabbed`. If `healthy_before and not healthy_after`, or the
     flip `False→True`: fire `self._on_grab_change(healthy_after)` (best-effort try/except).
   - On the `False→True` flip **with `_was_healthy is not None`** (i.e. not the initial grab):
     `log("hotkey grab recovered")`, reset `_combo_attempts` for hotkey combos to 0, and
     `_refuse_warned = False` (a future refusal gets a fresh WARN cycle).
   - Set `_was_healthy = healthy_after` at the end. Wrap the whole body in `try/except
     Exception: pass` (a closing display during stop() must not kill the loop thread).
4. **Property**: `hotkey_grabbed -> bool` = `self._keycode != 0 and all(_combo_ok.get(
   (self._keycode, self._mods | m), False) for m in _LOCK_MASKS)`.
5. **`setup()`**: after resolving `self._keycode`, call `self._sync_hotkey_grab()` in place of
   the bare `self._grab(...)` (keep the existing final `sync()`); summary unchanged. If refused
   after the initial attempt, set `_refuse_warned = True` so the listener cap-WARN stays quiet
   at startup — the daemon's WARN+notification (Phase 2) owns that moment.
6. **`_run()`**: first statement inside the `while` loop (before `_sync_cancel_grab()`):
   `self._sync_hotkey_grab()`.
7. **Constructor**: add `log: Callable[[str], None] | None = None` (default
   `lambda m: print(f"[sayit-ermano] {m}", flush=True)` — same style as `_sync_cancel_grab`)
   and `on_grab_change: Callable[[bool], None] | None = None` (default no-op). Refactor the
   existing `_sync_cancel_grab` WARN print to use `self.log` (message text unchanged).
   `set_recording(False)` also clears `_refuse_warned` (a new recording-idle period may WARN
   again). Keep everything else byte-compatible.

## Phase 2 — daemon.py + tray.py surfaces

1. **Status** (`handle_request` `"status"`): add
   `"hotkey_grabbed": (self._hotkey.hotkey_grabbed if self._hotkey is not None else None)`
   (`None` = hotkey disabled / `--no-hotkey`; `True`/`False` otherwise).
2. **Startup honesty** (`_start_hotkey`): after each of the three `listener.start()` blocks
   (main, rewrite, command), if `not listener.hotkey_grabbed`:
   - `log(f"WARN {label} hotkey '{key}' grab refused - held by another client, will retry")`
     (label ∈ `""`, `"rewrite "`, `"command "` — exact prompt wording for the main hotkey:
     `WARN hotkey 'Right_Control' grab refused - held by another client, will retry`), and
   - `ui.notify("SayItErmano", "Hotkey grab refused — another app holds the key; retrying "
     "automatically", timeout_ms=8000, enabled=self.cfg["notifications"]["enabled"])` — the
     same path/shape as the existing hotkey-unavailable branch. Factor as
   `_log_grab_state(listener, label, key)` to avoid triplication. Pass `log=log` into each
   `HotkeyListener(...)` construction so listener-side lines (recovered/cap-WARN) use the
   daemon's timestamped stderr logger.
3. **Tooltip** (`_tray_tooltip`): when `self._hotkey is not None and not
   self._hotkey.hotkey_grabbed`, append exactly ` - hotkey blocked!` to the returned string.
4. **Live refresh** (`tray.py` + wiring): add public `TrayIcon.refresh()` →
   `if self.active and self._loop is not None: try: self._glib.idle_add(self._apply_state)
   except Exception: pass` (`_apply_state` re-reads `self.tooltip()` and re-pushes icon/tooltip;
   menu too). In `_start_hotkey` (main listener only), pass
   `on_grab_change=lambda ok: self._refresh_tray()` where `_refresh_tray` is a new tiny
   best-effort method (`if self._tray is not None: self._tray.refresh()`, wrapped in
   try/except). This makes the tooltip follow recovery/blocking without waiting for the next
   recording transition.
5. No change to `_restart_hotkey`, apply_config, or the hotkey-unavailable HotkeyError branch.

## Phase 3 — doctor.py line

1. New helper `_hotkey_grab_line() -> list[str]` (module-private, unit-testable):
   - `from . import control` (local import, keeps module import cost unchanged).
   - `if not paths.socket_path().exists(): return ["  hotkey grab: unknown (daemon down)"]`.
   - Else `try: st = control.request("status")` / `except control.ControlError (and OSError)`:
     → `["  hotkey grab: unknown (daemon down)"]`.
   - `st.get("hotkey_grabbed") is None` (older daemon / hotkey disabled): distinguish with
     `"hotkey" in st.get(...)`? Keep simple: `None` → `"  hotkey grab: disabled (--no-hotkey or
     older daemon)"`; `False` → `"  hotkey grab: BLOCKED (held by another client - daemon is
     retrying)"`; `True` → `"  hotkey grab: ok"`.
2. `run()`: print a `\nhotkey:` section (or append to the existing `control socket:` block —
   prefer printing right after that line: `print("\n".join(_hotkey_grab_line()))`). Doctor's
   exit-code logic unchanged (a blocked grab is reported, not fatal).

## Phase 4 — tests

### Unit: `tests/test_hotkey_grab.py` (new; no X server)

Fakes (module-level in the test file):
- `FakeGrabError`: bare class standing in for BadAccess.
- `FakeRoot.grab_key(key, modifiers, owner_events, pointer_mode, keyboard_mode, onerror=None)`:
  records `("grab_key", key, modifiers)`; if `onerror is not None` and
  `(key, modifiers) in display.refused` → queue `(onerror, FakeGrabError())` into
  `display.pending_errors`. `ungrab_key` records.
- `FakeDisplay`: `.refused: set[tuple[int,int]]`, `.pending_errors`, `.calls`,
  `keysym_to_keycode()` mapping (F9→67, Escape→9), `screen()` → root, `sync()` → drain
  `pending_errors` by calling each `onerror(err, None)` (assert the handler's truthy return is
  irrelevant to the fake — just call it), `close()` no-op.
- Seam: `monkeypatch.setattr(fluidvoice.hotkey, "Display", lambda name=None: fake)`; build the
  listener with `log=lines.append` and a fresh `listener._display = fake` where needed.

Cases:
1. `setup()` all combos refused (`refused` = the 8 (67, m) pairs) → `hotkey_grabbed is False`;
   `_combo_attempts` counted; no exception; summary still returned; startup latch
   `_refuse_warned` set True (no immediate cap-WARN in `lines`).
2. Healthy path: after a successful setup, a second `_sync_hotkey_grab()` issues **zero** new
   `grab_key` calls (steady-state traffic-free).
3. Retry + recovery: start refused → `_sync_hotkey_grab()` retries only missing combos each
   call; `display.refused.clear()` → next `_sync_hotkey_grab()` → `hotkey_grabbed is True`,
   exactly one `"hotkey grab recovered"` in `lines`, attempts reset, `on_grab_change` saw
   `(False, True)`.
4. Warn cap: keep refused, call `_sync_hotkey_grab()` 10+ times → exactly one WARN line
   containing "still refused"; `listener.set_recording(False)` resets the latch → a further
   failed sync warns once more.
5. Partial refusal (holder has only the base combo): `hotkey_grabbed is False`; retries only
   the missing masks.
6. Error routing: `grab_key` was called with `onerror` for every combo (fake asserts non-None),
   and the closure returns truthy (`1`) — checked by calling the recorded handler directly.
7. Daemon status: `Daemon(cfg, recorder=StubRecorder-like, use_hotkey=False, ...)` with
   `d._hotkey = SimpleNamespace(hotkey_grabbed=False)` → `handle_request({"action": "status"})`
   includes `"hotkey_grabbed": False`; `None` listener → key absent-value `None`. (Mirror
   test_daemon.py's local `cfg`/`quiet_ui` fixtures — define small local ones; do not import
   across test modules.)
8. Tooltip: same fake listener `False` → `d._tray_tooltip().endswith(" - hotkey blocked!")`;
   `True` → no suffix; `self._hotkey = None` → no suffix.
9. Startup notify: monkeypatch `ui.notify` capture; a `_start_hotkey` with a stubbed
   `HotkeyListener` class (returns object with `hotkey_grabbed=False`, `summary=["..."]`,
   `start()`) → WARN logged once + one notify call; `True` → neither.
10. Doctor: monkeypatch `fluidvoice.doctor.control.request` /
    `fluidvoice.doctor.paths.socket_path` → assert `_hotkey_grab_line()` strings for
    ok / blocked / disabled / daemon-down (nonexistent path, request never called); one
    `doctor.run()` smoke test (capsys) asserting `"hotkey grab:"` appears.

### Integration: `tests/integration/test_live_x11.py` (appended) + conftest

1. `conftest._spawn_and_wait(tmp_path, extra_args, log_to: Path | None = None)`: when `log_to`
   is given, open it and use as Popen `stdout` (keep stderr merged); tag the proc
   (`proc._fv_log_to_file = True`). `_stop_daemon` skips its pipe-read/rewrite when that tag is
   set (the file is already complete — daemon `log()` flushes).
2. New fixture `daemon_blocked_hotkey(isolated_env, tmp_path)`: opens a *python-xlib* Display,
   `keysym_to_keycode("F9")`, grabs **all 8 `_LOCK_MASKS` combos** of F9 on the root window
   (exact same masks the daemon will request → deterministic BadAccess ×8), `sync()`, then
   spawns the daemon via `_spawn_and_wait(tmp_path, ["--no-sounds"], log_to=tmp_path /
   "daemon.log")`. Yields a small handle object with `.proc` and `.release()` (closes the holder
   display → X auto-releases its passive grabs). Teardown: release holder (idempotent), stop
   daemon.
3. New test class `TestHotkeyGrabRecovery` (same `requires_x11` guard):
   - assert `control.request("status")["hotkey_grabbed"] is False` (poll ~2 s for the field to
     be present and False — it is deterministic after socket-ready, but poll anyway);
   - assert `"grab refused - held by another client" in (tmp_path/"daemon.log").read_text()`;
   - `holder.release()`;
   - poll `status` until `hotkey_grabbed is True`, deadline 5 s (expected well under 1 s;
     slack for CI);
   - assert `"hotkey grab recovered" in daemon.log`;
   - `xdotool key F9` → poll `status["recording"] is True` (proves the re-taken grab actually
     fires), then `control.request("cancel")`.
   Keep the existing retry-loops style (the user's live daemon may transiently hold F9 — the
   fixture's holder makes that irrelevant since it is first).

## Phase 5 — verification & docs

1. Offline suite after every phase: `.venv/bin/python -m pytest -q tests --ignore=tests/integration`
   → 794+ passing (this is the per-phase gate).
2. Integration (live X session): `.venv/bin/python -m pytest -q tests/integration/test_live_x11.py
   -m desktop -k GrabRecovery` then the whole `TestHotkeyLive` class (F9 grab still healthy).
3. Manual live check (optional but cheap): start daemon, `fluidvoice status` shows
   `hotkey_grabbed` true; hold a conflicting grab from a python REPL (`Xlib` grab of the
   configured key), restart daemon, observe WARN + notification + `status` false, release,
   observe flip to true within ~1 s; `fluidvoice doctor` shows the line; tray tooltip shows the
   suffix while blocked.
4. Docs: `docs/STATUS.md` — add a line under the verification/ledger section for grab
   self-healing (date + one-line evidence); if `docs/ROADMAP.md` has a matching open row for
   hotkey trust/self-healing, tick it with a DONE note (the P1 proposal in
   docs/research/2026-09-04-product-proposals.md is the source — reference it).

## Commit slicing (small, each green)

1. `hotkey: route grab errors as data + per-combo retry state machine` (Phase 1 + unit tests 1-6)
2. `daemon: surface grab health in status, tooltip, startup warn+notify` (Phase 2 + tests 7-9)
3. `doctor: report daemon hotkey grab state` (Phase 3 + test 10)
4. `test(integration): conflicting-grab recovery on live X11` (Phase 4)
5. `docs: hotkey grab self-healing ledger entry` (Phase 5)

## Risks / notes for the builder

- **onerror return value matters**: the closure MUST return a truthy int (`1`), or the error
  falls through to the printing default handler. `call_error_handler` wraps in try/except and
  treats an exception as "not handled" — keep the closure trivial (dict writes + one log call).
- **Callback thread**: the closure fires on whichever thread pumps the socket — the main thread
  during `setup()`'s `sync()`, the hotkey thread during `_run()`'s reads. `setup()` completes
  before `_run()` starts, so there is no concurrent mutation; keep writes plain dict ops.
- **Optimistic marking**: a combo is `True` between issue and error-pump. A late BadAccess
  (pumped by `pending_events()` a tick later) flips it `False` and the next tick retries —
  self-correcting within one tick; `sync()` inside `_sync_hotkey_grab()` makes the common case
  immediate.
- **Do NOT** add `Display.set_error_handler` at display scope, and do not touch the default
  handler used by overlay.py/insertion.py (out of scope).
- **Cancel-key logic stays** exactly as-is (`_want_cancel`/`_cancel_grabbed`/warn branch); only
  its grab calls now carry `onerror` so refusals stop printing.
- The fake's `grab_key` signature must accept `onerror` positionally-last or by keyword
  (python-xlib passes it as `onerror=` kwarg).
- `--no-hotkey` daemons: status reports `hotkey_grabbed: None`; doctor prints the disabled
  wording. Tests for those paths use fakes, never a real display.
