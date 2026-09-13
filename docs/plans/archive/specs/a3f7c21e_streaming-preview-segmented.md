# Plan: Streaming preview — segmented finalization (phase 1)

Session `a3f7c21e`. Brief: `requests/streaming-finalization.md` (refreshed
with reviews-sweep validation + first-word-capture rider). Design context:
`docs/superpowers/specs/2026-09-05-macos-parity-and-beyond-design.md` (A1).
Status: **implemented** — verification below. The implementation rode
commit 396e63e (parallel-session sweep); test fixes + this doc + docs rows
followed up.

## Design decisions (beyond the brief's letter)

1. **Even-window tiling for commits.** Windows of `segment_s` (2.0 s) at a
   50% hop: windows 0, 2, 4, … tile the stream with zero overlap and zero
   gap, so their texts concatenate cleanly. Odd windows are never committed;
   the live tail is "the newest ≤ segment_s slice ending at stream end"
   (partial prefix allowed, so first words appear at `preview_min_audio`,
   not at the first full window). Per tick at most ONE decode: the oldest
   due commit first (order matters), else a tail refresh.
2. **Constant-cost bound made testable two ways**: decode-call count
   ≤ ticks + 1, and total decoded audio-seconds stays linear (< N²/4 on an
   N-second take) — the legacy engine re-decoded the whole take every tick.
3. **initial_prompt = last committed segment** (brief's exact wording),
   passed by `preview_transcriber()` where the backend supports it:
   faster-whisper ✓, whisper-torch ✓, whisper.cpp/parakeet ✗ (no prompt
   flag / no prompt concept — documented, omitted). The factory also
   generalizes preview beyond faster-whisper for the first time: all four
   bundled backends get live preview (whisper.cpp via temp-wav + binary,
   parakeet via its loaded featurizer/decoder on raw bytes).
4. **Tail/commit overlap dedupe.** Tail windows share audio with the last
   commit; `join_tail()` drops a word-exact suffix/prefix match (≤ 12 words)
   so re-emitted words don't flash twice.
5. **VAD**: energy + zero-crossring on 20 ms frames of the tail; frame RMS
   threshold 0.0045 ported from `audio_utils.is_silent` (`max_frame_rms`),
   ZCR ≥ 0.45 counts as unvoiced fricative speech ("sss" is not silence).
   Fires once per take, only when ≥ 1 NON-EMPTY committed segment exists
   (all-silence takes keep first-PCM/timeout semantics), then reuses the
   max-duration stop path (`_stop_recording_locked`) under the daemon lock.
6. **Self-join guard.** `on_silence` runs on the preview thread, which is
   the thread that stops the take → `SegmentedPreviewEngine.stop()` skips
   `join()` when called from its own thread (loop exits at the next
   `wait()`); otherwise stop-from-VAD would deadlock on itself.
7. **Final transcript untouched**: stop still runs one full-take decode;
   segmentation is preview+trigger only. Config
   `recording.preview_segmented` (default true) reverts to the legacy
   whole-buffer engine; `preview_segment_s` (1–6 s), `preview_vad_silence_s`
   (0 = off, default 2.0 s).
8. **First-word-capture rider** (upstream #751 regression class): pinned
   `Recorder.start()`'s probe contract — returns as soon as PCM flows
   (< 0.25 s, probe ceiling 0.35 s; a fixed-sleep refactor fails the test),
   never trims the stream head, and a live-but-silent source still returns
   within the bounded probe window.
9. **Instrumentation**: per-take `preview stats: decodes=… commits=…
   mean_decode_ms=… ticks=… audio_s=… lag_s=…` log line at stop
   (`lag_s` = audio captured − audio covered by preview text).

## Files

- `fluidvoice/preview.py`: `SegmentedPreviewEngine`, `trailing_silence_s`,
  `join_tail`, `preview_transcriber` factory (backend-generalized).
- `fluidvoice/daemon.py`: `_start_preview` prefers segmented (logs
  `segmented/<backend>` vs `legacy/faster-whisper` fallback), `_vad_auto_stop`,
  stats line in `_stop_preview`.
- `fluidvoice/config.py`: 3 new recording keys in DEFAULTS/ranges/whitelists.
- `fluidvoice/doctor.py`: `live preview:` section (engine kind, window, VAD).
- `fluidvoice/gtkui/settings_window.py`: streaming toggle + 2 spins.
- `tests/test_preview_segmented.py`: tiling, finalize semantics, linear-cost
  bound, VAD trigger table (fire-on-speech+silence, no-fire-all-silence,
  no-fire-short-silence, once-only, fricative≠silence), tail dedupe,
  factory readiness/prompt passthrough per backend, daemon wiring
  (engine choice + VAD reuses watchdog path), first-word-capture rider,
  doctor lines. Threaded end-to-end test included.

## Verification

- `.venv/bin/python -m pytest -q tests --ignore=tests/integration`:
  **1308 passed** (24 new in test_preview_segmented).
- Fake-backend decode-call bound: 40 s synthetic take → ≤ 41 decode calls,
  decoded audio-seconds < 400 (legacy shape would be ~820).
- Live smoke: see STATUS.md row (segmented/<backend> in daemon log +
  preview stats line on a real dictation after two-step restart).

## Out of scope (unchanged)

True streaming engines (Parakeet Realtime / Nemotron), the spoken-send
immediate-stop countdown UI (now unblocked by the VAD tail detector),
overlay redesign, diarization, multilingual VAD models.
