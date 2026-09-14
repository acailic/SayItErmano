# Changelog

Condensed release history for SayItErmano — the unofficial community
Linux port of [FluidVoice](https://github.com/altic-dev/FluidVoice) (macOS).

Newest release first. Each entry keeps the release's headline and a
trimmed summary of its highlights — the full, detailed notes live on
[GitHub Releases](https://github.com/acailic/SayItErmano/releases); use
the "full notes" link on each entry. Dates are release publish dates.
Releases begin at v0.4.0 (the rebrand to SayItErmano); earlier v0.1–v0.3
tags predate it and were deleted from the repository, so their notes are
not reproduced here.

## [Unreleased]

- **Fixed: Stats activity chart never drew on Ubuntu 24.04** — the
  GTK 4.14 stack has no `GtkWidget.lookup_color` and pycairo has no
  `Context.rounded_rectangle`; the chart's draw callback raised
  AttributeError before the first bar (silently, page shown). The
  accent lookup is guarded with the existing fallback color and bars
  use an arc-based rounded path. Found by the new per-section display
  tests (org plan 5.5).

## [v0.8.2] — reliability sweep, verified pasting, deb contract (2026-09-14)

**Reliability sweep + focused-field context + verified pasting** — the
P0–P2 program, the quality plan's code halves, and six live-reproduced
defect fixes from the first night desktop matrix.

- **IPC hardening (P0 audit sweep)** — the control socket gets a bounded
  accept queue and per-request read deadlines (transient `accept()` errors
  no longer kill the accept thread); the MCP bridge survives daemon
  timeouts/restarts, answers JSON-RPC 2.0 batch arrays per member, refuses
  NaN/Infinity ids at the parser, and bounds inbound stdio lines at 1 MiB;
  history appends survive invalid UTF-8 blobs and U+2028/U+2029/U+0085
  row rewrites; shutdown mutations are serialized and the control socket
  is never blindly unlinked.
- **Daemon decomposition (P1.2)** — `CaptureCoordinator`,
  `CommandCoordinator`, `SpeechEngineManager` and `RuntimeTasks` extract
  the take lifecycle, command conversations, engine lifecycle and named
  timers from the 1.4k-line Daemon; behavior-preserving, pinned by the
  failure-sequence tests (Q6).
- **Focused-field context (P2) + per-app profiles** — an insertion-time
  AT-SPI/X11 context seam reads the focused field (role, selection,
  bounded preceding text) so continuation capitalization/spacing, search-box
  handling and per-app prompt/behavior profiles work without a second
  focus lookup; context is off by default pending the Wayland matrix.
  **Busy-desktop fixes (F-31..F-33)**: the AT-SPI walk no longer truncates
  26+-app desktops, no longer attaches to a stale ACTIVE-flagged Electron
  window, and text reads work on GIR-only installs (verified live on
  GNOME X11).
- **Verified pasting (F-34/F-35)** — paste mode now verifies against the
  target actually reading the selection's TEXT CONTENT and against the
  focused field's payload (AT-SPI probe): duplicated transcripts in
  terminals, silently lost dictations in Firefox/Discord and
  stale-clipboard inserts in Chromium — all reproduced live on the night
  matrix — are fixed; ownership is held until verification concludes so
  the clipboard restore cannot race the app's read. Insertion notices
  reach the daemon log even with notifications off (F-37).
- **Chunked file transcription (P3)** — `transcribe` converts once and
  splits long inputs into ten-minute overlapping chunks reconciled into
  one transcript (ceiling 6 h); no more size-refusal.
- **Hallucination guard + preview confidence gating** — when a pinned
  language (or a broken mic feed) makes Whisper return confident fluent
  garbage, the take is re-decoded once with auto language detection
  (kept only when confidently better), pure repetition loops are never
  typed, and the live preview suppresses loop text and self-silences
  sustainedly-low-confidence takes to an honest ellipsis; a mic streaming
  digital silence gets a "no audio from the mic" notice. Dead-capture
  hallucinated text is suppressed and the doctor gained a mic probe.
- **First-use funnel** — visible model download with a guided first
  insertion; insertion failures are honest and actionable (machine-
  readable kinds, install hints, automatic clipboard preservation,
  "saved in History" as the floor).
- **Eval tooling (Q8 groundwork)** — recording manifest/consent/
  deterministic-split corpus tooling, measurement adapters (subgroups,
  omissions, hallucination, punctuation, language, guard scores) and a
  TTS decode baseline (real-model WER/latency/hallucination via piper).
- **Quality program (code halves)** — canonical test tiers with three
  green CI lanes (unit, process, gtk-x11 on Xvfb), a globally enforced
  leak gate, branch-coverage baseline (79.9% line / 78.9% branch),
  privacy/command-safety contracts, ruff correctness rules + focused
  mypy, and release provenance binding publication to the tested
  package and its exact source tree.
- **Repository organization** — the vendored SSSF agent factory is
  self-contained under `tools/factory/`, historical ADW plans archived
  under `docs/plans/archive/`, and the documentation truth pass fixed
  the drifted claims (test counts, paths, feature states).

Full notes: [v0.8.2](https://github.com/acailic/SayItErmano/releases/tag/v0.8.2).

Packaging and documentation correctness (plan P0.6).

- **Deb contract** — the .deb is now honestly Ubuntu 24.04 / x86_64 /
  Python 3.12 only: `Depends` pins `python3 (>= 3.12), python3 (<< 3.13)`
  instead of claiming 3.11+, and `packaging/build-deb.sh` refuses to build
  outside Linux/CPython 3.12/amd64 (its interpreter becomes the deb's
  runtime). pipx remains the any-distro Python 3.11+ route.
- **Pinned, reproducible deb builds** — release artifacts come from the
  pinned `ubuntu:24.04` container (`packaging/deb/Dockerfile`, digest
  pinning documented) using a committed, reviewed dependency lock
  (`packaging/deb/constraints.txt`, regenerable with
  `packaging/deb/update-constraints.sh`, wheel-hash capable) instead of a
  hidden `pip freeze` at build time.
- **Native AUR recipe** — `packaging/aur/` now builds the app from source
  against Arch's current Python (PEP 517 `python -m build` + `installer`,
  speech via AUR `python-faster-whisper`), replacing the unpublished
  `sayit-ermano-bin` deb-repackaging recipe.
- **README fixes** — the duplicate `[general]` settings table is merged
  (one `[general]` block) and the `hotwords` example moved to `[model]`
  where the key actually lives; install docs now lead with the Ubuntu
  24.04 deb and the pipx cross-distro route.
- **Roadmap** — the stale local-HTTP/TCP roadmap item is removed; the
  no-network-listener decision (Unix-socket-only) is canonical in
  Non-goals.

## [v0.8.1] — macOS-parity settings (2026-09-09)

The parity-with-upstream release.

- **Settings sidebar** — groups into **Settings / More** caption sections
  with the macOS page order (General, Dictation, Models, AI, History;
  Wayland + About under More); the AI page title shortens to "AI".
- **Page regrouping** — General splits into General / Notifications /
  Sounds; the language cycle + whitelist move to a "Languages" group on
  Dictation, and Command mode moves there too (macOS grouping).
- **Prompt profiles as radio rows** — per-profile rows with radio
  selection, per-row Rename…/Delete menus, Add… name dialog, and "Save
  to profile" beside the editor.
- **Models** — the active model row carries a radio-style indicator
  (all three catalogs).
- **History window** — **Export as Text** (offline plain-text export)
  and a **Pause saving / Resume saving** toggle (`history.save`, live).

Full notes: [v0.8.1](https://github.com/acailic/SayItErmano/releases/tag/v0.8.1).

## [v0.8.0] — MCP server, vocabulary boosting, polished app (2026-09-08)

The integration + polish release.

- **MCP server** (`sayit-ermano mcp`, upstream #927) — Model Context
  Protocol bridge: drive dictation status/history/config from Claude or
  any MCP client.
- **Vocabulary boosting** (`model.hotwords`, upstream #916) — bias the
  decoder toward your names and jargon (faster-whisper hotwords /
  whisper initial-prompt).
- **Guards** — prompt-leak detection (upstream #910: polish output that
  echoes the system prompt is never typed), mid-take stall watchdog
  (#852), AI over-correction guard (polish must not rewrite correct
  words; order: refusal → leak → over-correction).
- **Command-mode security** — two upstream bug classes fixed
  (`find -delete`/`-exec` bypassing the destructive gate #861, descendant
  process hang #930) + SECURITY.md.
- **Preview** — provisional-tail rendering keeps the live preview
  flicker-stable between segment commits.
- **Native app** — settings navigate by sidebar (one section at a time,
  collapsing to push-navigation on narrow windows); history/onboarding/
  settings presentation pass (caption meta lines, pill tags, friendly app
  names, theme-accent stats chart, checklist onboarding, reactive save
  bar); bundled icons self-register on every construction path; the
  settings codebase split per-page (`gtkui/settings_pages/`).
- **Distribution** — deb slimmed 77 → 65 MB (xz -9e + pruned venv); the
  one-shot installer no longer re-creates autostart when the systemd
  unit exists; AUR publish pipeline (recipe + one-command script).

Full notes: [v0.8.0](https://github.com/acailic/SayItErmano/releases/tag/v0.8.0).

## [v0.7.0] — language switching, remote STT, guardrails (2026-09-08)

The languages + endpoints + guardrails release.

- **Runtime language cycle + wrong-language guard** — a hotkey steps
  `general.language_cycle` as a runtime override; auto-detections outside
  the whitelist re-decode once.
- **Remote OpenAI-compatible STT backend** — `model.remote_url` posts the
  recording to any `/v1/audio/transcriptions` server; off unless
  configured.
- **AI refusal guardrail** — refusal-looking polish/rewrite replies are
  never typed; the raw transcript is used instead.
- **Spoken-send quiet countdown** — say the send phrase and go quiet; the
  dictation finishes itself and presses Enter.
- **Scriptable unix-socket API** — `transcribe` and `history` commands
  drive the daemon's warm model; no TCP by design.
- Housekeeping — screenshots retaken, ledger accuracy pass, update-fixture
  pin.

[Full notes](https://github.com/acailic/SayItErmano/releases/tag/v0.7.0)

## [v0.6.0] — Wayland, streaming preview, command mode v2 (2026-09-06)

The Wayland + streaming release.

- **Wayland session support** — per-capability probe, wtype/ydotool
  insertion, DE-shortcut hotkey assist, optional evdev push-to-talk; X11
  unchanged.
- **Segmented streaming preview** — constant-cost 2 s decode windows on
  every backend, plus trailing-silence VAD auto-stop.
- **Command mode v2** — multi-tool protocol, strong confirm for
  destructive commands, History re-run view.
- **Idle model unload** — release GPU/RAM after idle (`model.idle_unload_s`,
  default off).
- **Hotkeys** — self-healing XGrabKey combos, mouse push-to-talk, tap+hold
  activation, paste-last, per-shortcut prompt profiles.
- **Settings & UI** — prompt profiles, per-model language, spoken
  formatting actions, stats page, pill hover chips.
- **Lock suppression** — while the session is locked or suspended
  (logind LockedHint + Lock/Unlock signals + screensaver fallbacks,
  `general.pause_when_locked`, default on) hotkeys are ignored and an
  active dictation is cancelled.
- **Dictionary auto-learning** — History inline-repair diffs become
  suggest-only dictionary entries (2-occurrence threshold, permanent
  dismiss; nothing enters the dictionary without an explicit Accept) —
  upstream v1.6.3's feature with our D1–D7 divergences (see
  docs/STATUS.md).
- **Reliability & distribution** — terminal-safe and clipboard-safe
  insertion, check-and-assist updater, AUR recipe.

[Full notes](https://github.com/acailic/SayItErmano/releases/tag/v0.6.0)

## [v0.5.0] — Parakeet backend, faster toggles, overlay motion (2026-09-04)

The second-engine release: 23 commits since v0.4.0.

- **NVIDIA Parakeet TDT backend** — `parakeet-tdt-0.6b-v2` locally via
  ONNX Runtime, alongside the faster-whisper backends.
- **Faster hotkeys** — toggle-on ~350 ms → ~100 ms; CUDA warm-up tax
  moved to daemon start.
- **Overlay & history polish** — pill fade-in/done-beat motion with
  reduced-motion support; history confidence bands, date grouping, inline
  repair.
- **Also** — env overrides renamed `FLUIDVOICE_*` → `SAYITERMANO_*`;
  original app icon across all sizes.

[Full notes](https://github.com/acailic/SayItErmano/releases/tag/v0.5.0)

## [v0.4.0] — SayItErmano (2026-09-04)

The rebrand release: same app, own name and face.

- **Rebrand to SayItErmano** — repo, deb package, command, launcher and
  desktop integration renamed, with an original app icon (no FluidVoice
  artwork).
- **Migration from `fluidvoice-linux`** — the new deb replaces the old
  package, moves config/data/cache to `sayit-ermano` paths on first run,
  and retires the legacy daemon.
- **Mic priority fallback** — `recording.mic_priority`: when the chosen
  microphone vanishes, the daemon switches to the first matching
  priority pattern (e.g. Bluetooth before webcam) and notifies; switching
  never happens mid-take.

[Full notes](https://github.com/acailic/SayItErmano/releases/tag/v0.4.0)
