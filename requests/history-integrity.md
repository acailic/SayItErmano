History integrity - the test suite must not write into the live history database, and the stats features must stop counting test rows. Measured 2026-09-04 on the daily-driver machine: 724 of 738 entries in ~/.local/share/sayit-ermano/history.jsonl are command-mode TEST rows (commands "true 1", "true 2", "exit 3", purpose "fail"/"p", duration_ms 0-1) written by the suite across 2026-09-03/04. Result: `sayit-ermano status` printed "today: 333 dictations" when at most 1 was real; the History window list, the today header line, `history --export` ZIPs, and dictionary auto-learning counts all consumed polluted data.

STATUS: SHIPPED

<!-- shipped in 634dbca -->

Today: tests/conftest.py isolates XDG_CONFIG_HOME (so tests coexist with the live daemon's config/lock) but NOT the data dir - fluidvoice/paths.py resolves history/audio under ~/.local/share/sayit-ermano unconditionally, so every test that appends history (command-mode tests are the heaviest writer: 724 rows) writes to production. There is no scrub path and no regression guard.

Scope:
1) Isolation: extend the tests/conftest.py fixture layer so the data dir (history.jsonl, audio dir, dictionary-suggestions.json - everything under the paths.py data root) points at a tmp_path per test session; audit every test that imports or monkeypatches paths (grep tests/ for "paths." and "history") and route them through the fixture; keep XDG_CONFIG_HOME isolation as-is.
2) Regression guard: one test asserting that a full suite run leaves the REAL data files untouched - stat the real history.jsonl before/after a representative append-heavy test module under the isolated env; fails if mtime/size drift (this is the test that would have caught the leak).
3) One-time cleanup: a maintenance subcommand `sayit-ermano history --scrub-tests` that removes entries matching the test fingerprint (mode == "command" AND command in {"true 1","true 2","exit 3"} - exact set from the live file, kept as a constant, not a pattern that could match real commands) with a dry-run default, `--yes` to apply, and a backup copy written beside the file before mutation (history.jsonl.bak-<ts>). Manual run on this machine after merge; the tool ships so any other polluted install can clean itself.
4) doctor: add a history sanity line - entry count, file size, oldest entry date, and a warning count of test-fingerprint rows currently present (0 after scrub).

Where: tests/conftest.py (data-dir isolation), fluidvoice/paths.py (no behavior change - only if the fixture needs a seam, prefer env/monkeypatch over new code), fluidvoice/cli.py (history --scrub-tests subcommand), fluidvoice/history.py (scrub helper: filter + atomic rewrite reusing the existing tail/cap logic), fluidvoice/doctor.py (line), tests/test_history.py (scrub dry-run/apply/backup/no-match-negative), tests/test_conftest_isolation.py (new: the regression guard).

Done means: a phased plan under specs/ where each phase leaves `.venv/bin/python -m pytest -q tests --ignore=tests/integration` green; a fresh suite run against a decoy real-path file proves zero writes outside tmp; `history --scrub-tests` dry-run on a copy of the live file removes exactly the 724 test rows and 0 others; after the manual scrub on this machine `sayit-ermano status` today-line reflects only real dictations; doctor reports the history line with 0 test rows.

Out of scope: changing what the daemon writes (the leak is the test env, not the writer), schema changes to history.jsonl, touching the 5000-entry cap logic, any UI in the History window beyond it simply reading clean data, per-entry "test" flags or provenance fields.
