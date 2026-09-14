#!/usr/bin/env python3
"""Single test-tier selection source — org plan 5.1 (quality plan E1).

The `-m` marker expression and the test-path selection for every tier
live HERE, once. justfile (test / test-parallel / test-ui /
test-process / coverage / gate), ci.yml (unit + gtk-x11 lanes) and
release-prepare.yml all exec this script, so the lanes cannot silently
drift — the exact failure mode quality-plan Q1 set out to remove.
Callers keep their own strictness/coverage/reporting flags as extra
pytest arguments (they land after the selection, as pytest argv).

Usage: python scripts/test_tier.py <tier> [extra pytest args...]
  unit         offline/headless suite minus the integration dir (gate tier)
  display      the needs_display GTK tier (tests/ unignored — the CI
               gtk-x11 lane collects display-marked tests everywhere)
  process      model-free process lane (Q7): tests/integration minus
               model/network/desktop
  integration  the whole tests/integration tree (real hardware)
"""

from __future__ import annotations

import os
import sys

#: tier -> pytest `-m` marker expression (None = no marker filter).
MARKERS: dict[str, str | None] = {
    "unit": ("not integration and not desktop and not needs_display "
             "and not needs_model and not needs_network"),
    "display": "needs_display",
    "process": ("integration and not needs_model and not needs_network "
                "and not desktop"),
    "integration": None,
}

#: tier -> positional path arguments.
PATHS: dict[str, list[str]] = {
    "unit": ["tests", "--ignore=tests/integration"],
    "display": ["tests"],
    "process": ["tests/integration"],
    "integration": ["tests/integration"],
}


def build_args(tier: str, extra: list[str]) -> list[str]:
    """Full python argv for a tier invocation (module, selection, extras)."""
    if tier not in MARKERS:
        raise SystemExit(f"unknown tier {tier!r}; one of: "
                         + ", ".join(sorted(MARKERS)))
    selection: list[str] = []
    if MARKERS[tier] is not None:
        selection += ["-m", MARKERS[tier]]
    return ["-m", "pytest", *selection, *PATHS[tier], *extra]


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    if argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    args = build_args(argv[0], argv[1:])
    # execv: the process BECOMES pytest — exit code and signals propagate
    os.execv(sys.executable, [sys.executable, *args])
    raise AssertionError("execv returned")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
