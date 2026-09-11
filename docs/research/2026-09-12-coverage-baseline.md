# Test coverage baseline (Q5) — measured 2026-09-12

- Date: 2026-09-12
- Status: SHIPPED (baseline recorded; ratchet starts here)
- Source: `agent/qplan-20260911` at the Q5 commit; suite 3,455 unit-tier
  tests + 132 display-tier tests
- Method: `just coverage` / `just coverage-ui` (pytest-cov, branch
  coverage, `source=fluidvoice`, config in pyproject `[tool.coverage]`).
  Tiers are measured SEPARATELY: unit-tier numbers are the ratchet
  denominator; the display tier documents its own slice.

## Numbers

| Tier | Lines | Branches | Tests |
|---|---|---|---|
| unit/contract (`just coverage`) | **79.9%** | **78.9%** | 3,455 passed, 4 skipped |
| display/GTK (`just coverage-ui`) | 29.1% | — | 132 passed |

The 4 unit-tier skips are the documented backend-capability cases
(adapter lacking confidence signals / segments) — capability skips, not
coverage gaps. Display-tier runs execute the GTK slice only; its low
total reflects that it deselects everything else.

## Notable uncovered / weakly covered modules (unit tier)

Owner assignment follows the quality plan's queue: Q6 owns coordinator
lifecycle coverage, Q7 owns process/GTK lanes, Q11 owns the
privacy/safety surfaces.

| Module (unit tier) | Lines | Branches | Next owner |
|---|---|---|---|
| `gtkui/application.py`, `gtkui/main_window.py`, `gtkui/onboarding.py` | 0% | 0% | display tier covers these (Q7 CI lane makes their skipping fatal there) |
| `tray.py` | 38% | 42% | Q7 process lane (real SNI host paths can't be unit-tested; cover the fallbacks) |
| `ui.py` | 46% | 38% | Q10/Q11 (notification absence paths, secret masking) |
| `gtkui/client.py` | 54% | 25% | Q7 (daemon-offline UI, reconnect) |
| `rewrite.py` | 62% | 54% | Q6/Q8 (AI polish failure paths) |
| `__main__.py`, `evalharness/__main__.py` | 0% | 0% | Q7 process lane (CLI entry through a real interpreter) |
| `backends/*` adapters | low in unit tier | — | by design: contract suite is mock-based; real adapters are integration tier |

## Ratchet policy

- The unit-tier branch percentage must not regress: CI's unit job
  uploads `build/coverage/unit.xml`; a drop below the recorded baseline
  (78.9% branches) is a review blocker even when tests pass.
- Raise the recorded numbers here when meaningful assertions land
  (Q6/Q7/Q11 work); do NOT chase 100% — the adapters and live-host paths
  are integration-tier territory by design.

## Slowest unit-tier tests (2026-09-12, serial)

```
5.00s  test_runtime_tasks.py::TestShutdown::test_overall_timeout_caps_the_whole_sweep
3.10s  test_runtime_tasks.py::TestThreads::test_join_by_name
2.46s  test_hotkey_grab.py (doctor print line)
2.42s  test_stall_watchdog.py::test_growing_stream_survives
2.1s   test_infra.py doctor sections (x4)
2.00s  test_control_server.py::test_two_second_transcription_does_not_delay_status
```

All are deliberate waits (join deadlines, doctor CLI sleeps, watchdog
streams); none are leak-hangs (the Q2 gate would exit nonzero on those).

## Bugs the property tests found while establishing this baseline

Property tests (`tests/test_properties.py`, hypothesis) found three real
defects on their first run — exactly the class the plan added them for:

1. `evalharness/metrics.py`: punctuation-only text normalizes to zero
   words on both sides, but the empty-reference rule checked the RAW
   hypothesis string — identical input `"…"` scored WER 1.0. Emptiness
   is now judged on the normalized sequence.
2. `config.py::_toml_value`: JSON escaping leaves U+007F (DEL) raw,
   which TOML basic strings forbid — a config value containing DEL made
   the saved file UNPARSEABLE (save→load crashed). DEL is now escaped.
3. `evalharness/metrics.py::real_time_factor`: NaN inputs slipped
   through the falsy/`<=0` checks and scored `nan`; the docstring
   promises None. Non-finite/non-numeric inputs now return None.

## Reproduction

```bash
SAYIT_PY=/path/to/shared/venv/bin/python just coverage      # unit tier
SAYIT_PY=/path/to/shared/venv/bin/python just coverage-ui   # display tier
```
