# Flatpak permissions — per-finish-arg justifications (issue #11)

The shipping manifest is
[`packaging/io.github.acailic.SayItErmano.yml`](../../packaging/io.github.acailic.SayItErmano.yml);
its finish-args are exactly the ADR-0006 baseline — nothing else, and
`tests/test_flatpak_manifest.py` pins that set structurally so a stray
`--device=all` cannot slip in. This page is the draft justification
table for the Flathub submission PR, plus the documented opt-in
overrides we will NOT ship by default.

> **Submission note (Flathub generative-AI policy).** Flathub requires
> the submission PR, manifest and description text to be
> human-authored with no AI assistance. Treat every text block on this
> page as a working draft: the submitter rewrites the final PR text in
> their own words. The manifest itself must be reviewed word-by-word
> before submission for the same reason.

## Default finish-args

| Finish-arg | Why it is needed | What breaks without it |
|---|---|---|
| `--socket=wayland` | The GTK4/libadwaita UI on Wayland sessions; dictation input is the GlobalShortcuts portal (hotkey) and RemoteDesktop/EIS (insertion), both portal paths — no input sockets are requested (ADR-0006) | No window on GNOME/KDE Wayland; the X11 fallback below covers true X11 sessions |
| `--socket=pulseaudio` | Microphone capture via PipeWire's PulseAudio compatibility layer (the same capture path as the deb/apt build) | No audio: no dictation, no mic probe in `doctor` |
| `--device=dri` | GPU device nodes for whisper.cpp **Vulkan** (CPU int8 remains the default; on CPU-only hosts the nodes simply go unused) | Vulkan backend cannot initialize; falls back to CPU int8 (slower on GPU hosts) |
| `--share=network` | First-run model download from huggingface.co (`~/.var/app/…/cache/sayit-ermano/models`), release update checks, and the *user-configured* optional AI-polish endpoints | First run cannot fetch a model; update check and remote AI polish report offline |

Notably **absent** (each was considered and rejected for the default
manifest):

- `--socket=fallback-x11`, `--share=ipc` — X11 sessions are the
  legacy row of the ADR-0006 matrix; see the opt-in override below.
- `--device=all` — uinput/ydotool insertion is an opt-in override
  (reviewer pushback precedent: Vocalinux flathub PR #9368).
- `--socket=systembus`, `--talk-name=*` — nothing in the sandbox talks
  to system services; the portals ride the session bus, which flatpak
  always exposes.
- Any `--filesystem=` grant — settings/history/models all live under
  `~/.var/app/io.github.acailic.SayItErmano` (flatpak's redirected
  XDG dirs); nothing else on the host is read or written.

## GPU scope

- Default backend: whisper.cpp CPU int8.
- GPU path: whisper.cpp **Vulkan** via `org.freedesktop.Platform.GL`
  (the runtime GL extension, mounted at `/app/lib/GL` — the Blender
  manifest pattern). On NVIDIA hosts the extension provides the Vulkan
  ICD; AMD/Intel use Mesa from the runtime.
- **CUDA is out of scope for the Flatpak** — no verified Flathub
  precedent and a breakage history (GL.nvidia#184, Blender OptiX
  issues). faster-whisper with CUDA remains a host-build (deb/pipx)
  advantage, documented as such in the install guide.
- Known-issue policy: NVIDIA-extension regressions (driver update
  breaks Vulkan inside the sandbox) go to the Flathub known-issues
  section with the "remove the GL extension / force CPU int8"
  workaround.

## Opt-in, documented overrides (NOT in the default manifest)

| Override | Who it is for | How |
|---|---|---|
| `flatpak override --user --socket=fallback-x11 io.github.acailic.SayItErmano` (plus `--share=ipc`) | True X11 sessions wanting the XGrabKey+xdotool path (the ADR-0006 legacy row) | Xwayland ≥23.2 routes XTEST through the RemoteDesktop portal with a prompt; GNOME 49+ drops X11 sessions — prefer the portal paths |
| `flatpak override --user --device=all io.github.acailic.SayItErmano` (+ host udev setup for the user) | uinput/ydotool insertion where EIS is unavailable and wtype does not apply | Last rung of the ADR-0006 insertion ladder; requires host-side uinput permissions we cannot set from the sandbox |

## Lifecycle permissions actually exercised at runtime

- **Autostart**: `org.freedesktop.portal.Background.RequestBackground`
  (`fluidvoice/portal_autostart.py`) — the portal writes the host-side
  `~/.config/autostart/io.github.acailic.SayItErmano.desktop`
  (equivalent of the deb's `packaging/autostart.desktop`), one GNOME
  consent dialog, ask-once state in
  `~/.var/app/…/config/sayit-ermano/portal-state.json`. Where no
  Background backend exists (e.g. sway + xdg-desktop-portal-wlr), the
  manual fallback is a user-created autostart entry running
  `flatpak run --command=sayit-ermano io.github.acailic.SayItErmano app --background`.
- **Daemon ownership**: no systemd user unit inside the sandbox — the
  GTK app process spawns and supervises the daemon + control socket
  (`fluidvoice/gtkui/daemon_owner.py`); Settings/History windows talk
  to it over the in-sandbox Unix socket (`XDG_RUNTIME_DIR` is private
  to the app under flatpak).

## Submission-time TODOs (owner)

- Pin the `sayitermano` module source to the release tag + commit
  (the manifest tracks `branch: linux` until then).
- Refresh the metainfo `<releases>` list to the submitted version and
  add `<update_contact>`.
- First `.flatpakbundle` CI artifact: apply
  `packaging/flatpak/ci-flatpak-bundle.md` (the token-restricted run
  could not push the workflow file itself).
- Re-verify the whole justification table against the built sandbox on
  the X11 / GNOME Wayland / Plasma Wayland VM rows.
