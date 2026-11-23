# Quickstart: your first verified dictation

Part of the [documentation index](../README.md). This page walks the
**first-use setup flow** — one command that checks your machine,
recommends a speech model that fits it, downloads it with live
progress, audits your desktop's text-insertion path, and ends with a
real 2-second test take that is transcribed **and inserted**, so you
know the whole loop works before you dictate for real.

## Run it

```bash
sayit-ermano setup
```

Just installed? See the [install routes](install.md) first — the
one-shot installer pre-installs the system tools the flow checks for.

Non-interactive (defaults everywhere — CI, dotfiles, second install):

```bash
sayit-ermano setup --yes
```

Pin the dictation language or skip the recommendation:

```bash
sayit-ermano setup --language de --model small
```

## What happens, step by step

1. **Hardware scan** — GPU (CUDA + VRAM via `nvidia-smi`), CPU cores
   and RAM are detected and printed. Nothing is guessed silently.
2. **Grounded model pick** — candidates are ranked for *your* machine
   and language, one line each saying **why** (e.g. *"8192 MB VRAM
   fits large-v3-turbo (~1.6 GB) with room for the CUDA runtime"*).
   The default mirrors what the daemon's own `auto` resolution would
   pick, so setup never disagrees with real dictations. Your choice is
   saved to `config.toml`.
3. **Download with real progress** — percent, size, transfer rate and
   ETA, reusing the same download machinery as the app (resumable
   `.part` discipline, checksummed installs).
4. **Insertion audit** — X11 (`xdotool`/`xclip`) vs Wayland
   (`wtype`/`ydotool`/RemoteDesktop portal, `wl-clipboard`): the exact
   missing-package line for your distro, per-desktop shortcut binding
   instructions, and the permission guidance `sayit-ermano doctor`
   prints (e.g. `sudo usermod -aG input $USER` for evdev push-to-talk).
5. **Verified test take** — records 2 s from your mic, transcribes it
   with the model just installed, and inserts the result into the
   window you focus. Green exit = the full loop worked.

## Reading the result

| Exit | Meaning |
| --- | --- |
| `0` + `SETUP VERIFIED` | record → transcribe → insert all worked |
| `0` + `SETUP BLOCKED` | something is missing, and the **exact fix is
  printed** (install line, permission command). Fix it, re-run. |
| `1` + `SETUP INCOMPLETE` | the take was inconclusive (silence, no
  speech detected) — nothing is broken; re-run and speak during the
  2-second window. |

Blocked on the model download? The message includes the actionable
hint (rate-limit, network, retry guidance). Everything else working
but insertion blocked on Wayland? See the
[Wayland guide](wayland.md) for the full capability matrix.

## After setup

- Dictate with your hotkey, or `sayit-ermano toggle`.
- The daemon autostarts at login (systemd user unit).
- Something off later? `sayit-ermano doctor` is the full environment
  report; the [FAQ](../dev/FAQ.md) covers the common problems.
- Every dictation lands in History (`sayit-ermano history`) even when
  insertion fails — text is never lost.

The GTK app's first-run onboarding (opened automatically on a fresh
data dir) runs the same verification over the daemon socket; the CLI
flow here is the same funnel, offline-friendly and scriptable.

## When the text is wrong: corrections

Four features turn a mis-heard take into a better next one. None of
them is new machinery — this is where each one lives.

| You want to… | Go to | What it does |
| --- | --- | --- |
| fix the take you just dictated | **History** — tray menu → **History** (or `sayit-ermano app`): the pencil on a row edits the text, the paste icon re-inserts it at the cursor | repairs this entry; re-dictating is not needed |
| stop the same word coming out wrong | **Settings → Dictation → Custom dictionary** | "miro board" → "Miro board": phrases replaced on insert, hand-written |
| have the recognizer *hear* your names better | **Settings → Models → Vocabulary boosting** | hotwords bias the decoder before transcription (faster-whisper and openai-whisper; keep to about 20 words — long lists over-bias the decoder) |
| dictate differently into one app | **Settings → AI → Per-app prompts** | extra polish instructions for a matching app (needs AI polish on) |

The dictionary also learns from you: when you correct the same word in
History twice, the pair shows up as a **Suggested words** row on the
Dictation page (Settings → Dictation, below the dictionary) — and the
tray menu grows a **Dictionary suggestions (N)…** item that takes you
there. Nothing is added until you press **Accept**.

Which one do I want? The dictionary rewrites text *after* transcription;
hotwords bias the decoder *before*; per-app prompts change the AI
polish. A full survey of these surfaces (with click counts and what
each page hides) is in
[docs/research/2026-11-23-correction-discoverability.md](../research/2026-11-23-correction-discoverability.md).
