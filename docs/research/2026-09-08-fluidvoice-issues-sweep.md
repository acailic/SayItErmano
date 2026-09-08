# Upstream FluidVoice issues/discussions sweep — 2026-09-08

Companion to 2026-09-05-fluidvoice-reviews.md (press/strategy focus) and
the 2026-09-08 improvement audit. Method: all 58 non-PR issues
(state=all), the 40 most-recently-updated discussions, and the v1.6.8/
v1.6.9 release notes from altic-dev/FluidVoice, each mapped to our
status. Items verified against OUR tree are marked ✅-checked.

## 1. New actionable items (ported pain, verified where noted)

| # | Upstream item | Our status / action |
|---|---|---|
| #861 | `isDestructiveCommand` bypass classes | **✅-checked GAP**: `find / -delete` and `find . -exec rm {} +` return `is_destructive_command() == False` today (our list was the verbatim pre-#861 port). Add `find … -delete/-exec` patterns to `DESTRUCTIVE_PATTERNS` + regression tests incl. absolute-path forms (`/bin/rm` is already caught by the anywhere `rm -` rule). |
| #930 | Command mode hangs far past timeout when a background descendant holds stdout/stderr | **✅-checked GAP**: `run_shell` (command.py:327) uses `subprocess.run(timeout=)` which kills only the shell. Fix: `Popen(start_new_session=True)` + `os.killpg` on timeout so the process group dies and pipe reads end. |
| #910 (open) | AI Enhancement pastes the entire system prompt instead of the transcript | Same seam as our refusal guard (`is_refusal`): add a prompt-leak detector (reply echoing base-prompt markers) → fall back to raw transcript + notify. |
| #852 (open, 17 💬) | Dictation stops detecting mic input after 3 s | We have first-PCM timeout + max_seconds, but no mid-take stall watchdog (audio stream goes silent mid-take → we record padding until stop). Consider a mid-take PCM-stall detector → fail fast with a clear error. |
| #940 (open) | Typed insertion merges with physically held modifiers (stray Ctrl+V pastes old clipboard) | **✅-checked SHIPPED**: our typed path is `xdotool type --clearmodifiers` (insertion.py:117) — held modifiers are cleared during typing. Worth a regression test to pin it. |
| #917 / #948 (open) | Multi-day memory growth to 3.9 GB; 204 min CPU over 17 h idle | We unload models on idle (their #853/#854 ask — they still refuse) but have NO long-uptime soak measurement. Add a scripted soak (daemon idle 24 h, RSS/CPU sampled) to catch our own leaks. |
| #918 | "Send Custom Prompt Only" ignored with a profile selected | Our B1 profiles: verify the override semantics honor a custom-only flag (open UPSTREAM-TRACKING row). |
| #880 / #944 | Custom dictionary sometimes replaces incorrect words | ✅-check our matcher for word-boundary safety + add their false-positive cases as fidelity tests. |
| #926 / #914 / #885 / #913 | Provider compat (LM Studio), suggested small GGUF polish models, GigaAM v3 (Russian), Phonon-1 STT | Watch + docs: README could recommend known-good small polish models; backend seam makes GigaAM/Phonon additions cheap later. |
| #927 (discussion) | **MCP server so other agents can tap the STT engine** | Extension of our fresh unix-socket API (C1): an MCP wrapper exposing `transcribe`/`history` would make SayItErmano the STT backend for every MCP agent — upstream has the request, no TCP needed. Strong differentiator candidate. |
| #916 (discussion) | True custom vocabulary (words to ADD, not replace) | faster-whisper exposes `hotwords=`; a `processing.hotwords` list feeding both the final decode and preview `initial_prompt` would fulfill this. Real feature opportunity. |
| #886 / #904 | Word count in the overlay / dashboard speaking-time totals | Tiny UX wins; Stats page already has today/all-time time — add total speaking time if missing; pill word-count is a one-liner on the renderer. |
| #935 (open) | Coordinated vulnerability disclosure policy | Add SECURITY.md — 10-minute quick win for credibility. |
| #929 (open) / #928 (open) | Clipboard destroyed; light overlay theme | #929 we SHIPPED (paste verify + restore, clipboard-manager hygiene). #928: pill supports one dark theme — a light/system variant is a small renderer task. |
| v1.6.9 notes | Dictionary spoken formatting inserts newlines/tabs/punctuation via a start word; extra-closing-thinking-tag tolerance | Check our spoken formatting action coverage (we have formatting_action_triggers) + our think-strip against the extra `</think>` case. |

## 2. Already shipped here (their open pain = our differentiators)

| Upstream signal | Our shipped answer |
|---|---|
| #833 preview re-transcribes whole buffer, lags on long takes | Segmented constant-cost engine (every backend) |
| #853/#854/#922 model pinned in RAM, slow first dictation after idle | `model.idle_unload_s` + warm-toggle latency work |
| #506/#100 runtime language switching "not possible" | `hotkey.language_key` cycle + whitelist guard (v0.7.0) |
| #615 remote STT backend (maintainer declined the PR) | `model.remote_url` OpenAI-compatible backend (v0.7.0) |
| #871 local API/CLI access to the loaded engine | unix-socket `transcribe`/`history` routes (v0.7.0) |
| #925/#910 Fluid Intelligence slow/refuses/prompt-leaks | any-endpoint polish + refusal guardrail (leak: §1) |
| #884 hotkey fires while locked | `pause_when_locked` (logind, incl. suspend) |
| #834 multiple hotkeys → different models | `hotkey.extra_shortcuts` with prompt profiles (model swap = easy extension) |
| #838/#844/#846/#870 mic chaos after upgrades/route changes | mic priority list + never-mid-take switching (the v1.6.8 structural fix, ported earlier) |
| #868 opus/WhatsApp notes rejected | 14 formats + ffmpeg fallback |
| #865 minimal overlay | pill/small/medium/large sizes |
| #874 raw-only mode | per-shortcut profiles + ai.enabled |
| #889 quick send | spoken-send + quiet countdown (v0.7.0) |
| #931/#933/#891/#921 (Dock/input sources/clicks/event tap) | N/A on Linux/X11 by construction |

## 3. Watch (no action yet)

#877 on-device Apple models (N/A), #888 Russian `<unk>` tokenizer,
#899 AI-context truncation fallback, #901 multiple instances under
Universal Control (our lock + coexist doctor warning cover the class),
#908 mute-vs-pause sounds nuance, #892 managed configuration (enterprise,
later), #881 machine sync, #897 live translated captions, #920 MedASR,
#949 "UI needs a rework" (fresh, watch where it goes), #942 monetization
(their answer may shape the competitive landscape), #915 Windows parity.

## Suggested picks

Quick wins: #861 find-patterns fix, #930 killpg fix, #935 SECURITY.md,
#940 regression test, #918 profile semantics check.
Feature candidates for the next cycle: **#927 MCP wrapper** (rides C1),
**#916 hotwords vocabulary**, #910 prompt-leak guard (rides D5),
#852 mid-take stall watchdog, soak test from #917/#948.
