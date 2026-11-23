# Competitor landscape refresh — 2026-11-22

- Status: evidence note for monetization plan question 1
  ([plan](../plans/2026-09-11-product-excellence-and-monetization-plan.md),
  "Refresh the existing comparison from official sources … distinguish
  advertised features from tested behavior").
- Companion artifact: the refreshed [COMPARISON.md](../COMPARISON.md).
- Method: every competitor claim below was re-checked at the vendor's own
  page, repo, or release notes on **2026-11-22** (URL + access date inline).
  Prior repo research ([2026-11-21 productization verification](2026-11-21-productization-verification.md),
  [competitive digest](competitive/2026-11-21-digest.md)) was used only to
  cross-check, never as the source of record; where this pass disagrees with
  it, that is called out.
- **ADVERTISED** = read on the vendor's official source today; this
  repository has not exercised it. **TESTED** = this repository's own
  verification exercised the claim (BEHAVIOR-SPEC audits, eval notes, night
  runs under docs/research). No third-party roundups were used as evidence.

## Headline changes since the 2026-09-01 matrix

1. **Upstream FluidVoice now ships a Linux early beta** (Windows beta, iOS
   TestFlight, Android waitlist too). The old matrix's founding premise —
   "official FluidVoice is macOS-only" — is **STALE**. Upstream remains
   free/GPLv3 with a private-license local cleanup runtime (Fluid
   Intelligence). [README, accessed 2026-11-22](https://github.com/altic-dev/FluidVoice)
2. **Voxtype turned into a full local dictation stack** — v1.3.1 (released
   2026-11-21) has 8 engines incl. Cohere Transcribe and Parakeet, spoken
   punctuation, replacement tables, optional LLM post-processing, history +
   paste-last, single-binary packaging. The 2026-09-11 commercial-model note
   treated it as an early "advertises local processing" project; the
   2026-11-21 verification logged v0.7.0 — both stale. [voxtype.io, accessed 2026-11-22](https://voxtype.io)
3. **nerd-dictation is dormant** — last push 2025-10-10 (13 months).
   [GitHub API, accessed 2026-11-22](https://api.github.com/repos/ideasman42/nerd-dictation)
4. Vocalinux moved orgs (jatinkrmalik → **VocaHQ**), is at v0.18.6, defaults
   to hold-PTT, and types on Wayland via the RemoteDesktop portal in its
   Snap. [README, accessed 2026-11-22](https://github.com/VocaHQ/vocalinux)
5. Wispr Flow still has **no Linux client** ("There is no native Linux
   application"), while its free tier is now word-limited per week.
   [docs, accessed 2026-11-22](https://docs.wisprflow.ai/articles/6478598909) · [pricing, accessed 2026-11-22](https://wisprflow.ai/pricing)

## Linux-native competitors (all claims ADVERTISED unless marked)

Every row in this section is ADVERTISED: this repository has never run any
of these tools hands-on (no notes exist under docs/research doing so), so
nothing below is TESTED.

| Tool | Price (verified 2026-11-22, official source) | Engine / where speech runs | AI polish | Push-to-talk | Insertion | Linux support level |
|---|---|---|---|---|---|---|
| [Handy](https://github.com/cjpais/handy) | Free, MIT | Whisper GGUF (Small→Large, Turbo) + Parakeet V3 via whisper.cpp; Silero VAD; fully offline | Optional LLM post-processing (`--toggle-post-process`) + user `hooks/transcription` rewrite | hold or tap (hold-only/toggle-only modes) | paste (Linux default: Shift+Insert to clipboard+primary, restored); Direct typing layout-dependent | Native (Tauri): .deb/AppImage etc.; X11 via xdotool, Wayland limited via wtype/dotool (Ubuntu 26.04 needs ydotool); Wayland global shortcuts via DE binding of `handy --toggle-transcription` |
| [Vocalinux](https://github.com/VocaHQ/vocalinux) v0.18.6 | Free, AGPL-3.0 | whisper.cpp (default, Vulkan GPU) / OpenAI Whisper / Faster-Whisper / VOSK / Parakeet (sherpa-onnx); local after model download; optional remote HTTP API OFF by default | none built-in (configurable `post_processing.script_path` user script) | toggle or hold (new installs default: hold Right Alt) | xdotool, IBus, wtype, ydotool, clipboard fallback; Snap uses RemoteDesktop portal on Wayland | Native: .deb/.rpm/AppImage/AUR/PyPI/Snap(edge)/self-hosted Flatpak (NOT on Flathub); X11+Wayland |
| [nerd-dictation](https://github.com/ideasman42/nerd-dictation) | Free, GPL-3.0 | VOSK; offline; single Python file | none (user Python config script manipulates text) | begin/end commands you bind yourself (hold possible via press/release binds) | xdotool simulated keystrokes (or stdout) | X11; community Wayland workarounds only; **dormant since 2025-10-10** |
| [VOXD](https://github.com/jakovius/voxd) | Free, MIT | whisper.cpp; local/offline, CPU fine | AIPP: local llama.cpp (default model) / Ollama, or cloud OpenAI/Anthropic/xAI; GUI prompt editor (4 prompts) | hotkey toggle (first press start, second stop; DE shortcut runs `voxd --trigger-record`) | simulated typing via ydotool (works on Wayland; needs input group + relog), clipboard copy | Native: .deb/.rpm/.pkg.tar.zst + setup.sh; tested on Hyprland/GNOME/Sway/KDE/COSMIC/Cinnamon; X11+Wayland; VAD "flux" mode (beta) |
| [OpenWhispr](https://github.com/OpenWhispr/openwhispr) | Free tier: unlimited **local** dictation; Pro $6.67/user/mo ($80/user/yr); Business $13.33/mo ($160/yr); Enterprise custom. [pricing, accessed 2026-11-22](https://openwhispr.com/pricing) | Electron 41 + whisper.cpp/sherpa-onnx: Whisper, Parakeet, Cohere Transcribe, Orukeet locally (Metal/CUDA/Vulkan), or OpenWhispr Cloud (2,000 words/wk free; unlimited paid) | yes — dictation cleanup, AI agent, translation; local or cloud LLM | global hotkey (hold/toggle per settings) | "automatic pasting" at cursor | Native: .AppImage/.deb/.rpm (+ iOS paid); MIT; 12,040★ |
| [Voxtype](https://voxtype.io) v1.3.1 | Free, MIT, no paid tier | 8 engines: Whisper, Parakeet, Cohere Transcribe (q4f16, 9-11× realtime CPU), Moonshine, SenseVoice, Paraformer, Dolphin, Omnilingual; local by default (optional remote Whisper servers) | optional post-processing pipe through any LLM (e.g. Ollama) or shell script; meeting summaries | PTT first-class: hold ScrollLock, compositor `bind`/`bindr` press-release pairs, evdev fallback | Wayland-first typing via wtype (full CJK), fallback dotool → ydotool → clipboard; paste mode for terminals | Native single binary: AUR/.deb/.rpm/Nix/macOS; GNOME/KDE/Sway/Hyprland/River; Wayland-optimized, X11 works; history + paste-last (v1.3.1) |
| [Voicy](https://usevoicy.com/dictation-for-linux/) | Free trial 30 min; Pro $8.49/mo or $82/yr; **Lifetime $260**; Teams $6.79/user/mo (BFCM20 -20% annual promo running). [pricing, accessed 2026-11-22](https://usevoicy.com/pricing/) | **cloud** speech by default; optional offline mode (model download, 25 European languages, audio stays local); AI rewrite online-only | automatic punctuation; AI rewriting (internet) | "press your shortcut and speak" (app hotkey) | types into any focused text field | Native: .deb/.rpm/AppImage; advertises GNOME/KDE, Wayland + X11, 50+ languages online / 25 offline |
| [Vibe Typer](https://vibetyper.com/downloads) | Free: 2,000 words + 20 AI ops/mo; Pro $8/mo annual ($96/yr) or $10/mo monthly. [pricing, accessed 2026-11-22](https://vibetyper.com/pricing) | cloud transcription (metered words; "No recordings on our servers") | Magic Formatter (own model) + AI operations | hotkey ("press the hotkey and talk") | pastes into apps | Native: AppImage (Ubuntu/Fedora/Debian/Arch/Mint, X11 & Wayland); Mac/Windows/iOS/Android siblings |
| [FluidVoice (upstream)](https://github.com/altic-dev/FluidVoice) v1.6.13 | Free, GPLv3 (code); Fluid Intelligence local cleanup runtime is **private-license**, included in official downloads only | macOS app: Fluid Speech Pro/Mini/Pico (CC BY 4.0 weights), Parakeet, Nemotron, Qwen3 ASR, Cohere, Apple Speech, Whisper — all on-device; optional cloud AI providers | Fluid 1.1 Ripple/Splash **on-device cleanup** (research preview); Edit mode; per-app prompt sets | hold/toggle global hotkey; mouse PTT; Hold-Space-to-mute | AX/keystroke/paste (Smart Typing) | **Linux early beta now exists** (+ Windows beta, iOS TestFlight, Android waitlist); "features and available models vary by platform" — Linux beta scope not documented beyond that |

Non-Linux price anchors (context only, both ADVERTISED):

| Product | Offer | Platform | Source (accessed 2026-11-22) |
|---|---|---|---|
| [MacWhisper](https://macwhisper.com) | Free forever; Pro **€64 one-time**, lifetime updates; App Store edition IAP up to $99.99 | macOS | [site](https://macwhisper.com) |
| [Wispr Flow](https://wisprflow.ai/pricing) | Free (limited words/week); Pro $15/user/mo or $12 annual; Growth $23/$18 | Mac/Win/mobile — **no Linux client** | [pricing](https://wisprflow.ai/pricing), [docs](https://docs.wisprflow.ai/articles/6478598909) |

Discrepancies vs the 2026-11-21 productization note (both re-checked today):

- Voxtype version: that note logged v0.7.0/1,809★; the official site and
  releases show **v1.3.1** (2026-11-21) with history/paste-last and meeting
  mode. Use v1.3.1.
- Voicy Pro monthly $8.49 still on the Linux page pricing block; the pricing
  page's annual tab headlines $82/yr. Both cited above.
- OpenWhispr Pro is now billed "$6.67/user/mo ($80/user billed annually)" —
  same $80/yr number, clearer monthly framing.

## What this repository has actually TESTED (the SayItErmano row)

The SayItErmano column set in [COMPARISON.md](../COMPARISON.md) splits by
evidence. What counts as TESTED and where:

| Claim | Evidence (this repo) | Mark |
|---|---|---|
| Offline loop end-to-end on X11 (hotkey → record → faster-whisper → insert into a real app) | [night desktop matrix X11, 2026-09-13](night-2026-09-13-desktop-matrix-x11.md): real takes into gedit/gnome-terminal/Firefox/Chromium/Discord/LibreOffice, byte-exact readback | **TESTED** (X11) |
| Hotkey toggle+hold with native key passthrough; mouse PTT; locked-screen suppression; grab recovery | [integration run, 2026-09-13](night-2026-09-13-integration-run.md): 32 passed incl. live hotkey grabs, mouse-PTT escape/blocked-arm, synthetic toggles; BEHAVIOR-SPEC §4 | **TESTED** (X11) |
| AI polish offline-safe; verbatim upstream prompts | BEHAVIOR-SPEC §1 (byte-identical prompt audit 2026-09); integration tier live AI polish/rewrite ×4 vs local/mock LLM | **TESTED** |
| "literal" spoken-punctuation engine (108 aliases, spacing semantics) | unit tests, `fluidvoice/processing/punctuation.py` (BEHAVIOR-SPEC §2) | **TESTED** (unit + audits) |
| Insertion typed + paste w/ clipboard restore; terminal safety blocks | X11 matrix (above); integration run insertion/hygiene tests | **TESTED** (X11) |
| Decode speed/quality on real audio | [decode baseline, 2026-09-11](../eval/decode-baseline-2026-09-11.md): faster-whisper large-v3 CPU over 32 TTS utterances + negatives; parakeet-tdt-0.6b-v2 exact golden transcript via sherpa-onnx (integration run) | **TESTED** |
| Wayland insertion (wtype/ydotool ladder), evdev PTT, DE-shortcut toggle | implemented, unit-covered — but [STATUS.md](../STATUS.md): "Live Wayland/X11 smoke matrices have never run" | **ADVERTISED** (implementation claim; live matrix NOT DONE) |
| Paid SKU ($49 / $39 intro one-time) | [PRICING.md](../PRICING.md)/ADR-0005 — approved, Paddle checkout not yet live | **ADVERTISED** (own plan, not a shipped offer) |
| v0.9.0 productization (i18n, Flathub prep, license keys, apt repo) | CHANGELOG v0.9.0 (2026-11-22); post-release review pending | shipped per CHANGELOG |

## Gaps we own

Ahead — with the evidence line for each:

- **Fully-offline default loop, tested end-to-end on X11** — every open
  rival either clouds by default (Voicy, Vibe Typer, Wispr), meters words
  (OpenWhispr free cloud tier caps), or is offline like us; our
  differentiator is the *tested* chain above, not offline alone. Handy /
  Vocalinux / Voxtype / VOXD are also offline — offline is table stakes
  among the free tools, not a moat by itself.
- **The FluidVoice dictation-cleanup pipeline** (verbatim prompts, "literal"
  spoken-punctuation rule engine, slash/mention squeeze, terminal send
  guards, per-app prompt profiles) — no Linux rival documents an equivalent
  rule engine; Voxtype has spoken punctuation + replacement tables, VOXD
  has prompt-based AIPP, but none ports the FluidVoice behavior set. This
  remains the port's reason to exist — now also against upstream's own
  Linux beta, where we are the GPL-pure, pipeline-hackable alternative.
- **Local AI polish with any OpenAI-compatible endpoint** incl. fully-local
  (integration-tested offline); OpenWhispr charges for the cloud side and
  VOXD ships llama.cpp defaults — we are not unique here, but tested + 
  endpoint-agnostic is our version of it.
- **One-time pricing posture**: among Linux-native tools with a price, only
  Voicy offers lifetime ($260); everyone else is subscription. Our $49
  one-time stays the cheapest paid option on Linux (pricing evidence above).

Behind — same discipline:

- **Ecosystem/mindshare**: Handy 39.7k★ (MIT, active Flatpak-portal work),
  OpenWhispr 12.0k★ (paid team, iOS app), Voxtype shipping weekly-quality
  releases; we are a single-maintainer community port.
- **Mobile**: Voicy/Vibe Typer/OpenWhispr/Wispr/FluidVoice all have iOS or
  Android surfaces; we have none and no plan (Linux-only stance).
- **Engine breadth**: Voxtype ships 8 engines incl. Cohere Transcribe and
  GGUF/Qwen3-ASR paths; FluidVoice upstream ships Fluid Speech Pro. Our
  tested set is faster-whisper (+whisper.cpp path, golden-tested parakeet
  ONNX). Roadmap item, not a today claim.
- **Live Wayland evidence**: upstream beta scope is unknown but Vocalinux
  already types via the RemoteDesktop portal on Wayland in a store build;
  our Wayland story is implemented-but-not-live-tested (STATUS.md). This is
  the most actionable gap: the Wayland smoke matrix gates parity claims.
- **First-party vendor**: upstream itself entering Linux (early beta)
  crowds the "official FluidVoice on Linux" positioning; our niche must be
  the open pipeline + tested reliability, said plainly.

## Method and limits

- Official sources only: vendor pricing pages, product/repo READMEs,
  GitHub API (for nerd-dictation/OpenWhispr metadata), vendor docs. ~14
  fetches on 2026-11-22; no paywalled, cached, or third-party content was
  used for any table cell.
- fluidvoice.com/fluid/download/linux exceeded the fetcher's size limit
  twice; the Linux beta's existence and its "features vary by platform"
  caveat are cited from the upstream README instead. Scope of that beta
  (packaging, engines, portal use) is **unknown** — flagged, not inferred.
- No competitor was installed or run by this repository; every competitor
  row is ADVERTISED by the definition above. Prices are displayed list
  prices on the check date (Voicy Black-Friday promo noted); regional
  pricing and checkout taxes unchecked.
- Star counts are point-in-time API reads (Handy 39.7k from the repo page;
  OpenWhispr 12,040 and nerd-dictation 1,947 via GitHub API today).
