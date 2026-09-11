# Phase 0 — offline baseline run (Agent B)

- Date: 2026-09-11, 12:19–12:45 CEST
- Run by: phase-0 agent (baseline lane), worktree `FluidVoiceLinux-p0-baseline`
- SHA under test: `f74c9246282f5d37a5a35105f9873d0e046f916d` on branch
  `agent/p0-baseline` (identical tree content to `linux` at this SHA)
- Delta vs the plan's inspected baseline `68ef282`: two docs-only commits
  (`5efc25d` commercial-model evidence, `f74c924` the phase plan itself) —
  no code changed since the plan was written.
- Scope: existing offline checks only, per
  [the plan](2026-09-11-product-excellence-and-monetization-plan.md) item 2
  ("Run existing offline checks and save failures/skips/warnings with
  environment details"). Nothing was fixed; this is a measurement record.

## Environment

| Item | Value |
|---|---|
| OS | Pop!_OS 24.04 LTS (`ID_LIKE=ubuntu debian`) |
| Kernel | `7.1.5-76070105-generic #202607241434 SMP PREEMPT_DYNAMIC` x86_64 |
| CPU | AMD Ryzen 7 5700X, 8 cores / 16 threads |
| RAM | 31 GiB total (≈15 GiB in use by the desktop during the runs; ≈16 GiB available) |
| GPU | NVIDIA GeForce RTX 4060, 8 GiB (`nvidia-smi` available; a CUDA thread appears during the serial suite) |
| Python | 3.12.3 (system `/usr/bin/python3`, symlinked by the venv) |
| Venv | shared: `/home/nistrator/Documents/github/FluidVoiceLinux/.venv` |
| pytest | 9.1.1 · pytest-xdist 3.8.0 |
| ruff | venv `python -m ruff` 0.16.6 (the `just lint` recipe); system PATH ruff 0.14.9 — both run, both clean |
| Key deps | torch 2.13.0, onnxruntime 1.29.0, huggingface_hub 1.30.0 + hf-xet 1.6.0 |
| Production daemon | the user's real `sayit-ermano.service` was running the whole time (PID 3917319, since 04:12) — untouched by this run; nothing below involves it |

## Offline suite results

Command (from the worktree root, shared venv, per AGENTS.md):

```bash
/home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python -m pytest -q tests --ignore=tests/integration
```

| Run | Mode | Collected | Passed | Failed | Skipped | Warnings | pytest time | Wall clock | Exit |
|---|---|---|---|---|---|---|---|---|---|
| 1 | serial | 3314 | 3310 | **0** | 4 | 0 | 95.24 s | 5 min 49 s † | 0 |
| 2 | serial, `-rs` | 3314 | 3310 | 0 | 4 | 0 | 92.13 s | ≈7 min 44 s † | 0 |
| 3 | `-n auto` (16 workers) | 3314 | 3310 | 0 | 4 | 0 | 28.90 s | 29.20 s | 0 |
| 4 | `just gate` (= ruff + `pytest -q -W error`) | 3314 | 3310 | 0 | 4 | 0 (with `-W error`) | n/a | incl. hang † | 0 → "gate: clean tree, lint clean, suite green, zero warnings" |

† The serial runs and the gate **hang after printing the summary** for ~4–6.5
minutes before the process exits — reproduced observation, see
[below](#observed-post-summary-hang). pytest's own reported duration
(92–95 s) is the true suite time; the extra wall time is interpreter-exit
blocking.

- **Failures: none.** Zero failures in all four runs. Nothing to reproduce.
- **Flakiness serial vs xdist: none.** Identical counts (3310/4) and no
  trailing-log differences; results are stable across three full runs.
- xdist speedup on this machine: 92–95 s → 28.9 s ≈ **3.2×** (README claims
  "~4–5x"; see historical-claims table below).
- Peak RSS: serial ≈ 1.96 GiB (single process); xdist tree ≈ 1.28 GiB max
  reported per `/usr/bin/time` (spread across 16 workers).
- The serial run leaves non-daemon threads alive at exit (see below),
  including a CUDA thread and 16 `hf-xet` threads — the offline suite does
  initialize GPU/torch and HuggingFace-transfer machinery even though it
  never transcribes real speech.

### Skips (all four runs, with reasons)

`pytest -q -rs tests --ignore=tests/integration` output:

```text
SKIPPED [1] tests/test_backend_contract.py:458: adapter has confidence signals
SKIPPED [3] tests/test_backend_contract.py:465: adapter has segments
```

Both come from `TestDegradation`, which is deliberately conditional: the
`adapter` fixture parametrizes five faked backends (faster-whisper,
whisper-torch, whisper.cpp, parakeet, remote), and a degradation test is
skipped for any adapter that *has* the capability being degraded — 1 of 5
adapters advertises confidence signals, 3 of 5 advertise segments. These are
by-design capability skips, not environment gaps. No other skip reasons exist
in the offline suite.

## Static checks and validators

| Check | Command | Result |
|---|---|---|
| Lint (canonical, `just lint`) | shared-venv `python -m ruff check .` (ruff 0.16.6) | `All checks passed!` (exit 0) |
| Lint (system) | PATH `ruff check .` (ruff 0.14.9) | `All checks passed!` (exit 0) |
| Requests ledger | shared-venv `python scripts/validate_requests.py` | `validate-requests: 28 briefs ok OPEN=0 SHIPPED=28 SUPERSEDED=0` (exit 0) |
| Full local gate | `SAYIT_PY=<shared venv> just gate` | pass: "clean tree, lint clean, suite green, zero warnings" (exit 0) |
| Integration collection (count only, not run) | `pytest -q --collect-only tests/integration` | 38 tests collected |

## Observed post-summary hang (reproduced defect signal, not fixed)

**Observation.** In both serial runs and under `just gate`, pytest prints
`3310 passed, 4 skipped in 9Xs`, then the process stays alive for minutes.
Exactly five `max duration reached, stopping` / `no audio captured` pairs
print at one timestamp, one more pair ~24 s later ending with
`transcription failed: 'NoneType' object has no attribute 'name'`, then the
process exits 0:

```text
[sayit-ermano] 12:33:38 max duration reached, stopping      # ×5, same second
[sayit-ermano] 12:33:38 no audio captured                   # ×5
[sayit-ermano] 12:34:01 max duration reached, stopping
[sayit-ermano] 12:34:01 transcription failed: 'NoneType' object has no attribute 'name'
```

Run-1 timestamps (12:25:36 / 12:26:00) show the identical 5+1 pattern, so
this is deterministic, not flaky.

**Mechanism (read from code, no changes made).** `RuntimeTasks.prepare_timer`
(`fluidvoice/runtime_tasks.py:197`) returns `_TaskTimer`, a
`threading.Timer` subclass created **without `daemon=True`**. The capture
coordinator's max-duration watchdog defaults to
`recording.max_seconds = 300` (`fluidvoice/capture.py:129`, default 300 s from the registry at
`fluidvoice/config.py:677`). Tests that start takes through a coordinator
with the real `RuntimeTasks` (e.g. `tests/test_capture_coord.py`'s `make()`
helper builds `CaptureCoordinator(cfg, RuntimeTasks(), …)` with no
`shutdown()` teardown; daemon-level tests log with the real pipeline logger,
matching the leaked output) leave live 300 s non-daemon timers. Python's
`threading._shutdown` then blocks interpreter exit until the leaked
watchdogs fire (the observed ~5 min ≈ the 300 s default), each firing prints
the auto-stop path, and the last one walks a stop→transcribe path against a
`None` backend, producing the `NoneType ... name` error log. A process-table
snapshot during the hang confirms ~68 threads including the unnamed timer
threads, 16 `hf-xet-*`, `pool-spawner`, a `cuda…` thread, `gdbus`/`gmain`.

**Impact.** Exit status is always 0 and results are unaffected, but any
wrapper that waits on process-tree completion (CI steps, `/usr/bin/time`,
pipelines reading stdout) pays ~4–6.5 extra minutes per serial run, and the
suite briefly exercises real stop/notify paths *after* reporting success —
confusing for anyone reading logs. `pytest -n auto` is unaffected (xdist
workers exit hard, which is also why the leak is masked there).

**Suggested follow-up (for the phase-0 ledger, not done here):** shut down
`RuntimeTasks` in the coordinator test fixture/teardown, or make `_TaskTimer`
daemon threads; add a suite-level guard that fails if non-daemon timers
outlive their test. The `transcription failed: 'NoneType'…` line also merits
a look: a watchdog auto-stop after teardown reached a half-dismantled
pipeline — benign in tests, but it is the same class of late-timer race the
first-PCM leak fix (P0.4, `[tool.pytest]` thread-exception gate) targeted.

## Comparison vs historical claims (NOT current results)

| Claim | Source (historical) | Current measured |
|---|---|---|
| "1782 automated tests at v0.8.1 (`-n auto` runs it in ~19 s)" | README testing section | 3314 collected / 3310 passed offline; `-n auto` = 28.9 s |
| "**3022 automated offline tests + 38 integration** · verified" (ledger dated 2026-09-10) | `docs/STATUS.md` header | offline now 3314 (+292 vs claim); integration still exactly 38 collected — the integration count matches, the offline count in the ledger is stale |
| "`-n auto` … ~4-5x" speedup | README | 3.2× on this 16-thread machine (92–95 s → 28.9 s) |

These are historical claims recorded as-is; they were not refreshed by this
run and should not be cited as current numbers.

## Known unverified (cannot be established by the offline baseline)

Per the plan's "What already exists, and what must be verified" and phase-0
exit gate, the following gates are **not** verified by anything above and
must not be inferred from the green offline suite:

1. **Live desktop / Wayland matrices** — GNOME Wayland, sway, X11 capture,
   insertion, clipboard, hotkey-grab behavior. The plan records these
  matrices as explicitly NOT DONE; offline tests mock the desktop. See
  `docs/dev/wayland-smoke-matrix.md`.
2. **Real-model speech quality and latency** — the offline suite uses
  synthesized/stubbed audio only; the eval harness corpus is tones/silence,
  not recorded speech. No WER/latency/insertion-latency numbers exist for
  real speech on this machine from this run. See `docs/eval/README.md`.
3. **deb packaging, upgrades, release reproducibility** — `packaging/deb`
  build, install/upgrade/uninstall on supported Ubuntu versions, and the
  manual release gates were not exercised. See `docs/dev/release-gates.md`
  and `packaging/deb/README.md`.
4. **Onboarding end-to-end / first-use funnel, user studies, commercial
  questions** — out of scope for this lane entirely (phases 1+).
5. **The integration suite itself (38 tests)** — only collected, not run;
  they need the real model/mic and were excluded per the standard command.

## Reproduction notes

All commands were run from this worktree root (so its `fluidvoice/` is the
code under test) with the shared venv exactly as AGENTS.md prescribes.
Serial-run artifacts: `/tmp/serial-rs.log` (run-2 transcript with `-rs`),
process-table monitor log at `/tmp/proc-mon.log` (60 s of snapshots covering
the hang), both ephemeral. The production daemon was never restarted; no
config keys were flipped; no repo files other than this report were touched.
