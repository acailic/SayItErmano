# Plan: Fast language switching + wrong-language hallucination guard

**Session:** `d9379f08` · **Spec:** `specs/d9379f08_language-cycle-guard.md`
**Track:** C-track leapfrog (docs/research/2026-09-05-fluidvoice-reviews.md, insight 5)
**Why:** upstream #506 (runtime language switching) promised since 2026-04, still unshipped; #100 closed with "Parakeet doesn't allow language selection" while EN/DE users get Russian hallucinations on short takes; Superwhisper is recommended partly for automatic language switching. SayItErmano already has `general.language` + `model.languages`; the gaps are (a) switching requires opening Settings, (b) whisper-auto can lock onto the wrong language with no guard.

## Verified facts the plan builds on (planner recon, primary reads)

- **Language resolution today:** `fluidvoice/backends/__init__.py:205` `effective_language(cfg, backend=None) -> str` resolves `model.languages[backend_model_key(backend)]` > `model.languages[config_model_key(cfg)]` > `general.language`. Four call sites: `DictationPipeline._transcribe` (daemon.py:107-109), live preview start (daemon.py:1466), `test_dictation` (daemon.py:1663), doctor `_language_lines` (doctor.py:170).
- **Backends already take a language hint** in `transcribe(wav, language=None)` and return a `"language"` key:
  - `faster_whisper_backend.py:80-96` — hint via `WhisperModel.transcribe(language=...)`; **detected language surfaced** (`info.language`).
  - `torch_whisper.py:33-44` — hint via `whisper.transcribe(language=...)`; **detected language surfaced** (`result["language"]`).
  - `whisper_cpp.py:43-53` — hint via `-l` flag; under `auto` returns `"language": None` (detected language only exists in verbose output we don't parse) → guard must skip silently.
  - `parakeet_onnx.py:300-317` — accepts the param but **ignores it** (no language selection; same rationale as upstream #100). English-only v2 models; v3 multilingual is not language-addressable through this backend.
- **Runtime-override precedent:** `Daemon._profile_override` (daemon.py:341, set onto the pipeline at daemon.py:1747 `pipeline._profile_override = self._profile_override`, cleared in `finally` at :1753). The cycle override follows this exact pattern (attribute, never persisted).
- **Hotkey pattern for extra action keys:** `Daemon._start_hotkey` (daemon.py:798-894) builds one `HotkeyListener(key=..., modifiers=[], mode="toggle", on_toggle=<handler>, log=log)` per extra key (`rewrite_key` :831, `command_key` :845, `paste_key` :861, `extra_shortcuts` :875). Re-grab on settings change: `apply_config` (daemon.py:1085-1090) restarts hotkeys for any `hotkey.*` change via `_restart_hotkey` (daemon.py:924-937) — its stop-tuple `("_hotkey", "_rewrite_hotkey", "_command_hotkey")` must gain `_language_hotkey`.
- **hotkey.py needs no changes:** `HotkeyListener` is a generic single-key X11 listener — `resolve_keysym` (hotkey.py:207-220) validates keysym names incl. `_KEY_ALIASES` (:196-205) and raises `HotkeyError`; grabs run per lock-mask combo with BadAccess self-heal (`_grab` :273, `_sync_hotkey_grab` :337); the callback is a zero-arg `on_toggle` wrapped by `_safe` (:666). `_start_hotkey` early-returns on Wayland (daemon.py:805-810), so `language_key` is X11-only there — the `sayit-ermano language` CLI/socket action is the wayland path (same as every other action key today). Evdev PTT (`evdev_ptt.py`, wired at daemon.py:1023) is primary-dictation-key-only with no extra-action-key precedent — do NOT wire language_key into it. `Daemon.shutdown()` (:766-777) stops `_paste_hotkey` (:772-773) and loops `_extra_hotkeys` (:774-776). Pre-existing gap (observation only — do NOT fix here): `_restart_hotkey`'s stop-tuple omits `_paste_hotkey`/`_extra_hotkeys`.
- **Announcement surfaces:** the pill (`fluidvoice/overlay.py:825 FluidOverlay`) has `set_badge(text)` (:944) — a short status chip (used by spoken-send, daemon.py:213 `_set_pill_badge`); its fallback is `preview.NotifyPreview` (preview.py:397) — a replaceable 2 s notify-send bubble with `show(text)`. The daemon holds the live preview display as `self._preview = (engine, display)` while recording.
- **Status/IPC:** `Daemon.handle_request` (daemon.py:1217) returns the status dict read by `sayit-ermano status`, the GTK client and **doctor** (doctor.py:263 `control.request("status")` behind a socket-exists + try/except guard — the exact precedent for reporting live daemon state from doctor).
- **Tray tooltip:** `Daemon._tray_tooltip` (daemon.py:705-721) is passed to `TrayIcon(tooltip=callable)` (tray.py:369); `Daemon._refresh_tray` (daemon.py:913) pushes updates from any thread.
- **Doctor language section already exists:** `doctor._language_lines(cfg)` (doctor.py:156-178), printed at doctor.py:567 under "language resolution:"; tested in tests/test_models_manager.py:286-304.
- **Language code table:** `LANGUAGES` in gtkui/settings_window.py:29-31 (30 codes: en de es fr it nl pl pt ru uk sl sr hr bs cs sk sv da fi no hu ro bg el tr zh ja ko ar hi). It is the only enumeration; "validation accepts any code" for `general.language` today. For the NEW keys the spec requires unknown codes to be **rejected** against this table.
- **Settings UI:** General page `_build_general` (settings_window.py:450); row registry `self._rows[(section, key)]` auto-loads in `_load()` (:302-321); `_entry(section, key, title, capture=True)` (:241) is the key-capture row (Hotkey page uses it at :1456-1466); `_ListProxy` (:45ish, `set_text(", ".join(values))`) is the comma-list row precedent (filler_words :1585); **mic_priority is the ordered-list editor precedent** (`_load_mic_priority`/`_add_mic_prio`/`_move_mic_prio`/`_rebuild_mic_prio`/`_remove_mic_prio`/`_collect_mic_priority`, settings_window.py:1275-1335, saved into the body at :384). Save body assembly around :380-390 posts to the daemon's `set-config` → `config.apply_settings` + `save_config`; rejected keys come back and are toasted.
- **Config validation plumbing:** `config.py` `DEFAULTS`, `TEMPLATE`, `_SAVE_WHITELIST`, `ALLOWED_SETTINGS`, `SETTING_RANGES`, `SETTING_ENUMS`, `coerce_setting` + per-key `_coerce_*` helpers; keysym-ish keys (`paste_key` etc.) are `("str", 64)` ranges; `hotkey.*` changes trigger the live re-grab.
- **Control/CLI:** `control.request(action)` (control.py:63) over the unix socket; cli.py wires `toggle|cancel|status|paste-last` subcommands (cli.py:32-36, dispatch :107). A `language` subcommand + `cycle-language` socket action mirrors this (wayland DE-shortcut parity — `sayit-ermano toggle` is the wayland story for every other action key).
- **Test patterns:** tests/test_daemon.py — `make_wav`, `StubRecorder`, `StubBackend` (records `(wav, language)` calls, :60-67), `cfg` fixture (deep-copied DEFAULTS), `quiet_ui` fixture (records notify/sound), `FakeListener` (:325), `FakeTray` (:660), `backend_factory`/`pipeline_factory` injection. tests/test_hotkey_grab.py — `FakeDisplay`/`FakeRoot` X fakes. tests/test_gtkui.py — `StubClient(Client)`, module-level skip without DISPLAY (suite stays green headless). `_language_lines` tests live in tests/test_models_manager.py:286+.
- **Baseline:** `.venv/bin/python -m pytest -q tests --ignore=tests/integration` → 1483 passed. NOTE: the working tree carries **uncommitted work from another session** (multi-shortcut profiles B1/B3/B4 + overlay changes; `specs/33edcb18_multi-shortcut-profiles.md` untracked; modified: config.py, daemon.py, gtkui/settings_window.py, overlay.py, tests/test_overlay.py). **Do not revert, stash, or commit those files wholesale** — layer this feature on top. A rare flaky pre-existing thread-exception (`_check_first_pcm` vs `_StubRecorder.path`) can appear as 1 error; it is unrelated — rerun to confirm green. **Moving tree:** the parallel session is still editing `overlay.py` (1182 → 1396 lines during planning; the `actions` param :836 and `set_badge` :944 anchors stayed stable) and `config.py`/`settings_window.py` drift by a few lines — daemon.py and all its anchors below were re-verified stable at planning end. Before editing any WIP-touched file, re-locate anchors by symbol name (grep the function, not the line number).

## Design decisions

1. **Cycle state = daemon-only runtime state.** `Daemon._cycle_index: int | None` (`None` = not engaged). Press → engage at index 0; each further press advances `(i+1) % len(cycle)`; wrap-around after the last entry. Effective language precedence per take: **runtime cycle override (even `"auto"`) > `model.languages[model_key]` > `general.language`**. Never written to config; daemon restart resets to not-engaged. `general.language_cycle` is re-read from `self.cfg` on every press/use, so Settings edits apply live; a shrunk list clamps the index modulo its new length.
2. **Empty `language_cycle` = feature off**, even with `language_key` bound: the press logs a WARN and sends one gated notification ("Language cycle is empty — set general.language_cycle"), no state change.
3. **One resolution helper, extended.** `backends.effective_language(cfg, backend=None, runtime="") -> str` gains the `runtime` param (non-empty string wins; `"auto"` included). New `backends.language_detail(cfg, backend=None, runtime="") -> tuple[str, str]` returns `(language, source)` with `source ∈ {"cycle", "model", "general"}` for status/doctor. Existing callers/tests unaffected (`runtime` defaults to `""`).
4. **Guard lives in `DictationPipeline._transcribe`** (final decode only — preview partials must not double decode cost). Conditions: whitelist non-empty AND resolved language for the take == `"auto"` AND the backend surfaces a detected language AND `detected.split("-")[0]` not in `{w.split("-")[0] for w in whitelist}` → re-decode **once** with `language=whitelist[0]`, log one line `language guard: detected=<d> outside whitelist [<wl>]; re-decoded as <r>`, return the retry result. Detected language unavailable (whisper.cpp under auto) → silent skip; parakeet → not applicable. Backend capability is declared as a class attribute `surfaces_detected_language: bool` on each backend (True: faster-whisper, whisper-torch; False: whisper.cpp, parakeet) and mirrored in a static `backends.LANGUAGE_GUARD` map (name → applicability text) that doctor reads without instantiating a backend.
5. **Announcement** on every cycle press: pill badge `lang: <code>` when a preview display is up (mid-take switching works; the final decode resolves the language at process time, after stop), else `NotifyPreview().show("Language: <code>")` — literally the pill's existing notify fallback (same bypass of `notifications.enabled` as preview partials: it is UI feedback, not a notification).
6. **Config keys:** `general.language_cycle = []` (ordered, entries `"auto"` or a known code, ≤8, case-insensitive dedupe keep-first), `general.language_whitelist = []` (known codes only — `"auto"` rejected — ≤16, dedupe), `hotkey.language_key = ""` (keysym, `("str", 64)` range, validated at grab time by `HotkeyListener` exactly like `paste_key`). The known-code table moves to `config.py` as `KNOWN_LANGUAGES` (ordered list; gtkui re-imports it so the picker and validation cannot drift). Unknown codes are rejected by `coerce_setting` per the spec ("unknown codes rejected using the existing language table").
7. **Status surface (additive):** `handle_request("status")` gains `"language": {"effective": str, "source": "cycle"|"model"|"general", "cycle": [...], "cycle_engaged": bool, "whitelist": [...]}`. Doctor prints the live block when the daemon is up (`control.request("status")` behind the existing guarded pattern) and the static resolution otherwise.
8. **Tray tooltip:** `_tray_tooltip` appends ` — lang: <code>` whenever the cycle is engaged or the effective language != `"auto"`; cycle press calls `_refresh_tray()`.
9. **Wayland parity (cheap, follows the `sayit-ermano toggle` precedent):** socket action `cycle-language` + `sayit-ermano language` CLI subcommand, both routing to the same `Daemon._cycle_language()`.

## Files to touch

| File | Change |
|---|---|
| `fluidvoice/config.py` | `KNOWN_LANGUAGES` table (moved from gtkui); DEFAULTS (`general.language_cycle`, `general.language_whitelist`, `hotkey.language_key`); TEMPLATE docs; `_SAVE_WHITELIST` + `ALLOWED_SETTINGS` + `SETTING_RANGES` entries; `_coerce_language_cycle`, `_coerce_language_whitelist` wired into `coerce_setting` |
| `fluidvoice/backends/__init__.py` | `effective_language(..., runtime="")`; new `language_detail(...)`; `LANGUAGE_GUARD` map; `resolved_backend_name(cfg)` (load_backend's auto order via `_import_ok` probes, no instantiation) |
| `fluidvoice/backends/faster_whisper_backend.py`, `torch_whisper.py` | `surfaces_detected_language = True` |
| `fluidvoice/backends/whisper_cpp.py`, `parakeet_onnx.py` | `surfaces_detected_language = False` |
| `fluidvoice/daemon.py` | cycle state + `_cycle_language()` + `_effective_language()`/detail; pipeline `_language_override` plumb-through (`_process` :1747 area); whitelist guard in `DictationPipeline._transcribe`; `_announce_language()`; `_start_hotkey` language_key block (after paste_key, :870); `__init__` `_language_hotkey = None`; `_restart_hotkey` stop-tuple += `_language_hotkey`; `_tray_tooltip` lang segment; status `"language"` block; `handle_request` `cycle-language` action; preview (:1466) + `test_dictation` (:1663) resolve via the daemon helper |
| `fluidvoice/cli.py` | `language` subcommand → `control.request("cycle-language")` (mirrors toggle, :32-36/:107) |
| `fluidvoice/doctor.py` | extend `_language_lines`: cycle list + bound key, whitelist state, per-backend guard applicability via `LANGUAGE_GUARD`/`resolved_backend_name`, live effective source from the daemon when up |
| `fluidvoice/gtkui/settings_window.py` | import `KNOWN_LANGUAGES` from config (drop the local copy); General page: ordered cycle editor (mic_priority pattern) + whitelist comma row (`_ListProxy`); Hotkey page: `language_key` capture row; save-body collection for the cycle list |
| `tests/test_language_switch.py` | NEW — all feature tests (below) |
| `docs/STATUS.md`, `README.md` | one-paragraph feature note in the per-model-language neighborhood (STATUS.md :330, README :190) — last phase only |

---

## Phase 1 — Config foundation + language table

**Goal:** the three new keys exist, default off, validate strictly, round-trip through save/load; nothing else changes behavior.

1. `fluidvoice/config.py`:
   - Add module-level `KNOWN_LANGUAGES: list[str]` = the 30 codes currently in gtkui/settings_window.py:29-31 (comment: single source of truth for the picker and the new-key validation; `general.language` keeps its permissive grammar — a saved code outside the list stays legal there, as today).
   - `DEFAULTS["general"]["language_cycle"] = []`, `DEFAULTS["general"]["language_whitelist"] = []`, `DEFAULTS["hotkey"]["language_key"] = ""` (comment: `""` = off; cycle empty = feature off even when the key is bound; both are read live by the daemon).
   - `TEMPLATE`: under `[general]` after `language` — `language_cycle = []` (ordered codes the cycle hotkey steps through; may include "auto"; empty = off) and `language_whitelist = []` (when auto-detection lands outside these codes, one re-decode with the first entry); under `[hotkey]` — `language_key = ""` (keysym that cycles the language at runtime; never persisted state).
   - `_SAVE_WHITELIST["general"]` += `language_cycle`, `language_whitelist`; `_SAVE_WHITELIST["hotkey"]` += `language_key`; same for `ALLOWED_SETTINGS`.
   - `SETTING_RANGES[("hotkey", "language_key")] = ("str", 64)` (paste_key precedent). Note: the str-range rule rejects `""`, exactly as for `paste_key`/`rewrite_key`/`command_key` — an empty optional key never flows through `apply_settings` (the GTK `_collect()` skips empty strings at :365-368 and `save_config` carries the file value over), so socket-level tests must express "off" by OMITTING the key, not by posting `""` (do not add an `ai.base_prompt`-style empty-allowed branch — sibling consistency wins).
   - `_coerce_language_cycle(value)`: list of strings; each entry stripped, lowercased, must be `"auto"` or in `KNOWN_LANGUAGES` (case-insensitive); dedupe case-insensitively keeping the first occurrence; >8 entries or any unknown/empty entry rejects the whole value (docstring notes: ordered — order IS the feature).
   - `_coerce_language_whitelist(value)`: same but `"auto"` is rejected (it means "no constraint") and the cap is 16.
   - Wire both into `coerce_setting` (before the generic `SETTING_LISTS` fallthrough).
2. `fluidvoice/gtkui/settings_window.py`: replace the local `LANGUAGES = [...]` with `from ..config import KNOWN_LANGUAGES as LANGUAGES` (keep the name so the ~15 uses at :303-:638 are untouched). Pure rename-level change this phase.
3. `tests/test_language_switch.py` (new file) — `TestConfigValidation`:
   - cycle accepts `["auto", "en", "sl"]` verbatim; rejects `["auto", "xx"]` (unknown), `[""]`, non-list, >8 entries; dedupes `["EN", "en"]` → `["en"]` (order kept).
   - whitelist accepts `["sl", "en"]`; rejects `["auto"]`, `["xx"]`, >16, non-list; dedupes.
   - `apply_settings` round-trip: good body → `changed` contains the three keys; bad values → `rejected`, cfg untouched (follows tests/test_config_settings.py style).
   - `save_config` writes and `load_config` restores all three keys (tmp_path config file).
   - `load_config` on a file without the keys yields the defaults (empty/off).
4. Run `.venv/bin/python -m pytest -q tests --ignore=tests/integration` — green (test_config_settings / test_models_manager / test_gtkui must be untouched-green).

## Phase 2 — Resolution core + wrong-language guard (backends + pipeline)

**Goal:** precedence + guard are pure functions of (cfg, backend, runtime); no daemon wiring yet.

1. `fluidvoice/backends/__init__.py`:
   - `effective_language(cfg, backend=None, runtime: str = "")`: when `runtime` is a non-empty string it wins immediately (may be `"auto"`); otherwise the existing model.languages → general chain, unchanged.
   - `language_detail(cfg, backend=None, runtime="") -> tuple[str, str]`: same walk, returning `(language, "cycle"|"model"|"general")`; `effective_language` may delegate to it (keep both public names stable — tests/test_models_manager.py:49+ covers the old behavior).
   - `LANGUAGE_GUARD: dict[str, str]` keyed by backend name: `"faster-whisper"` and `"whisper-torch"` → `"language hint + detected language — whitelist guard active"`; `"whisper.cpp"` → `"language hint only — detected language is not surfaced under auto; guard skipped (limitation)"`; `"parakeet"` → `"no language selection (English-only models) — guard not applicable (upstream #100 rationale)"`.
   - `resolved_backend_name(cfg) -> str | None`: mirror `load_backend`'s branch order using `_import_ok`/`preload_cuda_libs`/`cuda_available`/`_whispercpp_binary` probes only (never instantiate); returns the canonical name or None when nothing resolves.
2. Backend class attributes: `surfaces_detected_language = True` on `FasterWhisperBackend` and `TorchWhisperBackend`; `= False` on `WhisperCppBackend` and `ParakeetOnnxBackend` (one line each, with a comment pointing at the guard).
3. `fluidvoice/daemon.py` — `DictationPipeline`:
   - `__init__(..., language_override: str | None = None)` stores `self._language_override` (None = not engaged; a string — including `"auto"` — engages). Default keeps every existing constructor call working.
   - `_transcribe` (daemon.py:107):
     ```python
     lang = self._language_override if self._language_override is not None \
         else backends.effective_language(self.cfg, self.backend)
     result = self.backend.transcribe(wav, language=lang)
     whitelist = [w for w in (self.cfg.get("general", {})
                               .get("language_whitelist") or [])]
     detected = str(result.get("language") or "").strip().lower() or None
     if (whitelist and lang == "auto" and detected
             and getattr(self.backend, "surfaces_detected_language", False)
             and detected.split("-")[0] not in
                 {w.split("-")[0] for w in whitelist}):
         retry = whitelist[0]
         self.log(f"language guard: detected={detected} outside whitelist "
                  f"[{', '.join(whitelist)}]; re-decoding as {retry}")
         result = self.backend.transcribe(wav, language=retry)
     return result
     ```
     (guard = final decode only; preview engines untouched — note in the docstring.)
4. `tests/test_language_switch.py` — `TestPrecedence` + `TestGuard`:
   - Precedence matrix with a fake backend exposing `model_name` (StubBackend pattern from tests/test_daemon.py): runtime beats per-model, per-model beats general, runtime `"auto"` still beats a per-model `"de"`, empty runtime falls through, missing model key falls through, `""` override values inherit.
   - `language_detail` source labels: `"cycle"`/`"model"`/`"general"` across the same matrix.
   - Guard retry: fake backend scripted to return `{"text": "русский халлюцинация…", "language": "ru"}` on call 1 and corrected text when `language == "sl"` on call 2; cfg `general.language="auto"`, `language_whitelist=["sl","en"]`; assert 2 calls, second with `"sl"`, final text is the corrected one, and the pipeline log line contains `detected=ru` and `re-decoding as sl`.
   - Guard off paths (each exactly 1 call): detected in whitelist; whitelist empty; effective language non-auto (e.g. cycle override `"sl"` via `language_override`); `surfaces_detected_language = False` backend (parakeet-class fake); detected `None` (whisper.cpp-class fake).
   - `language_override` honored end-to-end through `pipeline.run(...)` (backend.calls[0][1] == override).
5. Full suite green.

## Phase 3 — Daemon runtime cycle: hotkey, announce, status, tray, CLI

**Goal:** the key works against a live daemon; state never persists.

1. `fluidvoice/daemon.py` — `Daemon`:
   - `__init__`: `self._cycle_index: int | None = None`, `self._language_hotkey = None` (next to `_paste_hotkey`, :331).
   - `_cycle_list() -> list[str]`: reads `self.cfg["general"].get("language_cycle") or []` live.
   - `_cycle_language() -> dict`: the hotkey handler.
     - Locked-session guard first (`if self._locked: return` — same as `start_rewrite` :1339).
     - Empty list: `log("WARN language cycle: general.language_cycle is empty")` + `self.notify("SayItErmano", "Language cycle is empty — set general.language_cycle in Settings")`; return.
     - Else advance `self._cycle_index = 0 if None else (i + 1) % len(cycle)`; compute `(lang, source)` via `_language_detail()`; `log(f"language cycle -> {lang} ({source})")`; `self._announce_language(lang)`; `self._refresh_tray()`; return `{"ok": True, "language": lang, "source": source}` (socket shape).
   - `_language_detail() -> tuple[str, str]`: `runtime = cycle[self._cycle_index % len(cycle)]` when engaged and the list is non-empty, else `""`; returns `backends.language_detail(self.cfg, self.backend, runtime)`. (`self.backend` may be None — lazy — the helper handles it.)
   - `_announce_language(lang)`: if `self._preview` is a live tuple, `display = self._preview[1]`, `display.set_badge(f"lang: {lang}")` inside try/except (pill chip, `_set_pill_badge` precedent); on any miss or no live preview: `from .preview import NotifyPreview; NotifyPreview().show(f"Language: {lang}")` (the pill's own fallback).
   - `_start_hotkey` (after the paste_key block, :870): grab `language_key` exactly like paste_key — `HotkeyListener(key=language_key, modifiers=[], mode="toggle", on_toggle=lambda *_: self._cycle_language(), log=log)` (wrap in the same try/HotkeyError + `_log_grab_state(listener, "language ", key)` + `error = error or str(e)` pattern; store as `self._language_hotkey`).
   - `_restart_hotkey` (:927): add `"_language_hotkey"` to the stop-tuple so a Settings save re-grabs it (apply_config already restarts hotkeys for any `hotkey.*` change — `language_key` included; `language_cycle`/`language_whitelist` need nothing: they are read at use time).
   - `shutdown()` (:766-777): stop `self._language_hotkey` next to the `_paste_hotkey` stop (:772-773) so daemon shutdown releases the grab and thread.
   - `_process` (:1747 area): after `pipeline._profile_override = self._profile_override`, set `pipeline._language_override = self._cycle_runtime()` where `_cycle_runtime()` returns the engaged override string or `None` (clamped modulo the current list length). The override deliberately survives across takes (unlike the per-take profile override) — cycle state is sticky until cycled or the daemon restarts.
   - `_start_preview` (:1466) and `test_dictation` (:1663): replace `backends.effective_language(self.cfg, self.backend)` with `self._language_detail()[0]` so previews and the probe honor the cycle. (Preview keeps the language resolved at take start; the final decode re-resolves — documented in the code comment.)
   - `_tray_tooltip` (:705): before returning, `lang, source = self._language_detail()` (outside the recording lock) and append `f" — lang: {lang}"` when `source == "cycle"` or `lang != "auto"`.
   - `handle_request`: new action `"cycle-language"` → `{"ok": True, **self._cycle_language()}`; extend the `"status"` dict (near `"session"`, :1257) with the additive `"language"` block: `{"effective", "source", "cycle", "cycle_engaged", "whitelist"}`.
2. `fluidvoice/cli.py`: add `("language", "cycle the dictation language (language_cycle)")` to the subcommand list (:32-36) and to the dispatch at :107 → prints `cycled -> {resp.get('language')} ({resp.get('source')})` (mirror the toggle/cancel output style at :333).
3. `tests/test_language_switch.py` — `TestCycleStateMachine` + `TestDaemonWiring` (Daemon with `use_hotkey=False`, StubRecorder/StubBackend, `quiet_ui`):
   - Cycle `["auto","en","sl"]`: press ×4 → auto, en, sl, auto (wrap-around); initial state (no press) resolves per config.
   - Empty list: press is a no-op (index stays None, one notify recorded, log line).
   - Config shrink while engaged at index 2 → list of length 2 → index clamps (2 % 2 = 0 → "auto"); list emptied → override disengages.
   - Never persisted: after presses, `daemon.cfg["general"]` has no `language_cycle` mutation when unset, and `save_config` is not called (monkeypatch `fluidvoice.config.save_config` to record; or assert `daemon._set_config`-free run leaves the tmp config file unchanged).
   - Announce: with `daemon._preview = (None, fake_display)` → `fake_display.set_badge` called with `lang: en`; without preview → monkeypatched `preview.NotifyPreview.show` recorded. (Fakes follow the headless-fallback recipes in tests/test_overlay.py: `TestFluidOverlayFallback::test_falls_back_to_notify_when_display_unavailable` :169 monkeypatches `Xlib.display.Display` to raise + `NotifyPreview.show`; `TestSendBadge` :370 covers `set_badge`. Verified: `FluidOverlay.__init__` HAS the `actions` param — overlay.py:836 — the WIP daemon/overlay pair is internally consistent; the announce path builds no overlay of its own, so the finish/close-dismisses-bubble-early pitfall does not apply.) For the daemon-side fake, reuse/imitate `StubClosingDisplay` (tests/test_daemon.py ~:826 — records ("start"|"show"|"state"|"badge"|"finish"|"close") events, injected in `TestDoneBeat` ~:868); the `_start_preview`-built display itself needs monkeypatching `fluidvoice.overlay.FluidOverlay` / `fluidvoice.preview.NotifyPreview` rather than injection, and the `notifications.enabled`-independent gating of NotifyPreview is covered by the `TestNotifyPreview::test_no_notify_send_is_silent` pattern (monkeypatch `shutil.which` → None).
   - Status: `handle_request({"action": "status"})["language"]` reflects engaged state and effective source `cycle`.
   - Tooltip: `_tray_tooltip()` contains `lang: sl` when engaged; not appended when disengaged and effective is `"auto"`.
   - Hotkey wiring: `_start_hotkey` with `language_key="F7"` creates the listener (FakeListener pattern from tests/test_daemon.py:325 or the hotkey-grab fakes) and `on_toggle` routes to `_cycle_language`; `_restart_hotkey` stops the old one (fake records stop()).
   - `cycle-language` socket action cycles once and returns the new language; locked daemon ignores it.
   - `_process` pass-through: after two presses, a full take's `backend.calls[0][1]` == the engaged override (uses the `pipeline_factory` injection seam).
4. Full suite green.

## Phase 4 — Settings UI

**Goal:** the feature is configurable without editing TOML.

1. `fluidvoice/gtkui/settings_window.py` — General page (`_build_general`, after the Language combo :456-459):
   - **Language cycle** (ordered editor, mic_priority pattern :1275-1335): an `Adw.PreferencesGroup(title="Language cycle", description="Languages the cycle hotkey steps through (runtime only; never saved once engaged)")` with one `Adw.EntryRow(title="Code")` per entry + up/down/trash suffix buttons, plus an add button row (`Adw.ButtonRow` or the mic-priority add-row pattern); `_load_language_cycle` / `_add_cycle_lang` / `_move_cycle_lang` / `_rebuild_cycle_lang` / `_remove_cycle_lang` / `_collect_language_cycle` mirroring the mic-priority methods; on save, `body["general"]["language_cycle"] = self._collect_language_cycle()` next to the mic_priority line (:384). Subtitle hint: `"e.g. auto, en, sl — empty = hotkey off"`.
   - **Language whitelist**: `wl = Adw.EntryRow(title="Language whitelist — e.g. sl, en")`, `self._rows[("general", "language_whitelist")] = _ListProxy(wl)` (comma list, filler_words precedent :1585) — auto load/collect through the row registry.
   - Validation feedback: rely on the shared `coerce_setting` (rejected keys toast via the existing save-rejected path); additionally mark unknown codes in the cycle rows with `.add_css_class("error")` on `changed` when the stripped value is not `"auto"`/`KNOWN_LANGUAGES` (cheap client-side pre-flight; non-blocking).
2. Hotkey page (`_build_dictation`, after paste_key :1465): `hk.add(self._entry("hotkey", "language_key", "Cycle-language key (optional) — steps language_cycle", capture=True))` — the registry `_load()`/capture plumbing does the rest. (Capture plumbing is `_entry(..., capture=True)` :241 → `_start_capture` :434 → `_keyname` :1903 with `_KEY_REMAP` :22; nothing new needed. Semantics note: like `paste_key`/`rewrite_key`/`command_key`, an emptied field is skipped by `_collect()` (:365-368) and `save_config` carries the old file value over — "off" therefore behaves exactly like the sibling optional keys; do NOT add it to `_EMPTY_IS_MEANINGFUL`.)
3. `tests/test_language_switch.py` — `TestSettingsUI` (only when a display exists; follow the test_gtkui.py module-level skip): build the window with `StubClient`; set cycle rows en→sl→auto; collect body → `general.language_cycle == ["en","sl","auto"]`; move-up swaps; whitelist row `"sl, en"` → `["sl", "en"]`; language_key capture row round-trips a keysym. (Headless environments skip — suite stays green. `_keyname` can be unit-tested display-free, the tests/test_gtkui.py:599 `test_key_capture_maps_to_config_names` precedent.)
4. Full suite green.

## Phase 5 — Doctor + docs + live smoke

**Goal:** diagnosability and the ship-ready checklist.

1. `fluidvoice/doctor.py` — extend `_language_lines(cfg)` (:156-178):
   - Existing general/per-model/active-model lines unchanged.
   - `cycle:` line — the list (or "none") + `hotkey.language_key` binding ("key F7" or "no key bound — feature off") + `runtime: <effective> (source <cycle|model|general>)` **when the daemon answers** `control.request("status")` (socket-exists + try/except, the `_hotkey_grab_line` precedent :255-265); daemon down → `runtime: unknown (daemon down); cycle engages at runtime and is never persisted`.
   - `whitelist:` line — the list or "off (empty)".
   - `guard:` line — `LANGUAGE_GUARD[resolved_backend_name(cfg)]` (or "no backend resolves" when None); parakeet keeps its existing English-only note (:176).
   - `print("\nlanguage resolution:")` (:567) unchanged — the section simply grows.
2. `tests/test_language_switch.py` — `TestDoctor`: `_language_lines` includes the cycle/whitelist lines for a populated cfg; whisper.cpp resolution yields the "not surfaced under auto" limitation text; parakeet yields "not applicable"; faster-whisper yields "guard active"; daemon-down path (no socket in the XDG-isolated test env) prints the static variant. Monkeypatch `control.request` for the live variant.
3. Docs: `docs/STATUS.md` — extend the per-model-language entry neighborhood (:330) with the cycle + guard paragraph (runtime override precedence, guard semantics, whisper.cpp limitation); `README.md` — add the three keys to the config-keys list near the per-model language note (:190). One short paragraph each; no new sections.
4. Full suite: `.venv/bin/python -m pytest -q tests --ignore=tests/integration` — green, twice (flake check).

## Live smoke (manual, after Phase 5)

1. Config: `[general] language = "auto"`, `language_cycle = ["auto", "en", "sl"]`, `language_whitelist = ["sl", "en"]`, `[hotkey] language_key = "F7"`; restart the daemon (or save from Settings — the re-grab is live).
2. Press F7 three times → announcements cycle `auto → en → sl` (notify bubble, or pill badge mid-take); fourth press wraps to `auto`. Tray tooltip shows `lang: <code>`; `sayit-ermano status` shows the `"language"` block with `source: cycle`; `sayit-ermano language` cycles once.
3. `sayit-ermano doctor` → language section: cycle list + key, whitelist, per-backend guard line, effective source.
4. Slovenian dictation with `general.language=auto` + whitelist `[sl,en]`: the deterministic retry path is covered by `TestGuard::retry` (fake backend returning `ru` then the corrected `sl` text); live, watch the daemon log for `language guard: detected=ru outside whitelist [sl, en]; re-decoding as sl` on any take where detection misses — its absence on good takes is equally correct (guard is silent when detection is in-whitelist).
5. Restart the daemon → cycle state resets (not-engaged); config file unchanged by any number of presses.

## Out of scope (restated)

Automatic language-detection models (Silero/langid), app-UI translation, per-shortcut language profiles, model training/fine-tunes, remote endpoints, whisper.cpp verbose-output detected-language parsing (documented as a limitation instead).

## Verification gates (every phase)

- `.venv/bin/python -m pytest -q tests --ignore=tests/integration` — exit 0.
- No changes to files outside the table above; the other session's uncommitted work (config.py / daemon.py / settings_window.py / overlay.py / test_overlay.py modifications) is layered on top, never reverted.
- Judge commands by exit status, not output text.
