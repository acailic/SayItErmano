# Project quality and testing improvement plan

- Date: 2026-09-11
- Status: PLANNED — investigation complete; implementation is future work
- Audited source: `ed89dfb` on `linux`, application version `0.8.1`
- Scope: application, tests, CI/release workflows, packaging, desktop behavior, speech evaluation, documentation, and maintenance.

## Recommendation

Make a green test or release result trustworthy before adding more features. The project already has extensive example-based tests and useful boundaries for capture, model ownership, command handling, history, configuration, and backend adapters. The next improvement cycle should strengthen the checks around those implementations, establish real desktop and speech evidence, and use that evidence to choose product changes.

Start with Q1–Q4 below: fix test selection and isolation, enforce leak detection for every test invocation, repair dependency hash generation, and bind release artifacts to their source. Then add measured coverage, repeatable desktop/process tests, and the real-speech baseline. Do not repeat the architecture work already delivered by the September 10 reliability program.

This is a local source investigation, not a claim that published releases, remote CI runs, every runtime path, or supported hardware have been validated. No release, package installation, production daemon restart, or configuration change was performed for this audit. Temporary diagnostic tests were removed.

## Baseline and evidence

The isolated audit worktree used the shared CPython 3.12.3 environment specified in `AGENTS.md`.

| Check | Result / meaning |
|---|---|
| Application inventory | 93 Python files, 27,494 physical lines under `fluidvoice/`; size is context, not a defect metric |
| Test inventory | 89 Python files, 32,859 physical lines under `tests/`, including integration support |
| Prescribed test command with warnings as errors | **3,531 passed, 4 skipped, zero warnings in 118.59 seconds**, exit 0. The four skips are intentional backend capability cases; this command also includes the real-model/network test described in E1 |
| Current Ruff configuration | Passed; only `I001` and `F401` are enabled |
| Request status validator | Passed: 31 briefs, 29 SHIPPED, 2 OPEN |
| Integration collection | 38 tests collected successfully; not executed |
| Coverage instrumentation | No coverage configuration found in the reviewed gates; `coverage` / `pytest-cov` absent from the shared venv. No coverage percentage measured |
| Additional Pyflakes rules, application only | Three findings: one `F541` in `evalharness/report.py`, two `F841` in `overlay.py`; these are minor cleanup, not priority defects |
| Real desktop matrix | Existing Wayland runbook remains NOT DONE; no new live matrix run in this investigation |
| Representative speech evaluation | Committed corpus is synthetic; real-speech recording brief remains OPEN. Metric adapters and corpus tooling have already shipped |

### Findings verified in current code

| ID | Finding | Evidence and confidence | Consequence |
|---|---|---|---|
| E1 | The advertised offline suite contains network/model work | `tests/test_e2e_transcribe.py:1–42` is outside `tests/integration`, marks itself only `slow`, downloads JFK audio, and loads the tiny model. `justfile` and release-prepare exclude only the integration directory. Confirmed from source | Local gates depend on network/cache/model availability; CI uses a different test set |
| E2 | Leak detection can silently pass focused and distributed runs | `_session_leak_gate` lives in `tests/test_runner_hygiene.py:85`, while cleanup and the leak ledger live in `tests/conftest.py`. A temporary test that starts a sleeping child returned **0 / 1 passed** alone, but **1 / 5 passed, 1 error** when the hygiene module was included serially. With both files under `-n 2 --dist loadfile`, it again returned **0 / 5 passed**. Child was reaped in all runs | Focused runs and workers that never execute the hygiene module can report success despite a resource leak |
| E3 | Hash generator parses the wrong output format | `packaging/deb/update-constraints.sh:143` selects lines matching `^sha256=`. Actual local `python -m pip hash` output begins `--hash=sha256:`. A temporary local file reproduced zero matches and the resulting malformed `example==1.0 --` line | The full hash-generation path cannot produce the intended lock with this pip output; test the complete writer/consumer round trip |
| E4 | Pin-only builds remain permitted | `packaging/build-deb.sh:74–83` prints a note and builds without hash verification when no hashes exist; the committed constraints are pin-only. Confirmed source policy gap, not an observed compromised artifact | A missed manual step weakens the documented release guarantee |
| E5 | Artifact selection does not prove source provenance | `release-publish.yml:134–150` takes the first artifact named for the version, checks expiry and package version, but does not verify its prepare workflow identity/conclusion or source content against the release commit. Prepare uploads before committing/pushing the version bump | Exact-SHA CI protects source tests, but does not by itself prove the downloaded deb contains that source. No incorrect published artifact was demonstrated |
| E6 | CI and local acceptance differ materially | `ci.yml` excludes `slow` and `integration`, omits blanket `-W error`, installs no GTK test environment, and archives `.pytest_cache` rather than a useful test report. `just gate` runs a different scope. Confirmed configuration | Green CI is a narrower signal than the developer/release documentation suggests; GUI modules can skip in headless environments |
| E7 | Integration runner assumes a venv in each checkout | `tests/integration/conftest.py:79` launches `REPO/.venv/bin/fluidvoice`; isolated worktrees share a different venv by policy. Top-level conftest also forces X11 and removes `WAYLAND_DISPLAY` | Existing integration infrastructure needs separation before it can reliably validate arbitrary worktrees and Wayland sessions |
| E8 | Crucial product claims remain evidence gaps | `requests/wayland-matrix-execution.md` and `requests/speech-corpus-recording.md` are OPEN; matrix says NOT DONE; bundled manifest explicitly contains no recorded speech | Mocked success does not establish reliable insertion or speech quality in users' environments |

The numbered source references describe the audited commit; implementation should recheck them against the current branch. E1–E7 are concrete test/release infrastructure gaps, while E8 is explicitly missing evidence rather than proof of broken behavior.

### Preserve work that is already complete

- Keep `SpeechBackend` / `Transcript` and the five-adapter contract suite. Extend real dependency coverage rather than introducing another backend abstraction.
- Keep `CaptureCoordinator`, `SpeechEngineManager`, `CommandCoordinator`, and `RuntimeTasks`. Refactor only where a demonstrated lifecycle or ownership problem crosses their boundaries.
- Keep the configuration registry and transactional JSONL history. Existing history tests already cover concurrent append/edit/delete, two-process access, rollback, and corrupt rows; new tests should cover missing crash boundaries rather than duplicate those cases.
- First-use download visibility, guided insertion, honest clipboard fallback, runner cleanup, corpus tooling, and G1–G7 metric adapters are already in this source snapshot. Validate and extend them rather than reopening completed implementation briefs.
- Keep manual-only GitHub Actions, Linux scope, no TCP service, local-first defaults, and the Ubuntu deb contract. This plan does not change those decisions.

## Implementation queue

Estimates are rough focused engineering days, including useful tests and documentation, not delivery promises. Assign an owner before starting each item. Desktop access and participant recruitment are external dependencies with separate lead times.

### Q1 — Make test scopes explicit and align gates

Priority: P0. Effort: 1–2 days. Dependencies: none. Evidence: E1, E6.

Move or mark real-model/download tests as integration, with explicit capability markers for model, network, desktop, and packaging requirements. Keep fast local-process tests that need no hardware in a distinct runnable tier. A `slow` marker describes duration and must not stand in for environmental requirements.

Provide one canonical command per tier, consumed by `just` and manual CI. The mandatory unit/contract gate should use `python -m pytest`, exclude external requirements, enable `-W error`, reject unknown markers, emit JUnit, and have a bounded job timeout. Run request validation in the canonical gate. Treat required tier skips as unmet prerequisites rather than successful execution; retain documented capability-based skips such as an adapter lacking confidence signals.

Acceptance:

- With a fresh cache, no display, and outbound network disabled, the unit/contract gate passes and performs no model download.
- Local and CI core collection lists match for the same dependency environment; GTK is a separately required, provisioned lane.
- A network attempt in a unit test fails clearly; explicitly allowed local fake HTTP/STT servers still work.
- A warning, unknown marker, failing request validation, or hanging test makes its gate fail with a useful artifact. A development check can run on WIP; release cleanliness is enforced separately, including untracked files.

### Q2 — Enforce teardown for focused, full, and distributed tests

Priority: P0. Effort: 1–2 days. Dependencies: none. Evidence: E2, E7.

Move the leak verdict into globally loaded conftest/plugin infrastructure. Prefer attributing cleanup failures to the leaking test's teardown; use a session sweep for collection-time leftovers. Keep cleanup even when the test fails. Verify fixture finalization order rather than relying on the current comment that an autouse fixture always tears down last.

Scope environment normalization to unit tests. Explicitly isolate config/socket overrides and child process environments; do not overwrite the compositor identity needed by desktop tests. Retain the production-data tripwire but distinguish an external live-daemon write from a test write when investigating failures. Use `sys.executable -m fluidvoice` with an explicit checkout cwd for source tests, and the installed executable for artifact tests.

Acceptance:

- Intentionally leaking a timer or child produces a nonzero exit in a single-file run, full run, and `-n 2` run, including workers that never run `test_runner_hygiene.py`.
- Normal, assertion-failure, setup-failure, and timeout cases leave no owned processes, timers, sockets, or XDG residue; the outer process exits promptly.
- Real X11 and Wayland environments reach their respective test fixtures unchanged except for documented sandbox overrides.
- Meta-tests run in disposable directories and verify exit status and diagnostic attribution, not just the presence of a configuration string.

### Q3 — Repair and enforce dependency hash locking

Priority: P0. Effort: 1–2 days. Dependencies: none. Evidence: E3, E4.

Use a robust hash-generation path, for example `hashlib.sha256` over the selected wheel, and emit valid pip `--hash=sha256:` requirements. Validate wheel-to-package matching, missing/duplicate wheels, every required hash, and complete output before atomically replacing the lock. Factor shared validation out of duplicated workflow snippets.

Fail release builds on missing or malformed hashes. If pin-only builds remain useful during development, make that an explicit development mode that publishing cannot accept. Check the lock in the declared Ubuntu/Python/architecture environment and retain package-resolution validation.

Acceptance:

- Offline fixture wheels exercise generation → parse → `pip --require-hashes` consumption using a controlled local wheelhouse.
- Known wheel bytes produce the expected digest; changing one byte is rejected by the consumer.
- Missing wheels, malformed hashes, mixed hash/pin-only entries, and stale dependencies fail with actionable errors and preserve the previous lock.
- A clean target-environment package build consumes the reviewed hashed lock successfully before this item is closed.

### Q4 — Tie publication to the tested package and its source

Priority: P0. Effort: 2–3 days. Dependencies: Q3; Q1 for consistent source gates. Evidence: E5.

Give prepare artifacts a machine-readable manifest with source commit/content identity, package SHA-256, lock digest, version, build target, and prepare run ID. Because prepare currently builds before the version-bump commit, explicitly redesign that sequencing or bind the artifact to the prepared tracked source content; do not compare the original workflow `head_sha` to the later release SHA and assume they must match.

Publish should verify the intended prepare workflow succeeded, source/content matches the release commit, package digest and metadata match, and exact-release-SHA CI is green. Resolve the artifact by validated run identity rather than version name alone. Record required evaluation/desktop/package evidence, with missing prerequisites failing a release rather than silently skipping.

Acceptance:

- A fixture-driven publish rehearsal rejects a same-version artifact from another source, failed/wrong workflow, stale run, expired artifact, altered package, or mismatched lock.
- A successful rehearsal proves the complete manifest chain without creating a tag, uploading a release, or pushing a branch.
- The real candidate deb receives installation smoke tests; source-tree tests alone cannot approve it.

### Q5 — Measure test coverage and improve assertions at risky boundaries

Priority: P1. Effort: 2–3 days initially, then incremental. Dependencies: Q1, Q2.

Add branch coverage reports for `fluidvoice`, with unit, process, and GTK tiers identified separately. Establish the baseline before choosing module thresholds; there is no measured percentage in this audit. Track unexecuted modules, branches, skip reasons, and test duration. Ratchet the baseline upward as fixes add meaningful assertions rather than demanding 100% immediately.

Prioritize capture/pipeline cancellation, history durability, insertion/send behavior, configuration migration, socket validation, and package gates. Add a small set of property-based tests for transcript serialization, config round trips, Unicode text processing, metric arithmetic, and chunk-boundary reconciliation. Use invariants appropriate to the domain: WER, for example, can exceed 1 and must not be incorrectly constrained to [0,1].

Acceptance: reports are reproducible and attached to CI; important uncovered branches have an owner; seeded mutations to guard decisions, insertion suppression, config persistence, and locking preconditions are caught. Use mutation testing on selected pure functions first, not the entire GUI/model suite. Mock external boundaries; avoid tests that merely repeat implementation expressions or require arbitrary internal field layouts.

### Q6 — Add deterministic lifecycle and failure-sequence tests

Priority: P1. Effort: 3–5 days. Dependencies: Q2; use Q5 results to refine scope.

Build on existing coordinators with injectable clocks/events where needed. Model sequences such as start → stop → processing → cancel → late result, model reload while busy, duplicate stop, lock during capture, mic disappearance, and shutdown with pending callbacks. Use event barriers instead of guessed sleeps for concurrency assertions.

Extend existing history transaction tests with subprocess crash injection around append, audio staging, replace, and directory sync. Test disk full/permission errors and retry recovery. Maintain assertions that no unrelated history entry disappears and no unintended text/send key reaches an application.

Acceptance: repeated deterministic schedules preserve one active take, prevent stale results entering a newer take, release busy state on terminal paths, preserve committed history, and leave no tasks. A newly found runtime defect gets its own minimal reproducer before a fix; tests should verify the intended behavior through coordinator interfaces.

### Q7 — Establish repeatable process, GTK, and desktop validation

Priority: P1. Effort: 3–5 days plus live session access. Dependencies: Q1, Q2. Reuse the OPEN Wayland brief and existing matrices.

Split model-free daemon/socket/CLI tests from real mic/model tests. Use an isolated session bus and virtual X display for GTK and suitable X11 integration cases. Provision GTK4/libadwaita and verify required GUI modules actually execute. These tests should cover daemon-offline UI, reconnect, download failure/retry/cancel, first insertion, settings persistence, and history recovery.

Execute the existing real GNOME X11, GNOME Wayland, and sway matrices. Cover a native text editor, terminal, browser text field, and an Electron editor; include clipboard restoration, focus changes, absent insertion tools, compositor shortcuts, and permission failures. Record hardware, compositor/tool versions, source SHA, case counts, skip/failure reasons, and logs. Virtual-display success does not establish Wayland or physical-mic behavior.

Acceptance: model-free process tests run in disposable worktrees; GTK CI fails if its prerequisites cause required modules to skip; every supported desktop/app claim maps to dated evidence or an explicit limitation. Complete lock/resume and USB mic unplug/replug cases on real hardware. Do not mark existing Wayland requests SHIPPED until their specified evidence exists.

### Q8 — Run real-speech and end-to-end performance baselines

Priority: P1. Effort: 3–5 engineering days plus consented recording lead time. Dependencies: existing corpus tooling; Q1 for tier separation. Reuse the OPEN speech-corpus brief.

Collect the existing specification's 150–300 utterances from 10–15 speakers, plus silence/noise cases. Use the shipped provenance validator and held-out split tooling. Keep private recordings outside git. Establish speaker-separated evaluation and report per-language, mic, noise, and utterance-type results; do not tune thresholds on the held-out set.

Evaluate actual user paths: raw backend, production guard/post-processing, and preview/final timing. A raw-backend adapter cannot validate a guard it never invokes. Report WER/CER, omissions, names/numbers/hotwords, punctuation, language confusion, guard false suppression and missed hallucinations, cancellation behavior, p50/p95 latency, peak memory, and cold versus warm load time. Include the dead-capture guard's quiet valid speech and genuinely empty capture cases.

Acceptance: reproducible baseline report includes source SHA, model and dependency versions, corpus/split identity, hardware, denominators, unscored cases, and uncertainty. Set regression tolerances after inspecting this baseline and declare them before subsequent tuning. Preserve the harness's documented real-time-factor direction. A small corpus can locate regressions but cannot establish rare-failure reliability across all languages.

### Q9 — Test installation, upgrade, rollback, and long-running operation

Priority: P1. Effort: 3–5 days plus soak duration. Dependencies: Q3, Q4, Q7.

In disposable target environments, exercise deb install → first run → upgrade → restart → rollback → uninstall. Verify config/history retention, executable/version selection, service readiness, inherited desktop environment, and duplicate user/system installations. Add pipx install/upgrade checks across the documented Python support range and a clean Arch package build. Reconcile the open-ended `>=3.11` declaration with the versions actually tested instead of silently claiming universal future-version compatibility.

Run existing soak tooling with realistic take/reload/cancel/idle cycles: two hours before major releases and the documented 24-hour lifecycle run. Observe memory trend after warmup, file descriptors, threads/children, GPU memory where available, socket responsiveness, and recovery after sleep/device loss. A process that sits idle for 24 hours does not exercise the same risks.

Acceptance: dated artifact-specific installation reports, retained-data checks, and soak CSV/logs attach to the candidate. Resource thresholds are baseline-derived and account for cache warmup; unexplained sustained growth or unrecovered failure blocks acceptance.

### Q10 — Improve recovery UX and accessibility using observed failures

Priority: P2, promote a reproduced harmful defect sooner. Effort: 3–5 days. Dependencies: Q6–Q8 evidence.

Recheck prior ledger F-09/F-10: busy feedback and cancellation after recording. Current `CaptureCoordinator.cancel()` returns when not recording, so define the processing-cancel contract before implementation. Provide prompt visible acknowledgement, discard cancelled results before insertion/send, and keep resources unavailable until the backend can safely release them. Do not promise instant interruption of an uninterruptible model call.

Mirror important failures in persistent UI/status when notifications are absent. Audit keyboard-only navigation, focus return, screen-reader labels, scaling, contrast, and reduced motion. Observe fresh users completing the newly shipped onboarding-to-real-insertion journey with the existing user-test script.

Acceptance: no text or send key from an acknowledged cancelled take; next use recovers according to the documented backend contract. Every forced core failure has visible recovery guidance without notifications. Record the existing 8-of-10 unaided onboarding target as a formative usability target, not proof of population-wide success.

### Q11 — Turn privacy and command-safety promises into regression contracts

Priority: P1 for release checks; ongoing thereafter. Effort: 2–3 days. Dependencies: Q1, Q6.

Verify local mode does not send audio/transcripts to remote STT or AI services, while allowing separately documented update checks. Exercise remote opt-in and display its data flow accurately. Test secret masking in status/config/log/export surfaces, default audio retention, context non-persistence, clipboard failure/restoration, and existing command confirmation requirements. Use fake endpoints and injected errors; no real credentials or personal recordings belong in test artifacts.

Acceptance: fixture secrets never appear in public diagnostics; local/remote behavior and retention match configuration; denied/unconfirmed command proposals do not execute; failed insertion retains recoverable text. Pair mock clipboard tests with Q7's live clipboard-manager evidence and document known X11 limitations. This is a focused behavioral regression program, not a claim of a complete security audit.

### Q12 — Maintain quality as a recurring engineering practice

Priority: P2. Effort: 1–2 days setup, then part of normal work. Dependencies: Q1, Q5.

Extend Ruff gradually from unused imports to the remaining correctness-focused Pyflakes rules, fixing the small observed backlog first. Introduce focused type checking at typed backend/config/coordinator interfaces; avoid a repo-wide rewrite or suppressing errors en masse. Move release validation into reusable tested functions where duplicated shell/Python policy is already diverging.

Use one evidence index connecting requirements → tests → desktop/evaluation reports → release SHA. Keep research reports as dated snapshots, STATUS as shipped behavior, ROADMAP as remaining work, and request headers accurate. Remove stale testing statements such as the claim in `test_dev_gates.py` that CI does not test Python 3.11; the current workflow does. Reconcile the slightly different soak wording in the evaluation and release docs.

Acceptance: each bug fix adds a test at the layer where it escaped; flaky tests have an owner and expiry rather than unlimited reruns; no feature is closed solely because mocks pass when real-system evidence is required. Review the highest-risk uncovered branches, recurring incidents, slowest tests, and open compatibility gaps after each release. Change architecture only to remove a demonstrated source of defects or test setup complexity.

## Test portfolio and release decision

| Tier | Purpose | When / environment | Evidence |
|---|---|---|---|
| Unit + adapter contract | Pure behavior, error paths, state transitions with controlled boundaries | Every local change; required manual CI on tested Python versions, no external network/model/display | JUnit, branch coverage, durations, warnings, skip reasons |
| Process integration | Real CLI/daemon/socket/history, fake audio/model/provider boundaries | Required manual CI in isolated process environment | Exit/teardown checks, protocol results, sanitized logs |
| GTK / virtual X11 | Real widgets, focus, reconnect, selected input integration | Required provisioned GUI lane for relevant changes | Executed case list; prerequisite skips fail this lane |
| Live desktop / hardware | Actual focus/insertion/hotkeys/compositor/mic behavior | Candidate changes affecting these paths; dedicated sessions | Existing matrix with SHA, machine, apps, outcomes |
| Real speech | Quality, omissions, hallucinations, speed/resource tradeoffs | Speech-pipeline candidates, pinned model and corpus | Paired reports, held-out identity, denominators and uncertainty |
| Package + soak | Installed behavior, upgrade retention, runtime stability | Release candidate in supported targets | Artifact digest, VM results, resource traces |

Keep all workflow triggers manual. A release is ready only when required tiers have current evidence for its changes and artifact identity, not merely when a test count increases. Proposed additions to mandatory gates in this document take effect when implemented; this audit does not claim they already exist.

## Sequence and progress tracking

1. **First milestone: trustworthy gates.** Finish Q1–Q4. Immediately arrange desktop access and corpus recording using the existing OPEN briefs, since their lead times can dominate the schedule.
2. **Second milestone: measured application behavior.** Finish the initial Q5 baseline, Q6 lifecycle gaps, and Q7 process/GTK lanes; execute desktop matrices as sessions become available.
3. **Third milestone: evidence-backed candidate.** Complete Q8 real-speech baseline, Q9 package/soak runs, and Q11 behavioral privacy/safety checks. Use findings to choose the smallest Q10 product fixes.
4. **Every subsequent release:** apply Q12, rerun affected tiers, update supported claims, and select the next improvements from observed failures and uncovered high-risk behavior.

Track each Q item with owner, status, blocking dependency, implementation commit, test command, evidence artifact, and acceptance result. Do not close an item with only a design document. Estimates are deliberately not summed into a calendar commitment: hardware availability, recording consent/recruitment, and candidate failures can extend completion.

## Reproduction commands and diagnostic record

Run from the relevant worktree root; the shared interpreter below is required by this repository's agent rules:

```bash
/home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python -m pytest -q tests --ignore=tests/integration -W error -ra
/home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python -m ruff check .
/home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python scripts/validate_requests.py
/home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python -m pytest --collect-only -q tests/integration
```

The first command is the **current prescribed gate**, not proof of offline execution: E1 describes its network/model test. Q1 must correct the selection before it can honestly be called offline.

Leak diagnostic: a temporary test called `subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])` without waiting. Running only that test produced `1 passed in 0.02s`, exit 0. Adding `tests/test_runner_hygiene.py` produced `5 passed, 1 error in 0.06s`, exit 1, explicitly naming the leaked child. Running both files with `-n 2 --dist loadfile` produced `5 passed in 0.43s`, exit 0, reproducing the worker-local gap. Conftest killed the owned child in all cases; the temporary test was deleted.

Hash diagnostic: `python -m pip hash` on temporary fixed bytes returned a `--hash=sha256:…` line. Applying the generator's `^sha256=` selection matched nothing. This verifies the parsing defect without downloading wheels or editing the committed lock; a full target-environment lock/build remains Q3 work.

Related records: [earlier reliability program](2026-09-10-reliability-first-improvement-program.md), [phase-0 report](2026-09-11-phase0-report.md), [historical finding ledger](2026-09-11-phase0-finding-ledger.md), [release gates](../dev/release-gates.md), [desktop matrix](../dev/desktop-matrix.md), and [speech corpus specification](../eval/corpus-spec.md).
