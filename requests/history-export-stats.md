Plan the implementation of the two deferred History features from the native-app spec's out-of-scope list (docs/superpowers/specs/2026-09-02-native-settings-app-design.md - "ZIP export, stats page" were deliberately deferred; command mode has since landed, these are next).

STATUS: SHIPPED

<!-- shipped in 412617f -->

1) History ZIP export: a one-click "Export…" action in the GTK History window (fluidvoice/gtkui/main_window.py) that packs history.jsonl + every retained audio file referenced by entries into a zip via Gtk.FileChooser (Native) dialog, written on the GLib thread with a busy state; plus a CLI equivalent `fluidvoice history --export <path.zip>` (fluidvoice/cli.py, fluidvoice/history.py: new `export_zip(path) -> int` that streams entries, resolves audio paths safely - only files inside paths.audio_dir() - and returns the entry count; missing audio files are skipped with a note, never crash). No HTTP, no daemon action needed - the app shares files directly.

2) Today-usage stats (upstream "Today-usage stats" row): compute from history entries - dictations today, minutes dictated, words transcribed (pure function in fluidvoice/history.py: `today_stats(entries) -> dict`, local-midnight boundary); surface as a compact stats line in the History window header (updates on refresh) and in `fluidvoice status` output as `today: N dictations, M:SS minutes, K words` (CLI + the daemon `status` socket action both get it).

Where: fluidvoice/history.py (export_zip, today_stats - pure, unit-testable), fluidvoice/gtkui/main_window.py (Export… button + stats line), fluidvoice/cli.py (history --export, status wording), fluidvoice/daemon.py (status action gains "today"). Suite: `.venv/bin/python -m pytest -q tests --ignore=tests/integration` (382 green at HEAD 49ef209).

Done means: a phased, file-level plan under `specs/` a builder can implement without questions - each phase leaves the suite green; unit tests for export_zip (entries + audio roundtrip, missing-audio skip, path-traversal refusal for entries whose audio points outside the audio dir) and today_stats (midnight boundary, empty history, word counting); GTK wiring smoke-covered like the existing window tests do it.

Out of scope: speaker labeling/diarization, chunked uploads, any HTTP API, ZIP of anything beyond history+audio, remote/cloud storage.
