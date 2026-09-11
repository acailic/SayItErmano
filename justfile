# SSSF starter recipes. Stamped by install.py, then yours to edit.
#
# Two halves (plan P0.5):
#   1. SayItErmano APPLICATION recipes, unprefixed: lint / test /
#      test-parallel / gate (+ release-prepare / release-publish driving
#      the manual GitHub workflows).
#   2. The agent FACTORY (SSSF starter) recipes, all under the `factory-`
#      prefix, functionally unchanged. Add your own as your chains grow;
#      see the example branch for the fuller set (orchestrator agents,
#      kill, rosters, ipi).

# `.env` reaches every ADW through this, so keys work without exporting them.
set dotenv-load
set positional-arguments

# Every factory recipe passes this through, so `SSSF_CONFIG=other.yaml just
# factory-prompt "..."` swaps the whole roster for one run.
config := env_var_or_default("SSSF_CONFIG", "adws/adw_sssf_config/sssf.config.yaml")
db     := "adws/adw_data/sssf.db"

# Python for the application recipes: repo venv if present, else $SAYIT_PY,
# else python3. (Worktrees share the main tree's venv via SAYIT_PY; see
# AGENTS.md — never bare `pytest`.)
python := env_var_or_default("SAYIT_PY", `[ -x .venv/bin/python ] && echo .venv/bin/python || echo python3`)

# list every recipe
default:
    @just --list

# ── application: developer gates (plan P0.5 + quality plan Q1) ─────────────
# Tier model (docs/research/2026-09-11-project-quality-and-testing-plan.md):
#   unit/contract  — offline, headless, no model, no outbound network.
#                    This is `just gate`, the ONE mandatory gate.
#   display/GTK    — real display server + GTK4/Adw (Xvfb counts).
#                    `just test-ui`; CI runs it in the provisioned gtk-x11
#                    lane where a prerequisite skip FAILS instead of passing.
#   integration    — real model/mic/daemon/GPU. `just test-integration`
#                    (needs the shared venv; run from your worktree root).
# The `-m` filter deselects by DECLARED requirement (needs_* markers), so
# local and CI collection lists match regardless of the dev machine.
tier_unit := "not integration and not desktop and not needs_display and not needs_model and not needs_network"

# ruff lint (config: [tool.ruff.lint] in pyproject.toml)
lint:
    {{python}} -m ruff check .

# unit/contract tier, serial (plain pytest stays single-process: --pdb, -x)
test *ARGS:
    {{python}} -m pytest -q tests --ignore=tests/integration -m "{{tier_unit}}" {{ARGS}}

# unit/contract tier on pytest-xdist auto workers (~4-5x faster)
test-parallel *ARGS:
    {{python}} -m pytest -q -n auto tests --ignore=tests/integration -m "{{tier_unit}}" {{ARGS}}

# display/GTK tier (real display or Xvfb; skips headless — use
# SAYIT_TEST_REQUIRE_MARKERS=needs_display to make skips fail, like CI does)
test-ui *ARGS:
    {{python}} -m pytest -q tests -m "needs_display" {{ARGS}}

# real-model/real-mic/daemon-process tier (needs the shared venv + hardware;
# excluded from every offline gate by --ignore AND by the marker)
test-integration *ARGS:
    {{python}} -m pytest -q tests/integration {{ARGS}}

# THE canonical unit/contract gate (Q1): python -m pytest, warnings as
# errors, unknown markers rejected, every skip listed, JUnit artifact,
# bounded per-test timeout, request validation. CI's unit job and
# release-prepare run exactly this scope.
gate:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! git diff --quiet || ! git diff --cached --quiet; then
        echo "gate: tracked files have uncommitted changes — commit or stash first" >&2
        exit 1
    fi
    {{python}} -m ruff check .
    {{python}} scripts/validate_requests.py
    mkdir -p build/test-results
    {{python}} -m pytest -q -W error -ra --strict-markers --strict-config \
        --timeout=300 --junitxml=build/test-results/unit.xml \
        tests --ignore=tests/integration -m "{{tier_unit}}"
    echo "gate: clean tree, lint clean, briefs valid, suite green, zero warnings"

# release cleanliness on top of the gate: also refuses UNTRACKED files
# (release-prepare's clean-tree check uses git status --porcelain, which
# counts them; a dev gate only checks tracked changes)
gate-release: gate
    #!/usr/bin/env bash
    set -euo pipefail
    [ -z "$(git status --porcelain)" ] || {
        git status --porcelain
        echo "gate-release: untracked files present — release-prepare would refuse" >&2
        exit 1
    }
    echo "gate-release: tree fully clean"

# every requests/*.md brief carries exactly one valid STATUS: OPEN|SHIPPED|SUPERSEDED
validate-requests:
    {{python}} scripts/validate_requests.py

# ── application: release dispatch (manual-only, like all CI here) ───────────
# prepare bumps the version, runs the full gate + locked deb build, commits
# and pushes. You then dispatch CI for the produced SHA from the Actions
# tab, and publish verifies that green CI run for the EXACT SHA before
# tagging/uploading. Full flow: docs/dev/release-gates.md

# dispatch release-prepare for VERSION (e.g. just release-prepare 0.8.2)
release-prepare VERSION:
    #!/usr/bin/env bash
    set -euo pipefail
    gh workflow run release-prepare.yml -f version="{{VERSION}}"
    echo "dispatched release-prepare for v{{VERSION}} — watch: gh run watch"

# dispatch release-publish for VERSION at SHA (needs green CI on that SHA)
release-publish VERSION SHA:
    #!/usr/bin/env bash
    set -euo pipefail
    gh workflow run release-publish.yml -f version="{{VERSION}}" -f sha="{{SHA}}"
    echo "dispatched release-publish for v{{VERSION}} @ {{SHA}} — watch: gh run watch"

# ── factory (SSSF agent factory) ────────────────────────────────────────────

# ── first run ───────────────────────────────────────────────────────────────

# Proves the whole path works: config validated, session minted, agent ran,
# envelope parsed, gates checked, trace written. Costs a few cents and changes
# nothing in your repo, because both workflows are read-only.
#
# (`just --list` shows only the LAST comment line, so that one is the summary.)

# factory: two cheap read-only runs, end to end
factory-demo:
    @echo "1/2  adw_prompt: one agent, one prompt"
    uv run adws/adw_prompt.py --config {{config}} --agent scout "reply with a one-line summary of this repo"
    @echo "\n2/2  adw_scout: read-only recon"
    uv run adws/adw_scout.py --config {{config}} "list the top-level directories in this repo and what each is for. change nothing."
    @echo "\nboth done. now run:  just factory-sessions    (or: just factory-obs)"

# ── run a workflow ──────────────────────────────────────────────────────────
# Args pass straight through: "<prompt or path/to/prompt.md>" [--adw-id X]

# factory: one agent, one prompt
factory-prompt *ARGS:
    uv run adws/adw_prompt.py --config {{config}} "$@"

# factory: read-only recon
factory-scout *ARGS:
    uv run adws/adw_scout.py --config {{config}} "$@"

# factory: plan only
factory-plan *ARGS:
    uv run adws/adw_plan.py --config {{config}} "$@"

# factory: planner, builder, commit
factory-plan-build *ARGS:
    uv run adws/adw_plan_build.py --config {{config}} "$@"

# factory: plan, build, test, commit
factory-sdlc *ARGS:
    uv run adws/adw_plan_build_test.py --config {{config}} "$@"

# factory: the full chain, plus review and docs
factory-simple-sdlc *ARGS:
    uv run adws/adw_simple_sdlc.py --config {{config}} "$@"

# ── watch it ────────────────────────────────────────────────────────────────
# Reads never block a running workflow, the db is WAL. Poll as hard as you like.

# factory: the last 10 runs
factory-sessions:
    @sqlite3 {{db}} "select adw_id, status, substr(request,1,50), total_tokens, round(total_cost,4) from sessions order by started_at desc limit 10;"

# factory: phase status in sequence
factory-phases ADW_ID:
    @sqlite3 {{db}} "select seq, name, kind, owner, status, attempt from phases where adw_id='{{ADW_ID}}' order by seq;"

# factory: the live event tail
factory-tail ADW_ID:
    @sqlite3 {{db}} "select rowid, type, name, started_at from events where adw_id='{{ADW_ID}}' order by rowid desc limit 25;"

# factory: what a run has alive right now, with pids
factory-procs ADW_ID:
    @sqlite3 {{db}} "select kind, name, pid, command, started_at from processes where adw_id='{{ADW_ID}}' and ended_at is null order by id;"

# ── observability UI ────────────────────────────────────────────────────────

# Needs bun. The db path is passed explicitly because the server runs from the
# app dir and would otherwise look for a trace db sitting next to itself.

# factory: boot the trace UI, http://localhost:4601 (api on :4600)
factory-obs:
    cd .claude/skills/sssf/apps/visualizer && bun install && (SSSF_DB={{justfile_directory()}}/{{db}} bun run server/index.ts &) && bunx vite
