# Documentation index

One page per question. Everything under `docs/` is reachable from
here; the repo-root [README](../README.md) stays the front door for
the app itself.

## I use SayItErmano

- [README](../README.md) — what it is, install, first dictation,
  configuration overview.
- [STATUS.md](STATUS.md) — what works today, with evidence, and the
  known limitations.
- [ROADMAP.md](ROADMAP.md) — everything not built yet.
- [glossary.md](glossary.md) — domain terms (dictation, capture,
  insertion, context…).
- [screenshots/](screenshots/) — the UI, current and historical.

## I want to contribute or change the code

- [../AGENTS.md](../AGENTS.md) — working rules: one tree per agent,
  how tests are run, merge-back policy. Read this first.
- [adr/](adr/) — locked architecture decisions ([index](adr/README.md)).
  Non-goals live here (no TCP, no telemetry, Linux only).
- [BEHAVIOR-SPEC.md](BEHAVIOR-SPEC.md) — what the upstream macOS app
  does, with file:line evidence, and where this port differs.
- [dev/release-gates.md](dev/release-gates.md) — the local gates and
  the release path (nothing ships without them).
- [dev/quality-queue.md](dev/quality-queue.md) — historical Q1–Q12
  tracker; superseded by the quality plan's implementation log.
- [dev/context-seam.md](dev/context-seam.md) — the ContextProvider
  seam and its privacy invariants.
- [dev/e2e-sandbox.md](dev/e2e-sandbox.md) — running the end-to-end
  sandbox.

## I maintain / release the project

- [quality/evidence-index.md](quality/evidence-index.md) — every
  verified claim and its run record.
- [../requests/](../requests/) — the brief ledger; a request's STATUS
  header is the authority on whether it shipped.
- [plans/](plans/) — active plans, each with a Status header:
  [organization plan](plans/2026-09-12-organization-and-improvement-plan.md),
  [product-excellence plan](plans/2026-09-11-product-excellence-and-monetization-plan.md).
- [plans/archive/](plans/archive/) — completed or superseded plans and
  the historical ADW specs.
- [dev/wayland-smoke-matrix.md](dev/wayland-smoke-matrix.md),
  [dev/desktop-matrix.md](dev/desktop-matrix.md) — the live session ×
  application matrices (the standing evidence gaps).

## I want the evidence

- [research/](research/) — audits, reviews, run records, night-matrix
  evidence, coverage baseline; raw data in [research/data/](research/data/).
- [eval/](eval/) — the real-speech corpus spec and consent text,
  decode baselines, manifest schema ([eval README](eval/README.md)).
- [COMPARISON.md](COMPARISON.md) — vs. other Linux dictation tools.
- [UPSTREAM-TRACKING.md](UPSTREAM-TRACKING.md) — macOS-vs-Linux
  capability matrix and the upstream changelog refresh loop.
