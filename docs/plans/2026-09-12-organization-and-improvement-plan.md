# Organization and improvement plan

- Date: 2026-09-12
- Status: ACTIVE — Phases 1–4 shipped 2026-09-13/14 (v0.8.2 published
  with provenance evidence; docs architecture complete); see the
  implementation log at the end
- Audited source: `59aa282` on `linux`, application version `0.8.1`
  (113 commits unreleased since the `v0.8.1` tag)
- Scope: repository layout, documentation, code structure, tests/CI,
  release cadence, and the open evidence gaps. Product bets and
  monetization stay governed by the
  [product-excellence plan](2026-09-11-product-excellence-and-monetization-plan.md);
  test infrastructure by the
  [quality plan](archive/2026-09-11-project-quality-and-testing-plan.md). This plan
  does not repeat their items, it sequences what is left and adds the
  organization work neither covers.

## Summary

The software is in good shape and the engineering discipline is high:
the unit tier is green and fast, lint and the focused type check are
clean, decisions are recorded as ADRs, briefs carry validated STATUS
headers, and the September reliability and quality programs landed with
evidence. The weak points are all around the code, not in it:

1. **Documentation has drifted from the code.** Test counts, file paths
   and feature states in README/STATUS/quality-queue are wrong in at
   least ten places, and two of the cited files do not exist.
2. **The repository mixes three projects**: the product, a vendored agent
   factory (`adws/`, `.claude/skills/sssf/`, half the justfile), and
   planning/scratch artifacts (`specs/` with `_v2.._v4` copies,
   `scripts/tmp_*`, `.zcode/`).
3. **Docs have no information architecture.** Evidence, plans, run logs
   and design specs share `docs/research/`; the 37 KB README doubles as
   the user manual; the 44 KB STATUS ledger mixes capabilities with history.
4. **A large, verified improvement delta is unreleased.** 113 commits
   (+22.5k/−2.9k lines in `fluidvoice/` and `tests/`) since v0.8.1,
   including real bug fixes such as the lock-screen pause gate that never
   engaged.
5. **The remaining quality and product gates are blocked on the outside
   world**: the live Wayland matrix, a real-speech corpus, and
   install/soak VMs. They need decisions and access, not code.

Recommended order: fix documentation truth (1 day) → tidy the repository
(1 day) → cut v0.8.2 → restructure docs → targeted code refactors driven
by churn → unblock external evidence in parallel.

## Verified baseline (2026-09-12, `59aa282`)

| Check | Result |
|---|---|
| Unit/contract tier (`just test-parallel`) | **3452 passed, 4 skipped**, 20.1 s |
| Display/GTK tier (collection) | 154 tests (`-m needs_display`) |
| Integration tier (collection) | 40 tests |
| Lint (`ruff check .`) | clean |
| Types (`mypy`) | clean — 5 files in scope |
| Branch coverage (unit tier) | 79.9% line / 78.9% branch ([baseline](../research/2026-09-12-coverage-baseline.md)); display tier 29.1% |
| Request briefs | 31: 29 SHIPPED, 2 OPEN (`wayland-matrix-execution`, `speech-corpus-recording`) |
| Package size | `fluidvoice/` 27.5k lines; 33 top-level modules + 7 subpackages |
| Test size | `tests/` 96 files, 34.7k lines, flat directory |
| Largest modules | overlay 1476, daemon 1428, config 1324, hotkey 1100, insertion 1060 lines |
| Functions > 100 lines | 14 of 1342 (largest: `_build_dictation` 443 lines) |
| `except Exception` sites | 257 (hotkey 35, daemon 24, overlay 17, insertion 14) |
| Churn, all time | daemon.py 62 commits, config.py 46, settings_window.py 31, cli.py 20 |
| Churn since v0.8.1 | daemon.py 12, pipeline.py 6, mcp_server.py 6, history/control_server/config 5 each |
| Git | 285 commits on `linux`; 7 worktrees (all clean); 13 local branches already merged; 2 unmerged |
| GitHub | 0 open issues; latest release v0.8.1 (2026-09-09) |

### What to keep as-is

- The coordinator decomposition (`CaptureCoordinator`,
  `SpeechEngineManager`, `CommandCoordinator`, `RuntimeTasks`), the
  `SpeechBackend` seam with its shared contract suite, the `SettingSpec`
  config registry, and transactional JSONL history.
- The tier model (`needs_*` markers, conftest network guard, leak gate,
  skip-fails-in-CI lane) and the release provenance chain (Q3/Q4).
- ADR non-goals: no TCP listener, no telemetry, Linux only, manual CI,
  Ubuntu 24.04-only deb.
- The `fluidvoice` module name. It is deliberate and documented, and
  renaming it would churn 285 commits of blame and every open branch.

---

## Findings

### A. Documentation truth drift (verified)

| # | Where | Claim | Reality |
|---|---|---|---|
| A1 | `README.md:15`, `README.md:670` | "1782 automated tests at v0.8.1" | 3452 unit + 154 display + 40 integration |
| A2 | `README.md:665` | tier commands live in `scripts/run_test_tier.sh` | file does not exist; selection lives in `justfile:41` and is **duplicated** inline in `ci.yml:48` |
| A3 | `README.md:673-677` | integration row "29"; table has 2 columns, row has 3 cells (malformed) | 40 integration tests |
| A4 | `README.md:465` | "Per-app prompt sets 🚧 roadmap" | shipped: Settings → AI → "Per-app prompts" (`gtkui/settings_pages/ai.py:113`) |
| A5 | `README.md:520-521` | socket `transcribe` "rejects files over 200 MB (v1 does not chunk)" | no 200 MB limit found in `fluidvoice/`; daemon transcription routes through chunking since `ddfa9a2` — re-verify and fix |
| A6 | `README.md:683-686` | "Layout:" lists 7 modules | omits coordinators, `context/`, `evalharness/`, `gtkui/`, IPC |
| A7 | `docs/STATUS.md:3-9` | "3531 automated offline tests + 38 integration", updated 2026-09-11 | pre-quality-plan numbers |
| A8 | `pyproject.toml:63-68` | "1759 tests: ~69 s serial" | stale comment |
| A9 | `docs/dev/quality-queue.md:24-35` | Q3–Q12 "TODO"; commands `just test-unit` / `just test-gtk`; root `conftest.py`, `tests/_runner_hygiene.py` | Q3–Q7/Q11/Q12 shipped; recipes are `just test` / `just test-ui`; neither cited file exists (superseded design) |
| A10 | `tests/conftest.py:175`, `:215` | leak gate lives "in tests/test_runner_hygiene.py" | gate is `tests/conftest.py:252` |

Root cause: facts such as test counts and paths are written by hand in
several places. The fix is fewer copies (A2), not more careful
copying.

### B. Repository organization

- **B1 — vendored agent factory inside the product repo.** `adws/`
  (12 workflow scripts + modules), `.claude/skills/sssf/` (93 files,
  ~1 MB), 15 `factory-*` justfile recipes, a 16.8 MB `sssf.db`
  (ignored), and ruff per-file-ignores exist only for it. It is useful
  tooling but not SayItErmano, and it makes the tree harder to read for
  contributors.
- **B2 — `specs/` is an unindexed archive.** 34 ADW implementation plans
  keyed by hash, with four superseded copies
  (`settings-depth-pack_v2..v4`, `wayland-session-support_v2`), no STATUS
  headers, and no validator. The canonical record is `requests/`
  (validated) plus git history.
- **B3 — scratch files are tracked.** `scripts/tmp_learn_*.py/.json`
  (4 files, analysis from `1bcefc0`) and `.zcode/plans/*.md` (a
  tool-session plan).
- **B4 — icons are spread across three places.** `design/icons/`,
  `packaging/icons/`, `fluidvoice/assets/`, with at least one
  byte-identical duplicate. `scripts/gen-app-icon.py` should be the only
  producer.
- **B5 — stale worktrees and branches.** 6 extra worktrees and 13
  merged local branches. `agent/f3-bench` holds **unmerged real work**
  (`655f778` TTS decode baseline: real-model WER/latency/hallucination),
  which feeds Q8. `agent/commercial-research-20260911` (`a703c78`) has
  the same subject as `5efc25d` on `linux` and is probably a duplicate.
  Diff them before deleting.

### C. Documentation architecture

- **C1** — No `docs/README.md` index. Navigation depends on the
  companion-docs line in STATUS.md.
- **C2** — `docs/research/` holds four kinds of document: evidence
  (reviews, issue sweeps, HCI papers), plans (reliability program,
  quality plan, product plan), run records (phase-0 baseline, ledger,
  journey map, coverage baseline), and data. Readers cannot tell which
  plan is active.
- **C3** — The README is a 37 KB user manual (install, Wayland, command
  mode, remote STT, socket/MCP, full config, testing). Moving the guides
  out would let the README do its job: explain what the app is, show it,
  and get it installed.
- **C4** — STATUS.md (44 KB) mixes "what works today" with narrative
  history and incident residuals. The history belongs in the CHANGELOG
  and the residuals in a known-limitations section.
- **C5** — `docs/superpowers/specs/` is named after a tool, not its
  content (design specs).

### D. Code structure (evidence-weighted, not size-weighted)

The quality plan warns against refactoring on file length alone. The
targets below combine size with churn and change risk:

- **D1 — `daemon.py` is still the hotspot.** It has the most commits all
  time (62) and since the last release (12), imports 16 package modules,
  has 24 broad excepts, and its control dispatch `handle_request`
  (`daemon.py:990`, 106 lines) grows with every socket/MCP route
  (mcp_server and control_server each have 5–6 commits since v0.8.1).
  The next coordinator extraction is a route table for control
  requests.
- **D2 — `cli.main` is 241 lines** (`cli.py:16`, 20 commits): argparse
  setup and dispatch in one function. A subcommand→handler table makes
  each command testable in isolation.
- **D3 — GTK page builders are monoliths.** `_build_dictation` is 443
  lines (`settings_pages/dictation.py:12`), `_build_models` 161,
  `_build_ai` 115, and `MainWindow.__init__` 198. Display-tier coverage
  is 29%, and `gtkui/main_window.py` and `onboarding.py` sit at 0% in the
  unit tier. Splitting builders into per-section functions (hotkeys,
  languages, mic, commands) makes them unit-testable in pieces.
- **D4 — flat top-level package.** 33 modules at `fluidvoice/`. A
  layered grouping would help navigation (see the target layout below),
  but a big-bang move costs blame history, breaks the open worktrees and
  `agent/f3-bench`, and buys no correctness. Defer; move a subsystem
  only when you are already reworking it, and leave a re-export shim.
- **D5 — broad exception handling.** 257 `except Exception` sites.
  House style keeps them deliberately on best-effort paths, so the
  target is *silent* swallows (`except Exception: pass` with no log),
  which hide real failures from the doctor/log workflow. Measure first,
  then ratchet.
- **D6 — narrow typing and lint scope.** mypy covers 5 files. The next
  candidates are the IPC and persistence boundaries, where type errors
  corrupt data or protocol: `config.py`, `history.py`,
  `control_server.py`, `mcp_server.py`. Ruff could add bugbear rules that
  matter for this code's threads and GTK callbacks (B006 mutable
  defaults, B023 loop-variable closures).

### E. Tests and CI

- **E1** — Tier selection has two sources, `justfile:41` and `ci.yml:48`
  (plus release-prepare). They can drift silently, which is the exact
  failure mode the quality plan's Q1 set out to remove.
- **E2** — The coverage floor is review-only. Nothing fails a CI run
  that drops below 79%.
- **E3** — `tests/` is 96 flat files. Finding the tests for a module
  relies on naming luck (e.g. `test_audit_fixes.py`, `test_infra.py`).
- **E4** — The `gtk-x11` CI lane's first dispatch is still listed as a
  pending acceptance item (quality-queue). No run is recorded in the
  evidence index.
- **E5** — CI is manual-only by project rule, so a regression can sit on
  `linux` until someone dispatches it. That is a maintainer decision, not
  a defect (see "Decisions owed").

### F. Release and evidence (already known, still open)

- **F1** — 113 unreleased commits. Users on v0.8.1 lack the P0–P2
  reliability work and the Q-plan bug fixes (`_on_locked` pause gate,
  command-confirm watchdog race, TOML DEL escaping, dead-capture
  hallucination guard).
- **F2** — Live Wayland/X11 smoke matrices: never run ("Runs: none
  yet"). They block honest Wayland claims and the `context.enabled`
  default flip.
- **F3** — The real-speech corpus (150–300 utterances, 10–15 speakers)
  blocks Q8 and every "trustworthy speech" gate.
- **F4** — Q9 install/upgrade/rollback/soak needs disposable VMs. Q10
  recovery UX waits on the evidence from Q6–Q8.

---

## Plan

Each item lists its acceptance criteria. Efforts are rough focused days.

### Phase 1 — Documentation truth pass (≈1 day, no code risk)

| Item | Work | Acceptance |
|---|---|---|
| 1.1 | Fix A1–A10 in place. For A5, read the socket `transcribe` path first and document the real bound. | Every claim in the table above matches code; `rtk proxy grep -rn "1782\|3531\|1759\|run_test_tier\|_runner_hygiene\|test-unit\|test-gtk"` returns only historical plan text |
| 1.2 | Stop hand-copying counts. Drop the numeric test badge, or give README/STATUS one sentence pointing at `just test-parallel` output, instead of numbers in four places. | No test count appears outside dated research/run documents |
| 1.3 | Mark `docs/dev/quality-queue.md` SUPERSEDED at the top, pointing to the plan's implementation log. | One authoritative Q-item tracker |
| 1.4 | Refresh the STATUS.md header (date, commit, verified numbers). | Header matches the baseline table above |

### Phase 2 — Repository tidy (≈1 day)

| Item | Work | Acceptance |
|---|---|---|
| 2.1 | Move `scripts/tmp_learn_*` to `docs/research/data/2026-09-11-learning-session/` (they back `1bcefc0`), or delete them if the research note already quotes the results. | No `tmp_` files tracked |
| 2.2 | `git rm --cached .zcode/`; add `.zcode/` to `.gitignore`. | Tool state untracked |
| 2.3 | `specs/`: keep only the final version of each plan and move the directory to `docs/plans/archive/specs/`. Or, if the factory leaves the repo (2.4), send it along. | No `_vN` duplicates; README in the folder says "historical ADW plans, see `requests/` for status" |
| 2.4 | **Decision owed:** the agent factory. Option A, recommended: move `adws/`, `.claude/skills/sssf/` and the `factory-*` recipes into a separate `sssf-factory` repo (or a submodule) and load it with a small `justfile` import. Option B: keep it in-repo under `tools/factory/` with its own README. | Product tree contains only product code, docs, packaging, tests (A); or the factory is self-contained in one folder (B) |
| 2.5 | Icons: single source (`design/icons/` → generator → `packaging/icons/` + `fluidvoice/assets/`); remove byte duplicates; document in `design/README.md`. | `scripts/gen-app-icon.py` regenerates every shipped icon; no identical files in two tracked paths |
| 2.6 | Branch/worktree cleanup: merge or close `agent/f3-bench`, diff and drop `agent/commercial-research-20260911`, remove the merged worktrees (`product-plan`, `quality-plan`, `qplan`) and the 13 merged branches. Keep `FluidVoiceLinux-agent` per AGENTS.md. | `git branch --no-merged linux` is empty or intentional; `git worktree list` shows main + agent tree |

### Phase 3 — Cut v0.8.2 (≈0.5 day + CI time)

| Item | Work | Acceptance |
|---|---|---|
| 3.1 | Dispatch CI on the release candidate SHA, including the never-run `gtk-x11` lane (E4). Record the run ids in `docs/quality/evidence-index.md`. | All three CI jobs green on one SHA |
| 3.2 | `just release-prepare 0.8.2` → review provenance → `just release-publish`. The CHANGELOG `[Unreleased]` section becomes v0.8.2 (it currently covers only P0.6 packaging and must be extended with P1/P2/Q-plan highlights). | Release published through the Q4 provenance gate; CHANGELOG complete |
| 3.3 | Update the README "What's new" with a short v0.8.2 entry and move older entries to the CHANGELOG. | README shows at most the two latest releases |

Shipping first means every later refactor starts from a released
baseline.

### Phase 4 — Documentation architecture (≈1–2 days)

Target layout (moves use `git mv`; update inbound links in the same commit):

```
README.md                 what it is · demo · install (short) · links
CHANGELOG.md
docs/
  README.md               NEW: index — "start here" per audience (user / contributor / maintainer)
  guides/                 NEW: moved out of README
    install.md            deb, pipx, AUR, source, one-shot, updates
    configuration.md      full config reference (generated from SettingSpec — see 4.3)
    wayland.md            matrix + tool setup
    command-mode.md
    remote-stt.md
    scripting-and-mcp.md  socket API, MCP, security note
    file-transcription.md
  STATUS.md               capabilities + known limitations only (history → CHANGELOG)
  ROADMAP.md
  glossary.md  BEHAVIOR-SPEC.md  COMPARISON.md  UPSTREAM-TRACKING.md
  adr/
  design/                 was superpowers/specs/
  dev/                    + testing.md (tier model, one canonical place)
  eval/
  quality/
  plans/                  NEW: active plans, each with a Status: header
    archive/              completed/superseded plans (+ old specs/)
  research/               evidence only: reviews, sweeps, papers, run records
    data/                 raw analysis artifacts
  screenshots/
```

| Item | Work | Acceptance |
|---|---|---|
| 4.1 | Create `docs/README.md` and move plans into `docs/plans/` (active: this plan, product-excellence; archive: reliability program, quality plan, phase-0 set). | Every doc reachable from `docs/README.md`; each plan has a Status header |
| 4.2 | Split the README into `docs/guides/`. Target size: under 12 KB. | README covers install → first dictation in one screen; guides linked |
| 4.3 | Generate the configuration reference from the `SettingSpec` registry (`fluidvoice/config.py`) with a small script plus a test that fails when the doc is stale. | Config docs cannot drift from code |
| 4.4 | Trim STATUS.md to "works today" and "known limitations"; move the narrative into the CHANGELOG and ADRs. | STATUS under 15 KB; no duplicated release narrative |
| 4.5 | Add a docs link check (relative links + referenced repo paths exist) to `just gate`. It would have caught A2 and A9. | Gate fails on a dangling path in docs |

### Phase 5 — Targeted code and test improvements (≈4–6 days, incremental)

Order by risk × churn. Each item is its own commit, green gate, and
behavior-preserving change with tests first where coverage is thin.

| Item | Work | Acceptance |
|---|---|---|
| 5.1 | **Single tier source (E1).** Either create `scripts/run_test_tier.sh <tier>` for real and call it from justfile, `ci.yml` and `release-prepare.yml`, or install `just` in CI and call `just gate`. | The marker expression appears once in the repo |
| 5.2 | **Coverage floor (E2).** Add `--cov-fail-under=79` to the CI unit job and `just coverage`; raise it as coverage grows. | CI fails below the floor |
| 5.3 | **Control-route table (D1).** Extract `Daemon.handle_request` into a `control_routes` module: a `{action: handler}` registry shared by the socket and MCP bridges, one handler per route, with the existing socket/MCP tests as the safety net. | `handle_request` under 30 lines; adding a route touches one module; tests unchanged and green |
| 5.4 | **CLI subcommand table (D2).** Split `cli.main` into per-command handler functions registered with argparse subparsers. | `main` under 40 lines; `test_cli_ui_hotkey.py`, `test_transcribe_cli.py` green |
| 5.5 | **Settings page builders (D3).** Split `_build_dictation` (and models/AI) into section builders; add display-tier tests per section. | No builder over 120 lines; display coverage of `settings_pages/` up measurably |
| 5.6 | **Silent swallows (D5).** Count `except Exception:` + bare `pass`/`return` with no log. Enable ruff `S110` (try-except-pass), baseline existing sites with `# noqa: S110 — <reason>`, and forbid new ones. | New silent swallows fail lint; each remaining one carries a reason |
| 5.7 | **Typing/lint ratchet (D6).** Add `config.py`, `history.py`, `control_server.py`, `mcp_server.py` to mypy `files`; enable ruff `B006`, `B023`. | `just typecheck` and `just lint` clean with the wider scope |
| 5.8 | **Test layout (E3), optional.** When touching tests, place them under subdirectories that mirror the package (`tests/gtkui/`, `tests/backends/`, `tests/processing/`, `tests/eval/`, `tests/desktop/`). Rename catch-alls (`test_audit_fixes.py`, `test_infra.py`) by subject. No bulk move. | New tests land in mirrored folders; conftest unchanged |
| 5.9 | **Package grouping (D4), deferred.** Only when a subsystem is being reworked anyway: move it into a subpackage with a compatibility re-export. Candidate groups: `runtime/` (daemon, capture, command_coord, engine_manager, runtime_tasks, pipeline, session, preview, chunking), `desktop/` (hotkey, evdev_ptt, insertion, selection, lockmon, micmon, tray, overlay, media, recorder), `ipc/` (control, control_server, mcp_server, + 5.3's routes), `models/` (model_catalog, model_download). | Each move is one commit with a shim; no behavior change; blame preserved via `git mv` |

### Phase 6 — Unblock external evidence (parallel, calendar-bound)

These belong to existing plans. They are listed here so the sequence is
complete and the blockers are named.

| Item | Blocker | Next action |
|---|---|---|
| 6.1 Wayland/X11 live matrix ([brief](../../requests/wayland-matrix-execution.md)) | no Wayland session on the dev machine (ROADMAP: no compositor installed, GNOME runs X11) | install/enable a GNOME-Wayland or sway session on the same box, or use a VM without GPU passthrough (the CPU `base` model is enough for insertion cases) |
| 6.2 Real-speech corpus ([brief](../../requests/speech-corpus-recording.md)) | consent text + storage approval | approve `docs/eval/consent-text.md` and the `eval-private/` location; then recruit |
| 6.3 Q8 speech/perf baseline | 6.2 (partially covered by the `agent/f3-bench` TTS baseline) | merge `655f778` as the interim synthetic-voice baseline, clearly labeled as not real speech |
| 6.4 Q9 install/upgrade/rollback + soak | disposable VMs | one Ubuntu 24.04 VM snapshot + one Arch VM; script the soak with `scripts/soak.py` |
| 6.5 Q10 recovery UX | evidence from 6.1–6.3 | start only after 6.1 lands |
| 6.6 Product phases 1–5 (user tests, monetization) | the gates above | per the product-excellence plan; no billing work before its gates |

---

## Sequence at a glance

| Week | Work | Done when |
|---|---|---|
| 1 | Phase 1 (doc truth), Phase 2 (tidy, incl. the factory decision), Phase 3 (v0.8.2) | v0.8.2 published; zero known false claims in docs; tree holds only product files |
| 2 | Phase 4 (docs architecture) + 5.1/5.2 (single tier source, coverage floor) | `docs/README.md` index; README < 12 KB; CI gate matches local gate by construction |
| 3–4 | 5.3–5.7 (daemon routes, CLI table, page builders, swallow lint, typing ratchet) | each item's acceptance met, suite green, coverage floor raised |
| ongoing | Phase 6 as access and approvals arrive; 5.8/5.9 opportunistically | briefs move OPEN → SHIPPED with evidence |

## Decisions owed by the maintainer

1. **Agent factory:** separate repo/submodule (recommended) or `tools/factory/` in-repo (2.4).
2. **`specs/` fate:** archive under `docs/plans/archive/`, or leave with the factory (2.3).
3. **CI trigger policy:** keep manual-only, or allow a cheap push-triggered `lint + unit` job on `linux` (E5). The current rule is deliberate; revisit only if regressions have slipped through.
4. **Test-count badge:** drop it, or keep it and generate it at release time (1.2).
5. **Wayland access and corpus consent** (6.1, 6.2): carried over from the phase-0 report, still open.

## Guardrails

- Do not re-implement shipped briefs (`requests/*.md` STATUS). Check
  `git log` first.
- One tree per agent (AGENTS.md). Organization moves (Phases 2 and 4)
  touch many paths, so do them in the main tree with no parallel session
  running.
- `git mv` for every relocation; update links in the same commit;
  `just gate` green after each commit.
- No refactor without a churn or defect reason (see D). No feature work
  sneaks into organization commits.
- ADR non-goals stay non-goals.

## Reproduction commands

```bash
# baseline numbers
just test-parallel                                   # 3452 passed, 4 skipped
.venv/bin/python -m pytest --collect-only -q tests -m needs_display | tail -1   # 154
.venv/bin/python -m pytest --collect-only -q tests/integration | tail -1        # 40
.venv/bin/python -m ruff check . && .venv/bin/python -m mypy

# drift checks
rtk proxy grep -rn -E '1782|3531|1759|run_test_tier|_runner_hygiene' README.md docs pyproject.toml tests
ls scripts/run_test_tier.sh conftest.py tests/_runner_hygiene.py     # all missing

# churn / release delta
rtk proxy git rev-list --count v0.8.1..linux                          # 113
rtk proxy git log --name-only --format= -- fluidvoice | sort | uniq -c | sort -rn | head

# hygiene
git ls-files scripts/tmp_* .zcode
git branch --merged linux ; git branch --no-merged linux ; git worktree list
```

---

## Implementation log

### 2026-09-13/14 — Phases 1–3

- **1.1/1.2/1.4** — `ded87aa` fixed A1, A3–A8, A10, dropped
  hand-copied counts, refreshed the STATUS header. A2's README tier
  text was corrected in the same pass (tier selection now points at the
  justfile + CI mirror). **1.3** (quality-queue SUPERSEDED banner)
  landed with 4.1.
- **2.1–2.5** — `02f0b16` (scratch → `docs/research/data/`),
  `39d3450` (.zcode untracked), `ac8646b` (specs →
  `docs/plans/archive/`), `e67df35` (factory → `tools/factory/`,
  option B), `61367f4` (icon pipeline single-source).
- **2.6** — worktrees/branches reduced to main + agent (AGENTS.md) +
  the active night branch (`agent/night/20260914-browser-paste-cells`,
  seeded `a678503`, carries the F-38/F-39b evidence); `agent/f3-bench`
  merged as `2cc130c`. No further cleanup owed.
- **3.1–3.3** — CI dispatched on the release SHA (incl. the first
  `gtk-x11` lane), `just release-prepare/publish` for **v0.8.2**
  (`4851af1`), release evidence recorded (`3061d6e`), CHANGELOG and
  README What's-new updated (`550c192`, `751e6ca`).

### 2026-09-14 — Phase 4.1

Plans moved out of `docs/research/` into `docs/plans/` (active: this
plan, product-excellence) and `docs/plans/archive/` (reliability
program, quality plan, phase-0 report/baseline/journey map);
`docs/README.md` created as the audience index; every inbound link
updated in the same commit. **Deviation:** the phase-0 finding ledger
stays in `docs/research/` — it is a living run record (night matrix
appends through 2026-09-14), not a plan.

### 2026-09-14 — Phase 4.2 + 4.5

**4.2** — `752a43a`: the 36 KB README split into `docs/guides/`
(install, configuration, wayland, command-mode, remote-stt,
file-transcription, scripting-and-mcp, dev/testing); README now 12 KB,
front-door job only; guides indexed from `docs/README.md`.
**4.5** — `55df225`: `scripts/check_docs_links.py` in `just gate` +
unit-tier mirror (`tests/test_docs_links.py`), so CI enforces it too.

### 2026-09-14 — Phase 4.3

`scripts/gen_config_reference.py` renders the "All settings" block of
`docs/guides/configuration.md` (one table per `[section]`: key,
default, apply/secret notes, effect) straight from
`fluidvoice.config.REGISTRY` — the same one registry `config init`
renders its commented template from. `--check` is a gate step and
`tests/test_config_reference.py` the unit-tier mirror, so a registry
change without regenerating the doc fails the suite. The hand-curated
TOML highlights were replaced by a 3-key quick start plus the
generated 108-key reference; the duplicated intro sentence (a 4.2
assembly artifact) fixed in passing.

### 2026-09-14 — Phase 4.4 (Phase 4 complete)

STATUS.md rewritten from a 44 KB narrative ledger to a 15 KB
"works today + known limitations" doc: per-area capability bullets,
a consolidated limitations section (paste-verify residuals, Wayland
degradations + pending matrices, English-only guards, no real-speech
corpus), the divergence table kept (the one unique decision ledger,
cells tightened), verification pointers to dev/testing.md + the
evidence index. The narrative moved to the CHANGELOG, which also got
the fixes it owed: the [Unreleased] section was actually the v0.8.2
release notes (release-prepare never retitled it) — now `## [v0.8.2]`
with a fresh empty Unreleased above; backfilled the never-mentioned
lock suppression + dictionary auto-learning (v0.6.0) and mic priority
fallback (v0.4.0).

### 2026-09-14 — Phase 5.1 + 5.2

**5.1** — `scripts/test_tier.py` is the single tier-selection source
(marker expression + path selection per tier); justfile (7 recipes),
ci.yml (unit + gtk-x11) and release-prepare.yml all exec it with their
own strictness/coverage flags. The unit expression went from three
executable copies to one; `tests/test_tier_source.py` guards against
re-inlining and pins the per-tier argv.
**5.2** — coverage floor enforced: `--cov-fail-under=71` in `just
coverage` and the CI unit job, pinned together by the same test.
Measured 71.95% post-v0.8.2 (code grew faster than the suite since
the 09-12 baseline's 79.9% — recorded in the baseline doc); a floor
of 72 flapped on ±0.01 jitter, 71 is the stable start of the ratchet.

### 2026-09-14 — Phase 5.3

`4d804cb`: `Daemon.handle_request` (106 lines) → 5-line delegation to
new `fluidvoice/control_routes.py` — one handler per action, a
`{action: handler}` ROUTES table, dispatch() with the byte-identical
unknown-action error. Socket + MCP share it by construction (MCP
forwards action dicts over the socket). Bodies extracted verbatim;
the 143 daemon/control/MCP tests unchanged and green; the full gate
re-run green. Infra side-fix `707b3e0`: a bleeding-edge user-site
numpy (PEP 695 stubs) had silently broken `just typecheck` — the
recipe now runs under PYTHONNOUSERSITE=1, restoring the 3.11 floor.

### 2026-09-14 — Phase 5.4

`cli.main` (241 lines) → `_build_parser()` with `set_defaults(func=…)`
per subcommand + one `_cmd_*` handler each (the five control
round-trips share `_cmd_remote`); `main()` is 7 lines. Bodies
verbatim, CLI suites unchanged and green.

Remaining: Phase 5 items (5.5 page builders, 5.6 silent-swallow lint,
5.7 typing ratchet), Phase 6 as external access arrives; 5.8/5.9
opportunistically.
