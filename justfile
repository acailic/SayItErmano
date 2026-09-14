# SayItErmano application recipes (lint / test / test-parallel / gate,
# release-prepare / release-publish driving the manual GitHub
# workflows). The agent FACTORY (SSSF) recipes live in
# tools/factory/justfile, imported below — all under the `factory-`
# prefix, unchanged behavior (org plan 2.4).

# `.env` reaches every ADW through this, so keys work without exporting them.
set dotenv-load
set positional-arguments

# agent factory recipes (tools/factory/, org plan 2.4)
import 'tools/factory/justfile'

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
# The tier marker expressions + path selection live ONCE in
# scripts/test_tier.py (org plan 5.1) — every recipe and CI lane below
# execs it; the drift guard is tests/test_tier_source.py.

# ruff lint (config: [tool.ruff.lint] in pyproject.toml)
lint:
    {{python}} -m ruff check .

# unit/contract tier, serial (plain pytest stays single-process: --pdb, -x)
test *ARGS:
    {{python}} scripts/test_tier.py unit -q {{ARGS}}

# unit/contract tier on pytest-xdist auto workers (~4-5x faster)
test-parallel *ARGS:
    {{python}} scripts/test_tier.py unit -q -n auto {{ARGS}}

# display/GTK tier — VIRTUAL display by default: xvfb-run + GTK's cairo
# software renderer, so nothing flashes on the real desktop, on pytest-xdist
# workers (~4x faster: 154 tests, 6s vs 25s serial). Skips headless only
# when xvfb-run is missing; SAYIT_TEST_REAL_DISPLAY=1 opts onto the live
# display (real-renderer debugging — windows WILL appear).
test-ui *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ "${SAYIT_TEST_REAL_DISPLAY:-0}" = "1" ] \
            || ! command -v xvfb-run >/dev/null 2>&1; then
        {{python}} scripts/test_tier.py display -q {{ARGS}}
    else
        xvfb-run -a env GSK_RENDERER=cairo \
            {{python}} scripts/test_tier.py display -q -n auto -W error --timeout=300 \
            {{ARGS}}
    fi

# real-model/real-mic/daemon-process tier (needs the shared venv + hardware;
# excluded from every offline gate by --ignore AND by the marker)
test-integration *ARGS:
    {{python}} scripts/test_tier.py integration -q {{ARGS}}

# MODEL-FREE process lane (Q7): real daemon/socket/CLI subprocesses, no
# GPU/model/network — runs in any checkout (spawns `sys.executable -m
# fluidvoice` from THIS tree), works under Xvfb:
#   xvfb-run -a just test-process
test-process *ARGS:
    {{python}} scripts/test_tier.py process -q {{ARGS}}

# unit/contract tier branch coverage (Q5): terminal summary + XML for CI.
# Tiers are measured separately (this is the unit tier only; the display
# tier runs the same command with -m needs_display). Baseline:
# docs/research/2026-09-12-coverage-baseline.md — ratchet upward only.
# FLOOR 71 (org plan 5.2/E2): measured 71.94% post-v0.8.2 (the released
# code base grew faster than the suite since the 09-12 baseline's
# 79.9%); ci.yml's unit job carries the same number — pinned together
# by tests/test_tier_source.py. Raise as coverage grows.
coverage *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p build/coverage
    {{python}} scripts/test_tier.py unit -q -n auto -W error --strict-markers --timeout=300 \
        --cov=fluidvoice --cov-branch \
        --cov-fail-under=71 \
        --cov-report=term-missing \
        --cov-report=xml:build/coverage/unit.xml \
        {{ARGS}}

# display/GTK tier coverage (same Xvfb isolation as test-ui, its own XML)
coverage-ui *ARGS:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p build/coverage
    if command -v xvfb-run >/dev/null 2>&1; then
        xvfb-run -a env GSK_RENDERER=cairo \
            {{python}} scripts/test_tier.py display -q -n auto -W error --strict-markers --timeout=300 \
            --cov=fluidvoice --cov-branch \
            --cov-report=term-missing \
            --cov-report=xml:build/coverage/display.xml \
            {{ARGS}}
    else
        {{python}} scripts/test_tier.py display -q -n auto -W error --strict-markers --timeout=300 \
        --cov=fluidvoice --cov-branch \
        --cov-report=term-missing \
        --cov-report=xml:build/coverage/display.xml \
        {{ARGS}}
    fi

# focused type check (Q12): the typed seam modules (config in pyproject
# [tool.mypy] — grow the list as modules earn annotations)
typecheck:
    {{python}} -m mypy

# THE canonical unit/contract gate (Q1): the tier script, warnings as
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
    {{python}} scripts/check_docs_links.py
    {{python}} scripts/gen_config_reference.py --check
    {{python}} scripts/validate_requests.py
    mkdir -p build/test-results
    {{python}} scripts/test_tier.py unit -q -W error -ra --strict-markers --strict-config \
        --timeout=300 --junitxml=build/test-results/unit.xml
    echo "gate: clean tree, lint clean, briefs valid, config docs fresh, suite green, zero warnings"

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

# dispatch release-publish for VERSION at SHA (needs green CI + evidence;
# EVIDENCE must quote the deb sha256 from the prepare summary, or be an
# explicit 'EVIDENCE-SKIP: <why>' waiver — Q4 provenance gate)
release-publish VERSION SHA EVIDENCE:
    #!/usr/bin/env bash
    set -euo pipefail
    gh workflow run release-publish.yml -f version="{{VERSION}}" -f sha="{{SHA}}" -f evidence="{{EVIDENCE}}"
    echo "dispatched release-publish for v{{VERSION}} @ {{SHA}} — watch: gh run watch"
