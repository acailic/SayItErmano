# Plan: Idle model unload / keep-warm policy (`model.idle_unload_s`)

Session: `32005ced` · Spec: `specs/32005ced_idle-model-unload.md`
Source: docs/research/2026-09-05-fluidvoice-reviews.md insight 4 (upstream pins ~3.4 GB models forever — issue #548, discussion #854; maintainer refuses time-based unload; #922 asks for a configurable keep-awake window). Linux port ships it as a differentiator.

**Deliverable constraint honored:** this phase writes only plan documents. All code/doc/test changes below belong to the builder.

---

## 0. Verified ground truth (planner recon, file:line)

Everything below was read directly in this session; line numbers are current HEAD (`8c06e09`).

**Where the model lives**
- `Daemon.backend` holds the active backend instance; `None` = not loaded (`fluidvoice/daemon.py:323`, comment "lazy: loads model on first use").
- Load-on-demand chokepoint: `Daemon._ensure_backend()` (`daemon.py:1727-1731`) — called by `_process` (transcription, `daemon.py:1739`, error path notifies "Transcription failed" and returns cleanly, `daemon.py:1661-1666`) and `test_dictation` (`daemon.py:1665`).
- Other `self._backend_factory` users: `run()` startup (`daemon.py:371`), model hot-swap `_warmup_model` (`daemon.py:1146,1152`), engine-option reload `_reload_backend` (`daemon.py:1167-1168`).
- Eager warmup: `run()` constructs the backend, then if `model.eager_warmup` (default True) spawns the `fluidvoice-warmup` thread calling `backend.warmup()` (`daemon.py:371-390`).
- **Take lifecycle:** `toggle()` (`daemon.py:1382`) → `_start_recording_locked()` (`daemon.py:1407`, starts watchdog `threading.Timer`, preview, first-PCM timer) → stop via `_stop_recording_locked()` (`daemon.py:1571`, stops preview, spawns `_process` thread) → `_process` clears `busy` in its `finally` (`daemon.py:1765+`). VAD auto-stop funnels into `_stop_recording_locked` (`_vad_auto_stop`, `daemon.py:1537`).
- Preview is built once at take start from the live backend: `preview_transcriber(cfg, self.backend, language)` (`daemon.py:1470`; `fluidvoice/preview.py:314-321`) — **it already handles `backend=None` by returning None ("preview simply stays off")**. Preview only exists while recording.
- Status IPC: `handle_request` "status" (`daemon.py:1236-1265`) returns `backend: self.backend.name if self.backend else None` (already None-safe) plus `warmup`, `active_model_key`, etc. Additive keys are established practice ("additive keys - JSON consumers unaffected", `daemon.py:1259`).
- Tray: `TrayIcon(tooltip=callable)`; daemon supplies `_tray_tooltip()` (`daemon.py:705-721`, format `SayItErmano: {state}{hint}` + `" - hotkey blocked!"` / `" - paused (locked)"` suffixes). `_refresh_tray()` (`daemon.py:913-922`) re-pushes icon/tooltip from any thread (hotkey thread precedent; `tray.py:467-470`).
- Live config apply: settings UI → `set-config` → `_set_config` → `apply_settings` (validate/merge) → `apply_config(live)` (`daemon.py:1286,1294-1327,1085-1120`). `RESTART_REQUIRED = {"model.eager_warmup"}` (`fluidvoice/config.py:629`); `ENGINE_KEYS` triggers backend reload (`config.py:630-632`).
- `shutdown()` (`daemon.py:741-783`) stops all monitors — the idle thread joins here.

**What "unload" means per backend (all verified; no `close()` exists anywhere today)**
- `backends/__init__.py` defines the `Backend` base (`name`, `transcribe`, optional `warmup`) and `load_backend(cfg)` — the single config→backend chokepoint. No remote backend exists in `backends/`.
- faster-whisper: weights in `self._model` (CTranslate2 `WhisperModel`), lazy `_load()` (`fluidvoice/backends/faster_whisper_backend.py:32,57-78`). Dropping the instance ref + `gc.collect()` frees RSS and (CTranslate2) VRAM.
- torch: `self._model = whisper.load_model(...)` (`fluidvoice/backends/torch_whisper.py:24-31`). Drop frees the module; torch's CUDA caching allocator may retain VRAM — **out of scope** per prompt (no `empty_cache` tricks).
- parakeet ONNX: weights via `self._decoder` (ort sessions), lazy `_load()` (`fluidvoice/backends/parakeet_onnx.py:227-277`). Drop frees.
- whisper.cpp: `subprocess.run` per transcription (`fluidvoice/backends/whisper_cpp.py:43-48`); the instance holds only strings — **no pinned memory; unload is bookkeeping only** (no long-lived process to kill).
- `preload_cuda_libs()` dlopens cuDNN/cuBLAS once (`backends/__init__.py`); those shared libs stay mapped after unload — expected, small, shared; note in measurements.

**Config mechanism** (`fluidvoice/config.py`)
- `DEFAULTS["model"]` at `config.py:98-109` (`backend/name/device/compute/whispercpp_model/eager_warmup/languages`).
- Whitelist: model keys listed at `config.py:430`. Validation: `coerce_setting` (`config.py:634-701`) — generic `SETTING_RANGES` (kind, (lo,hi)) plus custom branches (e.g. `("general","language")`, `("ai","max_retries")` int-range special-case at `config.py:895-903`). `apply_settings` (`config.py:882-916`) merges validated keys, returns `changed`/`rejected`. Template generator `write_template` (`config.py:401`).
- Tests: `tests/test_config_settings.py` exists; suite runs with `.venv/bin/python -m pytest -q tests --ignore=tests/integration` (markers in `pyproject.toml:54`).

**Doctor** (`fluidvoice/doctor.py`)
- Daemon-live lines use `control.request("status")` with socket-exists guard and "daemon down" fallback — `_hotkey_grab_line` (`doctor.py:254-273`), `_mouse_ptt_lines` (275), `_lock_watch_lines` (311). Output assembled in `run()` (`doctor.py:487+`; models-cache lines printed at 524).

**Settings window** (`fluidvoice/gtkui/settings_window.py`)
- `_build_models` (`settings_window.py:476-530`): groups "Speech models", "whisper.cpp GGUF", "Parakeet", "State" (warmup spinner row), "Engine options", per-model language, disk usage.
- Generic row plumbing: `_spin(section, key, title, lo, hi, step, digits, subtitle)` registers an `Adw.SpinRow` in `self._rows` (`settings_window.py:273-280`); `_load`/`_collect` iterate `self._rows` generically (`:300-380`) **plus special-cased extras** (`modifiers`, `mic_priority`, `model.languages` — `:364-380`). Custom units therefore follow the special-case pattern.
- `save()` → `self.c.set_config(body)` → daemon `_set_config` → `apply_config` (`settings_window.py:390-426`).

**Tests**
- `tests/test_daemon.py`: `StubRecorder`/`StubBackend`, `cfg` (deep-copied `DEFAULTS`) and `quiet_ui` fixtures at top; daemon built as `dm.Daemon(cfg, recorder=rec, backend_factory=lambda c: ..., use_hotkey=False, use_sounds=False)`; `wait_done`-style deadline loops with `time.monotonic()`. `test_backend_factory_failure_is_lazy` (`test_daemon.py:416-424`) is the exact graceful-reload-error precedent.
- No fake clock exists in the suite — the plan injects `now` into the idle check instead of sleeping.
- `tests/conftest.py` isolates XDG; never writes real config/history.

---

## 1. Design decisions (settled)

1. **Unload = drop the daemon's instance reference.** `self.backend = None` through the existing seam (`_backend_factory` re-loads on next use), plus an optional `Backend.close()` no-op on the base class for future backends, plus `gc.collect()`. No per-backend unload code (whisper.cpp needs none; CTranslate2/ORT free on refcount drop; torch cache retention is out of scope).
2. **Idle definition (activity events):** take start, take stop, transcription-complete (`_process` finally), successful eager/start warmup, successful model hot-swap/reload, and setting the policy at runtime. NOT: status polls, preview ticks (only exist while recording anyway), history reads/paste-last/insert-text, doctor probes.
3. **Timer shape:** a single daemon watcher thread with `threading.Event.wait(interval)` (precedent: watchdog `threading.Timer`, micmon poller). Poll interval `max(5, min(60, threshold // 4))` seconds. `_maybe_idle_unload(now=None)` takes an injectable `time.monotonic()` for tests. Thread exists **only when** `idle_unload_s > 0` — when 0, nothing is started and behavior is byte-identical to today.
4. **Reload trigger:** `_start_recording_locked` spawns a background `_ensure_backend()` thread when `self.backend is None` (load overlaps speech); `_process`'s synchronous `_ensure_backend()` remains the guaranteed path and fails gracefully (existing "Transcription failed" notify, covered by `test_backend_factory_failure_is_lazy`). A new `_backend_load_lock` serializes concurrent `_ensure_backend` callers — this also closes a pre-existing hole: `test_dictation` (onboarding) runs `_ensure_backend` on the control-socket thread with no lock today, so it can race `_process`'s load and double-load. First take after unload has no live preview (preview builds at take start from a not-yet-loaded backend; `preview_transcriber` already returns None) — documented trade-off.
5. **Never mid-take:** the unload decision checks `recording`, `busy`, `warmup["running"]`, and start-warmup-thread liveness under `self._lock`; the actual drop (destructor can take ~100 ms) happens outside the lock.
6. **Config:** `model.idle_unload_s`, int, default 0. Valid: 0 or 30..86400 (custom `coerce_setting` branch — the plain ranges table cannot express "0 or ≥30"). Not in `RESTART_REQUIRED`, not in `ENGINE_KEYS` (live-applied via `apply_config`).
7. **Surfacing:** additive `model_state` dict in the status payload (`{"policy_s": int, "loaded": bool, "idle_s": float}`); doctor line from the same payload; tray tooltip suffix; Settings Models page spinbutton in whole minutes (0 = off) with custom load/collect (seconds↔minutes).

---

## 2. Phases

Every phase ends with `.venv/bin/python -m pytest -q tests --ignore=tests/integration` green. Run it on a clean tree first (Phase 0 baseline) and after each phase.

### Phase 0 — baseline
- `.venv/bin/python -m pytest -q tests --ignore=tests/integration` → confirm green before touching anything.

### Phase 1 — config plumbing (`model.idle_unload_s`)
Files: `fluidvoice/config.py`, `tests/test_config_settings.py`.

1. `DEFAULTS["model"]["idle_unload_s"] = 0` with an inline comment (`config.py:98-109` block): `# seconds idle (no dictation) before the model is unloaded; 0 = never`.
2. Add `"idle_unload_s"` to BOTH model registries — `config.py` is a dict schema, not dataclasses, and a key missing from either is dropped or rejected:
   - `_SAVE_WHITELIST["model"]` (`config.py:429-431`) — keys `save_config` persists (missing here = the UI save silently drops the value).
   - `ALLOWED_SETTINGS["model"]` (`config.py:604-606`) — keys `apply_settings` accepts over the socket and in the daemon-offline UI fallback.
3. Custom branch in `coerce_setting` (next to the `("general","language")` branch, `config.py:640-645`):
   - accept `int` (reject `bool`) where `value == 0 or 30 <= value <= 86400`; return `(ok, int(value))`.
4. Add the commented line to the `TEMPLATE` string's `[model]` block (`config.py:211+`; `write_template` writes TEMPLATE): `# idle_unload_s = 300        # unload the speech model after N idle seconds (0 = keep loaded)`. This is enforced by the suite's doc-guard idiom (`tests/test_config_settings.py:296` `test_defaults_documented_in_template` asserts every DEFAULTS key appears in TEMPLATE).
5. Do NOT touch `RESTART_REQUIRED` / `ENGINE_KEYS`.
6. Tests (new class in `tests/test_config_settings.py`, follow existing apply_settings/coerce tests): default is 0; accepts 30 / 3600 / 86400; rejects 29, 1, 86401, -60, `"abc"`, `True`, `30.5`; `apply_settings` reports `model.idle_unload_s` in `changed` only on real change; rejected values leave cfg untouched.

Verify: pytest suite green.

### Phase 2 — daemon idle-unload core (the seam)
Files: `fluidvoice/backends/__init__.py`, `fluidvoice/daemon.py`, `tests/test_idle_unload.py` (new).

1. `Backend.close(self) -> None: pass` on the base class (`backends/__init__.py:147-155`) — optional teardown hook, no-op today.
2. `daemon.py` state (in `__init__`, near `daemon.py:349`):
   - `self._last_activity = time.monotonic()`
   - `self._idle_unloaded_at: float | None = None`
   - `self._backend_load_lock = threading.Lock()`
   - `self._idle_thread = None`, `self._idle_stop = threading.Event()`
   - `self._start_warm_thread = None` (captures the existing `run()` warmup thread, `daemon.py:390`)
   - `import gc` at module top.
3. `Daemon._touch_activity()` — `self._last_activity = time.monotonic()`.
4. `Daemon._ensure_backend()` (rewrite `daemon.py:1727-1731`):
   ```python
   def _ensure_backend(self):
       with self._backend_load_lock:
           if self.backend is None:
               self.backend = self._backend_factory(self.cfg)
               log(f"speech backend: {self.backend.name}")
               self._idle_unloaded_at = None
               self._touch_activity()
       return self.backend
   ```
5. `Daemon._maybe_idle_unload(self, now: float | None = None)`:
   - `now = time.monotonic() if now is None`; threshold `t = int(self.cfg["model"].get("idle_unload_s", 0) or 0)`; `if t <= 0 or self.backend is None: return`.
   - Under `self._lock` decide: bail if `self.recording or self.busy or self.warmup.get("running")` or `self._start_warm_thread is not None and self._start_warm_thread.is_alive()` or `now - self._last_activity < t`. Otherwise snapshot `backend = self.backend`, set `self.backend = None`, `self._idle_unloaded_at = now`; release lock.
   - Outside the lock: `try: backend.close() except Exception: pass`, `gc.collect()`, `log(f"model idle {int((now - self._last_activity) // 60)}m >= {t}s - unloaded")`, `self._refresh_tray()`.
6. Watcher thread:
   - `_start_idle_watch()`: if threshold <= 0 or thread alive → return; `self._idle_stop = threading.Event()`; `self._idle_thread = threading.Thread(target=self._idle_watch_loop, name="fluidvoice-idle-unload", daemon=True)`; start.
   - `_idle_watch_loop()`: `while not self._idle_stop.wait(max(5.0, min(60.0, t / 4.0))): try: self._maybe_idle_unload() except Exception as e: log(f"WARN idle-unload check failed: {e}")`.
   - `_stop_idle_watch()`: `self._idle_stop.set()`; join(timeout=2) if thread.
   - `_apply_idle_unload_setting()`: `_stop_idle_watch()`; `self._touch_activity()`; `_start_idle_watch()` (fresh `Event` inside `_start_idle_watch`; when the new value is 0 nothing starts — model stays loaded, watching stops).
   - Call `_start_idle_watch()` in `run()` right after the eager-warmup block (~`daemon.py:391`); store the warmup thread in `self._start_warm_thread` and touch activity in `_warm` after `backend_ref.warmup()` succeeds (`daemon.py:385-386`).
   - `shutdown()` (`daemon.py:741`): add `self._stop_idle_watch()` first.
7. Activity touch points (one line each):
   - `_start_recording_locked` after `self.recording = True` (`daemon.py:1412`; note `_start_recording_locked` spans 1397-1429).
   - `_stop_recording_locked` after `self.recording = False` (`daemon.py:1581`).
   - `_process` `finally` block alongside `self.busy = False` (`daemon.py:1765`).
   - `_warmup_model` success branch (`daemon.py:1152-1153`) and `_reload_backend` success (`daemon.py:1167-1168`).
8. Take-start reload: in `_start_recording_locked`, after the touch, `if self.backend is None: threading.Thread(target=self._ensure_backend, name="fluidvoice-reload", daemon=True).start()` — unconditional on policy (also improves the lazy-first-use case; `_process`'s synchronous call remains the graceful-error guarantee).
9. `apply_config` (`daemon.py:1085-1120`): add `if "model.idle_unload_s" in changed: _try("idle unload", self._apply_idle_unload_setting)`.
10. Status payload (`handle_request` "status", `daemon.py:1236-1265`): additive key, computed under `self._lock`:
    ```python
    "model_state": {"policy_s": int(...idle_unload_s or 0),
                    "loaded": self.backend is not None,
                    "idle_s": round(time.monotonic() - self._last_activity, 1)}
    ```
11. New `tests/test_idle_unload.py` — stubs copied from `tests/test_daemon.py` (`StubRecorder`, `StubBackend`, `cfg`, `quiet_ui`-style notify capture or import from test_daemon if importable — test files are modules under the same package (`tests/__init__.py` exists), so `from tests.test_daemon import StubRecorder, StubBackend` works if those names stay importable; otherwise redefine locally). Counting factory: `loads = []; def factory(c): b = CountingBackend(); loads.append(b); return b`.
    Tests (no real sleeping except the thread-spawn test; drive `d._maybe_idle_unload(now=...)` with synthetic monotonic values):
    - load on first take: `d.toggle(); d.toggle(); wait_done` → exactly 1 factory call.
    - no unload while active: set `d.recording = True` / `d.busy = True` → `_maybe_idle_unload(now=base + 10_000)` keeps `d.backend`.
    - unload at threshold: `idle_unload_s=60`; take completes; `_maybe_idle_unload(now=base+59)` → still loaded; `now=base+60` → `d.backend is None`, `_idle_unloaded_at` set.
    - no unload when 0: `idle_unload_s=0`; `_maybe_idle_unload(now=base+999_999)` → loaded; `_start_idle_watch()` leaves `_idle_thread None`.
    - reload-on-next-take success: after unload, `d.toggle()` → within deadline `d.backend is not None` (reload thread; polling loop with 5 s deadline like `test_daemon.wait_done`), `d.toggle()` → transcription succeeds, factory called twice total, `_idle_unloaded_at is None`.
    - reload error path: factory raises on 2nd call → take completes with `last_result == {}` and a "Transcription failed" notify (mirror `test_backend_factory_failure_is_lazy`, `test_daemon.py:416-424`); daemon still functional.
    - timer not reset by non-activity: after a take, call `d.handle_request({"action": "status"})`, `d.handle_request({"action": "paste-last"})` (with a `last_result` seeded), `d.handle_request({"action": "insert-text", ...})` monkeypatched insertion — then `_maybe_idle_unload(now=base+threshold)` still unloads (proves those paths don't touch).
    - `close()` seam: backend stub with `close()` recording the call → unloaded stub's `close_called` is True.
    - warmup guard: `d.warmup = {"running": True, ...}` → no unload.
    - apply_config live change: `d.cfg["model"]["idle_unload_s"] = 60; resp = d.apply_config(["model.idle_unload_s"])` → `"idle unload" in resp["applied"]`, thread running; set back to 0 → thread stopped.
    - shutdown idempotence: `d.shutdown()` with watcher running exits cleanly.
12. Keep `tests/test_daemon.py` untouched and green (regression: `_ensure_backend` rewrite must not break `test_backend_factory_failure_is_lazy`).

Verify: full suite green; `python - <<'EOF'`-style REPL check optional: construct Daemon with stubs and drive one unload cycle manually.

### Phase 3 — surfacing (doctor, tray, settings UI, README)
Files: `fluidvoice/doctor.py`, `fluidvoice/daemon.py` (tooltip), `fluidvoice/gtkui/settings_window.py`, `README.md`, `tests/test_idle_unload.py` (extend).

1. Tray tooltip — `Daemon._tray_tooltip` (`daemon.py:705-721`): inside the existing `with self._lock` block also read `backend_none = self.backend is None`; after the locked/paused suffixes:
   ```python
   t = int(self.cfg["model"].get("idle_unload_s", 0) or 0)
   if backend_none and t > 0:
       idle_m = int((time.monotonic() - self._last_activity) // 60)
       tip += f" - model unloaded (idle {idle_m}m)"
   ```
   The unload path already calls `_refresh_tray()` so the suffix appears without waiting for a state transition.
2. Doctor — new `_model_state_lines(cfg: dict) -> list[str]` in `doctor.py` (follow `_hotkey_grab_line`, `doctor.py:254-273`):
   - `t = int((cfg.get("model", {}) or {}).get("idle_unload_s", 0) or 0)`.
   - Daemon up (`paths.socket_path().exists()` + `control.request("status")` succeeds): read `model_state` →
     - `t == 0`: `["  model: loaded (idle unload off)"]` (or `unloaded` never happens with 0; if `loaded` False with t==0, print `model: not loaded (lazy first use)`)
     - `t > 0`, loaded: `["  model: loaded (idle Xm; policy {t}s)"]`
     - `t > 0`, unloaded: `["  model: unloaded (idle Xm; policy {t}s)"]`
     - `idle_s = model_state["idle_s"]`, `Xm = int(idle_s // 60)`.
   - Daemon down: `["  model: daemon down (policy from config: {t}s)"]`; on older daemon without `model_state`: treat as unknown policy-live state, print config line only.
   - Print in `run()` right after the models-cache lines (`doctor.py:524`) — keeps the config fallback visible even when the daemon is down (the alternative `control socket:` block at `doctor.py:587` only renders when the socket exists).
3. Settings Models page — `_build_models` (`settings_window.py:476-530`): new `Adw.PreferencesGroup(title="Memory")` between "State" and "Engine options":
   - `self._idle_unload_row = Adw.SpinRow` with `Gtk.Adjustment(value=0, lower=0, upper=1440, step_increment=1)`, `digits=0`, title "Unload model after idle (minutes)", subtitle "0 = keep the model loaded (fastest first word); higher frees RAM/VRAM after inactivity — the next dictation pays the model load time", connected to `self._touch()`.
   - Do NOT register in `self._rows` (units differ). In `_load` (`settings_window.py:300-345`, after the `self._rows` loop): `s = int(self.cfg.get("model", {}).get("idle_unload_s", 0) or 0); self._idle_unload_row.set_value(float(0 if s == 0 else max(1, round(s / 60))))` — hand-edited sub-minute values (30-89 s) round up to 1 min on display; note the save converts back to whole minutes (documented UI granularity).
   - In `_collect` (next to the other special cases, `settings_window.py:364-380`): `body.setdefault("model", {})["idle_unload_s"] = int(self._idle_unload_row.get_value()) * 60`.
   - Optional polish: in `_refresh_models`, when `st.get("model_state", {}).get("loaded") is False`, set `self.warmup_row.set_subtitle("unloaded (idle)")`.
4. README (`README.md`):
   - In the `[model]` TOML block (`README.md:379-390`): `# idle_unload_s = 0     # unload the model after N idle seconds (0 = never; 30..86400)`.
   - One short paragraph right after the Configuration code block: **"Why is my first dictation slow after a break?"** — with `idle_unload_s` set, the speech model is released after the idle window to free RAM (and VRAM); the first dictation afterwards pays the model load again (a few seconds) while later ones are instant. Set it to 0 to keep the model always loaded. Mention `sayit-ermano doctor` shows the live state. User-facing tone; no upstream-issue citations needed.
5. Tests (extend `tests/test_idle_unload.py`):
   - Tooltip: with `idle_unload_s=60` and stub tray (`d._tray = None` is fine — `_tray_tooltip` only reads state): loaded → no suffix; after `_maybe_idle_unload(now=...)` → `"model unloaded (idle" in d._tray_tooltip()`.
   - Doctor lines: monkeypatch `fluidvoice.control.request` to return a fake status dict (and `paths.socket_path` existence via monkeypatched tmp file or monkeypatch `paths.socket_path` to a `tmp_path` file) — assert the three formats (off / loaded / unloaded) and the daemon-down line.
   - Status payload: `d.handle_request({"action": "status"})["model_state"] == {"policy_s": 60, "loaded": False/True, ...}`.

Verify: full suite green; manual `sayit-ermano doctor` shows the new line (daemon down variant acceptable headless).

### Phase 4 — live smoke on the daily driver + measurement (builder)
1. Set `idle_unload_s = 60` in the real config; restart the daemon (`systemctl --user restart` or the packaged unit in `systemd/`).
2. Take one dictation (model loads; doctor shows `model: loaded`).
3. Measure and record in this spec file (append a "Measured results" section — the builder owns doc edits in this phase):
   - RSS before unload / after unload: `ps -o rss=,vsz= -p $(pgrep -f 'sayit-ermano.*daemon|fluidvoice.*daemon' | head -1)` (use the actual process name from `systemctl --user status`).
   - VRAM (CUDA machines): `nvidia-smi --query-compute-apps=pid,used_memory --format=csv` before/after (torch-cache caveat noted if applicable).
   - Wall time from last activity to observed unload (expect ≤ ~75 s with the 60 s threshold and ≤60 s poll interval... with `t/4` interval = 15 s, expect ≤ ~75 s).
4. Verify next dictation: hotkey → speak → text inserted correctly (reload overlapped speech; note perceived latency).
5. `sayit-ermano doctor` in both states (loaded / after idle unload); tray tooltip shows the suffix while unloaded.
6. Set `idle_unload_s = 0` via Settings → Models → Memory (spinbutton 0), confirm live-apply (`applied: idle unload` feedback / doctor shows "idle unload off") without a daemon restart.
7. Re-run the full suite one last time; append measured numbers + observations to this spec.

---

## 3. Interaction rules recap (acceptance)

- `idle_unload_s = 0` (default): no watcher thread, no tooltip suffix, doctor says off, everything byte-identical to today.
- Eager warmup at start still loads; the idle clock starts at daemon start and is re-touched when the warmup thread finishes.
- Unload never fires while recording, busy (transcribing/AI/command), during a model switch/download (`warmup["running"]`), or during the start-warmup thread.
- VAD auto-stop and segmented preview unchanged (they funnel into the same stop path; preview never survives a take).
- First take after unload: model reloads (load overlaps speech via the take-start thread; `_process` guarantees graceful failure with a notification if the reload errors), no live preview for that take, then normal.
- Status/preview/history/doctor reads never reset the idle clock.

## 4. Out of scope (restated from the brief)

No pruning of on-disk models (exists: Settings → Models / `delete_model`), no `torch.cuda.empty_cache()` or GPU cache tricks, no suspend/lock hooks (lockmon owns those; lockmon already cancels recording), no per-backend custom unload code beyond the `close()` no-op seam, no threshold auto-tuning, no unloading anything but the active model.

## 5. Risks / notes for the builder

- `_ensure_backend` gains a lock — keep the factory call the only thing under it (slow); never call it while holding `self._lock`.
- The start-warmup orphan race (unload fires while `_warm` still running) is closed by the `_start_warm_thread.is_alive()` guard; even without it the outcome is benign (a doomed object finishes warming, then frees).
- `DictationPipeline` holds the backend only per-take; `SegmentedPreviewEngine`'s transcriber closure is dropped at `_stop_preview` — no lingering weight references minutes later; `gc.collect()` is belt-and-braces.
- The status payload key is additive — CLI/UI consumers ignore unknown keys (established precedent at `daemon.py:1259`).
- Settings spinbutton granularity is whole minutes; a hand-edited `idle_unload_s = 45` keeps working (validation allows it) but displays/saves as 1 minute after the user touches the row — acceptable, documented.
- Commit suggestion per phase: `feat(config): model.idle_unload_s setting`, `feat(daemon): idle model unload + reload-on-take`, `feat(ui): surface idle-unload state (doctor/tray/settings/readme)`, `docs: measured idle-unload smoke results`.

---

## 6. Measured results (Phase 4 live smoke, 2026-09-05)

Method: second daemon instance from the repo venv, fully isolated via
`XDG_CONFIG_HOME`/`XDG_DATA_HOME`/`SAYITERMANO_SOCKET` (the documented
multi-daemon overrides), real daily-driver config + `idle_unload_s = 60`,
real faster-whisper **small** model, real mic. The installed daily-driver
daemon kept running throughout and stayed healthy (hotkey grabbed, backend
loaded) — verified before and after.

**CUDA machine (faster-whisper small, float16, eager warmup):**

| State | RSS | VRAM |
|---|---|---|
| loaded (after warmup) | 568.4 MiB | 752 MiB |
| after idle unload | 558.9 MiB | **102 MiB** |
| reloaded (first take after) | 953.9 MiB* | 752 MiB |

- Unload observed **73 s after the warmup finished** (threshold 60 s,
  poll interval `max(5, min(60, t/4))` = 15 s → worst case ~75 s, as designed).
- **650 MiB VRAM freed**; the 102 MiB residual is the CUDA context held by
  the preloaded cuDNN/cuBLAS `dlopen` handles (expected, shared, documented
  out-of-scope). RSS on CUDA only drops ~10 MiB because the weights live in
  VRAM; the host-side allocator arenas stay mapped.
- *RSS after reload is higher than the initial load: one real transcription's
  transient buffers (malloc arenas) — normal, not a leak.
- **CPU mode** (`SAYITERMANO_FORCE_CPU=1`, small int8): RSS 600.5 → 459.6 MiB
  (**~141 MiB freed**; standalone probe, same unload path).

**First dictation after an unload:** the take-start reload overlapped a 4 s
take; the whole toggle→transcribed cycle took **5.3 s** (vs. ~1 s warm).
Transcription completed gracefully on a silent room (`empty transcription`,
nothing typed — no hang, no error). `speech backend: faster-whisper` logged
by the reload thread while the user "spoke".

**Surfacing, observed live:**
- status: `model_state: {'policy_s': 60, 'loaded': True, 'idle_s': 2.7}` →
  `{'policy_s': 60, 'loaded': False, 'idle_s': 75.6}`.
- doctor: `model: unloaded (idle 1m; policy 60s)` → after the take
  `model: loaded (idle 0m; policy 60s)`.
- Live-apply over `set-config` (`idle_unload_s` 60 → 0), no restart:
  `applied: ['idle unload']`, doctor: `model: loaded (idle unload off)`.
- Older daemon without `model_state` (the installed v0.5.0 daily driver):
  `model: unknown (older daemon; policy from config: 0s)`.

**Bug found and fixed during the smoke:** `run()`'s eager-warmup design kept
the backend in a `backend_ref` closure cell local to `run()` — alive for the
daemon's whole lifetime, defeating the unload (first smoke showed VRAM
752 → 752). Fixed by snapshotting the reference *inside* the `_warm` thread
function (the frame dies with the thread); verified with a gc probe (0 live
`WhisperModel` objects after unload) and re-smoked. Also `del backend` before
`gc.collect()` in `_maybe_idle_unload` so reference cycles die in the same
collection.

**Suite:** 1533 passed (baseline 1483; +50 new tests:
`tests/test_idle_unload.py` 27, config 9, GTK roundtrip 1, doctor/status
cases included above).
