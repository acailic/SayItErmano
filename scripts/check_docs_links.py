#!/usr/bin/env python3
"""Docs link checker — org plan 4.5.

Fails when a Markdown file contains a relative link (or image) whose
target does not exist, or an anchor that no heading in the target file
matches. Would have caught the A2/A9 drift (README citing
scripts/run_test_tier.sh and rows citing nonexistent files).

Usage: python scripts/check_docs_links.py [ROOT]
Exits 0 when every checked link resolves, 1 with a report otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
IMG_RE = re.compile(r"""(?:src|href)=["']([^"']+)["']""")
EXTERNAL = ("http://", "https://", "mailto:", "data:")

# Files checked: repo-root docs + the front doors. tools/factory is a
# self-contained vendored tool with its own layout (org plan 2.4).
SCOPES = ["README.md", "AGENTS.md", "CHANGELOG.md", "docs", "requests"]


def slugify(heading: str) -> str:
    """GitHub-style anchor: lowercase, strip punctuation, each space a '-'."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s", "-", text)


def heading_slugs(text: str) -> set[str]:
    slugs: set[str] = set()
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^#{1,6}\s+(.*?)\s*$", line)
        if m:
            slugs.add(slugify(m.group(1)))
    return slugs


def check(root: Path) -> list[str]:
    files: list[Path] = []
    for scope in SCOPES:
        p = root / scope
        if p.is_file() and p.suffix == ".md":
            files.append(p)
        elif p.is_dir():
            files.extend(sorted(p.rglob("*.md")))

    problems: list[str] = []
    for md in files:
        try:
            text = md.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        targets = LINK_RE.findall(text) + IMG_RE.findall(text)
        for raw in targets:
            if not raw or raw.startswith(EXTERNAL):
                continue
            path_part, _, anchor = raw.partition("#")
            if raw.startswith("#"):
                # same-file anchor
                target_file, anchor = md, raw[1:]
            else:
                target_file = (md.parent / path_part).resolve()
                if not target_file.exists():
                    problems.append(f"{md}: missing target {path_part}")
                    continue
                if target_file.suffix != ".md":
                    continue  # images/data files: existence is the check
            if anchor:
                try:
                    target_text = target_file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if anchor not in heading_slugs(target_text):
                    problems.append(f"{md}: anchor #{anchor} not in {target_file.relative_to(root)}")
    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    problems = check(root)
    if problems:
        print(f"docs link check: {len(problems)} problem(s)")
        for p in problems:
            print(f"  {p}")
        return 1
    print("docs link check: all relative links and anchors resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
