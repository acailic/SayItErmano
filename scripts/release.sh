#!/usr/bin/env bash
# Release helper - encodes the house recipe (release-distribution memory +
# v0.7.0 run). Interactive by design: every destructive step confirms.
#
#   bash scripts/release.sh 0.8.0
#
# Steps: version consistency -> clean dist -> full suite -> deb build ->
# integration deb gate -> bump commit+push -> tag + gh release (notes file
# you provide) -> machine refresh (one-shot) -> post-refresh checks.
set -euo pipefail

VERSION="${1:?usage: bash scripts/release.sh X.Y.Z}"
REPO="acailic/SayItErmano"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VENV=".venv/bin/python"
DEB="dist/sayit-ermano_${VERSION}-1_amd64.deb"

confirm() { read -r -p "==> $1 [y/N] " a; [ "$a" = "y" ] || exit 1; }

echo "==> 1/9 version sanity"
CUR="$(grep -oP '(?<=__version__ = ")[^"]+' fluidvoice/__init__.py)"
[ "$CUR" != "$VERSION" ] || { echo "fluidvoice/__init__.py already $VERSION"; }
confirm "bump BOTH fluidvoice/__init__.py and pyproject.toml to $VERSION now (I'll wait while you edit or say n to abort)"

echo "==> 2/9 clean dist/ (stale-deb trap)"
rm -f dist/*.deb dist/*.build dist/*.changes

echo "==> 3/9 full offline suite"
$VENV -m pytest -q tests --ignore=tests/integration

echo "==> 4/9 build deb"
./packaging/build-deb.sh
[ -f "$DEB" ] || { echo "missing $DEB"; exit 1; }

echo "==> 5/9 integration deb gate"
$VENV -m pytest -q tests/integration/test_installation.py

echo "==> 6/9 bump commit + push"
git add fluidvoice/__init__.py pyproject.toml
git commit -m "release: v${VERSION}"
git push origin linux

echo "==> 7/9 tag + GitHub release"
NOTES="/tmp/release-notes-${VERSION}.md"
[ -f "$NOTES" ] || { echo "write $NOTES first (house style: gh release view v0.6.0)"; exit 1; }
confirm "create the public release v${VERSION} with $DEB?"
gh release create "v${VERSION}" "$DEB" -R "$REPO" --target linux \
    --title "v${VERSION} — $(head -1 "$NOTES" | sed 's/^#* *//')" \
    --notes-file "$NOTES"

echo "==> 8/9 machine refresh"
confirm "upgrade THIS machine's user install (one-shot) now?"
bash scripts/install-one-shot.sh

echo "==> 9/9 post-refresh checks"
systemctl --user is-active sayit-ermano
"$HOME/.local/bin/sayit-ermano" status --json | grep -o "\"version\": \"${VERSION}\"" \
    || echo "WARN: daemon version mismatch"
# single-path startup: the installer must NOT have re-created autostart
# when the unit is enabled (fixed in the installer, verify anyway)
if systemctl --user is-enabled sayit-ermano >/dev/null 2>&1 \
        && [ -e "$HOME/.config/autostart/sayit-ermano.desktop" ]; then
    echo "WARN: autostart re-created alongside the enabled unit - removing"
    rm -f "$HOME/.config/autostart/sayit-ermano.desktop"
fi
echo "done: v${VERSION} released and this machine is on it."
echo "manual follow-ups: AUR recipe bump (packaging/aur), Reddit/HN post."
