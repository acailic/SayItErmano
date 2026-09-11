#!/usr/bin/env python3
"""Dependency-lock machinery for packaging/deb/constraints.txt (Q3).

The shell generator used to scrape ``python -m pip hash`` output with an
``awk '/^sha256=/'`` pattern — but pip prints ``--hash=sha256:...``, so
the pattern matched NOTHING and the writer emitted malformed
``example==1.0 --`` lines (audit E3). Hashing now happens here, in pure
stdlib Python over the downloaded wheel bytes, with every failure mode
the audit called out handled explicitly:

* wheel-to-pin matching by parsed wheel FILENAME (not a glob), so a
  wrong version or an ambiguous wheel set is an error, not a guess;
* missing wheels, duplicate wheels for one pin, mixed hash/pin-only
  lines, malformed hashes: actionable errors, previous lock preserved
  (atomic replace only after a successful self-validate round trip);
* the stale-constraints pins-coverage check the release workflows used
  to duplicate inline is ``pins-coverage`` here, so prepare/publish/CI
  run one tested implementation.

Usage:
  locklib.py validate <lock> [--require-hashes]
  locklib.py pins-coverage --pyproject <pyproject.toml> --lock <lock>
  locklib.py write --wheels-dir <dir> --pins <file> --header <file> \
                    --out <lock>

Exit status 1 on any validation failure; ``write`` never touches the
output file unless the generated text passes ``validate``.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path

HASH_LINE_RE = re.compile(
    r"^(?P<pin>[A-Za-z0-9][A-Za-z0-9._-]*==\S+)\s+"
    r"--hash=(?P<alg>sha256):(?P<digest>[0-9a-f]{64})$")

PIN_LINE_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>\S+)$")


class LockError(Exception):
    """Actionable lock failure; str() is the whole user-facing message."""


def norm_name(name: str) -> str:
    """PEP 503 normalized distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------------------
# parsing / validation
# ---------------------------------------------------------------------------

def parse_pins_text(text: str) -> list[tuple[str, str]]:
    """Exact ``name==version`` pins from the non-comment lines (hash
    suffixes are tolerated so this also reads a hash-locked file)."""
    pins: list[tuple[str, str]] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(_strip_hash_suffixes(text).splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = PIN_LINE_RE.match(line)
        if not m:
            raise LockError(
                f"line {lineno}: not an exact pin: {line!r} (expected "
                f"'name==version')")
        name = norm_name(m.group("name"))
        if name in seen:
            raise LockError(f"line {lineno}: duplicate pin for {name}")
        seen.add(name)
        pins.append((name, m.group("version")))
    if not pins:
        raise LockError("no pins found")
    return pins


def validate_lock_text(text: str, *, require_hashes: bool) -> list[tuple[str, str]]:
    """Full structural validation; returns [(norm_name, version)].

    Rejects: non-pin lines, malformed hashes, non-sha256 algorithms,
    MIXED hash/pin-only entries (an ambiguous lock is worse than either
    clean state), duplicates, and — with require_hashes — pin-only."""
    pins: list[tuple[str, str]] = []
    seen: set[str] = set()
    hashed: set[str] = set()
    bare: set[str] = set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = HASH_LINE_RE.match(line)
        if m is not None:
            name_raw, _, version = m.group("pin").partition("==")
            name = norm_name(name_raw)
            hashed.add(name)
        else:
            bare_m = PIN_LINE_RE.match(line)
            if bare_m is None:
                raise LockError(
                    f"line {lineno}: malformed lock line: {line!r} — "
                    f"expected 'name==version --hash=sha256:<64 hex>'")
            name = norm_name(bare_m.group("name"))
            version = bare_m.group("version")
            bare.add(name)
        if name in seen:
            raise LockError(f"line {lineno}: duplicate pin for {name}")
        seen.add(name)
        pins.append((name, version))
    if not pins:
        raise LockError("lock carries no pins")
    if hashed and bare:
        raise LockError(
            f"mixed hash/pin-only entries (ambiguous lock): hashed "
            f"{sorted(hashed)}, pin-only {sorted(bare)} — regenerate the "
            "whole lock: packaging/deb/update-constraints.sh")
    if require_hashes and bare:
        raise LockError(
            f"pin-only entries in a lock that must be hash-locked: "
            f"{sorted(bare)} — regenerate with a networked run of "
            "packaging/deb/update-constraints.sh")
    return pins


# ---------------------------------------------------------------------------
# wheel matching / hashing
# ---------------------------------------------------------------------------

def parse_wheel_filename(filename: str) -> tuple[str, str]:
    """(normalized name, version) from a wheel filename. Per the binary
    distribution format spec, runs of non-alphanumeric characters in the
    project name are escaped to ``_`` in wheel filenames, so a dash-joined
    stem splits cleanly: 5 parts = name-version-py-abi-platform, 6 adds a
    build tag."""
    stem = filename[:-4] if filename.endswith(".whl") else filename
    parts = stem.split("-")
    if len(parts) == 5:            # name-version-py-abi-platform
        name, version = parts[0], parts[1]
    elif len(parts) == 6:          # name-version-build-py-abi-platform
        name, version = parts[0], parts[1]
    else:
        raise LockError(
            f"not a wheel filename: {filename!r} (expected 5 or 6 "
            f"dash-joined components after name escaping)")
    if not name or not version:
        raise LockError(f"not a wheel filename: {filename!r}")
    return norm_name(name), version


def match_wheels(wheels_dir: Path,
                 pins: list[tuple[str, str]]) -> dict[str, tuple[Path, str]]:
    """Map every pin to exactly one downloaded wheel.

    Errors on: no wheels at all, a pin with no wheel (missing download),
    a pin with multiple wheels (ambiguous set — e.g. two versions), and
    a wheel whose version disagrees with the pin (name-collision)."""
    by_name: dict[str, list[tuple[Path, str]]] = {}
    for path in sorted(wheels_dir.glob("*.whl")):
        name, version = parse_wheel_filename(path.name)
        by_name.setdefault(name, []).append((path, version))
    if not by_name:
        raise LockError(f"no *.whl files under {wheels_dir}")
    matched: dict[str, tuple[Path, str]] = {}
    problems: list[str] = []
    for name, version in pins:
        wheels = by_name.get(name, [])
        if not wheels:
            problems.append(f"missing wheel for {name}=={version}")
        elif len(wheels) > 1:
            found = ", ".join(f"{p.name}" for p, _ in wheels)
            problems.append(
                f"ambiguous wheels for {name} (pin {version}): {found}")
        else:
            path, wheel_ver = wheels[0]
            if wheel_ver != version:
                problems.append(
                    f"wheel/version mismatch for {name}: pin says "
                    f"{version}, downloaded {path.name}")
            else:
                matched[name] = (path, wheel_ver)
    if problems:
        raise LockError("wheel set does not match the pins:\n  "
                        + "\n  ".join(problems))
    return matched


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def render_lock(header: str, pins: list[tuple[str, str]],
                hashes: dict[str, str]) -> str:
    lines = [header, ""]
    for name, version in pins:
        lines.append(f"{name}=={version} --hash=sha256:{hashes[name]}")
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, text: str) -> None:
    """Replace only on success; the previous lock survives any failure."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name,
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def build_lock_text(wheels_dir: Path, pins_text: str, header: str) -> str:
    """Generation WITH the round-trip guarantee: the emitted text must
    itself validate as a fully hash-locked file before it is returned."""
    pins = parse_pins_text(pins_text)
    matched = match_wheels(wheels_dir, pins)
    hashes = {name: sha256_file(path) for name, (path, _) in matched.items()}
    text = render_lock(header, pins, hashes)
    validate_lock_text(text, require_hashes=True)  # self round trip
    return text


# ---------------------------------------------------------------------------
# pins coverage (the stale-constraints check both release workflows run)
# ---------------------------------------------------------------------------

def check_pins_coverage(pyproject: Path, lock: Path) -> str:
    """Every runtime dependency in pyproject must have an exact pin in
    the lock. Returns a one-line OK summary; raises LockError otherwise."""
    import tomllib

    with open(pyproject, "rb") as fh:
        deps = tomllib.load(fh)["project"]["dependencies"]
    lock_text = lock.read_text(encoding="utf-8")
    # coverage applies to pins whether or not hashes are present yet
    pins = parse_pins_text(_strip_hash_suffixes(lock_text))
    pinned = {name for name, _ in pins}
    missing = []
    for dep in deps:
        name = norm_name(re.split(r"[<>=!~\[; ]", dep.strip(), 1)[0])
        if name not in pinned:
            missing.append(name)
    if missing:
        raise LockError(
            "stale constraints — runtime deps without an exact pin in "
            f"{lock}: {', '.join(missing)}\n"
            "       regenerate with: packaging/deb/update-constraints.sh")
    return (f"constraints OK: all {len(deps)} runtime deps exactly "
            f"pinned ({len(pins)} pins total)")


def _strip_hash_suffixes(text: str) -> str:
    """Reduce hash-locked lines to bare pins so parse_pins_text applies."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and " --hash=" in line:
            line = line.split(" --hash=")[0]
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# fixture-wheel helper used by the offline tests (tests/test_deb_lock.py)
# ---------------------------------------------------------------------------

def make_minimal_wheel(directory: Path, name: str, version: str,
                       *, extra_file: str = "hello.py",
                       extra_content: bytes = b"x = 1\n") -> Path:
    """Hand-build a structurally valid wheel (zipfile + dist-info) that a
    real offline ``pip install --require-hashes`` accepts. Stdlib only.
    The FILENAME and dist-info dir use the spec's name escaping (runs of
    non-alphanumerics -> ``_``); the METADATA Name keeps the real name —
    exactly how real PyPI wheels of hyphenated projects look."""
    escaped = re.sub(r"[^\w\d.]+", "_", name, flags=re.UNICODE)
    dist = f"{escaped}-{version}"
    root = escaped
    files: dict[str, bytes] = {
        f"{root}/__init__.py": b"",
        f"{root}/{extra_file}": extra_content,
        f"{dist}.dist-info/METADATA":
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            .encode(),
        f"{dist}.dist-info/WHEEL":
            b"Wheel-Version: 1.0\nGenerator: locklib-test\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record_rows = []
    for rel, data in files.items():
        digest = base64.urlsafe_b64encode(
            hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record_rows.append(f"{rel},sha256={digest},{len(data)}")
    record_rows.append(f"{dist}.dist-info/RECORD,,")
    files[f"{dist}.dist-info/RECORD"] = "\r\n".join(record_rows).encode()

    path = directory / f"{dist}-py3-none-any.whl"
    with open(path, "wb") as fh:
        # deterministic-ish timestamps; pip does not care
        import zipfile
        with zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in sorted(files):
                info = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))
                info.external_attr = 0o644 << 16
                zf.writestr(info, files[rel])
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="locklib.py",
                                     description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="validate a lock file's structure")
    v.add_argument("lock", type=Path)
    v.add_argument("--require-hashes", action="store_true",
                   help="the lock must be fully hash-locked (release)")

    p = sub.add_parser("pins-coverage",
                       help="every pyproject runtime dep is exactly pinned")
    p.add_argument("--pyproject", type=Path, required=True)
    p.add_argument("--lock", type=Path, required=True)

    w = sub.add_parser("write",
                       help="generate a hash-locked file from downloaded "
                            "wheels (used by update-constraints.sh)")
    w.add_argument("--wheels-dir", type=Path, required=True)
    w.add_argument("--pins", type=Path, required=True,
                   help="file of exact name==version pins, one per line")
    w.add_argument("--header", type=Path, required=True,
                   help="lock header comment block (rendered verbatim)")
    w.add_argument("--out", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "validate":
            validate_lock_text(Path(args.lock).read_text(encoding="utf-8"),
                               require_hashes=args.require_hashes)
            print(f"lock OK: {args.lock} "
                  f"({'hash-locked' if args.require_hashes else 'structure'})")
        elif args.cmd == "pins-coverage":
            print(check_pins_coverage(args.pyproject, args.lock))
        elif args.cmd == "write":
            text = build_lock_text(
                args.wheels_dir,
                Path(args.pins).read_text(encoding="utf-8"),
                Path(args.header).read_text(encoding="utf-8"))
            atomic_write(args.out, text)
            print(f"wrote {args.out} (pinned + hashed, self-validated)")
    except LockError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
