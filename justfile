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

# ── application: developer gates (plan P0.5; tiers per quality plan Q1) ─────
# The canonical per-tier commands live in scripts/run_test_tier.sh — ONE
# source of truth shared with CI (ci.yml) and release-prepare.yml. These
# recipes and the release workflows only call that script.

# ruff lint (config: [tool.ruff.lint] in pyproject.toml)
lint:
    {{python}} -m ruff check .

# dev convenience: whole suite minus integration, serial (plain pytest
# stays single-process: --pdb, -x). Includes the gtk/slow-marked tests —
# they skip headless. The canonical gate is `just test-unit`.
test *ARGS:
    {{python}} -m pytest -q tests --ignore=tests/integration {{ARGS}}

# dev convenience on pytest-xdist auto workers (~4-5x faster)
test-parallel *ARGS:
    {{python}} -m pytest -q -n auto tests --ignore=tests/integration {{ARGS}}

# canonical unit/contract gate (Q1): offline, no model/display/network
# (loopback guard on), -W error, strict markers, junit artifact, timeout
test-unit *ARGS:
    bash scripts/run_test_tier.sh unit {{ARGS}}

# provisioned GUI lane (Q1): GTK4/libadwaita + display; skips FAIL the tier
test-gtk *ARGS:
    bash scripts/run_test_tier.sh gtk {{ARGS}}

# real subsystems: daemon processes, model, mic — never in `just gate`
test-integration *ARGS:
    bash scripts/run_test_tier.sh integration {{ARGS}}

# every requests/*.md brief carries exactly one valid STATUS: OPEN|SHIPPED|SUPERSEDED
validate-requests:
    {{python}} scripts/validate_requests.py

# the complete local gate: clean tree, lint, valid briefs, unit tier green
# (warnings as errors, network guard, bounded timeout); the GUI lane runs
# too when a display is present, and SAYS SO when it cannot.
gate:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! git diff --quiet || ! git diff --cached --quiet; then
        echo "gate: tracked files have uncommitted changes — commit or stash first" >&2
        exit 1
    fi
    {{python}} -m ruff check .
    {{python}} scripts/validate_requests.py
    bash scripts/run_test_tier.sh unit
    if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        bash scripts/run_test_tier.sh gtk
    else
        echo "gate: GUI lane not run (no display) — 'just test-gtk' inside a session for GUI coverage"
    fi
    echo "gate: clean tree, lint clean, briefs valid, unit tier green"

# release-grade cleanliness (Q1): everything `gate` checks, but the tree
# must be fully clean INCLUDING untracked files — what release-prepare
# verifies on the checked-out SHA.
gate-release:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -n "$(git status --porcelain)" ]; then
        git status --porcelain
        echo "gate-release: working tree not fully clean (incl. untracked)" >&2
        exit 1
    fi
    {{python}} -m ruff check .
    {{python}} scripts/validate_requests.py
    bash scripts/run_test_tier.sh unit
    echo "gate-release: tree fully clean, lint clean, briefs valid, unit tier green"

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
