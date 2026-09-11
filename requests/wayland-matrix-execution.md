# Wayland matrix execution (phase 1 evidence)

STATUS: OPEN

Source: [phase 0 report](../docs/research/2026-09-11-phase0-report.md)
finding F-26. The runbook already exists:
[wayland-smoke-matrix.md](../docs/dev/wayland-smoke-matrix.md), extended by
[desktop-matrix.md](../docs/dev/desktop-matrix.md). This brief executes it —
no product code changes.

## Goal

Produce the REQUIRED-BEFORE-PARITY evidence: dictation (capture →
transcribe → context read → insertion) verified live on GNOME Wayland,
sway, and the X11 control row, with results recorded per matrix cell.

## Requirements

1. Run every REQUIRED cell from `docs/dev/wayland-smoke-matrix.md` +
   `docs/dev/desktop-matrix.md` on real sessions; record PASS/FAIL +
   one-line evidence (what was inserted, where) per cell.
2. Any reproduced failure becomes a ledger entry with severity and a
   reproduction script; do not fix product code in this brief.
3. Context-provider (`context.enabled`) off and on, on one Wayland
   session each, since the matrices gate the P2 default flip.
4. Update `docs/STATUS.md` compatibility rows and the matrix docs with
   run dates + machine specs; link the evidence file from the ledger.

## Out of scope

Code fixes (they follow as their own briefs), KDE Wayland extended rows,
performance measurement (corpus brief owns speed).
