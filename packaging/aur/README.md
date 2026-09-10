# AUR recipe — `sayit-ermano` (native source package)

Native **PEP 517 build against Arch's current Python** (plan P0.6): the
recipe builds the wheel from the tagged source tarball with
`python -m build --wheel --no-isolation` and installs it via
`python -m installer`. This replaces the earlier `-bin` approach that
repackaged the official release `.deb` — that deb is now a
**Ubuntu 24.04 / x86_64 / Python 3.12-only artifact by contract**, so
repackaging it for Arch would smuggle in a foreign Python. The speech
stack is mapped to distro packages instead: **`python-faster-whisper`
(AUR)** pulls ctranslate2/onnxruntime/av through its own dependency chain;
the app wheel itself is pure Python.

Published by the maintainer via **`publish.sh`** (one command; needs an AUR
account + SSH key registered at <https://aur.archlinux.org/account/>). The
project rule stays *manual releases only* — no CI/publish automation; each
release bumps the recipe and a human runs the script.

## Dependency mapping

| Runtime need | Arch package |
|---|---|
| Speech engine (faster-whisper + ctranslate2/onnxruntime/av) | `python-faster-whisper` (AUR) |
| X11 hotkey/insertion helpers | `python-xlib` |
| Image/overlay assets | `python-pillow` |
| GTK app + tray | `python-gobject`, `gtk4`, `libadwaita` |
| Capture / typing / clipboard / notifications | `pipewire-audio-utils`, `xdotool`, `xclip`, `libnotify` |
| Wayland extras | optdepends: `wtype`, `ydotool`, `wl-clipboard` |
| evdev push-to-talk | optdepends: `python-evdev` |

Everything the wheel declares beyond these resolves to packages already in
the chain (tqdm/huggingface-hub/numpy/… via `python-faster-whisper`).

## Publishing (or updating after a release)

1. Bump `pkgver` (and `pkgrel` after recipe-only changes).
2. Fill `sha256sums` from the release page digest — or run `updpkgsums`,
   or `makepkg -g >> PKGBUILD`. `SKIP` must not survive an actual
   submission (it ships in-repo only because the recipe is unadopted).
3. Mirror the same values into `.SRCINFO` — on the dev box (no makepkg)
   it is hand-edited; regenerate with
   `makepkg --printsrcinfo > .SRCINFO` on an Arch box when possible.
4. `./publish.sh` — clones the AUR repo, copies `PKGBUILD` + `.SRCINFO`,
   commits and pushes; no-op when already current.
5. Lint when available: `namcap PKGBUILD` and `namcap <built .pkg.tar.*>`.

## The superseded `-bin` recipe (rename note)

The previous `sayit-ermano-bin` recipe (deb repackaging) was **never
published to the AUR**, so there is no old package to merge or delete. If
a `sayit-ermano-bin` git repo does exist under your AUR account (e.g. a
prepared but unpushed attempt), delete it on the AUR web interface or file
a deletion request — do not leave both live, and let `provides/conflicts`
in this recipe handle upgrades from any stray install.

## Notes for reviewers/maintainers

- **Layout parity with the deb:** desktop entry, `/etc/xdg/autostart`
  (the dictation daemon must run per session for the global hotkey —
  remove that file or mask the user unit if you autostart it yourself),
  hicolor icons, and the systemd **user** unit. The `/usr/bin/sayit-ermano`
  entry point comes from the wheel itself (PEP 517 console script).
- **`--no-isolation`** keeps the build offline: everything it needs is in
  `makedepends` (`python-build`, `python-installer`, `python-wheel`,
  `python-setuptools` — setuptools ≥ 68 per pyproject `build-system`).
- **No `check()`** by design: the upstream suite needs pytest plus
  PipeWire/X11/GPU fixtures and model downloads, which makepkg cannot
  provide; upstream CI covers it per release. (Clean-chroot verification
  of the recipe itself is tracked as platform polish.)
- `sayit-ermano doctor` warns when a *user* install coexists with this
  package (two daemons fight over the XGrabKey hotkey). The app's own
  `sayit-ermano update` prints a `pacman -Syu sayit-ermano` hint on Arch.

Back to the main README's install section: [`README.md`](../../README.md).
