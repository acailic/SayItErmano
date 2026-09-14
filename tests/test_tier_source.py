"""Single tier-source drift guard — org plan 5.1 (E1).

The unit-tier marker expression and the other tier selections live
ONLY in scripts/test_tier.py; justfile and both CI workflows exec the
script. If someone re-inlines the expression (the exact silent-drift
failure mode quality-plan Q1 removed), these tests fail.
"""

import re
from pathlib import Path

import scripts.test_tier as tier

REPO_ROOT = Path(__file__).resolve().parents[1]

#: executable files that are allowed to carry tier selections.
LANES = ["justfile", ".github/workflows/ci.yml",
         ".github/workflows/release-prepare.yml"]
SCRIPT = "scripts/test_tier.py"


def _flex(pattern: str) -> str:
    """Match the expression across line wraps (yaml `>-` folding)."""
    return pattern.replace(" ", r"\s+")


def test_unit_marker_expression_lives_only_in_the_tier_script() -> None:
    pattern = _flex(re.escape(tier.MARKERS["unit"]))
    duplicated = [rel for rel in LANES
                  if re.search(pattern, (REPO_ROOT / rel).read_text())]
    assert not duplicated, (
        "unit-tier marker expression re-inlined (edit scripts/test_tier.py "
        "instead): " + ", ".join(duplicated))


def test_every_lane_execs_the_tier_script() -> None:
    for rel in LANES:
        assert "test_tier.py" in (REPO_ROOT / rel).read_text(), (
            f"{rel} no longer routes tier selection through the script")


def test_build_args_per_tier() -> None:
    assert tier.build_args("unit", ["-q", "-n", "auto"]) == [
        "-m", "pytest",
        "-m", tier.MARKERS["unit"],
        "tests", "--ignore=tests/integration",
        "-q", "-n", "auto",
    ]
    assert tier.build_args("display", []) == [
        "-m", "pytest", "-m", "needs_display", "tests",
    ]
    assert tier.build_args("process", []) == [
        "-m", "pytest", "-m", tier.MARKERS["process"], "tests/integration",
    ]
    # integration = the whole tree, no marker filter
    assert tier.build_args("integration", []) == [
        "-m", "pytest", "tests/integration",
    ]


def test_unknown_tier_exits_with_message(capsys) -> None:  # type: ignore[no-untyped-def]
    try:
        tier.build_args("nope", [])
    except SystemExit as exc:
        assert "nope" in str(exc.code)
        assert "unit" in str(exc.code)
    else:
        raise AssertionError("unknown tier must SystemExit")
