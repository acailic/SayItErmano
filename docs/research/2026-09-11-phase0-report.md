# Phase 0 report — baseline, ledger, and five findings

- Date: 2026-09-11
- Status: COMPLETE (phase 0 of
  [the product-excellence and monetization plan](2026-09-11-product-excellence-and-monetization-plan.md))
- Executed by: 3-agent parallel team on isolated worktrees
  (`agent/p0-baseline`, `agent/p0-ledger`, `agent/p0-evalspec`), merge-gated
  into `linux`; post-merge verification: ruff clean,
  `3310 passed, 4 skipped, 0 warnings` (`-W error`) on the merged tree.

## Artifacts

| Deliverable | File |
|---|---|
| Verified offline baseline + env + deltas | [phase0-baseline-run.md](2026-09-11-phase0-baseline-run.md) |
| Finding ledger (ranked, evidence-tagged) | [phase0-finding-ledger.md](2026-09-11-phase0-finding-ledger.md) |
| Install→first-insertion + take-lifecycle maps | [phase0-journey-map.md](2026-09-11-phase0-journey-map.md) |
| Real-speech corpus specification | [../eval/corpus-spec.md](../eval/corpus-spec.md) |
| Desktop/app support matrix (extends wayland-smoke-matrix) | [../dev/desktop-matrix.md](../dev/desktop-matrix.md) |
| Moderated user-test script | [../dev/user-test-script.md](../dev/user-test-script.md) |

Roadmap reconciled with shipped work in the same wave
(`docs/ROADMAP.md`): program-complete engineering items cleared; the
plan-blocked backlog (diarization/streaming/n-best) retained.

## Baseline in one paragraph

Current truth on `f74c924`+merges (not historical claims): offline suite
3310 passed / 4 skipped / 0 warnings in ~92 s serial (28.9 s `-n auto`),
ruff clean under both canonical and system versions, request validator
28/28 SHIPPED. Everything else remains honestly unverified offline: live
Wayland/X11 matrices, real-model speech quality/latency, deb
build/upgrade reproducibility, and 38 collected-only integration tests.
Two runner hygiene anomalies recorded in the baseline doc (suite process
outliving the run; a `NoneType` transcription error line from a leaked
daemon path) — promoted to findings, not fixed here.

## The five highest-impact findings (coordinator synthesis)

Ranked per the plan (safety/data-loss first — none is active data-loss;
the closest, F-05, strands-but-keeps the text in History):

1. **F-26 Live Wayland + context matrices are NOT DONE** — blocks honest
   Wayland positioning, the P2 payoff, the context default flip, and the
   "≥99% dependable insertion" gate on half the target desktops. The
   runbook already exists; execution needs a Wayland session, not code.
2. **F-02 First-use model download is invisible and can hang the first
   take/tryout** — the first-use funnel (bet #1, the 8/10 setup gate)
   dies here on any non-instant network; pair with sibling F-19.
3. **F-14/F-13 No real-speech measurement instrument** — the synthetic
   corpus makes WER/CER, guard FP/FN, VAD false-stops and the whole
   "trustworthy speech" gate unfalsifiable; gates phase-2 prioritization.
4. **F-05 (+F-18, F-08, F-17) Missing-tool install path strands the
   first insertion with a wrong message** — core promise fails on first
   use for user-space installs; small effort, outsized trust payoff.
5. **F-19 Tryout → real-insertion transition untested and untaught** —
   the exact step bet #1 names; drop-off measurement is user-test
   question #1; guided final step is the likely fix after 2 and 4 stop
   eating users first.

## Selected work promoted to OPEN request briefs

Only these three (frequency × severity × reach × confidence ÷ effort):

1. [wayland-matrix-execution](../../requests/wayland-matrix-execution.md) —
   run the existing live matrices; produces the compatibility evidence
   F-26 blocks (no product code).
2. [first-use-funnel](../../requests/first-use-funnel.md) — F-02 + F-05 +
   F-19 + F-18 as one funnel fix with instrumentation hooks for the
   phase-1 user tests.
3. [speech-corpus-recording](../../requests/speech-corpus-recording.md) —
   recruit + record + validate the corpus per
   [the spec](../eval/corpus-spec.md); wires the measurement adapters gap
   list into follow-ups.

Runner hygiene anomalies (leaked processes / NoneType line) stay in the
ledger as engineering-pool items, not product briefs.

## Decisions the maintainer owes next

- Confirm the initial buying audience hypothesis (Linux professionals,
  local-first, one-time purchase) or amend after phase-1 interviews.
- Grant Wayland test-machine access for brief 1; without it F-26 stays
  open indefinitely.
- Approve recording consent text + storage location for brief 3.

Phase 1 (whole-product investigation) can start against this baseline as
soon as briefs 1–3 have owners; the user-test script and desktop matrix
are ready to schedule.
