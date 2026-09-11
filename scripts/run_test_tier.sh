#!/usr/bin/env bash
# Canonical per-tier test commands (quality plan Q1, finding E1/E6).
#
# ONE source of truth: the justfile recipes, ci.yml and release-prepare.yml
# all call this script — the pytest selection strings must never be
# duplicated anywhere else. Tiers:
#
#   unit        mandatory unit/contract gate: offline (loopback-only,
#               enforced by the conftest network guard), no model download,
#               no display/GTK, warnings as errors, unknown markers
#               rejected, JUnit artifact, bounded per-test timeout
#   gtk         provisioned GUI lane: GTK4/libadwaita + a display
#               (xvfb-run -a on CI). A skip here is an UNMET PREREQUISITE
#               and fails the tier — never a green.
#   integration real subsystems (daemon processes, model, mic); not part of
#               any automatic gate — run explicitly when needed
#
# Usage: scripts/run_test_tier.sh <tier> [extra pytest args...]
#   e.g. scripts/run_test_tier.sh unit -n auto        # parallel unit run
#        scripts/run_test_tier.sh unit --collect-only -q
set -euo pipefail

tier="${1:?usage: run_test_tier.sh unit|gtk|integration [extra pytest args...]}"
shift || true

# Same interpreter resolution as the justfile: SAYIT_PY (worktrees sharing
# the main tree's venv), else a local .venv, else PATH python3 (CI).
python="${SAYIT_PY:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )}"

mkdir -p .junit

case "$tier" in
  unit)
    export FLUIDVOICE_TEST_TIER=unit   # activates the conftest network guard
    exec "$python" -m pytest -q -ra tests --ignore=tests/integration \
      -m "not slow and not integration and not network and not model and not desktop and not packaging and not gtk" \
      -W error --strict-markers --timeout=300 \
      --junitxml=.junit/unit.xml "$@"
    ;;
  gtk)
    export FLUIDVOICE_TEST_TIER=gtk
    "$python" -m pytest -q -ra tests -m gtk \
      -W error --strict-markers --timeout=300 \
      --junitxml=.junit/gtk.xml "$@"
    # Required-tier skips are unmet prerequisites, not success: the lane was
    # provisioned (or the display was there) — anything that skipped means
    # the prerequisites were NOT actually met.
    "$python" scripts/_tier_skip_check.py .junit/gtk.xml gtk
    ;;
  integration)
    # tier identity for conftest scoping (Q2: the integration tier keeps
    # the ambient compositor identity — no X11 pinning)
    export FLUIDVOICE_TEST_TIER=integration
    "$python" -m pytest -q -ra tests/integration \
      --strict-markers --timeout=1800 \
      --junitxml=.junit/integration.xml "$@"
    ;;
  *)
    echo "run_test_tier.sh: unknown tier '$tier' (unit|gtk|integration)" >&2
    exit 2
    ;;
esac
