# Installing and updating SayItErmano

Part of the [documentation index](../README.md). Routes: one-shot
installer, Ubuntu .deb, pipx, AUR, source — then updates and the
update-check switches.

### Quick start — one-shot installer

One download + one command, then SayItErmano appears in your app launcher,
autostarts at login, and needs no terminal. The default install is
**user-space and needs no sudo at all** (`~/.local/…` + a systemd user unit
that shadows any system unit); it only asks for sudo if a required system
package (GTK/pygobject, xdotool, …) is missing:

```bash
curl -fsSL https://raw.githubusercontent.com/acailic/SayItErmano/linux/scripts/install-one-shot.sh | bash
```

Prefer the classic system-wide .deb (root-owned, `/opt` runtime)?

```bash
curl -fsSL https://raw.githubusercontent.com/acailic/SayItErmano/linux/scripts/install-one-shot.sh | bash -s -- --system
```

### Ubuntu 24.04 — official .deb

The .deb is a **single-target package: Ubuntu 24.04 · x86_64 · Python 3.12**
(24.04 derivatives like Pop!_OS 24.04 work too). Its bundled runtime venv
uses the system Python, and the package declares
`Depends: python3 (>= 3.12), python3 (<< 3.13)` — so it installs cleanly
on the target and refuses to lie about compatibility. Other distros or
Python versions: use [pipx](#pipx--pip-any-distro-python-311) below or the
[AUR package](#arch-linux-aur).

```bash
curl -LO https://github.com/acailic/SayItErmano/releases/download/v0.8.1/sayit-ermano_0.8.1-1_amd64.deb
sudo apt install ./sayit-ermano_0.8.1-1_amd64.deb
```

Grab a specific version from the [releases page](https://github.com/acailic/SayItErmano/releases).
Building it yourself: release artifacts come out of a **pinned Ubuntu 24.04
container** with a **committed, hash-lockable dependency set** —

```bash
docker build -t sayit-ermano-deb -f packaging/deb/Dockerfile .
mkdir -p dist && docker run --rm -v "$PWD/dist:/out" sayit-ermano-deb
```

(or `./packaging/build-deb.sh` directly on an Ubuntu 24.04 / Python 3.12
host — it refuses to run anywhere else). See
[packaging/deb/README.md](../../packaging/deb/README.md) for the deb contract,
the pinned environment, and the reviewed `constraints.txt` lock.

**What you get after install** (log out/in once):

- **App launcher entry "SayItErmano"** (opens the native app) with its own icon
- **Daemon autostarts at login** (XDG autostart; a systemd user unit is also
  provided: `systemctl --user enable --now sayit-ermano`)
- `sayit-ermano` available everywhere in PATH (`doctor`, `toggle`,
  `settings`, `history`, …)
- Removes cleanly with `sudo apt remove sayit-ermano` — upgrading from the
  pre-rename `fluidvoice-linux` package replaces it automatically; your
  config, history and downloaded models are kept

### pipx / pip (any distro, Python 3.11+)

The cross-distro route (the deb above is Ubuntu 24.04-only): works on any
Linux with Python 3.11+ — a pipx install lands under `~/.local/pipx` (or
`~/.local/share/pipx`) and never touches the system Python:

```bash
pipx install sayit-ermano          # from PyPI (publishing is manual — if the
                                   # latest release isn't on PyPI yet, use:)
pipx install git+https://github.com/acailic/SayItErmano.git@linux
```

Upgrades are one command (`sayit-ermano update` detects the pipx install
and prints exactly this):

```bash
pipx upgrade sayit-ermano
```

You can also install a locally built wheel (e.g. after `git clone` +
`uv build --wheel`): `pipx install ./dist/sayit_ermano-<ver>-py3-none-any.whl`.
Verify an install any time with `./scripts/verify-pipx.sh` (entry points,
data files, and the updater's install-method detection, in a sandbox).

Using the one-shot user install instead? Its bundled venv can be upgraded
directly (the exact line `sayit-ermano update` prints for that layout):

```bash
~/.local/share/sayit-ermano/venv/bin/pip install -U sayit-ermano
```

(re-running the one-shot installer is the fully supported path — it also
restarts the daemon and cleans up duplicate installs.)

### Arch Linux (AUR)

A native package, [`sayit-ermano`](https://aur.archlinux.org/packages/sayit-ermano),
source-built against Arch's current Python (PEP 517 `python -m build` —
speech via the AUR
[`python-faster-whisper`](https://aur.archlinux.org/packages/python-faster-whisper)
package). The recipe and the one-command publish script live in
[`packaging/aur/`](../../packaging/aur/). If the package page does not exist
yet, the first push is still pending an AUR SSH key —
`packaging/aur/publish.sh` finishes it. (An earlier `sayit-ermano-bin`
recipe that repackaged the Ubuntu deb was never published and is
superseded.)

### From source (development)

```bash
git clone https://github.com/acailic/SayItErmano.git -b linux
cd SayItErmano
./scripts/install.sh          # apt deps + venv (reuses your CUDA torch if present)

# run it (foreground; systemd unit in systemd/)
.venv/bin/sayit-ermano daemon
```

Press **Right Ctrl**, speak, press **Right Ctrl** again. Done.

Useful commands:

```bash
sayit-ermano app               # native GTK app: History, Settings, onboarding
sayit-ermano doctor            # environment check
sayit-ermano toggle            # CLI trigger (bind to a DE shortcut on Wayland)
sayit-ermano cancel            # abort a recording
sayit-ermano language          # cycle the dictation language (language_cycle)
sayit-ermano transcribe x.opus --json   # one-shot file transcription
sayit-ermano history -n 10
sayit-ermano config init       # write ~/.config/sayit-ermano/config.toml
sayit-ermano update            # check for a newer release + print the upgrade command
```

### Requirements

- **X11**: the full experience (global hotkey grab + xdotool typing + the
  pill preview). **Wayland is supported** since v0.3 — see the
  [Wayland guide](wayland.md) for the capability matrix and tool setup;
  you need `wtype` or `ydotool` for text insertion and a
  desktop-environment custom shortcut for the hotkey (Settings → Wayland
  assists with both).
- Python 3.11+ for pipx/source installs (tested 3.12); the .deb bundles
  and requires Ubuntu 24.04's Python 3.12. Also `pipewire` (`pw-record`),
  `xdotool`, `xclip`, `libnotify-bin`, `pulseaudio-utils` (sounds).
- A whisper model is downloaded on first use (~75 MB tiny … ~3.1 GB large-v3;
  default `small` ≈ 484 MB, or `base` on CPU). For the whisper.cpp backend,
  the curated GGUF models are one-click downloads in Settings → Models.
- GPU is optional: faster-whisper uses CUDA automatically when cuBLAS 12 +
  cuDNN 9 are resolvable; otherwise it falls back to CPU int8.

## Updates

SayItErmano checks GitHub **once per daemon start and once a day** for a
newer release (10 s timeout, on a background thread — startup is never
delayed). When a newer release is seen you get **one desktop
notification**; `sayit-ermano status`, the History window's status row,
Settings → About and `sayit-ermano doctor` show it too. Nothing is ever
installed automatically — run:

```bash
sayit-ermano update    # prints the exact copy-paste upgrade command for
                       # YOUR install method (deb dpkg -i / one-shot
                       # installer / pipx upgrade / git pull)
sayit-ermano update --dismiss   # stop the notification for this release
```

`doctor` also warns when a system deb (`/opt/sayit-ermano`) and a user
install (`~/.local/share/sayit-ermano`) coexist — the two-daemon hotkey
fight this project's lock file guards against at runtime.

### Disabling the checks

```toml
[updates]
check = false   # no GitHub probe at all (notify = false keeps checks, drops
                # only the desktop notification)
```

(`SAYITERMANO_SKIP_UPDATE_CHECK=1` does the same per-run.)

## Naming and the pre-rename package

The project, repo, package, command and env-var overrides
(`SAYITERMANO_CONFIG`, `SAYITERMANO_SOCKET`, `SAYITERMANO_API_KEY`, …) are
**SayItErmano** (`sayit-ermano`). Only the Python module keeps the upstream
`fluidvoice` naming on purpose — internals credit the port's origin.
Installing `sayit-ermano` replaces the pre-rename `fluidvoice-linux`
package and takes over its config (`~/.config/sayit-ermano/`), history
and downloaded models.
