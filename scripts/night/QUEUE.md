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

- [ ] q7-x11-matrix | Execute the GNOME X11 desktop matrix, file the evidence | Read docs/dev/desktop-matrix.md and run every X11-runnable case live on this desktop (this session is X11, DISPLAY=:1): native text editor, terminal, browser text field, Electron editor if present; clipboard restoration, focus changes, absent insertion tools, compositor shortcuts where safe. Record per docs/dev/desktop-matrix.md: hardware, versions, source SHA, case counts, outcomes, logs. Write docs/research/night-<date>-desktop-matrix-x11.md; update the Q7 row in docs/dev/quality-queue.md with what this closes and what still needs Wayland/sway. Do NOT restart or reconfigure the production sayit-ermano daemon; dictation via its hotkey (Right_Control) is fine for exercising insertion. requests/wayland-matrix-execution.md stays OPEN unless its specified evidence is complete.
- [ ] q7-integration-evidence | Full integration tier once in the quiet window | Run `SAYIT_PY=/home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python just test-integration` from the worktree (real model/mic/GPU; tests use F9/F12, the live daemon uses Right_Control — no grab conflict; keep the machine otherwise idle). Expect ~40 tests with some capability skips (no mic input at night is fine — record it). File docs/research/night-<date>-integration-run.md: environment, per-test outcomes, junit summary (build/test-results/), failures with captured logs, implications for Q7's model half. Never restart the production daemon; if a test needs PipeWire capture and none answers at night, record the skip honestly.
- [ ] q9-soak | Bounded soak run against an ISOLATED daemon, evidence for Q9 | Read scripts/soak.py and docs/dev/release-gates.md soak sections. Run a ≤2 h soak with realistic take/reload/cancel/idle cycles against a daemon YOU spawn isolated in the worktree (own XDG root + SAYITERMANO_SOCKET + SAYITERMANO_CONFIG like tests/integration/conftest.py does; CPU tiny model is fine) — NEVER the production daemon. Collect memory trend after warmup, fds, threads, socket responsiveness, recovery after simulated device loss if the tool supports it. Write docs/research/night-<date>-soak-run.md with the CSV/log pointers and a verdict against the plan's Q9 acceptance (baseline-derived thresholds, unexplained growth blocks). Update the Q9 tracker row: 'local soak half: evidence <date>; VM install/upgrade half still open'.
