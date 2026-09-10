#!/usr/bin/env bash
# Publish (or update) sayit-ermano on the AUR — one command.
#
# Prereqs (once, human-owned):
#   1. An AUR account: https://aur.archlinux.org/register/
#   2. An SSH key registered under My Account -> SSH Keys, usable as
#      ssh aur@aur.archlinux.org (add a Host alias in ~/.ssh/config if
#      the key is not the default identity).
# Then, from the repo root:  packaging/aur/publish.sh
#
# The script pushes PKGBUILD + .SRCINFO to ssh://aur@aur.archlinux.org/
# sayit-ermano.git (the NATIVE source package; the old -bin recipe was
# never published — see packaging/aur/README.md for the rename note).
# Nothing else is required — AUR renders the page from these two files.
# Verify at https://aur.archlinux.org/packages/sayit-ermano
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
name="sayit-ermano"
work="$(mktemp -d "${TMPDIR:-/tmp}/aur-${name}.XXXXXX")"
trap 'rm -rf "$work"' EXIT

echo ">> sanity: ssh access to the AUR"
ssh -o BatchMode=yes aur@aur.archlinux.org help >/dev/null

echo ">> cloning aur:${name}"
git clone -q "ssh://aur@aur.archlinux.org/${name}.git" "$work/$name"

cp "$here/PKGBUILD" "$here/.SRCINFO" "$work/$name/"
cd "$work/$name"

if git diff --quiet; then
    echo ">> AUR already up to date (pkgver $(awk '/^pkgver=/{print $2}' PKGBUILD))"
    exit 0
fi
if grep -q "^sha256sums=('SKIP')$" PKGBUILD; then
    echo ">> refusing: sha256sums still 'SKIP' — fill the real digest first" >&2
    exit 1
fi
pkgver="$(awk '/^pkgver=/{print $2}' PKGBUILD)"
git add PKGBUILD .SRCINFO
git commit -q -m "sayit-ermano ${pkgver}"
git push origin master

echo ">> pushed ${pkgver}: https://aur.archlinux.org/packages/${name}"
echo ">> first push of a new package can take a minute to appear."
