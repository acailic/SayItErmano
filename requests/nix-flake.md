Plan the implementation of a Nix flake for SayItErmano - ROADMAP "Platform polish" ("native Nix flake"); today's routes are the one-shot installer, the Ubuntu 24.04 deb (ADR-0004), pipx, and the AUR recipe (packaging/), so Nix users have no first-class install and no dev shell.

STATUS: OPEN

Constraints that shape the work: the project is Python 3.11+
(pyproject.toml), depends on system GTK4/libadwaita + PyGObject (the deb
solves this with a --system-site-packages venv; Nix solves it natively
with python3.withPackages + gtk4/adwaita packages), bundles data files
(fluidvoice/assets, sfx, icons) through setuptools data packaging, and
calls external tools at runtime (pw-record, xdotool, xclip,
notify-send; wtype/ydotool on Wayland) - a working module must put
those on PATH or the doctor will honestly report them missing.

Scope:
1) flake.nix at the repo root exposing:
   - `packages.<system>.sayit-ermano` - the app (daemon + `sayit-ermano`
     CLI entry points) with the runtime tools on PATH, GTK/Adwaita +
     PyGObject linked, faster-whisper and friends as Python deps;
   - `devShells.default` - the contributor shell (ruff, pytest,
     pygobject, the test tier prerequisites) so `nix develop` gets a
     working `just test` on any distro;
   - a NixOS/home-manager module is NOT in v1 scope (see below).
2) Binary-size honesty: pick CUDA-off by default with an override flag
   (`withCuda ? false`) mirroring how the pipx route treats torch - the
   CPU int8 path is the default experience; document the trade-off.
3) nix flake check passes on x86_64-linux at minimum; the flake inputs
   nixpkgs pinned to a moving but reproducible lockfile.
4) CI lane (optional, decide in plan): a light `nix flake check` lane
   alongside the existing ones, manual-dispatch first like ci.yml's
   lanes.
5) Docs: docs/guides/install.md grows a "Nix / NixOS" route section
   (nix run / profile install / flake input); the README install
   paragraph links it; STATUS.md packaging paragraph mentions the flake.

Where: flake.nix (new), packaging/nix/ (module/support code if more
than one file), .github/workflows/ (only if the check lane lands),
docs/guides/install.md; pyproject untouched.

Done means: a phased plan a builder can implement without questions;
`nix build` produces a working `sayit-ermano daemon` on a Nix host
(verified live, evidence note like the deb/pipx runs), `nix develop`
yields a shell where the unit tier passes; docs updated; the AUR/deb
routes untouched.

Out of scope: a NixOS module/service option set, home-manager module,
bundling whisper models into the store path (first-use download stays),
macOS/darwin flakes, publishing to nixpkgs proper.
