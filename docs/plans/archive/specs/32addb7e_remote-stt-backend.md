# Plan: Remote OpenAI-compatible STT backend (`model.remote_url`)

Source: docs/research/2026-09-05-fluidvoice-reviews.md insight 12 — upstream users
want to point the app at a LAN GPU box serving Whisper/Parakeet via an
OpenAI-compatible `POST /v1/audio/transcriptions` endpoint (vLLM / NIM / DGX
Spark); the upstream maintainer **declined** the community's opt-in PR in favor
of a native protocol, so this is a differentiator upstream will not ship.

SayItErmano stays local-first: the remote backend exists **only** when the user
configures a URL. Nothing leaves the machine when `model.remote_url` is empty —
that invariant is test-enforced.

Note: `specs/e675e121_remote-stt-backend.md` is an earlier plan for this same
brief written before the language-cycle/guard work landed (commits d604db8…052df8a).
This plan re-verifies every seam against the current tree and **supersedes it**;
build from this one.

## Verified facts (current tree, agent worktree @ b5354d2)

- **Audio format — WAV, no re-encoding**: `Recorder.stop()` wraps raw PCM via
  `audio_utils.raw_to_wav_file` into a 16 kHz mono s16 WAV;
  `DictationPipeline.run` calls `pad_wav(wav)` (≥1 s for whisper.cpp) then
  `self.backend.transcribe(wav, language=lang)` — note the **keyword** `language=`.
  → the multipart `file` field carries the WAV bytes as-is.
- **Backend seam**: `fluidvoice/backends/__init__.py::load_backend(cfg)` is the
  single selection point. Callers: `Daemon.__init__(backend_factory=...)`,
  `_ensure_backend`, `_reload_backend` (triggered by `ENGINE_KEYS` changes in
  `_set_config`), `_warmup_model` (socket `select-model`), plus
  `tests/test_backends_selection.py`. Contract: class attr `name`;
  `transcribe(wav_path, language=None) -> {"text", "language", "duration",
  "segments"}`; optional `warmup()` / `close()`; optional `model_name`/`model`
  attrs consumed by `backend_model_key()`; optional class attr
  `surfaces_detected_language` (the wrong-language guard `getattr`s it,
  default False). `WhisperCppBackend` is the minimal reference implementation.
- **HTTP client — stdlib urllib (decision)**: `pyproject.toml` dependencies are
  python-xlib/pillow/faster-whisper only; no `requests` vendored. The
  established transport is `fluidvoice/ai/client.py` (AIClient): bearer header,
  `urllib.request.Request` + `urlopen(timeout=)`, retry loop with
  **no retry on 400/401/403/404/422**, retry on 429/5xx + transport errors,
  0.2·attempt backoff, HTTPError detail = first 300 chars of the response body.
  The remote backend mirrors this exactly. **No new dependency.**
- **Failure path (no daemon changes needed)**: `DictationPipeline.run` catches
  `_transcribe` exceptions → `log("transcription failed: {e}")` +
  `notify("SayItErmano", "Transcription failed: {e}")` → returns None (pill
  closes, no text). `_process` wraps `_ensure_backend` failures identically.
  A remote backend that raises `RuntimeError`-family errors gets the same UX
  as local decode failures, free. Verified in `tests/test_daemon.py`.
- **Preview — none for remote, by construction**: `_start_preview` →
  `preview.preview_transcriber(cfg, backend, language)` dispatches per known
  backend name; unknown name `"remote"` returns None; the legacy fallback
  requires `backend._model` (None for remote) → `display.close()` → preview
  stays off. VAD auto-stop lives in the segmented engine only → remote takes
  stop by hotkey. **Do not touch `fluidvoice/preview.py`.**
- **Language machinery (new since the old spec)**: `effective_language(cfg,
  backend, runtime="")` = cycle override > `model.languages[key]` (keys from
  `backend_model_key(backend)` then `config_model_key(cfg)`) >
  `general.language`. Setting `self.model_name = remote_model` on the remote
  backend makes per-model overrides keyed by the remote model name work.
  `backend_model_key` = `model_name` when present.
- **Secrets pattern**: `ai.api_key` is stored in config.toml, carried over by
  `save_config` when the UI omits it, masked to a bool by `mask_secrets`
  (socket `get-config` → daemon line 1479), NOT in `ALLOWED_SETTINGS` (env-only
  in the UI). The remote key follows the same storage/masking rules but IS
  socket-settable (the scope wants a password entry). Daemon `get-config`
  returns masked cfg; GTK `Client.get_config()` returns the **unmasked file**
  when the daemon is down — the UI must never display a stored key in either
  case (see phase 4).
- **Config machinery**: `DEFAULTS` → `coerce_setting` (dedicated branches
  precede the `SETTING_RANGES` lookup; str-ranges reject empty) →
  `ALLOWED_SETTINGS` (socket `set-config` whitelist) → `_SAVE_WHITELIST` +
  `_EMPTY_IS_MEANINGFUL` (empty values carry over from the file unless listed)
  → `ENGINE_KEYS` (change ⇒ background `_reload_backend`) → `mask_secrets`.
  `SETTING_ENUMS[("model","backend")]` gates the backend enum.
- **Settings UI**: `_build_models` builds the Models page; row registry
  `self._rows[(sec,key)]`; `_load` fills (EntryRow branch does
  `set_text(str(val))` — a masked bool would render "True"); `_collect`
  **skips empty EntryRows** ("keep the saved value") except via explicit
  exceptions (`ai.base_prompt` `_TextProxy`; `remote_url` needs the same
  treatment — empty must POST to turn remote off). Proxy-adapter pattern:
  `_SwitchProxy`/`_ListProxy`/`_TextProxy`. `_refresh_models` renders the
  State row from `daemon status` + `self._active_model()`. GTK tests gate on
  display (module-level skip in `tests/test_gtkui.py`) and stay green headless.
- **Doctor**: `run()` prints `_*_lines(cfg)` sections; network-ish sections
  (`_update_lines`) are informational and never flip the `ok` exit gate.
  `_language_lines` prints the guard line from
  `backends.resolved_backend_name(cfg)` + `backends.LANGUAGE_GUARD`.
- **model_catalog / model_download — NO changes** (verified): `MODEL_CATALOG`
  is a static dict iterated by the UI; `cached_models()` scans the disk cache;
  a remote backend adds no disk entries, so listing/deleting never sees it.
  (If the user names `remote_model` the same as a local FW model, the
  delete-model active-guard may over-refuse deleting that local model —
  harmless over-caution, accepted.)
- **Daemon model picking**: `select_model(name)` (FW names only) →
  `_warmup_model(name)` builds `cfg-with-name` → `load_backend` → hot-swap +
  `save_config`. Because remote wins inside `load_backend` (decision below),
  picking a local model must CLEAR `remote_url` first or "Use" silently keeps
  remote.
- Test conventions: `tests/conftest.py` isolates XDG/config; selection tests
  monkeypatch fake backend classes into the backend modules
  (`tests/test_backends_selection.py::fakes`); suite command (AGENTS.md):
  `/home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python -m pytest
  -q tests --ignore=tests/integration` from the worktree root.

## Design decisions (binding for the builder)

1. **Selection rule**: `model.remote_url` non-empty ⇒ `load_backend` returns
   the remote backend **before any local dispatch** (including
   `backend = "auto"`). Empty ⇒ byte-identical code path to today. Also accept
   explicit `model.backend = "remote"` (added to `SETTING_ENUMS` + UI combo);
   `"remote"` without a URL raises a clear `RuntimeError` from the backend
   constructor. "Use <local model>" clears `remote_url` (phase 3) so the two
   selection surfaces can't fight.
2. **Wire format**: `multipart/form-data` POST to an endpoint normalized like
   `ai/client.py::_endpoint`: if the URL already ends with
   `/audio/transcriptions` use as-is, else append `/v1/audio/transcriptions`.
   Fields: `file` (filename `audio.wav`, `Content-Type: audio/wav`, raw WAV
   bytes from `Path(wav_path).read_bytes()`), `model` (string), `language`
   (only when the effective language is neither `""` nor `"auto"`).
   `Authorization: Bearer {remote_api_key}` header only when a key is set.
   Response JSON: `text` = `data["text"]` when present and non-empty, else the
   concatenation of `data["segments"][*]["text"]` (verbose_json); result dict
   `{"text": str, "language": lang-or-None, "duration": None, "segments":
   <passthrough list or []>}`. Remote segments lack `avg_logprob`, so
   `confidence_band()` naturally returns None — fine.
3. **Retry policy (mirrors AIClient)**: max **2 attempts total** (one retry).
   Retry only on `urllib.error.URLError` (refused/DNS/timeout — note
   `HTTPError` subclasses `URLError`, so catch `HTTPError` first) and on
   `HTTPError` with code in {429, 500, 502, 503, 504}; 0.2 s backoff. No retry
   on other 4xx, malformed JSON, or missing text. All failures raise
   `RemoteSttError(RuntimeError)` whose message contains only the URL host,
   HTTP code and a ≤300-char response-body excerpt — never the key, never
   request headers.
4. **Secrets**: `model.remote_api_key` masked to a bool by `mask_secrets`;
   carried over by `save_config` when the UI posts nothing (same rule as
   `ai.api_key`); never printed by doctor; never logged (the pipeline only
   logs exception messages, which decision 3 keeps key-free). Removing a saved
   key = edit `~/.config/fluidvoice/config.toml` (same limitation as
   `ai.api_key`; document in README).
5. **No streaming in v1**: remote takes have no live preview and no VAD
   auto-stop (verified consequence). Language cycle still works per-take (the
   final decode re-resolves and the `language` field follows it).
6. **Language identity**: remote backend sets `self.model_name =
   remote_model`; `config_model_key(cfg)` returns the remote model name when
   `remote_url` is set (so `model.languages` overrides and doctor's
   "active model" line stay honest even daemon-down);
   `resolved_backend_name(cfg)` returns `"remote"` when set, and
   `LANGUAGE_GUARD["remote"]` documents that the guard does not apply (the
   endpoint doesn't surface detected language in the plain-JSON response).

New config keys (all under `[model]`):

| key | default | validation |
|---|---|---|
| `remote_url` | `""` (off) | dedicated `_coerce_remote_url`: `""` ok; else http/https, non-empty netloc, ≤2048 chars, no whitespace (strip first) |
| `remote_model` | `"whisper-large-v3"` | `SETTING_RANGES ("str", 256)` (empty entry keeps the saved value, like `ai.model`) |
| `remote_api_key` | `""` | dedicated branch: any str ≤4096 (incl. `""`) |
| `remote_timeout_s` | `30` | `SETTING_RANGES ("float", (5.0, 600.0))` — tests bypass via direct dict cfg with sub-second values |

All four: `ALLOWED_SETTINGS["model"]`, `_SAVE_WHITELIST["model"]`,
`ENGINE_KEYS` (a change hot-reloads the backend — cheap and correct).
`("model", "remote_url")` also joins `_EMPTY_IS_MEANINGFUL` so clearing the
URL in the UI actually persists "off" instead of carrying the old value over.

---

## Phase 1 — Config surface

Files: `fluidvoice/config.py`, `tests/test_remote_stt.py` (new).

1. `DEFAULTS["model"]` += the four keys (with the comments style of the file).
2. `SETTING_RANGES` += `("model", "remote_model"): ("str", 256)`,
   `("model", "remote_timeout_s"): ("float", (5.0, 600.0))`.
3. `coerce_setting`: dedicated branches before the ranges lookup (pattern of
   `_coerce_button_spec`): `_coerce_remote_url(value)` per the table; the api
   key branch inline (`isinstance(str) and len ≤ 4096`, accepts `""`).
4. `SETTING_ENUMS[("model", "backend")]` += `"remote"`.
5. `ALLOWED_SETTINGS["model"]` and `_SAVE_WHITELIST["model"]` += the four keys.
6. `ENGINE_KEYS` += all four `model.remote_*`.
7. `_EMPTY_IS_MEANINGFUL` += `("model", "remote_url")`.
8. `mask_secrets`: also replace `model.remote_api_key` with `bool(value)`.
9. `TEMPLATE` `[model]` section: commented examples —
   `# remote_url = "http://192.168.1.50:8000"  # OpenAI-compatible /v1/audio/transcriptions; empty = local only`
   (+ `remote_model`, `# remote_api_key = ""`, `# remote_timeout_s = 30`).
10. Tests (`TestRemoteConfig`): defaults exist per the table; coerce accepts
    `http://h:1`, `https://x/y/v1/audio/transcriptions`, `""`; rejects
    `ftp://x`, `not a url`, whitespace-bearing and >2048 strings; timeout
    rejects/clamps outside 5–600; `apply_settings` round-trips all four and
    rejects unknown garbage; `save_config` keeps a saved `remote_api_key` when
    the body omits it, and persists `remote_url = ""` (empty is meaningful) —
    a previously-set URL is actually gone after save; `mask_secrets` hides the
    key; `"remote"` is an accepted `model.backend` value, `"remote-x"` is not.

Gate: full suite green.

## Phase 2 — Backend module + selection seam + fake server

Files: `fluidvoice/backends/remote_stt.py` (new),
`fluidvoice/backends/__init__.py`, `tests/fake_remote_stt_server.py` (new),
`tests/test_remote_stt.py`.

1. `tests/fake_remote_stt_server.py` — stdlib `http.server.ThreadingHTTPServer`
   on an ephemeral port, importable by tests AND runnable standalone:
   `python tests/fake_remote_stt_server.py [port] [--mode MODE] [--verbose]`
   prints the URL and a ready-to-paste config snippet (this IS the live-smoke
   server of phase 6). Handler:
   - Parses the multipart body (boundary from the Content-Type header; split
     parts, parse per-part headers) and records per request: field names,
     `model` value, `language` value (None when absent), `file` length +
     first 4 bytes, `Authorization` header presence/value.
   - Modes: `default` → `{"text": "hello remote world"}`; `verbose_json` →
     `{"text": "a b", "segments": [{"start":0,"end":1,"text":"a"},
     {"start":1,"end":2,"text":"b"}]}`; `missing_text` → `{}`; `bad_json` →
     non-JSON body; `http500` / `http401` → the status; `slow` → sleep 5 s.
   - Thread-safe request log + counter exposed on the server object
     (`server.requests`, `server.count`) so tests assert exact attempt counts.
2. `fluidvoice/backends/remote_stt.py`:
   - `class RemoteSttError(RuntimeError)`.
   - `class RemoteSttBackend`: `name = "remote"`; `__init__(cfg)` reads the
     four keys (`float()` the timeout), normalizes the endpoint
     (`_endpoint()` per decision 2), `self.model_name = remote_model`
     (language overrides + doctor identity), `self.language =
     effective_language(cfg) or "auto"` (same as `WhisperCppBackend`);
     missing `remote_url` ⇒ `RuntimeError("model.remote_url is required for
     the remote backend")`. Do NOT set `surfaces_detected_language` (guard
     skips remote, decision 6).
   - `warmup()`: no-op (`pass`) — reachability belongs to doctor and the real
     take; `close()`: inherited no-op.
   - `_multipart(fields, wav_bytes) -> (body, content_type)` hand-rolled with
     a `uuid.uuid4().hex` boundary, CRLF separators, `Content-Disposition`
     per field; `Content-Type: audio/wav; filename="audio.wav"` for `file`.
   - `transcribe(self, wav_path, language=None)`: read WAV bytes, resolve
     `lang = language or self.language` (omit field when `""`/`"auto"`),
     build the request, run the 2-attempt loop of decision 3, parse JSON,
     extract text per decision 2, return the result dict. Every failure path
     raises `RemoteSttError` with a key-free message (HTTP code +
     ≤300-char body excerpt, AIClient style).
3. `fluidvoice/backends/__init__.py`:
   - `load_backend(cfg)`: at the very top (before the local imports execute
     any probe), `remote_url = str(cfg["model"].get("remote_url") or
     "").strip()`; if set ⇒ `from .remote_stt import RemoteSttBackend;
     return RemoteSttBackend(cfg)`. Also `if wanted == "remote": ...` in the
     explicit dispatch (constructor raises without a URL).
   - `config_model_key(cfg)`: when `remote_url` is set, return
     `remote_model.strip() or None` before the local dispatch.
   - `resolved_backend_name(cfg)`: return `"remote"` when `remote_url` is set
     (first line of the function).
   - `LANGUAGE_GUARD["remote"]` = "language hint sent with the request;
     detected language not surfaced by the endpoint - guard not applicable
     (batch backend)".
   - Module docstring: append item 5 "remote — user-configured
     OpenAI-compatible /v1/audio/transcriptions endpoint (config-gated)".
   - `backend_status()`: unchanged (local install availability only).
4. Tests:
   - Roundtrip: fake server default mode; `cfg = copy.deepcopy(DEFAULTS)`
     with `remote_url = server.url`; `load_backend(cfg)` → `.name ==
     "remote"`; write a real tiny WAV via
     `fluidvoice.audio_utils.raw_to_wav_bytes` → temp file;
     `backend.transcribe(path)` returns `{"text": "hello remote world", …}`.
   - Multipart shape (from the recorded request): content-type multipart;
     `file` field non-empty and starts with `RIFF`; `model` field equals
     `remote_model`; no `language` field when general.language = "auto";
     `language == "de"` present when set (both a direct `transcribe(...,
     language="de")` call and through `effective_language` with
     `model.languages = {<remote_model>: "de"}`).
   - Bearer: `Authorization: Bearer <key>` recorded iff a key is configured.
   - Error table: refused (server shut down) → `RemoteSttError`; timeout
     (`slow` mode + `remote_timeout_s = 0.3` in the direct cfg dict) →
     `RemoteSttError`; `http500` then default (script two modes) → succeeds
     with exactly 2 requests; constant `http500` → raises after exactly 2
     requests; `http401` → raises after exactly 1 request; `bad_json` →
     raises, 1 request; `missing_text` → raises.
   - Selection: `remote_url` set wins over `backend="auto"` AND
     `backend="faster-whisper"` (reuse the `fakes` monkeypatch pattern from
     `tests/test_backends_selection.py` to prove no local class constructs);
     `backend="remote"` without URL → `RuntimeError` naming `model.remote_url`.
   - No-request-when-unconfigured: fake server up, `load_backend` on a
     remote-free cfg with local fakes (backend constructs), then assert
     `server.count == 0`.
   - `preview.preview_transcriber(cfg, remote_backend, "en") is None`
     (documents the no-preview decision).
   - `config_model_key` returns the remote model name when set;
     `resolved_backend_name` returns `"remote"` when set.

Gate: full suite green.

## Phase 3 — Daemon interplay + doctor

Files: `fluidvoice/daemon.py`, `fluidvoice/doctor.py`, `tests/test_remote_stt.py`.

1. `Daemon._warmup_model` (daemon.py ~line 1219): before building the
   per-name cfg, if `self.cfg["model"].get("remote_url")` is set, clear it in
   `self.cfg` (`self.cfg["model"]["remote_url"] = ""`) so the local model
   actually loads — picking a local model in Settings → Models switches
   dictation back to local. `save_config(self.cfg)` on success persists the
   cleared URL; on failure the name rolls back but the URL stays cleared
   (documented, acceptable: the running remote instance keeps serving until
   restart). `select_model` itself needs no change (it only gates FW names).
2. Daemon status/history: no code change — `"backend": self.backend.name`
   already reports `"remote"`; history entries carry `backend: "remote"`.
3. `doctor.py`: new `_remote_stt_lines(cfg) -> list[str]` printed as its own
   `"\nremote STT:"` section in `run()` (after the parakeet section, before
   language resolution):
   - Unconfigured: one line — `not configured (model.remote_url) - local
     models only`.
   - Configured: `endpoint: <url>`, `model: <remote_model> · timeout: <n>s ·
     key: set|none` (boolean only; the URL contains no secret), then a
     reachability probe: one `GET {scheme}://{netloc}/` with a 2.5 s timeout
     via urllib, no auth header; ANY HTTP response (200/404/405…) ⇒
     `reachable (HTTP <code> from / - POST target: <endpoint>)`;
     `URLError`/timeout ⇒ `unreachable (<err>)`. Never sends audio, never
     prints the key, never flips the `ok` gate (informational, matching the
     `_update_lines` offline-tolerance philosophy).
4. Tests: `_remote_stt_lines` unconfigured (single line; no network — keep
   remote_url empty so the probe never runs); configured + fake server up
   (reachable line contains the endpoint, not the key); configured + server
   down (unreachable). Key-absence: set `remote_api_key =
   "sk-test-DO-NOT-PRINT-9f1c"` and assert the key string is absent from
   every doctor line. Daemon: construct a `Daemon` with a stub recorder and a
   fake `backend_factory` (per `tests/test_daemon.py` patterns), set
   `remote_url`, call `daemon._warmup_model("small")` directly (monkeypatch
   `backends.load_backend` to a fake local backend with no-op warmup), assert
   `cfg["model"]["remote_url"] == ""` afterward and the hot-swapped backend
   is the local fake.

Gate: full suite green.

## Phase 4 — Settings UI (Models page)

Files: `fluidvoice/gtkui/settings_window.py`, `tests/test_gtkui.py` (extend).

1. `_build_models`: after the Engine-options group, add
   `Adw.PreferencesGroup(title="Remote (OpenAI-compatible)")` with:
   - URL row: `_entry("model", "remote_url", "Server URL")`; subtitle-style
     description covers: `http://lan-box:8000` — dictation POSTs the recorded
     WAV to `<url>/v1/audio/transcriptions`; empty = local models only; audio
     leaves this machine only toward the URL you set.
   - Model row: `_entry("model", "remote_model", "Model name")`.
   - Key row: a password row — `Gtk.Entry` with `set_visibility(False)` (and
     `set_input_purpose(PASSWORD)`), inside an `Adw.ActionRow`, wrapped in a
     small `_PasswordProxy` adapter (the `_SwitchProxy` pattern) exposing
     `get_text/set_text` and registered in
     `self._rows[("model", "remote_api_key")]`. (Do NOT use
     `Adw.PasswordRow` — availability across the supported libadwaita
     versions is unverified; Gtk.Entry is universal.)
   - Timeout row: `_spin("model", "remote_timeout_s", "Timeout (seconds)",
     5, 600, 1, digits=0)`.
2. `_collect`: `("model", "remote_url")` POSTS EVEN WHEN EMPTY (empty = off)
   — explicit exception in the `Adw.EntryRow` branch, mirroring the
   `base_prompt` rule. The `_PasswordProxy` branch: skip when empty (keep the
   saved key), post the typed value otherwise. `remote_model` keeps the
   default EntryRow skip-when-empty rule.
3. `_load`: `_PasswordProxy` branch — NEVER render a stored key: value is a
   bool (masked, daemon up) or a non-empty string (file-only mode) ⇒ keep the
   field empty and set the row subtitle "key saved — type to replace"; only
   `""`/None shows a plain empty field. (Prevents both the literal `"True"`
   bug and on-screen secret echo.)
4. Backend combo (`model.backend`): append `("remote", "remote")` to the
   values list in the Engine-options group.
5. `_refresh_models` State row: when `cfg["model"].get("remote_url")` is set,
   the warmup subtitle shows `active: remote (<remote_model> @ <host>)`
   (host from `urlparse(remote_url).netloc`) instead of the local model name.
6. GTK tests (skip gracefully headless, like the rest of test_gtkui.py):
   window builds with the Remote group; `_collect` posts `remote_url: ""`
   when the entry is empty and the typed value otherwise; `_collect` omits
   `remote_api_key` when the password field is empty; `_load` with a masked
   `True` leaves the field empty (never `"True"`) and shows the saved
   subtitle; spin row round-trips 30; backend combo offers "remote".

Gate: full suite green.

## Phase 5 — Docs

Files: `README.md`, `docs/STATUS.md`.

1. README: new `### Remote STT server (optional)` subsection after "Command
   mode" (same tone as "Enable AI polish"): privacy stance first ("Nothing
   leaves your machine unless you point SayItErmano at a URL you choose —
   the remote backend is off by default and LAN-friendly"), the four config
   keys as a TOML block, examples:
   - vLLM: `vllm serve openai/whisper-large-v3 --port 8000` →
     `remote_url = "http://<lan-host>:8000"`,
     `remote_model = "whisper-large-v3"`.
   - whisper.cpp: `whisper-server -m models/ggml-large-v3.bin --port 8080`
     (its OpenAI-compatible route).
   Note any `POST /v1/audio/transcriptions` server works (NIM, DGX Spark,
   Groq/OpenAI cloud too); optional bearer key; doctor line; "clear
   remote_url — or click Use on a local model — to go back to local"; no
   live preview for remote takes; key removal = edit config.toml. Also add
   the four commented keys to the `## Configuration` model block.
2. `docs/STATUS.md` "Intentional divergences" table: one row — remote
   OpenAI-compatible STT backend, config-gated via `model.remote_url`;
   upstream declined the community PR in favor of a native protocol
   (research insight 12); cite `fluidvoice/backends/remote_stt.py`.

Gate: full suite green (docs only — rerun anyway).

## Phase 6 — Live smoke (manual done-criteria; AGENTS.md housekeeping applies)

Restart dance per AGENTS.md: `systemctl --user stop sayit-ermano`, run the
repo daemon, `systemctl --user start sayit-ermano` afterwards; restore any
config keys flipped in `~/.config/sayit-ermano/config.toml`.

1. `.venv/bin/python tests/fake_remote_stt_server.py 8399 --verbose` (leave
   running).
2. Set config: `remote_url = "http://127.0.0.1:8399"`,
   `remote_model = "whisper-large-v3"`.
3. Start the daemon; dictate with the hotkey (and/or socket `test-dictation`):
   text is typed into the focused app; the fake server log shows the multipart
   POST (file starts `RIFF`, `model` field present, no `language` under auto);
   a history entry appears with `backend: "remote"`; no live preview pill.
4. `sayit-ermano doctor`: "remote STT:" shows configured + reachable; kill
   the fake server → doctor shows unreachable; dictation shows the
   "Transcription failed: …" notification (same path as local failures), no
   crash; restart the server in `--mode http500` → exactly 2 POSTs per take.
5. Clear `remote_url` via the settings UI (empty entry + save — exercises the
   empty-is-meaningful path) → dictation uses the local backend again; click
   "Use" on a local model while remote is set → remote is cleared and the
   local model hot-swaps in; full suite green.

## Phase 6 results (2026-09-08, Xvfb-isolated live smoke per e2e recipe)

All done-criteria green on a sandboxed daemon (XDG-isolated config/history,
virtual null-sink + `module-virtual-source` mic — note: `.monitor` sources
are excluded by micmon/tray listings, so the recipe's monitor name needs the
virtual-source hop; bare-Xvfb xdotool `Control_R` did not fire the passive
grab, so takes were driven via socket `toggle`):

- happy path: take → typed "hello remote world" into gedit; server log shows
  one POST (`file` head `RIFF` 215 kB, `model=whisper-large-v3`,
  `lang=None` under `general.language=auto`, `auth=no`); history row
  `backend: "remote"`.
- no live preview for remote: remote takes log NO `preview stats:` line and
  finish in 0.1 s total; the local take after clearing logs
  `preview stats: decodes=5 …` (pill pixels unverifiable on this Xvfb —
  no 32-bit visual, the renderer can't map there at all).
- doctor: `reachable (HTTP 404 from / …)` while up → `unreachable
  (Connection refused)` after kill; failed take → `transcription failed:
  remote STT 127.0.0.1:8399: …` (key-free), daemon survives; `--mode
  http500` → **exactly 2 POSTs** per take then the failure line.
- clearing: socket `set-config model.remote_url ""` persists "off"
  (0 occurrences in the file) and hot-swaps back to faster-whisper; a real
  local take then types a real transcript; re-set + `select-model small`
  clears the URL again and swaps the local model in.
- Find fixed during the smoke: the standalone server banner crashed on
  non-JSON error modes (`http500`/`http401`) — `json.loads` in `main()`;
  now falls back to printing the raw body.

## Out of scope (do not build)

Streaming/chunked remote transcription, websocket protocols (Grok speech),
multi-endpoint routing/failover chains, server-side VAD, cloud-provider
SDKs, any network activity when `remote_url` is empty, changes to
`model_catalog.py` / `model_download.py` (verified unnecessary),
`fluidvoice/preview.py` (verified: no branch needed).

## Files touched (summary)

- `fluidvoice/config.py` — 4 keys, validation, whitelists, ENGINE_KEYS, mask, template
- `fluidvoice/backends/remote_stt.py` — NEW backend module
- `fluidvoice/backends/__init__.py` — selection rule, config_model_key, resolved_backend_name, LANGUAGE_GUARD, docstring
- `fluidvoice/daemon.py` — `_warmup_model` clears remote_url
- `fluidvoice/doctor.py` — `_remote_stt_lines` + section
- `fluidvoice/gtkui/settings_window.py` — Remote group, `_PasswordProxy`, collect/load guards, combo, State row
- `README.md`, `docs/STATUS.md` — docs
- `tests/fake_remote_stt_server.py`, `tests/test_remote_stt.py` — NEW
- `tests/test_gtkui.py` — Remote-group UI tests

Unchanged by design: `fluidvoice/recorder.py` (WAV already produced),
`fluidvoice/preview.py`, `fluidvoice/model_catalog.py`,
`fluidvoice/model_download.py`.
