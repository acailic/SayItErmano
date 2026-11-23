#!/usr/bin/env python3
"""PAID_ARTIFACT policy hooks (ADR-0008) — one switch, two options.

The rights inventory (docs/research/2026-11-22-rights-and-redistribution-
inventory.md, "The AGPL finding") established: the vendored
``global-shortcut-portal`` wheel is AGPL-3.0-only and installs into the
Flatpak, so a PAID artifact must either offer that wheel's Corresponding
Source (option A) or drop the ``portal`` extra from its dependency set
(option B). The operator picks A or B; BOTH are implemented here and in
the build entry points, behind one switch:

  PAID_ARTIFACT=1            engage (default: unset — free builds are
                             byte-for-byte unaffected)
  PAID_ARTIFACT_OPTION=A|B   which remediation (default: A — the ADR's
                             recommendation, NOT a decision; ADR-0008's
                             decision marker is OPERATOR)

What each consumer does:

* ``packaging/build-deb.sh`` — option A extracts this offer (sources +
  license + pinned-sdist notice) into /usr/share/doc inside the deb;
  option B filters the AGPL pin out of the installed dependency lock
  (a no-op today: the deb's closure never carried the wheel — the
  filter guarantees it stays that way).
* the Flatpak manifests — ``paid_policy.py flatpak`` emits the paid
  variant of either committed manifest: option A appends an
  ``agpl-source-offer`` module (the installed sources ARE the wheel's
  preferred source form; license copied to the Flathub license dir);
  option B rewrites the ``deps`` module to drop the wheel, its license
  line and its source entry.

Everything here is stdlib-only and offline: the wheel's source offer is
extracted from the vendored wheel bytes; the pinned upstream sdist URL
below is the "source tarball next to the artifact" half of the offer
(see AGPL_SDIST_URL). Unit tests: tests/test_paid_policy.py.

Usage:
  paid_policy.py option                     print the normalized option
  paid_policy.py filter-lock --lock F       paid dependency lock on stdout
  paid_policy.py agpl-offer --dest D        write the offer into D
  paid_policy.py flatpak [--manifest F]     paid manifest on stdout
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
import zipfile
from pathlib import Path

# locklib owns PEP 503 name normalization + wheel-filename parsing; load
# it by path (scripts have no package context) exactly like the tests do.
_spec = importlib.util.spec_from_file_location(
    "locklib", Path(__file__).resolve().parent / "deb" / "locklib.py")
locklib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(locklib)


class PolicyError(Exception):
    """Actionable paid-policy failure; str() is the user-facing message."""


SWITCH_ENV = "PAID_ARTIFACT"
OPTION_ENV = "PAID_ARTIFACT_OPTION"
OPTION_A = "A"  # bundle/offer the AGPL wheel's Corresponding Source
OPTION_B = "B"  # drop the portal extra from the dependency set
DEFAULT_OPTION = OPTION_A  # ADR-0008 recommendation; decision is OPERATOR

# The one copyleft-wrinkle package (rights inventory §2.1). PEP 503
# normalized name; wheel files use the escaped underscore form.
AGPL_DIST = "global-shortcut-portal"
AGPL_PACKAGE_DIR = "global_shortcut_portal"
AGPL_REPO_URL = "https://codeberg.org/marvin1099/python-global-shortcut-portal"
# Corresponding-Source tarball for the vendored 0.1.0 wheel: the upstream
# repo carries no v0.1.0 tag (oldest is v0.2.0), but PyPI hosts the sdist
# of the exact version — content-addressed, so this URL + sha256 pair is
# the offer target published next to any paid artifact. Verified against
# the PyPI JSON API on 2026-11-23.
AGPL_SDIST_URL = (
    "https://files.pythonhosted.org/packages/86/ba/"
    "f818e9294e29c4f6af60dcec6318f003efb57fccb9acab82aac8b988b37e/"
    "global_shortcut_portal-0.1.0.tar.gz")
AGPL_SDIST_SHA256 = (
    "bae2d6ba9f70e2b2b3d1d052b06cfd264abc04b7004dd3f847f3f429c22aebda")

WHEELS_DIR = Path(__file__).resolve().parent / "wheels"


def agpl_wheel_path(wheels_dir: Path = WHEELS_DIR) -> Path:
    """The vendored AGPL wheel (exactly one; a second is an error)."""
    hits = sorted(wheels_dir.glob(f"{AGPL_PACKAGE_DIR}-*.whl"))
    if len(hits) != 1:
        raise PolicyError(
            f"expected exactly one vendored {AGPL_PACKAGE_DIR}-*.whl under "
            f"{wheels_dir}, found {[p.name for p in hits]}")
    return hits[0]


def normalize_option(raw: str | None) -> str:
    """''/None -> the default option; 'a'/'A'/'b'/'B' accepted; else error."""
    if raw is None or not raw.strip():
        return DEFAULT_OPTION
    candidate = raw.strip().upper()
    if candidate in (OPTION_A, OPTION_B):
        return candidate
    raise PolicyError(
        f"{OPTION_ENV}={raw!r}: expected 'A' or 'B' (ADR-0008); unset "
        f"means the default, {DEFAULT_OPTION}")


def switch_on(env: dict[str, str] | None = None) -> bool:
    env = env if env is not None else dict(os.environ)
    return env.get(SWITCH_ENV, "") == "1"


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


AGPL_OFFER_MODULE = f"""\
  # ADR-0008 paid option A — Corresponding-Source offer for the AGPL-3.0-only
  # global-shortcut-portal wheel the deps module installs. The wheel is pure
  # Python, so the sources pip installed ARE the preferred form for
  # modification: copied verbatim under /app/share/doc. The license gets its
  # own Flathub license directory. The pinned sdist (same version as the
  # wheel, url + sha256 in packaging/paid_policy.py) is the source tarball
  # published next to the paid artifact — that pair is the written offer.
  - name: agpl-source-offer
    buildsystem: simple
    build-commands:
      - install -Dm0644 -t $FLATPAK_DEST/share/licenses/$FLATPAK_ID/agpl-source-offer $FLATPAK_DEST/lib/python3*/site-packages/{AGPL_PACKAGE_DIR}-*.dist-info/licenses/LICENSE
      - mkdir -p $FLATPAK_DEST/share/doc/{AGPL_DIST}
      - cp -r $FLATPAK_DEST/lib/python3*/site-packages/{AGPL_PACKAGE_DIR} $FLATPAK_DEST/share/doc/{AGPL_DIST}/src
"""


def transform_manifest_text(text: str, option: str) -> str:
    """The paid variant of a committed Flatpak manifest.

    Option A appends the ``agpl-source-offer`` module (identical bytes
    for both manifest variants — it adds no sources, so the divergence
    rule is preserved). Option B drops the wheel from the pip install
    line, its license install and its source item; it is an ERROR if B
    finds no wheel to drop — the manifests install it by design, and a
    silent no-op would read as "paid B applied" when it did nothing.
    """
    if option == OPTION_A:
        if not text.endswith("\n"):
            text += "\n"
        return text + "\n" + AGPL_OFFER_MODULE
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


def write_agpl_offer(wheel: Path, dest: Path) -> Path:
    """Extract the wheel's Corresponding-Source offer into ``dest``:

    ::

        dest/LICENSE   the wheel's own AGPL license text
        dest/src/...   the package sources (pure-Python wheel: the
                       .py files pip installs ARE the source form)
        dest/SOURCE    the offer notice — pinned sdist URL + sha256,
                       wheel identity, upstream repository

    Works from the vendored wheel bytes alone: offline, deterministic,
    and automatically the exact version the artifact installs.
    """
    name, version = locklib.parse_wheel_filename(wheel.name)
    if name != AGPL_DIST:
        raise PolicyError(
            f"{wheel.name}: not a {AGPL_DIST} wheel (parsed {name!r})")
    (dest / "src").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as archive:
        for entry in archive.namelist():
            if entry.endswith("/"):
                continue
            if entry.startswith(f"{AGPL_PACKAGE_DIR}/"):
                target = dest / "src" / entry
            elif re.fullmatch(
                    rf"{AGPL_PACKAGE_DIR}-\S+\.dist-info/licenses/LICENSE",
                    entry):
                target = dest / "LICENSE"
            else:
                continue  # WHEEL/METADATA/RECORD — metadata, not source
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
        if not (dest / "LICENSE").is_file():
            raise PolicyError(
                f"{wheel.name}: no .dist-info/licenses/LICENSE inside — "
                "the offer cannot ship the license text; re-vendor the "
                "wheel (packaging/flatpak manifests pin its sha256)")
        sources = sorted((dest / "src" / AGPL_PACKAGE_DIR).glob("*.py"))
        if not sources:
            raise PolicyError(f"{wheel.name}: no package sources found")
    wheel_sha = locklib.sha256_file(wheel)
    (dest / "SOURCE").write_text(
        f"{AGPL_DIST} {version} — AGPL-3.0-only (ADR-0008 option A offer)\n"
        f"\n"
        f"Vendored wheel: packaging/wheels/{wheel.name}\n"
        f"  sha256 {wheel_sha}\n"
        f"Corresponding Source: the .py files under src/ (pure-Python\n"
        f"wheel — the installed sources are the preferred form for\n"
        f"modification) and the upstream sdist of the same version:\n"
        f"  {AGPL_SDIST_URL}\n"
        f"  sha256 {AGPL_SDIST_SHA256}\n"
        f"Upstream repository: {AGPL_REPO_URL}\n"
        f"\n"
        f"Paid artifacts publish the sdist URL next to the artifact\n"
        f"(release asset / apt repo entry) so Corresponding Source stays\n"
        f"available at no further charge.\n", encoding="utf-8")
    return dest


# -- CLI ---------------------------------------------------------------------


def _paid_option(env: dict[str, str] | None = None) -> str:
    env = env if env is not None else dict(os.environ)
    return normalize_option(env.get(OPTION_ENV))


def main(argv: list[str] | None = None,
         env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="paid_policy.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("option", help="print the normalized paid option (A|B)")

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
    try:
        if not switch_on(env) and args.cmd != "option":
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
        option = _paid_option(env)
        if args.cmd == "option":
            print(option)
        elif args.cmd == "filter-lock":
            sys.stdout.write(filter_lock_text(
                args.lock.read_text(encoding="utf-8"), option))
        elif args.cmd == "agpl-offer":
            wheel = args.wheel or agpl_wheel_path()
            dest = write_agpl_offer(wheel, args.dest)
            print(f"agpl offer written: {dest} (option {option})")
        elif args.cmd == "flatpak":
            text = (args.manifest.read_text(encoding="utf-8")
                    if args.manifest else sys.stdin.read())
            sys.stdout.write(transform_manifest_text(text, option))
    except (PolicyError, OSError, ValueError, zipfile.BadZipFile) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
