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

[Full notes](https://github.com/acailic/SayItErmano/releases/tag/v0.4.0)
