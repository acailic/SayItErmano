# Quickstart: the guided first-use setup

Part of the [documentation index](../README.md). One page, one command:
get from "just installed" to a **verified** first dictation.

## Run it

```bash
sayit-ermano --setup
```

The pass is interactive and takes a few minutes (most of it is the one
model download). It ends **green only** when a complete loop —
record → transcribe → insert — actually worked on your machine, or when
it hit a blocker it can name the exact fix for.

## What happens, step by step

1. **Hardware probe.** Your GPU (+ VRAM), CUDA readiness, CPU cores and
   RAM — the same probes `sayit-ermano doctor` uses — are printed, then
   grounded into the next step.
2. **Language.** You confirm the dictation language (ISO code like
   `en`, `de`, `es`, or `auto`).
3. **Model pick, with reasons.** Two or three candidates, each with a
   one-line *why* that cites the facts that produced it — e.g.
   *"CUDA ready on NVIDIA RTX 4070 (12 GB VRAM) — fits large-v3"* or
   *"no usable CUDA runtime, 8 CPU cores, 16 GB RAM — base decodes
   realtime on CPU"*. Non-English choices exclude English-only models.
   Press Enter to take the recommendation.
4. **Download with real progress.** Percent, size, transfer rate and
   ETA, streamed through the same verified (checksummed) download
   machinery the Models settings page uses. Already-downloaded models
   are skipped.
5. **Insertion-path check.** X11 (xdotool/xclip) or Wayland
   (portal/wtype/ydotool + wl-clipboard) — and when something is
   missing, the **exact install line for your distro** (e.g.
   `sudo apt install xdotool xclip`), plus the DE-shortcut notes on
   Wayland.
6. **The VERIFIED test take.** Focus any text field (the terminal
   running the setup works), press Enter, say something for two
   seconds. The pass records, transcribes, inserts the text — and only
   then declares you set up.

## When it ends on a blocker

Every failure state is explicit and actionable: no microphone (the
PipeWire/pulseaudio-utils install line), download failure (the retry
hint), no insertion path (the package line above), an empty or failed
take (what to check). Fix what it names and re-run `--setup` — it picks
up where the downloads left off.

For anything deeper: [`sayit-ermano doctor`](../dev/FAQ.md) is the full
environment report, [guides/install.md](install.md) covers the install
routes, and [guides/configuration.md](configuration.md) has every
setting the setup wrote for you.
