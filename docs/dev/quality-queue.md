# Quality-plan item tracker

Companion to `docs/research/2026-09-11-project-quality-and-testing-plan.md`
(implementation queue Q1–Q12). Every row records what the plan demands:
owner, status, dependency, commit, test command, evidence, acceptance.
An item is DONE only with implementation + evidence — never a design doc.

| Item | Status | Depends on | Commit | Test command | Evidence / acceptance |
|---|---|---|---|---|---|
| Q1 test scopes + gate alignment | **DONE 2026-09-12** | — | see `git log --grep "quality plan Q1"` | `just test-unit`, `just test-gtk`, `just gate` | E1: e2e real-download test moved to `tests/integration/` (network+model marks); meta-test `TestE2eStaysIntegration` guards it. E6: one tier source `scripts/run_test_tier.sh` consumed by justfile + ci.yml + release-prepare.yml; unit gate = `-W error --strict-markers --timeout --junitxml` + brief validation; JUnit uploaded on failure. Unit tier run 2026-09-12: 3382 passed, 4 documented capability skips, 154 deselected, network guard active, zero model downloads (fresh-cache offline proof = the guard denies any outbound socket pre-connect). GTK tier run 2026-09-12 on `:1`: 154 passed, 0 skipped (skip⇒fail via `scripts/_tier_skip_check.py`). CI gtk lane provisioned (first dispatch pending — workflows are manual-only by project rule). |
| Q2 teardown enforcement (E2, E7) | TODO | — | | | |
| Q3 dependency hash locking (E3, E4) | TODO | — | | | |
| Q4 publication↔source binding (E5) | TODO | Q3, Q1 | | | |
| Q5 coverage baseline | TODO | Q1, Q2 | | | |
| Q6 lifecycle/failure-sequence tests | TODO | Q2 | | | |
| Q7 process/GTK/desktop lanes + matrices | TODO | Q1, Q2 | | | |
| Q8 real-speech + perf baselines | TODO | corpus brief OPEN | | | |
| Q9 install/upgrade/rollback + soak | TODO | Q3, Q4, Q7 | | | |
| Q10 recovery UX + accessibility | TODO | Q6–Q8 | | | |
| Q11 privacy/command-safety contracts | TODO | Q1, Q6 | | | |
| Q12 recurring quality practice | TODO | Q1, Q5 | | | |

Known follow-ups surfaced while doing Q1 (small, fold into their items):

- The session-end production-data tripwire (`tests/conftest.py`) cannot
  distinguish the live daemon's external history writes from a suite leak:
  a dictation landing inside the ~85 s suite window fails the gate. That
  discrimination is Q2 scope ("distinguish an external live-daemon write
  from a test write"); until then, rerun the gate in a quiet window.
- ci.yml's new gtk job is written but undispatched (manual-only CI rule);
  its first dispatch must be treated as part of the lane's acceptance.
