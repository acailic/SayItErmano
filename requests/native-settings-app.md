Plan the implementation of the native GTK4 + libadwaita settings & history app that replaces the web UI, exactly as designed in the approved spec: a new `fluidvoice/gtkui/` package (GtkApplication `dev.fluidvoicelinux.FluidVoice` with a History main window, a Settings window, and an onboarding flow), daemon control-socket actions `get-config`/`set-config`/`select-model` with the webui validation layer moved into `fluidvoice/config.py` as `apply_settings()`, tray/CLI/first-run spawning the app, then deletion of `fluidvoice/webui.py` and the `server` config section.

STATUS: SHIPPED

<!-- shipped in 1ce8734 (+922584a, 3803787) -->

Where: `docs/superpowers/specs/2026-09-02-native-settings-app-design.md` is the authoritative design; the working tree carries uncommitted WIP (per-app prompts in `fluidvoice/processing/per_app.py` + MPRIS media pause in `fluidvoice/media.py`, wired through `fluidvoice/daemon.py`, `fluidvoice/config.py`, `fluidvoice/webui.py`, and tests) that the plan must absorb, not clobber; the suite runs with `.venv/bin/python -m pytest -q tests --ignore=tests/integration`.

Done means: a phased, file-level plan under `specs/` that a builder can implement without asking questions — each phase leaves the default test suite green, follows the spec's build order (shared validation + socket actions first, gtkui package second, webui removal + docs/packaging third), and adds unit tests for `apply_settings` and the new socket actions.

Out of scope: dictionary and filler-word list editors, history ZIP export, stats page, Parakeet/streaming models, Wayland insertion, upstream's `/v1` local HTTP API — the spec's out-of-scope list.
