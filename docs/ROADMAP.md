# Roadmap — future work

Everything on this page is **not built yet**. What already shipped lives
in [STATUS.md](STATUS.md); the reasoning behind locked decisions lives
in [adr/](adr/) (index: [adr/README.md](adr/README.md)); the evidence
base is [research/](research/), especially
[reliability-first improvement program](research/2026-09-10-reliability-first-improvement-program.md)
(the "plan" below). **Reconciled 2026-09-11 (phase 0, plan item 1):**
plan P1 and the P2 code work are complete and in tree (unreleased,
shaping v0.8.2+); the current prioritized backlog is the **live Wayland
+ context smoke matrices** and the **plan P3 capability backlog** below.

Terminology follows the [glossary](glossary.md). Request briefs carry a
`STATUS:` header (`just validate-requests` enforces it).

## Shipped in tree — plan P1 (v0.9: architecture and measurement)

The 2026-09-10 P1 wave landed 2026-09-10 and is in tree, unreleased
(evidence: merges `49452fb` eval, `4230b38` config, `5d6d6b3` seam,
`de2e045`+`9cec4c9` RuntimeTasks, `309896e` EngineManager/Command/
Capture coordinators; STATUS.md "P1 wave 1" header). Items kept here
only as history — do not re-implement:

- **Speech-backend seam** — `SpeechBackend` / capabilities / typed
  `Transcript` across all five adapters, one shared contract suite
  (`fluidvoice/backends/base.py`, `tests/test_backend_contract.py`).
- **Daemon ownership decomposition** — SpeechEngineManager,
  CaptureCoordinator, CommandCoordinator, `RuntimeTasks`
  ([ADR-0003](adr/ADR-0003-runtime-task-ownership.md)).
- **Configuration registry** — every key derived from one
  `SettingSpec` (`fluidvoice/config.py`, `tests/test_config_registry.py`).
- **Local evaluation harness** — `fluidvoice/evalharness/` +
  [docs/eval](eval/README.md) (synthetic CC0 corpus only; representative
  real-speech corpus is open work — see the P3 backlog and the
  product-excellence plan).

## Next — plan P2 live validation (gates both Wayland parity and the context default)

The P2 code shipped 2026-09-10 in tree, **prototype OFF by default**
(`context.enabled = false`), merged `b1e9011` (seam `e7fa15c`, profiles
`fdbcf73`, take-path consumers `e47f763`; STATUS.md "Contextual
dictation seam"):

- ~~`ContextProvider` seam: X11 and AT-SPI adapters~~ — shipped.
- ~~Per-app behavior profiles with legacy-key migration~~ — shipped.
- ~~Context-aware sentence capitalization/spacing, GAAV, terminal
  safety, Wayland app hints~~ — shipped behind `context.enabled`.

What remains open, REQUIRED-BEFORE-PARITY, is the live matrices —
[dev/wayland-smoke-matrix.md](dev/wayland-smoke-matrix.md) has the full
case list ("Runs: none yet"). Blocked on a real Wayland session on the
dev machine (noted 2026-09-08: no compositor installed, GNOME runs X11
there; unit coverage is complete in `tests/test_wayland_capabilities.py`).
Per compositor, GNOME-Wayland first, then sway:
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

- AT-SPI insertion fallback (the context seam's AT-SPI adapter now
  supplies identity/role/preceding text — shipped with P2 above — but
  *insertion* itself still has no AT-SPI route).
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
