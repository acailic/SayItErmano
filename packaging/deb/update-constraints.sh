#!/usr/bin/env bash
# Regenerate packaging/deb/constraints.txt — the reviewed, hash-lockable
# dependency set of the Ubuntu 24.04 deb (P0.6 deb contract).
#
# The lock is derived from the LOCKED VENV (the tested environment: repo
# .venv, Ubuntu 24.04 / Python 3.12 / x86_64) by walking the runtime
# dependency closure of pyproject's [project].dependencies (core only —
# the deb installs no extras). build-deb.sh then consumes the file with
# `pip install -c constraints.txt .`, so nothing is resolved behind the
# reviewer's back at build time.
#
# Usage:
#   packaging/deb/update-constraints.sh [--pins-only] [venv-python]
#
#   --pins-only   emit exact `==` pins without --hash lines (no network
#                 needed; use for a first lock, hashes are added by a
#                 full run before a release)
#   venv-python   defaults to <repo>/.venv/bin/python
#
# A full run (default) additionally downloads the exact wheels the pinned
# Ubuntu 24.04 / py3.12 / x86_64 build WOULD fetch and appends
# `--hash=sha256:...` to every pin (pip verifies each artifact when the
# constraint carries hashes — needs pip >= 23.1 in the build venv;
# build-deb.sh upgrades pip first). Run it on the release branch and
# commit the result; the diff IS the review.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out="$here/constraints.txt"

PINS_ONLY=0
if [ "${1:-}" = "--pins-only" ]; then PINS_ONLY=1; shift; fi
VENV_PY="${1:-$repo/.venv/bin/python}"

[ -x "$VENV_PY" ] || { echo "ERROR: locked venv python not found: $VENV_PY" >&2; exit 1; }

# The lock is only meaningful for the deb's target interpreter: refuse to
# generate from anything else (the same guard build-deb.sh enforces).
"$VENV_PY" - <<'GUARD' || exit 1
import sys
v, mach = sys.version_info[:2], sys.platform_machine if hasattr(sys, "platform_machine") else __import__("platform").machine()
if v != (3, 12) or sys.platform != "linux" or mach not in ("x86_64", "amd64", "AMD64"):
    sys.exit(f"refusing: locked venv must be Linux/CPython 3.12/x86_64 (got {v} {sys.platform}/{mach})")
GUARD

echo ">> walking runtime dependency closure from $VENV_PY"
# 1. Resolve the closure (name==version pairs) from the locked venv.
pins="$("$VENV_PY" - "$repo" <<'PY'
import sys
from importlib.metadata import distribution

# pip._vendor.packaging: always present in the venv, no extra dependency.
from pip._vendor.packaging.markers import default_environment
from pip._vendor.packaging.requirements import Requirement

repo = sys.argv[1]
import tomllib
with open(f"{repo}/pyproject.toml", "rb") as fh:
    deps = tomllib.load(fh)["project"]["dependencies"]

env = default_environment()
env["extra"] = ""  # extras' markers evaluate False -> core closure only

def norm(name):
    return name.lower().replace("_", "-")

closure = {}

def walk(requirement):
    name = norm(requirement.name)
    if name in closure:
        return
    dist = distribution(name)  # PackageNotFoundError -> loud failure: the
    # locked venv must actually contain every runtime dependency.
    closure[name] = dist.version
    for spec in dist.requires or []:
        req = Requirement(spec)
        # env["extra"]="" mirrors pip installing without extras: pure
        # extra deps evaluate False, combined markers evaluate correctly.
        if req.marker is not None and not req.marker.evaluate(env):
            continue
        walk(req)

for dep in deps:
    walk(Requirement(dep))

for name in sorted(closure):
    print(f"{name}=={closure[name]}")
PY
)"

n="$(printf '%s\n' "$pins" | grep -c .)"
echo ">> $n packages pinned"

header() {
    cat <<EOF
# Locked dependency set for the SayItErmano deb — Ubuntu 24.04 / x86_64 /
# Python 3.12 ONLY (P0.6 deb contract). Generated $(date -u +%Y-%m-%d)
# from the locked venv by packaging/deb/update-constraints.sh. Hashing/
# validation live in packaging/deb/locklib.py (Q3); the file is consumed
# by packaging/build-deb.sh via \`pip install --require-hashes -r <this
# file>\` — every downloaded wheel is verified against these sha256.
#
# Review rules:
#   - every pin is exact (==); version bumps must arrive as a visible
#     diff of this file, never as silent resolution at build time;
#   - --hash lines are sha256 of the exact wheels the Ubuntu 24.04 /
#     py3.12 / x86_64 build downloads from PyPI; pin-only locks are
#     rejected by release builds (DEB_ALLOW_PIN_ONLY=1 is dev-only);
#   - regenerate with: packaging/deb/update-constraints.sh
#     (add --pins-only for a network-free pin refresh).
EOF
}

if [ "$PINS_ONLY" = "1" ]; then
    { header; echo; printf '%s\n' "$pins"; } > "$out"
    echo ">> wrote $out (pins only — run without --pins-only to add hashes)"
    exit 0
fi

# 2. Download the exact wheels the deb build would fetch and hash them.
#
# Hashing/matching/writing is packaging/deb/locklib.py (Q3): the old
# inline `pip hash | awk '/^sha256=/'` scraped a line format pip never
# printed (`--hash=sha256:`), matched nothing, and emitted malformed
# `name==version --` lines. locklib hashes the wheel bytes directly,
# validates the wheel set against the pins (missing/duplicate/mismatched
# wheels are errors), self-validates the generated text, and replaces
# the lock ATOMICALLY — any failure preserves the previous file.
work="$(mktemp -d "${TMPDIR:-/tmp}/deb-constraints.XXXXXX")"
trap 'rm -rf "$work"' EXIT
# shellcheck disable=SC2086  # pins is one name==ver per line, no spaces
"$VENV_PY" -m pip download -q --no-cache-dir --only-binary=:all: \
    -d "$work/wheels" $pins

printf '%s\n' "$pins" > "$work/pins.txt"
header > "$work/header.txt"
"$VENV_PY" "$here/locklib.py" write \
    --wheels-dir "$work/wheels" \
    --pins "$work/pins.txt" \
    --header "$work/header.txt" \
    --out "$out"

echo ">> wrote $out (pinned + hashed)"
