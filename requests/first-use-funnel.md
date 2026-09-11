# First-use funnel: download visibility + stranded insertion + guided transition (phase 1)

STATUS: SHIPPED

Shipped 2026-09-11 (wave 1, 3-agent team): F-02+F-19 in
`agent/f1-onboard` (visible download progress in onboarding/pill, safe
cancel, guided real-insertion final step with graceful degradation,
local-only funnel counters), F-05+F-18 in `agent/f1-insert` (insertion
pre-flight capability check, actionable missing-tool errors with install
command, clipboard fallback preserving the transcript, machine-readable
result). Suite 3310→3401 passed. Wayland/X11 live verification of the
funnel remains with the user tests + matrix brief.

Source: [phase 0 report](../docs/research/2026-09-11-phase0-report.md)
findings F-02, F-05, F-18, F-19 (siblings F-03, F-08, F-17 fold in). One
brief because they are one funnel: install → model ready → tryout → first
real insertion.

## Goal

A new user on a clean machine reaches useful text in their own app
without developer help: visible model download progress, honest errors
when tools are missing, and a guided final step from the onboarding
tryout to a real insertion.

## Requirements

1. **F-02 download visibility**: the first take/tryout during a model
   download shows progress and a working state — no invisible hang.
   Progress surfaces in onboarding UI and the pill; cancellation safe.
2. **F-05/F-18 stranded insertion**: when insertion tooling (xdotool et
   al.) is absent, the take is not silently stranded with a false
   success message — an actionable error offers the exact install
   command or a copy-to-clipboard fallback that preserves the text.
3. **F-19 guided transition**: onboarding's final step walks the user
   through one real dictation into an app of their choice, detecting
   success where possible and gracefully degrading to self-report.
4. Instrumentation hooks (opt-in, local-only counters — no telemetry):
   funnel step reached, minutes to first insertion, first failure
   point; these feed the phase-1 user tests' 8/10 gate.
5. Tests: failure-path unit tests with stubbed tooling/downloads; the
   live verification happens in the user tests, not CI.

## Out of scope

Model recommendation heuristics, languages beyond existing support,
Wayland-specific insertion work (matrix brief owns discovery).
