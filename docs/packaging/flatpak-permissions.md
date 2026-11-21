# Flatpak permissions — justifications, overrides, scope

The reviewer-facing companion to
`packaging/io.github.acailic.SayItErmano.yml` (issue #11, ADR-0006).
Every `finish-arg` of the default manifest is justified here in the
shape the Flathub submission PR needs; the opt-in overrides below are
documented user choices, NOT defaults. Structural invariants are
pinned by `tests/test_flatpak_manifest.py` — changing the baseline
means changing the ADR first.

## Default finish-args (the ADR-0006 baseline, exactly)

| Permission | Value | Justification (submission-PR text) |
|---|---|---|
| Socket | `wayland` | The app is a GTK 4/libadwaita application: it renders on the Wayland display. Input — the dictation hotkey (GlobalShortcuts portal) and text insertion (RemoteDesktop portal + EIS) — goes through the freedesktop portals on the session bus, which needs no extra permission; the compositor owns all grabs. |
| Socket | `pulseaudio` | Microphone capture for dictation (PipeWire/PulseAudio). Recording is the app's core function; the portal camera/`--device` paths do not cover audio input. |
| Device | `dri` | GPU device nodes for whisper.cpp's Vulkan backend (CPU int8 works without it). NVIDIA users get Vulkan through the `org.freedesktop.Platform.GL.nvidia-*` runtime extension — the Blender-manifest pattern; no CUDA in the sandbox (no verified Flathub precedent, breakage history: GL.nvidia#184, Blender #110298 — CUDA remains a host-build advantage). |
| Shared | `network` | First-run speech-model download (Hugging Face/GitHub releases, into `~/.var/app/$APPID/cache`) and the release update check. No telemetry; the check is disableable (`SAYITERMANO_SKIP_UPDATE_CHECK=1`). |
| Env | `SAYITERMANO_FLATPAK=1` | Not a permission: flags the sandbox so the GTK app process owns the daemon + control socket (no systemd user units exist inside — ADR-0006). |

What is deliberately **absent** from the default manifest:

- `--device=all` — uinput (`ydotool`) text insertion is a fallback of
  last resort; the sanctioned insertion path is the RemoteDesktop
  portal. Reviewer pushback precedent: Vocalinux flathub PR#9368.
- `--socket=fallback-x11` / `--share=ipc` — X11 sessions are legacy
  (GNOME 49+ drops them); Xwayland ≥23.2 routes XTEST through the
  RemoteDesktop portal anyway. X11 users can opt in (below).
- `--talk-name=org.kde.StatusNotifierWatcher` — tray icon support;
  the app is fully usable from its window and the hotkey without it.

## Opt-in overrides (documented, not defaults)

Users apply these with
`flatpak override --user <flag> io.github.acailic.SayItErmano`:

| Override | Unlocks | Caveat |
|---|---|---|
| `--socket=fallback-x11 --share=ipc` | X11 sessions: XGrabKey hotkey + `xdotool` insertion | Xwayland ≥23.2 proxies XTEST via the RemoteDesktop portal (consent prompt); true X11 sessions only |
| `--talk-name=org.kde.StatusNotifierWatcher` | StatusNotifier tray icon | Grants talking to a non-hardened host service (impersonation risk class) — that is why it is not default |
| `--device=all` | uinput via `ydotool` insertion on compositors without a RemoteDesktop/EIS backend | Exposes all devices incl. evdev PTT without the `input` group; last-resort only |

## Daemon lifecycle inside the sandbox

- No systemd user units exist in the sandbox: the GTK app process owns
  the daemon (`fluidvoice/daemon_owner.py` starts `sayit-ermano
  daemon` detached when the control socket is dead; the daemon's flock
  singleton resolves double-start races).
- Login autostart replaces the deb's unit via the **Background
  portal**: Settings → General → "Start dictation at login" calls
  `RequestBackground(autostart=true, commandline=["sayit-ermano",
  "daemon"])`; the desktop's backend writes
  `~/.config/autostart/io.github.acailic.SayItErmano.desktop` on the
  host (`Exec=flatpak run --command=sayit-ermano
  io.github.acailic.SayItErmano daemon`). On desktops without a
  Background backend the Settings row reports the failure and the
  manual fallback is the desktop's own autostart editor with that
  same command. `packaging/autostart.desktop` stays the deb-side
  reference.
- Settings/History talk to the daemon over the in-sandbox control
  socket (`$XDG_RUNTIME_DIR/sayit-ermano.sock`), same as host installs
  (`fluidvoice/gtkui/client.py`).

## Data locations

Flatpak redirects `XDG_DATA_HOME`/`XDG_CACHE_HOME`/`XDG_CONFIG_HOME`
under `~/.var/app/io.github.acailic.SayItErmano/`; the app follows
those env vars (`fluidvoice/paths.py`), so config, history, audio and
downloaded models (`~/.var/app/<id>/cache/sayit-ermano/models/`) land
inside the per-app home and survive restarts. `flatpak uninstall
--delete-data` removes them; nothing is written outside the sandbox.

## Engine scope (what is and is not vendored)

- Vendored as sha256-pinned wheels (build sandbox has no network):
  `python-xlib`, `pillow` (runtime), `dbus-next`,
  `global-shortcut-portal` (portal input, ADR-0006).
- **Not vendored in this slice** — the speech engines: the
  `faster-whisper` wheel chain (ctranslate2/onnxruntime/av binaries)
  and a `whisper.cpp` Vulkan build. The GPU decision (ADR-0006) is
  whisper.cpp Vulkan on `--device=dri` with CPU int8 default; landing
  the engine module is the remaining slice of #11 before the Flathub
  submission (#12). Until then the in-sandbox remote-STT backend
  (`remote_url`) works with no engine at all.
- Sounds ship as Python package data (`fluidvoice/assets/sfx/`),
  not as `/app/share/sounds` — the deb does the same via the wheel.

## Known issues to carry into the submission

- NVIDIA runtime-extension regressions tracked in the Flathub
  `org.freedesktop.Platform.GL.nvidia-*` queue; Vulkan breakage
  reports go there, not to app bugs.
- sway/wlroots and COSMIC have no GlobalShortcuts backend (ADR-0006
  matrix): the DE-shortcut `sayit-ermano --toggle` path is the
  documented fallback there.
