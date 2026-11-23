"""Parity gate for the flathub-submission branch (runbook section 2, #12).

The branch `flathub-submission` holds EXACTLY the artifact the
flathub/new-pr PR will carry: one top-level manifest named after the
app ID and nothing else — a Flathub submission PR may contain no
source code and no binary/precompiled files (runbook section 0,
docs/packaging/flathub-submission.md). The branch is pushed, never
PR'd or merged: it exists so the exact bytes are reviewable while the
human owner hand-types the PR copy per Flathub's Generative-AI policy
(the branch is the reference to type from, not copy-paste material).

Pinned here:

* the branch manifest is BYTE-IDENTICAL to the working-tree reference
  packaging/flatpak/io.github.acailic.SayItErmano.flathub.yml — the
  same file tests/test_flatpak_manifest.py gates — so editing the
  packaging copy without re-cutting the branch fails the suite;
* the branch tree is ONLY the manifest (no sources, no binaries, no
  stray files — the runbook's hard rule);
* SUBMISSION-PR.md, the PR-text worksheet, stays pulled from the real
  manifest: it justifies every finish-arg of the reviewed baseline
  and links exactly the metainfo's raw-`linux` screenshot URLs.

The branch tests skip gracefully when the branch is absent from the
local clone (single-branch CI checkouts fetch neither the local nor
the remote-tracking ref).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.test_flatpak_manifest import (
    APP_ID,
    FLATHUB,
    MANIFEST_PATHS,
    SCREENSHOT_URL_PREFIX,
    SCREENSHOTS,
    parse_manifest,
)

REPO = Path(__file__).resolve().parents[1]

SUBMISSION_BRANCH = "flathub-submission"
BRANCH_MANIFEST = f"{APP_ID}.yml"
PACKAGING_COPY = MANIFEST_PATHS[FLATHUB]
SUBMISSION_PR = REPO / "packaging" / "flatpak" / "SUBMISSION-PR.md"

# Where the submission shape may live, in order: the local branch,
# then its remote-tracking twin. Neither in a single-branch clone is
# the skip case.
_BRANCH_REFS = (
    f"refs/heads/{SUBMISSION_BRANCH}",
    f"refs/remotes/origin/{SUBMISSION_BRANCH}",
)


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, check=False)


@pytest.fixture(scope="module")
def branch_ref() -> str | None:
    for ref in _BRANCH_REFS:
        if _git("show-ref", "--verify", "--quiet", ref).returncode == 0:
            return ref
    return None


def _require_branch(branch_ref: str | None) -> str:
    if branch_ref is None:
        pytest.skip(f"{SUBMISSION_BRANCH} not fetched into this clone")
    return branch_ref


def test_branch_manifest_is_byte_identical_to_the_packaging_copy(
        branch_ref):
    ref = _require_branch(branch_ref)
    located = _git("rev-parse", f"{ref}:{BRANCH_MANIFEST}")
    assert located.returncode == 0, (
        f"{SUBMISSION_BRANCH} exists but its top-level "
        f"{BRANCH_MANIFEST} is missing — the Flathub PR shape is a "
        "manifest named after the app ID at the top level")
    blob = _git("cat-file", "blob", located.stdout.decode().strip())
    assert blob.stdout == PACKAGING_COPY.read_bytes(), (
        f"{SUBMISSION_BRANCH}:{BRANCH_MANIFEST} and "
        f"{PACKAGING_COPY.name} drifted apart — re-cut the branch "
        "from the packaging copy (runbook section 2)")


def test_branch_tree_is_exactly_the_manifest(branch_ref):
    ref = _require_branch(branch_ref)
    listing = _git("ls-tree", "-r", "--name-only", ref)
    assert listing.returncode == 0
    assert listing.stdout.decode().split() == [BRANCH_MANIFEST], (
        "a Flathub submission PR may carry ONLY the manifest — no "
        "sources, no binaries, no extra files (runbook hard rule)")


def test_submission_pr_worksheet_covers_every_finish_arg():
    manifest = parse_manifest(PACKAGING_COPY.read_text(encoding="utf-8"))
    text = SUBMISSION_PR.read_text(encoding="utf-8")
    for arg in manifest["finish-args"]:
        assert arg in text, (
            f"{arg}: no justification in {SUBMISSION_PR.name} — the "
            "PR worksheet must justify every finish-arg of the "
            "reviewed baseline")


def test_submission_pr_worksheet_links_the_metainfo_screenshots():
    text = SUBMISSION_PR.read_text(encoding="utf-8")
    for name in SCREENSHOTS:
        assert SCREENSHOT_URL_PREFIX + name in text, (
            f"{name}: raw-linux URL missing from {SUBMISSION_PR.name} "
            "— the worksheet and the metainfo drifted apart")


def test_submission_pr_worksheet_pins_the_runbook_pr_title():
    text = SUBMISSION_PR.read_text(encoding="utf-8")
    assert "Add io.github.acailic.SayItErmano" in text, \
        "the runbook-mandated PR title is missing from the worksheet"
