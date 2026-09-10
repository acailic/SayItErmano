Review the native GTK settings & history app as shipped — the full arc, not just the residuals in `previous_envelope`: commits `1ce8734` (web UI replaced by `fluidvoice/gtkui/`, control-socket actions `get-config`/`set-config`/`select-model`/`mics`, `apply_settings` in config, tray/CLI/onboarding spawning, `webui.py` + `[server]` deleted), `922584a` (settings v1.1: dictionary/filler editors, language picker), and `3803787` (gap-closure: About backend/CUDA rows, mics/rollback tests, doc retirement).

STATUS: SHIPPED

<!-- shipped in 3803787 -->

Where: the spec is `docs/superpowers/specs/2026-09-02-native-settings-app-design.md` plus the plan at `context_handoff/plan.md`; the code is `fluidvoice/gtkui/`, `fluidvoice/config.py` (`apply_settings`), `fluidvoice/daemon.py` (socket actions), `fluidvoice/tray.py`, `fluidvoice/cli.py`, with tests in `tests/test_config_settings.py`, `tests/test_daemon.py`, `tests/test_gtkui.py`, `tests/test_gtkui_client.py`; the gates are `.venv/bin/python -m pytest -q tests --ignore=tests/integration` (331 passed) and `tests/integration/test_daemon_socket.py` (6 passed).

Done means: a ruling on every spec requirement with file:line evidence, written to `context_handoff/review.md`; `approved` is true only when nothing blocking remains.

Out of scope: roadmap items beyond the spec (Parakeet/streaming, Wayland insertion, `/v1` local HTTP API, ZIP export, stats) and the factory machinery itself (`adws/`, `.claude/`, `requests/`, `justfile`).
