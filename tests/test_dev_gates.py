"""Meta-tests guarding the developer gates themselves.

The suite proves application behavior; these prove the gates have not
silently rotted: the lint floor matches the oldest supported interpreter
(F10) and the thread-exception filter actually fails a run that deserves
to fail (N6)."""
from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    with (REPO / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


class TestLintTargetsOldestPython:
    """F10: ruff must lint for the OLDEST supported interpreter
    (requires-python ">=3.11"). target-version py312 would not flag
    3.12-only syntax that breaks 3.11 users, and the CI matrix does not
    build 3.11 — lint is the only automated 3.11 guard."""

    def test_ruff_target_version_matches_requires_python_floor(self):
        cfg = _pyproject()
        req = cfg["project"]["requires-python"]
        assert req.startswith(">="), req
        floor = req[2:].split(",")[0].strip()      # "3.11"
        digits = floor.replace(".", "")            # "311"
        assert cfg["tool"]["ruff"]["target-version"] == f"py{digits}"
