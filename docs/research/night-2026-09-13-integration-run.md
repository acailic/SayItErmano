# Night run: full integration tier — 2026-09-13

- Command: `SAYIT_PY=/home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python just test-integration --junitxml=/tmp/fv-night/build/test-results/integration.xml -rsx`
  (from the worktree root, `DISPLAY=:1`, `XDG_RUNTIME_DIR=/run/user/1000`,
  session otherwise idle).
- Source: `09e1b9ffe0c2221710218f49a1faa6b08fe83591` (branch
  `agent/night/20260913-q7-x11-matrix`).
- Environment: Pop!_OS 24.04, GNOME 46 X11 on `:1`, NVIDIA RTX 4060 8 GB,
  Python 3.12.3 (shared main-tree venv), PipeWire live. The production
  `sayit-ermano` daemon ran the whole time with whisper large-v3 resident
  on the GPU (its idle state; never touched).
- JUnit: copied to `build/test-results/integration.xml` in the worktree
  (gitignored; original also at `/tmp/fv-night/build/test-results/`).
  Raw console log: `/tmp/fv-night/logs/integration-run.log`.

## Result

**32 passed, 8 skipped, 0 failed — 172.75 s, exit 0** (40 collected).

All skips are honest capability skips; no mic-related skip occurred
(PipeWire capture worked — `TestRealRecorder.test_pw_record_roundtrip`
passed against the live default source at 01:04):

| skip | tests | reason |
|---|---|---|
| GPU busy (205–336 MiB free) | 7 (TestCli.test_transcribe_one_shot, TestHoldPassthroughLive.typing-during-hold, TestMousePTTLLive.hold-passthrough, TestRealTranscription ×2, TestRealPreviewEngine ×2) | the production daemon holds large-v3 on the 8 GB GPU; real-model GPU transcription is intentionally not attempted, and the CPU E2E test covers real transcription |
| copyq not running | 1 (TestSelectionHoldLive hygiene) | misleading — copyq IS running; its CLI probe fails inside the pytest env (see F8 in the matrix report; the check passes when run manually with the session env) |

## Per-test outcomes (junit)

Passed (32): live AI polish/rewrite ×4 (against a local/mock LLM path —
all offline-safe), CLI ×6 (version/help/doctor-ready/config-init/history/
toggle-without-daemon), daemon socket process ×7 (status shape, config
roundtrip, toggle/cancel cycle, paste-last empty, shutdown cleans socket,
unknown action, model-delete), e2e real download+transcription ×2 (tiny
CPU int8 on the JFK sample: "and so my fellow" clean, post-processing
idempotent), deb build/extract/import (72 s), one-shot installer dry-run,
hotkey live ×3 (synthetic F9 toggles recording, F12 cancels, rapid
toggles), hotkey grab recovery, selection-hold read observation, pill
overlay pixel check, mouse-PTT ×2 (escape cancels, blocked arm recovery),
parakeet-tdt-0.6b-v2 golden transcript (36 s: exact sherpa-onnx golden
sentence through the real ONNX model), real recorder ×2
(pw-record roundtrip + cancel cleanup).

## Implications for Q7's model half

- The tier is **green on real hardware with the production daemon
  resident**: hotkey grabs (F9/F12) coexist with the live Right_Control
  daemon exactly as designed; deb packaging and the installer path
  verified for real.
- The GPU-busy guard works as intended (never OOM-races the user's
  model); the trade-off is that GPU-transcription evidence must come
  from a window where the production daemon is paused (day session,
  two-step) — tonight's CPU E2E + parakeet runs still cover real-model
  decode paths end-to-end.
- One actionable: the copyq skip (F8) under-reports a live capability —
  the probe should inherit the session env or the skip reason should say
  "copyq CLI unreachable from test env".
- No flakiness: zero retries needed; the desktop marks (real X server,
  real grabs, pixel checks) all passed on the first run.
