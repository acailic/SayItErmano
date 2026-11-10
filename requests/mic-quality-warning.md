Proactive mic-quality warnings: while dictating, tell the user the microphone is bad (dead path, clipping/distortion) or they are too far away (speech present but level very low) — today every guard is REACTIVE (dead-mic notice 3157fdf and hallucination guard b73ecd2 fire after a take; preview "silenced" mutes mid-take but never says why at the input level; micmon watches device hotplug only, not levels).

STATUS: QUEUED

Context anchors: audio_utils.AudioStats (peak/rms/max_frame_rms, 20 ms frames) + is_digital_silence already exist; preview.py:116-142 already computes per-frame RMS for VAD; capture.py:436-443 already has a "silenced → notice" precedent; save_audio stays ON (BT-incident directive: saved wavs are the ground truth for tuning thresholds). 2026-09-10 BT incident (b73ecd2 memory): garbage-mic + wedged-PipeWire-silence are REAL recurring failure modes on this machine — this brief is the upstream UX for them.

Scope:

1) Incremental level monitor: during an active recording, accumulate cheap PCM stats per 100 ms hop — mean RMS (dBFS), peak, clip ratio (samples >= ~0.98 FS), DC offset, and speech-present fraction (reuse the preview VAD frame-RMS threshold; when preview is off, compute the same 20 ms frame RMS inline). No new threads: hook the same first-PCM path capture.py already feeds.

2) Three verdicts, decided on a sliding window once enough audio exists (>= ~1.5 s), each warned AT MOST ONCE per dictation via ui.notify (+ the pill subtitle while the overlay is up, mirroring the silenced-notice tone):
   - DEAD: speech fraction ~0 AND level < digital-silence threshold for the whole window (muted source / wedged PipeWire path / wrong device) → "Microphone is silent — check input device".
   - TOO FAR: speech fraction high (user IS talking) but mean speech-RMS below the far-threshold → "Very quiet input — move closer to the mic / raise input volume".
   - CLIPPING/DISTORTED: clip ratio above threshold (or peak pinned at FS on a meaningful fraction of frames) → "Input is clipping — lower the mic volume" (covers BT-garbage style distortion heuristically; hallucination guard stays the backstop).

3) Per-device baseline instead of fixed guesses: persist a small sidecar ({source_name: rolling stats of recent takes}) beside the config (config.py store discipline: atomic 0600, corrupt → {}). TOO FAR fires on level far below THIS device's own recent norm (e.g. < max(fixed_floor, 0.25 x baseline)); with no history yet, fall back to the fixed floor. This survives quiet mics vs hot mics without a settings UI.

4) doctor.py: report the current device's baseline + last-take verdicts (one line), so a bad session is diagnosable after the fact.

5) Config: one [recording] key `mic_warnings = true` (bool, default on) to silence the feature; thresholds are constants, not settings (YAGNI).

Done means: a phased, file-level plan a builder can implement without questions; unit tests with synthetic PCM arrays for all three verdicts (silence, quiet-speech, clipping) + baseline learn/fallback + once-per-dictation suppression + notify-off respect; no network, no display tier beyond the existing notify patterns. Thresholds tuned against saved wavs from the 2026-09-10 incident where applicable.

Out of scope: live input-level meter UI in settings, per-app overrides, automatic device switching decisions (micmon already owns that), AGC/gain changes, Wayland-only paths.
