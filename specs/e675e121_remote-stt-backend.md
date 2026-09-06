# Plan: Remote OpenAI-compatible STT backend (`remote_url`)

## Context & verified facts (read before building)

Source request: docs/research/2026-09-05-fluidvoice-reviews.md insight 12 — upstream
users want to point the app at a LAN GPU box serving Whisper/Parakeet via an
OpenAI-compatible `POST /v1/audio/transcriptions` endpoint (vLLM / NIM / DGX Spark);
the upstream maintainer **declined** the community's opt-in PR in favor of a native
protocol, so this is a differentiator upstream will not ship. SayItErmano stays
local-first: the remote backend exists only when the user configures a URL.

Recon results (all verified in this repo):

- **Audio format**: `fluidvoice/recorder.py` records headerless raw PCM and
  `Recorder.stop()` wraps it into a **16 kHz mono s16 WAV**; `DictationPipeline.run`
  calls `pad_wav(wav)` (≥1 s) then `backend.transcribe(wav_path, language)`.
  → The `file` multipart field is **WAV, no re-encoding**.
- **Backend seam**: `fluidvoice/backends/__init__.py` `load_backend(cfg)` is the
  single selection seam used by the daemon (`_backend_factory`, `_ensure_backend`,
  `_reload_backend`, `_warmup_model`) and `tests/test_backends_selection.py`.
  Backend contract: `.name`, `.transcribe(wav_path, language) -> dict` with
  `{"text": str, "language": str|None, "duration": None, "segments": [dict]}`,
  optional `.warmup()`. Local backends also expose `.model_name` /
  `.model` used by `backend_model_key()` for per-model language overrides and the
  delete-model guard.
- **HTTP client**: no `requests` dependency (the repo-root `requests/` dir is spec
  docs, not the library). The established pattern is **stdlib
  `urllib.request`** — see `fluidvoice/ai/client.py` (AIClient: bearer header,
  timeout, retry table with no-retry on 4xx config errors, `APIError`), plus
  `model_download.py` and `update.py`. **Decision: stdlib urllib. No new dependency.**
- **Failure path**: transcription errors surface as
  `log("transcription failed: {e}")` + `ui.notify("SayItErmano", "Transcription failed: …")`
  and the pill simply closes (`display.close()` when `out` has no text) — that IS
  the local decode failure path (asserted in `tests/test_daemon.py`). A remote
  backend only needs to raise `RuntimeError`-family exceptions from `transcribe()`;
  no daemon error plumbing changes. "No crash, no infinite retry" comes free.
- **Preview**: `daemon._start_preview` → `preview.preview_transcriber(cfg, backend,
  language)` dispatches on `backend.name`; unknown names return `None`, and the
  legacy fallback requires `backend._model` (a loaded faster-whisper model).
  A remote backend therefore gets **no live preview** (batch backend — VAD
  auto-stop also off; takes stop by hotkey). This matches scope; do NOT add a
  remote branch to `preview_transcriber`.
- **Config machinery** (`fluidvoice/config.py`): `DEFAULTS` → `coerce_setting`
  (SETTING_RANGES/ENUMS/BOOLS + dedicated `_coerce_*` branches) →
  `ALLOWED_SETTINGS` (socket `set-config` whitelist, used by
  `daemon._set_config` and offline `Client.set_config`) → `_SAVE_WHITELIST`
  (`save_config`, carries over empty values unless in `_EMPTY_IS_MEANINGFUL`)
  → `mask_secrets` (get-config over the socket returns masked cfg;
  `ai.api_key` becomes a **bool**) → `ENGINE_KEYS` (changed keys trigger daemon
  `_reload_backend` in a background thread) / `RESTART_REQUIRED`.
  Note: the AI key is env-var-only in the UI today (`ai.api_key` is not in
  `ALLOWED_SETTINGS`); the remote key follows the same never-logged, masked
  rules but IS settable (the scope asks for a password entry).
- **Settings UI** (`fluidvoice/gtkui/settings_window.py`): Models page built in
  `_build_models`; `_entry/_combo/_switch/_spin` row registry `self._rows`;
  `_load` fills rows from the (masked) config — `Adw.EntryRow` load does
  `set_text(str(val))`, which would corrupt a masked **bool** key into the
  literal string "True" (must be guarded for the password row);
  `_collect` skips empty `Adw.EntryRow` values ("keep the saved value") —
  `remote_url` MUST be postable empty (empty = off), like `ai.base_prompt`
  (`_TextProxy`) is.
- **Daemon model picking**: `Daemon.select_model(name)` → `_warmup_model(name)`
  loads `backends.load_backend(cfg-with-name)` and hot-swaps. Because the new
  selection rule makes `remote_url` win inside `load_backend`, picking a local
  model must CLEAR `remote_url` first or "Use" would silently keep remote.
- **Doctor** (`fluidvoice/doctor.py`): section-style `_*_lines(cfg)` helpers
  printed from `run()`; `ok` exit-code gate is deliberately tolerant of network
  state (see `_update_lines` docstring) — remote reachability stays informational.
- **model_catalog.py / model_download.py**: **no changes needed** (verified):
  `cached_models()` scans the disk cache; a remote backend adds no disk entries,
  so listing/deleting never sees it.
- Test conventions: stdlib `http.server` threaded fakes are fine; XDG/config
  isolation is handled by `tests/conftest.py`; GTK tests skip without a display.

## Design decisions (binding for the builder)

1. **Selection rule**: `model.remote_url` non-empty ⇒ `load_backend` returns the
   remote backend **before any local selection** (including `backend = "auto"`).
   Empty ⇒ code path is byte-identical to today. Also accept the explicit
   `model.backend = "remote"` value (added to `SETTING_ENUMS`) for users who want
   to be explicit; `remote` without a URL is a clear `RuntimeError`.
   Rationale: scope says "when remote_url is set, the daemon uses the remote
   backend"; the Models page "Use <local model>" button clears `remote_url`
   (see phase 3) so the two selection surfaces can't fight.
2. **Wire format**: `multipart/form-data` POST to `{remote_url}` normalized by an
   `_endpoint()` helper mirroring `ai/client.py`: if the URL already ends with
   `/audio/transcriptions` use as-is, else append `/v1/audio/transcriptions`.
   Fields: `file` (filename `audio.wav`, `Content-Type: audio/wav`, raw WAV bytes),
   `model` (string), `language` (only when effective language ≠ ""/"auto").
   `Authorization: Bearer {remote_api_key}` header only when a key is set.
   Response: JSON; `text` = `data["text"]` when present, else concatenate
   `data["segments"][*]["text"]` (verbose_json). Result dict:
   `{"text": str, "language": None-or-lang, "duration": None, "segments": [...]}`
   (pass segments through best-effort; remote responses lack `avg_logprob`, so
   `confidence_band()` naturally returns `None`).
3. **Retry policy** (mirrors `AIClient`): max 2 attempts total. Retry once on
   transient only — `urllib.error.URLError` (refused/DNS/timeout) and
   `HTTPError` with code in {429, 500, 502, 503, 504}; 0.2 s backoff between
   attempts. No retry on other 4xx (config errors), malformed JSON, or missing
   `text`. All failures raise `RemoteSttError(RuntimeError)` whose message never
   contains the API key or request headers (HTTPError detail is truncated
   response-body only, like `AIClient`).
4. **Secrets**: `model.remote_api_key` is masked to a bool by `mask_secrets`
   (socket get-config), carried over by `save_config` when the UI posts nothing
   (same rule as `ai.api_key`), never printed by doctor, never logged. Removing a
   saved key = edit `~/.config/fluidvoice/config.toml` (same limitation as
   `ai.api_key` today; document in README).
5. **No streaming in v1**: remote takes have no live preview and no VAD auto-stop
   (verified consequence of decision in facts above). Doctor's preview section
   stays as-is.

New config keys (all under `[model]`, defaults in `DEFAULTS`):

| key | default | validation |
|---|---|---|
| `remote_url` | `""` (off) | dedicated `_coerce_remote_url`: "" ok; else http/https URL, ≤2048 chars, no whitespace |
| `remote_model` | `"whisper-large-v3"` | `SETTING_RANGES ("str", 256)` (empty entry keeps saved value, like `ai.model`) |
| `remote_api_key` | `""` | dedicated branch: any str ≤4096 (incl. "") |
| `remote_timeout_s` | `30` | `SETTING_RANGES ("float", (5.0, 600.0))` |

All four: `ALLOWED_SETTINGS["model"]`, `_SAVE_WHITELIST["model"]`,
`ENGINE_KEYS` (a change hot-reloads the backend — reload is cheap and correct).

---

## Phase 1 — Config surface

Files: `fluidvoice/config.py`, `tests/test_remote_stt.py` (new).

1. `DEFAULTS["model"]` += the four keys above.
2. `SETTING_RANGES` += `("model", "remote_model"): ("str", 256)`,
   `("model", "remote_timeout_s"): ("float", (5.0, 600.0))`.
3. `coerce_setting`: add dedicated branches BEFORE the ranges lookup (pattern of
   `_coerce_button_spec`): `_coerce_remote_url(value)` — `""` ok; non-empty must
   parse with `urllib.parse.urlparse` to scheme http/https and a non-empty
   netloc, length ≤2048, no whitespace; return stripped value. Remote api key:
   `isinstance(str) and len <= 4096` (any content incl. "" accepted).
4. `SETTING_ENUMS[("model","backend")]` += `"remote"`.
5. `ALLOWED_SETTINGS["model"]`, `_SAVE_WHITELIST["model"]` += the four keys.
6. `ENGINE_KEYS` += `model.remote_url`, `model.remote_model`,
   `model.remote_api_key`, `model.remote_timeout_s`.
7. `mask_secrets`: also replace `model.remote_api_key` with `bool(value)`.
8. `TEMPLATE` `[model]` section: add commented lines
   (`# remote_url = "http://192.168.1.50:8000"  # OpenAI-compatible /v1/audio/transcriptions; empty = local only`
   + remote_model / `# remote_api_key = ""` / `# remote_timeout_s = 30`).
9. Tests (`TestRemoteConfig`): defaults exist and equal the table; coerce accepts
   `http://h:1/p`, `https://x/v1/audio/transcriptions`, `""`; rejects `ftp://x`,
   `not a url`, >2048; timeout clamps/rejects outside 5–600; `apply_settings`
   round-trips all four and rejects unknown garbage; `save_config` keeps a saved
   `remote_api_key` when the body omits it and writes `remote_url = ""` state as
   an omitted line; `mask_secrets` hides the key.

Gate: `.venv/bin/python -m pytest -q tests --ignore=tests/integration` green.

## Phase 2 — Backend module + selection seam

Files: `fluidvoice/backends/remote_stt.py` (new), `fluidvoice/backends/__init__.py`,
`tests/fake_remote_stt_server.py` (new), `tests/test_remote_stt.py`.

1. `tests/fake_remote_stt_server.py`: stdlib `http.server.ThreadingHTTPServer`
   on an ephemeral port, runnable BOTH imported by tests and standalone
   (`python tests/fake_remote_stt_server.py [port]` — prints the URL + a ready
   config snippet; `--verbose` prints each request's parsed fields). Handler:
   - Parses the multipart body (split on the request's boundary; or
     `email.parser`) and records: field names, `model` value, `language` value,
     `file` length + first 4 bytes (assert `RIFF`), Authorization header.
   - Scriptable responses via a queue/attribute: default
     `{"text": "hello remote world"}`; modes for `verbose_json`
     (`{"text": "a b", "segments": [{"start":0,"end":1,"text":"a"},{"start":1,"end":2,"text":"b"}]}`),
     HTTP 500 / 401 / slow (sleep) / malformed JSON / missing-text `{}`.
   - Thread-safe request counter so tests assert exact attempt counts.
2. `fluidvoice/backends/remote_stt.py`:
   - `class RemoteSttError(RuntimeError)`.
   - `class RemoteSttBackend(backends.Backend)`: `name = "remote"`; `__init__(cfg)`
     reads the four keys (`float()` the timeout), normalizes the endpoint
     (`_endpoint()` per decision 2), sets `self.model_name = remote_model`
     (gives `backend_model_key()` a key → per-model language overrides work
     keyed by the remote model name) and `self.language = effective_language(cfg)
     or "auto"` (same as `WhisperCppBackend`); missing `remote_url` ⇒
     `RuntimeError("model.remote_url is required for the remote backend")`.
   - `warmup()`: no-op (`pass`) — doctor owns reachability; daemon eager warmup
     logs "speech model loaded (preview ready)" which is harmless.
   - `_multipart(fields, wav_bytes) -> (body, content_type)` hand-rolled with a
     `uuid.uuid4().hex` boundary, CRLF separators, `Content-Disposition` per
     field, `Content-Type: audio/wav` + `filename="audio.wav"` for `file`.
   - `transcribe(wav_path, language=None)`: read the WAV bytes
     (`Path(wav_path).read_bytes()`), resolve `lang = language or self.language`,
     omit the field when `""`/`"auto"`; build the request
     (`urllib.request.Request(url, data=body, headers=..., method="POST")`);
     2-attempt loop per decision 3; on success parse JSON, extract text
     (`data.get("text")` or segments concat), return the result dict of
     decision 2; every failure path raises `RemoteSttError` with a key-free
     message (include HTTP code + ≤300-char response body, like `AIClient`).
3. `fluidvoice/backends/__init__.py`:
   - `load_backend(cfg)`: first thing after the local imports, read
     `remote_url = str((cfg["model"].get("remote_url") or "")).strip()`;
     if set ⇒ `from .remote_stt import RemoteSttBackend; return
     RemoteSttBackend(cfg)`. Also handle `wanted == "remote"` in the explicit
     dispatch (raises inside the constructor when no URL).
   - `config_model_key(cfg)`: when `remote_url` is set, return `None` (remote is
     not a local catalog identity; stops the doctor cache listing / language
     resolution from marking a local model ACTIVE while remote is live —
     `effective_language` still resolves via `backend_model_key(backend)` =
     the remote model name).
   - Module docstring: add item 5 "remote — OpenAI-compatible
     /v1/audio/transcriptions endpoint (user-configured, config-gated)".
   - `backend_status()`: unchanged (it reports local install availability).
4. Tests (`TestRemoteBackend`, `TestRemoteSelection`):
   - Roundtrip: fake server default mode; `cfg = deepcopy(DEFAULTS)` with
     `remote_url = server.url`; `load_backend(cfg)` → `.name == "remote"`;
     write a real tiny WAV via `fluidvoice.audio_utils.raw_to_wav_bytes` → temp
     file; `transcribe()` returns `{"text": "hello remote world", ...}`.
   - Multipart shape (from the server's recorded request): content-type is
     multipart with boundary; a `file` field exists, non-empty, starts `RIFF`;
     `model` field equals `remote_model`; no `language` field at
     `general.language = "auto"`; `language == "de"` field present when set
     (via `effective_language` through the pipeline-style call and directly).
   - Bearer: server records `Authorization: Bearer <key>` iff key configured.
   - Error table: connection refused (server shut down) → `RemoteSttError`;
     timeout (slow mode + `remote_timeout_s = 0.3` in the cfg dict — direct
     construction bypasses UI ranges by design) → `RemoteSttError`; 500-then-200
     (scripted) → succeeds and server saw exactly 2 requests; always-500 →
     raises after exactly 2 requests; 401 → raises after exactly 1 request;
     malformed JSON → raises, 1 request; missing `text` → raises.
   - Selection: `remote_url` set wins over `backend="auto"` and
     `backend="faster-whisper"` (reuse the `fakes` monkeypatch pattern from
     `tests/test_backends_selection.py` to prove no local backend is
     constructed); `remote_url=""` + `remote_model="x"` ⇒ unchanged local
     behavior (existing tests already cover; add one explicit no-hit assertion:
     start the fake server, `load_backend(cfg-without-url)` with local fakes,
     assert server request count == 0).
   - `preview.preview_transcriber(cfg, remote_backend, "en") is None`
     (documents the no-preview decision).
   - `config_model_key(cfg)` is `None` when `remote_url` set;
     `effective_language` honors `model.languages = {<remote_model>: "de"}`.

Gate: full suite green.

## Phase 3 — Daemon selection interplay + doctor

Files: `fluidvoice/daemon.py`, `fluidvoice/doctor.py`, `tests/test_remote_stt.py`.

1. `Daemon.select_model` (daemon.py ~line 1129): before spawning
   `_warmup_model`, if `self.cfg["model"].get("remote_url")` is set, clear it
   (`self.cfg["model"]["remote_url"] = ""`) so the local model actually loads —
   picking a local model in Settings → Models switches dictation back to local.
   `_warmup_model` already persists `self.cfg` via `save_config` on success; on
   failure the name rolls back but `remote_url` stays cleared (documented,
   acceptable: the running remote backend instance keeps working until restart).
2. Daemon `status`: no code change needed — `"backend": self.backend.name`
   already reports `"remote"`; history entries get `backend: "remote"` the same
   way.
3. `doctor.py`: new `_remote_stt_lines(cfg) -> list[str]` printed as its own
   `\nremote STT:` section (between the parakeet and language-resolution
   sections):
   - Unconfigured: one line `not configured (model.remote_url) - local models only`.
   - Configured: `endpoint: <url>` (full configured URL is fine — it contains no
     secret), `model: <remote_model> · timeout: <n>s · key: set/none`
     (boolean only), then a reachability probe: one `GET` to the URL's origin
     (`scheme://host[:port]/`) with a 2 s timeout via urllib; ANY HTTP response
     (200/404/…) ⇒ `reachable (HTTP <code> from / — POST target: <endpoint>)`;
     `URLError`/timeout ⇒ `unreachable (<err>)`. Never sends audio, never
     prints the key, never flips `ok` (informational, matching the
     `_update_lines` offline-tolerance philosophy).
4. Tests: `_remote_stt_lines` unconfigured (single line, no network — assert by
   monkeypatching nothing and keeping remote_url empty); configured + fake
   server up (reachable line contains the endpoint, not the key); configured +
   server down (unreachable). Key-absence assertion: set
   `remote_api_key = "sk-test-DO-NOT-PRINT-9f1c"` and assert the key string is
   not in any doctor line. Daemon: construct a `Daemon` with stub recorder /
   backend factory per existing `tests/test_daemon.py` patterns, monkeypatch
   `backends.load_backend` to a fake local backend with no-op warmup, set
   `remote_url`, call `daemon._warmup_model("small")` directly, assert
   `cfg["model"]["remote_url"] == ""` and the hot-swapped backend is the local
   fake.

Gate: full suite green.

## Phase 4 — Settings UI (Models page)

Files: `fluidvoice/gtkui/settings_window.py`, `tests/test_gtkui.py` (extend).

1. `_build_models`: after the Engine-options group, add an
   `Adw.PreferencesGroup(title="Remote (OpenAI-compatible)",
   description=…)` with:
   - URL row: `_entry("model", "remote_url", "Server URL")` — description text
     covers: `http://lan-box:8000` — dictation POSTs the recorded WAV to
     `<url>/v1/audio/transcriptions`; empty = local models only; audio leaves
     this machine only toward the URL you set.
   - Model row: `_entry("model", "remote_model", "Model name")`.
   - Key row: a password row — use `Adw.PasswordRow` if available
     (`hasattr(Adw, "PasswordRow")`), else `Gtk.PasswordEntry` inside an
     `Adw.ActionRow`; register it in `self._rows[("model", "remote_api_key")]`
     via a tiny proxy object exposing `get_text/set_text` (or use the row
     directly — both have those methods).
   - Timeout row: `_spin("model", "remote_timeout_s", "Timeout (seconds)", 5, 600, 1, digits=0)`.
2. `_collect`: `("model", "remote_url")` must be posted EVEN WHEN EMPTY (empty =
   off) — extend the `Adw.EntryRow` branch with an explicit
   `if (sec, key) == ("model", "remote_url"): post regardless` exception
   (mirrors the `_TextProxy` base_prompt rule). `remote_model` and
   `remote_api_key` keep the default skip-when-empty behavior (keep saved).
3. `_load`: guard the password row — when the loaded value is a **bool**
   (masked config), keep the field EMPTY and set the placeholder/transient
   text to "key saved — type to replace"; never `set_text(str(bool))`. Also
   make sure a plain-string load (unmasked, file-only mode shows "" default)
   behaves normally.
4. `_refresh_models` State row: when `cfg["model"].get("remote_url")` is set,
   the warmup/active subtitle shows `active: remote (<remote_model> @ <host>)`
   instead of the local model name (host from `urlparse(remote_url).netloc`).
5. GTK tests (they skip gracefully without a display, same as the rest of
   test_gtkui.py): window builds with the Remote group; `_collect` posts
   `remote_url=""` when the entry is empty and posts the typed value
   otherwise; password field stays empty and shows the saved-placeholder when
   the config value is `True`; spin row roundtrips 30.

Gate: full suite green.

## Phase 5 — Docs

Files: `README.md`, `docs/STATUS.md`.

1. README: new `### Remote STT server` subsection right after "Enable AI polish
   (optional)" (same tone/format): privacy stance first ("Nothing leaves your
   machine unless you point SayItErmano at a URL you choose — the remote
   backend is off by default and LAN-friendly"), the four config keys as a
   TOML block, and example servers:
   - vLLM (Whisper): `vllm serve openai/whisper-large-v3 --port 8000` →
     `remote_url = "http://<lan-host>:8000"`, `remote_model = "whisper-large-v3"`.
   - whisper.cpp server: `whisper-server -m models/ggml-large-v3.bin --port 8080`
     (its OpenAI-compatible `/v1/audio/transcriptions` route).
   Note: any server implementing `POST /v1/audio/transcriptions` works
   (NIM, DGX Spark, Groq/OpenAI cloud too); API key optional bearer; doctor
   line; "clear remote_url (or click Use on a local model) to go back to
   local"; no live preview for remote takes; key removal = edit config.toml.
2. `docs/STATUS.md` under "Intentional divergences": one short entry — remote
   OpenAI-compatible STT backend, config-gated (`model.remote_url`), upstream
   declined the community PR in favor of a native protocol (research insight
   12); cite `fluidvoice/backends/remote_stt.py`.

Gate: full suite green (docs only — rerun anyway).

## Phase 6 — Live smoke (manual, done-criteria)

1. `.venv/bin/python tests/fake_remote_stt_server.py 8399 --verbose` (leave
   running).
2. Set config (`~/.config/fluidvoice/config.toml`):
   `remote_url = "http://127.0.0.1:8399"`, `remote_model = "whisper-large-v3"`.
3. Start the daemon; dictate with the hotkey (and/or the socket
   `test-dictation` action): text is typed into the focused app; the fake
   server log shows the multipart POST (file RIFF, model field); a history
   entry appears with `backend: "remote"`.
4. `sayit-ermano doctor`: "remote STT:" section shows configured + reachable;
   kill the fake server → doctor shows unreachable; dictation shows the
   "Transcription failed: …" notification (same path as local failures), no
   crash, exactly 2 attempts server-side if it 500s.
5. Clear `remote_url` (settings UI, leaving the entry empty and saving —
   verifies the empty-is-meaningful path) → dictation uses the local backend
   again; `pytest -q tests --ignore=tests/integration` green.

## Out of scope (do not build)

Streaming/chunked remote transcription, websocket protocols (Grok speech),
multi-endpoint routing/failover, server-side VAD, cloud-provider SDKs, any
network activity when `remote_url` is empty, changes to
`model_catalog.py`/`model_download.py` (verified unnecessary).

## Files touched (summary)

- `fluidvoice/config.py` — 4 keys, validation, whitelist, ENGINE_KEYS, mask, template
- `fluidvoice/backends/remote_stt.py` — NEW backend module
- `fluidvoice/backends/__init__.py` — selection rule, `config_model_key`, docstring
- `fluidvoice/daemon.py` — `select_model` clears `remote_url`
- `fluidvoice/doctor.py` — `_remote_stt_lines` + section
- `fluidvoice/gtkui/settings_window.py` — Remote group, collect/load guards
- `README.md`, `docs/STATUS.md` — docs
- `tests/fake_remote_stt_server.py`, `tests/test_remote_stt.py` — NEW tests
- `tests/test_gtkui.py` — Remote-group UI tests

Unchanged by design: `fluidvoice/recorder.py` (WAV already produced),
`fluidvoice/preview.py` (no remote branch — batch backend),
`fluidvoice/model_catalog.py`, `fluidvoice/model_download.py`.
