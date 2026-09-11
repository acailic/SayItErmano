# Quality-plan item tracker

> **2026-09-12 merge note:** Q1/Q2 were implemented TWICE in parallel —
> bd6a531/cf6f947 on `linux` (design described in the rows below) and
> d745255 on `agent/qplan-20260911`, alongside Q3–Q12. The merge that
> followed resolves to the agent-branch design (markers
> `needs_model`/`needs_network`/`needs_display`; leak gate, network guard
> and skip-escalation all in `tests/conftest.py`; canonical commands in
> the justfile) because Q3–Q12 build on it; the rows below are kept as
> the historical record of the superseded approach, whose commits remain
> in history. The authoritative per-item log is now in the
> [plan document](../research/2026-09-11-project-quality-and-testing-plan.md)
> (§ Implementation log). The extra GUI-dependent sites those commits
> marked (language_switch TestSettingsUI, models_manager GTK classes)
> were ADOPTED as `needs_display`.

Companion to `docs/research/2026-09-11-project-quality-and-testing-plan.md`
(implementation queue Q1–Q12). Every row records what the plan demands:
owner, status, dependency, commit, test command, evidence, acceptance.
An item is DONE only with implementation + evidence — never a design doc.

| Item | Status | Depends on | Commit | Test command | Evidence / acceptance |
|---|---|---|---|---|---|
| Q1 test scopes + gate alignment | **DONE 2026-09-12** | — | see `git log --grep "quality plan Q1"` | `just test-unit`, `just test-gtk`, `just gate` | E1: e2e real-download test moved to `tests/integration/` (network+model marks); meta-test `TestE2eStaysIntegration` guards it. E6: one tier source `scripts/run_test_tier.sh` consumed by justfile + ci.yml + release-prepare.yml; unit gate = `-W error --strict-markers --timeout --junitxml` + brief validation; JUnit uploaded on failure. Unit tier run 2026-09-12: 3382 passed, 4 documented capability skips, 154 deselected, network guard active, zero model downloads (fresh-cache offline proof = the guard denies any outbound socket pre-connect). GTK tier run 2026-09-12 on `:1`: 154 passed, 0 skipped (skip⇒fail via `scripts/_tier_skip_check.py`). CI gtk lane provisioned (first dispatch pending — workflows are manual-only by project rule). |
| Q2 teardown enforcement (E2, E7) | **DONE 2026-09-12** | — | see `git log --grep "quality plan Q2"` | `just test-unit` (+ `tests/test_leak_gate_meta.py`) | E2: leak verdict moved from tests/test_runner_hygiene.py into the globally-loaded plugin tests/_runner_hygiene.py, registered via `pytest_plugins` in the NEW repository-root conftest.py — every run shape loads it. Audit repro re-run 2026-09-12: temp leaking test alone → `1 passed, 1 error, exit 1` with node attribution (was exit 0). Meta-tests (disposable dirs, real subprocess pytest): child/timer leaks fail single-file runs with attribution; `-n 2 --dist loadfile` with no hygiene module still red (the E2 gap); clean runs green; failing+leaking reports both; setup-failure keeps cleanup; `--timeout` hang fails promptly (<60 s); focused repo run loads the plugin (`--trace-config`). E7: integration daemon/CLI launches use `sys.executable -m fluidvoice` + explicit checkout cwd (no assumed per-checkout .venv; artifact tests keep the installed binary); X11/WAYLAND pinning now scoped OUT of the integration tier (`FLUIDVOICE_TEST_TIER=integration` keeps ambient compositor identity); finalization order of the autouse sweep verified dynamically (TestFinalizationOrder), not by comment. Tripwire: external live-daemon writes now classified by /proc lineage in the failure message, with `FLUIDVOICE_TOLERATE_EXTERNAL_HISTORY_WRITES=1` to downgrade classified-external changes on daily-driver machines. |
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

- ci.yml's gtk job is written but undispatched (manual-only CI rule);
  its first dispatch must be treated as part of the lane's acceptance.
- The tripwire's external-write classification is lineage-based: a
  double-forked daemon escaping the pytest process tree would read as
  external. Residual risk accepted in Q2; revisit if a leak ever
  masquerades (Q6's deterministic lifecycle tests would catch the
  behavior itself).
