# Settings depth pack — prompt profiles, per-model language, model pruning

Closes three visible Settings gaps from `docs/ROADMAP.md`:
- v0.2: "User-editable prompt profiles (named presets of the base prompt)"
- v0.4: "Per-model language selection"
- v0.4: "Model manager … prune/freeing stays open"

Upstream semantics: `docs/UPSTREAM-TRACKING.md` line 111 ("Whisper per-model
language picker + `automatic` preserved"), `docs/BEHAVIOR-SPEC.md` §3 gap note
("upstream has separate whisper/cohere/nemotron language stores; we have one
global `language`").

## Corrections to the request's assumptions (verified in code)

1. **`ai.base_prompt` does not exist yet.** The dictation prompt is hard-coded
   in `fluidvoice/ai/prompts.py::default_dictation_prompt()`; it is consumed at
   `fluidvoice/ai/client.py:113` (`self.system_prompt = default_dictation_prompt()`)
   and `fluidvoice/daemon.py:120` (per-app compose via `system_prompt_for`).
   Phase 1 must introduce the editable config key; profiles (Phase 2) are built
   on top of it. There is also **no prompt editor in the GTK AI page today** —
   Phase 1 adds both the editor and the key.
2. **Socket actions dispatch in `fluidvoice/daemon.py::Daemon.handle_request`
   (~line 782), not `control.py`.** `control.py` is transport-only
   (serve/request) and needs **no changes**; the new `model-delete` action is a
   `handle_request` branch plus a `Daemon.delete_model()` method.
3. **Downloads already run in the GTK process** (`settings_window.py` calls
   `model_download.download_gguf/download_parakeet` in threads). Only
   *deletion* must be socket-only per the request; size computation is a
   direct read (same precedent as `dict_suggestions`).
4. Rewrite mode's edit prompt (`ai/prompts.py::BASE_EDIT_PROMPT`) is **out of
   scope** — v1 profiles are dictation-base-prompt presets only.

Baseline verified: `.venv/bin/python -m pytest -q tests --ignore=tests/integration`
→ **839 passed** (~30 s, GTK tests run here; they self-skip headless). Every
phase below must leave this command green.

## Key design decisions

- **Profiles are a sidecar JSON, not config** — `~/.config/sayit-ermano/
  prompt-profiles.json`, shape `{"name": "<base_prompt text>"}`, written
  directly by the client layer (exact precedent: `dictionary-suggestions.json`
  via `processing/dict_learn.py` `load_store`/`save_store` + `Client.
  dict_suggestion_dismiss`). The "set-config is the only write path" rule
  applies to `config.toml` keys; profile CRUD never touches config.toml. No
  active-profile pointer: loading copies text into the editor; config.toml
  stays the single source of truth.
- **Per-model language is one flat config key**: `model.languages` — a dict
  `{model_key: code}` valid across all three catalogs (keys are unique:
  `tiny…large-v3-turbo`, `ggml-*.bin`, `parakeet-*`). Value semantics
  (upstream "automatic preserved"): missing/`""` → inherit `general.language`;
  `"auto"` → force auto-detect for this model; `"de"` → lock. Resolution lives
  in ONE helper `backends.effective_language(cfg, backend=None)`; the existing
  per-backend `language=` plumbing enforces it (whisper.cpp `-l` at
  `whisper_cpp.py:56-58`, faster-whisper `language=` at
  `faster_whisper_backend.py:73`, torch at `torch_whisper.py:37`). Parakeet
  records the language but cannot constrain it (English/multilingual models) —
  UI shows the entry only for v3 (multilingual); doctor notes it.
  `model.languages` is deliberately NOT in `ENGINE_KEYS`/`RESTART_REQUIRED`:
  it is read per-dictation and applies live.
- **`model-delete` takes kind+name, never a raw path.** The daemon resolves
  the target under `paths.models_dir()` itself and then double-checks
  `target.resolve().is_relative_to(models_dir().resolve())` (symlink-safe:
  a link pointing outside resolves outside → refused). Refusals: active model
  (live-backend identity first, config-derived fallback), any in-flight
  warmup/download (`warmup["running"]`), unknown kind/name, missing target.
  The GTK app never deletes files — not even in daemon-offline degraded mode.

---

## Phase 1 — Editable base prompt (`ai.base_prompt`)

**Files:** `fluidvoice/config.py`, `fluidvoice/ai/client.py`,
`fluidvoice/daemon.py`, `fluidvoice/gtkui/settings_window.py`,
`tests/test_settings_profiles.py` (new).

1. `config.py`:
   - `DEFAULTS["ai"]["base_prompt"] = ""` (empty = built-in prompt).
   - `SETTING_RANGES[("ai", "base_prompt")] = ("str", 8000)` — but empty must
     be ACCEPTED (clearing the editor restores the built-in): add an explicit
     branch in `coerce_setting` before the range rule:
     `if (section, key) == ("ai", "base_prompt"): return (isinstance(value, str) and len(value) <= 8000, value)`.
   - Add `"base_prompt"` to `_SAVE_WHITELIST["ai"]` and
     `ALLOWED_SETTINGS["ai"]`. (`save_config` already skips empty values, so a
     cleared prompt drops out of the file → loads as "" → built-in.)
   - `TEMPLATE` `[ai]` section: comment
     `# Custom base prompt for AI polish (empty = built-in). Settings → AI can save named presets of it.`
2. `ai/client.py:113`: `self.system_prompt = ai.get("base_prompt") or default_dictation_prompt()`.
3. `daemon.py` `_polish` (~line 118-120): compute the base once with the same
   fallback, e.g. a tiny helper `ai/prompts.py::base_prompt_for(cfg)` →
   `cfg.get("ai", {}).get("base_prompt") or default_dictation_prompt()`, used
   by both `AIClient` and `system_prompt_for(base_prompt_for(self.cfg), instructions)`.
4. GTK AI page (`_build_ai`): new group **"Base prompt"** placed above the
   per-app rules group:
   - Multi-line editor: reuse the `_InstructionRow` pattern (TextView in a
     preferences row) + a new `_TextProxy` adapter registered in
     `self._rows[("ai", "base_prompt")]` (mirrors `_ListProxy`; `set_value`/
     `get_value` ↔ `Gtk.TextBuffer`). Add `_TextProxy` branches to `_load`
     and `_collect`.
   - **Collect special-case:** unlike EntryRow (where empty = "keep saved
     value"), base_prompt must POST even when empty — `""` is meaningful
     (reset to built-in). Handle in `_collect` for this key explicitly.
   - Subtitle "Empty = the built-in dictation prompt"; a flat "Insert
     built-in" button that loads `default_dictation_prompt()` into the buffer
     (editing from scratch is hostile otherwise). Editing marks dirty via
     `_touch()`; Save goes through the normal `save()` → `set-config`.
5. Tests (`tests/test_settings_profiles.py`, new file — module header copies
   the `tests/test_gtkui.py` skip guards: `pytest.importorskip("gi")`,
   version check, DISPLAY/WAYLAND check):
   - `coerce_setting` accepts "" and a 7 999-char string, rejects >8 000 /
     non-str; `apply_settings` round-trips `ai.base_prompt` into cfg.
   - `AIClient` with `ai.base_prompt="X"` uses "X"; with "" uses the built-in
     (starts with `"You are a voice-to-text dictation cleaner."`).
   - Pipeline `_polish` per-app compose uses the custom base (stub polisher
     captures the system prompt).
   - GTK: base-prompt row renders with cfg value; typing marks dirty; `save()`
     posts `ai.base_prompt`; clearing posts `""`.

**Phase gate:** suite green; a saved custom prompt demonstrably reaches the
AI request (unit-level).

## Phase 2 — Prompt profiles (sidecar CRUD + profile bar)

**Files:** `fluidvoice/paths.py`, `fluidvoice/ai/profiles.py` (new),
`fluidvoice/gtkui/client.py`, `fluidvoice/gtkui/settings_window.py`,
`tests/test_settings_profiles.py`, `tests/test_gtkui.py` (additive StubClient
methods only).

1. `paths.py`:
   ```python
   def prompt_profiles_file() -> Path:
       """Named presets of the AI base prompt ({name: prompt})."""
       return config_dir() / "prompt-profiles.json"
   ```
2. New `fluidvoice/ai/profiles.py` (stdlib only; mirrors dict_learn's store):
   - `load_profiles(path=None) -> dict[str, str]` — missing file → `{}`;
     unreadable/malformed JSON or non-dict → `{}` **with exactly one
     `log.warning`** ("prompt-profiles.json unreadable — starting empty"),
     never raises; valid dict keeps only `str:str` entries (values stripped?
     no — keep raw; drop only empty-NAME keys).
   - `save_profiles(profiles, path=None)` — atomic write (tmp + `os.replace`)
     and **`os.chmod(tmp, 0o600)`** before replace (config.py `_write_private`
     discipline; prompts may hold private content).
   - `save_named(name, prompt, path=None) -> dict` (upsert), `rename_profile
     (old, new, path=None) -> dict` (KeyError→ValueError if `old` missing),
     `delete_profile(name, path=None) -> dict`. Name validation: strip;
     non-empty; ≤64 chars; else `ValueError`. All take `path=None` →
     `paths.prompt_profiles_file()` (test seam).
3. `gtkui/client.py` (direct file access, dict_learn precedent — no socket):
   ```python
   def prompt_profiles(self) -> dict[str, str]
   def prompt_profile_save(self, name: str, prompt: str) -> dict   # {"ok": bool, "error": str|None, "profiles": {...}}
   def prompt_profile_rename(self, old: str, new: str) -> dict
   def prompt_profile_delete(self, name: str) -> dict
   ```
   Wrap profile-module errors into `{"ok": False, "error": ...}` so the UI
   only ever toasts.
4. GTK AI page — **profile bar ABOVE the prompt editor** (its own
   PreferencesGroup "Prompt profiles", description "Named presets of the base
   prompt — loading copies the text into the editor"):
   - `Adw.ComboRow "Profile"` (StringList of names; "— none —" when empty);
     selecting a profile copies its text into the base-prompt buffer and
     calls `_touch()` (dirty; the user then Saves to persist to config).
   - `Adw.EntryRow "Profile name"` + three buttons on one row: **Save**
     (writes the CURRENT editor text under the entry name — overwrite OK),
     **Rename** (selected profile → entry name; insensitive without a
     selection), **Delete** (flat destructive style).
   - Delete confirmation: `Adw.MessageDialog` DESTRUCTIVE, copied from
     `_confirm_clear_history` (heading "Delete profile ‘<name>’?", body notes
     the config's current prompt is not touched).
   - `_load_profiles()` on `_load()`; failures degrade to an empty combo.
5. `tests/test_gtkui.py` — **extend `StubClient` additively** with the four
   profile methods (record calls, return ok) — required because
   `SettingsWindow._load()` will call `self.c.prompt_profiles()` and the
   existing StubClient would raise AttributeError otherwise. No changes to
   existing assertions.
6. Tests in `tests/test_settings_profiles.py`:
   - Unit: save/load round-trip via explicit `path=tmp_path/...`; rename
     preserves order/content and removes the old key; delete removes only the
     named key; upsert overwrites; 0600 mode asserted (`stat().st_mode & 0o777`);
     malformed JSON / non-dict / wrong-typed values → `{}` (and caplog/
     monkeypatched log sees exactly one warning); bad names raise.
   - GTK: profile bar renders from stub profiles; selecting a profile copies
     text into the editor and sets `_dirty`; Save calls
     `c.prompt_profile_save(name, <editor text>)`; Delete shows the
     confirmation dialog and only deletes on the destructive response
     (drive via `dlg.emit("response", "delete")` or call the confirmed
     callback directly, following the clear-history test style);
     Rename/Save with an empty name toasts and does not write.

**Phase gate:** suite green; profiles survive "restart" (fresh
`load_profiles` on the same path); malformed file → empty list + one WARN.

## Phase 3 — Per-model language (`model.languages`)

**Files:** `fluidvoice/config.py`, `fluidvoice/backends/__init__.py`,
`fluidvoice/daemon.py`, the four backend constructors,
`fluidvoice/doctor.py`, `fluidvoice/gtkui/settings_window.py`,
`tests/test_models_manager.py` (new), `tests/test_gtkui.py` (nothing).

1. `config.py`:
   - `DEFAULTS["model"]["languages"] = {}`.
   - New `_coerce_model_languages(value)`: dict, ≤30 entries; keys non-empty
     str ≤64; values str matching the existing language regex
     `auto|[a-z]{2,3}(-[A-Za-z0-9]{2,8})?` (strip first); reject otherwise.
     Wire it: `ALLOWED_SETTINGS["model"] += {"languages"}`,
     `_SAVE_WHITELIST["model"] += ["languages"]`, and a branch in
     `coerce_setting` (like `mic_priority`). NOT in `ENGINE_KEYS`/
     `RESTART_REQUIRED`.
   - **`_toml_value` dict branch bug fix (required):** keys are currently
     emitted unquoted (`f"{k} = ..."`, config.py ~line 400) — `ggml-base.bin`
     contains dots and would round-trip as a nested table. Quote dict keys:
     bare when they match `[A-Za-z0-9_-]+`, else `json.dumps(k)`. Add a
     round-trip test: `save_config` with `languages = {"small": "de",
     "ggml-base.bin": "en"}` → `load_config` returns the same dict.
   - `TEMPLATE` `[model]` comment:
     `# Per-model language overrides, e.g. languages = { small = "de", "ggml-base.en.bin" = "en" }`
     `# "auto" = always detect for that model; a missing key follows general.language`.
2. `backends/__init__.py` — new helpers (model_catalog only via lazy import,
   same as `resolve_model_name`):
   ```python
   def backend_model_key(backend) -> str | None:
       # identity of a LIVE backend: FasterWhisperBackend.model_name /
       # ParakeetOnnxBackend.model_name / Path(WhisperCppBackend.model).name
   def config_model_key(cfg) -> str | None:
       # config-derived approximation: backend == "whisper.cpp" ->
       #   basename(whispercpp_model); "parakeet" -> name or
       #   PARAKEET_DEFAULT_MODEL; else resolve_model_name(name)
       # ("auto" backend simplification matches Daemon._active_model_name)
   def effective_language(cfg, backend=None) -> str:
       # override = model.languages.get(key) for the live-backend key (or
       # config key); override not in (None, "") -> override (may be "auto");
       # else cfg["general"]["language"] (default "auto")
   ```
3. `daemon.py`:
   - `DictationPipeline._transcribe` (line ~106):
     `return self.backend.transcribe(wav, language=backends.effective_language(self.cfg, self.backend))`.
   - `_start_preview` (~line 902): `faster_whisper_transcriber(model,
     backends.effective_language(self.cfg, self.backend))`.
   - `status` action: add `"active_model_key": backends.backend_model_key(
     self.backend) or backends.config_model_key(self.cfg)`.
4. Backend constructor fallbacks (defense in depth, same resolver):
   `faster_whisper_backend.py:28`, `torch_whisper.py:19`,
   `whisper_cpp.py:43`, `parakeet_onnx.py:221` — set
   `self.language = effective_language(cfg)` (keep each backend's existing
   auto/None mapping at transcribe time unchanged).
5. `doctor.py`: new `_language_lines(cfg)` printed under a
   `\nlanguage resolution:` header (style of `_formatting_lines`):
   `general: <general.language>`, `per-model overrides: {…} (model.languages)`,
   `active model <config_model_key> -> <effective_language(cfg)>` (+ note when
   parakeet v2 ignores the code).
6. GTK Models page: new PreferencesGroup **"Per-model language"** (below
   "Engine options", description "Overrides general.language per model —
   empty = inherit, auto = always detect"):
   - One `Adw.ComboRow` per **downloaded** model across catalogs
     (`model_catalog.model_downloaded(n)` for the six fw names,
     `gguf_downloaded`, `parakeet_downloaded`); values
     `[(inherit, ""), ("auto (detect)", "auto")] + LANGUAGES`, appending an
     unknown saved code like the general-language refill does. Parakeet v2
     (English-only) is skipped.
   - Rows managed by `_refresh_model_language_rows()` called from
     `_refresh_models()`: diff the desired name set against current rows and
     add/remove only the diff (entered selections survive, mic-priority
     discipline). Collect via a manual `_collect_model_languages()` merged
     into `body["model"]["languages"]`: shown rows — empty/inherit drops the
     key, a code sets it; entries for models **without** rows (not
     downloaded) are carried over from cfg unchanged.
7. Tests (`tests/test_models_manager.py`):
   - `effective_language`: no override → general; override "de" wins for the
     config key; "auto" override beats general "en"; live-backend key wins
     over config key; whisper.cpp path → basename key.
   - Pipeline: cfg with `model.languages = {"stub-ish": …}` — simplest is a
     stub backend exposing `model_name` matching the config key; assert the
     stub's `transcribe` received `language="de"` (copy the `StubBackend`
     calls-recording pattern from `tests/test_daemon.py:67`).
   - whisper.cpp arg plumbing: existing `TestWhisperCppModelResolution`
     fixture style (fake binary via monkeypatch, tmp XDG cache) — assert
     `-l de` present with an override and absent with "auto".
   - Config: `apply_settings` accepts/rejects shapes (non-dict, bad code,
     too many entries); TOML round-trip with dotted keys (Phase 3.1).
   - GTK: language rows render for downloaded models only (monkeypatch
     `sw.model_catalog.*_downloaded` like existing gguf tests); selecting a
     code and saving posts `model.languages`; inherit selection drops the key
     but preserves entries for unshown models; doctor lines smoke (call
     `_language_lines` directly).

**Phase gate:** suite green; a language set on a whisper model verifiably
reaches the backend invocation (stub assert + whisper.cpp argv assert).

## Phase 4 — Model pruning (disk usage + `model-delete`)

**Files:** `fluidvoice/model_catalog.py`, `fluidvoice/daemon.py`,
`fluidvoice/gtkui/client.py`, `fluidvoice/gtkui/settings_window.py`,
`fluidvoice/doctor.py`, `tests/test_models_manager.py`,
`tests/test_gtkui.py` (additive StubClient method).

1. `model_catalog.py`:
   ```python
   def _dir_size(p: Path) -> int            # os.walk sum of file sizes
   def cached_models() -> list[dict]        # [{kind, name, path, bytes}]
   def cache_entry_path(kind: str, name: str) -> Path   # ValueError on unknown
   ```
   Enumeration under `paths.models_dir()` ONLY (the legacy
   `cache_dir().parent / "huggingface" / "hub"` fallback stays unmanaged —
   note in doctor): `faster-whisper/models--*` dirs (kind `"faster-whisper"`;
   display name = reverse `FW_MODEL_REPOS` lookup, else the repo id),
   `whisper.cpp/ggml-*.bin` files (skip `*.part`; kind `"whisper.cpp"`),
   `parakeet/<name>` dirs (skip `.*` staging/tarballs; kind `"parakeet"`).
   `cache_entry_path` maps (kind, name) → the same paths (accepting repo id
   or short name for fw) so the daemon resolves targets without trusting
   client paths.
2. `daemon.py` — `handle_request` branch + method:
   ```python
   if action == "model-delete":
       return self.delete_model(str(req.get("kind", "")), str(req.get("name", "")))
   ```
   `delete_model(kind, name)`:
   1. resolve `target = model_catalog.cache_entry_path(kind, name)` (unknown
      kind/name → `{"ok": False, "error": …}`); missing on disk → ok-style
      error.
   2. confinement: `root = paths.models_dir().resolve()`;
      `t = target.resolve()`; refuse unless `t.is_relative_to(root)` AND
      `t != root` (never the root itself).
   3. refuse the active model: `name == backends.backend_model_key(self.backend)`
      or `== backends.config_model_key(self.cfg)` → "…is the active model
      (switch models first)".
   4. refuse while `self.warmup["running"]` → "a model load is in progress —
      try again once it finishes".
   5. delete (`shutil.rmtree` dir / `Path.unlink` file), return
      `{"ok": True, "path": str(t), "bytes": freed}`; log the line.
3. `gtkui/client.py`:
   ```python
   def model_delete(self, kind: str, name: str) -> dict   # socket only
   ```
   ClientError propagates (daemon down) — the window toasts
   "daemon not running — start it to delete models"; **no file-only fallback**.
4. GTK Models page — new PreferencesGroup **"Disk usage"** (last on the page,
   description "Cached models under ~/.cache/sayit-ermano/models"):
   - Header row: total bytes (human-readable, `_dl_subtitle` mb-style
     helper) + entry count; refreshed by `_refresh_disk_rows()` from
     `model_catalog.cached_models()` (direct read) inside `_refresh_models()`.
   - One row per entry: title `name`, subtitle `kind · <size> · <path>`;
     suffix **Delete** button (flat, destructive) for non-active entries;
     the active entry (cfg-derived `self._active_model() or _active_gguf()
     or _active_parakeet()`) gets an **insensitive** Delete with tooltip
     "This is the active model" per the request.
   - Delete click → `Adw.MessageDialog` DESTRUCTIVE confirmation (heading
     "Delete <name>?", body "Frees <size> from the models cache.") →
     `self.c.model_delete(kind, name)` in a worker thread (pattern of
     `_test_ai`), toast on completion with freed size or the refusal error,
     then `_refresh_models()`.
5. `doctor.py`: extend the bare `models cache:` line into
   `_models_cache_lines(cfg)`: one line per cached entry
   (`kind name <size>` + ` · ACTIVE` marker via `config_model_key`), a total
   line, and a note that the legacy `huggingface/hub` location is not
   manageable here.
6. `tests/test_gtkui.py`: add `model_delete` to `StubClient` (records
   `(kind, name)`).
7. Tests (`tests/test_models_manager.py`; conftest's XDG isolation already
   points `models_dir()` at the session tmp root):
   - `cached_models`: build decoys (fw repo dir with blobs, a `ggml-x.bin` +
     a stray `.part`, a parakeet dir + a `.tmp-` staging dir) → exactly the
     right entries with correct sizes; empty cache → `[]`.
   - `Daemon.delete_model` via `handle_request` with a stubbed backend
     (`model_name="small"`): decoy dir removed and `ok` + bytes reported;
     refuses the active model (fw name, gguf active name, parakeet name);
     refuses unknown kind/name; refuses a symlink inside the cache pointing
     outside (resolved path escapes root); refuses while
     `warmup["running"]`; missing target reports cleanly.
   - GTK: disk rows render with sizes; Delete on a non-active model posts
     `model_delete(kind, name)` only after the destructive confirmation;
     active model's button is insensitive with the tooltip.

**Phase gate:** suite green; a decoy cached model dir is removed via the
socket action; both the active model and any outside-root path are refused.

## Phase 5 — Docs

- `docs/ROADMAP.md`: tick the v0.2 prompt-profiles row and the v0.4
  per-model-language row; rewrite the v0.4 model-manager row's "prune/freeing
  stays open" tail (list/download/prune now all in Settings → Models).
- `docs/STATUS.md`: replace the "Per-model language selection (one global
  language today)" open item under speech with the shipped note (flat
  `model.languages` dict vs upstream per-store pickers; "auto" semantics
  preserved; parakeet v2 unaffected). Mention the `ai.base_prompt` addition
  under the AI section if one exists (follow the file's local style).
- No BEHAVIOR-SPEC edit required (its gap note about per-model language is
  historical; STATUS is the living divergence log — keep the change there).

**Phase gate:** suite green; docs consistent.

## Out of scope (restated)

Cloud prompt libraries, prompt versioning/diffing, per-dictation language
detection, auto-pruning policies (age/size thresholds), new model types,
rewrite/edit-prompt profiles, any UI beyond the three named sections, and
managing the legacy `~/.cache/huggingface/hub` location.

## Verification summary (done means)

- `.venv/bin/python -m pytest -q tests --ignore=tests/integration` green at
  the end of every phase (baseline 839 passed).
- Profiles survive restart (re-load from disk) and a malformed
  `prompt-profiles.json` degrades to `{}` with exactly one WARN, never a
  crash; file is 0600; delete is confirmation-gated.
- Setting a language on a whisper model verifiably reaches the backend
  invocation (stub-backend `language=` assertion + whisper.cpp `-l` argv
  assertion); `model.languages` round-trips through TOML with dotted keys.
- `model-delete` removes a decoy cached model dir over the socket and refuses
  the active model, in-flight warmups, unknown kinds, and any path resolving
  outside `paths.models_dir()`; the GTK app has no direct-delete code path.
