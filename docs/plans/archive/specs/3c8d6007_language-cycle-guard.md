# Plan: Fast language switching + wrong-language hallucination guard

**Session:** `3c8d6007` · **Spec:** `specs/3c8d6007_language-cycle-guard.md`
**Track:** C-track leapfrog (docs/research/2026-09-05-fluidvoice-reviews.md, insight 5)
**Why:** upstream #506 (runtime language switching) promised since 2026-04, still unshipped; #100 closed with "Parakeet doesn't allow language selection" while EN/DE users get Russian hallucinations on short takes; Superwhisper is recommended partly for automatic language switching. SayItErmano already has `general.language` + `model.languages`; the gaps are (a) switching requires opening Settings, (b) whisper-auto can lock onto the wrong language with no guard.
**Prior planning:** `specs/d9379f08_language-cycle-guard.md` planned this feature but it was never built (config.py has no `language_cycle`/`language_whitelist`/`language_key` keys). This spec re-baselines that plan onto the current tree — all anchors below were re-verified against HEAD `f405f6b` (the multi-shortcut WIP that plan had to layer around is now committed in 3411af3; **the tree is clean**).

## Verified facts the plan builds on (recon, primary reads against f405f6b)

- **Baseline:** `.venv/bin/python -m pytest -q tests --ignore=tests/integration` → **1533 passed** (~60 s). Working tree clean (only `.venv` symlink untracked).
- **Language resolution today:** `fluidvoice/backends/__init__.py:213` `effective_language(cfg, backend=None) -> str` resolves `model.languages[backend_model_key(backend)]` > `model.languages[config_model_key(cfg)]` > `general.language` (`""`/missing inherits; override may be `"auto"`). Call sites: `DictationPipeline._transcribe` (daemon.py:110), live preview start (daemon.py:1609), `test_dictation` (daemon.py:1807), doctor `_language_lines` (doctor.py:196).
- **Backends already take a language hint** in `transcribe(wav, language=None)` and return a `"language"` key:
  - `faster_whisper_backend.py:80-95` — hint via `WhisperModel.transcribe(language=...)`; **detected language surfaced** (`info.language`).
  - `torch_whisper.py:33-44` — hint via `whisper.transcribe(language=...)`; **detected language surfaced** (`result["language"]`).
  - `whisper_cpp.py:43-52` — hint via the `-l` flag; under `auto` returns `"language": None` (detected language exists only in verbose output we don't parse) → guard must skip silently (limitation, note in doctor).
  - `parakeet_onnx.py:298-317` — accepts the param but **never feeds the model** (English-only v2; v3 multilingual not language-addressable here; same rationale as upstream #100's closure). Echoes the hint back in the result.
- **Runtime-override precedent:** `Daemon._profile_override` (init daemon.py:342; set onto the pipeline at daemon.py:1898 `pipeline._profile_override = self._profile_override`, cleared in `finally` at :1905). Attribute injection, `getattr(self, "_profile_override", None)` at :122 — the language override copies this pattern exactly (constructor signature untouched).
- **Hotkey pattern for extra action keys:** `Daemon._start_hotkey` (daemon.py:821-933) builds one `HotkeyListener(key=..., modifiers=[], mode="toggle", on_toggle=<handler>, log=log)` per extra key: `rewrite_key` :847, `command_key` :868, `paste_key` :883-895, `extra_shortcuts` :897-917. Re-grab on settings change: `apply_config` restarts hotkeys for any `hotkey.*` change via `_restart_hotkey` (daemon.py:947-958) — its stop-tuple `("_hotkey", "_rewrite_hotkey", "_command_hotkey")` **must gain `_language_hotkey`**. Pre-existing gap (observation only — do NOT fix here): the tuple already omits `_paste_hotkey`/`_extra_hotkeys`.
- **hotkey.py needs no changes:** `HotkeyListener` is a generic single-key X11 listener; `resolve_keysym` validates keysym names (incl. `_KEY_ALIASES`) and raises `HotkeyError`; the callback is a zero-arg callable. `_start_hotkey` early-returns on Wayland (:828-834) — `language_key` is X11-only there; the `sayit-ermano language` CLI/socket action is the wayland path (same as every other action key today). Evdev PTT is primary-dictation-key-only — do NOT wire language_key into it. `Daemon.shutdown()` (:763-799) stops `_paste_hotkey` (:795-796) and `_extra_hotkeys` (:797-799).
- **Announcement surfaces:** pill `FluidOverlay.set_badge(text)` (overlay.py:944; daemon precedent `_set_pill_badge` daemon.py:214, used at :284-290). While recording, the daemon holds `self._preview = (engine, display)` (set daemon.py:1639, cleared :1645); `display` is either a `FluidOverlay` (has `set_badge`) or the fallback `NotifyPreview` (has only `show(text)` — preview.py). Fresh `NotifyPreview().show("Language: <code>")` is the no-preview fallback (the pill's own notify fallback; bypasses `notifications.enabled` like preview partials — it's UI feedback, not a notification).
- **Status/IPC:** `Daemon.handle_request` (daemon.py:1345) returns the status dict read by `sayit-ermano status`, the GTK client and doctor. `"status"` block includes `backend` (:1371) and a model-policy sub-dict — additive-key precedent for a `"language"` block.
- **Tray tooltip:** `Daemon._tray_tooltip` (daemon.py:722-741) is passed to `TrayIcon(tooltip=callable)` (tray.py:375); `Daemon._refresh_tray` (:936) pushes updates from any thread.
- **Doctor:** `_language_lines(cfg)` (doctor.py:182-200) printed at :595-596 under "language resolution:"; already has a per-model + parakeet English-only note (:198-200). Live-daemon query precedent: `_hotkey_grab_line` (doctor.py:282+) uses `control.request` behind socket-exists + try/except.
- **Backend availability vs resolution:** `backends.backend_status()` (:110-144) reports per-backend install state for doctor but does NOT resolve `backend=auto`; a probe-only `resolved_backend_name(cfg)` mirroring `load_backend`'s auto order (:229+: faster-whisper GPU via `_import_ok("faster_whisper") and preload_cuda_libs()`, then torch GPU via `cuda_available()`, then whisper.cpp via `_whispercpp_binary()`, then parakeet) is needed for the guard-applicability line.
- **Language code table:** `LANGUAGES` in gtkui/settings_window.py:29-31 (30 codes: en de es fr it nl pl pt ru uk sl sr hr bs cs sk sv da fi no hu ro bg el tr zh ja ko ar hi). Only enumeration in the codebase; `general.language` validation is permissive (regex, config.py `coerce_setting`) and STAYS permissive. The NEW keys must reject unknown codes against this table.
- **Settings UI:** General page `_build_general` (settings_window.py:460-471; Language combo :464-469); row registry `self._rows[(section, key)]` auto-loads in `_load()` (:293-341) / auto-collects in `_collect()` (:355-390); `_entry(section, key, title, capture=True)` (:241) is the key-capture row (Hotkey page rows at :1483-1497, `paste_key` at :1497); `_ListProxy` (:50-61, comma list — filler_words precedent) is the list row form; **mic_priority is the ordered-list editor precedent** (`_load_mic_priority`/`_add_mic_prio`/`_move_mic_prio`/`_rebuild_mic_prio`/`_remove_mic_prio`, settings_window.py:1302-1348+, saved into the body at :389-390 with an explicit "empty list is meaningful" comment). Save posts to the daemon's `set-config` → `config.apply_settings` + `save_config`; rejected keys come back and are toasted.
- **Config plumbing:** `DEFAULTS`, `TEMPLATE`, `_SAVE_WHITELIST`, `ALLOWED_SETTINGS`, `SETTING_RANGES`, `SETTING_ENUMS`, `coerce_setting` + per-key `_coerce_*` helpers (config.py). Sibling optional keysyms (`paste_key` etc.) use `("str", 64)` ranges — that rule rejects `""`, and an empty optional key never flows through `apply_settings` (GTK `_collect()` skips empty strings; `save_config` carries the file value over) — "off" = key omitted from the body. Empty LIST values DO round-trip: `save_config`'s `value in ("", None)` check is False for `[]`, so `language_cycle = []` persists as an explicit off (mic_priority semantics).
- **Test patterns:** tests/test_daemon.py — `make_wav` (:22), `StubRecorder` (:40), `StubBackend` (:67; records `(wav, language)` calls, returns `{"text", "language": "en", "duration"}`), `cfg` fixture (:83), `quiet_ui` (:88), `FakeListener` (:604), `FakeTray` (:660), `backend_factory`/`pipeline_factory` injection (:269). tests/test_hotkey_grab.py — X fakes. tests/test_gtkui.py — module-level skip without DISPLAY (suite stays green headless). `_language_lines`/`effective_language` tests live in tests/test_models_manager.py:49+.
- **Docs:** docs/STATUS.md per-model-language entry at :330; README.md language-key notes around :190 (and `--json` language note :303).

## Design decisions

1. **Cycle state = daemon-only runtime state.** `Daemon._cycle_index: int | None` (`None` = not engaged). Press → engage at index 0; each further press advances `(i+1) % len(cycle)`; wrap-around after the last entry. Effective language precedence per take: **runtime cycle override (even `"auto"`) > `model.languages[model_key]` > `general.language`**. Never written to config; daemon restart resets to not-engaged. `general.language_cycle` is re-read from `self.cfg` on every press/use, so Settings edits apply live; a shrunk list clamps the index modulo its new length; an emptied list disengages.
2. **Empty `language_cycle` = feature off**, even with `language_key` bound: the press logs a WARN and sends one gated notification ("Language cycle is empty — set general.language_cycle"), no state change.
3. **One resolution helper, extended.** `backends.effective_language(cfg, backend=None, runtime="")` gains the `runtime` param (non-empty string wins; `"auto"` included). New `backends.language_detail(cfg, backend=None, runtime="") -> tuple[str, str]` returns `(language, source)` with `source ∈ {"cycle", "model", "general"}` for status/doctor; `effective_language` delegates to it. Existing callers/tests unaffected (`runtime` defaults to `""`).
4. **Guard lives in `DictationPipeline._transcribe`** (final decode only — preview partials must not double decode cost). Conditions: whitelist non-empty AND resolved language for the take == `"auto"` AND the backend class declares `surfaces_detected_language` AND `detected.split("-")[0]` not in `{w.split("-")[0] for w in whitelist}` → re-decode **once** with `language=whitelist[0]`, log one line `language guard: detected=<d> outside whitelist [<wl>]; re-decoding as <r>`, return the retry result. Detected language unavailable (whisper.cpp under auto returns `None`) → silent skip; parakeet → not applicable (no language selection). Capability is a class attribute `surfaces_detected_language: bool` on each backend (True: faster-whisper, whisper-torch; False: whisper.cpp, parakeet) plus a static `backends.LANGUAGE_GUARD` map (name → applicability text) that doctor reads without instantiating a backend.
5. **Announcement** on every cycle press: pill badge `lang: <code>` when a live preview display has `set_badge`; else `display.show("Language: <code>")` when the display is the notify-fallback; else a fresh `NotifyPreview().show(...)`. All inside try/except (announcement must never break the cycle). Eyes-free usable mid-take.
6. **Config keys:** `general.language_cycle = []` (ordered; entries `"auto"` or a known code; ≤8; case-insensitive dedupe keep-first), `general.language_whitelist = []` (known codes only — `"auto"` rejected; ≤16; dedupe), `hotkey.language_key = ""` (keysym, `("str", 64)` range, validated at grab time by `HotkeyListener` exactly like `paste_key`). The known-code table moves to `config.py` as `KNOWN_LANGUAGES` (single source of truth; gtkui re-imports it as `LANGUAGES` so the picker and validation cannot drift). Unknown codes rejected by `coerce_setting` per the brief; `general.language` keeps its permissive grammar (a saved out-of-table code stays legal there, as today).
7. **Status surface (additive):** `handle_request("status")` gains `"language": {"effective": str, "source": "cycle"|"model"|"general", "cycle": [...], "cycle_engaged": bool, "whitelist": [...]}`. Doctor prints the live block when the daemon is up (the `_hotkey_grab_line` guarded pattern) and the static resolution otherwise.
8. **Tray tooltip:** `_tray_tooltip` appends ` — lang: <code>` whenever the cycle is engaged or the effective language != `"auto"`; a cycle press calls `_refresh_tray()`.
9. **Wayland parity (cheap, follows the `sayit-ermano toggle` precedent):** socket action `cycle-language` + `sayit-ermano language` CLI subcommand, both routing to the same `Daemon._cycle_language()`.
10. **Mid-take switching semantics:** a cycle press during a live take announces immediately; the preview engine keeps the language it captured at take start (daemon.py:1609); the FINAL decode re-resolves via `_process` at stop time, so the take ends in the newly selected language. Documented in the code comment.

## Files to touch

| File | Change |
|---|---|
| `fluidvoice/config.py` | `KNOWN_LANGUAGES` table (moved from gtkui); DEFAULTS (`general.language_cycle`, `general.language_whitelist`, `hotkey.language_key`); TEMPLATE docs; `_SAVE_WHITELIST` + `ALLOWED_SETTINGS` + `SETTING_RANGES` entries; `_coerce_language_cycle`, `_coerce_language_whitelist` wired into `coerce_setting` |
| `fluidvoice/backends/__init__.py` | `effective_language(..., runtime="")`; new `language_detail(...)`; `LANGUAGE_GUARD` map; `resolved_backend_name(cfg)` (load_backend's auto order via probes only, no instantiation) |
| `fluidvoice/backends/faster_whisper_backend.py`, `torch_whisper.py` | class attr `surfaces_detected_language = True` |
| `fluidvoice/backends/whisper_cpp.py`, `parakeet_onnx.py` | class attr `surfaces_detected_language = False` (comment: guard reads this) |
| `fluidvoice/daemon.py` | cycle state + `_cycle_language()` + `_language_detail()`/`_cycle_runtime()`; pipeline `_language_override` plumb-through at `_process` :1898; whitelist guard in `DictationPipeline._transcribe`; `_announce_language()`; `_start_hotkey` language_key block (after paste_key :895); `__init__` `_language_hotkey = None` (~:340); `_restart_hotkey` stop-tuple += `_language_hotkey`; `shutdown()` stop next to `_paste_hotkey`; `_tray_tooltip` lang segment; status `"language"` block; `handle_request` `cycle-language` action; preview (:1609) + `test_dictation` (:1807) resolve via the daemon helper |
| `fluidvoice/cli.py` | `language` subcommand → `control.request("cycle-language")` (subcommand tuple :33-36, dispatch `if args.cmd in (...)` ~:107) |
| `fluidvoice/doctor.py` | extend `_language_lines` (:182-200): cycle list + bound key, whitelist state, per-backend guard applicability via `LANGUAGE_GUARD`/`resolved_backend_name`, live effective source from the daemon when up |
| `fluidvoice/gtkui/settings_window.py` | import `KNOWN_LANGUAGES` from config (drop the local copy); General page: ordered cycle editor (mic_priority pattern) + whitelist comma row (`_ListProxy`); Hotkey page: `language_key` capture row; save-body collection for the cycle list |
| `tests/test_language_switch.py` | NEW — all feature tests (per phase below) |
| `docs/STATUS.md`, `README.md` | one-paragraph feature note in the per-model-language neighborhood (STATUS.md :330, README :190) — last phase only |

---

## Phase 1 — Config foundation + language table

**Goal:** the three new keys exist, default off, validate strictly, round-trip through save/load; nothing else changes behavior.

1. `fluidvoice/config.py`:
   - Module-level `KNOWN_LANGUAGES: list[str]` = the 30 codes from gtkui/settings_window.py:29-31 (comment: single source of truth for the picker and the new-key validation; `general.language` keeps its permissive grammar).
   - `DEFAULTS["general"]["language_cycle"] = []`, `DEFAULTS["general"]["language_whitelist"] = []`, `DEFAULTS["hotkey"]["language_key"] = ""` (comment: `""` = off; empty cycle = feature off even when the key is bound; both lists are read live by the daemon).
   - `TEMPLATE`: under `[general]` after `language` — `language_cycle = []` (ordered codes the cycle hotkey steps through; may include "auto"; empty = off) and `language_whitelist = []` (when auto-detection lands outside these codes, one re-decode with the first entry); under `[hotkey]` — `language_key = ""` (keysym that cycles the language at runtime; the cycle state itself is never persisted).
   - `_SAVE_WHITELIST["general"]` += `language_cycle`, `language_whitelist`; `_SAVE_WHITELIST["hotkey"]` += `language_key`; same for `ALLOWED_SETTINGS`.
   - `SETTING_RANGES[("hotkey", "language_key")] = ("str", 64)` (paste_key precedent — the rule rejects `""`; "off" = key omitted from the save body, exactly like `paste_key`/`rewrite_key`/`command_key`; do NOT add an `_EMPTY_IS_MEANINGFUL` branch).
   - `_coerce_language_cycle(value)`: must be a list of strings; each entry stripped + lowercased, must be `"auto"` or in `KNOWN_LANGUAGES` (case-insensitive); dedupe case-insensitively keeping the first occurrence; >8 entries or any unknown/empty/non-str entry rejects the whole value (docstring: ordered — order IS the feature).
   - `_coerce_language_whitelist(value)`: same shape, but `"auto"` is REJECTED (it means "no constraint") and the cap is 16.
   - Wire both into `coerce_setting` (explicit `(section, key)` branches, before the `SETTING_LISTS` fallthrough).
2. `fluidvoice/gtkui/settings_window.py`: replace the local `LANGUAGES = [...]` (:29-31) with `from ..config import KNOWN_LANGUAGES as LANGUAGES` (keep the name so the existing uses — the language combo :464-469 etc. — are untouched). Pure import swap this phase.
3. `tests/test_language_switch.py` (new file) — `TestConfigValidation`:
   - cycle accepts `["auto", "en", "sl"]` verbatim; rejects `["auto", "xx"]` (unknown), `[""]`, non-list, >8 entries; dedupes `["EN", "en"]` → `["en"]` (order kept).
   - whitelist accepts `["sl", "en"]`; rejects `["auto"]`, `["xx"]`, >16, non-list; dedupes.
   - `apply_settings` round-trip: good body → `changed` contains the three keys; bad values → `rejected`, cfg untouched (tests/test_config_settings.py style).
   - `save_config` writes and `load_config` restores all three keys (tmp_path config file); `[]` persists as `key = []` (mic_priority semantics — removal round-trips).
   - `load_config` on a file without the keys yields the defaults (empty/off).
4. Run `.venv/bin/python -m pytest -q tests --ignore=tests/integration` — green (test_config_settings / test_models_manager / test_gtkui untouched-green).

## Phase 2 — Resolution core + wrong-language guard (backends + pipeline)

**Goal:** precedence + guard are pure functions of (cfg, backend, runtime); no daemon wiring yet.

1. `fluidvoice/backends/__init__.py`:
   - `effective_language(cfg, backend=None, runtime: str = "")`: when `runtime` is a non-empty string it wins immediately (may be `"auto"`); otherwise the existing model.languages → general chain, byte-identical.
   - `language_detail(cfg, backend=None, runtime: str = "") -> tuple[str, str]`: same walk, returning `(language, "cycle"|"model"|"general")`; `effective_language` delegates to it (both public names stay stable — tests/test_models_manager.py:49+ covers the old behavior).
   - `LANGUAGE_GUARD: dict[str, str]` keyed by backend name: `"faster-whisper"` and `"whisper-torch"` → `"language hint + detected language — whitelist guard active"`; `"whisper.cpp"` → `"language hint only — detected language not surfaced under auto; guard skipped (limitation)"`; `"parakeet"` → `"no language selection (English-only models) — guard not applicable (upstream #100 rationale)"`.
   - `resolved_backend_name(cfg) -> str | None`: mirror `load_backend`'s branch order using only `_import_ok`/`preload_cuda_libs`/`cuda_available`/`_whispercpp_binary` probes (never instantiate); an explicit non-auto backend returns itself when its probe passes, else None; `"auto"` returns the first resolving name or None.
2. Backend class attributes (one line + comment each): `surfaces_detected_language = True` on `FasterWhisperBackend` and `TorchWhisperBackend`; `= False` on `WhisperCppBackend` and `ParakeetOnnxBackend`.
3. `fluidvoice/daemon.py` — `DictationPipeline`:
   - `_transcribe` (:107-110) becomes:
     ```python
     def _transcribe(self, wav: Path) -> dict:
         override = getattr(self, "_language_override", None)
         lang = override if override else backends.effective_language(
             self.cfg, self.backend)
         result = self.backend.transcribe(wav, language=lang)
         # wrong-language guard: final decode only (preview partials never
         # re-decode); one retry with whitelist[0] when auto-detection
         # landed outside the whitelist
         whitelist = list((self.cfg.get("general", {})
                           .get("language_whitelist")) or [])
         detected = str(result.get("language") or "").strip().lower() or None
         if (whitelist and lang == "auto" and detected
                 and getattr(self.backend, "surfaces_detected_language", False)
                 and detected.split("-")[0] not in
                     {w.split("-")[0] for w in whitelist}):
             retry = whitelist[0]
             self.log(f"language guard: detected={detected} outside "
                      f"whitelist [{', '.join(whitelist)}]; "
                      f"re-decoding as {retry}")
             result = self.backend.transcribe(wav, language=retry)
         return result
     ```
     (`self.log` is the pipeline logger attr set in `__init__`; attribute-injection via `getattr` mirrors `_profile_override` at :122 — no constructor change.)
4. `tests/test_language_switch.py` — `TestPrecedence` + `TestGuard`:
   - Precedence matrix with a fake backend exposing `model_name` (StubBackend pattern, tests/test_daemon.py:67): runtime beats per-model, per-model beats general, runtime `"auto"` still beats a per-model `"de"`, empty runtime falls through, missing model key falls through, `""` override values inherit.
   - `language_detail` source labels: `"cycle"`/`"model"`/`"general"` across the same matrix.
   - Guard retry: fake backend scripted to return `{"text": "русский халлюцинация…", "language": "ru"}` on call 1 and corrected text when `language == "sl"` on call 2; cfg `general.language="auto"`, `language_whitelist=["sl","en"]`; assert 2 calls, second with `"sl"`, final text corrected, and the pipeline log line contains `detected=ru` and `re-decoding as sl`.
   - Guard off paths (each exactly 1 call): detected in whitelist; whitelist empty; effective language non-auto (override `"sl"` via `pipeline._language_override = "sl"`); `surfaces_detected_language = False` fake (parakeet-class); detected `None` (whisper.cpp-class).
   - Override honored end-to-end through `pipeline.run(...)` (backend.calls[0][1] == override).
5. Full suite green.

## Phase 3 — Daemon runtime cycle: hotkey, announce, status, tray, CLI

**Goal:** the key works against a live daemon; state never persists.

1. `fluidvoice/daemon.py` — `Daemon`:
   - `__init__` (~:340-344 neighborhood, next to `_paste_hotkey`/`_profile_override`/`_preview`): `self._cycle_index: int | None = None`, `self._language_hotkey = None`.
   - `_cycle_list() -> list[str]`: `list(self.cfg.get("general", {}).get("language_cycle") or [])` — read live every call.
   - `_cycle_language() -> dict`: the hotkey handler.
     - Locked-session guard first (`if self._locked: return {"ok": False, "error": "locked"}` — same guard the action handlers use, e.g. :1476).
     - Empty list: `log("WARN language cycle: general.language_cycle is empty")` + one `ui.notify("SayItErmano", "Language cycle is empty — set general.language_cycle in Settings", enabled=self.cfg["notifications"]["enabled"])`; return `{"ok": False, "error": "empty cycle"}`.
     - Else advance `self._cycle_index = 0 if self._cycle_index is None else (self._cycle_index + 1) % len(cycle)`; `lang, source = self._language_detail()`; `log(f"language cycle -> {lang} ({source})")`; `self._announce_language(lang)`; `self._refresh_tray()`; return `{"ok": True, "language": lang, "source": source}`.
   - `_cycle_runtime() -> str | None`: when `_cycle_index` is not None and the list is non-empty → `cycle[_cycle_index % len(cycle)]`; else `None` (disengaged — covers a list emptied under an engaged index).
   - `_language_detail() -> tuple[str, str]`: `runtime = self._cycle_runtime() or ""`; `return backends.language_detail(self.cfg, self.backend, runtime)` (`self.backend` may be None — lazy; the helper handles it via `config_model_key`).
   - `_announce_language(lang)`: `display = self._preview[1]` when `self._preview` is a non-empty tuple; if it has a callable `set_badge` → `set_badge(f"lang: {lang}")`; elif it has `show` → `show(f"Language: {lang}")`; else fresh `from .preview import NotifyPreview; NotifyPreview().show(f"Language: {lang}")`. Whole body in try/except (log on failure, never raise).
   - `_start_hotkey`: after the paste_key block (:883-895), same shape —
     ```python
     language_key = (hk.get("language_key") or "").strip()
     if language_key:
         try:
             self._language_hotkey = HotkeyListener(
                 key=language_key, modifiers=[], mode="toggle",
                 on_toggle=lambda *_: self._cycle_language(), log=log)
             self._language_hotkey.start()
             for line in self._language_hotkey.summary:
                 log(line)
             self._log_grab_state(self._language_hotkey, "language ", language_key)
         except HotkeyError as e:
             self._language_hotkey = None
             log(f"WARN language hotkey unavailable: {e}")
             error = error or str(e)
     ```
   - `_restart_hotkey` (:947-958): add `"_language_hotkey"` to the stop-tuple. (`apply_config` already restarts hotkeys on any `hotkey.*` change — `language_key` included; `language_cycle`/`language_whitelist` need nothing: they are read at use time.)
   - `shutdown()` (:763-799): stop `self._language_hotkey` next to the `_paste_hotkey` stop (:795-796).
   - `_process` (:1898): after `pipeline._profile_override = self._profile_override`, add `pipeline._language_override = self._cycle_runtime()`. The override deliberately SURVIVES across takes (do NOT clear it in the `finally` — cycle state is sticky until cycled away or the daemon restarts; the profile override clears because it is per-take).
   - `_start_preview` (:1609) and `test_dictation` (:1807): replace `backends.effective_language(self.cfg, self.backend)` with `self._language_detail()[0]` so previews and the probe honor the cycle (preview captures at take start; final decode re-resolves — comment it).
   - `_tray_tooltip` (:722-741): before returning, `lang, source = self._language_detail()` and append `f" — lang: {lang}"` when `source == "cycle"` or `lang != "auto"`.
   - `handle_request` (:1345): new action `"cycle-language"` → `{"ok": True, **self._cycle_language()}` (flatten so `ok` from the inner dict wins); extend the `"status"` dict (near `"session"`/`"active_model"`, ~:1390) with the additive `"language"` block: `{"effective", "source", "cycle", "cycle_engaged", "whitelist"}`.
2. `fluidvoice/cli.py`: add `("language", "cycle the dictation language (general.language_cycle)")` to the action subcommand tuple (:33-36) and to the dispatch membership test (`if args.cmd in ("toggle", "cancel", "status", "paste-last", "language")`, ~:107); non-JSON output prints `cycled -> {resp.get('language')} ({resp.get('source')})` mirroring the toggle style.
3. `tests/test_language_switch.py` — `TestCycleStateMachine` + `TestDaemonWiring` (Daemon with `use_hotkey=False`, StubRecorder/StubBackend, `quiet_ui`; follow tests/test_daemon.py construction):
   - Cycle `["auto","en","sl"]`: press ×4 → auto, en, sl, auto (wrap-around); initial state (no press) resolves per config (source model/general).
   - Empty list: press is a no-op (index stays None, one notify recorded, WARN logged).
   - Config shrink while engaged at index 2 → list of length 2 → clamp (2 % 2 = 0 → first entry); list emptied → override disengages (`_cycle_runtime() is None`).
   - Never persisted: after several presses, `save_config` was never called (monkeypatch `fluidvoice.config.save_config` to record) and `daemon.cfg["general"]["language_cycle"]` is unchanged.
   - Announce: with `daemon._preview = (None, fake_display)` where the fake records `set_badge` calls → called with `lang: en`; with a fake exposing only `show` → `show("Language: en")`; with `daemon._preview = None` and monkeypatched `fluidvoice.preview.NotifyPreview.show` → recorded. (Overlay/notify fake recipes: tests/test_overlay.py `TestFluidOverlayFallback` + `TestSendBadge` patterns; NotifyPreview headless silence: `TestNotifyPreview` monkeypatching `shutil.which`.)
   - Status: `handle_request({"action": "status"})["language"]` reflects engaged state, effective source `"cycle"`, the configured lists.
   - Tooltip: `_tray_tooltip()` contains `lang: sl` when engaged; no lang segment when disengaged and effective is `"auto"`.
   - Hotkey wiring: `_start_hotkey` with `language_key="F7"` creates the listener (FakeListener pattern tests/test_daemon.py:604 / hotkey-grab fakes) and its toggle callback routes to `_cycle_language`; `_restart_hotkey` stops the old one (fake records `stop()`).
   - `cycle-language` socket action cycles once and returns the new language; locked daemon (`daemon._locked = True`) ignores it.
   - `_process` pass-through: after presses, a full take's `backend.calls[0][1]` == the engaged override (via the `pipeline_factory` seam with the real `DictationPipeline` — attribute injection means the default factory needs no change).
4. Full suite green.

## Phase 4 — Settings UI

**Goal:** the feature is configurable without editing TOML.

1. `fluidvoice/gtkui/settings_window.py` — General page (`_build_general` :460, after the Language combo :469):
   - **Language cycle** (ordered editor, mic_priority pattern :1302-1348): `Adw.PreferencesGroup(title="Language cycle", description="Languages the cycle key steps through (runtime state; never persisted)")`; one `Adw.EntryRow(title="Code")` per entry + up/down/trash suffix buttons + an add-row; `_load_language_cycle`/`_add_cycle_lang`/`_move_cycle_lang`/`_rebuild_cycle_lang`/`_remove_cycle_lang`/`_collect_language_cycle` mirroring the mic-priority methods; on save, `body.setdefault("general", {})["language_cycle"] = self._collect_language_cycle()` next to the mic_priority line (:389-390; empty list is meaningful — removals). Subtitle hint: `"e.g. auto, en, sl — empty = cycle key off"`.
   - **Language whitelist**: `wl = Adw.EntryRow(title="Language whitelist — e.g. sl, en")`; `self._rows[("general", "language_whitelist")] = _ListProxy(wl)` (comma list, filler_words precedent) — auto load/collect through the row registry.
   - Client-side pre-flight: on `changed`, `.add_css_class("error")` when a stripped cycle value is neither `"auto"` nor in `KNOWN_LANGUAGES` (non-blocking; the daemon's `coerce_setting` is the authority and toasts rejects).
2. Hotkey page (`_build_dictation`, after the paste_key row :1497): `hk.add(self._entry("hotkey", "language_key", "Cycle-language key (optional) — steps language_cycle", capture=True))` — the registry `_load()`/capture plumbing does the rest. (Emptied field is skipped by `_collect()` — "off" behaves exactly like the sibling optional keys; do NOT add it to `_EMPTY_IS_MEANINGFUL`.)
3. `tests/test_language_switch.py` — `TestSettingsUI` (display-gated like tests/test_gtkui.py; module-level skip keeps the headless suite green): build the window with `StubClient`; add cycle rows en→sl→auto; collect body → `general.language_cycle == ["en","sl","auto"]`; move-up swaps; whitelist row `"sl, en"` → `["sl","en"]`; `language_key` capture row round-trips a keysym (`_keyname` mapping can be unit-tested display-free).
4. Full suite green.

## Phase 5 — Doctor + docs + live smoke

**Goal:** diagnosability and the ship-ready checklist.

1. `fluidvoice/doctor.py` — extend `_language_lines(cfg)` (:182-200):
   - Existing general/per-model/active-model/parakeet lines unchanged.
   - `cycle:` line — the list (or "none") + `hotkey.language_key` binding ("key F7" or "no key bound — feature off") + `runtime: <effective> (source <cycle|model|general>)` **when the daemon answers** `control.request("status")` (socket-exists + try/except, `_hotkey_grab_line` precedent :282+; read `resp["language"]`); daemon down → `runtime: unknown (daemon down); the cycle engages at runtime and is never persisted`.
   - `whitelist:` line — the list or "off (empty)".
   - `guard:` line — `LANGUAGE_GUARD[resolved_backend_name(cfg)]` (or "no backend resolves" when None).
   - `print("\nlanguage resolution:")` at :595 stays — the section simply grows.
2. `tests/test_language_switch.py` — `TestDoctor`: `_language_lines` includes the cycle/whitelist/guard lines for a populated cfg; whisper.cpp resolution yields the "not surfaced under auto" limitation text; parakeet yields "not applicable"; faster-whisper yields "guard active"; daemon-down path prints the static variant (no socket in the XDG-isolated test env); live variant via monkeypatched `control.request`.
3. Docs: `docs/STATUS.md` — extend the per-model-language entry neighborhood (:330) with the cycle + guard paragraph (runtime override precedence, guard semantics, whisper.cpp limitation); `README.md` — add the three keys to the config-keys list near the per-model language note (:190). One short paragraph each; no new sections.
4. Full suite: `.venv/bin/python -m pytest -q tests --ignore=tests/integration` — green, run twice (flake check).

## Live smoke (manual, after Phase 5; AGENTS.md housekeeping applies)

1. Housekeeping: restarting the production daemon is two-step — stop the `sayit-ermano` user unit, run the repo daemon, `systemctl --user start sayit-ermano` afterwards. Restore any config keys flipped in `~/.config/sayit-ermano/config.toml`.
2. Config: `[general] language = "auto"`, `language_cycle = ["auto", "en", "sl"]`, `language_whitelist = ["sl", "en"]`, `[hotkey] language_key = "F7"`; restart the daemon (or save from Settings — the re-grab is live).
3. Press F7 three times → announcements cycle `auto → en → sl` (notify bubble, or pill badge mid-take); fourth press wraps to `auto`. Tray tooltip shows `lang: <code>`; `sayit-ermano status` shows the `"language"` block with `source: cycle`; `sayit-ermano language` cycles once.
4. `sayit-ermano doctor` → language section: cycle list + key, whitelist, per-backend guard line, effective source.
5. Slovenian dictation with `general.language=auto` + whitelist `[sl,en]`: the deterministic retry path is covered by `TestGuard` (fake backend returning `ru` then corrected `sl` text); live, watch the daemon log for `language guard: detected=ru outside whitelist [sl, en]; re-decoding as sl` on any take where detection misses — its absence on good takes is equally correct (the guard is silent when detection is in-whitelist).
6. Restart the daemon → cycle state resets (not-engaged); the config file is unchanged by any number of presses.

## Out of scope (restated)

Automatic language-detection models (Silero/langid), app-UI translation, per-shortcut language profiles, model training/fine-tunes, remote endpoints, whisper.cpp verbose-output detected-language parsing (documented as a limitation instead), fixing the pre-existing `_restart_hotkey` stop-tuple gap for `_paste_hotkey`/`_extra_hotkeys`.

## Verification gates (every phase)

- `.venv/bin/python -m pytest -q tests --ignore=tests/integration` — exit 0 (judge by exit status, never by scanning output).
- No changes to files outside the table above; no `git restore`/`checkout --` of anything not created in this session (AGENTS.md one-tree rule; commit on `agent/linux`).
- Each phase commits green on `agent/linux`; merge-back via `git -C ../FluidVoiceLinux merge --ff-only agent/linux` when clear, push `linux` only.
