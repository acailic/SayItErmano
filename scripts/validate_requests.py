#!/usr/bin/env python3
"""Validate STATUS headers on every requests/*.md brief (plan P1.5).

Rules (docs: TEAM brief "Consolidate project knowledge"):
  1. Every requests/*.md carries EXACTLY ONE machine-readable status
     line, of the exact form `STATUS: OPEN`, `STATUS: SHIPPED` or
     `STATUS: SUPERSEDED`.
  2. The line sits near the top: within the first 5 lines of the file
     (right after the title line).
  3. No other line may start with the word STATUS (catches the legacy
     prose format "STATUS 2026-09-06: SHIPPED in ...") - the detail
     belongs in prose AFTER the machine line, not in it.

Read-only and idempotent by construction: running it never changes
anything. Stdlib only, well under a second.

Exit 0 = all briefs valid (summary printed); exit 1 = offenders listed
on stderr, one per line, with the reason.

Usage: python3 scripts/validate_requests.py [requests_dir]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

VALID = ("OPEN", "SHIPPED", "SUPERSEDED")
EXACT = re.compile(r"^STATUS: (" + "|".join(VALID) + r")$")
ANY_STATUS = re.compile(r"^STATUS\b")
NEAR_TOP_LINES = 5  # title is line 1; STATUS lands on line 3 after it


def validate_file(path: Path) -> list[str]:
    """Return a list of problems with one brief (empty = valid)."""
    problems: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [f"unreadable: {exc}"]

    exact = [i for i, ln in enumerate(lines, 1) if EXACT.match(ln)]
    other = [i for i, ln in enumerate(lines, 1)
             if ANY_STATUS.match(ln) and not EXACT.match(ln)]

    if not exact:
        problems.append("no STATUS line (expected one of: "
                        + " | ".join(f"STATUS: {v}" for v in VALID) + ")")
    else:
        if len(exact) > 1:
            problems.append(f"{len(exact)} STATUS lines "
                            f"(at {exact}), expected exactly 1")
        elif exact[0] > NEAR_TOP_LINES:
            problems.append(f"STATUS line at {exact[0]}, must be within "
                            f"the first {NEAR_TOP_LINES} lines "
                            "(right after the title)")
    if other:
        where = ", ".join(map(str, other))
        problems.append(f"invalid/legacy STATUS line(s) at {where} "
                        "(must match exactly `STATUS: OPEN|SHIPPED|SUPERSEDED`)")
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else (
        Path(__file__).resolve().parent.parent / "requests")
    if not root.is_dir():
        print(f"validate-requests: {root} is not a directory", file=sys.stderr)
        return 1

    files = sorted(p for p in root.glob("*.md") if p.is_file())
    if not files:
        print(f"validate-requests: no *.md briefs under {root}", file=sys.stderr)
        return 1

    tally: dict[str, int] = {v: 0 for v in VALID}
    failed = 0
    for path in files:
        problems = validate_file(path)
        if problems:
            failed += 1
            for problem in problems:
                print(f"{path.name}: {problem}", file=sys.stderr)
        else:
            line = next(ln for ln in path.read_text(
                encoding="utf-8").splitlines() if EXACT.match(ln))
            tally[line.split(":", 1)[1].strip()] += 1

    if failed:
        print(f"validate-requests: {failed} of {len(files)} briefs invalid",
              file=sys.stderr)
        return 1
    print(f"validate-requests: {len(files)} briefs ok "
          + " ".join(f"{v}={tally[v]}" for v in VALID))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
