"""Offline license-key keygen (issue #16, ADR-0005) — the OWNER tool.

The shipped app (fluidvoice/license.py) verifies only; the matching
signing side lives here so the private seed never needs to be anywhere
near the installed package. Everything is stdlib: the Ed25519 signer is
RFC 8032 §5.1.6 composed from the same point arithmetic the verifier
uses, pinned against the official RFC vectors by tests/test_license.py
(the sign side) — no `cryptography` dependency anywhere in the chain.

Custody rules (docs/dev/license-ops.md has the full runbook):

* `generate` writes the seed to an explicit path and REFUSES paths
  inside this repository — the seed belongs on offline media;
* the seed is a single line of 64 hex chars, mode 0600;
* `issue` prints the signed key on stdout (hints go to stderr, so the
  key is pipeable into mail/support tooling);
* `verify` re-checks a key with the public keys embedded in the app,
  exactly what a buyer's offline `sayit-ermano license` will do.

Usage:

    python scripts/license_keygen.py generate --private-key /media/k/sec
    python scripts/license_keygen.py issue --private-key /media/k/sec \
        --email buyer@example.com --name "Jane" --v 1
    python scripts/license_keygen.py verify <KEY>

The private-key path may also come from $SAYIT_LICENSE_PRIVATE_KEY.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fluidvoice import license as lic  # noqa: E402 (path bootstrap above)

PRIVATE_KEY_ENV = "SAYIT_LICENSE_PRIVATE_KEY"


# ---------------------------------------------------------------------------
# Ed25519 signing (RFC 8032 §5.1.5/§5.1.6), composed from the verifier's
# point arithmetic in fluidvoice/license.py — one implementation of the
# curve math, two directions.
# ---------------------------------------------------------------------------

def _secret_expand(seed: bytes) -> tuple[int, bytes]:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return a, h[32:]


def sign(seed: bytes, msg: bytes) -> tuple[bytes, bytes]:
    """RFC 8032 §5.1.6. Returns (signature, public_key), both 32/64 raw."""
    a, prefix = _secret_expand(seed)
    a_enc = lic._point_compress(lic._scalar_mult(a, lic._B))
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % lic._l
    r_enc = lic._point_compress(lic._scalar_mult(r, lic._B))
    h = int.from_bytes(hashlib.sha512(r_enc + a_enc + msg).digest(), "little") % lic._l
    s = (r + h * a) % lic._l
    return r_enc + s.to_bytes(32, "little"), a_enc


def public_key(seed: bytes) -> bytes:
    a, _ = _secret_expand(seed)
    return lic._point_compress(lic._scalar_mult(a, lic._B))


# ---------------------------------------------------------------------------
# Key building
# ---------------------------------------------------------------------------

def build_payload(email: str, purchased: dt.date, v: str,
                  name: str | None = None) -> dict:
    payload = {"product": lic.PRODUCT_ID, "email": email}
    if name:
        payload["name"] = name
    payload["purchased"] = purchased.isoformat()
    payload["v"] = v
    return payload


def issue_key(seed: bytes, payload: dict) -> str:
    """Sign `payload` into the app's payload.signature key format."""
    import base64
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    sig, _pk = sign(seed, payload_bytes)
    b = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return f"{b(payload_bytes)}.{b(sig)}"


# ---------------------------------------------------------------------------
# Seed-file handling
# ---------------------------------------------------------------------------

def load_seed(path: Path) -> bytes:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise SystemExit(f"error: cannot read private key {path}: {e}")
    if len(raw) != 64:
        raise SystemExit(f"error: {path}: expected 64 hex chars (32-byte "
                         "Ed25519 seed), "
                         f"found {len(raw)} chars")
    try:
        return bytes.fromhex(raw)
    except ValueError as e:
        raise SystemExit(f"error: {path}: not hex: {e}")


def _seed_path(args: argparse.Namespace) -> Path:
    raw = args.private_key or os.environ.get(PRIVATE_KEY_ENV)
    if not raw:
        raise SystemExit(f"error: no private key path: pass --private-key "
                         f"or set ${PRIVATE_KEY_ENV}")
    return Path(raw).expanduser()


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def _cmd_generate(args: argparse.Namespace) -> int:
    path = _seed_path(args)
    resolved = path.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise SystemExit("error: refusing to write the private seed inside "
                         f"this repository ({resolved}) — keep it on offline "
                         "media; see docs/dev/license-ops.md")
    if path.exists():
        raise SystemExit(f"error: {path} already exists — generate never "
                         "overwrites (rotation appends a NEW key instead)")
    seed = secrets.token_bytes(32)
    path.write_text(seed.hex() + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    print(f"wrote private seed to {path} (mode 0600)", file=sys.stderr)
    print(f"public key (hex): {public_key(seed).hex()}")
    print("append it to PUBLIC_KEYS in fluidvoice/license.py, ship a "
          "release, THEN start issuing with this seed", file=sys.stderr)
    return 0


def _cmd_issue(args: argparse.Namespace) -> int:
    seed = load_seed(_seed_path(args))
    if args.v != lic.FOREVER and not args.v.isdigit():
        raise SystemExit(f"error: --v must be '{lic.FOREVER}' or a major "
                         f"version digit string, got {args.v!r}")
    if lic.PUBLIC_KEYS and public_key(seed) not in lic.PUBLIC_KEYS:
        print("warning: this seed's public key is NOT embedded in "
              "fluidvoice/license.py PUBLIC_KEYS — the app will reject "
              "keys signed by it", file=sys.stderr)
    purchased = (dt.date.fromisoformat(args.purchased)
                 if args.purchased else dt.datetime.now(dt.timezone.utc).date())
    payload = build_payload(args.email, purchased, args.v, args.name)
    print(issue_key(seed, payload))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    try:
        info = lic.parse_and_verify(args.key)
    except lic.LicenseError as e:
        print(f"rejected: {e}", file=sys.stderr)
        return 1
    covered = "all releases" if info.max_version == lic.FOREVER \
        else f"major {info.max_version} and below"
    print(f"valid: {info.email} · purchased {info.purchased} · {covered} "
          f"· verified by embedded key #{info.key_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="license_keygen.py",
        description="Owner-side Ed25519 license keygen (private seed never "
                    "enters the repository)")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate",
                       help="create a keypair; writes the seed, prints the "
                            "public key hex to embed")
    g.add_argument("--private-key", metavar="PATH",
                   help=f"where to write the seed (or ${PRIVATE_KEY_ENV})")
    g.set_defaults(func=_cmd_generate)

    i = sub.add_parser("issue", help="sign one key and print it")
    i.add_argument("--private-key", metavar="PATH",
                   help=f"seed path (or ${PRIVATE_KEY_ENV})")
    i.add_argument("--email", required=True, help="buyer email (identity)")
    i.add_argument("--name", default=None, help="buyer name (optional)")
    i.add_argument("--purchased", metavar="YYYY-MM-DD", default=None,
                   help="purchase date (default: today UTC)")
    i.add_argument("--v", required=True,
                   help=f"covered major version ('1') or '{lic.FOREVER}'")
    i.set_defaults(func=_cmd_issue)

    v = sub.add_parser("verify",
                       help="verify a key against the app's embedded "
                            "public keys (what the buyer's app will do)")
    v.add_argument("key", help="the license key")
    v.set_defaults(func=_cmd_verify)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
