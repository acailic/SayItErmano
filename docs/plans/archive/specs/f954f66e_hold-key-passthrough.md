# Plan: Hold-mode key passthrough (other keys reach the app, not swallowed)

Adw: `f954f66e` · Repo: FluidVoiceLinux @ HEAD `60c4d60` · Offline baseline verified: **509 passed** (`.venv/bin/python -m pytest -q tests --ignore=tests/integration`, ~42 s).

## 1. Problem & goal

`fluidvoice/hotkey.py::_hold_cycle` implements push-to-talk by grabbing the
whole keyboard (`root.grab_keyboard(..., GrabModeAsync, GrabModeAsync)`) from
hotkey press to release, so it can see the hotkey's KeyRelease. Every other
key event during the hold is delivered to us and discarded — the user cannot
type while holding the dictation key. Upstream macOS passes keystrokes
through during hold.

Goal: during a hold cycle, non-hotkey / non-Escape key events are **replayed
to the focused application** (presses and releases, modifiers included). No
new config keys; doctor untouched. If replay is unavailable on a display,
hold mode must degrade to exactly today's swallow behavior.

Out of scope: Wayland, key remapping, per-app hold behavior, changing the
default hotkey, changing *when* the hold ends (upstream's "other keys
interrupt the trigger" / clean-tap semantics stay a documented divergence —
we deliberately keep dictation running while typing, per request framing
"keep typing while holding").

## 2. Feasibility — verified live on `:1` (probe scripts in this session; results)

| # | Question | Verified result |
|---|---|---|
| P2 | Does an active keyboard grab capture XTEST fakes? | **Yes.** With the grab held, `d.xtest_fake_input(KeyPress/KeyRelease, kc)` events arrive at the *grabber* and the focused window gets nothing. → Injecting while grabbed is useless (the key would be eaten twice). |
| P3 | ungrab → XTEST inject → re-grab | **Works cleanly.** Focused window receives press+release (send_event=False, indistinguishable from real keys); grabber receives *nothing back* (no echo). X processes requests on one connection strictly in order, so there is no race between our own ungrab/inject/re-grab. |
| P4 | Release-race reconciliation | `d.query_keymap()` returns a 32-entry list; keycode *N* is down ⇔ `km[N//8] & (1 << (N%8))`. Usable to check whether the hotkey is still physically held. |
| P5 | XSendEvent fallback mechanics | Constructed `Xlib.protocol.event.KeyPress` sent via `focus.send_event(ev, X.KeyPressMask, True)` arrives at the focused window with `send_event=True`. (Real-toolkit acceptance varies — honest fallback only.) |
| P6 | Auto-repeat of a held key | An XTEST-held key auto-repeats at ~30 Hz as synthetic `KeyRelease+KeyPress` pairs (63 events/s observed). **Today's code ends the hold on the first synthetic release** — reconciliation is needed both for the ungrab race and to survive long holds. (Per-key `change_keyboard_control(auto_repeats=…)` did *not* suppress it — don't rely on that.) |
| API | python-xlib 0.33 specifics | XTEST method is `d.xtest_fake_input(event_type, detail)` (NOT `d.fake_input`); presence via `d.has_extension("XTEST")` (no round trip — extension list cached at Display init); `root.grab_keyboard(...)` reply has `.status`; `d.get_input_focus().focus` is a window resource; `X.GrabSuccess` exists. Window event selection: `w.change_attributes(event_mask=…)`. |

**Chosen approach** (matches request direction "keep the keyboard grab but
re-send each swallowed event"): keep the async keyboard grab for release
detection; for every passthrough event do **ungrab → `xtest_fake_input` →
re-grab**; after each re-grab (and on every hotkey KeyRelease) reconcile with
`query_keymap`. Fallback chain if XTEST is missing: XSendEvent to the focused
window; if that also fails at runtime: permanent no-op for the cycle (=
today's swallow). Rationale vs alternatives:

- Inject-while-grabbed (XTEST or XSendEvent-only, no ungrab): XTEST is eaten
  by our own grab (P2) — keys would be *lost*, worse than swallowing. XSendEvent
  bypasses grabs but many toolkits (GTK/Qt/Chromium) ignore `send_event=True`
  keys — unreliable as the primary path. XSendEvent stays the fallback.
- Passive-grab pairs / `XAllowEvents(ReplayKeyboard)`: replay terminates the
  grab entirely, losing release detection; no per-event passthrough exists in
  the core protocol for an active async grab.
- XI2 raw events: python-xlib's XI2 support is too limited; out of scope.

Why it can't duplicate keys or stick modifiers:

- The original event is consumed by our grab (P2); only our fakes reach the
  app; during the sub-millisecond ungrab window real events flow directly to
  the app exactly once. No double delivery is possible.
- Replay is strictly symmetric (we replay every press and every release of
  passthrough keys). Any injected press is balanced either by our injected
  release or by the user's real release (which flows to the app un-grabbed
  after the hold ends). Orphan releases (key pressed before the hold, released
  during it) are replayed too — that *prevents* the classic stuck modifier.
  We never inject the hotkey or Escape keycode.

## 3. Implementation

All code changes in `fluidvoice/hotkey.py`; tests in
`tests/test_cli_ui_hotkey.py` + `tests/integration/`. No new files in
`fluidvoice/`, no config changes.

### Phase 1 — pure event classification (no behavior change yet)

Add module-level:

```python
_HOLD_END, _HOLD_ABORT, _HOLD_REPLAY, _HOLD_IGNORE = "end", "abort", "replay", "ignore"

def classify_hold_event(etype, detail, hotkey_keycode, escape_keycode) -> str:
    """Pure: how _hold_cycle should treat one grabbed keyboard event.
    - KeyRelease(hotkey)            -> end
    - KeyPress(escape_keycode)      -> abort   (cancel recording)
    - Key{Press,Release}(anything else incl. modifiers) -> replay
    - hotkey KeyPress (auto-repeat), escape KeyRelease, non-key events,
      None details                  -> ignore
    escape_keycode may be None (cancel disabled) -> escape is a normal key."""
```

Rewire `_hold_cycle`'s inner dispatch to use it, keeping today's semantics
exactly (release ends hold, Escape aborts, everything else silently
discarded — replay wiring comes in Phase 3).

Unit tests (`tests/test_cli_ui_hotkey.py`, new `TestHoldClassification`):
table-driven — end/abort/replay/ignore for: hotkey release, escape press,
other press, other release, modifier-keycode press/release, hotkey press
(auto-repeat), escape release, non-key event type (e.g. `X.MappingNotify`),
`etype=None`, `detail=None`, `escape_keycode=None` (escape keycode is then
"replay").

### Phase 2 — the replay helper (not yet wired)

Add `class KeyReplayer` in `fluidvoice/hotkey.py`:

```python
class KeyReplayer:
    """Replays key events swallowed by the hold grab to the focused app.
    XTEST path: ungrab -> xtest_fake_input -> re-grab (one connection,
    in-order: no self-race). XSendEvent path: direct to focus window,
    grab untouched. Degrades to "off" (swallow, pre-change behavior) if
    the extension is missing or a send raises. Per-hold-cycle instance."""

    def __init__(self, d, log=None):
        self._d = d
        self._mode = "xtest" if _display_has_xtest(d) else "send_event"

    def replay(self, etype, keycode) -> bool:
        """True = keyboard grab still held afterwards; False = grab lost
        (caller must end the hold). Never raises."""
```

- `_display_has_xtest(d)`: `try: return bool(d.has_extension("XTEST")) except Exception: return False`.
- XTEST path: `d.ungrab_keyboard(X.CurrentTime)` → `d.xtest_fake_input(etype, keycode)` → re-grab via `d.screen().root.grab_keyboard(False, X.GrabModeAsync, X.GrabModeAsync, X.CurrentTime)`; check reply `.status == X.GrabSuccess` → return that. Wrap inject in try/except: on exception, still re-grab (critical — restore the grab before returning), flip `_mode = "off"`, return True.
- send_event path: `focus = d.get_input_focus().focus`; build `Xlib.protocol.event.KeyPress/KeyRelease(time=0, root=root_id, window=focus.id, same_screen=1, child=X.NONE, root_x=0, root_y=0, event_x=0, event_y=0, state=0, detail=keycode)`; `focus.send_event(ev, X.KeyPressMask | X.KeyReleaseMask, True)`; on any Exception flip `_mode = "off"`. Grab untouched → return True.
- "off" mode: no-op, return True.

Unit tests (new `TestKeyReplayer`, no X server — plain fake objects with
call recording, e.g. a small `FakeDisplay`/`FakeRoot` pair or
`unittest.mock.MagicMock`):
- XTEST present: one `replay(X.KeyPress, 38)` records exactly
  `ungrab_keyboard` → `xtest_fake_input(2, 38)` → `grab_keyboard(...)`, in
  order; returns True when fake grab reply `.status == X.GrabSuccess`.
- fake grab reply `.status == X.AlreadyGrabbed` → returns False.
- `xtest_fake_input` raises → re-grab still called (assert!), returns True,
  subsequent replay is a no-op (mode flipped off).
- XTEST absent: no `ungrab_keyboard`/grab calls; `get_input_focus` +
  `send_event` recorded; returns True.
- XTEST absent and `get_input_focus` raises → no exception, returns True,
  mode off; later replay is a no-op.

### Phase 3 — wire into `_hold_cycle` + auto-repeat/ungrab-race reconciliation

New private helper on `HotkeyListener`:

```python
def _hotkey_still_down(self, d, keycode) -> bool:
    """query_keymap reconciliation. True = key physically held (the
    KeyRelease we just saw was a synthetic auto-repeat release, or the
    release escaped during a replay ungrab window we already survived).
    On any error: return True (keep event-driven semantics)."""
    try:
        km = d.query_keymap()
        return bool(km[keycode // 8] & (1 << (keycode % 8)))
    except Exception:
        return True
```

`_hold_cycle` becomes (structure preserved from today — same start/stop/
cancel callbacks, same degradation on initial grab failure):

```python
def _hold_cycle(self, d, keycode):
    root = d.screen().root
    try:
        root.grab_keyboard(False, X.GrabModeAsync, X.GrabModeAsync, X.CurrentTime)
    except Exception:
        self._safe(self.on_toggle)          # degrade to single toggle (unchanged)
        return
    self._safe(self.on_toggle)              # start
    aborted = False
    replayer = KeyReplayer(d)
    try:
        while not self._stop_flag.is_set():
            try:
                event = d.next_event()
            except Exception:
                break
            verdict = classify_hold_event(getattr(event, "type", None),
                                          getattr(event, "detail", None),
                                          keycode, self._escape_keycode)
            if verdict == _HOLD_END:
                if self._hotkey_still_down(d, keycode):
                    continue        # synthetic auto-repeat release: ignore
                break
            if verdict == _HOLD_ABORT:
                aborted = True
                break
            if verdict == _HOLD_REPLAY:
                if not replayer.replay(getattr(event, "type", None),
                                       getattr(event, "detail", None)) \
                        or not self._hotkey_still_down(d, keycode):
                    break           # grab lost, or release escaped an ungrab window
    finally:
        try:
            d.ungrab_keyboard(X.CurrentTime)
        except Exception:
            pass
    if aborted:
        self._safe(self.on_cancel)
    else:
        self._safe(self.on_toggle)  # stop and transcribe
```

(The `classify == ignore` case falls through — silently skipped, as today.)
Also update the module docstring (lines 1–10) and the `_hold_cycle` docstring
to describe replay + reconciliation.

Unit tests (new `TestHoldCycle`, same file — no X server): a `FakeDisplay`
with a scripted `next_event()` queue (empty → raise to end the loop),
`query_keymap()` returning a 32-byte bytes with chosen keycodes down,
recorded `ungrab_keyboard`, and `screen().root` fake; monkeypatch
`fluidvoice.hotkey.KeyReplayer` with a stub recording `(etype, keycode)`
calls and returning a scripted `still_grabbed`. Construct
`HotkeyListener("F9", [], "hold", on_toggle=…, on_cancel=…)`, set
`listener._escape_keycode` and call `listener._hold_cycle(fake_d, 67)`:

- Happy path: `press('a'), release('a'), KeyRelease(F9 with keymap bit clear)`
  → on_toggle ×2 (start/stop), replay recorded for 'a' press+release, on_cancel
  never called.
- Auto-repeat: `KeyRelease(F9)` while keymap bit *set* → hold continues;
  following `KeyPress(F9)` (repeat press) ignored; then `KeyRelease(F9)` bit
  clear → ends; on_toggle ×2, replay never called for F9.
- Escape abort: `KeyPress(Escape)` → on_cancel ×1, on_toggle ×1, replay never
  called for Escape.
- Replay loses the grab: stub returns False → hold ends via stop path
  (on_toggle ×2).
- Release escaping an ungrab window: after a replay, `_hotkey_still_down`
  returns False (keymap bit clear) → hold ends, stop path.
- Initial grab raises → on_toggle ×1 exactly (existing degradation, now pinned
  by a test).

### Phase 4 — honest docs & config comments

Behavior improves in place; document exactly what landed:

- `fluidvoice/config.py`: template comment (~line 156) and the `DEFAULTS`
  inline comment (~line 30) for `mode`: extend the `"hold"` line with
  "other keys typed during the hold are replayed to the focused app
  (XTEST, XSendEvent fallback; swallowed only if the X server provides
  neither)". No new keys, no schema change.
- `docs/STATUS.md`: move the near-term `- [ ] Hold-mode key passthrough`
  line to done with the honest one-liner; extend the core-loop hold bullet;
  **reword (do not delete) the divergence-table row**: still a grab-based
  hold with replay; remaining divergence = typed keys do *not* end the
  dictation (upstream clean-tap interrupts).
- `docs/BEHAVIOR-SPEC.md` §4 port-status: replace "known divergence: the
  grab swallows other keystrokes…" with the replay description + the
  remaining interrupt-semantics divergence.
- `docs/ROADMAP.md`: mark the v0.2 hold-passthrough item `[x]` DONE with
  the same honest wording.
- `README.md` hotkey table row (~line 162): `✅ toggle; hold for non-modifier
  keys (other keys pass through while held)`.

### Phase 5 — live desktop-marked integration test (optional-but-preferred)

- `tests/integration/conftest.py`: new fixture `daemon_hold_hotkey` — after
  `isolated_env` writes `TEST_CONFIG`, rewrite the file with
  `mode = "hold"` added under `[hotkey]` (same F9 key, `--no-sounds` spawn
  via the existing `_spawn_and_wait`, `_stop_daemon` teardown). Do not touch
  the existing fixtures.
- `tests/integration/test_live_x11.py`: new class
  `TestHoldPassthroughLive` under the module's existing
  `integration`+`desktop` marks and `requires_x11` guard:

```python
def test_typing_during_hold_reaches_app_and_recording_completes(self, daemon_hold_hotkey):
    # receiver window (probe pattern): override-redirect, KeyPress|KeyRelease
    # mask, map, wait Viewable, save focus, set focus to it
    # 1. xdotool keydown F9  -> poll control.request("status") until recording
    # 2. xdotool type --delay 60 "hi"     (XTEST chars → daemon replays them)
    # 3. xdotool keyup F9    -> poll until recording False and status ok
    # 4. drain the receiver window's events; assert KeyPress events for the
    #    'h' and 'i' keycodes arrived (send_event False)
    # finally: keyup F9 + control.request("cancel") (safety), restore focus,
    # destroy window
```

  Retry loops and timing follow the existing `TestHotkeyLive` patterns
  (previous daemons may still hold grabs). Auto-repeat flood (P6: ~30 Hz
  synthetic pairs while F9 is held) must *not* end the hold — that is exactly
  what the Phase-3 reconciliation fixes; the test would fail on pre-change
  code both by ending early and by losing the typed keys.
- Keycodes exist on `:1` for alphanumerics and F9/F10 (verified); F13+ do
  **not** — don't use them in tests.

## 4. Verification (each phase must pass before the next)

```bash
.venv/bin/python -m pytest -q tests --ignore=tests/integration   # 509 + new, green
# Phase 5 (DISPLAY=:1 is live here; excluded from default runs via marks):
.venv/bin/python -m pytest -q tests/integration/test_live_x11.py -m desktop -k HoldPassthrough
```

Done = all phases merged, offline suite green, the desktop test passes on
`:1` (or its failure is investigated and the fallback honestly documented),
docs/config comments match shipped behavior, and hold mode with XTEST
unavailable still behaves byte-for-byte like today (unit-pinned by the
`off`-mode tests).

## 5. Risks / accepted edges (document in the PR description)

- Replayed keys trigger other clients' passive grabs (WM shortcuts fire
  during hold) — that *is* correct passthrough, same as macOS.
- Escape pressed inside a sub-ms ungrab window can reach the app once
  (no passive Escape grab is armed mid-hold; the active grab normally
  delivers it to us). Accepted; one retry ends the hold.
- Replay costs two extra X round trips per key event while typing during a
  hold — sub-millisecond locally, imperceptible.
- XSendEvent fallback may be ignored by some apps (send_event=True) —
  worst case equals today's swallow; documented as fallback, never as parity.
- Scope-3 contingency (only if live testing exposes a real failure class):
  narrow replay to character-producing keysyms and keep function/modifier
  keys swallowed — document whichever lands in the config comment and
  STATUS; do not ship this narrowing preemptively.
