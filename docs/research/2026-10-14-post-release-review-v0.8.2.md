# Post-release quality review — v0.8.2 + Unreleased delta

- Date: 2026-10-14
- Reviewer: Q12 recurring practice (quality plan § Q12 acceptance:
  "review the highest-risk uncovered branches, recurring incidents,
  slowest tests, and open compatibility gaps after each release")
- Scope: everything since the v0.8.2 release commit `4851af1`
  (2026-09-14) through `linux` @ `0df6e77` (2026-10-14) — the
  org-plan Phase 5 delta (tier-source unification, coverage floor,
  control-route/CLI-handler tables, GTK section split + display-suite
  growth), the stats-chart draw fix, the Pages landing site, and the
  queue-row verification raid (Q3–Q6 rows).
- Method — all numbers below are from this review's own runs on the
  review worktree, 2026-10-14:
  - `just coverage` → 3512 passed, 4 documented capability skips,
    172 deselected, 71.97% total (floor 71 enforced), XML at
    `build/coverage/unit.xml`, `--durations=25` captured
  - `just lint` → clean (ruff: I001, F401, F541, F632, F841, S110,
    B006, B023)
  - `just typecheck` → clean (9 seam files)
- Companion action shipped with this review: the flaky-test
  owner+expiry registry + its meta-test (see below); this file is the
  dated snapshot, the registry lives in
  [testing.md](../dev/testing.md).

## 1. Highest-risk uncovered branches

Tier framing first (Q5 policy): `fluidvoice/gtkui/**` measures ~0% in
the unit tier **by design** — the display lane owns it (172 tests;
`main_window.py` 78%, `onboarding.py` 85% recorded there in the Q5
row). The risks below are the gaps that no tier currently names.

| Module (unit tier, today) | Lines / branches | Why it is high-risk | Owner / roll-forward |
|---|---|---|---|
| `rewrite.py` | 61.8% / 54.2% | the AI rewrite/expand production path (57–67, 90–126 uncovered) — the only user-facing AI feature with no display/integration tier behind it | next coverage ratchet (Q5 policy: raise floor as meaningful assertions land) |
| `tray.py` | 37.7% / 42.0% | 208 uncovered statements — StatusNotifier menu, the always-visible surface on every desktop; **not among the modules the Q5 display-tier recording named** | display-suite growth candidate (next `c8069b9`-style mirror) |
| `selection.py` | 83% / 83% | typed/paste insertion fallbacks (38–44, 139–148, 251–252) — Q5's named priority "insertion/send behavior"; fallback branches only fire when primary insertion fails | Q5 ratchet + Q7 live matrix evidence |
| `preview.py` | 84% / 81% | streaming-preview failure branches (516–527, 556–574) | Q5 ratchet |
| `model_download.py` | 86% / 87% | resume/retry branches (400–426) — first-use funnel reliability | fold into Q7 first-use matrix cases |
| `overlay.py` | 74% / 74% | 1109–1408 blocks — overlay UI paths; display territory | display lane, same as tray |

No action forced today beyond the registry; the review's verdict is
that the *floor + ratchet policy* is holding (71.97 vs floor 71) and
the named owners above are the next-ratchet targets, in that order.

## 2. Recurring incidents

| Incident | Dates | Standing guard |
|---|---|---|
| CI-only flakes — three strikes in `test_spoken_send_countdown.py` (countdown fired before `recorder.start()`; `recording=False` flipped before the result thread landed) + the cairo-bindings env flake in the chart test | 2026-09-12 (`95978c3`), 2026-09-14 (`586cb8c`) | **new with this review**: flaky registry + `test_dev_gates.py` meta-test (owner+expiry enforced, no unlimited reruns); seam-poll pattern documented in the registry lesson |
| Workflow-file parse rot — the ci.yml 5.1 rewrite left a step with neither `run` nor `uses`: PyYAML-legal, GitHub-parser-invalid, 0-second failures on every push since `92f3ce5` | 2026-09-14 | dispatch CI after any workflow edit (local gates cannot see it) — recorded in the evidence index |
| Release-dispatch ref-metadata race (push→dispatch resolved a stale ref) | 2026-09-14 | mitigation applied (re-prepare); follow-up open: `select-prepare-run` digest fallback |
| Worktree contamination — two hard-discards of uncommitted in-flight work by parallel sessions | 2026-09-05/07 | one-tree-per-agent contract in repo `AGENTS.md` (2026-09-07) |

## 3. Slowest tests (unit tier, today)

Top of `--durations=25`, total tier 28.47 s parallel / 93 s serial:

- **doctor real-CLI family, ~4.1–5.0 s each** — 8 tests across
  `test_infra.py`, `test_privacy_contracts.py`, `test_hotkey_grab.py`,
  `test_mouse_ptt.py`, `test_wayland_capabilities.py`. Each spawns the
  real CLI subprocess; the secrets-masking ones are the privacy proof
  (Q11) and stay real by design. The only genuine batching candidate
  if the tier ever needs to shrink — **deferred**, total cost ~35 s of
  a ~93 s serial tier.
- `test_runtime_tasks.py` shutdown-deadline proofs (5.00 s, 1.61 s) —
  deliberate real-timing assertions (the deadline they prove IS the
  timeout). Keep.
- `test_spoken_send_countdown.py` 3.05 s — the twice-flaked seam; its
  cost is the polling hardening. Keep.

Verdict: no slow-test hazard; nothing retried/rerun to hide a flake
(the registry now makes that explicit policy).

## 4. Open compatibility gaps

- **Python**: `requires-python >=3.11` is open-ended; CI exercises
  3.11 + 3.12 only (ruff `py311` + the 3.11 unit lane guard the floor,
  mypy checks 3.12 — agreement enforced by `test_dev_gates.py`). 3.13+
  is claimed-but-untested; reconcile per Q9 ("versions actually
  tested") rather than silently claiming future compatibility.
- **Desktop stacks**: live evidence exists only for Ubuntu 24.04 /
  GNOME X11 (GTK 4.14 — the stack whose quirks the stats-chart fix
  found). GNOME Wayland + sway matrices OPEN (Q7 briefs); KDE absent;
  GNOME 46/47 untested.
- **Packaging**: deb contract proven end-to-end (Q3/Q4); Arch build,
  pipx install/upgrade across the Python range, and install/upgrade/
  rollback matrices remain Q9 work.
- **Speech quality**: real-speech baseline still corpus-gated (Q8
  brief OPEN) — mock-passing cannot close it (evidence index already
  marks it NOT ESTABLISHED).

## 5. Actions from this review

| # | Action | Status |
|---|---|---|
| A1 | Flaky-test registry (owner+expiry, no unlimited reruns) in testing.md + meta-test guard | **done in this commit** |
| A2 | Post-release review recorded (this file) + evidence-index link | **done in this commit** |
| A3 | rewrite.py / tray.py / selection.py named as next-ratchet targets in that order | owner: Q5 ratchet policy — raise the floor as assertions land |
| A4 | 3.13 gap | owner: Q9 (test-matrix reconciliation) |
| A5 | select-prepare-run digest fallback (ref-metadata race) | owner: release workflow follow-up (open) |

Next review: after the next release tag (v0.8.3+), same shape, dated
snapshot in this directory.
