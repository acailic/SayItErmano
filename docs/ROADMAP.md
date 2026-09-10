# Roadmap — future work

Everything on this page is **not built yet**. What already shipped lives
in [STATUS.md](STATUS.md); the reasoning behind locked decisions lives
in [adr/](adr/) (index: [adr/README.md](adr/README.md)); the evidence
base is [research/](research/), especially the
[reliability-first improvement program](research/2026-09-10-reliability-first-improvement-program.md)
(the "plan" below), whose P2/P3 sections are the current prioritized
backlog.

Terminology follows the [glossary](glossary.md). Request briefs carry a
`STATUS:` header (`just validate-requests` enforces it).

## In flight — plan P1 (v0.9: architecture and measurement)

The 2026-09-10 P1 wave; details in the plan §P1:

- **Speech-backend seam** — `SpeechBackend` / capabilities / typed
  `Transcript` across all five adapters, one shared contract suite.
- **Daemon ownership decomposition** — SpeechEngineManager,
  CaptureCoordinator, CommandCoordinator, `RuntimeTasks`
  (the ownership invariant is already decided: [ADR-0003](adr/ADR-0003-runtime-task-ownership.md)).
- **Configuration registry** — every key derived from one
  `SettingSpec`; widgets and the template stop duplicating policy.
- **Local evaluation harness** — WER/CER/hotword-recall/latency/guard
  metrics on a committed fixture manifest; representative-model eval
  before speech-pipeline releases.

## Next — plan P2 (v0.10: contextual dictation)

- `ContextProvider` seam: X11 and AT-SPI adapters returning app
  identity, accessible role, selection and bounded preceding text.
- Per-app behavior profiles (prompt profile, insertion mode,
  formatting mode, spoken-send policy) with legacy-key migration.
- Context-aware sentence capitalization/spacing, GAAV continuous
  dictation, terminal safety, Wayland app hints — always preserving
  behavior when accessibility data is missing.
- **Wayland live smoke matrices** before declaring Wayland parity —
  blocked on a real Wayland session on the dev machine (noted 2026-09-08:
  no compositor installed, GNOME runs X11 there; unit coverage is
  complete in `tests/test_wayland_capabilities.py`). Per compositor,
  GNOME-Wayland first, then sway:
  1. Baseline, no tools installed: daemon starts foreground AND under
     the systemd user unit; tray/socket/`status` alive; a dictation
     transcribes and lands in history; the "no insertion tool"
     notification appears.
  2. sway + wtype: typed insertion into a terminal and an editor;
     spoken-send Enter; paste-last; paste mode via wl-clipboard — verify
     the pre-paste clipboard is restored after both paths; leading-dash
     text takes the paste path.
  3. GNOME + ydotool (ydotoold running, uinput perms): same set; note
     whether key-duration tuning is needed.
  4. Overlay: recording shows the notification preview; no X11-pill
     attempt noise in the log.
  5. Doctor on the live session: matrix + per-tool found/missing
     correct; exit 0 with insertion resolved, non-zero without.
  6. Settings → Wayland: renders, Copy yields a working script; bind it
     in the DE; toggle dictation via the shortcut.
  7. evdev push-to-talk (if input-group access): hold-to-talk works;
     note the device-name match.
  8. X11 regression, same build: full manual pass (hotkey grab, pill
     preview, verified paste, spoken-send, rewrite) — zero deltas.

## Capability backlog — plan P3

- **Chunked file transcription** (next product priority): convert once,
  ten-minute chunks with overlap, reconciled timestamps, current output
  shape — removes the 25 MB warning path.
- **Diarization** — only if an offline adapter passes the
  accuracy/redistribution/memory benchmark; optional `speaker` fields
  without changing plain-text output.
- **True streaming** — only after a real Parakeet/Nemotron streaming
  adapter exists; the segmented preview is the foundation, not a
  hypothetical seam.
- **n-best correction picker** — blocked until ≥2 backends expose
  genuine alternatives.
- **Parakeet fp16/fp32 model variants** — benchmark through the
  evaluation harness before adding catalog choices.
- **Platform polish** — system/light overlay theme, wlroots
  layer-shell preview pill, native Nix flake, clean-chroot AUR
  verification.

## Small standing items (pre-program threads)

- AT-SPI caret-context smart typing / preceding-text capture (also the
  GAAV-continuous-formatting prerequisite, plan P2).
- AT-SPI insertion fallback (grouped with the above).
- Settings drag-to-reorder rows for the mic-priority list (up/down
  buttons ship today).

## Non-goals (decided — do not re-add as work)

- **Local HTTP/TCP API** — [ADR-0001](adr/ADR-0001-no-tcp.md): the
  user-owned Unix control socket plus the stdio MCP bridge is the only
  external interface; the daemon never opens a network listener.
- Bundling a closed-source "Fluid Intelligence" equivalent — use any
  local OpenAI-compatible server (Ollama/LM Studio/llama.cpp) instead.
- Cohere Transcribe (CoreML-only upstream artifacts; no Linux runtime).
- macOS support (upstream owns that).
- Telemetry (upstream ships opt-in analytics; we ship none,
  deliberately).
