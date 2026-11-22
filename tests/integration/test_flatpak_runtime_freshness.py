"""Flathub runtime freshness for the shipping Flatpak manifests (#12).

Flathub requires ``runtime-version`` to be the runtime's latest hosted
branch at submission time (requirements page, "Manifest"), and
flatpak-builder-lint reports a runtime three versions behind as an
error (``runtime-is-eol-*``). The unit tier is offline, so the
hosted-branch comparison lives here: it asks Flathub's public runtimes
API which ``org.gnome.Platform`` branches exist and fails when the
manifests pin anything but the highest.

Run it before a submission (and after each GNOME release, to learn when
the bump is due):

    .venv/bin/python -m pytest tests/integration/test_flatpak_runtime_freshness.py

A failure means: bump ``runtime-version`` in BOTH manifest variants,
move the compiled wheel to the new Python ABI
(``RUNTIME_PYTHON`` in tests/test_flatpak_manifest.py) and update the
runbook's runtime note.
"""
from __future__ import annotations

import json
import urllib.request

import pytest

from tests.test_flatpak_manifest import MANIFEST_PATHS, parse_manifest

pytestmark = [pytest.mark.integration, pytest.mark.needs_network]

# The official API behind flathub.org's runtime listing: a mapping of
# "<runtime>/<arch>/<branch>" -> number of apps using it.
RUNTIMES_API = "https://flathub.org/api/v2/runtimes"
PLATFORM = "org.gnome.Platform"


def _hosted_branches(payload) -> set[str]:
    if isinstance(payload, dict):
        refs = payload.keys()
    elif isinstance(payload, list):
        refs = [entry["ref"] if isinstance(entry, dict) else entry
                for entry in payload]
    else:
        refs = None
    assert refs is not None, f"unexpected runtimes API shape: {type(payload)}"
    branches = set()
    for ref in refs:
        parts = str(ref).split("/")
        if len(parts) == 3 and parts[0] == PLATFORM and parts[1] == "x86_64":
            branches.add(parts[2])
    assert branches, f"no {PLATFORM} branch in the runtimes API payload"
    return branches


@pytest.fixture(scope="module")
def latest_hosted_platform() -> str:
    with urllib.request.urlopen(RUNTIMES_API, timeout=30) as response:
        payload = json.load(response)
    return max(_hosted_branches(payload), key=int)


@pytest.mark.parametrize("variant", sorted(MANIFEST_PATHS))
def test_runtime_version_is_the_latest_hosted_gnome_platform(
        variant, latest_hosted_platform):
    """A stale pin fails here, before the Flathub lint or a reviewer
    gets to say so."""
    manifest = parse_manifest(
        MANIFEST_PATHS[variant].read_text(encoding="utf-8"))
    assert manifest["runtime"] == PLATFORM
    assert manifest["runtime-version"] == latest_hosted_platform, (
        f"{MANIFEST_PATHS[variant].name} pins org.gnome.Platform "
        f"{manifest['runtime-version']} but Flathub hosts "
        f"{latest_hosted_platform} — bump both manifest variants (and "
        "the compiled wheel's ABI) before submitting")
