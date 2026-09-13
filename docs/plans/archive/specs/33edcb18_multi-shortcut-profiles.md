> SUPERSEDED 2026-09-07: B1 SHIPPED in 3411af3 (hotkey.extra_shortcuts with per-shortcut prompt profiles, Settings rows, daemon wiring, tests). Do not implement from this spec — it predates the shipped design and will conflict. Fresh work: see specs/d9379f08 (language cycling) and specs/e675e121 (remote STT backend).

# Multi-shortcut dictation with per-shortcut prompt profiles (B1)

Plan for adw `33edcb18`. Request: `requests/multi-shortcut-profiles.md` (committed in f7f5fb9).

Goal: bind up to 3 dictation shortcuts, each optionally tied to a named prompt
profile (macOS parity: one shortcut AI-polished, one raw). Legacy single-key
configs behave byte-identically. Every phase leaves
`.venv/bin/python -m pytest -q tests --ignore=tests/integration` green.

---

## 1. Verified current state (read before building)

**Committed at HEAD (`86fad3e`)** — B1 scaffolding already exists:
- `fluidvoice/config.py`: `hotkey.extra_shortcuts` in DEFAULTS (lines 51–55),
  `_coerce_extra_shortcuts` (741–764, max **2** entries `{key, modifiers,
  profile}`), listed in `_SAVE_WHITELIST` (416) and `ALLOWED_SETTINGS` (594),
  dispatch at 649–650. No keysym/uniqueness validation.
- `fluidvoice/gtkui/settings_window.py`: `_ExtraShortcutsProxy` (63–96) and
  two FIXED extra rows in `_build_dictation` (1476–1497): key `Adw.EntryRow` +
  capture button (`_start_capture`, line 434), profile `Adw.ComboRow` (model
  `["(none)"] + sorted(load_profiles())`). No per-extra modifiers, no
  add/remove, primary cannot carry a profile.
- No daemon wiring, no doctor/status/tray coverage, **no tests** reference
  `extra_shortcuts`.

**Prompt profiles (settings depth pack)** — verified store: sidecar JSON
`~/.config/fluidvoice/prompt-profiles.json` (`paths.prompt_profiles_file()`,
paths.py:73), managed by `fluidvoice/ai/profiles.py` (`load_profiles`,
`save_named`, `rename_profile`; missing/corrupt → `{}` + one warning). Shape
`{"<name>": "<base-prompt text>"}`. There is **no active-profile pointer** —
profiles are presets of `ai.base_prompt`; the Settings → AI page loads one
into the editor (`_load_profiles`, settings_window.py:1130). So a per-take
override must read the sidecar at polish time.

**Committed at `3411af3` ("feat: parity pack … extra shortcuts with prompt
profiles (B1)"), docs marked shipped at `0e47e14`** — the B1 scaffold is:
- daemon.py: `_extra_hotkeys` list (337), `_profile_override` (341), extra
  listener registration in `_start_hotkey` (875–890) — **`mode="toggle"`
  hardcoded**, no `on_grab_change`, no `cancel_key`; `_toggle_with_profile`
  (1669–1676, sets override only when starting); override cleared on cancel
  (1611) and after processing (1747); threaded into the pipeline at process
  time (`pipeline._profile_override = …`, 1741); `_polish` override block
  (118–140): `load_profiles().get(name)`, missing profile → WARN + base
  prompt, composes with per-app instructions via
  `system_prompt_for(override_prompt or base_prompt_for(cfg), instructions)`.
- tests: `TestParityPackKeys` (config), `TestExtraShortcutProfiles` (daemon),
  grab additions, `test_processing.py` prompt-composition additions, plus the
  B3 tests `TestBothActivationMode` (test_hotkey_grab.py).
- The ROADMAP/UPSTREAM-TRACKING note "mark B1 briefs shipped (prevent
  duplicate factory runs)" does NOT reflect this request's acceptance
  criteria — the gaps below (config shape `hotkey.shortcuts` ≤ 3 with
  primary-profile support, mode-follow, per-shortcut doctor lines/status,
  dynamic settings rows, `tests/test_multi_shortcut.py`) are all still open.
  This plan is the gap-closure plan.

⚠️ **Provenance warning**: part of 3411af3 (3 test files + a daemon fix)
was authored mid-planning-session by a recon subagent without authority and
was committed unreviewed — read every B1 hunk before building on it; rewrite
anything that is wrong. Do not blindly trust or blindly discard.

⚠️ **Parallel work in the tree**: the working tree ALSO carries an active,
unrelated in-flight feature (overlay hover action chips:
`recording.overlay_chips` added to config.py whitelist/bools/DEFAULTS, a
daemon `FluidOverlay(actions=chips)` wiring, a settings switch, and big
`fluidvoice/overlay.py` / `tests/test_overlay.py` additions). It belongs to
another session — never stash/revert/rebase it away, and when editing the
shared files (config.py, daemon.py, settings_window.py) touch only the
B1-related hunks. Rebase this plan's line numbers against the tree you
actually start from.

**Remaining gaps vs the request** (this plan closes them):
1. Config shape: request wants `hotkey.shortcuts` = up to **3** full entries
   `{key, modifiers, profile}` (primary CAN carry a profile — the request's
   example binds the primary `Right_Control` = "raw"); today's key is
   `extra_shortcuts` (2 extras, primary never profiled).
2. Validation: max 3, unique key+modifiers combos, keysym rules.
3. Extra listeners hardcode `mode="toggle"` — must follow `hotkey.mode`.
4. Extras lack `on_grab_change` → tray tooltip never flips on their grab health.
5. `_tray_recording` (daemon.py:687–700) calls `set_recording` only on the
   primary listener → Escape-cancel grab unarmed if the primary failed to bind.
6. `_restart_hotkey` (923–940) stops only `_hotkey/_rewrite/_command` —
   leaks `_extra_hotkeys` (and `_paste_hotkey`) grabs on settings change.
7. Status payload (1231–1264) exposes only aggregate `hotkey_grabbed`.
8. Doctor prints one `hotkey grab:` line (`_hotkey_grab_line`, doctor.py:254–272).
9. Settings UI: fixed 2 rows, no modifiers per extra, "(none)" not "Base
   prompt", no add/remove, no primary profile.

## 2. Design decisions

- **D1 — Config.** New key `hotkey.shortcuts`: list ≤ 3 of
  `{key, modifiers, profile}`; `profile: ""` = base prompt. When
  absent/empty, the legacy `hotkey.key`/`modifiers` entry governs alone
  (byte-identical behavior, no profile). When non-empty it is the COMPLETE
  set of dictation shortcuts; `hotkey.key`/`modifiers` are ignored for
  grabbing. Read-compat: `extra_shortcuts` (dev-build-only shape, never in a
  release — only `windows-v0.0.x` tags exist) folds into the effective list.
- **D2 — One listener per entry through the EXISTING machinery.** Each entry
  gets its own `HotkeyListener` (same self-heal `_sync_hotkey_grab`, per-combo
  `_combo_ok` state, `hotkey_grabbed`, summary). Never a parallel mechanism.
  `hotkey.py` is expected to need **no changes** — instances already support
  modifiers, hold/both modes, and the modifier-only demotion. Multi-listener
  Escape contention: while recording, every dictation listener tries the
  cancel grab; the first wins, the others' refusals are silent data (no
  retry-spam) — accepted; cancel works via whichever holds it.
- **D3 — Validation split.** Coercion (`_coerce_shortcuts`) enforces: shape,
  max 3, key is a resolvable keysym (`hotkey.resolve_keysym` — pure, no
  Display; precedent: `_coerce_button_spec` imports from `.hotkey`),
  modifiers ⊆ {ctrl, alt, shift, super}, profile str ≤ 64 ("" ok), unique
  (key, modifiers) within the list. Modifier-only keys are NOT rejected at
  coercion (mode is a sibling key; coupling them breaks atomic saves) — the
  listener's existing hold/both→toggle demotion (hotkey.py `_run`) is the
  reused validation, exactly as for `hotkey.key` today.
- **D4 — Profile plumbing.** Seam already exists in the inherited diff and is
  correct: take-start sets `_profile_override`; `_polish` resolves it via
  `load_profiles()` and substitutes for `base_prompt_for(cfg)` (still
  composed with per-app instructions); cleared on finish/cancel. Profiles
  affect ONLY the AI polish path — `ai.enabled = false` makes them inert
  (document in the UI subtitle). Missing profile name → WARN + base prompt.
- **D5 — Status/doctor/tray.** Status payload gains additive
  `"hotkeys": [{"key", "modifiers", "profile", "grabbed"}, …]` (bind order);
  `hotkey_grabbed` becomes all-healthy across dictation listeners (None when
  `--no-hotkey`). Doctor prints one line per shortcut, keeping the
  `hotkey grab:` prefix (an existing test asserts it). Tray suffix
  `" - hotkey blocked!"` fires when ANY dictation listener is unhealthy
  (exact string kept).
- **D6 — Settings UI.** Keep the existing primary "Dictation key" entry +
  "Extra modifiers" toggles as shortcut 1 (standard save path for
  `hotkey.key`/`modifiers` unchanged), ADD a primary profile `Adw.ComboRow`,
  and replace the two fixed extra rows with dynamic rows (add/remove, cap 3
  total) each with key entry + capture button, 4 modifier toggles, profile
  combo (index 0 = "Base prompt"). Save materializes `hotkey.shortcuts` =
  [primary(+profile), *extras] ONLY when any profile is set or any extra is
  non-empty; otherwise `shortcuts` stays `[]` → legacy files byte-identical.
  Primary is always entry 1 → `hotkey.key` stays in sync for the tray menu
  label and any other readers.
- **D7 — Deprecation.** `extra_shortcuts` is removed from DEFAULTS,
  `_SAVE_WHITELIST`, `ALLOWED_SETTINGS` and `_coerce_extra_shortcuts` is
  deleted (phase 4); `dictation_shortcuts()` keeps tolerating it on read
  (fold) so interim configs survive.

## 3. Implementation phases

Each phase ends with the full suite green. Files: `fluidvoice/config.py`,
`fluidvoice/daemon.py`, `fluidvoice/doctor.py`,
`fluidvoice/gtkui/settings_window.py`, `tests/test_multi_shortcut.py` (new),
plus reshaping the inherited test additions. `fluidvoice/hotkey.py`: no
changes expected.

### Phase 0 — Rebaseline (no feature code)
1. Run `.venv/bin/python -m pytest -q tests --ignore=tests/integration`
   and record the count (3411af3 + the overlay WIP was green at planning
   time; expect roughly 1479+ overlay tests). Do NOT stash or revert the
   overlay WIP to get a "clean" baseline — it is another session's active
   work; build on the tree as you find it.
2. REVIEW the B1 hunks of commit 3411af3 (provenance §1): the daemon
   extras block, `_toggle_with_profile`, the `_polish` override, and the
   four test-file additions. Fix anything wrong as part of the later
   phases, not blindly here.
3. Re-verify the §1 gap list against the current tree (line numbers will
   have drifted with the overlay WIP).

### Phase 1 — Config: `hotkey.shortcuts` + effective-list helper
In `fluidvoice/config.py`:
1. DEFAULTS `hotkey`: add `"shortcuts": []` with a comment
   (`[{key = "F9", modifiers = ["ctrl"], profile = "Terse notes"}]`, ≤ 3,
   empty = legacy single `key`/`modifiers` entry). Update the
   `extra_shortcuts` comment to "deprecated alias, folded on read".
2. `_coerce_shortcuts(value)` per D3 (mirror `_coerce_extra_shortcuts`
   style): ≤ 3 dicts; `key` non-empty str ≤ 64 AND `resolve_keysym(key)`
   succeeds; `modifiers` list ⊆ the 4 names; `profile` str ≤ 64, `""`
   allowed and stored as `""`; reject duplicate (stripped key,
   frozenset(modifiers)). Dispatch from `coerce_setting`.
3. Add `"shortcuts"` to `_SAVE_WHITELIST["hotkey"]` and
   `ALLOWED_SETTINGS["hotkey"]`; add `("hotkey", "shortcuts")` to
   `_EMPTY_IS_MEANINGFUL` (clearing the list must write `shortcuts = []`).
4. New pure helper `dictation_shortcuts(cfg: dict) -> list[dict]`:
   `hk = cfg.get("hotkey", {})`; if `hk.get("shortcuts")` non-empty →
   normalized copies of those entries; else →
   `[{key: hk.get("key",""), modifiers: hk.get("modifiers",[]), profile: ""}]`
   + folded `hk.get("extra_shortcuts")` entries (defensive normalization).
5. Tests — create `tests/test_multi_shortcut.py`: coercion (valid 3-entry
   pass; >3 reject; empty/bad key reject; unknown keysym e.g. `"not_a_key"`
   reject; bad modifier reject; profile >64 reject; duplicate combo reject;
   `""` profile round-trips), `dictation_shortcuts` (legacy → 1 entry no
   profile; extra_shortcuts fold; shortcuts-governs-when-set). MOVE the
   extra_shortcuts assertions out of `TestParityPackKeys`
   (tests/test_config_settings.py) into here reshaped for `shortcuts`; keep
   its mode/action-trigger tests where they are. The existing `hotkey.key`
   keysym-rule tests (valid/unknown names, modifier-only handling) live in
   tests/test_cli_ui_hotkey.py — mirror their style for per-entry keys.

### Phase 2 — Daemon: per-shortcut listeners, restart, status, tray
In `fluidvoice/daemon.py`:
1. `_start_hotkey`: replace the primary + extras blocks with one loop over
   `config.dictation_shortcuts(self.cfg)`. Entry 0 keeps TODAY'S exact
   construction semantics when it is the legacy entry (label `""` in
   `_log_grab_state`, `on_toggle=self.toggle` when profile empty) so legacy
   logs/behavior are byte-identical. Every entry: `mode=hk.get("mode",
   "toggle")` (fixes the hardcode), `on_cancel=self.cancel`,
   `cancel_key=hk.get("cancel_key", "Escape")`, `on_grab_change=lambda h:
   self._refresh_tray()`, `on_toggle=self._toggle_with_profile(profile)`
   (profile `""` → override None → plain toggle). Extra entries keep the
   `"extra "` grab-state label. Store all in `self._extra_hotkeys`-style
   list (rename or reuse) and keep per-listener summaries logged.
2. `_tray_recording`: fan out `set_recording(recording)` to ALL dictation
   listeners (best-effort try/except as today).
3. `_restart_hotkey`: also stop `_paste_hotkey` and every extra listener,
   clearing the list, before `_start_hotkey()` (fixes the grab leak).
4. Status `"status"` payload: add `"hotkeys"` list per D5 (from the live
   listeners, in bind order; `grabbed` from each `hotkey_grabbed`).
   `hotkey_grabbed` = all listeners healthy (None if none).
5. `_tray_tooltip` (702–715): suffix when any dictation listener unhealthy.
6. Keep `_toggle_with_profile` and the `_polish` override block as inherited
   (they match D4); double-check the stop-press edge: override is set only
   when not recording (a press that stops a take must not change its
   profile — inherited test covers it). Hardening while here: snapshot
   `self._profile_override` under the lock in `_stop_recording_locked`
   (daemon.py ~1562) exactly as `mode`/`rewrite_context` are, and pass it
   into `_process` as an argument, instead of the committed mid-function
   read (`pipeline._profile_override = self._profile_override`, ~1741) —
   same take-state discipline as mode, race-safe by construction.
   Note for tests: `AIClient.__init__` caches
   `self.system_prompt = base_prompt_for(cfg)` (ai/client.py:113) and
   `polish(transcript, system_prompt=None)` uses
   `system_prompt or self.system_prompt` (:119) — the per-take override is
   ONLY live via the `system_prompt=` argument, so that is the assertion
   target (final render: `render_dictation_user_message(prompt, transcript)`).
Tests (extend `tests/test_multi_shortcut.py`; MOVE
`TestExtraShortcutProfiles` here from tests/test_daemon.py, and the
`test_processing.py` additions if they are B1-scoped): registration (stub
`HotkeyListener` capturing ctor kwargs → one per entry with right
key/mods/mode/cancel/on_grab_change), restart stops extras+paste, status
`hotkeys` content + aggregate, set_recording fanout, effective-profile
assertions (fake AIClient/polisher capturing `system_prompt`; tmp
`prompt-profiles.json` via monkeypatched `paths.prompt_profiles_file` or the
`load_profiles(path=…)` seam), missing-profile WARN fallback, cancel clears
override. Machinery-level self-heal coverage stays in
`tests/test_hotkey_grab.py` (FakeRoot/FakeDisplay onerror contract; the
`make(monkeypatch)` fixture at :93 monkeypatches `hotkey.Display`;
`all_combos`/`grab_calls` helpers; `TestRetryLoop` covers retry/recovery/
partial-refusal/WARN-cap) — add a case showing retry state per extra
listener instance if cheap. Extend the existing daemon-surface classes
there rather than inventing new ones: `TestDaemonStatusField` (asserts
`handle_request({"action": "status"})["hotkey_grabbed"]` via the
`_StubListener` replacement of `hotkey.HotkeyListener`), `TestTrayTooltipSuffix`
(`_tray_tooltip().endswith(" - hotkey blocked!")`), and `TestStartupHonesty`
(exact WARN string + single notification).

### Phase 3 — Doctor per-shortcut lines
In `fluidvoice/doctor.py` (structure: no registry — private
`_<topic>_lines()` helpers called straight-line from `run()` at :487, which
also prints section headers; an `ok` bool accumulates hard failures and
drives exit 0/1 via "result: ready" / "see warnings above" at :625–627):
1. Extend `_hotkey_grab_line` (254–272; today's four outputs: "unknown
   (daemon down)" :265, "disabled (--no-hotkey or older daemon)" :268,
   "BLOCKED (held by another client - daemon is retrying)" :270, "ok"
   :272): when `status["hotkeys"]` is present, emit one line per entry,
   e.g. `  hotkey grab: ctrl+F9 — BLOCKED (held by another client - daemon
   is retrying) (profile: Terse notes)` with `ok` / `BLOCKED` / `disabled`
   per entry and `(profile: base)` when empty. Keep the plain single line
   when `hotkeys` is absent (older daemon — same None-safe pattern
   `_mouse_ptt_lines`/`_lock_watch_lines` use for additive status fields)
   and the `hotkey grab:` prefix on every line. Per-shortcut BLOCKED stays
   an advisory line like today's single BLOCKED line (no `ok` flip).
2. Wire into `run()` at the existing print site (the control-socket block,
   :587–593 — `_hotkey_grab_line()` prints at :589).
Tests: extend `TestDoctorHotkeyGrabLine` — its `_doctor` helper
monkeypatches `doctor.paths.socket_path` + `control.request` with a canned
status dict (or `ControlError` for the daemon-down case), and
`test_run_prints_the_line` runs full `doctor.run()` asserting on stdout:
add multi-shortcut lines with profiles, daemon-down line unchanged, legacy
single line unchanged.

### Phase 4 — Settings UI + config cleanup
In `fluidvoice/gtkui/settings_window.py` (spine: `self._rows` registry 168,
`_load()` 293 with its isinstance dispatch 311–324, `_collect()` 350; the
capture button is `_entry(capture=True)` 241 → `_start_capture` 434 →
`_keyname` 1904 + `_KEY_REMAP` 16, already emitting config keysym names —
reuse as-is):
1. In `_build_dictation`, after the primary key entry + modifiers row: add a
   primary profile `Adw.ComboRow` ("Prompt profile", model
   `["Base prompt"] + sorted(load_profiles())`, subtitle noting it needs AI
   polish enabled).
2. Replace the two fixed extra rows + `_ExtraShortcutsProxy` (64–119) with a
   dynamic builder: up to 2 extra rows (3 total), each = key `Adw.EntryRow`
   with the `_start_capture` record-button suffix, 4 modifier
   `Gtk.ToggleButton`s (pattern: `_mod_toggles`, 1497–1505), profile
   `Adw.ComboRow`, and a remove button; an "Add shortcut" button
   (insensitive at 3). Two conforming precedents: keep the `_rows`-registered
   proxy protocol (`set_value`/`get_value`, zero `_load`/`_collect`
   special-casing — `_ExtraShortcutsProxy` is the template), or the per-app
   rules pattern (`_load_rules` 1219 / `_add_rule` 1226 / `_remove_rule`
   1254 / `_collect_rules` 1259 + explicit `_collect()` hook at 380–381);
   mic priority (1275+) adds up/down ordering if ever wanted. Mind two
   `_collect()` behaviors: an empty-text EntryRow is OMITTED from the save
   body, keeping the saved value (368–369), and
   `hotkey.modifiers` is rebuilt from `_mod_toggles` (377–378) — the
   normalization in step 3 must account for both. Seed rows from
   `dictation_shortcuts(...)` over the config the window loaded (config
   reaches the UI via the `self.c` client — `client.py` `set_config` 101,
   `prompt_profiles` 201).
3. Save path: a `_ShortcutsListProxy`-style object registered as
   `self._rows[("hotkey", "shortcuts")]` whose `get_value()` materializes
   the full list per D6 (empty list when no profiles and no extras) and
   `set_value()` seeds the rows. `save()` (390) → `_collect()` →
   `self.c.set_config(body)`; the client falls back to local
   `apply_settings` + `save_config` when the daemon is down, so the Phase 1
   coercion is the sole validation gate — it must be complete. Profile
   combos must REFRESH when profiles change: today's extra-row combos are
   built once at page build and never refreshed on profile CRUD (confirmed
   gap) — rebuild the StringList models on `_load()` and after profile
   save/rename/delete (`_after_profile_call` 1209 / `_load_profiles` 1130),
   keeping the selected name when it still exists, else "Base prompt".
   Fetch profile names via the `self.c` client wrapper
   (`client.prompt_profiles()`, client.py:201), NOT a direct
   `ai.profiles.load_profiles()` import (the current rows do the latter —
   it breaks stub-client testability; the wrappers do direct file access
   with no daemon round-trip, so behavior is unchanged).
4. Config cleanup per D7: remove `extra_shortcuts` from DEFAULTS, whitelist,
   `ALLOWED_SETTINGS`; delete `_coerce_extra_shortcuts` + its dispatch;
   delete `_ExtraShortcutsProxy`.
Tests: rows seed from legacy and from shortcuts configs; add/remove caps at
3; combo includes "Base prompt"; save normalization (single plain entry →
`shortcuts` stays `[]`; any profile/extra → full list with primary first);
`extra_shortcuts` now rejected by `apply_settings`. Follow the
stub-client pattern of tests/test_settings_profiles.py: real
`SettingsWindow` over a `_StubClient(Client)` whose `get_config`/`set_config`
return canned payloads (so the new profile-source-via-client wiring is
stubbed too — the stub's four `prompt_profile_*` methods are backed by a
`self.profile_store` dict, which now also feeds the shortcut rows), with
`loop`/`pump` fixtures, assertions on `w._rows` / `w._collect()`, and
`w.close()` at the end.

### Phase 5 — Verification & live smoke
1. Full suite green; `ruff check fluidvoice tests` (repo has ruff config /
   `.ruff_cache`; use `.venv/bin/python -m ruff check …`).
2. X11 live smoke (operator's daily driver is X11):
   - Write two profiles into `~/.config/fluidvoice/prompt-profiles.json`
     (e.g. `"CAPS": "Rewrite everything in ALL CAPS."`,
     `"Terse": "Clean the text; answer in the fewest words."`), enable AI
     against a working endpoint.
   - Config `shortcuts = [{key="Right_Control"}, {key="F9",
     profile="CAPS"}, {key="F10", profile="Terse"}]`; start the daemon;
     logs must list all three listeners; `sayit-ermano doctor` prints three
     `hotkey grab:` lines with profiles.
   - Dictate one sentence per key into an editor: outputs differ per
     profile; the same key stops the take; Escape cancels.
   - Legacy: `shortcuts = []`, `key = "Right_Control"` → doctor output and
     toggle behavior identical to the pre-change build.
   - Settings UI: add/remove/save round-trip; daemon re-bind logs re-list
     the listeners (verifies the restart fix).
3. Wayland: unchanged behavior (`_start_hotkey` early-returns before any
   grabs; the DE-shortcut assist log line stays). Per the request, only
   document that `sayit-ermano toggle` remains the bindable command — no
   per-profile CLI, no automation.

## 4. Out of scope (restated)
Per-shortcut activation modes (all follow `hotkey.mode`), per-shortcut
language, wayland DE-shortcut automation for extra keys, >3 shortcuts, any
"raw = skip AI polish" special profile semantics (a profile is a base-prompt
preset only).

## 5. Done criteria mapping
- Phased plan under `specs/` with every phase green → §3.
- Two shortcuts, different profiles → takes differ on X11 smoke → Phase 5.2.
- Legacy single-shortcut config byte-identical, existing suite unchanged →
  D1/D6 + Phase 2.1 entry-0 construction parity.
- Doctor lists every shortcut with grab health → Phase 3.
