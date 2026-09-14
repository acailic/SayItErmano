# SayItErmano — Status

Last updated: 2026-09-14 · this file tracks **what works today and the
known limitations** — release history lives in the
[CHANGELOG](../CHANGELOG.md), locked decisions in [adr/](adr/)
([index](adr/README.md)), future work in [ROADMAP.md](ROADMAP.md).
Current automated-test count: run `just test-parallel`.

Companion docs: [glossary.md](glossary.md) (domain terms) ·
[BEHAVIOR-SPEC.md](BEHAVIOR-SPEC.md) (what upstream does, with file:line
evidence) · [COMPARISON.md](COMPARISON.md) (vs. other Linux dictation
tools) · [UPSTREAM-TRACKING.md](UPSTREAM-TRACKING.md) (macOS-vs-Linux
capability matrix) · [guides/](guides/) (user manual) ·
[research/](research/) (the evidence base).

---

## ✅ What works today

Verified on the daily-driver Pop!_OS X11 desktop (live hardware loop,
night matrices) plus the automated tiers (see Verification below).

### Core dictation loop
- **Hotkeys** (X11 global grab): toggle with any keysym (modifier-only
  keys included), hold push-to-talk with native key passthrough, "both"
  tap/hold activation; up to three shortcuts with per-shortcut prompt
  profiles; cancel / rewrite / command keys; **mouse-button push-to-talk**
  (buttons 6–255, clicks pass through during the hold); self-healing
  grabs (a refused combo re-arms and is surfaced in status/tray/doctor).
- **Recording** through PipeWire/PulseAudio, configurable device; watchdogs:
  max duration, first-PCM timeout (muted mic fails fast), mid-take stall
  cancel, opt-in silence gate; digital-silence notice for a dead mic.
- **Backends**: faster-whisper (CUDA, CPU-int8 fallback), whisper-torch,
  whisper.cpp, Parakeet TDT v2/v3 (ONNX), and a remote
  OpenAI-compatible STT endpoint that wins while configured; auto
  selection, background download + hot-swap, per-model language
  overrides, runtime language-cycle hotkey with wrong-language whitelist
  re-decode, idle model unload, vocabulary hotwords.
- **Streaming preview** on every backend (segmented constant-cost
  engine, stable committed text, VAD trailing-silence auto-stop) in a
  Mac-style pill overlay.
- **Insertion**: typed (clipboard-free) or paste — SayItErmano owns the
  CLIPBOARD selection for the paste's duration with clipboard-manager
  hygiene markers, verifies the target actually read the text, then
  restores; terminal quirks (ctrl+shift+v, autocomplete space, no
  spoken-send Enter); fallback ladder ends in clipboard + notice.
- **Modes**: Rewrite (selection → edit instruction → typed result),
  spoken-send with quiet countdown, GAAV casual mode, Command mode
  (voice-driven terminal agent under a strict-JSON tool protocol, every
  call confirmed, two-press strong confirm for destructive patterns),
  MCP server (`sayit-ermano mcp`) for agent access.
- **Guards**: hallucination/repeat suppression (incl. dead-capture),
  AI refusal guard, prompt-leak guard, over-correction guard — model
  junk is never typed.
- **Around the take**: mic priority fallback with input-device
  monitoring (switch never mid-take), lock/suspend suppression, tray +
  desktop notifications + SFX, transactional history (5000-entry cap,
  retained audio with rollback), `paste-last`, optional
  copy-to-clipboard, check-and-assist updater, systemd user unit.

### Text processing (upstream-faithful, audit-verified)
Filler removal; custom dictionary (case-insensitive, longest-first);
**dictionary auto-learning** — History inline-repair diffs become
suggest-only entries (nothing auto-added); spoken punctuation matching
upstream's live rule table (all 108 aliases, toggling double quote,
longest-first); slash/mention literal squeeze; terminal send-safety.

### AI polish (prompts byte-identical to upstream)
All five upstream prompts; `${transcript}` placeholder; faithful request
params (reasoning models, Responses API, think-tag stripping); works
with any OpenAI-compatible endpoint (live-tested against Ollama); AI
failure falls back to the raw transcript; custom base prompt + named
presets; per-app prompt rules.

### Native GTK app (`sayit-ermano app` / `settings`)
GTK 4 + libadwaita, macOS-parity sidebar; Settings covers every
validated key (General/Models/AI/Dictation/History/Notifications/
Sounds/Wayland/About) with dirty tracking and hot-apply; History window
with live status header, search, inline audio replay, confidence dots,
inline repair (stamps `edited_from`), Insert-at-cursor, date grouping,
Export as Text/ZIP, Pause saving, Stats page; whisper.cpp GGUF and
Parakeet model managers (atomic downloads, one-click Use); per-model
language; cached-model disk usage with socket-only delete.

### Wayland session support
Strictly additive on a session probe; per-capability matrix in
`doctor`/status/Settings; insertion via wtype or ydotool with a
degradation ladder (tool+typed → tool+wl-paste → wl-copy + notice);
hotkeys via a bindable toggle script + per-DE bind instructions;
optional evdev push-to-talk (privileged path); notification-preview
fallback; rewrite selection capture and clipboard routes adapted.
Divergences below; live smoke matrices still pending (limitations).

### Chunked file transcription
Inputs over ten minutes: convert once, 10-minute overlapping slices,
conservative boundary dedup, one merged transcript with global
timestamps; 6-hour decoded-audio bound; CLI/socket/MCP response shapes
frozen (golden tests).

### IPC and infrastructure
Unix-socket control server (bounded, 8 workers, structured errors,
0600); MCP bridge (valid JSON-RPC 2.0, version negotiation); CLI
(`daemon/toggle/cancel/status/paste-last/transcribe/history/config/
settings/doctor/mcp/update`); no TCP listener ever (ADR-0001);
packaging: Ubuntu 24.04 deb (pinned container build, ADR-0004), pipx
(Python 3.11+ any distro), native AUR recipe.

---

## ⚠️ Known limitations

- **X11 paste residuals**: a GNOME shell selection proxy (e.g. the
  clipboard-indicator extension) reads flashed text at ownership change
  regardless of hygiene markers — a shell-extension history capture
  remains possible (no X11-side fix short of not pasting). CopyQ
  suppression is verified live; Klipper/GPaste are untested.
- **Verify-timeout edge**: an app whose window read the clipboard during
  the quiesce and pastes without a fresh selection read falls back to
  typed insertion (the documented trade-off of the read-based verify).
- **ghostty** `ctrl+shift+v` is expected but untested locally.
- **Wayland**: paste verification degrades to a fixed settle (no
  cross-client read observation); clipboard managers see flashed text
  (no hygiene markers); no app hints, so `terminal_apps` quirks are
  inert; the pill overlay is impossible on GNOME-Wayland (notification
  preview instead).
- **Live Wayland/X11 smoke matrices have never run** (no Wayland
  session on the dev box) — Wayland claims rest on unit coverage plus
  the tool matrix, and `context.enabled` stays **off by default**
  until they do.
- **Remote STT backend**: no live preview/VAD for remote takes in v1.
- **Refusal/prompt-leak guards**: English patterns only in v1.
- **No real-speech corpus yet** — speech-quality evidence is
  unit/contract tests plus a clearly-labeled synthetic TTS baseline.
- **Deb**: Ubuntu 24.04 / x86_64 / Python 3.12 only (ADR-0004); pipx or
  AUR elsewhere.
- **Lock detection**: signal-driven on logind DEs; a pathological DE
  could lag to the 5 s reconcile poll. Suspend counts as locked.
- **Mouse PTT**: a pointer vanishing mid-hold ends the take via the
  max-duration watchdog, not instantly.
- **History**: 5000-entry cap and GB-bounded retained audio (by
  design).

---

## Intentional divergences (documented decisions)

| Divergence | Why |
|---|---|
| MCP server bridge (`sayit-ermano mcp`): stdio JSON-RPC tools for MCP agents, forwarded over the unix control socket | upstream users ask for agent access (#927, loopback API #715); our no-TCP scope beats both — filesystem-scoped trust boundary |
| AI refusal guardrail (`ai.refusal_guard`): a refusal-shaped reply is never typed — raw transcript instead, with a notification | upstream has no guard; its closed model once pasted "I'm sorry, I can't assist with that." into a document. English patterns only in v1; a verbatim refusal-looking dictation falls back to raw text (never data loss) |
| Remote OpenAI-compatible STT backend (`model.remote_url`): while set, POSTs each take to `<url>/v1/audio/transcriptions`; wins over every local backend | upstream declined the community PR for a native protocol — LAN GPU boxes are a real ask; local-first: empty URL = backend never constructs, no network (test-enforced) |
| 429/5xx HTTP responses retried (upstream never retries HTTP errors) | resilience for rate-limited endpoints |
| Thinking-only model answers fall back to raw transcript (upstream types raw content) | never type `<think>` junk |
| AI timeout 120 s (upstream: 30 s streaming / 120 s non-streaming) | we are non-streaming; big local models are slow |
| `max_seconds` recording cap (upstream: none) | runaway-recording safety; configurable |
| Hold mode: typed keys pass through natively but do NOT end the dictation (upstream: other keys interrupt the trigger) | deliberate "keep typing while holding"; X11 has no per-event passthrough under an active grab — releasing the grab entirely is the only clean mechanism (live-verified) |
| No telemetry at all (upstream has opt-in analytics) | privacy-first choice |
| D1: case-only corrections are dictionary candidates (upstream rejects them, `AutomaticDictionaryCorrectionTracker.swift:111`/`216-227`) | the documented use of the dictionary is exactly `["miro board"] → "Miro board"` and triggers match case-insensitively; threshold-2 + suggest-only + permanent dismiss bound the risk |
| D2: learning signal = History inline repair (`edited_from`), not a live accessibility observer (`TypingService.swift:525-532`) | no AT-SPI text-field observation on Linux yet (roadmap); v1 scope: no re-dictation, no external editors |
| D3: suggestions surface as a persistent Settings list, not a typing-time 5 s overlay (`AutomaticDictionaryCorrectionOverlay.swift`) | no D2 signal at typing time; a passive list needs no interruption — none of upstream's cooldowns/session-ignores |
| D4: dismissal is permanent, not upstream's 7-day cooldown with max-3 dismissals (Tracker:236-237) | the brief mandates "never resuggested"; simpler and stricter |
| D5: occurrence counts derive from the history itself, no 7-day window (Tracker:234-235), no state file | the 5000-entry cap bounds them; the store records decisions only |
| D6: token-level difflib over the whole edit, not upstream's anchored in-range character diff | our edit boundary is one whole History entry, so anchoring is trivially satisfied |
| D7: no config toggle (upstream `automaticDictionaryLearningEnabled`) | upstream's toggle gates an interruptive overlay; a passive list needs no gate — `history.save = false` disables the signal at the source |
| D1 corollary: suggest-only, unlike the Windows port which silently auto-adds | silent dictionary growth degrades trust — nothing enters without an explicit Accept |
| C1: EVERY command needs the hotkey confirm; upstream auto-executes non-destructive ones (`CommandModeService.swift:439-456`) | voice → shell is the highest-blast-radius path; one uniform gate beats two mental models |
| C2: destructive commands need a TWO-press strong confirm (armed state, amber pill, watchdog) | port addition: a mis-heard `rm -rf` under a single stray keypress is the worst failure mode command mode has |
| C3: follow-up context is in-memory, per focused app, last-5 within 300 s, cleared by "new session"; upstream persists a 30-chat global store and replays it (`ChatHistoryStore.swift:93-110`, `CommandModeService.swift:790+`) | voice runs stay cheap to reason about; a daemon restart starts cold; nothing about shell usage is persisted beyond existing history rows |
| C4: the tool schema travels in our strict-JSON text protocol; upstream sends a native OpenAI `tools` array (`CommandModeService.swift:868`, `LLMClient.swift:329-333`) | keeps the single transport every other mode uses — works with any chat-completions endpoint incl. local models without tool-calling; the schema SHAPE is ported, the wire format is not |
| C5: a reply may propose a SET of tool calls, each individually confirmed; upstream consumes `toolCalls.first` alone (`CommandModeService.swift:953-954`) and silently drops undecodable calls (`LLMClient.swift:847-865`) — we reject them loudly | one voice run = one confirmed set (no autonomous chaining); a half-parsed proposal must never look like success |
| C6: an empty/non-string `command` argument is a parse error; upstream tolerates it (`?? ""` would run `zsh -c ""`, `CommandModeService.swift:955`) | executing an empty shell is always a protocol failure |
| Mouse PTT release detection is XI2 raw-event-driven, not button-state polling | core XQueryPointer's mask only carries buttons 1–5 — thumb buttons 8/9 are invisible to it; XI2 RawButtonRelease is grab-independent and non-consuming (needs XI ≥ 2.1; setup refuses to start below it rather than never fire) |
| Mouse PTT buttons 1–5 refused outright | a primary/wheel button PTT would swallow every click/scroll while armed — breaking the desktop |
| Suspend is treated as locked (PrepareForSleep flips the same gate) | a suspended screen with a live dictation is exactly the bug `pause_when_locked` fixes |
| GNOME lock detection goes through logind LockedHint, not the screensaver D-Bus name | on GNOME neither screensaver name is ever owned — ActiveChanged alone would miss every lock; screensaver sources remain as fallbacks where a DE owns them |
| Lock latency = signal + ≤ 5 s reconcile | instant for logind-locking DEs; a pathological DE could lag to the poll — the recording-under-locked-screen bug is still fixed |
| A pointer vanishing mid-hold ends the take via max_seconds, not instantly | no raw release ever fires; the listener stays healthy and re-arms |

---

## Verification

- **Automated**: three CI lanes (unit, process, gtk-x11 on Xvfb) + the
  local `just gate` (lint, brief validation, docs link + config-doc
  freshness, offline suite with warnings-as-errors). Tier model:
  [dev/testing.md](dev/testing.md); runs and coverage:
  [quality/evidence-index.md](quality/evidence-index.md).
- **Live hardware**: mic→GPU transcription loop, hotkey grabs, conflict
  recovery, lock cycles, paste-verify matrices — recorded in
  [research/](research/) run notes; night desktop matrices append
  ongoing.
- **Upstream fidelity**: 5-agent audit (prompts/AI, punctuation rules,
  daemon pipeline, models, security) — [BEHAVIOR-SPEC.md](BEHAVIOR-SPEC.md)
  carries the file:line evidence.
- **Briefs**: every feature's request brief and its STATUS line live in
  [requests/](../requests/) (`just validate-requests`).
