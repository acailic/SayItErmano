# Troubleshooting FAQ

Part of the [documentation index](../README.md). The most common
problems, grouped, with concrete fixes. Sources are cited per entry:
`upstream #N` = the same product's users reporting it in the
[altic-dev/FluidVoice](https://github.com/altic-dev/FluidVoice/issues)
tracker (macOS/Windows); everything else cites this port's own verified
docs and code. This port has no issue tracker reports yet — nothing
here is a guess, and nothing here is invented: if it isn't documented
somewhere, it isn't in this FAQ.

**First stop, always:** `sayit-ermano doctor` — it prints the
per-tool found/missing matrix, the session capability matrix
(X11/Wayland), the default-source microphone probe, the hotkey-grab
state, the models cache with per-model disk usage, the live model
state, and update/duplicate-install warnings. `sayit-ermano status`
carries the same capability matrix as JSON.

---

## Audio — PipeWire / PulseAudio

### 1. Dictations come back empty / nothing is transcribed

Muted microphone, wrong input device, or a wedged PipeWire path
streaming exact zeros (real incident in this port: a healthy webcam
mic streamed digital silence after a route glitch — dictations came
back empty or hallucinated).

```bash
sayit-ermano doctor     # microphone section: records ~1.5 s from the
                        # default source and flags DIGITAL SILENCE
pactl info | grep 'Default Source'   # which mic is the system default
```

- Unmute the mic; pick the right device in **Settings → Dictation**
  (mic dropdown), or pin it in `config.toml`: `recording.device = "…"`
  (a PipeWire node target or PulseAudio source).
- If `doctor` reports **DIGITAL SILENCE** — it prints the fix itself:
  `systemctl --user restart wireplumber` (or reconnect the device).
- Watchdogs make this fail fast rather than hang: a source that sends
  no audio at all stops the take after `recording.first_pcm_timeout`
  (2 s default); pure digital silence is surfaced as a notice.
- Pure PulseAudio (no PipeWire) system: set `recording.command =
  "parecord"` (`auto` prefers `pw-record`).

*Sources: `doctor` microphone probe (`fluidvoice/doctor.py`,
2026-09-10 incident note); [configuration.md](../guides/configuration.md)
`[recording]`; [STATUS.md](../STATUS.md) recording watchdogs.*

### 2. The wrong microphone records me (webcam instead of headset)

Set the input device in **Settings → Dictation**, or in `config.toml`:

```toml
[recording]
device = ""   # "" = system default; or a source/node name
mic_priority = ["bluez", "usb-cam"]   # ordered, case-insensitive name
                                      # substrings; first match wins
```

When the configured device disappears (headset off), the daemon
switches to the first available `mic_priority` match and notifies you —
never mid-dictation.

*Sources: [configuration.md](../guides/configuration.md) `[recording]`;
[STATUS.md](../STATUS.md) mic priority fallback + input-device
monitoring.*

### 3. Recording stops while I'm just pausing to think

The trailing-silence VAD auto-stop ends a take after
`recording.preview_vad_silence_s` (default **2.0 s**) of quiet.
Disable it (the hotkey then stops every take) or widen the window:

```toml
[recording]
preview_vad_silence_s = 0     # 0 = off
```

Also check the other automatic stops: `recording.max_seconds` (300 s
hard cap) and `recording.skip_silent` (takes ≤ 4 s of pure silence
are skipped — a take you made while muted is dropped by design).

*Source: [configuration.md](../guides/configuration.md) `[recording]`.*

## Hotkeys

### 4. X11: the hotkey is dead / was working, now isn't

On X11 the hotkey is a global `XGrabKey` grab; another application
holding the same combo refuses it. This port's grabs are
**self-healing** — a refused combo re-arms and the condition is
surfaced in `sayit-ermano status`, the tray tooltip, and
`sayit-ermano doctor` (hotkey-grab line):

```bash
sayit-ermano doctor     # hotkey grab state
sayit-ermano status     # JSON, same listener state
```

Change the key if the conflict is permanent: `[hotkey] key = "F9"`
(any keysym; modifier-only keys like Right_Control need
`mode = "toggle"`). The upstream equivalent — hotkey stuck on
"Initializing…" after an OS update (upstream #1088) — has no Linux
analogue: there is no OS-level permission to grant on X11.

*Sources: [STATUS.md](../STATUS.md) hotkeys (self-healing grabs);
[configuration.md](../guides/configuration.md) `[hotkey]`; upstream
[#1088](https://github.com/altic-dev/FluidVoice/issues/1088).*

### 5. Wayland: the hotkey does nothing

Wayland compositors don't allow global key grabs — this is a protocol
property, not a bug. The hotkey becomes a desktop-environment custom
shortcut bound to the generated `sayit-ermano-toggle` script:

- **Settings → Wayland** prints the exact per-GNOME/KDE/COSMIC bind
  steps — `sayit-ermano doctor` prints the same matrix.
- Optional physical push-to-talk without a DE shortcut: evdev
  `hotkey.wayland_evdev` (privileged — `input` group +
  `pip install 'sayit-ermano[wayland]'`).

*Source: [wayland.md](../guides/wayland.md) capability matrix.*

## Text insertion (Wayland)

### 6. Text is not typed into any app on Wayland

Wayland needs a typing tool — `wtype` (wlroots/KDE; GNOME exposes no
virtual-keyboard protocol) or `ydotool` (any compositor, incl. GNOME):

```bash
sudo apt install wtype wl-clipboard        # sway/wlroots, KDE
sudo apt install ydotool wl-clipboard      # any compositor incl. GNOME
sudo systemctl enable --now ydotool        # ydotoold must run; /dev/uinput
```

`insertion.wayland_tool` = `auto | wtype | ydotool`. Note two more
Wayland facts from the same matrix: the live pill preview is
impossible on GNOME-Wayland (a notification-bubble preview is used
instead), and paste verification degrades to a fixed settle (no
cross-client observation exists on Wayland).

*Source: [wayland.md](../guides/wayland.md) — install block and
capability matrix.*

## Model download

### 7. The model download fails on a flaky network / seems stuck

The most-reported problem class in the upstream tracker: a 1–2 s
network drop kills the download and every retry restarts from zero
(upstream #1105, #1140), downloads stuck at a random percentage
(#1140), or losing progress when leaving Settings (#1035). This port
answers it with **atomic downloads**: a GGUF/ONNX download streams to
a `.part` file and is moved into place only when complete — a dropped
connection or a cancel discards the partial file, so **retrying is
always safe and never leaves a corrupt model**; progress is visible in
the first-use funnel and Settings → Models while it runs.

```bash
sayit-ermano doctor     # "models cache" section: what actually landed,
                        # per-model size, ACTIVE marker, disk total
```

- Retry after the network settles; pick a smaller model first
  (`tiny` ≈ 75 MB, `base`, `small` ≈ 484 MB — see next entry).
- faster-whisper models download through `huggingface_hub` (its
  `.incomplete` blobs); whisper.cpp GGUFs are one-click downloads in
  **Settings → Models**.

*Sources: `fluidvoice/model_download.py` (atomic `.part` contract,
"never a half-written state"); upstream
[#1105](https://github.com/altic-dev/FluidVoice/issues/1105),
[#1140](https://github.com/altic-dev/FluidVoice/issues/1140),
[#1035](https://github.com/altic-dev/FluidVoice/issues/1035).*

### 8. What gets downloaded on first use, and where does it live?

The first dictation downloads the speech model automatically: the
default is `small` (≈ 484 MB) on CUDA machines, `base` on CPU; the
range is `tiny` ≈ 75 MB … `large-v3` ≈ 3.1 GB. Models live in the
user cache — `~/.cache/sayit-ermano/models` — and `sayit-ermano
doctor` lists the directory, every cached model with its size, which
one is ACTIVE, and the total; Settings → Models can delete cached
models.

*Sources: [install.md](../guides/install.md) requirements; `doctor`
models-cache section.*

## GPU / CPU performance

### 9. How do I know whether the GPU is being used?

```bash
sayit-ermano doctor     # prints cuda_available() and, when present,
                        # nvidia-smi's GPU name + VRAM
```

faster-whisper uses CUDA **automatically** when cuBLAS 12 + cuDNN 9
are resolvable; otherwise it falls back to CPU int8 (that fallback is
normal, not an error). `model.name = "auto"` picks `small` under CUDA
and `base` on CPU. To force a lane:

```toml
[model]
device  = "cuda"      # or "cpu"
compute = "float16"   # or "int8"
```

CPU too slow? Use a smaller model (`model.name = "base"` or `"tiny"`)
— CPU int8 with `base` is the documented CPU default.

*Sources: [install.md](../guides/install.md) requirements;
[configuration.md](../guides/configuration.md) `[model]`; `doctor`
CUDA lines.*

### 10. The first dictation after a break is slow

With `model.idle_unload_s` set (Settings → Models → Memory), the
speech model is unloaded after the idle window to free RAM/VRAM; the
next dictation pays the model load again (a few seconds — the load
overlaps your speech). Check the live state with `sayit-ermano
doctor` (prints `model: loaded/unloaded (idle Xm; policy Ns)`), and
set `idle_unload_s = 0` — the default — to keep the model loaded
forever.

*Source: [configuration.md](../guides/configuration.md) "Why is my
first dictation slow after a break?".*

## Installation & dependencies

### 11. `xdotool` / `pw-record` / `xclip` / notifications missing

The runtime needs a handful of system packages that pip can't
provide:

```bash
sudo apt install pipewire xdotool xclip libnotify-bin pulseaudio-utils
```

The one-shot installer checks for these and asks for sudo only when
one is missing; `sayit-ermano doctor` prints the per-tool
found/missing matrix at any time.

*Sources: [install.md](../guides/install.md) requirements and
one-shot installer; `doctor` tool matrix.*

### 12. The .deb refuses to install on my distro

The deb is a **single-target package: Ubuntu 24.04 · x86_64 ·
Python 3.12** (declared `Depends: python3 (>= 3.12), python3 (<<
3.13)` — it refuses to lie about compatibility; 24.04 derivatives
like Pop!_OS 24.04 work). Every other distro: use pipx (any distro,
Python 3.11+) or the AUR package — all routes are in the
[install guide](../guides/install.md).

*Sources: [install.md](../guides/install.md) deb section;
[STATUS.md](../STATUS.md) known limitations (deb target).*

### 13. Hotkeys act possessed — two installs are fighting

A system deb (`/opt/sayit-ermano`) and a user install
(`~/.local/share/sayit-ermano`) can coexist after a partial
migration, and two daemons fight over the hotkey. `doctor` warns
about exactly this; the runtime lock file guards against it. Fix:
re-run the one-shot installer — it also restarts the daemon and
cleans up duplicate installs.

*Source: [install.md](../guides/install.md) updates section
(duplicate-install warning).*

## Output quality

### 14. Text is typed twice / a dictation is lost in the browser

Two open upstream reports: transcription pasted twice (#1138) and
text disappearing after the second hotkey press (#1144). This port's
answer shipped in **v0.8.2 — verified pasting**: paste mode proves
the target read the clipboard and the field got the text before the
clipboard is restored; the release notes name exactly these symptoms
— *duplicated transcripts in terminals and lost dictations in
browsers: fixed* (live-matrix evidenced). Terminal apps additionally
get the `ctrl+shift+v` paste key and a trailing autocomplete space
(`insertion.terminal_*`). If you see either symptom: update to
≥ v0.8.2 (`sayit-ermano update` prints your install method's command)
and keep `insertion.mode = "auto"`.

*Sources: [CHANGELOG.md](../../CHANGELOG.md) v0.8.2; upstream
[#1138](https://github.com/altic-dev/FluidVoice/issues/1138),
[#1144](https://github.com/altic-dev/FluidVoice/issues/1144).*

### 15. The transcript came out in the wrong language

Upstream report: a German meeting transcribed as English with no way
to redo it (#1033). This port has three documented levers:

```toml
[general]
language = "de"                       # pin instead of "auto"
language_cycle = ["auto", "en", "sl"] # runtime cycling via language_key
language_whitelist = ["sl", "en"]     # wrong-language guard: "auto"
                                      # detected outside the list → the
                                      # take re-decodes once with the
                                      # first entry
```

`sayit-ermano language` cycles at runtime; per-model overrides exist
(`model.languages`). `doctor` prints the live language state.

*Sources: [configuration.md](../guides/configuration.md) `[general]`;
upstream
[#1033](https://github.com/altic-dev/FluidVoice/issues/1033).*

---

Still stuck? `sayit-ermano doctor` first, then the
[guides](../README.md) (install, Wayland, configuration), then
[STATUS.md](../STATUS.md) for known limitations. This port takes bug
reports as GitHub issues in
[acailic/SayItErmano](https://github.com/acailic/SayItErmano) — the
`doctor` and `status` output belongs in the report.
