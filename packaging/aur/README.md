# AUR recipe — `sayit-ermano-bin`

Repackages the official release `.deb` asset (built by
[`packaging/build-deb.sh`](../build-deb.sh)) into an Arch package,
published by the maintainer via **`publish.sh`** (one command; needs an
AUR account + SSH key registered at
<https://aur.archlinux.org/account/>). The project rule stays *manual
releases only* — no CI/publish automation; each release bumps the
recipe and a human runs the script.

## Publishing (or updating after a release)

1. Bump `pkgver` (and `pkgrel` after recipe-only changes).
2. Update the `source_x86_64` URL: the deb asset of that release, e.g.
   `.../releases/download/v0.6.0/sayit-ermano_0.6.0-1_amd64.deb`
   (the `-1` is the deb's own packaging revision — copy it exactly from
   the release page; `sayit-ermano update` prints this URL for the
   current latest release).
3. Fill `sha256sums_x86_64` from the release page digest — or run
   `updpkgsums`, or `makepkg -g >> PKGBUILD`. `SKIP` must not survive an
   actual submission.
4. Mirror the same values into `.SRCINFO` (no `makepkg` on the dev box —
   hand-edit, or regenerate with `makepkg --printsrcinfo > .SRCINFO` on
   an Arch box).
5. `./publish.sh` — clones the AUR repo, copies `PKGBUILD` + `.SRCINFO`,
   commits and pushes; no-op when already current.
6. Lint when available: `namcap PKGBUILD` and `namcap <built .pkg.tar.*>`.

## Notes for reviewers/maintainers

- **Payload:** the deb installs `/opt/sayit-ermano/venv` (a bundled venv,
  so no pip/uv needed at install time), `/usr/bin/sayit-ermano`, desktop
  entries, hicolor icons, a systemd **user** unit, and
  `/etc/xdg/autostart/sayit-ermano.desktop`. The autostart entry is
  *intended* — the dictation daemon must run per session for the global
  hotkey. Remove that file (or mask the user unit) if you autostart it
  yourself. `sayit-ermano doctor` warns when a *user* install coexists
  with this package (two daemons fight over the XGrabKey hotkey).
- **data.tar flavor:** the release debs use `data.tar.zst`; `bsdtar`
  auto-detects it (hence the `zstd` dependency, and no pinned `-J`/`-z`
  flag). Verified with `ar t dist/sayit-ermano_*.deb`.
- **`--system-site-packages`:** the bundled venv is created with it so a
  CUDA torch already on the machine is reused; without it the app runs on
  CPU (faster-whisper int8).
- Upgrades inside the package: `sudo pacman -Syu` once adopted; the app's
  own `sayit-ermano update` will detect the `deb` install method and
  print the `dpkg -i`-style command — on Arch, prefer the AUR upgrade.

## Manual review note (no namcap/makepkg in this workspace)

`namcap` and `makepkg` were unavailable when this recipe was written
(Debian-based dev box); the manual review covered: correct `sha256sums`
handling (SKIP documented, never to be submitted), no unexpected
`/etc/xdg/autostart` surprise (documented as intended above), arch/deps
mapping mirroring the deb's `Depends:` line (plus `zstd` for the data
tarball), and `provides/conflicts/replaces` matching the pre-rename
`fluidvoice-linux` package this supersedes. The committed `.SRCINFO` was
hand-written to match — regenerate it with
`makepkg --printsrcinfo > .SRCINFO` on an Arch box when adopting.

Back to the main README's install section: [`README.md`](../../README.md).
