#!/usr/bin/env python3
"""PAID_ARTIFACT — the paid-artifact AGPL policy switch (ADR-0008).

The rights inventory
(docs/research/2026-11-22-rights-and-redistribution-inventory.md, "The
AGPL finding") found the one real copyleft wrinkle in the shipped
artifacts: the vendored ``global-shortcut-portal`` wheel is
**AGPL-3.0-only** and installs into the Flatpak. GPLv3 §13 permits the
combination, but a PAID artifact must also offer that wheel's
Corresponding Source — or ship without it. Before any paid artifact
exists, both remediation paths are implemented here behind ONE switch
(the final choice is the operator's, per ADR-0008):

  PAID_ARTIFACT=1 PAID_AGPL_POLICY=source      option A — bundle it:
      the artifact installs the AGPL wheel AND its Corresponding
      Source (the pinned sdist) + license text under share/licenses/.
  PAID_ARTIFACT=1 PAID_AGPL_POLICY=drop-portal option B — drop it:
      the AGPL wheel leaves the dependency set (dbus-next, MIT and
      needed by EIS insertion, stays); the portal hotkey falls back to
      DE shortcuts (docs/guides/wayland.md).
  (default)                                       off — free build,
      byte-for-byte today's behavior.

Consumers:
  packaging/build-deb.sh     ``resolve`` / ``deb-guard`` / ``verify`` /
                             ``extract-license`` (option A stages the
                             sdist + LICENSE into the deb)
  flatpak paid rehearsal     ``flatpak`` rewrites a committed manifest
                             per policy (drop-portal removes the wheel
                             from the deps pip line + license line +
                             source block; source appends the sdist
                             source + its license-dir install). With
                             the switch off it is an identity copy.

The pins below are the single source of truth for the AGPL artifacts;
tests/test_paid_artifact.py fails if the committed files drift.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WHEELS_DIR = REPO / "packaging" / "wheels"

# --- the AGPL component and its content pins --------------------------------
AGPL_PACKAGE = "global-shortcut-portal"
AGPL_VERSION = "0.1.0"
AGPL_WHEEL = "global_shortcut_portal-0.1.0-py3-none-any.whl"
AGPL_WHEEL_SHA256 = ("b4512ca320f2478f86680b12b6bc7fec5e2ec4537"
                     "99e01f2513247bdd86817f0")
AGPL_SDIST = "global_shortcut_portal-0.1.0.tar.gz"
AGPL_SDIST_SHA256 = ("bae2d6ba9f70e2b2b3d1d052b06cfd264abc04b7"
                     "004dd3f847f3f429c22aebda")
AGPL_SDIST_URL = (
    "https://files.pythonhosted.org/packages/86/ba/"
    "f818e9294e29c4f6af60dcec6318f003efb57fccb9acab82aac8b988b37e/"
    "global_shortcut_portal-0.1.0.tar.gz")
# the wheel ships its license here (pip also installs it via dist-info)
AGPL_WHEEL_LICENSE_ENTRY = (
    f"global_shortcut_portal-{AGPL_VERSION}.dist-info/licenses/LICENSE")

# --- the switch --------------------------------------------------------------
ENV_PAID = "PAID_ARTIFACT"
ENV_POLICY = "PAID_AGPL_POLICY"
OFF = "off"
SOURCE = "source"
DROP_PORTAL = "drop-portal"
POLICIES = (SOURCE, DROP_PORTAL)


class PaidArtifactError(Exception):
    """Actionable switch failure; str() is the whole user-facing message."""


def resolve_mode(env: Mapping[str, str] = os.environ) -> str:
    """PAID_ARTIFACT / PAID_AGPL_POLICY (ADR-0008) -> off|source|drop-portal.

    Unset/empty/0 means a free build (policy ignored). 1 REQUIRES an
    explicit policy — a compliance-relevant switch must not guess a
    default the operator never chose.
    """
    paid = env.get(ENV_PAID, "")
    if paid in ("", "0"):
        return OFF
    if paid != "1":
        raise PaidArtifactError(
            f"{ENV_PAID}={paid!r}: expected '1' (or unset/0 for the free "
            f"build) — see docs/adr/ADR-0008-paid-artifact-agpl-policy.md")
    policy = env.get(ENV_POLICY, "")
    if policy not in POLICIES:
        raise PaidArtifactError(
            f"{ENV_PAID}=1 needs {ENV_POLICY}=<one of {', '.join(POLICIES)}> "
            f"(got {policy!r}). ADR-0008 analyzes both; picking one is the "
            "operator's one-line choice:\n"
            f"  {ENV_POLICY}={SOURCE}      bundle the AGPL wheel + its "
            "Corresponding Source + license in the artifact\n"
            f"  {ENV_POLICY}={DROP_PORTAL} drop the AGPL wheel from the "
            "dependency set (portal hotkey falls back to DE shortcuts)")
    return policy


def norm_name(name: str) -> str:
    """PEP 503 normalized distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def artifact_name(filename: str) -> str:
    """Normalized project name from a wheel/sdist filename (first dash
    component of the stem, ``global_shortcut_portal-...`` ->
    ``global-shortcut-portal``)."""
    stem = re.sub(r"\.(whl|tar\.gz|zip)$", "", filename)
    return norm_name(stem.split("-")[0])


def filter_wheels(wheels: Iterable[str], mode: str) -> list[str]:
    """The dependency-set filter: drop-portal removes exactly the AGPL
    wheel; source/off keep the set unchanged.

    dbus-next stays in every mode: it is MIT (no copyleft wrinkle) and
    EIS insertion (fluidvoice/portal_eis.py) needs it — only the
    AGPL-3.0-only global-shortcut-portal is the remediation target.
    """
    if mode == DROP_PORTAL:
        return [w for w in wheels if artifact_name(w) != AGPL_PACKAGE]
    return list(wheels)


# --- deb side: the lock must never grow the AGPL wheel silently --------------


def deb_lock_guard(lock_text: str, mode: str) -> str:
    """Paid deb invariant: global-shortcut-portal is NOT a constraints
    pin. Under source it enters via the vendored, hash-pinned wheel
    (this module's verify); under drop-portal it must not enter at all.
    Returns a one-line OK summary; raises PaidArtifactError otherwise.
    """
    if mode == OFF:
        return "free build: AGPL policy not engaged"
    for lineno, raw in enumerate(lock_text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pin = re.split(r"[<>=!~;\s]", line, 1)[0]
        if norm_name(pin) == AGPL_PACKAGE:
            raise PaidArtifactError(
                f"line {lineno}: {AGPL_PACKAGE} is pinned in the deb lock, "
                f"but mode {mode!r} routes the AGPL wheel through the "
                f"vendored pin in packaging/paidartifact.py instead "
                f"(ADR-0008) — remove the pin or change the policy")
    return (f"deb lock OK for {mode!r}: {AGPL_PACKAGE} absent "
            "(AGPL wheel never enters the constraints closure)")


# --- flatpak side: manifest transform per policy ------------------------------
#
# The manifests are static, byte-pinned files (tests/test_flatpak_manifest.py)
# and flatpak-builder sandboxes see no caller environment — so the paid mode
# is a REHEARSAL-TIME rewrite of a committed manifest (same shapes both
# variants; only the deps module is touched):

PIP_LINE_RE = re.compile(
    r"^(?P<prefix>\s*- pip3 install --no-index .*)"
    f"(?P<wheel>{re.escape(AGPL_WHEEL)})(?P<suffix>.*)$")
LICENSE_LINE_TOKEN = f"licenses/$FLATPAK_ID/{AGPL_PACKAGE} "
SDIST_INSTALL = (
    "install -Dm0644 "
    f"{AGPL_SDIST} $FLATPAK_DEST/share/licenses/$FLATPAK_ID/"
    f"{AGPL_PACKAGE}/{AGPL_SDIST}")
DROP_NOTE = (
    "# PAID_ARTIFACT drop-portal (ADR-0008): global-shortcut-portal "
    "(AGPL-3.0-only) omitted — hotkey falls back to DE shortcuts")
SOURCE_NOTE = (
    "# PAID_ARTIFACT source (ADR-0008): the AGPL wheel's Corresponding "
    "Source (sdist) + license ship inside the artifact")


def _deps_module_lines(lines: list[str]) -> tuple[int, int]:
    """(start, end) slice of the `deps` module block in a manifest."""
    start = end = None
    for i, line in enumerate(lines):
        if re.match(r"^  - name: deps\s*$", line):
            start = i
        elif start is not None and re.match(r"^  - name: \S", line):
            end = i
            break
    if start is None:
        raise PaidArtifactError("manifest has no `- name: deps` module")
    return start, end if end is not None else len(lines)


def _source_entry_range(lines: list[str], start: int, end: int,
                        filename: str) -> tuple[int, int] | None:
    """Slice of the `type: file` source entry whose path/url basename is
    `filename` (entry head line + its indented attribute lines)."""
    for i in range(start, end):
        if not re.match(r"^\s+- type: file\s*$", lines[i]):
            continue
        j = i + 1
        attrs: list[str] = []
        while j < end and re.match(r"^ {8}\S", lines[j]):
            attrs.append(lines[j])
            j += 1
        for attr in attrs:
            m = re.match(r"^\s*(?:path|url):\s*(\S+)\s*$", attr)
            if m and m.group(1).split("/")[-1] == filename:
                return i, j
    return None


def flatpak_transform(text: str, mode: str) -> str:
    """Rewrite a committed flatpak manifest per the paid policy.

    off -> identity (the free build is unchanged by construction).
    drop-portal -> AGPL wheel leaves the pip line, its license-install
    line and its source entry (a note marks each removal site).
    source -> the pinned sdist becomes a deps source and is installed
    into the Flathub license directory next to the wheel's license.

    Raises PaidArtifactError on any shape it does not recognize — a
    manifest that drifted past this transformer fails loudly instead of
    silently keeping the AGPL wheel in a drop-portal build.
    """
    if mode == OFF:
        return text
    lines = text.splitlines()
    start, end = _deps_module_lines(lines)
    block = lines[start:end]

    pip_at = next((i for i, ln in enumerate(block) if PIP_LINE_RE.match(ln)),
                  None)
    if pip_at is None:
        raise PaidArtifactError(
            "deps module: no `pip3 install --no-index` line carrying "
            f"{AGPL_WHEEL} — manifest shape not recognized (ADR-0008 "
            "transform refuses to guess)")
    license_at = next((i for i, ln in enumerate(block)
                       if ln.lstrip().startswith("- install ")
                       and LICENSE_LINE_TOKEN in ln), None)
    if license_at is None:
        raise PaidArtifactError(
            "deps module: no license-install line for "
            f"{AGPL_PACKAGE} — manifest shape not recognized")
    src = _source_entry_range(block, 0, len(block), AGPL_WHEEL)
    if src is None:
        raise PaidArtifactError(
            f"deps module: no `type: file` source for {AGPL_WHEEL}")

    if mode == DROP_PORTAL:
        # edit in reverse positional order: an earlier deletion must not
        # shift the indices of a later splice
        s, e = src
        block[s:e] = [" " * 6 + DROP_NOTE]
        del block[license_at]
        m = PIP_LINE_RE.match(block[pip_at])
        assert m is not None  # for the type checker; found above
        prefix, suffix = m.group("prefix"), m.group("suffix")
        replaced = prefix.rstrip()
        if suffix.strip():  # the wheel was mid-line: keep the rest apart
            replaced += " " + suffix.lstrip()
        block[pip_at] = replaced
    elif mode == SOURCE:
        # variant shape: local `path:` sources (CI bundle) vs remote
        # `url:` (Flathub PR) — the sdist entry must match its variant
        local = any(re.match(r"^\s+path: wheels/", ln) for ln in block)
        s, e = src
        location = (f"        path: wheels/{AGPL_SDIST}" if local
                    else f"        url: {AGPL_SDIST_URL}")
        block[e:e] = ["      # " + SOURCE_NOTE,
                      "      - type: file",
                      location,
                      f"        sha256: {AGPL_SDIST_SHA256}"]
        src_cmd_at = next((i for i, ln in enumerate(block)
                           if re.match(r"^    sources:\s*$", ln)), None)
        if src_cmd_at is None:
            raise PaidArtifactError(
                "deps module: no `sources:` key to insert the sdist "
                "install command before")
        block[src_cmd_at:src_cmd_at] = ["      # " + SOURCE_NOTE,
                                        f"      - {SDIST_INSTALL}"]
    else:
        raise PaidArtifactError(f"unknown mode {mode!r}")

    lines[start:end] = block
    return "\n".join(lines) + "\n"


# --- vendored-artifact verification (offline) --------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_vendored() -> str:
    """The committed AGPL wheel + sdist match the pins (ADR-0008 option A
    installs exactly these bytes). Returns a one-line OK summary."""
    problems = []
    for filename, sha in ((AGPL_WHEEL, AGPL_WHEEL_SHA256),
                          (AGPL_SDIST, AGPL_SDIST_SHA256)):
        path = WHEELS_DIR / filename
        if not path.is_file():
            problems.append(f"missing {path}")
        elif _sha256_file(path) != sha:
            problems.append(f"sha256 drift for {filename} — re-pin in "
                            "packaging/paidartifact.py (and both flatpak "
                            "manifests for the wheel)")
    if problems:
        raise PaidArtifactError("; ".join(problems))
    return (f"AGPL artifacts OK: {AGPL_WHEEL} + {AGPL_SDIST} match their "
            "pins")


def extract_license(dest: Path) -> None:
    """Write the AGPL license text (from the vendored wheel's dist-info)
    to `dest` — the license line of every paid artifact."""
    wheel = WHEELS_DIR / AGPL_WHEEL
    try:
        with zipfile.ZipFile(wheel) as archive:
            dest.write_bytes(archive.read(AGPL_WHEEL_LICENSE_ENTRY))
    except (OSError, KeyError) as e:
        raise PaidArtifactError(
            f"cannot extract the AGPL license from {wheel} "
            f"({AGPL_WHEEL_LICENSE_ENTRY}): {e}") from e


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="paidartifact.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("resolve", help="print off|source|drop-portal for the "
                                    "PAID_ARTIFACT/PAID_AGPL_POLICY env")
    g = sub.add_parser("deb-guard", help="paid deb invariant on the lock")
    g.add_argument("--lock", type=Path, required=True)
    sub.add_parser("verify", help="vendored AGPL wheel+sdist match pins")
    x = sub.add_parser("extract-license", help="write the AGPL license "
                                               "text (wheel dist-info)")
    x.add_argument("-o", "--dest", type=Path, required=True)
    f = sub.add_parser("flatpak", help="rewrite a flatpak manifest per the "
                                       "paid policy (identity when off)")
    f.add_argument("manifest", type=Path)
    f.add_argument("-o", "--out", type=Path,
                   help="output path (default: stdout)")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "resolve":
            print(resolve_mode())
        elif args.cmd == "deb-guard":
            mode = resolve_mode()
            print(deb_lock_guard(args.lock.read_text(encoding="utf-8"),
                                 mode))
        elif args.cmd == "verify":
            print(verify_vendored())
        elif args.cmd == "extract-license":
            extract_license(args.dest)
        elif args.cmd == "flatpak":
            mode = resolve_mode()
            out = flatpak_transform(
                args.manifest.read_text(encoding="utf-8"), mode)
            if args.out:
                args.out.write_text(out, encoding="utf-8")
                print(f"wrote {args.out} (paid mode: {mode})")
            else:
                sys.stdout.write(out)
    except PaidArtifactError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
