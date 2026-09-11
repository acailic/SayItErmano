#!/usr/bin/env python3
"""Fail a required test tier whose JUnit report contains skips.

Quality plan Q1: in the provisioned gtk lane (and any future required
lane), a skip means a prerequisite was NOT actually met — GTK missing,
no display, wrong plugin — which must fail the tier instead of silently
turning green. Documented capability-based skips (e.g. a backend adapter
lacking confidence signals) live in the unit tier, which does NOT run
this check.

Usage: _tier_skip_check.py <junit.xml> <tier>
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    junit, tier = Path(argv[1]), argv[2]
    if not junit.is_file():
        print(f"{tier} tier: no JUnit report at {junit} — pytest did not "
              "write its artifact; treat as a tooling failure", file=sys.stderr)
        return 1
    root = ET.parse(junit).getroot()
    # pytest junit: <testsuites><testsuite skipped="N" .../></testsuites>
    skipped = sum(int(ts.get("skipped", "0"))
                  for ts in root.iter("testsuite"))
    if skipped:
        names = [tc.get("classname", "") + "::" + tc.get("name", "")
                 for ts in root.iter("testsuite")
                 for tc in ts.iter("testcase")
                 if any(ch.tag == "skipped" for ch in tc)]
        print(f"{tier} tier: {skipped} skipped = UNMET PREREQUISITES, not a "
              "green run (provision the lane or fix the skip):",
              file=sys.stderr)
        for n in names[:20]:
            print(f"  {n}", file=sys.stderr)
        if len(names) > 20:
            print(f"  ... and {len(names) - 20} more", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
