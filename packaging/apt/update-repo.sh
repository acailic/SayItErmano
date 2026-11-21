#!/usr/bin/env bash
# Publish SayItErmano .deb(s) into the signed apt repo that lives in the
# tree at site/apt and is served by GitHub Pages:
#
#     https://acailic.github.io/SayItErmano/apt  (suite `stable`)
#
# Called by release-publish after the GitHub release exists (manual-only
# flow - no automation ever runs this on its own), and runnable locally
# for testing the channel:
#
#     bash packaging/apt/update-repo.sh site/apt dist/sayit-ermano_x.y.z-1_amd64.deb
#
# Signing key (see packaging/apt/README.md):
#   APT_GPG_KEY     armored PRIVATE key on stdin-able env (CI: the
#                   APT_REPO_GPG_KEY secret)
#   APT_GPG_HOME    alternatively, a pre-seeded gpg home with the secret
#
# Single-version policy: the repo holds ONLY the latest release
# (any older sayit-ermano version is dropped first). Upgrades from any
# installed n to the newest n+1 work; downgrades are not served. This
# bounds the repo - and thus the GitHub Pages site - to ~one deb
# (Pages caps sites at 1 GB / 100 MB per file).
#
# After publishing, the script verifies the repo exactly like a client:
# a sandboxed `apt-get update` (signature checked against the committed
# public key), `apt-cache policy` candidate == the deb's Version, and an
# `apt-get install --download-only` (hash check of the pool file).
set -euo pipefail

REPO_DIR="${1:-}"; shift || true
[ -n "$REPO_DIR" ] && [ $# -ge 1 ] || {
    echo "usage: update-repo.sh <repo-dir> <deb> [deb...]" >&2
    exit 2
}

die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

deb_versions() {  # one Version line per given .deb
    local d
    for d in "$@"; do dpkg-deb -f "$d" Version; done
}

for tool in reprepro gpg apt-get apt-cache dpkg-deb; do
    command -v "$tool" >/dev/null 2>&1 \
        || die "$tool is required (sudo apt install reprepro gnupg)"
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"     # packaging/apt
KEYFILE="$SCRIPT_DIR/sayit-ermano.asc"
CONFDIR="$SCRIPT_DIR/conf"
[ -f "$KEYFILE" ] || die "public key $KEYFILE missing (provision it: packaging/apt/README.md)"
[ -f "$CONFDIR/distributions" ] || die "missing $CONFDIR/distributions"
for deb in "$@"; do
    [ -f "$deb" ] || die "no such deb: $deb"
    [ "$(dpkg-deb -f "$deb" Package)" = "sayit-ermano" ] \
        || die "$(basename "$deb") is not a sayit-ermano package"
done

# --- 1. the signing key ------------------------------------------------------
CLEANUP=""
if [ -n "${APT_GPG_HOME:-}" ]; then
    export GNUPGHOME="$APT_GPG_HOME"
else
    if [ -z "${APT_GPG_KEY:-}" ]; then
        die "no signing key: set APT_GPG_KEY (armored private key) or APT_GPG_HOME (packaging/apt/README.md)"
    fi
    export GNUPGHOME="$(mktemp -d)"
    CLEANUP="$GNUPGHOME"
    chmod 700 "$GNUPGHOME"
    printf '%s' "$APT_GPG_KEY" | gpg --batch --import
fi
gpg --batch --list-secret-keys --with-colons 2>/dev/null | grep -q '^sec' \
    || die "no secret key in GNUPGHOME - the repo cannot be signed"

# --- 2. publish into the repo tree -------------------------------------------
mkdir -p "$REPO_DIR"
# single-version policy: drop every earlier sayit-ermano version first
reprepro --confdir "$CONFDIR" -b "$REPO_DIR" remove stable sayit-ermano \
    || [ ! -d "$REPO_DIR/pool" ]   # first publish: nothing to remove yet
for deb in "$@"; do
    reprepro --confdir "$CONFDIR" -b "$REPO_DIR" includedeb stable "$deb"
done
# the client-fetchable public key travels with the repo
install -m644 "$KEYFILE" "$REPO_DIR/key.asc"

# exactly the versions we just published - nothing older lingers
pool_versions() {
    local f
    for f in "$REPO_DIR"/pool/main/s/sayit-ermano/*.deb; do
        [ -e "$f" ] || continue
        dpkg-deb -f "$f" Version
    done | sort -u
}
WANT="$(deb_versions "$@" | sort -u)"
GOT="$(pool_versions)"
[ "$WANT" = "$GOT" ] || die "pool versions ($GOT) != published versions ($WANT) - single-version policy violated"

# --- 3. verify like a client (sandboxed apt against a file:// copy) ----------
VERIFY_ROOT="$(mktemp -d)"
CLEANUP="$CLEANUP $VERIFY_ROOT"
cleanup() { [ -z "$CLEANUP" ] || rm -rf $CLEANUP; }
trap cleanup EXIT
mkdir -p "$VERIFY_ROOT/etc/apt/keyrings" \
         "$VERIFY_ROOT/etc/apt/sources.list.d" \
         "$VERIFY_ROOT/etc/apt/preferences.d" \
         "$VERIFY_ROOT/etc/apt/apt.conf.d" \
         "$VERIFY_ROOT/var/lib/apt/lists/partial" \
         "$VERIFY_ROOT/var/lib/dpkg" \
         "$VERIFY_ROOT/var/cache/apt/archives/partial"
touch "$VERIFY_ROOT/var/lib/dpkg/status"
gpg --batch --yes --dearmor --output \
    "$VERIFY_ROOT/etc/apt/keyrings/sayit-ermano.gpg" "$KEYFILE"
REPO_ABS="$(cd "$REPO_DIR" && pwd)"
# NB: signed-by must carry the ABSOLUTE sandbox path - a /etc/apt/... path
# would be read from the real system, not from Dir=$VERIFY_ROOT
printf 'deb [signed-by=%s/etc/apt/keyrings/sayit-ermano.gpg] file:%s stable main\n' \
    "$VERIFY_ROOT" "$REPO_ABS" > "$VERIFY_ROOT/etc/apt/sources.list.d/sayit-ermano.list"
# rootless sandbox: everything apt touches lives under VERIFY_ROOT
echo 'APT::Sandbox::User "root";' > "$VERIFY_ROOT/etc/apt/apt.conf.d/99sandbox"
APT_OPTS=(-o Dir="$VERIFY_ROOT" -o Dir::State="$VERIFY_ROOT/var/lib/apt"
          -o Dir::Cache="$VERIFY_ROOT/var/cache/apt"
          -o Dir::Cache::archives="$VERIFY_ROOT/var/cache/apt/archives"
          -o Dir::Etc="$VERIFY_ROOT/etc/apt"
          -o Dir::State::status="$VERIFY_ROOT/var/lib/dpkg/status"
          -o Acquire::Languages=none)

say "verifying as a client: apt-get update (signature check)..."
# (rootless runs may print a harmless "rm: cannot remove
#  /var/cache/apt/archives/partial/*.deb" from apt's file method - it
#  touches the compile-time default dir, not this sandbox; exit 0)
apt-get "${APT_OPTS[@]}" update

CAND="$(apt-cache "${APT_OPTS[@]}" policy sayit-ermano | sed -n 's/^  Candidate: //p')"
[ -n "$CAND" ] || die "sandboxed apt sees no candidate - the repo is broken"
NEWEST="$(printf '%s\n' "$WANT" | sort -V | tail -1)"
[ "$CAND" = "$NEWEST" ] || die "apt candidate $CAND != published $NEWEST"

say "downloading through apt (hash verification)..."
apt-get "${APT_OPTS[@]}" install --download-only -y sayit-ermano >/dev/null

say "OK: repo serves sayit-ermano $CAND with a valid signature and hashes"
