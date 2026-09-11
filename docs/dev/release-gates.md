# Developer and release gates (plan P0.5)

How the repo is checked locally, and how a release ships — with every
gate that can fail it. The release path exists because five releases
through v0.8.1 shipped **without a single CI run**; these gates make
that impossible to repeat by accident.

Policy everywhere: **GitHub Actions stay manual-only** (`workflow_dispatch`
only — no push triggers), and publishing never happens without a green,
manually dispatched CI run for the **exact** commit being released.

## Local developer gates (`justfile`)

Application recipes are unprefixed; the agent-factory (SSSF) recipes all
live under the `factory-` prefix and are unchanged.

| Recipe | What it runs |
|---|---|
| `just lint` | `ruff check .` (config: `[tool.ruff.lint]` in pyproject.toml) |
| `just test-unit` | **canonical unit/contract gate** (quality plan Q1): `scripts/run_test_tier.sh unit` — offline (a conftest network guard fails any outbound socket), no model/display/GTK, `-W error`, `--strict-markers`, JUnit artifact, per-test timeout |
| `just test-gtk` | provisioned GUI lane (`run_test_tier.sh gtk`): GTK4/Adw + display; a **skip fails the tier** — unmet prerequisites are not a green |
| `just test-integration` | real subsystems (`run_test_tier.sh integration`): model, mic, daemon processes, packaging — never in any automatic gate |
| `just test` | dev convenience: whole suite minus integration, serial |
| `just test-parallel` | same scope on pytest-xdist `-n auto` (~4–5× faster) |
| `just gate` | clean-tree check + lint + brief validation + unit tier (+ gtk tier when a display is present) |
| `just gate-release` | `gate`'s checks but fully-clean tree including untracked files |
| `just validate-requests` | every `requests/*.md` brief carries one valid `STATUS: OPEN\|SHIPPED\|SUPERSEDED` header |

Notes:

- **One source of truth for tiers**: every tier command lives in
  `scripts/run_test_tier.sh`; the justfile, `ci.yml` and
  `release-prepare.yml` only call it. Never copy the pytest selection
  string anywhere else. CI's unit job and release-prepare run the exact
  command `just test-unit` runs (E6 closed).
- Capability markers (`model`, `network`, `desktop`, `packaging`, `gtk`,
  plus duration-only `slow`) select tests out of the unit gate; the
  real-download e2e transcription test moved under `tests/integration/`
  with `network`+`model` marks (E1 closed: the "offline" suite no longer
  downloads a model).
- The interpreter resolves to `.venv/bin/python` if present, else
  `$SAYIT_PY`, else `python3`. Worktrees that share the main tree's venv
  set `SAYIT_PY=/path/to/main/tree/.venv/bin/python` (see AGENTS.md). \
  The tier script resolves the interpreter the same way.
- `just gate` fails on **any** warning: `-W error` plus the
  unhandled-thread-exception error filter in `[tool.pytest.ini_options]`
  (added by the lifecycle work, P0.4).
- Bare `pytest` collects only `tests/` (`testpaths` in
  `[tool.pytest.ini_options]`) — the `adws/*_test.py` agent-factory
  scripts are no longer swept into test runs. Integration tests
  (`tests/integration/`) still need the real model and stay excluded
  from every offline gate via `--ignore`.
- Dev dependencies (`pip install -e ".[dev]"`): `pytest`, `pytest-xdist`,
  `pytest-timeout`, `ruff`.
- **Runner hygiene is a plugin** (Q2): the leak gate loads for every run
  shape — focused single-file, full, and each pytest-xdist worker — via
  `pytest_plugins` in the repository-root `conftest.py`
  (`tests/_runner_hygiene.py`). A leaked child or pending timer fails
  the run naming the test.
- **Session identity scoping** (Q2/E7): unit/gtk/dev runs pin
  `XDG_SESSION_TYPE=x11` for determinism; the integration tier
  (`scripts/run_test_tier.sh integration`) keeps the AMBIENT compositor
  identity so desktop matrices exercise the real session.
- **Tripwire external writes** (Q2): if the real history file changes
  while an external (non-suite) daemon is live, the failure message
  says so; export `FLUIDVOICE_TOLERATE_EXTERNAL_HISTORY_WRITES=1` on a
  daily-driver machine to downgrade exactly that classified case to a
  warning. Suite-owned writes never get that mercy.
- The deb's Ubuntu 24.04 / x86_64 / Python 3.12 target and the pinned
  container build are a decision, not a setting —
  [ADR-0004](../adr/ADR-0004-ubuntu-deb-contract.md).

## Release flow

Three manual steps, two workflows. Nothing runs automatically.

```text
  just release-prepare 0.8.2          (or: Actions → release-prepare → Run)
        │  clean-tree check, version sanity + bump,
        │  ruff, full offline suite with -W error,
        │  stale-constraints check, locked deb build
        │  in the pinned Ubuntu 24.04 container,
        │  commit "release: v0.8.2", push to linux
        ▼
  release SHA (printed by the run summary)
        │
  Actions → CI → Run workflow (branch linux = the release SHA)
        │  wait for green on that EXACT commit
        ▼
  just release-publish 0.8.2 <SHA>    (or: Actions → release-publish → Run)
        │  verifies everything (below), then tags v0.8.2
        │  and creates the GitHub release with the deb
```

### `release-prepare` (`.github/workflows/release-prepare.yml`)

Manual dispatch with the version. In order:

1. **Version sanity** — `X.Y.Z` shape, strictly newer than current, and
   `pyproject.toml` and `fluidvoice/__init__.py` already agree (refuses
   to "fix" a disagreement by bumping over it).
2. **Clean-tree check** — `git status --porcelain` must be empty.
3. **Version bump** — both files, then re-verified.
4. **Lint + unit tier with warnings as errors** — the exact canonical
   gate `just test-unit` runs (same script, same selection), plus brief
   status validation; headless on the runner, so GUI coverage is the
   provisioned gtk lane in ci.yml.
5. **Dependency-lock freshness** — every runtime dependency in
   `pyproject.toml` must have an exact `==` pin in
   `packaging/deb/constraints.txt`, and a `pip install --dry-run -c
   constraints.txt .` resolution must still succeed (catches bumped
   minimums the lock no longer satisfies). Regenerate the lock with
   `packaging/deb/update-constraints.sh`. Hash locking (F9): regenerate
   **with hashes** (the full, networked run — hashes cannot be fabricated
   offline) and commit the result on the release branch before the
   locked build; `build-deb.sh` then verifies every downloaded wheel
   (`--require-hashes -r`) automatically. The committed lock is pin-only
   between releases.
6. **Locked package build** — the deb is built in the pinned container
   (`packaging/deb/Dockerfile`, Ubuntu 24.04 / x86_64 / Python 3.12)
   against the hashed constraints; its control `Version` must equal
   `<version>-1`. The deb is uploaded as workflow artifact
   `deb-v<version>`.
7. **Commit + push** — `release: v<version>` pushed to `linux`; a
   non-fast-forward push (branch moved mid-run) fails the release.

### `release-publish` (`.github/workflows/release-publish.yml`)

Manual dispatch with the version and the release SHA. Every gate fails
loudly before anything public happens:

| Failure mode | Gate |
|---|---|
| Mismatched / missing CI SHA | a **completed, successful** run of `ci.yml` whose `head_sha` equals the release SHA must exist (manual dispatch is the only way CI runs) |
| Stale release commit | the SHA must be the current tip of `linux` |
| Version disagreement | input == `pyproject.toml` == `fluidvoice/__init__.py` == deb control `Version` |
| Dirty files | clean checkout required (`git status --porcelain`) |
| Stale constraints | same pins-coverage check as prepare |
| Wrong / missing artifact | the `deb-v<version>` artifact from the successful prepare run, unexpired, control version verified |
| Double release | tag `v<version>` must not exist yet |

Only then: push the tag and create the GitHub release with the deb.
Release notes default to the `[Unreleased]` section of `CHANGELOG.md`;
pass the `notes` input to override.

House-style manual follow-ups after publish (unchanged): AUR recipe
bump (`packaging/aur`), Reddit/HN post, machine refresh
(`scripts/install-one-shot.sh`).

## Speech-pipeline evaluation gate (plan P1.4)

Before any release that changes the speech pipeline — backends,
transcription decoding, the hallucination guard, or transcript
post-processing — a **manual representative-model evaluation** through
the local evaluation harness is required (`docs/eval/README.md`):

1. Run the harness over a corpus that includes real speech for the
   languages you ship (`--external` private corpus + the committed
   synthetic one), with the representative model plugged in via
   `--transcriber`.
2. Attach the resulting `report.json`/`report.md` to the release
   notes; WER/CER/hotword-recall regressions and any guard
   false-positive/false-negative change block the release until
   explained.
3. Soak (same policy doc): a **2-hour soak** before major releases,
   and a documented **24-hour run** for lifecycle changes (idle-unload,
   model reload/hot-swap, memory work) — `scripts/soak.py` CSV embedded
   with `evalharness run --soak-csv`.

The harness's metric math is exercised in CI without any model; the
model-dependent half of this gate is the manual step above.

## History

`scripts/release.sh` is the pre-P0.5 interactive single-host release
helper. The workflow pair above is now the canonical release path (it
adds the CI-on-exact-SHA requirement and the warning/dirty/version/
constraints gates the old script did not have); the script remains in
the tree as a reference for the manual follow-up steps.
