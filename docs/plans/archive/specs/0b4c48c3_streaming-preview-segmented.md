# Plan: Streaming preview — segmented finalization (phase 1), session `0b4c48c3`

Brief: `requests/streaming-finalization.md` (verbatim). Sister docs:
`specs/a3f7c21e_streaming-preview-segmented.md` (prior session's plan),
`docs/research/2026-09-05-fluidvoice-reviews.md` (validation), roadmap
rows in `docs/ROADMAP.md` + `docs/UPSTREAM-TRACKING.md`.

## 0. Critical context: the implementation ALREADY LANDED

This brief was planned and **built by session `a3f7c21e`** in commit
`9126cb2` ("feat: segmented streaming preview — constant-cost windows,
all-backends preview, VAD auto-stop"), including docs rows, integration
test, and a recorded live smoke. The brief was then amended in `e0c59b3`
to pin planning deliverables to `specs/` only. **This session's plan is
therefore an audit-and-close plan, not a build plan.** The builder must
NOT re-implement anything — phases 1–2 below are one small test extension
and one table-cell docs edit; phase 3 is an operational re-verification.

Planner-verified current state (audit performed 2026-09-06, working tree
clean at `e0c59b3`):

| Brief item | Status | Evidence |
|---|---|---|
| 1 Segmenter: 2.0 s windows, 50% hop, incremental decode, rolling `initial_prompt` | ✅ | `fluidvoice/preview.py`: `SegmentedPreviewEngine` (even windows 0,2,4,… tile with zero overlap/gap and are COMMITTED once; per tick ≤ 1 decode — oldest due commit else live tail; `join_tail` dedupes overlap re-emissions). `preview_transcriber(cfg, backend, language)` factory: faster-whisper ✅ `initial_prompt`, whisper-torch ✅ `initial_prompt`, whisper.cpp ✗ (no prompt flag — documented in-code), parakeet ✗ (TDT has no prompt concept — documented). Preview generalized to all four backends. |
| 2 Cost bound test | ✅ | `tests/test_preview_segmented.py::test_constant_decode_cost_not_quadratic`: decode calls ≤ ticks+1 and decoded audio-seconds < N²/4 on a 40 s take (legacy whole-buffer shape ≈ N(N+1)/2). |
| 3 VAD early-stop gate | ✅ | `trailing_silence_s()` in `preview.py`: 20 ms frames, RMS 0.0045 ported from `audio_utils.is_silent` (`audio_utils.py:105`, `max_frame_rms < 0.0045` on 20 ms frames), ZCR ≥ 0.45 fricative guard ("sss" ≠ silence). Fires once per take, only when ≥ 1 NON-EMPTY committed segment. `Daemon._vad_auto_stop` (`daemon.py:1362`): re-check under `self._lock` → `_stop_recording_locked()` — the SAME path the max-duration watchdog uses (`_auto_stop` → `_stop_recording_locked`). Trigger table tests: fire-on-speech+silence, NO-fire-all-silence, no-fire-short-silence, once-only, fricative≠silence. |
| 4 Final transcript = full-take decode; config + doctor | ✅ structurally / ⚠️ unpinned | `_stop_recording_locked` stops the recorder (writes WAV from the COMPLETE raw), spawns `_process(wav)` which calls `backend.transcribe(wav)` — untouched legacy path; segmentation is preview+trigger only. Config: `recording.preview_segmented` (default true), `preview_segment_s` (range 1–6), `preview_vad_silence_s` (0–10, 0 = off) — `config.py:72–74`, ranges `:488–489`, whitelists `:391`, `:530`, `:562`. Doctor: `doctor.py::_preview_lines` (engine kind, window/hop, VAD) with tests. Settings UI toggle + 2 spins in `gtkui/settings_window.py:1476–1482`. ⚠️ No unit test PINS "stop-time transcript comes from a full-take decode, not the window mosaic" — Phase 1 closes this. |
| 5 Instrumentation + first-word rider | ✅ | `daemon.py:1367` `preview stats: decodes=… commits=… mean_decode_ms=… ticks=… audio_s=… lag_s=…` per take at `_stop_preview`. `TestFirstWordCapture` in `tests/test_preview_segmented.py`: `Recorder.start()` returns < 0.25 s when PCM flows (probe ceiling `rec.PROBE_SECONDS`), never trims the head (`raw_path` keeps all bytes from process start), bounded wait on a live-but-silent source. |
| Integration test extension | ✅ | `tests/integration/test_real_pipeline.py::test_segmented_engine_constant_cost_real_model` (JFK clip, commits ≥ 3, decodes ≤ commits·2+4). |
| Live smoke on daily-driver | ✅ recorded | `9126cb2` commit message: segmented/faster-whisper engine, "trailing silence detected, stopping" at 3.9 s, stats line, final transcript stayed a full-take decode, empty room-noise take typed nothing. Phase 3 re-verifies. |
| Tracking row notes the foundation | ⚠️ partial | `docs/ROADMAP.md` spoken-send row: "energy+ZCR trailing-silence VAD foundation landed with the segmented preview engine (2026-09-05); only the countdown UI remains" ✅. `docs/UPSTREAM-TRACKING.md:69` preview-overlay row notes segmented engine + VAD ✅. But the immediate-stop-adjacent row `docs/UPSTREAM-TRACKING.md:107` (spoken-send/quiet-countdown) cell still reads bare "quiet-countdown ⏳" — Phase 2 adds the note. |
| Unit gate green | ✅ verified this session | `.venv/bin/python -m pytest -q tests --ignore=tests/integration` → **1308 passed** in ~50 s. |

Naming note: the brief says `preview.segmented/segment_s/vad_silence_s`;
the repo's recording namespace holds every other preview key
(`preview_mode`, `preview_interval`, `preview_min_audio`), so the landed
names are `recording.preview_segmented`, `recording.preview_segment_s`,
`recording.preview_vad_silence_s`. Keep these; do not rename.

## Phase 0 — Baseline re-verification (read-only, ~1 min)

Run the gate and confirm the engine is wired:

```bash
.venv/bin/python -m pytest -q tests --ignore=tests/integration
# expect: 1308 passed (1309+ after Phase 1)
grep -n "SegmentedPreviewEngine\|_vad_auto_stop\|preview stats" fluidvoice/daemon.py
```

Exit status is the only judge — do not parse words out of output. If
anything is red or missing, STOP and report: the baseline assumption of
this plan is broken.

## Phase 1 — Pin the correctness guard (test-only)

**File:** `tests/test_preview_segmented.py`, class `TestDaemonWiring`.

Add `test_vad_stop_finishes_via_full_take_decode` (sibling of
`test_vad_auto_stop_stops_the_take`, reusing its `make_daemon` /
`make_backend` / stub-recorder helpers):

1. Capture inserted text: change the `insertion.insert_text` monkeypatch
   in the test to `lambda text, cfg, on_notice=None: captured.append(text)
   or "typed"`.
2. Make the stub backend's `transcribe(wav, language)` RECORD its `wav`
   argument (path) alongside returning `{"text": "final text", ...}`.
3. Stub recorder `stop()` writes a real WAV (already does, via `wave`) —
   make it a few seconds of `pcm()` (helper exists in this file) so
   `Path(wav).stat().st_size >= 200` clears the no-audio guard.
4. Drive: `d.recording = True`, arm a dummy `d._watchdog`, call
   `d._vad_auto_stop()`, then `d._process_thread.join(timeout=5)` (the
   finish work runs on that daemon thread) and assert:
   - inserted text == `"final text"` — the FULL-DECODE result, i.e. the
     VAD stop path produced a final transcript, not a preview mosaic;
   - `backend.transcribe` was called exactly once and its WAV contains
     the whole take: parse with `wave.open` → `getnframes()/16000` ≥ the
     stub recorder's captured audio seconds (no window-sized truncation);
   - `d.recording is False` and the watchdog was cancelled
     (`_stop_recording_locked` nulls it).
5. Keep the existing `test_vad_auto_stop_stops_the_take` untouched.

Guard rationale: brief item 4 — "the FINAL transcript at stop must remain
a full-take decode over the complete PCM… segmentation is a
preview+trigger mechanism". Today that invariant is structural (the stop
path is unchanged code); this test makes a future refactor that swaps
stop-time transcription to concatenated windows fail loudly.

Gate: unit suite green. Commit: `test: pin VAD-stop final transcript as full-take decode`.

## Phase 2 — Upstream-tracking row note (docs, one cell)

**File:** `docs/UPSTREAM-TRACKING.md`, row at line ~107
("Spoken-send commands, quiet-countdown completion, terminal blocklist").
In the Notes cell, extend the trailing `quiet-countdown ⏳` to read
(equivalent wording fine):

> quiet-countdown ⏳ — its VAD foundation landed with the segmented
> preview engine (2026-09-05, `recording.preview_vad_silence_s`); only
> the countdown UI remains

This is the literal done-criterion "the upstream tracking table row for
immediate-stop notes the new foundation" — the ROADMAP row already has
it; this adds the tracking-table mirror. Do NOT touch any other row, and
do not edit ROADMAP/STATUS (already correct; see STATUS.md's
`recording.preview_segmented=false` revert note).

Gate: unit suite green (docs-only change; run it anyway). Commit: `docs: note VAD foundation in upstream immediate-stop row`.

## Phase 3 — Live smoke re-verification (operational, no code changes)

On the daily-driver machine:

1. Restart the daemon (two-step restart per STATUS.md practice) and check
   the log shows `preview started (…, segmented/<backend>)` — NOT
   `legacy/faster-whisper` (legacy appears only while a model loads, or
   with `recording.preview_segmented=false`).
2. Dictate a real take and trail off into silence. Expect, in order:
   `trailing silence detected, stopping` ≈ 2 s after you stop speaking
   (default `preview_vad_silence_s=2.0`), then
   `preview stats: decodes=… commits=… mean_decode_ms=… ticks=…
   audio_s=… lag_s=…` with bounded `mean_decode_ms` (order of the model's
   per-window cost — for faster-whisper small this is well under a few
   hundred ms; flag if it approaches the 1.2 s tick interval).
3. Confirm the typed final transcript is the complete sentence
   (full-take decode) — no missing words at window seams, no doubled
   words from the 50% overlap.
4. Negative control: one take in pure room silence → NO VAD stop; the
   first-PCM/timeout watchdog semantics still apply (silence-only take
   keeps waiting for the human / max-duration stop).
5. Deviations are findings to report back — do not hot-patch behavior
   during smoke; the unit suite is the change instrument.

No commit (no changes). Record results in the session report; if a real
deviation is found, it becomes a follow-up item, not an in-flight fix.

## Verification (Done means, mapped)

- Each phase leaves `.venv/bin/python -m pytest -q tests
  --ignore=tests/integration` green — judge by exit status only.
- Synthetic long take, fake backend: decode-call count linear —
  `test_constant_decode_cost_not_quadratic` (already green).
- Final transcript equals full-decode result — Phase 1 test.
- Trailing-silence takes auto-stop at ~threshold; all-silence takes keep
  first-PCM/timeout semantics — `TestVad` table + Phase 1 + Phase 3.4.
- Instrumentation line with bounded mean decode ms on the daily-driver
  machine — Phase 3.2.
- Upstream tracking row notes the foundation — Phase 2.

## Out of scope (unchanged from brief)

True streaming engines (NeMo/Riva, Parakeet Realtime / Nemotron
streaming), the immediate-stop countdown UI (now unblocked), overlay
redesign, diarization, final-transcription backend/quality-knob changes,
multilingual VAD models, renaming the landed config keys. No
implementation/config/doc/test edits beyond Phases 1–2 above.

## Builder quick-reference

- Engine: `fluidvoice/preview.py` — `SegmentedPreviewEngine` (`_tick` is
  the deterministic seam tests drive directly), `trailing_silence_s`,
  `join_tail`, `preview_transcriber`; constants `VAD_FRAME_RMS=0.0045`
  (ported from `audio_utils.is_silent` max_frame_rms, `audio_utils.py:105`),
  `VAD_ZCR_MAX=0.45`, `VAD_FRAME_S=0.02`.
- Daemon wiring: `_start_preview` (`daemon.py:~1280`, prefers segmented,
  logs `segmented/<backend>` vs `legacy/faster-whisper` fallback),
  `_stop_preview` (stats line at `:1367`), `_vad_auto_stop` (`:1362`),
  `_stop_recording_locked` (cancels watchdog → stops preview → recorder
  WAV from complete raw → `_process` thread → `backend.transcribe`).
- Config: `recording.preview_segmented` / `preview_segment_s` /
  `preview_vad_silence_s` in `config.py` DEFAULTS `:72–74`, ranges
  `:488–489`, whitelists `:391`, `:530`, `:562`; doctor `_preview_lines`
  `doctor.py:180`; settings UI `settings_window.py:1476–1482`.
- Tests: `tests/test_preview_segmented.py` (drive helpers `pcm`,
  `silence`, `fricative`, `RecordingEngine`, `drive`),
  `tests/integration/test_real_pipeline.py` (real-model segmented test,
  excluded from the unit gate).
