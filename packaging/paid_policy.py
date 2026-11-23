#!/usr/bin/env python3
"""PAID_ARTIFACT policy hooks (ADR-0008) — one switch, two options, fail closed.

The rights inventory (docs/research/2026-11-22-rights-and-redistribution-
inventory.md, "The AGPL finding") established: the vendored
``global-shortcut-portal`` wheel is AGPL-3.0-only and installs into the
Flatpak, so a PAID artifact must either offer that wheel's Corresponding
Source (option A) or drop the ``portal`` extra from its dependency set
(option B). The operator picks A or B; BOTH are implemented here and in
the build entry points, behind one switch:

  PAID_ARTIFACT=1            engage (default: unset — free builds are
                              byte-for-byte unaffected). Unset/''/'0' is
                              off and '1' is on; any other value is an
                              ERROR, never a silent free build.
  PAID_ARTIFACT_OPTION=A|B   optional cross-check: it must AGREE with
                              the decision recorded in ADR-0008. Unset
                              means that recorded decision.

FAIL CLOSED — there is no code default and no environment variable that
selects a paid option. The option a paid build uses comes from ONE
place, the decision marker line in
docs/adr/ADR-0008-paid-artifact-agpl-policy.md::

    Paid-artifact decision: OPERATOR      (not decided — paid builds refuse)
    Paid-artifact decision: A | B         (the operator's recorded decision)

While the marker reads OPERATOR every paid path below exits 1 with a
message naming the ADR, so no paid artifact is produced by a guess. The
operator flips the marker; flipping the launch posture is then one
environment variable, no code.

What each consumer does:

* ``packaging/build-deb.sh`` — option A extracts this offer (sources +
  license, the Corresponding-Source sdist itself and a pinned-sdist
  notice) into /usr/share/doc inside the deb; option B filters the AGPL
  pin out of the installed dependency lock (a no-op today: the deb's
  closure never carried the wheel — the filter guarantees it stays that
  way).
* the Flatpak manifests — ``paid_policy.py flatpak`` emits the paid
  variant of either committed manifest: option A appends an
  ``agpl-source-offer`` module (the installed sources ARE the wheel's
  preferred source form; the license is copied to the Flathub license
  dir; the sdist rides along as a sha256-pinned source — local ``path:``
  for the CI variant, the PyPI ``url:`` for the Flathub one); option B
  rewrites the ``deps`` module to drop the wheel, its license line and
  its source entry.

Everything here is stdlib-only and offline: the wheel's source offer is
extracted from the vendored wheel bytes, and the sdist shipped next to
it is the vendored ``packaging/wheels/global_shortcut_portal-0.1.0.tar.gz``
— verified against its sha256 pin AND against the wheel (same package,
version and license; every installed .py byte-identical) before any
paid path ships it. Unit tests: tests/test_paid_policy.py.

Usage:
  paid_policy.py option                     print the option a PAID build
                                            uses (fails closed without the
                                            ADR-0008 decision)
  paid_policy.py verify                     the vendored sdist is the pinned
                                            Corresponding Source of the
                                            vendored wheel
  paid_policy.py filter-lock --lock F       paid dependency lock on stdout
  paid_policy.py agpl-offer --dest D        write the offer into D
  paid_policy.py flatpak [--manifest F]    paid manifest on stdout
"""
from __future__ import annotations

import argparse
import email.parser
import importlib.util
import os
import re
import shutil
import sys
import tarfile
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import NamedTuple

# locklib owns PEP 503 name normalization + wheel-filename parsing; load
# it by path (scripts have no package context) exactly like the tests do.
_spec = importlib.util.spec_from_file_location(
    "locklib", Path(__file__).resolve().parent / "deb" / "locklib.py")
locklib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(locklib)


class PolicyError(Exception):
    """Actionable paid-policy failure; str() is the user-facing message."""


REPO = Path(__file__).resolve().parents[1]

SWITCH_ENV = "PAID_ARTIFACT"
OPTION_ENV = "PAID_ARTIFACT_OPTION"
OPTION_A = "A"  # bundle/offer the AGPL wheel's Corresponding Source
OPTION_B = "B"  # drop the portal extra from the dependency set
# The ADR's recommendation: what `paid_policy.py option` prints for a FREE
# build, and nothing more. It is never a paid build's option — paid_option()
# refuses while the ADR-0008 marker reads OPERATOR.
DEFAULT_OPTION = OPTION_A

# --- the operator decision (ADR-0008) ---------------------------------------
# The marker is ONE line of the ADR, read verbatim: the recorded decision
# is reviewable history (a commit), not an environment variable.
ADR_NAME = "ADR-0008"
ADR_PATH = REPO / "docs" / "adr" / f"{ADR_NAME}-paid-artifact-agpl-policy.md"
DECISION_PREFIX = "Paid-artifact decision:"
OPERATOR = "OPERATOR"  # the marker's "not decided" value
_DECISION_LINE_RE = re.compile(rf"{re.escape(DECISION_PREFIX)}[ \t]*(.*)")

# The one copyleft-wrinkle package (rights inventory §2.1). PEP 503
# normalized name; wheel files use the escaped underscore form.
AGPL_DIST = "global-shortcut-portal"
AGPL_PACKAGE_DIR = "global_shortcut_portal"
AGPL_REPO_URL = "https://codeberg.org/marvin1099/python-global-shortcut-portal"
# Corresponding-Source tarball for the vendored 0.1.0 wheel: the upstream
# repo carries no v0.1.0 tag (oldest is v0.2.0), but PyPI hosts the sdist
# of the exact version. It is VENDORED under packaging/wheels/ (the
# vendored bytes and the file this URL serves are one and the same), and
# the URL stays the remote locator of the Flathub-shaped paid manifest.
# Verified against the PyPI JSON API and by byte-comparing the download on
# 2026-11-23.
AGPL_SDIST_NAME = "global_shortcut_portal-0.1.0.tar.gz"
AGPL_SDIST_URL = (
    "https://files.pythonhosted.org/packages/86/ba/"
    "f818e9294e29c4f6af60dcec6318f003efb57fccb9acab82aac8b988b37e/"
    "global_shortcut_portal-0.1.0.tar.gz")
AGPL_SDIST_SHA256 = (
    "bae2d6ba9f70e2b2b3d1d052b06cfd264abc04b7004dd3f847f3f429c22aebda")

WHEELS_DIR = Path(__file__).resolve().parent / "wheels"


# -- the operator decision: the fail-closed gate ------------------------------


def operator_decision(adr_path: Path = ADR_PATH) -> str:
    """ADR-0008's decision marker: OPERATOR (not made), or A, or B.

    The marker is the single ``Paid-artifact decision: <value>`` line of
    the ADR. Anything else is an error, never a default: no marker, two
    markers, an unknown value or an unreadable file all refuse here.
    Markdown emphasis around the value (``**A**``) is tolerated.
    """
    try:
        text = adr_path.read_text(encoding="utf-8")
    except OSError as e:
        raise PolicyError(
            f"cannot read {ADR_NAME} at {adr_path} ({e.strerror or e}) — "
            "the paid-artifact gate needs its decision marker and fails "
            "closed without it") from e
    found = [match.group(1).strip(" \t*`_")
             for line in text.splitlines()
             if (match := _DECISION_LINE_RE.fullmatch(line))]
    if not found:
        raise PolicyError(
            f"{adr_path} carries no decision marker — expected one line "
            f"`{DECISION_PREFIX} {OPERATOR}` (or A, or B). {ADR_NAME}'s "
            "recorded decision is the only thing that opens a paid build")
    if len(found) != 1:
        raise PolicyError(
            f"{adr_path}: {len(found)} decision marker lines — exactly one "
            f"`{DECISION_PREFIX} <value>` line is allowed")
    value = found[0]
    if value not in (OPERATOR, OPTION_A, OPTION_B):
        raise PolicyError(
            f"{adr_path}: decision marker reads {value!r} — expected "
            f"{OPERATOR}, {OPTION_A} or {OPTION_B}")
    return value


def switch_on(env: Mapping[str, str] | None = None) -> bool:
    """PAID_ARTIFACT: unset/''/'0' -> off, '1' -> on, anything else is an
    error — a mistyped switch must never read as a free build."""
    env = env if env is not None else dict(os.environ)
    value = env.get(SWITCH_ENV, "")
    if value in ("", "0"):
        return False
    if value == "1":
        return True
    raise PolicyError(
        f"{SWITCH_ENV}={value!r}: expected '1' to engage the paid build, "
        f"or unset/'0' for the free build ({ADR_NAME})")


def normalize_option(raw: str | None, default: str = DEFAULT_OPTION) -> str:
    """''/None -> ``default``; 'a'/'A'/'b'/'B' accepted; else error."""
    if raw is None or not raw.strip():
        return default
    candidate = raw.strip().upper()
    if candidate in (OPTION_A, OPTION_B):
        return candidate
    raise PolicyError(
        f"{OPTION_ENV}={raw!r}: expected 'A' or 'B' ({ADR_NAME}); unset "
        f"means the recorded decision")


def paid_option(env: Mapping[str, str] | None = None,
                adr_path: Path = ADR_PATH) -> str:
    """The option a PAID build may use — or PolicyError. FAILS CLOSED.

    ADR-0008's recorded decision is the only source of a paid option:
    while the marker reads OPERATOR the build is refused; once it reads A
    or B, that is what the build gets. ``PAID_ARTIFACT_OPTION`` may
    confirm the decision and nothing else — unset is fine, a different
    value is an error (a pipeline cannot pick an option the operator did
    not record).
    """
    env = env if env is not None else dict(os.environ)
    decision = operator_decision(adr_path)
    if decision == OPERATOR:
        raise PolicyError(
            f"{SWITCH_ENV}=1 refused: {ADR_NAME} records no decision yet "
            f"(its marker reads `{DECISION_PREFIX} {OPERATOR}`) — no paid "
            "artifact is built while the choice between option A (offer "
            "the AGPL wheel's Corresponding Source) and option B (drop "
            "the portal extra) is unmade. The operator records it by "
            f"changing that marker line in {adr_path} to {OPTION_A} or "
            f"{OPTION_B}; until then the free build ({SWITCH_ENV} unset) "
            "is the only build")
    chosen = normalize_option(env.get(OPTION_ENV), default=decision)
    if chosen != decision:
        raise PolicyError(
            f"{OPTION_ENV}={chosen} contradicts {ADR_NAME}'s recorded "
            f"decision {decision} ({adr_path}) — the marker is the source "
            "of truth: fix the pipeline, or have the operator re-decide")
    return decision


def _is_agpl_pin(line: str) -> bool:
    """True for a lock/requirements line whose pin IS the AGPL wheel."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    name = re.split(r"[<>=!~;\s\[]", stripped, maxsplit=1)[0]
    return locklib.norm_name(name) == AGPL_DIST


def filter_lock_text(text: str, option: str) -> str:
    """Option B: the paid dependency lock without the AGPL pin.

    A no-op for A and when the pin is absent (the deb's committed lock
    never carried it — there the filter is a guard, not an edit).
    Comments, hashes and every other pin pass through untouched.
    """
    if option == OPTION_A:
        return text
    kept = [line for line in text.splitlines() if not _is_agpl_pin(line)]
    return "\n".join(kept) + "\n"


# -- flatpak manifest transformation ----------------------------------------


def _drop_agpl_build_commands(lines: list[str]) -> list[str]:
    """Option B over a manifest's lines: the wheel leaves the pip install
    line and the Flathub license directory loses its copy step. The
    wheel token is matched only at a whitespace boundary so a URL that
    merely CONTAINS the filename (a source url: line) is untouched —
    dropping source items is the next function's job."""
    out = []
    for line in lines:
        if re.search(rf"licenses/\$FLATPAK_ID/{AGPL_DIST}(?:\s|$)", line):
            continue  # the wheel's license install line
        new = re.sub(rf"(?<!\S){AGPL_PACKAGE_DIR}-\S+\.whl", "", line)
        out.append(new.rstrip() if new != line else new)
    return out


def _drop_agpl_source_blocks(lines: list[str]) -> list[str]:
    """Option B: remove the wheel's `- type: file` source item (the
    2-4 line block whose url:/path: names the vendored/remote wheel).
    Comment/blank lines always end an item — a comment between items
    belongs to what follows and is never dropped with the block."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() != "- type: file":
            out.append(lines[i])
            i += 1
            continue
        item_indent = len(lines[i]) - len(lines[i].lstrip(" "))
        block = [lines[i]]
        j = i + 1
        while j < len(lines):
            stripped = lines[j].lstrip(" ")
            if not stripped or stripped.startswith("#"):
                break
            if len(lines[j]) - len(stripped) <= item_indent:
                break
            block.append(lines[j])
            j += 1
        if re.search(rf"{AGPL_PACKAGE_DIR}-\S+\.whl", "\n".join(block)):
            i = j  # drop the whole source item
        else:
            out.extend(block)
            i = j
    return out


def _has_local_wheel_sources(text: str) -> bool:
    """The CI (bundle) variant identifies itself by its vendored wheels:
    `path: wheels/<wheel>` source lines. The Flathub variant has none."""
    return any(re.match(r"^\s*path:\s*wheels/", line)
               for line in text.splitlines())


def _offer_module(local: bool) -> str:
    """Option A's appended module. The two committed variants grow the
    SAME module — only the sdist's locator differs, exactly like their
    wheel sources (local path vs remote url)."""
    locator = (f"        path: wheels/{AGPL_SDIST_NAME}" if local
               else f"        url: {AGPL_SDIST_URL}")
    return f"""\
  # ADR-0008 paid option A — Corresponding-Source offer for the AGPL-3.0-only
  # global-shortcut-portal wheel the deps module installs. The wheel is pure
  # Python, so the sources pip installed ARE the preferred form for
  # modification: copied verbatim under /app/share/doc. The license gets its
  # own Flathub license directory. The upstream sdist of the same version
  # ships INSIDE the artifact next to them, content-pinned in this manifest
  # (url + sha256 in packaging/paid_policy.py).
  - name: agpl-source-offer
    buildsystem: simple
    build-commands:
      - install -Dm0644 -t $FLATPAK_DEST/share/licenses/$FLATPAK_ID/agpl-source-offer $FLATPAK_DEST/lib/python3*/site-packages/{AGPL_PACKAGE_DIR}-*.dist-info/licenses/LICENSE
      - mkdir -p $FLATPAK_DEST/share/doc/{AGPL_DIST}
      - cp -r $FLATPAK_DEST/lib/python3*/site-packages/{AGPL_PACKAGE_DIR} $FLATPAK_DEST/share/doc/{AGPL_DIST}/src
      - install -Dm0644 -t $FLATPAK_DEST/share/doc/{AGPL_DIST} {AGPL_SDIST_NAME}
    sources:
      - type: file
{locator}
        sha256: {AGPL_SDIST_SHA256}
"""


def transform_manifest_text(text: str, option: str) -> str:
    """The paid variant of a committed Flatpak manifest.

    Option A appends the ``agpl-source-offer`` module (the sdist follows
    the variant's own locator style, so the divergence rule is
    preserved). Option B drops the wheel from the pip install line, its
    license install and its source item; it is an ERROR if B finds no
    wheel to drop — the manifests install it by design, and a silent
    no-op would read as "paid B applied" when it did nothing.
    """
    if option == OPTION_A:
        if not text.endswith("\n"):
            text += "\n"
        return text + "\n" + _offer_module(_has_local_wheel_sources(text))
    lines = text.splitlines()
    pip_hits = sum(
        1 for line in lines
        if re.search(rf"{AGPL_PACKAGE_DIR}-\S+\.whl", line)
        and "pip3 install" in line)
    if pip_hits != 1:
        raise PolicyError(
            f"option {OPTION_B}: expected exactly one pip3 install line "
            f"carrying {AGPL_PACKAGE_DIR}-*.whl, found {pip_hits} — is "
            "this a SayItErmano flatpak manifest?")
    lines = _drop_agpl_build_commands(lines)
    lines = _drop_agpl_source_blocks(lines)
    return "\n".join(lines) + "\n"


# -- option A: the in-artifact source offer ---------------------------------


class _WheelParts(NamedTuple):
    """What the offer takes from the wheel, and what the sdist must match."""

    name: str
    version: str
    license_expression: str
    license: bytes
    files: dict[str, bytes]  # package-relative path -> bytes


def _wheel_offer_parts(wheel: Path) -> _WheelParts:
    """The AGPL wheel's license text, package sources and identity — the
    offer bytes, and the reference the sdist is verified against. Works
    from the wheel alone: offline, deterministic, and automatically the
    exact version the artifact installs."""
    name, version = locklib.parse_wheel_filename(wheel.name)
    if name != AGPL_DIST:
        raise PolicyError(
            f"{wheel.name}: not a {AGPL_DIST} wheel (parsed {name!r})")
    license_expression = ""
    license_bytes: bytes | None = None
    files: dict[str, bytes] = {}
    dist_info = rf"{AGPL_PACKAGE_DIR}-\S+\.dist-info"
    with zipfile.ZipFile(wheel) as archive:
        for entry in archive.namelist():
            if entry.endswith("/"):
                continue
            if re.fullmatch(rf"{dist_info}/METADATA", entry):
                metadata = email.parser.Parser().parsestr(
                    archive.read(entry).decode("utf-8"))
                license_expression = metadata.get("License-Expression", "")
            elif re.fullmatch(rf"{dist_info}/licenses/LICENSE", entry):
                license_bytes = archive.read(entry)
            elif entry.startswith(f"{AGPL_PACKAGE_DIR}/"):
                files[entry[len(AGPL_PACKAGE_DIR) + 1:]] = archive.read(entry)
            # else: WHEEL/RECORD — metadata, not source
    if license_bytes is None:
        raise PolicyError(
            f"{wheel.name}: no .dist-info/licenses/LICENSE inside — "
            "the offer cannot ship the license text; re-vendor the "
            "wheel (packaging/flatpak manifests pin its sha256)")
    if not any(f.endswith(".py") for f in files):
        raise PolicyError(f"{wheel.name}: no package sources found")
    if not license_expression:
        raise PolicyError(
            f"{wheel.name}: METADATA carries no License-Expression — the "
            "sdist cannot be matched to the wheel's license")
    return _WheelParts(name, version, license_expression, license_bytes,
                       files)


def agpl_wheel_path(wheels_dir: Path = WHEELS_DIR) -> Path:
    """The vendored AGPL wheel (exactly one; a second is an error)."""
    hits = sorted(wheels_dir.glob(f"{AGPL_PACKAGE_DIR}-*.whl"))
    if len(hits) != 1:
        raise PolicyError(
            f"expected exactly one vendored {AGPL_PACKAGE_DIR}-*.whl under "
            f"{wheels_dir}, found {[p.name for p in hits]}")
    return hits[0]


def agpl_sdist_path(wheels_dir: Path = WHEELS_DIR) -> Path:
    """The vendored sdist — the wheel's Corresponding-Source tarball."""
    path = wheels_dir / AGPL_SDIST_NAME
    if not path.is_file():
        raise PolicyError(
            f"missing vendored sdist {path} — option A ships the AGPL "
            "wheel's Corresponding Source from this file; re-vendor the "
            f"upstream sdist ({AGPL_SDIST_URL})")
    return path


def _read_sdist(sdist: Path) -> dict[str, bytes]:
    """{member path: bytes} of the sdist's regular files. Members are read
    into memory only (nothing is extracted to disk), and any member that
    could escape an extraction directory is refused outright."""
    try:
        with tarfile.open(sdist, "r:gz") as archive:
            members = archive.getmembers()
            out: dict[str, bytes] = {}
            for member in members:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise PolicyError(
                        f"{sdist.name}: unsafe member path {member.name!r}")
                if member.isdir():
                    continue
                if not member.isfile():
                    raise PolicyError(
                        f"{sdist.name}: {member.name} is not a regular file")
                stream = archive.extractfile(member)
                assert stream is not None  # isfile() members extract
                out[member.name] = stream.read()
    except (tarfile.TarError, OSError, EOFError) as e:
        raise PolicyError(
            f"{sdist.name}: not a readable sdist tarball ({e})") from e
    if not out:
        raise PolicyError(f"{sdist.name}: the sdist carries no files")
    return out


def _check_sdist(sdist: Path, parts: _WheelParts) -> None:
    """``sdist`` is the Corresponding Source of the wheel ``parts`` came
    from: same distribution, version and license expression in its
    PKG-INFO, the same license text, and every file the wheel installs
    present under its source tree byte for byte (a pure-Python wheel's
    installed .py files ARE its source). Raises PolicyError otherwise."""
    members = _read_sdist(sdist)
    root = f"{AGPL_PACKAGE_DIR}-{parts.version}"
    pkg_info = members.get(f"{root}/PKG-INFO")
    if pkg_info is None:
        raise PolicyError(
            f"{sdist.name}: no {root}/PKG-INFO — not an sdist of "
            f"{AGPL_DIST} {parts.version}")
    info = email.parser.Parser().parsestr(pkg_info.decode("utf-8"))
    for field, expected in (("Name", parts.name),
                            ("Version", parts.version),
                            ("License-Expression", parts.license_expression)):
        actual = info.get(field)
        if actual != expected:
            raise PolicyError(
                f"{sdist.name}: PKG-INFO {field} is {actual!r}, the "
                f"vendored wheel's is {expected!r} — not its source")
    if members.get(f"{root}/LICENSE") != parts.license:
        raise PolicyError(
            f"{sdist.name}: {root}/LICENSE is not the wheel's license text")
    for rel, data in sorted(parts.files.items()):
        located = [m for m in (f"{root}/src/{AGPL_PACKAGE_DIR}/{rel}",
                               f"{root}/{AGPL_PACKAGE_DIR}/{rel}")
                   if m in members]
        if not located:
            raise PolicyError(
                f"{sdist.name}: the wheel's {AGPL_PACKAGE_DIR}/{rel} has "
                "no source in the sdist")
        if members[located[0]] != data:
            raise PolicyError(
                f"{sdist.name}: {located[0]} is not the wheel's "
                f"{AGPL_PACKAGE_DIR}/{rel}")


def verify_sdist(sdist: Path, wheel: Path) -> None:
    """The vendored sdist is the pinned Corresponding Source of the
    vendored wheel: its sha256 is AGPL_SDIST_SHA256, and it matches the
    wheel (_check_sdist). Every option-A paid path ships the sdist only
    after this passes."""
    digest = locklib.sha256_file(sdist)
    if digest != AGPL_SDIST_SHA256:
        raise PolicyError(
            f"{sdist.name}: sha256 {digest} is not the pin "
            f"{AGPL_SDIST_SHA256} — the bytes are not the pinned sdist "
            f"({AGPL_SDIST_URL}); re-vendor, or re-pin "
            f"{AGPL_SDIST_SHA256} if the wheel moved")
    _check_sdist(sdist, _wheel_offer_parts(wheel))


def write_agpl_offer(wheel: Path, dest: Path,
                     sdist: Path | None = None) -> Path:
    """Extract the wheel's Corresponding-Source offer into ``dest``:

    ::

        dest/LICENSE   the wheel's own AGPL license text
        dest/src/...   the package sources (pure-Python wheel: the
                       .py files pip installs ARE the source form)
        dest/SOURCE    the offer notice — pinned sdist URL + sha256,
                       wheel identity, upstream repository
        dest/<sdist>   the upstream sdist of the same version: the
                       Corresponding-Source tarball itself, verified
                       against its pin and against the wheel first

    Works from the vendored bytes alone: offline, deterministic, and
    automatically the exact version the artifact installs.
    """
    parts = _wheel_offer_parts(wheel)
    target = sdist if sdist is not None else agpl_sdist_path()
    digest = locklib.sha256_file(target)
    if digest != AGPL_SDIST_SHA256:
        raise PolicyError(
            f"{target.name}: sha256 {digest} is not the pin "
            f"{AGPL_SDIST_SHA256} — refusing to ship it as the "
            "Corresponding Source")
    _check_sdist(target, parts)

    (dest / "src").mkdir(parents=True, exist_ok=True)
    for rel, data in sorted(parts.files.items()):
        target_file = dest / "src" / AGPL_PACKAGE_DIR / rel
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_bytes(data)
    (dest / "LICENSE").write_bytes(parts.license)
    shutil.copyfile(target, dest / AGPL_SDIST_NAME)
    (dest / "SOURCE").write_text(
        f"{AGPL_DIST} {parts.version} — AGPL-3.0-only (ADR-0008 option A offer)\n"
        f"\n"
        f"Vendored wheel: packaging/wheels/{wheel.name}\n"
        f"  sha256 {locklib.sha256_file(wheel)}\n"
        f"Corresponding Source: the .py files under src/ (pure-Python\n"
        f"wheel — the installed sources are the preferred form for\n"
        f"modification) and the upstream sdist of the same version,\n"
        f"shipped next to this notice as {AGPL_SDIST_NAME}:\n"
        f"  {AGPL_SDIST_URL}\n"
        f"  sha256 {AGPL_SDIST_SHA256}\n"
        f"Upstream repository: {AGPL_REPO_URL}\n"
        f"\n"
        f"Paid artifacts also publish the sdist URL next to the artifact\n"
        f"(release asset / apt repo entry) so Corresponding Source stays\n"
        f"available at no further charge.\n", encoding="utf-8")
    return dest


# -- CLI ---------------------------------------------------------------------


def _paid_option(env: Mapping[str, str] | None) -> str:
    """The free-build reading of PAID_ARTIFACT_OPTION (no gate)."""
    return normalize_option(env.get(OPTION_ENV))


def main(argv: list[str] | None = None,
         env: Mapping[str, str] | None = None,
         adr_path: Path = ADR_PATH) -> int:
    parser = argparse.ArgumentParser(
        prog="paid_policy.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("option", help="print the option a PAID build uses; "
                                    "with the switch off, the ADR "
                                    "recommendation")

    sub.add_parser("verify", help="the vendored sdist is the pinned "
                                    "Corresponding Source of the "
                                    "vendored wheel")

    f = sub.add_parser("filter-lock",
                       help="print the lock filtered per the paid option")
    f.add_argument("--lock", type=Path, required=True,
                   help="constraints/lock file to read")

    o = sub.add_parser("agpl-offer",
                       help="write the AGPL source offer into --dest")
    o.add_argument("--dest", type=Path, required=True)
    o.add_argument("--wheel", type=Path, default=None,
                   help="vendored wheel (default: the one in "
                        "packaging/wheels/)")

    m = sub.add_parser("flatpak",
                       help="print the paid variant of a flatpak manifest")
    m.add_argument("--manifest", type=Path, default=None,
                   help="manifest to read (default: stdin)")

    args = parser.parse_args(argv)
    env = env if env is not None else dict(os.environ)
    try:
        on = switch_on(env)
        if not on and args.cmd not in ("option", "verify"):
            # The switch is OFF: pass input through untouched so build
            # pipelines can always route through here (a free build is
            # byte-for-byte unaffected by this tool existing).
            if args.cmd == "flatpak":
                text = (args.manifest.read_text(encoding="utf-8")
                        if args.manifest else sys.stdin.read())
                sys.stdout.write(text)
            elif args.cmd == "filter-lock":
                sys.stdout.write(args.lock.read_text(encoding="utf-8"))
            elif args.cmd == "agpl-offer":
                print(f"{SWITCH_ENV}!=1: no offer written", file=sys.stderr)
            return 0
        # FAIL CLOSED: a paid path exists only behind a recorded decision.
        option = paid_option(env, adr_path) if on else _paid_option(env)
        if args.cmd == "option":
            print(option)
        elif args.cmd == "verify":
            verify_sdist(agpl_sdist_path(), agpl_wheel_path())
            print(f"AGPL source OK: {AGPL_SDIST_NAME} is the pinned "
                  "Corresponding Source of the vendored wheel")
        elif args.cmd == "filter-lock":
            sys.stdout.write(filter_lock_text(
                args.lock.read_text(encoding="utf-8"), option))
        elif args.cmd == "agpl-offer":
            wheel = args.wheel or agpl_wheel_path()
            dest = write_agpl_offer(wheel, args.dest)
            print(f"agpl offer written: {dest} (option {option})")
        elif args.cmd == "flatpak":
            if option == OPTION_A:
                # the module pins the vendored sdist: emit no manifest
                # for bytes that are not the pin
                verify_sdist(agpl_sdist_path(), agpl_wheel_path())
            text = (args.manifest.read_text(encoding="utf-8")
                    if args.manifest else sys.stdin.read())
            sys.stdout.write(transform_manifest_text(text, option))
    except (PolicyError, OSError, ValueError, zipfile.BadZipFile) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
