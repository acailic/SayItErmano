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

## Fixing mistakes: the correction loop

Dictation that mishears you should get cheaper to fix and rarer over
time. Four surfaces do that job — know where they live and the loop
closes itself:

- **History** — every take lands there even when insertion fails. Edit
  a transcript inline and the diff between what the recognizer heard
  and your correction becomes the dictionary-learner's signal
  (tray → **History**, or the app's main window). Retention controls:
  Settings → History.
- **Dictionary suggestions** — corrections seen twice become
  suggestions you can accept or dismiss:
  Settings → **Dictation** → *Suggested words* (bottom of the page).
  Hand-curated replacements live in the *Custom dictionary* group
  right above it.
- **Vocabulary boosting** — names and jargon the recognizer should
  BIAS toward (add, not replace): Settings → **Models** →
  *Vocabulary boosting*. Changing them reloads the speech engine.
- **Per-app prompt profiles** — named presets of the AI-polish prompt,
  bound either to an app (Settings → **AI** → *Per-app prompts*) or to
  one of the extra dictation shortcuts
  (Settings → **Dictation** → *Hotkeys*).

The settings pages cross-link these groups (the → chevrons), so a
wrong door has a signpost to the right one. Config keys for all four:
[`history`](configuration.md#history),
[`processing.dictionary`](configuration.md#processing),
[`model.hotwords`](configuration.md#model) and
[`ai.per_app_prompts`](configuration.md#ai) /
[`profiles.rules`](configuration.md#profiles) — see the
[configuration reference](configuration.md).

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
