"""Docs link gate — org plan 4.5.

The unit-tier mirror of `scripts/check_docs_links.py` (also a gate
step): every relative Markdown link and anchor under README.md,
AGENTS.md, CHANGELOG.md, docs/ and requests/ must resolve. This is the
check that would have caught the A2/A9 documentation drift (cited
files that did not exist).
"""

from pathlib import Path

import scripts.check_docs_links as checker

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_all_relative_links_and_anchors_resolve() -> None:
    problems = checker.check(REPO_ROOT)
    assert not problems, "dangling docs links/anchors:\n" + "\n".join(problems)


def test_slugify_matches_github_anchor_rules() -> None:
    # one dash per space, punctuation dropped, case folded
    assert checker.slugify("pipx / pip (any distro, Python 3.11+)") == (
        "pipx--pip-any-distro-python-311"
    )
    assert checker.slugify("License & credits") == "license--credits"
    assert checker.slugify("Q5 coverage + properties") == "q5-coverage--properties"


def test_heading_slugs_ignores_fenced_code() -> None:
    text = "# Real\n\n```python\n# comment that is not a heading\n```\n\n## Two\n"
    assert checker.heading_slugs(text) == {"real", "two"}
