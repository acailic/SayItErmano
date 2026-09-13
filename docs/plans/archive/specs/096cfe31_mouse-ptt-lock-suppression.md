# Plan: Mouse-button push-to-talk (XGrabButton) + locked-screen suppression

Adw: `096cfe31` · Repo: FluidVoiceLinux @ HEAD · Offline baseline verified: **945 passed** (`.venv/bin/python -m pytest -q tests --ignore=tests/integration`, ~43 s).

Two ROADMAP "Later" items in one feature set:

1. **Mouse PTT** — a spare mouse button (e.g. button 8) held down = dictation,
   released = stop & transcribe; clicks during the hold reach the focused
   window natively (upstream macOS PR #939 parity: press lifecycle, isolation,
   interrupted-hold safety).
2. **Lock suppression** — while the session is locked/suspended the daemon
   ignores hotkey callbacks, cancels an active dictation through the existing
   cancel path, and the tray tooltip says `paused (locked)`
   (`general.pause_when_locked = true` default).

## 1. Problem & goals

Today `fluidvoice/hotkey.py::HotkeyListener` is keyboard-only (XGrabKey).
Keyboard hold-mode already solved the hard part of X11 push-to-talk: the
passive-grab activation is released for the hold's duration so other keys pass
through natively, release is detected by auto-repeat-proof `query_keymap()`
polling, a passive Escape grab covers cancel-during-hold, and the grab
re-arms afterwards. Nothing anywhere suppresses the hotkey when the session
is locked — a locked screen with an active dictation keeps recording.

Goals:

- `recording.push_to_talk_button` (e.g. `"button8"` / `"b8"`, empty = off, the
  default → behavior unchanged) + optional `recording.push_to_talk_modifiers`
  qualifier; doctor reports resolution.
- A `MousePTTListener` in `fluidvoice/hotkey.py` mirroring the hold-mode
  design: `grab_button` with `owner_events=False`, `GrabModeAsync`; press
  starts recording and releases the grab activation (`ungrab_pointer`) so
  clicks pass through; release detected by event-driven pointer-button
  observation; re-arm is automatic (passive grabs persist); Escape
  cancel-grab armed during the hold, same as keyboard holds.
- Lock suppression via a `fluidvoice/lockmon.py` monitor (logind D-Bus
  signals + LockedHint property, screensaver-name fallback): while locked,
  hotkey entries are ignored (logged once per transition), an active
  recording is cancelled through the existing `cancel()` path, tooltip notes
  `paused (locked)`.
- Unit tests for button parsing/validation and the lock state machine; a
  live X11 integration test for arm/hold/release/cancel; a documented manual
  check for the lock flow (locking the live session in CI would lock the
  operator's desktop — out).

Out of scope (per request): gestures, scroll-button PTT (buttons 4–5 are
refused), per-app button overrides, Wayland pointer protocols (X11 only),
any UI editor beyond config keys + doctor lines.

## 2. Feasibility — verified live on `:1` (Xorg 21.1, GNOME/X11, python-xlib 0.33)

Probes ran during planning; every design decision below is backed by one.

| # | Question | Verified result |
|---|---|---|
| P1 | Does core `XQueryPointer`'s mask show buttons >5? | **No.** `root.query_pointer().mask` has bits only for buttons 1–5 (CARD16: modifier bits + Button1–5Mask). Buttons 6–9 down → delta `0x0000`. The request's assumed mechanism ("query_pointer button mask") **cannot see the canonical PTT thumb buttons (8/9)** — release detection is redesigned, see P3. |
| P2 | `XGrabButton` passive-grab flow | **Works.** Grabbing button 8 on root for all 8 lock-mask combos (NumLock was ON live — the Mod2 combo matched): 0 refused, press delivered to the grab window as a real `ButtonPress` (`send_event=False`, `detail=8`). `d.ungrab_pointer(X.CurrentTime)` releases the active grab; a button-1 click while "holding" reached the receiver window under the pointer as native press+release (`send_event=False`). The **passive grab persists** across the ungrab — a second press fired again with no re-grab (unlike the keyboard hold, which must `ungrab_key`+re-grab because key auto-repeat re-triggers its passive grab; buttons have no auto-repeat). |
| P3 | Release observation without consuming | **XI2 raw events work, with one landmine.** `root.xinput_select_events([(AllMasterDevices, RawButtonPressMask \| RawButtonReleaseMask)])` + a negotiated XI version **≥ 2.1 (use 2.2)** delivers `RawButtonRelease` (evtype 16) with `detail == button` for buttons 1..9 — non-consuming, grab-independent, auto-repeat-immune (buttons don't auto-repeat). **python-xlib hardcodes XIQueryVersion(2,0), and this Xorg delivers RawButtonRelease only to clients that negotiated > 2.0** — with 2.0 you get presses but never releases (verified side-by-side against `xinput test-xi2 --root`). Fix: send `XIQueryVersion(2,2)` as the connection's *first* XI2 request (the version is fixed per client at first ask). |
| P4 | Raw event shape in python-xlib | Raw events are **not registered** in `xinput.init()`'s `ge_add_event_data` table → they arrive as `GenericEvent` with `.evtype` (16 = release, 15 = press) and `.data` = raw bytes. Wire layout (little-endian): `CARD16 deviceid` @ [0:2], `CARD32 time` @ [2:6], **`CARD32 detail` = button number** @ [6:10]. GenericEvents have **no `.detail`/`.window` attributes** — never `getattr` those without a guard, and they interleave with core events in one queue (skip generics when waiting for a core `ButtonPress`). |
| P5 | XTEST drives the flow for tests | `xdotool mousedown/mouseup/click <n>` fires grabs and raw events for buttons 1–9. **State-matching fakes are dropped** (Xorg dedup, same as the keyboard finding): a `mousedown 8` while 8 is logically down produces nothing. Keep every fake down/up balanced; teardown must always `mouseup` (a stray held button poisoned a probe run). A fake re-press of an already-held button can't fire, so the mid-hold re-press branch (second pointer device pressing the same number) is defensive only. |
| P6 | Lock surfaces | logind present on the system bus; session 4 (GNOME/X11) exposes `LockedHint` (`no` while unlocked). **No screensaver D-Bus name is currently owned** (`org.freedesktop.ScreenSaver` / `org.gnome.ScreenSaver` both unowned on this GNOME) → the property/signal path on the logind *session object* is primary, screensaver `ActiveChanged` is the fallback. `Manager.GetSessionByPID` works only for processes inside the session (an agent shell got `NoSessionForPID`); resolve the session path via `XDG_SESSION_ID` first, then `GetSessionByPID(os.getpid())`. `/proc/self/sessionid` is the *kernel* session id — not logind's; do not use it. |

**Chosen design** (press = core passive grab, release = XI2 raw, cancel =
Escape key grab — exactly the keyboard hold's shape with the pointer twin of
each mechanism):

- Press: `XGrabButton(button, mods|extra)` over `_LOCK_MASKS` (shared with
  the keyboard listener — buttons match modifier state exactly too, NumLock
  verified live), `owner_events=False`, `GrabModeAsync`,
  `event_mask=ButtonPressMask|ButtonReleaseMask`, `confine_to=cursor=X.NONE`,
  refusals routed through per-request `onerror` (BadAccess → data + retry,
  the battle-tested keyboard pattern).
- Passthrough: `ungrab_pointer(X.CurrentTime)` immediately after the press.
- Release: XI2 raw `RawButtonRelease` for `detail == button` (needs XI 2.2
  negotiation; refuse-to-start honestly if the server won't do > 2.1 —
  releases would never arrive).
- Cancel: passive `grab_key(Escape, AnyModifier)` armed only during the hold
  (macOS overlay-up semantics, identical to keyboard hold), `ungrab_key` +
  `ungrab_keyboard` at hold end (the Escape activation is a keyboard grab).
- Re-arm: nothing to do — the passive grab survives `ungrab_pointer` (P2).

## 3. Implementation

Files: `fluidvoice/hotkey.py` (MousePTTListener + parsing helpers), new
`fluidvoice/lockmon.py`, `fluidvoice/daemon.py`, `fluidvoice/config.py`,
`fluidvoice/doctor.py`, new `tests/test_mouse_ptt.py`, new
`tests/test_lock_suppression.py`, `tests/integration/conftest.py` +
`tests/integration/test_live_x11.py` (append), docs (final phase).

### Phase 1 — button parsing + config keys (pure, no behavior change)

`fluidvoice/hotkey.py`:

```python
MOUSE_PTT_MIN_BUTTON = 6   # 1-3 click, 4-5 scroll (wheel) - all refused

def parse_button_spec(name: str) -> int | None:
    '''Config value -> X button number. None for ""/absent (feature off).
    Accepts "button8", "b8", "8", "Button 8" (case/space insensitive).
    Raises HotkeyError for buttons 1-5 ("a <click/scroll> button would break
    the desktop") and anything unparsable or > 255 (X detail is a CARD8).'''

def parse_raw_button_event(data) -> tuple[int, int] | None:
    '''(deviceid, button) from a raw XI2 GenericEvent's .data bytes, or
    None when the bytes are too short. Layout per P4.'''
```

`fluidvoice/config.py`:

- `DEFAULTS["recording"]["push_to_talk_button"] = ""` — comment: `# mouse push-to-talk: "button8"/"b8" (6-255; 1-5 refused). Empty = off. Independent of hotkey.mode - the button is always hold-style.`; `DEFAULTS["recording"]["push_to_talk_modifiers"] = []` (any of ctrl/alt/shift/super); `DEFAULTS["general"]["pause_when_locked"] = True`.
- TEMPLATE: same keys with comments under `[recording]` (button + modifiers,
  with the "thumb buttons are usually 8/9; 6/7 on some mice" hint) and
  `[general]` (`pause_when_locked = true` — "hotkeys ignored + active
  dictation cancelled while the session is locked/suspended").
- Validation plumbing (all four tables, like `hotkey.modifiers`):
  `ALLOWED_SETTINGS`/`_SAVE_WHITELIST` entries; `_coerce_button_spec` for
  `("recording", "push_to_talk_button")` (str ≤ 32 chars; normalize via
  `parse_button_spec`, reject when it raises); modifiers reuses the
  ctrl/alt/shift/super list check (extend the `modifiers` branch to cover
  both keys); `SETTING_BOOLS` += `("general", "pause_when_locked")`.

`tests/test_mouse_ptt.py` (new):

- `parse_button_spec`: `"button8"`→8, `"b8"`→8, `"8"`→8, `"BUTTON 8"`→8,
  `"button08"`→8 (strip leading zeros), `""`/`"  "`→None, `"none"`→None
  (explicit off), `"button1".."button5"`/`"b3"`/`"4"` raise with
  click/scroll in the message, `"button256"`/`"button0"`/`"button"`/
  `"b-8"`/`"xyz"` raise, `255` OK.
- config: defaults present; `coerce_setting` accepts/normalizes valid
  values, rejects 1–5 / >255 / non-str; `apply_settings` round-trips the
  three keys; template contains the new keys (load_template smoke).
- `parse_raw_button_event`: bytes `02 00 .. .. .. .. 08 00 00 00 ..` →
  `(2, 8)`; short/None/str inputs → None.

### Phase 2 — `MousePTTListener` in `fluidvoice/hotkey.py`

Same file as `HotkeyListener`; share `_LOCK_MASKS`, `HotkeyError`,
`_MAX_GRAB_ATTEMPTS`, `MODIFIER_MASKS`, `resolve_keysym`, and the
`onerror`-routing/retry philosophy (duplicated small helpers inside the new
class with a comment pointing at the keyboard twin — **do not refactor
`HotkeyListener`**; its machinery is battle-tested and entangled with
keycodes).

```python
class MousePTTListener:
    """Mouse push-to-talk via XGrabButton + XI2 raw release events.
    Mirrors HotkeyListener hold mode: press -> on_toggle (start),
    raw button release -> on_toggle (stop & transcribe), Escape while
    holding -> on_cancel. Clicks during the hold pass through natively
    (the passive-grab activation is ungrab_pointer'd for the hold)."""

    def __init__(self, button: int, modifiers: list[str], on_toggle,
                 on_cancel=None, cancel_key: str | None = "Escape",
                 display_name=None, log=None, on_grab_change=None):
        # _mods from MODIFIER_MASKS; combo-health dicts
        # (_combo_ok/_combo_attempts/_pending/_refuse_warned/_was_healthy)
        # copied from the keyboard pattern, keyed (button, mask).

    def _negotiate_xi(self, d) -> bool:
        # FIRST XI2 request on the connection: XIQueryVersion(2, 2) built
        # directly (python-xlib's xinput_query_version hardcodes 2.0 and
        # RawButtonRelease is gated behind >2.0 clients - probe P3).
        # Return negotiated >= (2, 1).

    def setup(self) -> list[str]:
        # Display(display_name); raise HotkeyError if not _negotiate_xi
        #   ("X server does not deliver raw button releases (XI < 2.1)").
        # root.xinput_select_events([(AllMasterDevices,
        #     RawButtonPressMask | RawButtonReleaseMask)])   # both: matches
        #   the verified configuration; raw presses are filtered out.
        # resolve cancel key (reuse _resolve-style logic + escape keycode).
        # _sync_button_grab(); summary line:
        #   f"mouse PTT button {button} (XGrabButton), modifiers {mods:#x}"
        #   + cancel note.

    def _sync_button_grab(self):  # self-heal refused combos on the ~10ms
        # tick, exactly like _sync_hotkey_grab (on_grab_change flips fire)

    @property
    def button_grabbed(self) -> bool:  # all lock combos held & settled

    # _run loop (daemon thread "fluidvoice-mouse-ptt"): per tick
    #   _sync_button_grab(); _sync_cancel_grab();
    #   if no events: wait(0.01); continue
    #   ev = next_event()
    #     GenericEvent (guard: no .detail attr!) -> evtype 16 &&
    #       parse_raw_button_event(ev.data)[1] == button -> self._released
    #     core ButtonPress, detail == button, not holding -> _hold_cycle()
    #     anything else -> ignore (raw presses, other buttons, motions)

    def _hold_cycle(self, d):
        # on_toggle (start); _released = False; aborted = False
        # d.ungrab_pointer(X.CurrentTime)               # clicks pass through
        # root.grab_key(escape, AnyModifier, ...)       # cancel-during-hold
        # loop while not stop:
        #   if self._released: break
        #   drain pending events: raw release of button -> released;
        #     KeyPress(Escape) -> aborted; break;
        #     ButtonPress(detail == button) -> ungrab_pointer again
        #       (second-pointer re-activation) and keep holding; else ignore
        #   wait(0.02)
        # finally: ungrab_key(escape, AnyModifier); d.ungrab_keyboard(...)
        #   (release the escape activation, same as the keyboard hold);
        #   one final ungrab_pointer (idempotent, covers the re-press path)
        # aborted -> on_cancel; else -> on_toggle (stop & transcribe)

    # set_recording(active) -> Escape grab while recording (same contract);
    #   _sync_cancel_grab copies the keyboard listener's; stop(); summary
```

Unit tests (`tests/test_mouse_ptt.py`, own fake X — copy the
`FakeDisplay`/`FakeRoot`/`FakeGrabError` pattern from
`tests/test_hotkey_grab.py`, do not import from tests):

- `FakeRoot` gains `grab_button(button, modifiers, owner_events,
  event_mask, pointer_mode, keyboard_mode, confine_to, cursor, onerror)`,
  `ungrab_button`, `ungrab_pointer`, plus a scripted event queue
  (`ButtonPress(BTN)`, fake GenericEvents made from
  `SimpleNamespace(evtype=16, data=bytes(...))` with a `type` that isn't
  core).
- Grab routing: all 8 lock combos issued with onerror; all-refused →
  `button_grabbed is False`, no crash; holder release → recovered +
  on_grab_change fired; healthy steady state issues zero grabs.
- `setup()` without XI 2.1+ (scripted reply 2.0) → `HotkeyError` mentioning
  raw button releases.
- `_hold_cycle` state machine: press → `on_toggle` (start); scripted raw
  release event → second `on_toggle`, never `on_cancel`; scripted
  `KeyPress(escape)` → `on_cancel`, single `on_toggle`; a queued
  `ButtonPress(BTN)` mid-hold → hold continues (extra
  `ungrab_pointer` recorded); `stop()` mid-hold → ends without callbacks
  hanging; `ungrab_pointer` is called before any passthrough expectation;
  Escape grab armed for the hold and disarmed after.
- `parse_raw_button_event` wired: a generic whose data encodes a *different*
  button does not end the hold.

### Phase 3 — daemon wiring, status, doctor

`fluidvoice/daemon.py`:

- `_mouse_ptt: Any = None` in `__init__`; `_start_mouse_ptt()` called from
  `run()` right after `_start_hotkey()`: read
  `cfg["recording"].get("push_to_talk_button")`; empty → skip silently;
  `parse_button_spec` raise or `HotkeyError` from setup →
  `log(WARN mouse PTT unavailable: ...)` + notification (no daemon death);
  else `MousePTTListener(button, modifiers, on_toggle=self.toggle,
  on_cancel=self.cancel, cancel_key=hk cancel_key, log=log,
  on_grab_change=lambda *_: self._refresh_tray())`, start, log summary,
  `_log_grab_state`-style startup honesty for refusals.
- `_tray_recording` also forwards `set_recording` to `_mouse_ptt`
  (Escape-grab arming, best-effort try/except).
- `shutdown()` stops `_mouse_ptt`.
- `handle_request("status")`: add
  `"mouse_ptt_grabbed": (self._mouse_ptt.button_grabbed if configured
  else None)` (None = unconfigured — mirrors `hotkey_grabbed` semantics) —
  this is the surface the integration test asserts the live arm through.
- `apply_config`: keys starting with `hotkey.` **or**
  `recording.push_to_talk_button` / `recording.push_to_talk_modifiers` →
  restart the mouse listener (stop+start inside `_restart_hotkey` or a
  sibling `_restart_mouse_ptt`; raise-through for the settings UI like
  hotkeys).
- `_tray_tooltip`: when `self._locked` (Phase 4) append ` - paused (locked)`
  after the state.

`fluidvoice/doctor.py`:

- `_mouse_ptt_lines(cfg)`: resolution block — `push-to-talk button:
  button8 (XGrabButton on button 8, modifiers none)` /
  `not configured (keyboard hotkey only)` / `INVALID: <reason>` (1–5,
  unparsable — from the parser error) / `disabled`. When configured and the
  daemon is up, one line from status (`mouse_ptt_grabbed`): `arm: ok` /
  `arm: BLOCKED (another client holds it - retrying)`.
- Print the block near the existing hotkey-grab line in `run()`.

Tests (extend `tests/test_mouse_ptt.py`): daemon wiring with a stub
listener class (mirror `_StubListener` in `test_hotkey_grab.py`) —
configured → constructed with parsed button + started; unconfigured → no
listener; parser error → WARN + no listener + daemon alive; status field
None/True/False; `_tray_recording` forwards `set_recording`;
`apply_config` restarts on button change; default config (no button)
changes nothing (status has `mouse_ptt_grabbed: None`, no extra logs).
Doctor line tests mirroring `TestDoctorHotkeyGrabLine`.

### Phase 4 — `fluidvoice/lockmon.py` + daemon lock gate

New module (sibling of `micmon.py`; same best-effort contract):

```python
"""Session lock/suspend watch: logind session signals + LockedHint
property, screensaver-name fallback, low-frequency reconcile poll."""

class LockMonitor:
    """Flips on_change(locked: bool) on lock/unlock/suspend transitions.
    Sources, in order: logind session Lock()/Unlock() signals
    (loginctl-driven locking), PropertiesChanged(LockedHint) on the same
    session object (GNOME's path - it sets the property, no screensaver
    bus name is owned there), Manager PrepareForSleep(bool) on
    /org/freedesktop/login1 (suspend = locked), and - where a DE owns the
    names - org.freedesktop.ScreenSaver / org.gnome.ScreenSaver
    ActiveChanged(bool) on the session bus. A 5 s LockedHint Get inside
    the GLib loop reconciles any missed signal. Without D-Bus, logind or
    a resolvable session, start() returns False and the feature is off."""

    def __init__(self, on_change, log=log): ...
    def start(self) -> bool:
        # resolve session path: $XDG_SESSION_ID ->
        #   /org/freedesktop/login1/session/<id>; else Manager
        #   GetSessionByPID(os.getpid()) (works for autostart/systemd-user
        #   daemons); else False. Then a daemon thread runs
        #   DBusGMainLoop(set_as_default=True) + GLib.MainLoop (the tray's
        #   pattern - importing dbus/GLib lazily inside start()).
        # initial state: session LockedHint Get -> on_change once if True.
    def stop(self) -> None: ...
    @property
    def locked(self) -> bool: ...
    # test seams / internal handlers:
    def _session_path(self, bus=None) -> str | None
    def _apply(self, locked: bool, source: str)  # dedup: only flips fire
```

Daemon (`fluidvoice/daemon.py`):

- `self._locked = False`, `self._lockmon = None`; `_start_lockmon()` from
  `run()` after `_start_micmon()`: skip (log once) when
  `general.pause_when_locked` is false; `LockMonitor(on_change=self._on_locked,
  log=log).start()` → False → `log("WARN lock watch unavailable
  (<reason>)")`, headless continues.
- `_on_locked(locked)`: transition only (monitor dedups); on lock:
  `log("screen locked - hotkeys paused")`, if recording → `self.cancel()`
  (existing path: watchdog off, recorder cancel, media resume, notify
  "Cancelled"), if `_command_pending` → `cancel_pending_command()`,
  `_refresh_tray()`; on unlock: `log("screen unlocked - hotkeys resumed")`,
  `_refresh_tray()`.
- Gate the recording-start entries (quiet per press — the transition was
  logged once): `toggle()` returns False immediately when `self._locked`
  (covers hotkey, tray click, and socket `toggle`); `start_rewrite()` and
  `start_command()` return early under the lock; `_on_command_hotkey`
  confirm path is already safe (pending was cancelled). `cancel` and the
  rest of the socket surface stay available while locked.
- `handle_request("status")` += `"locked": bool`.
- `apply_config`: `general.pause_when_locked` flips live (start/stop the
  monitor; stopping clears `_locked`).

`tests/test_lock_suppression.py` (new):

- `LockMonitor._apply`: dedup (True→True no callback), both sources of
  truth (Lock signal then PropertiesChanged with same value → one flip),
  `locked` property tracks.
- `_session_path`: XDG_SESSION_ID path string; fallback via a fake
  Manager/`GetSessionByPID` returning a path / raising → None.
- Signal handlers as direct method calls: Lock()/Unlock(),
  PropertiesChanged with `{"LockedHint": True}`, PrepareForSleep(True)
  → locked, PrepareForSleep(False) → unlocked, ActiveChanged(True) →
  locked (each firing `on_change` exactly once per flip).
- Daemon state machine (Daemon with `_NoopRecorder`, `use_hotkey=False`,
  stub recorder asserting start/cancel): locked while idle → `toggle()`
  returns False, recorder.start never called, `_tray_tooltip()` contains
  `paused (locked)`; locked while recording → recorder.cancel called,
  recording False, notify path exercised; unlock → `toggle()` starts again;
  locked while `_command_pending` → cancelled; `pause_when_locked = false`
  → monitor never started, toggles work; status `locked` field follows;
  socket `cancel` still works while locked; log-once: two locked-while-idle
  toggles → one "hotkeys paused" log line (assert via injected log).

### Phase 5 — live integration + docs

`tests/integration/conftest.py`: new fixture `daemon_mouse_ptt` —
`isolated_env` + `push_to_talk_button = "button8"` under `[recording]`
(same rewrite pattern as `daemon_hold_hotkey`), `--no-sounds` spawn,
`_stop_daemon` teardown.

`tests/integration/test_live_x11.py` — new `TestMousePTTLive` (module
marks + `requires_x11` already present; follow `TestHoldPassthroughLive`
structure, receiver-window probe pattern included):

1. **Arm**: poll status until `mouse_ptt_grabbed is True` (retry window:
   a previous test daemon's grabs release lazily).
2. **Hold + passthrough + release** (gpu-guarded via `skip_if_gpu_busy()`
   — the take transcribes): create receiver window (override-redirect,
   ButtonPress|ButtonRelease mask), park pointer over it, save/restore
   focus + pointer position in `finally`; `xdotool mousedown 8` → poll
   status `recording True`; `xdotool click 1` during the hold → drain
   receiver window, assert native (send_event False) press+release of
   button 1 arrived; `xdotool mouseup 8` → poll `recording False` + `ok`;
   `finally` always `mouseup 8` + `cancel` (P5: a stray held button poisons
   later runs).
3. **Escape cancels**: `mousedown 8` → recording; `xdotool key Escape` →
   recording False via the cancel path (status ok; not transcribed);
   `mouseup 8` in finally.
4. **Blocked-arm honesty** (optional, mirrors `TestHotkeyGrabRecovery`):
   pre-hold all lock combos of button 8 with a holder connection → daemon
   status `mouse_ptt_grabbed is False`; release → recovered.

Docs (honest, shipped-behavior wording):

- `docs/ROADMAP.md`: mark the Later item `[x]` — mouse PTT via XGrabButton
  + XI2 raw release events, lock suppression via logind; note buttons 1–5
  refused, X11 only.
- `docs/UPSTREAM-TRACKING.md`: PR #939 row → ✅ with the X11 mechanism
  mapping (grab isolation = ungrab_pointer + passive persistence;
  interrupted holds = raw-release event detection + Escape cancel; press
  lifecycle = full state machine); locked-screen row → ✅ via lockmon
  (logind signals + LockedHint, screensaver fallback; dock stays
  macOS-only).
- `docs/STATUS.md`: Done section entry (both features, config keys, doctor
  lines); divergence table additions: release detection is XI2
  raw-event-driven (core XQueryPointer cannot see buttons >5), buttons
  1–5 refused, suspend treated as locked, GNOME path is the LockedHint
  property (no screensaver name owned), lock latency = signal + ≤5 s
  reconcile; test-status section: unit + desktop-integration coverage and
  the manual lock check procedure (below).
- `docs/BEHAVIOR-SPEC.md` §4: add the mouse-PTT bullet + lock-suppression
  line.
- `README.md`: hotkey table row (~line 234) gains mouse PTT; config
  example mention of `push_to_talk_button` + `pause_when_locked`.
- Manual check note (STATUS.md test section): with a live daemon and
  `push_to_talk_button = "button8"` set — start a dictation, lock the
  session (Super+L / `loginctl lock-session`), observe the log line +
  cancelled recording + tray `paused (locked)`, press the hotkey while
  locked (nothing), unlock, dictate again.

## 4. Verification

Every phase must leave the offline suite green before the next:

```bash
.venv/bin/python -m pytest -q tests --ignore=tests/integration
# Phase 5 (DISPLAY=:1 live; excluded from default runs via marks):
.venv/bin/python -m pytest -q tests/integration/test_live_x11.py -m desktop -k MousePTT
```

Done means: phases merged; a configured spare button starts/stops dictation
live with clicks during the hold reaching the focused window (integration
assertion); locking with dictation active cancels it and hotkey presses
while locked are ignored (unit state machine + the documented manual
check); default config (no button set, pause_when_locked on) changes
nothing observable except the new status fields (`locked: False`,
`mouse_ptt_grabbed: None`).

## 5. Risks / accepted edges (document in the PR)

- **XI version gate**: any server that won't negotiate ≥ 2.1 makes release
  detection impossible → `HotkeyError` at setup, doctor/WARN surface, no
  daemon death. The version must be negotiated before any other XI2 request
  on the listener's connection (per-client fix at first ask).
- **Raw events from any master pointer**: a second mouse releasing the same
  button number ends the hold (desired); a second pointer *pressing* it
  mid-hold re-activates the passive grab — handled by the tolerate +
  re-ungrab branch (unit-pinned, not live-reachable with XTEST).
- **Hold while the pointer device vanishes** (USB unplug): no raw release
  ever fires → the existing `max_seconds` watchdog ends the take; the
  daemon stays healthy. Noted in STATUS divergences.
- **Lock-latency**: signals are instant for logind-locking DEs and GNOME
  (LockedHint); a pathological DE could lag up to the 5 s reconcile poll —
  accepted (the prompt's bug — recording forever under a locked screen —
  is still fixed).
- **Screensaver fallback names unowned on GNOME** (verified live): relying
  on `ActiveChanged` alone would be wrong — it is additive, never primary.
- **XTEST dedup in tests**: every fake down needs its up, teardowns
  defensively `mouseup` — a held button silently breaks subsequent grabs.
- **grab_button + WM conflicts**: a compositor holding button8 combos (e.g.
  a binding) → refused-combo data + retry + `mouse_ptt_grabbed: False`
  status/doctor honesty, identical UX to the keyboard twin.
- **Wayland**: unchanged — doctor's existing Wayland note covers it; the
  listener raises the standard `HotkeyError` on non-X11 displays.
