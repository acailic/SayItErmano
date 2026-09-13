# Night-work queue

Curated by the human; consumed by `~/.pi/demons/night-demon.sh` (cron,
01:00, ~5.5 h budget, per-item cap 150 min). One line per item:

    - [ ] <id> | <title> | <what the session should do>

The demon skips items whose `~/.pi/demons/state/night/done-<id>` exists.
Check the box only after the morning review merged (or rejected) the
`agent/night/<date>-<id>` branch. Reports land next to the state files.

Status check 2026-09-12: per the plan's Implementation log, Q1–Q7 (code
half), Q11 and Q12 are SHIPPED. What still needs machine time — not more
code — is exactly the overnight-friendly half: live desktop evidence
(Q7 external), a full integration-tier run (Q7 model half), and soak
windows (Q9). The night is also the tripwire-quiet window (no live
dictation racing the production-data guard).

- [x] q7-x11-matrix | Execute the GNOME X11 desktop matrix, file the evidence | MERGED 2026-09-13 (morning review, ff-only 4f49f85+76460ab into linux, gate green 3453/4): Matrix 4 + Matrix C X11 half evidenced, findings F-31..F-36 filed. Wayland/sway/KDE cells remain open — see docs/research/night-2026-09-13-desktop-matrix-x11.md.
- [x] q7-integration-evidence | Full integration tier once in the quiet window | DONE as part of the 2026-09-13 night session (same branch): 32 passed / 8 honest skips / 0 failed, 172.75 s, junit at build/test-results/. Evidence: docs/research/night-2026-09-13-integration-run.md. GPU-real-model evidence still needs a window with the production daemon paused — re-open a queue item only for that if wanted.
- [ ] browser-paste-cells | Rerun the browser/Electron paste matrix cells on the F-34/F-35 fix | The 2026-09-14 day session shipped the paste-verify redesign (33431b2+023c44d, ledger F-34/F-35 FIXED): gedit + gnome-terminal cells PASSED live, Firefox landed in bisect runs, but the full Firefox/Chromium/Discord cells could not run - the desktop was in live use and windows were closed under the harness. Rerun them overnight with the method from the ledger F-35 row: probe page ~/fv-paste-cell.html (textarea at window y~220, click to focus, do NOT rely on autofocus/title mirror - use the AT-SPI readback), full DEFAULTS cfg with insertion.mode=paste (a minimal cfg breaks terminal detection - see the day session's driver lesson), marker on the clipboard before each insert to catch stale-clipboard inserts, assert payload exactly once + clipboard restored. Append the outcomes to the F-34/F-35 ledger rows. Do NOT touch the production daemon; F9/F12-free direct insertion API calls only.
- [ ] q9-soak | Bounded soak run against an ISOLATED daemon, evidence for Q9 | Read scripts/soak.py and docs/dev/release-gates.md soak sections. Run a ≤2 h soak with realistic take/reload/cancel/idle cycles against a daemon YOU spawn isolated in the worktree (own XDG root + SAYITERMANO_SOCKET + SAYITERMANO_CONFIG like tests/integration/conftest.py does; CPU tiny model is fine) — NEVER the production daemon. Collect memory trend after warmup, fds, threads, socket responsiveness, recovery after simulated device loss if the tool supports it. Write docs/research/night-<date>-soak-run.md with the CSV/log pointers and a verdict against the plan's Q9 acceptance (baseline-derived thresholds, unexplained growth blocks). Update the Q9 tracker row: 'local soak half: evidence <date>; VM install/upgrade half still open'.
