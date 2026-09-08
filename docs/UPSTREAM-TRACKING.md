# Upstream Tracking — macOS FluidVoice vs. the Linux port

This is the canonical ledger for **what macOS FluidVoice can do, whether the
Linux port has it, and what changed upstream lately**. The port's own internal
status lives in [STATUS.md](STATUS.md); this doc faces *upstream*.

- **[Capability matrix](#capability-matrix--what-macos-can-do-vs-linux)** — the
  standing "possible on macOS vs. available on Linux" view.
- **[Upstream changelog](#upstream-changelog-newest-first)** — every upstream
  release (and the unreleased tip), each change tagged with its Linux status.
- **[Tracking loop](#tracking-loop)** — how to refresh this doc; one command.

## Status legend

| Mark | Meaning |
|---|---|
| ✅ | Ported — works on Linux today |
| 🚧 | Planned — on [ROADMAP.md](ROADMAP.md) with a target version |
| ⏳ | Not ported, not on the roadmap yet (candidate — decide: adopt or won't-do) |
| ➖ | N/A on Linux — platform-bound; a substitute is noted where one exists |
| ❓ | Needs triage — looks relevant, verify against our behavior before deciding |

## Baseline

| | |
|---|---|
| Upstream commit | `b395a7a` (2026-09-02) — `b395a7af0242b6869867abdd61e245a5a80ec218` |
| Latest upstream release | `v1.6.9` (2026-08-18) |
| Port audited against it | 2026-09-02, 5-agent audit + the v0.1.x feature work through `e3bfa37` (295 tests) |
| Machine-readable pin | [upstream-baseline.txt](upstream-baseline.txt) (read by `scripts/upstream-diff.sh`) |

The baseline says: everything upstream through `b60a302` was already considered
by the 2026-09-02 audit and feature work; the statuses below reflect that.
Anything upstream lands *after* `b395a7a` is "new and not yet available on
Linux" until triaged here.

## Tracking loop

1. Run `./scripts/upstream-diff.sh` — fetches upstream and prints new commits,
   new tags, and watched Swift sources that changed since the baseline.
2. Triage: give every user-facing change a row in the
   [changelog](#upstream-changelog-newest-first) below with a status mark
   (move 🚧 items onto ROADMAP.md if adopted).
3. Bump the baseline: update the table above **and**
   `docs/upstream-baseline.txt` (`sha`, `short`, `date`, `tag`, `checked`).
4. Commit together: `docs(tracking): upstream <old-short>..<new-short>`.

A quick cadence (e.g. after each upstream release or once a week) keeps "what's
new that Linux doesn't have" a one-command answer.

---

## Capability matrix — what macOS can do vs. Linux

| macOS capability | Linux port | Notes / substitute |
|---|---|---|
| Global push hotkey (Right ⌥) | ✅ | X11: Right Ctrl (any keysym) via XGrabKey. Wayland (v0.3): DE custom shortcut → the generated `sayit-ermano-toggle` script (Settings → Wayland assists; doctor prints per-DE steps) + optional evdev push-to-talk (`hotkey.wayland_evdev`) |
| Whisper models (tiny→large) | ✅ | faster-whisper (CUDA/int8), whisper.cpp, torch backends; Parakeet TDT via ONNX Runtime (explicit `backend = "parakeet"`) |
| Parakeet TDT v2/v3 (upstream default) | ✅ | offline v2/v3 via the community ONNX exports (sherpa-onnx) + our own numpy log-mel + greedy TDT decode over ONNX Runtime — no NeMo dependency; explicit `backend = "parakeet"` selection ("auto" still prefers the whisper family — deliberate divergence, see STATUS.md) |
| Parakeet Flash / Realtime, Nemotron Speech 3.5 (streaming) | 🚧 later | streaming engines also unlock tighter live preview |
| Apple Speech (zero-download) | ➖ | macOS system API; substitute: whisper today, Parakeet in v0.4 |
| Cohere Transcribe | ➖ | non-goal — CoreML-only artifacts, no Linux runtime |
| Fluid Intelligence (bundled local AI) | ➖ | closed-source private runtime; substitute: any OpenAI-compatible endpoint incl. local Ollama ✅ |
| Cloud AI polish (OpenAI/Groq/custom) | ✅ | byte-identical prompts, audit-verified request params |
| Live streaming preview overlay | ✅ | Mac-style pill overlay port (solid-black stadium, gloss border, live-audio waveform, streaming text, processing shimmer); segmented engine since 2026-09-05: constant-cost 2 s windows, all four backends, trailing-silence VAD auto-stop (upstream's "detect silence and finish" without the countdown UI); notch ➖ N/A |
| Write/Rewrite mode (⌥R) | ✅ | `hotkey.rewrite_key`, verbatim upstream edit prompts |
| Spoken punctuation ("literal …") | ✅ | full live rule table (108 aliases, spacing semantics) |
| Filler removal + custom dictionary | ✅ | same defaults and matching semantics |
| Spoken-send ("send it" → Enter) | ✅ | configurable phrase; terminal blocklist ✅ (`general.terminal_apps` — phrase strips, text inserts, Enter suppressed) |
| Slash-command/mention literal + spoken formatting | ✅ | literal squeeze AND upstream's spoken forms ported (bc1ced3): `slash fix`/`forward slash fix` with the verbatim 17-word lead-in gate, `at sign`/`at the rate`/`tag`/`mention` names with the possessive guard; relaxed app-gated `at John` stays unported |
| Command mode (voice → actions) | ✅ v2 | upstream tool schema ported into the strict-JSON protocol (`tools` shape from `TerminalService.swift:20-61`: `execute_terminal_command` with per-arg validation, upstream's only-honored tool name enforced loudly); the destructive-command list ported VERBATIM (`CommandModeService.swift:562-598`: 19 prefixes + 9 compound patterns + the anywhere `rm -` rule, literal case-insensitive matching) plus a user-extensible `command.destructive_patterns` list; confirm-every-run with a two-press strong confirm for destructive commands is a deliberate divergence (see STATUS); follow-up context = in-memory per-app last-5/300 s window with spoken "new session" clear (vs upstream's persisted 30-chat global store, `ChatHistoryStore.swift:93-110`); History Commands view with Copy + confirm-gated Re-run; native tool_calls wire format and persistent chat sessions remain upstream-only |
| Per-app prompt sets | ⏳ v0.2 | frontmost-app hint already captured |
| Smart typing via accessibility APIs | ✅ / 🚧 | xdotool on X11 ✅; wtype/ydotool on Wayland ✅ (v0.3); AT-SPI app hints ⏳ (wayland terminal quirks are inert) |
| Menu bar + notch overlay | ✅ (equivalent) | tray/panel icon via StatusNotifierItem (`tray.py`): click = toggle, right-click = settings, recording badge, tooltip w/ hotkey; notch ➖ N/A |
| Audio history (budget, retention) | ✅ | history JSONL + GB budget; searchable History page w/ inline replay, delete, clear; ZIP export ✅ (history + retained audio) |
| Today-usage stats | ✅ | History window header line, `fluidvoice status` `today:` line (local midnight) |
| Adaptive light/dark theming | ✅ | native GTK window follows the system theme |
| Auto-updates + beta channel | ⏳ | today: .deb / GitHub releases; AUR/nix on roadmap |
| Mic priority list, Bluetooth auto-switch | ✅ | pactl 3 s source poll + `recording.mic_priority` patterns (tray menu ordered, settings editor rows); drag-to-reorder later |
| Speaker labeling (diarization) for file transcription | ⏳ | not started; timestamps/JSON export shipped via `transcribe --json`; diarization pending |
| API keys in Keychain | ➖ | substitute: `SAYITERMANO_API_KEY` env var + 0600 config.toml |
| Opt-in telemetry | ➖ | intentionally none (privacy divergence, see STATUS.md) |

## Upstream changelog (newest first)

### Unreleased upstream (after v1.6.9 → `b395a7a`, 09-01 → 09-02)

8 commits (incl. merges), triaged 2026-09-04:

| Upstream change | Linux | Notes |
|---|---|---|
| Mouse-button hotkeys: event-tap isolation, interrupted mouse holds, press lifecycle (3 fixes, PR #939) | ✅ | `recording.push_to_talk_button` arms XGrabButton (all lock-mask combos); mechanism mapping — event-tap isolation = the grab activation is ungrab_pointer'd for the hold so clicks pass through natively (and the passive grab SURVIVES it, no re-arm needed); interrupted mouse holds = release detection via XI2 RawButtonRelease + Escape cancel-during-hold; press lifecycle = full press/hold/release state machine (hotkey.MousePTTListener) |
| Ignored microphones stay removed after reconnect (fixes #933) | ✅ | nothing to port: our `recording.mic_priority` is a static pattern list — there is no per-device suppression set that could resurrect on reconnect; removals persist by design |
| Locked-screen shortcut suppression + dock visibility | ➖ / ✅ | dock stays macOS-only; hotkey suppression while locked is DONE via fluidvoice/lockmon.py — logind session Lock/Unlock + LockedHint PropertiesChanged (GNOME's path, no screensaver name is owned there), PrepareForSleep (suspend counts as locked), screensaver ActiveChanged fallback, ≤5 s LockedHint reconcile; active dictations cancel, tray notes `paused (locked)` (`general.pause_when_locked`) |
| Debug-preferences test isolation | ➖ | upstream test-only |

### Unreleased upstream (after v1.6.9 → `b60a302`, 2026-08-19 → 09-01)

61 commits (incl. merges), grouped by theme:

| Upstream change | Linux | Notes |
|---|---|---|
| Overlay "custom cleanup styles" + streamlined dictation controls (6 commits) | ⏳ | our overlay exists; per-style labels/limits not ported |
| Settings: split AI providers / cleanup styles; side-panel nav; simplified welcome | ✅ | native Settings window (Adw Preferences pages) covers the same knobs — v1.1 adds dictionary/filler editors + language picker; richer upstream provider profiles remain on the roadmap |
| Honor "Send Custom Prompt Only" for dictation-shortcut prompt overrides | ✅ | B1's per-shortcut prompt profiles ARE custom-prompt-only by construction: the profile replaces the base prompt (and any per-app instructions — composition rule pinned by test after their #918) |
| File transcription: chunked API uploads, `.opus`/`.oga` input | ⏳ | `transcribe` accepts opus/oga + 10 more verified formats with ffmpeg fallback; `--json` exports timestamps/segments; **chunked API uploads still pending** |
| Spoken-send commands, quiet-countdown completion, terminal blocklist | ✅ | spoken-send shipped in our `a1390f5`; terminal blocklist ✅ (Enter suppressed in `general.terminal_apps`, pill shows "⏎ skipped (terminal)"); quiet-countdown ✅ (`recording.spoken_send_countdown_s`: phrase + 0.5 s quiet arms a pill countdown, speech cancels, expiry finishes the take — rides the segmented engine's VAD tail detector) |
| Incremental Parakeet preview finalization (experimental) | 🚧 | parakeet-specific; lands with v0.4 streaming work |
| Private AI (Fluid Intelligence) in Edit mode + model verification fixes | ➖ | FI is closed-source; edit mode works with any endpoint ✅ |
| Long-dictation fallback for token-dense Fluid-1 output | ➖ | FI-specific |
| Whisper per-model language picker + `automatic` preserved | ⏳ v0.2 | per-model language selection is on the roadmap |
| Analytics/onboarding-telemetry commits (DAU, weekly buffer, latency capture) | ➖ | no telemetry by design |
| "Instant replacement boost leak" fix | ❓ | verify vs. our dictionary/instant-replacement path |
| CI: archive link on fork PRs | ➖ | upstream CI only |

### v1.6.9 (2026-08-18)

| Upstream change | Linux | Notes |
|---|---|---|
| Custom-dictionary spoken formatting (insert new line/paragraph/tab/punctuation via trigger word) | ❓ | our dictionary does plain replacement only — triage |
| Mic alerts not shown on routine startup + disable option | ⏳ | assumes macOS permission flow |
| Fixes: 3.5mm external mics, Bluetooth route changes, clamshell mode | ✅ / ⏳ | Bluetooth route changes covered by source monitoring; 3.5mm/clamshell ⏳ |
| Speaker-labeled transcription drops no trailing audio | ⏳ | with diarization |

### v1.6.8 (2026-08-11)

| Upstream change | Linux | Notes |
|---|---|---|
| Offline speaker labeling (timestamps, speaker-aware history, text/JSON export) | ⏳ | candidate feature; pyannote-class models exist on Linux |
| Microphone priority list, drag-to-reorder, device history | ✅ / ⏳ | priority list + auto-switch shipped (up/down reorder buttons); drag-to-reorder and device history remain ⏳ |
| Fix: modifier-only shortcuts firing after Shift combos | ❓ | we handle lock-mask variants; verify shift-combos |
| Fix: temperature ignored for some OpenAI-compatible models | ✅ | our client always sends temperature (audit-verified) |

### v1.6.7 (2026-08-05)

| Upstream change | Linux | Notes |
|---|---|---|
| Parakeet up to 2× faster on Apple silicon | ➖ | CoreML; our perf work comes with v0.4 ONNX |
| First-word latency ~350 ms → <100 ms | 🚧 | recorder start probe pinned <0.25 s by regression test (2026-09-05, reviews rider); preview first partial at ~1 s (min_audio + one window) — below that needs true streaming engines |
| Temporary pasteboard writes hidden from clipboard managers | ✅ | X11 hygiene markers (CopyQ live-verified); GNOME-shell-extension residual in STATUS |
| Fix: AirPods media controls stopped while enabled | ⏳ | roadmap: MPRIS media pause |

### v1.6.6 (2026-07-31)

| Upstream change | Linux | Notes |
|---|---|---|
| Faster-appearing recording overlays | ✅ | our overlay window is immediate |
| Optional skip of short silent recordings | ✅ | opt-in silence gate (upstream thresholds) |
| Core Audio default path (sped-up audio fix) | ➖ | PipeWire/PulseAudio here |

### v1.6.5 (2026-07-21)

| Upstream change | Linux | Notes |
|---|---|---|
| Reliable pasting in Ghostty/tmux/terminals | ✅ | verify-then-restore + `ctrl+shift+v` in `general.terminal_apps`; ghostty untested locally |
| Experimental recording path (now opt-in) | ➖ | macOS capture stack |

### v1.6.4 (2026-07-14)

| Upstream change | Linux | Notes |
|---|---|---|
| Fix Fluid-1 MLX backend on macOS 15 | ➖ | Fluid Intelligence only |

### v1.6.3 (2026-07-14)

| Upstream change | Linux | Notes |
|---|---|---|
| Fluid-1 2.2× faster on Apple silicon | ➖ | Fluid Intelligence only |
| Auto-suggested custom-dictionary replacements | ✅ | suggest-only, from history edits (`edited_from`); see STATUS divergences |
| Train by Voice (pronunciation samples) | ⏳ | not started |
| Optional/configurable spoken punctuation | ✅ | toggles + full rule table ported |
| All Whisper models restored | ✅ | tiny→large-v3-turbo |
| Copy latest transcript from menu bar | ✅ | `fluidvoice paste-last` + socket action + `hotkey.paste_key` global shortcut + optional copy-to-clipboard |

### v1.6.0 (the release our port was audited against)

| Upstream change | Linux | Notes |
|---|---|---|
| Rebuilt Parakeet ("pretty much zero delay") | 🚧 | v0.4 model work |
| Fluid Intelligence local AI | ➖ | substitute: any OpenAI-compatible endpoint ✅ |
| Adaptive theming + compact toolbar switcher | ✅ | native GTK app (AdaptiveDialog-free PreferencesWindow, follows system theme) |
| Refreshed onboarding (engine setup, tryout) | ✅ | native onboarding window (`fluidvoice app --onboard`) opens once on first launch: mic/engine/hotkey checks + real 3s dictation tryout (nothing typed) |

---

## Watch: other platforms upstream

- **Windows port** — upstream publishes Windows pre-releases (branch
  `windows-main`, tags through `windows-v0.0.9`, 2026-08-11): redesigned
  overlay with word-by-word live preview, Fluid-1 Mini (1.5 GB local model),
  "learn from your edits" dictionary. Useful signal for what a non-macOS port
  needs; Fluid-1 itself remains closed-source, so the Linux substitute stays
  "bring your own OpenAI-compatible endpoint".
- **iOS** — waitlist only (`altic.dev/fluid/waitlist`); nothing to track yet.
