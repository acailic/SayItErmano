# How this project relates to other dictation tools

Research date: **2026-11-22** (previous matrix: 2026-09-01). Full evidence
trail with URLs, access dates, and the advertised/tested audit:
[2026-11-22 competitor landscape refresh](research/2026-11-22-competitor-landscape-refresh.md).

Two things changed since the last matrix: **upstream FluidVoice now ships a
Linux early beta** (plus Windows beta / iOS TestFlight), and the free local
tools consolidated — Handy (39.7k★), Vocalinux (v0.18.x), and a greatly
expanded Voxtype (v1.3.1, 8 engines) all ship real PTT dictation. This
project's niche is unchanged but no longer unclaimed: it ports FluidVoice's
*differentiating behavior* — the dictation-cleanup prompt pipeline, the
"literal" spoken-punctuation engine with context rules, per-app prompts —
keeping the pipeline local, GPLv3-compatible, and hackable.

**Evidence legend** — every row below is one of:

- **ADVERTISED** — read on the vendor's official page/repo on 2026-11-22;
  this repository has not exercised the tool.
- **TESTED** — this repository's own verification exercised the claim
  (night runs / integration tier / eval notes — linked from the research
  note). Applies to the SayItErmano row, per-claim.

| Tool | Price (2026-11-22) | Speech runs | AI polish | PTT | Insertion | Linux support | Evidence |
|---|---|---|---|---|---|---|---|
| **SayItErmano (this, formerly FluidVoiceLinux)** v0.9.0 | Free (GPL-3.0); $49 one-time / $39 intro paid SKU approved but store not live ([PRICING](PRICING.md)) | local: faster-whisper / whisper.cpp / torch (CUDA) | ✅ verbatim FluidVoice prompt, any OpenAI-compatible endpoint incl. fully local | ✅ XGrabKey toggle+hold w/ passthrough, mouse PTT — **TESTED** (X11); Wayland DE-shortcut + evdev PTT — ADVERTISED (live matrix not run) | typed + paste w/ restore (X11 **TESTED** across 6 apps); Wayland wtype/ydotool ladder — ADVERTISED | X11 **TESTED**; Wayland implemented, live smoke matrix NOT DONE | mixed — per-claim split in the [research note](research/2026-11-22-competitor-landscape-refresh.md) |
| [Handy](https://github.com/cjpais/handy) | Free, MIT | local (whisper.cpp: Whisper GGUF + Parakeet V3; Silero VAD) | optional LLM post-process + `hooks/transcription` user hook | ✅ hold or tap | paste (Shift+Insert default on Linux) + layout-dependent direct typing | native (Tauri); X11 xdotool, Wayland limited (wtype/dotool; Ubuntu 26.04 ydotool), DE shortcut for global keys | ADVERTISED |
| [Vocalinux](https://github.com/VocaHQ/vocalinux) v0.18.6 | Free, AGPL-3.0 | local (whisper.cpp default, Vulkan; Whisper/Faster-Whisper/VOSK/Parakeet) | ➖ (user `post_processing` script only) | ✅ toggle or hold (new installs: hold Right Alt) | xdotool / IBus / wtype / ydotool / clipboard; Snap types via RemoteDesktop portal | native (.deb/.rpm/AppImage/AUR/PyPI/Snap/Flatpak-selfhost; not on Flathub); X11+Wayland | ADVERTISED |
| [nerd-dictation](https://github.com/ideasman42/nerd-dictation) | Free, GPL-3.0 | local (VOSK) | ➖ (user Python config) | begin/end commands you bind | xdotool keystrokes (or stdout) | X11; community Wayland; **dormant since 2025-10-10** | ADVERTISED |
| [VOXD](https://github.com/jakovius/voxd) | Free, MIT | local (whisper.cpp, CPU fine) | ✅ AIPP: llama.cpp/Ollama local or OpenAI/Anthropic/xAI | ✅ hotkey toggle (DE shortcut `--trigger-record`) | simulated typing via ydotool; clipboard | native (.deb/.rpm/Arch); X11+Wayland (ydotool); VAD flux beta | ADVERTISED |
| [OpenWhispr](https://openwhispr.com/pricing) | local dictation free; Pro $6.67/mo ($80/yr); Business $13.33/mo ($160/yr) | local (Whisper/Parakeet/Cohere/Orukeet via whisper.cpp+sherpa-onnx) **or** cloud (2,000 words/wk free, unlimited paid) | ✅ cleanup + AI agent, local or cloud | ✅ global hotkey | auto-paste at cursor | native (Electron): AppImage/.deb/.rpm; X11+Wayland; MIT | ADVERTISED |
| [Voxtype](https://voxtype.io) v1.3.1 | Free, MIT | local by default (8 engines: Whisper, Parakeet, Cohere Transcribe, Moonshine, SenseVoice, Paraformer, Dolphin, Omnilingual) | ✅ optional LLM/shell post-process pipe | ✅ PTT first-class (compositor bind/bindr, ScrollLock, evdev) | wtype Wayland-first (CJK), dotool→ydotool→clipboard ladder; history + paste-last | native single binary (AUR/.deb/.rpm/Nix); Wayland-optimized, X11 works | ADVERTISED |
| [Voicy](https://usevoicy.com/dictation-for-linux/) | $8.49/mo · $82/yr · **$260 lifetime**; 30-min free trial | **cloud** default; offline mode 25 langs (model download) | ✅ automatic punctuation; AI rewrite (online) | ✅ app hotkey | types into focused field | native (.deb/.rpm/AppImage); GNOME/KDE, Wayland+X11 advertised | ADVERTISED |
| [Vibe Typer](https://vibetyper.com/pricing) | free 2,000 words + 20 AI ops/mo; Pro $8/mo annual ($96/yr) or $10/mo | cloud (metered; "no recordings on our servers") | ✅ Magic Formatter + AI ops | ✅ hotkey | pastes into apps | native AppImage (X11 & Wayland); +Mac/Win/mobile | ADVERTISED |
| [FluidVoice (upstream)](https://github.com/altic-dev/FluidVoice) v1.6.13 | Free (GPLv3 code; Fluid Intelligence cleanup runtime is private-license) | local (Fluid Speech Pro/Mini/Pico, Parakeet, Nemotron, Qwen3 ASR, Cohere, Whisper); optional cloud providers | ✅ Fluid 1.1 Ripple/Splash on-device cleanup; per-app prompts | ✅ hold/toggle + mouse PTT + hold-space-mute | AX/keystroke/paste | macOS + **Linux early beta** (scope beyond existence undocumented); iOS/Windows betas | ADVERTISED |

Non-Linux price anchors (ADVERTISED, cited in the research note):
[MacWhisper](https://macwhisper.com) €64 one-time (macOS);
[Wispr Flow](https://wisprflow.ai/pricing) $15/mo · $12/mo annual — still
**no Linux client** per [its docs](https://docs.wisprflow.ai/articles/6478598909).

**Why this port exists even though Handy/Vocalinux/Voxtype are good tools:**
none of them implements FluidVoice's differentiating behavior — the
dictation-cleanup prompt pipeline, the "literal" spoken-punctuation engine
with context rules, and per-app behavior. This project ports *that*, keeps
the pipeline local and hackable, and stays GPLv3-compatible with upstream
(verbatim prompts + SFX copied under the same license). With upstream now
beta-testing Linux itself, the honest position is: we are the open,
pipeline-complete, tested-on-X11 community port today — see the "gaps we
own" section of the research note for what is genuinely ahead vs behind.

If you just want "press key → text appears" with minimal setup, Handy is a
fine choice. If you want FluidVoice's polished-output behavior on Linux
today, that's this project's niche.
