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

Tier model (quality plan Q1): the `-m` filter deselects by DECLARED
requirement (`needs_display` / `needs_model` / `needs_network` markers,
plus `integration`/`desktop`), so local and CI collection lists match no
matter what the dev machine happens to have installed.

| Recipe | What it runs |
|---|---|
| `just lint` | `ruff check .` (config: `[tool.ruff.lint]` in pyproject.toml) |
| `just test` | unit/contract tier, serial: pytest `-m "<unit filter>"` |
| `just test-parallel` | same scope on pytest-xdist `-n auto` (~4–5× faster) |
| `just test-ui` | display/GTK tier on a VIRTUAL display by default (xvfb-run + GSK_RENDERER=cairo + `-n auto`, ~6 s, nothing flashes on the real desktop); `SAYIT_TEST_REAL_DISPLAY=1` opts onto the live display |
| `just test-integration` | real model/mic/daemon: `tests/integration` |
| `just test-process` | MODEL-FREE process lane (Q7): real daemon/socket/CLI subprocesses, no GPU/model/network — works headless or under `xvfb-run -a just test-process` |
| `just coverage` / `just coverage-ui` | branch coverage per tier (Q5 baseline: docs/research/2026-09-12-coverage-baseline.md) |
| `just gate` | clean tree (tracked) + lint + request validation + unit tier with `-W error -ra --strict-markers --strict-config --timeout=300 --junitxml` |
| `just gate-release` | `just gate` + untracked files also refused (what release-prepare checks) |
| `just validate-requests` | every `requests/*.md` brief carries one valid `STATUS: OPEN\|SHIPPED\|SUPERSEDED` header |

Notes:

- The interpreter resolves to `.venv/bin/python` if present, else
  `$SAYIT_PY`, else `python3`. Worktrees that share the main tree's venv
  set `SAYIT_PY=/path/to/main/tree/.venv/bin/python` (see AGENTS.md).
- `just gate` fails on **any** warning: `-W error` plus the
  unhandled-thread-exception error filter in `[tool.pytest.ini_options]`
  (added by the lifecycle work, P0.4).
- The unit tier is enforced offline IN-PROCESS: a conftest autouse guard
  raises on any non-loopback `connect()` (loopback fake servers stay
  allowed; `integration`/`desktop`/`needs_network`-marked tests are
  exempt). Real-model/network tests live under `tests/integration`.
- The leak gate (nothing a test starts outlives it) lives in
  `tests/conftest.py` and applies to EVERY invocation — focused runs,
  `--lf`, and pytest-xdist workers included (Q2);
  `tests/test_leak_gate_meta.py` drives real pytest subprocesses to pin
  that.
- A provisioned lane (CI's gtk-x11 job) sets
  `SAYIT_TEST_REQUIRE_MARKERS=needs_display`: a tier test or module that
  skips there FAILS the lane instead of silently shrinking coverage.
- Bare `pytest` collects only `tests/` (`testpaths` in
  `[tool.pytest.ini_options]`) — the `adws/*_test.py` agent-factory
  scripts are not swept into test runs.
- Dev dependencies (`pip install -e ".[dev]"`): `pytest`, `pytest-xdist`,
  `pytest-timeout`, `ruff`.
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
4. **Lint + request validation + offline suite with warnings as
   errors** — the canonical unit gate scope and `-W error` strictness of
   `just gate`.
5. **Dependency lock: hash-locked + fresh (Q3)** —
   `packaging/deb/locklib.py validate --require-hashes` (the lock must
   carry wheel hashes; pin-only locks fail the release build), the
   shared `locklib pins-coverage` check (every runtime dep exactly
   pinned), and a `pip install --dry-run -c constraints.txt .`
   resolution check. Regenerate with
   `packaging/deb/update-constraints.sh` (networked run).
6. **Locked package build** — the deb is built in the pinned container
   (`packaging/deb/Dockerfile`, Ubuntu 24.04 / x86_64 / Python 3.12)
   against the hashed lock; its control `Version` must equal
   `<version>-1`.
7. **Provenance manifest (Q4)** — `scripts/release_verify.py manifest`
   binds the built deb to its source: package sha256/size/control
   version, a digest over every tracked file of the post-bump
   (pre-commit) worktree, the dependency lock's sha256, and the prepare
   run's identity. The manifest is uploaded IN THE SAME ARTIFACT as the
   deb (`deb-v<version>`), and the summary prints the deb sha256.
8. **Commit + push** — `release: v<version>` pushed to `linux`; a
   non-fast-forward push (branch moved mid-run) fails the release.

### `release-publish` (`.github/workflows/release-publish.yml`)

Manual dispatch with the version, the release SHA, and **required
evidence**. Every gate fails loudly before anything public happens:

| Failure mode | Gate |
|---|---|
| Mismatched / missing CI SHA | a **completed, successful** run of `ci.yml` whose `head_sha` equals the release SHA must exist (manual dispatch is the only way CI runs) |
| Stale release commit | the SHA must be the current tip of `linux` |
| Version disagreement | input == `pyproject.toml` == `fluidvoice/__init__.py` == deb control `Version` |
| Dirty files | clean checkout required (`git status --porcelain`) |
| Wrong prepare run | the successful `release-prepare` run whose `head_sha` is the **parent** of the release SHA is resolved by `scripts/release_verify.py select-prepare-run` (prepare pushes the bump commit after building, so its own head_sha is the parent — never the release SHA) |
| Stale / foreign artifact | the `deb-v<version>` artifact is resolved **by that run id** (`select-artifact`), unexpired; a same-version artifact from any other run is rejected |
| Untested source | the manifest re-verifies: deb sha256/size/control match, the tracked-source digest of this checkout equals the digest of the tree the deb was built from, and the dependency lock sha256 matches (`verify-publish`; unit-tested in `tests/test_release_verify.py`) |
| Stale constraints | the shared `locklib pins-coverage` check (same as prepare) |
| Missing evidence | the `evidence` input must quote the deb sha256 from the prepare summary (proving the record refers to the exact bytes published) or carry an explicit `EVIDENCE-SKIP: <why>` waiver; empty/unbound evidence fails the release |
| Double release | tag `v<version>` must not exist yet |

Only then: push the tag and create the GitHub release with the deb.
Release notes default to the `[Unreleased]` section of `CHANGELOG.md`;
pass the `notes` input to override. Locally:
`just release-publish <version> <sha> "<evidence>"`.

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
3. Soak (same policy doc): a **2-hour soak** before every release that
   changes the speech pipeline (and before majors),
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
