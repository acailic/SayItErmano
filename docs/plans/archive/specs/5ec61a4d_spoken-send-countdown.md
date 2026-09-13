# Plan: Spoken-send quiet countdown (B7)

Source: design doc B-track B7 ("gated on A1's VAD tail detector; follow-up
inside A1's spec") + UPSTREAM-TRACKING row "quiet-countdown completion —
its VAD foundation landed with the segmented preview engine; only the
countdown UI remains". Upstream behavior: after the user says the send
phrase and goes QUIET, a short visible countdown runs; speaking again
cancels it; expiry finishes the dictation (no hotkey press) and the send
key (Enter) is pressed by the existing spoken-send path.

## Verified facts (agent worktree @ b4290da)

- `SegmentedPreviewEngine._tick` (preview.py:273) computes trailing
  silence FIRST (energy+ZCR DSP on the raw tail, ≥ threshold + committed
  speech → one-shot `on_silence`), then commits, then the live tail
  decode; `_emit(committed, tail)` → `on_text(shown)` (pill), deduped by
  `last_text`.
- Daemon `_start_preview` (daemon.py:1653) wires
  `on_silence=self._vad_auto_stop` (default VAD 2.0 s,
  `recording.preview_vad_silence_s`); `_vad_auto_stop` locks, re-checks
  `self.recording`, `_stop_recording_locked()` — the exact finish path a
  countdown expiry reuses.
- `parse_spoken_send(text, phrase)` (processing/extra_formats.py:33) is
  the canonical trailing-phrase matcher (comma + optional trailing
  period + the "literal <phrase>" escape) — reuse it for the preview-side
  endswith check so arm semantics == final strip semantics.
- `Daemon._start_preview` owns the display; `display.show(text)` renders
  pill text; `set_state("processing")` exists. NotifyPreview (Wayland)
  show() works, set_state is a no-op.
- spoken-send post-processing (daemon `_after_ai_formatting`) strips the
  phrase, sets `_pending_send_key`, `_press_send_key` presses it after
  insertion — already shipped; the countdown only changes WHEN the take
  stops.
- Test harness: `tests/test_preview_segmented.py` has `pcm()/silence()/
  fricative()/drive()` (deterministic tick replay, no threads) and the
  VAD trigger-table class to extend.
- Settings: `send.add_row(_plain_switch_row(...spoken_send_enabled...))`
  + phrase entry + key combo exist (settings_window.py:1840-1844).

## Design decisions (binding)

1. **Trigger ladder** (defaults): 0.5 s trailing quiet + rolling text
   ending with the phrase → COUNTDOWN ARMS (pill notice, engine pauses
   text emission); countdown lasts `recording.spoken_send_countdown_s`
   (default 1.2, 0 = feature off) → take stops (~1.7 s total quiet). The
   existing VAD auto-stop (2.0 s, unchanged, no-phrase path/backstop)
   can never beat an armed countdown. Speech resuming before expiry
   CANCELS (pill restored, countdown re-armable).
2. **Engine-side**: new kwargs `send_phrase=""`, `send_countdown_s=0.0`,
   `on_send_countdown=None`, `on_send_resume=None`. In `_tick`, before
   the existing VAD block: while feature on, not `_silence_fired`,
   `any(committed)`, and `sil >= 0.5` and
   `parse_spoken_send(self.last_text, send_phrase).should_send` → fire
   `on_send_countdown()` once (`_send_armed=True`). While `_send_armed`:
   `_emit` suppressed (the notice stays); `sil < 0.5` → `_send_armed=
   False` + `on_send_resume()`. Speech/quiet DSP is computed once per
   tick and shared with the VAD block.
3. **Daemon-side**: `Daemon._on_send_countdown()` → log, pill notice
   (`⏎ sending… (speak to cancel)` via the tracked display), arm
   `threading.Timer(countdown_s, self._send_countdown_stop)` stored on
   `self._send_countdown_timer`; `_send_countdown_stop` verifies the
   timer is still the armed instance, locks, re-checks `recording`, logs
   `spoken-send quiet countdown elapsed, stopping`,
   `_stop_recording_locked()` (same as `_vad_auto_stop`). Resume/stop/
   cancel paths null+cancel the timer (a stale timer firing during the
   NEXT take is impossible: identity check + recording re-check).
   `_start_preview` wraps `display.show` to track the last text and
   re-show it on resume; passes the kwargs only when
   `spoken_send_enabled` and countdown > 0.
4. **Config**: `recording.spoken_send_countdown_s` float (0.3..5.0),
   default 1.2, 0 = off (dedicated branch accepting 0; range clamps
   non-zero). Whitelists + template comment. Live per-take (no
   ENGINE_KEYS).
5. **Surfaces**: doctor spoken-send line gains the countdown value;
   Settings Recording spoken-send group gains a spin row; README
   spoken-send note gains two sentences.

## Phase 1 — engine + config + daemon wiring

Files: `fluidvoice/preview.py`, `fluidvoice/config.py`,
`fluidvoice/daemon.py`, `fluidvoice/doctor.py`,
`fluidvoice/gtkui/settings_window.py`, tests.

1. Engine per decision 2 (unit-drive tests in
   `tests/test_preview_segmented.py::TestSendCountdown`: arms on
   phrase+quiet with committed speech; no arm without the phrase; no arm
   on all-silence; cancels on resumed speech and re-arms; emission
   suppressed while armed; VAD auto-stop still fires when the phrase is
   absent; the "literal send it" escape does not arm).
2. Config per decision 4 (tests: default, 0 accepted as off, range
   rejects >5/<0.3-nonzero, socket-settable, save persists).
3. Daemon per decision 3 (tests with a stub recorder + lock: countdown
   stop calls `_stop_recording_locked` when recording; resume cancels;
   stale-timer identity no-op; pill notice + restore via tracked
   display).
4. Doctor line + Settings spin row (tests: doctor string; GTK group
   round-trips the value).

Gate: full suite green.

## Phase 2 — docs + live smoke

README/STATUS/UPSTREAM-TRACKING row updates. Live smoke on the Xvfb-isolated
instance (e2e recipe): `spoken_send_enabled`, phrase set to the fixture
WAV's trailing words ("the old portrait") so REAL audio/decode/VAD drive
the arm; expect one socket `toggle` to START, then the take stops BY
ITSELF at ~quiet+countdown (no second toggle), log lines `spoken-send
quiet countdown armed/elapsed`, the phrase stripped, text typed, Enter
pressed (gedit newline), history row present. Speaking-cancel path is
unit-proven (no live mic improvisation possible).

## Live-smoke findings (2026-09-08, folded into the implementation)

Two races the first live run exposed, both fixed + regression-tested:
1. **Tick-lag race**: the arm can land up to one preview interval (1.2 s)
   after quiet starts, so a 0.5+1.2 s ladder loses to the plain 2.0 s VAD
   auto-stop. Fix: while the countdown is armed the engine SUPPRESSES the
   plain VAD auto-stop (the countdown is the take's finisher; VAD stays
   for no-phrase takes and as a pre-arm backstop).
2. **Tail-decode degradation**: once speech stops, the sliding tail
   window decodes mostly-silence audio and DROPS the final words - the
   phrase vanished from the rolling text exactly when the arm window
   opened. Fix: the engine stamps `_phrase_seen_s` while the rolling text
   ends with the phrase and clears the stamp on any active-speech tick
   (quiet < 0.5 s); arming accepts (quiet >= 0.5 s) + live stamp. A
   phrase followed by more speech never arms.

## Out of scope

Per-second pill digit animation (static notice in v1), countdown for
command/rewrite takes, non-English VAD models, changing VAD auto-stop
semantics.
