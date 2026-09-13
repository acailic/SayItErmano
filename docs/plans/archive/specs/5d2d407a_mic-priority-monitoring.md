# Plan: Mic priority list + input-device monitoring (auto microphone switching)

UPSTREAM-TRACKING row "Mic priority list, drag-to-reorder, device history" and
STATUS.md "Input-device monitoring / Bluetooth auto-switch" — the remaining
half after MPRIS media pause landed.

**Baseline verified:** `.venv/bin/python -m pytest -q tests --ignore=tests/integration`
→ 467 passed (at planning HEAD). Every phase below must leave this green.

**Problem today:** the tray Microphone submenu (`fluidvoice/tray.py
list_microphones` + `daemon._set_device`) lists pactl sources and switching
persists `recording.device`, but nothing reacts when devices appear/disappear.
A Bluetooth headset connecting mid-session never becomes the mic unless the
user re-opens the menu; a disconnect mid-dictation kills the take with no
fallback.

---

## 1. Design decisions (locked — do not re-litigate while building)

| Decision | Choice | Why |
|---|---|---|
| Watcher home | **New module `fluidvoice/micmon.py`** (not `media.py`) | `media.py` is a focused playerctl/MPRIS double with pause/resume semantics; mic monitoring is a different concern (pactl polling, diffing, priority matching) with its own test surface. New module keeps both deep. |
| Event source | **3 s `pactl list short sources` diff poll** | Per prompt: no long-lived `pactl subscribe` subprocess parsing; works on PipeWire and PulseAudio alike. Cost: a ~10–30 ms subprocess every 3 s (same order as `pw-top`). |
| Switch trigger | **Only when the configured device vanishes** and a priority match exists | No preemptive upgrade of a working configured device when a higher-priority one connects (conservative; user's explicit choice is never fought). Upgrade-on-connect stays out of scope. |
| Auto (`device = ""`) | **Never switched** | Auto means system default; we do not fight it. |
| Mid-dictation | **Never switch while `recording` or `busy`** | Finish the take on the still-open stream (`pw-record` keeps its PipeWire node after the physical device vanishes — document, don't code). The watcher re-evaluates on **every poll**, so the fallback applies on the first idle poll — **≤ 3 s after the take ends**. No pending flag, no state-machine hooks. |
| Startup recovery | Baseline poll applies the same reselect once | Daemon restarted while the configured mic is disconnected → fall back at start instead of failing with `first_pcm_timeout` on the next dictation. Only when `mic_priority` has a match. |
| Toggle | None — watcher always runs | With an empty `mic_priority` it only logs connects/disconnects; auto-switch needs a matching pattern anyway. No new knob in v1. |
| Poll interval | Module constant `POLL_INTERVAL = 3.0` (not config) | YAGNI; can become a setting later. |
| Callback cadence | `on_change(added, removed, current)` fires on **every poll** after the baseline (diffs often empty) | This is what removes the need for a pending flag: idle polls retry the reselect naturally. Daemon filters/logs only when `added or removed`. |

### Matching semantics (be exact; these are tested)

- Patterns are **case-insensitive substrings of the source name**; **first
  pattern wins** (pattern order beats listing order).
- Among sources matching the *same* pattern, **first in pactl listing order**
  wins (pactl order is stable).
- `priority_rank(name, patterns) -> int | None` — index of the first pattern
  matching `name` (case-insensitive substring), `None` if no match.
- `match_priority(patterns, names) -> str | None` — for each pattern in
  order, the first name in `names` order that matches it; `None` if no
  pattern matches anything.
- `sort_by_priority(mics, patterns)` — stable sort: matched mics first
  ordered by `(rank, original index)`, unmatched keep listing order after.

---

## 2. Phase 1 — Config: `recording.mic_priority`

**Files: `fluidvoice/config.py`, `tests/test_config_settings.py`**

1. `DEFAULTS["recording"]["mic_priority"] = []` (place right after `device`).
2. `TEMPLATE` — inside `[recording]`, after `device = ""`, add (this is scope
   item 3's "document this behavior in the config comment", covering
   auto-switch, mid-dictation and auto semantics):

   ```toml
   # Ordered microphone priority patterns — case-insensitive substrings of
   # the PulseAudio/PipeWire source name, first match wins, e.g.
   #   ["bluez", "usb-cam"]   # Bluetooth headset first, then a USB webcam
   # When the configured `device` above disappears, FluidVoice switches to
   # the first available match and notifies you. Switching never happens
   # mid-dictation: the take finishes on the still-open stream and the
   # fallback applies within a few seconds after it. With device = ""
   # ("auto") the system default is followed and never overridden.
   mic_priority = []
   ```

3. `_SAVE_WHITELIST["recording"]` += `"mic_priority"`.
4. `SETTING_LISTS` += `("recording", "mic_priority")`.
5. `ALLOWED_SETTINGS["recording"]` += `"mic_priority"`.
6. `coerce_setting` — in the `SETTING_LISTS` branch add a `mic_priority` rule:
   - not a list → reject;
   - any non-`str` entry → reject;
   - per entry: `strip()`, drop empties; any entry longer than **64 chars** →
     reject the whole list; more than **20** entries (after cleanup) → reject;
   - dedupe case-insensitively preserving first occurrence;
     return `(True, cleaned)`.

**Tests (`tests/test_config_settings.py`, new class `TestMicPriority`):**
- `coerce_setting("recording", "mic_priority", [" bluez ", "", "USB-Cam"])`
  → `(True, ["bluez", "USB-Cam"])`.
- Case-insensitive dedupe: `["BlueZ", "bluez"]` → `["BlueZ"]`.
- Rejects: `"bluez"` (not a list), `[42]`, `[ "x"*65 ]`, 21 entries.
- `apply_settings(cfg, {"recording": {"mic_priority": ["bluez"]}})` → in
  `changed`, value applied; unknown-ish path already covered by whitelist.
- `DEFAULTS["recording"]["mic_priority"] == []` and `"mic_priority" in
  TEMPLATE` (cheap doc-guard).

**Verify:** `.venv/bin/python -m pytest -q tests/test_config_settings.py`,
then the full suite.

---

## 3. Phase 2 — New module `fluidvoice/micmon.py` + unit tests

**Files: `fluidvoice/micmon.py` (new), `tests/test_micmon.py` (new)**

Module docstring: input-device monitoring — poll pactl sources, diff,
priority-match. No wiring anywhere yet; suite stays green because nothing
imports it except tests.

```python
"""Input-device monitoring: poll pactl sources, diff the list, and
priority-match mic names for automatic switching."""
POLL_INTERVAL = 3.0

def list_source_names() -> list[str]:
    """Names from `pactl list short sources` (tab-separated; column 1 is the
    name). `.monitor` sources excluded. Returns [] on any failure (no pactl,
    timeout) — polling must never raise."""

def priority_rank(name: str, patterns: list[str]) -> int | None: ...
def match_priority(patterns: list[str], names: list[str]) -> str | None: ...
def sort_by_priority(mics: list[dict], patterns: list[str]) -> list[dict]: ...

class MicMonitor:
    """Diff-poll sources every `interval` seconds and hand every poll to
    `on_change(added, removed, current)` after the baseline poll. The
    baseline itself does NOT fire the callback."""

    def __init__(self, on_change, *, interval=POLL_INTERVAL,
                 poll: Callable[[], list[str]] | None = None,
                 log: Callable[[str], None] = ...): ...
    # poll=None -> default list_source_names; a custom poll also skips the
    # pactl availability check (tests / injection).
    last_names: list[str]          # [] until the first poll

    def start(self) -> bool:
        # shutil.which("pactl") check ONLY when using the default poll;
        # False (logged) without pactl — mirrors MediaController._available.
        # Baseline poll_once() synchronously here, then daemon thread
        # "fluidvoice-micmon" looping: stop_event.wait(interval) then poll_once().
    def stop(self) -> None:
        # stop_event.set(); thread.join(timeout=2). Must be prompt and idempotent.
    def poll_once(self) -> None:
        # names = [n for n in self._poll() if not n.endswith(".monitor")]
        # first call: store baseline, return. Otherwise diff vs last
        # (added = new names in listing order, removed = gone names in old
        # order), update state, call on_change — wrapped in try/except so a
        # callback error is logged, never kills the thread.
```

Implementation notes:
- `list_source_names` parses `pactl list short sources` — one
  `subprocess.run(..., capture_output=True, text=True, timeout=3)`;
  `line.split("\t")`, take field `[1]`, skip empty. Keep this **separate**
  from `tray.list_microphones` (full listing with descriptions + default +
  5 s cache, used at menu-open): different output format, different cadence.
- Thread is `daemon=True`; use `threading.Event` + `wait(interval)` so
  `stop()` returns immediately, not after the remaining sleep.

**Tests (`tests/test_micmon.py`) — fake pactl runner, monkeypatch
`subprocess.run` exactly like `tests/test_tray.py::TestMicrophoneListing`:**

- `TestListSourceNames`:
  - fake `pactl list short sources` output (tab-separated: id, name, driver,
    spec, state, …) with a `bluez_source.00_11_….headset-mono`, a
    `alsa_input.usb-Cam.mono-fallback`, `alsa_input.pci.analog-stereo` and a
    `.monitor` line → the three names in order, monitor excluded;
  - pactl missing (`FileNotFoundError`) → `[]`, no raise.
- `TestMatching`:
  - `match_priority(["usb-cam", "bluez"], [bluez, usb-cam, pci])` → usb-cam
    (pattern order beats listing order);
  - same-pattern tie → first listing order;
  - no match → `None`; empty patterns → `None`; case-insensitivity
    (`["BLUEZ"]` matches `bluez_source...`);
  - `sort_by_priority` stable ordering: matched-by-rank first, unmatched
    keep original order.
- `TestMicMonitor` (custom `poll` = scripted `list.pop(0)`; call `poll_once()`
  directly — deterministic, no sleeps):
  - baseline poll fires **no** callback; second poll with a new source fires
    `on_change(added=[x], removed=[], current=[...])`;
  - removal detected; no-change poll fires the callback with empty diffs
    (contract);
  - `poll()` raising is swallowed (thread/callback survive);
  - lifecycle: `start()` with `interval=0.01`, wait for ≥2 callbacks (use a
    `threading.Event`/counter), `stop()` → thread not `is_alive()` within a
    short join, no callbacks after stop.

**Verify:** `.venv/bin/python -m pytest -q tests/test_micmon.py`, then full suite.

---

## 4. Phase 3 — Daemon wiring + tray priority ordering

**Files: `fluidvoice/daemon.py`, `fluidvoice/tray.py` (no change needed in
tray.py itself — ordering happens in the daemon's menu builder), `tests/test_micmon.py`**

### daemon.py

1. `__init__`: add `self._micmon = None`, `self._mic_missing_logged = False`
   (warn-once latch).
2. `run()`: after `self._start_tray()` (and before `_maybe_first_run_onboard`)
   call `self._start_micmon()`.
3. New methods:

   ```python
   def _start_micmon(self, poll=None, interval=None) -> None:
       # best-effort, like the tray; poll/interval overrides are for tests
       from .micmon import MicMonitor
       mon = MicMonitor(on_change=self._on_sources_changed, log=log,
                        **({"poll": poll} if poll else {}),
                        **({"interval": interval} if interval else {}))
       if not mon.start():
           return  # already logged "mic monitoring unavailable"
       self._micmon = mon
       log("mic monitoring active")
       self._mic_reselect(mon.last_names)   # startup recovery (design table)

   def _on_sources_changed(self, added, removed, current) -> None:
       if added or removed:
           log(f"audio sources: +{', '.join(added) or '—'} "
               f"-{', '.join(removed) or '—'}")
       with self._lock:
           idle = not self.recording and not self.busy
       if not idle:
           return          # mid-dictation safety: retry on the next poll
       self._mic_reselect(current)

   def _mic_reselect(self, names: list[str]) -> None:
       """Auto-switch ONLY when the configured device is absent and a
       priority match exists. device == "" (auto) is never touched."""
       device = self.cfg["recording"].get("device", "")
       if not device:
           return
       if device in names:
           self._mic_missing_logged = False   # reset the warn-once latch
           return
       patterns = self.cfg["recording"].get("mic_priority") or []
       best = micmon_match_priority(patterns, names)
       if best is None:
           if not self._mic_missing_logged:
               log(f"microphone '{device}' unavailable and no priority match")
               self._mic_missing_logged = True
           return
       self._mic_missing_logged = False
       self._set_device(best)   # existing path: lock re-check, save, rebuild
       ui.notify("FluidVoice", f"Microphone switched to {best}",
                 enabled=self.cfg["notifications"]["enabled"])
   ```

   Import `match_priority` from `.micmon` at module top (`from .micmon import
   match_priority as micmon_match_priority` or a plain import — builder's
   choice, but keep it importable for monkeypatching in tests).
4. `shutdown()`: right after the `self._watchdog` cancel block add
   `if self._micmon: self._micmon.stop(); self._micmon = None`.
5. No `apply_config` changes: `recording.mic_priority` is read live from
   `self.cfg` on every poll, so a settings save hot-applies by construction
   (`recording.device` hot-apply already exists).

### Tray menu ordering (`_build_tray_menu`)

Replace the bare `for m in list_microphones():` loop with:

```python
from .micmon import sort_by_priority
mics_list = sort_by_priority(
    list_microphones(),
    self.cfg["recording"].get("mic_priority") or [])
for m in mics_list:
    ...  # unchanged checkmark logic: checked = device == m["name"]
```

"Auto (system default)" stays the first entry; the effective device stays
checkmarked (existing logic).

**Tests (in `tests/test_micmon.py`, class `TestDaemonAutoSwitch` — follow
`tests/test_media.py::TestDaemonWiring`: build `dm.Daemon(cfg, recorder=
StubRecorder(), backend_factory=lambda c: None, use_hotkey=False,
use_sounds=False)` with `copy.deepcopy(DEFAULTS)`, monkeypatch
`fluidvoice.config.save_config` to a dict, and import the `quiet_ui` fixture
from `tests.test_daemon` at the bottom of the file). Call
`d._on_sources_changed(...)` directly — no threads needed:**

- `test_switches_when_configured_device_vanishes`: device = the usb-cam
  source, `mic_priority = ["bluez"]`; poll where usb-cam is present (no
  switch), then usb-cam gone + bluez present → `cfg device` == bluez name,
  `d.recorder.device` == bluez name, notify recorded with "Microphone
  switched to", save called.
- `test_no_switch_when_configured_device_present`.
- `test_auto_device_never_switches`: device `""`, `mic_priority=["bluez"]`,
  arbitrary add/remove → device stays `""`, no notify, no save.
- `test_no_priority_match_logs_once`: device vanished, nothing matches → no
  switch; two consecutive callbacks → the "unavailable" log appears exactly
  once (collect `dm.log` output via monkeypatch), then device reappears →
  latch resets (log can fire again after a future disappearance — assert via
  a third disappearance).
- `test_pattern_order_beats_listing_order`: `mic_priority=["usb-cam","bluez"]`,
  both present, device gone → usb-cam chosen.
- `test_never_switches_while_recording`: `d.toggle()` (StubRecorder) →
  callback with usb-cam removed → device unchanged, no notify; `d.cancel()`;
  next callback (idle) → switched. Also the `busy` variant: set
  `d.busy = True` directly → no switch; `d.busy = False` → callback switches.
- `test_startup_recovery`: `d._start_micmon(poll=lambda: [pci, bluez])` with
  device = absent usb-cam and `mic_priority=["bluez"]` → switched at start;
  then `d.shutdown()` exits cleanly (covers watcher lifecycle in the daemon).
- `test_watcher_not_started_by_constructor`: plain `Daemon(...)` has
  `_micmon is None` (no runaway thread in every existing daemon test).

**Tray test (in `tests/test_tray.py::TestMenuModel`):**
`test_menu_orders_mics_by_priority` — monkeypatch `fluidvoice.tray.
list_microphones` to return fixed dicts (bluez, usb-cam, pci — bypasses the
5 s cache and subprocess), cfg with `mic_priority=["bluez","usb-cam"]` and
device = bluez name; build `d._build_tray_menu()`; assert the Microphone
children are `[Auto, bluez, usb-cam, pci]` in that order and the bluez entry
is the checked one.

**Verify:** `.venv/bin/python -m pytest -q tests/test_micmon.py
tests/test_tray.py tests/test_daemon.py`, then full suite.

---

## 5. Phase 4 — GTK settings editor (Dictation page)

**Files: `fluidvoice/gtkui/settings_window.py`, `tests/test_gtkui.py`**

Rows editor with **up/down buttons** (v1 choice — simpler and safer than
drag-and-drop), mirroring the existing per-app-rules / dictionary editor
habits (registry list + rebuild-on-change like `_refresh_models`).

1. `__init__`: `self._mic_prio_rows: list[dict] = []` (each
   `{"row": Adw.EntryRow}`), alongside `_rule_rows`/`_dict_rows`.
2. `_build_dictation()`: after the "Microphone and recording" group add a
   `Adw.PreferencesGroup(title="Microphone priority", description="Ordered
   name patterns (e.g. bluez for a Bluetooth headset). When the chosen
   microphone disappears, the first available match is used.")`. Keep a
   `self._mic_prio_add_row` ActionRow ("Add pattern", `list-add-symbolic`
   button → `self._add_mic_prio("")`).
3. `_add_mic_prio(value: str)`: `Adw.EntryRow(title="Pattern")`,
   `set_text(value)`, `changed` → `self._touch()`; suffix buttons
   `go-up-symbolic` / `go-down-symbolic` / `user-trash-symbolic`
   (`flat`; trash also `destructive-action`), wired to
   `_move_mic_prio(ref, -1/+1)` and `_remove_mic_prio(ref)`. Append to the
   registry, `_rebuild_mic_prio()`.
4. `_move_mic_prio(ref, delta)`: swap the registry position with the
   neighbor (ignore at the edges), then `_rebuild_mic_prio()`.
5. `_rebuild_mic_prio()`: remove every pattern row from the group, remove and
   re-append the Add row, then append the registry's rows in order (the same
   widgets are re-added, so entered text survives), and refresh the up/down
   buttons' sensitivity (first row's up / last row's down disabled).
6. `_remove_mic_prio(ref)`: drop from registry, `_rebuild_mic_prio()`,
   `self._touch()`.
7. `_collect_mic_priority() -> list[str]`: `[r["row"].get_text().strip()
   for r in self._mic_prio_rows if r["row"].get_text().strip()]`.
8. `_collect()`: always include
   `body.setdefault("recording", {})["mic_priority"] =
   self._collect_mic_priority()` (empty list is meaningful — removals must
   persist), next to the dictionary/modifiers lines.
9. `_load()`: call `self._load_mic_priority(list(self.cfg.get("recording",
   {}).get("mic_priority") or []))` (clear + rebuild rows, no `_touch`).

**Tests (`tests/test_gtkui.py`, `TestSettingsWindow`):**
- Extend `StubClient.get_config` to set
  `cfg["recording"]["mic_priority"] = ["bluez", "usb-cam"]`.
- `test_mic_priority_editor` (mirrors `test_per_app_rule_editing`): loaded →
  2 rows; `w._collect()["recording"]["mic_priority"] == ["bluez", "usb-cam"]`;
  `w._add_mic_prio("pci")` + set text via the new row's EntryRow → collect
  `["bluez", "usb-cam", "pci"]`; `w._move_mic_prio(w._mic_prio_rows[2], -1)`
  → `["bluez", "pci", "usb-cam"]`; move down of the first row ignored at the
  edge (order unchanged); remove the middle row → `["bluez", "usb-cam"]`;
  `w._dirty` flips on edits; `w.save()` posts `mic_priority` in
  `c.saved[-1]["recording"]`.
- `test_loads_every_section` keeps passing untouched (registry-based rows are
  not in `_rows`; no changes needed there — do not register mic_priority in
  `_rows`, it is not a scalar field).

**Verify:** `.venv/bin/python -m pytest -q tests/test_gtkui.py` (auto-skips
headless — run on a display if available), then full suite.

---

## 6. Phase 5 — Docs & ledgers

**Files: `README.md`, `docs/STATUS.md`, `docs/ROADMAP.md`,
`docs/UPSTREAM-TRACKING.md`**

1. `README.md` — in the Configuration sample (after the `[model]` block /
inside the highlights toml) add a `[recording]` slice:
   `mic_priority = ["bluez", "usb-cam"]` with the one-line explanation
   (Bluetooth-headset-first example, per scope item 4).
2. `docs/STATUS.md`:
   - Core dictation loop bullet or a new bullet under it: mic priority list +
     device monitoring (3 s pactl poll, vanished-device fallback, never
     mid-take, auto never overridden), tray priority ordering, settings
     editor.
   - "Left / Later": change `- [ ] Input-device monitoring / Bluetooth
     auto-switch; MPRIS media pause.` → `[x] … — DONE: mic priority list +
     pactl source monitoring with auto-switch (bluez pattern example in
     README); MPRIS pause shipped earlier.` Keep honest wording.
3. `docs/ROADMAP.md`: tick the "Input-device monitoring / Bluetooth
   auto-switch" checkbox with the same one-liner.
4. `docs/UPSTREAM-TRACKING.md`:
   - capability row "Mic priority list, Bluetooth auto-switch" → ✅ with note
     "pactl poll + priority patterns; drag-to-reorder later".
   - v1.6.8 row "Microphone priority list, drag-to-reorder, device history" →
     ✅-with-caveat note: "priority list + auto-switch shipped (up/down
     reorder buttons); drag-to-reorder and device history remain ⏳".
   - v1.6.9-ish row "Fixes: 3.5mm external mics, Bluetooth route changes,
     clamshell mode" → note "Bluetooth route changes covered by source
     monitoring; others ⏳".
   - Bump the header test count to the new total once the final suite run is
     known (STATUS.md line 3: "467 automated tests").

**Verify:** full suite one more time; grep docs for stale "roadmap: input-device
monitoring" notes and update the ones in scope.

---

## 7. Full verification recipe (per phase and at the end)

```bash
.venv/bin/python -m pytest -q tests --ignore=tests/integration
```

Optional manual QA (only if a desktop + pactl exist): start the daemon, watch
`[fluidvoice] audio sources: …` logs while plugging/unplugging a USB mic or
pairing a BT headset with `mic_priority = ["bluez"]` and a non-auto device;
confirm the notification and that a take started before the unplug finishes
untouched and the switch lands within ~3 s after.

Suggested commit split = phases 1–5 (config → micmon → daemon/tray → GTK →
docs), each with its tests in the same commit.

## 8. Out of scope (do not build)

Drag-and-drop reordering · per-app mic profiles · PipeWire native API (stay
on pactl) · Wayland audio portals · UI notifications beyond the existing
`ui.notify` path · preemptive upgrade-on-connect · device history persistence
· `pactl subscribe` streaming.

## 9. Risks / notes for the builder

- `list_microphones` has a 5 s cache — the tray-ordering test must
  monkeypatch `fluidvoice.tray.list_microphones` (the daemon imports it
  inside `_build_tray_menu` at call time, so the patch takes effect).
- No offline test calls `Daemon.run()` (verified: only a GLib `loop.run()`
  in test_gtkui) — auto-starting the watcher in `run()` cannot break the
  offline suite.
- Keep `poll_once()` side-effect-light and callback exceptions contained: a
  crashed watcher thread is a silent feature loss.
- `MicMonitor.stop()` must join with a timeout (never block shutdown on a
  hung pactl call — the subprocess timeout is 3 s; join timeout 2 s is fine,
  the thread is daemon=True anyway).
- Don't register mic_priority in the GTK `_rows` field registry — it is a
  list editor with its own collect path (like dictionary/modifiers).
