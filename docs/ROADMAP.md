# Roadmap

Audit-verified 2026-09-01 by a 5-agent comparison against the upstream Swift
sources. Everything below is a known, classified gap — see
[BEHAVIOR-SPEC.md](BEHAVIOR-SPEC.md) for the upstream references.

## v0.2 — daily-driver polish
- [x] Live streaming preview — DONE: raw-PCM recording + X11 overlay
      window (or replaceable notifications); verified end-to-end with
      pixel-level screenshot proof. 2026-09-05 upgrade: SEGMENTED engine
      (spec a3f7c21e) — fixed 2 s windows / 50% hop, one decode per tick
      (constant cost, upstream bug #833 class impossible), committed
      segments stable, tail-dedupe, preview on ALL four backends
      (faster-whisper, whisper-torch, whisper.cpp, parakeet), trailing-
      silence VAD auto-stop (2.0 s default), per-take preview stats line,
      first-word-capture probe contract pinned by test (reviews rider).
- [x] Rewrite/Write mode — DONE: `hotkey.rewrite_key` captures the selection
      (clipboard snapshot + restore), dictates the instruction, runs it
      through the verbatim edit prompts (temperature 0.7, context block,
      follow-up history), types the result over the selection.
- [x] First-PCM timeout (2s) — DONE: live-but-silent mics stop early with a
      notification (probes the raw stream, not the finalized WAV).
- [x] Hold-mode key passthrough — DONE: keys typed during a push-to-talk
      hold reach the focused app as REAL events (the XGrabKey activation is
      released for the hold's duration — X11 gives that activation a full
      keyboard grab — with release detected by auto-repeat-proof query_keymap
      polling, a passive Escape grab keeping cancel-during-hold, and the
      hotkey re-armed after). An XTEST ungrab→inject→re-grab replay design
      was prototyped and rejected by live testing (Xorg 21.1 drops XTEST
      fakes that match the current key state). The upstream interrupt
      semantics deliberately stay out: typed keys keep the dictation running
      instead of ending the trigger (clean-tap state machine remains a
      divergence, see STATUS).
- [x] Escape cancels aborted hold recordings (not stop-and-transcribe).
- [x] Spoken-send — DONE (final-transcript variant): trailing phrase strips
      and presses enter/shift+enter/ctrl+enter after typing; "literal send
      it" escape honored. Immediate-stop countdown UI: still future — the
      energy+ZCR trailing-silence VAD foundation landed with the segmented
      preview engine (2026-09-05); only the countdown UI remains.
- [x] Paste-last-transcription — DONE: `fluidvoice paste-last` + socket
      action + global `hotkey.paste_key` shortcut (upstream parity, 2026-09-05).
- [x] Per-app prompt sets — DONE: `ai.per_app_prompts` rules matched against
      the recording-start app (Settings → AI → Per-app prompts, per-rule
      editor; the frontmost-app hint was already captured).
- [x] User-editable prompt profiles — DONE: named presets of the base
      prompt as a sidecar `prompt-profiles.json` (save/load/rename/delete
      in Settings → AI; loading copies text into the editor, config.toml
      stays the source of truth).
- [x] Always-copy-to-clipboard option; sub-1s zero-padding — DONE.
- [x] GAAV mode (lowercase-first / strip trailing period) — DONE.

## v0.3 — Wayland parity
- [x] Insertion via ydotool/wtype + wlr virtual-keyboard protocol — DONE:
      `fluidvoice/session.py` probes the session (XDG_SESSION_TYPE >
      WAYLAND_DISPLAY > DISPLAY > unknown-as-x11); wayland insertion uses
      `wtype` (auto-skipped on GNOME — no virtual-keyboard protocol) or
      `ydotool` (spec→code table, `insertion.wayland_tool`), with the
      per-capability matrix on `status`/`doctor`/Settings → Wayland.
- [x] Hotkey: document/bind DE shortcuts per compositor (GNOME/KDE/COSMIC);
      optional evdev listener for physical push-to-talk — DONE: the daemon
      writes a bindable `sayit-ermano-toggle` script and prints per-DE
      steps (doctor + Settings → Wayland); `hotkey.wayland_evdev` adds a
      physical hold-to-talk key read from /dev/input (privileged path,
      `pip install 'sayit-ermano[wayland]'`).
- [x] Clipboard via wl-clipboard (wl-copy/wl-paste) with restore — DONE:
      paste mode snapshots via `wl-paste`, writes `wl-copy`, pastes through
      the typing tool, restores with the original mime type. Deliberate
      divergence: paste verification degrades to a fixed settle delay
      (cross-client selection reads are impossible on Wayland) and no
      clipboard-manager hygiene markers (see STATUS).

## v0.4 — model variety
- [ ] Parakeet TDT v2/v3 on GPU via NeMo / ONNX Runtime — upstream's default
      model and the highest-value Linux addition.
- [ ] Parakeet Realtime / Nemotron 3.5 streaming (NeMo/Riva) — the
      segmented preview already streams window-wise on every backend
      (2026-09-05); true streaming engines would tighten first-word latency
      below the ~1 s preview floor.
- [x] whisper.cpp GGUF auto-download — DONE: curated `ggerganov/whisper.cpp`
      ggml catalog in Settings → Models with streaming one-click download
      (progress, atomic rename), name-or-path `model.whispercpp_model`, and a
      doctor resolution report.
- [x] Per-model language selection — DONE: flat `model.languages` dict
      ({model_key: code} across all three catalogs, "auto" forces
      detection, missing key follows general.language), resolved through
      one `backends.effective_language` helper at every call site
      (pipeline, preview, test-dictation, CLI); picker in Settings →
      Models.
- [x] Model manager: list/download/prune in `~/.cache/fluidvoice/models` —
      DONE: faster-whisper one-click switch + GGUF/Parakeet downloads + a
      disk-usage section in Settings → Models (per-model size + total) with
      socket-only deletion (`model-delete` refuses the active model, in-
      flight loads and anything outside the cache root).

## Later
- [x] macOS-parity quick wins (design doc 2026-09-05 "macOS parity and
      beyond", B-track) — DONE 2026-09-05/06: paste-last hotkey
      (hotkey.paste_key, 0d500af); spoken slash/mention grammar (bc1ced3);
      user-extensible spoken formatting actions + Settings editor
      (7b1fc4a + 3411af3, processing.formatting_action_triggers); Stats
      page with streak/time-saved/activity chart (48b32ac); up to 3
      dictation shortcuts with per-shortcut prompt profile
      (hotkey.extra_shortcuts, 3411af3); activation mode "both" (86fad3e).
      Was: ("slash fix", "at sign
      John", "tag John"); stats page (streak, time-saved, 7/30-day chart);
      pill hover chips (prompt/mode/actions) — stretch.
- [ ] Leapfrog menu (same doc, C/D tracks, evidence: reviews sweep) — three
      shipped: idle model-unload / keep-warm policy (`model.idle_unload_s`,
      923001e); fast language switching + whisper language whitelist
      (`hotkey.language_key` + `general.language_cycle`/`language_whitelist`,
      d604db8…052df8a); OpenAI-compatible remote STT endpoint as a backend
      (`model.remote_url`, `fluidvoice/backends/remote_stt.py` — LAN GPU
      boxes, upstream declined the PR). Still open: AI refusal/derail
      guardrail (never type "I'm sorry…"); scriptable unix-socket API
      (upstream loopback-API parity without TCP); AT-SPI caret-context
      smart typing; per-app behavior profiles; diarization; n-best pick
      lists (blocked on backend alternates).
- [x] Command mode (voice → terminal agent) with the upstream tool schema and
      destructive-command confirmation list — DONE (v2): tool schema ported
      into the strict-JSON `tool_calls` protocol with per-arg validation;
      upstream's 28-rule destructive classification ported verbatim +
      `command.destructive_patterns` user additions; every command confirmed,
      destructive ones through a two-press strong confirm; per-app follow-up
      context (last 5 / 300 s / "new session"); History Commands view with
      confirm-gated Re-run.
- [ ] GAAV mode + continuous-dictation formatting (smart caps from the text
      before the caret; needs preceding-text capture via AT-SPI).
- [x] Slash-command/mention literal formatting (`/ fix`, `@ John Smith` in
      Slack/Discord/Teams) + terminal autocomplete spacing — DONE: literal
      squeeze ported from upstream `DictationLiteralFormatting` (runs after
      AI cleanup, `processing.slash_mention_squeeze`; literal-`@` pass is a
      port addition mirroring upstream's spoken-mention name grammar) plus
      one trailing space on typed insertions in `general.terminal_apps`
      (`insertion.terminal_autocomplete_space`) and the spoken-send terminal
      blocklist. Upstream's spoken forms (`slash fix`, `at sign John`) stay
      tracked in UPSTREAM-TRACKING.
- [x] Insertion hardening: paste-verification before clipboard restore,
      transient marks so clipboard managers ignore dictation, per-app paste
      quirks (terminals) — DONE: verify-then-restore paste (selection
      ownership + read observation; unverified pastes fall back to typed
      insertion with a notification), clipboard-manager hygiene markers
      (CopyQ 7.1.0 live-verified; the GNOME-shell-extension residual is
      documented in STATUS.md), terminal `ctrl+shift+v` paste key, both
      config keys + doctor lines. AT-SPI insertion fallback stays later.
- [x] Input-device monitoring / Bluetooth auto-switch — DONE: mic priority
      list (`recording.mic_priority`) + a 3 s pactl source diff poll that
      switches to the first priority match when the configured mic vanishes
      (never mid-take; auto device untouched). The MPRIS media pause half
      shipped earlier. Drag-to-reorder in the settings editor stays a
      polish item (up/down buttons for now).
- [x] Custom-dictionary auto-learning from post-insertion corrections
      — DONE: inline repair (`history.update_text`) stamps the pre-edit
      text as `edited_from` (first edit wins — the ASR-heard audit trail);
      `processing/dict_learn.py` diffs it against the final text
      (token-level difflib) with upstream's shape checks (≤3 words/side,
      ≤40 chars/side, ≥2 letters/side, purely alphabetic tokens, filler-
      free — `AutomaticDictionaryCorrectionTracker.swift:215-241`) and
      suggests a pair only after it appears in ≥2 entries (upstream
      `requiredOccurrences`; counts derive from the history itself, the
      5000-entry cap bounds them). Settings → Dictation gains a passive
      "Suggested words" group (below the hand-curated editor, hidden when
      empty): Accept merges through the validated `set-config` path with
      no duplicate triggers (the merged entry verifiably rewrites the old
      form), Dismiss is permanent. Decisions live in
      `~/.config/sayit-ermano/dictionary-suggestions.json` (dismissed /
      accepted pairs only — no counts, no config key: a passive list that
      records what the user already typed needs no gate;
      `history.save = false` disables the signal at the source).
      `fluidvoice doctor` prints the pending count. Divergences (case-only
      candidates, no overlay, permanent dismiss…) in STATUS.md.
- [x] Audio-history ZIP export; local usage stats — DONE: `fluidvoice
      history --export PATH.zip` (history + retained audio; audio outside the
      audio dir refused, missing skipped), History-window Export… menu item,
      today line in the window header + `fluidvoice status` (local midnight).
- [x] Auto-updater (or packaged releases); onboarding — DONE: check-and-assist
      updater (`fluidvoice/update.py`; daily GitHub check on a daemon thread
      that never blocks startup, one notification per newer release with
      dismiss state in `update-state.json`, `sayit-ermano update` prints the
      copy-paste upgrade command per detected install method, doctor drift
      WARN for deb+user double installs; NO silent self-update).
      Onboarding's one added sentence: the update check + how to disable it.
- [x] Mouse-button push-to-talk + lock suppression — DONE: `recording.
      push_to_talk_button` (e.g. "button8"; 6–255, buttons 1–5 click/scroll
      refused) arms an XGrabButton passive grab per lock-mask combo; the
      press releases the grab activation (ungrab_pointer) so clicks during
      the hold reach the focused window natively, and the release is
      detected from XI2 RawButtonRelease events (core XQueryPointer cannot
      see buttons > 5 — the wire mask is a CARD16 carrying only buttons
      1–5; XI 2.2 is negotiated directly because python-xlib hardcodes
      2.0 and Xorg then withholds release events). Escape cancels
      mid-hold, same as keyboard holds. While the session is locked or
      suspended (`general.pause_when_locked`, default true) the daemon
      ignores hotkey presses, cancels any active dictation, and the tray
      notes `paused (locked)` — logind session Lock/Unlock + LockedHint
      signals (GNOME's path), PrepareForSleep, screensaver-name fallback
      (fluidvoice/lockmon.py). X11 only; Wayland pointer protocols are a
      separate item. Upstream PR #939 parity.
- [x] Settings UI — done as a native GTK 4 + libadwaita app
      (`fluidvoice app`; History/Settings/onboarding windows over the
      control socket; the former web page was retired with it).
- [ ] Local HTTP API (upstream exposes an OpenAI-style server on 127.0.0.1
      with /v1/transcribe, /v1/history, dictionary routes).
- [x] Packaging: AUR, pipx (deb is DONE - packaging/build-deb.sh: launcher entry,
      login autostart, icon, systemd unit, bundled venv) — DONE: pipx verified
      from a locally built wheel (`scripts/verify-pipx.sh`: entry points, data
      files, pipx method detection; PyPI publish stays manual by project rule),
      AUR `sayit-ermano-bin` recipe in packaging/aur/ (instructions only, not
      published by us). nix still open.

## Non-goals
- Bundling a closed-source "Fluid Intelligence" equivalent — use any local
  OpenAI-compatible server (Ollama/LM Studio/llama.cpp) instead.
- Cohere Transcribe (CoreML-only upstream artifacts; no Linux runtime).
- macOS support (upstream owns that).
- Telemetry (upstream has opt-in/out analytics; we ship none, deliberately).
