#!/usr/bin/env python3
"""Private license-key generator (ADR-0005, issue #16) — OWNER ONLY.

The signing seed NEVER enters this repository or any CI secret: it lives
on offline media in the owner's custody and is passed per invocation via
--secret-file, --secret (hex) or $LICENSE_KEYGEN_SECRET (hex), in that
order of precedence. Public halves are embedded in
fluidvoice/license.py PUBLIC_KEYS (rotation appends, never replaces).

Subcommands:

  keypair          generate a fresh seed + public key (first setup,
                   or rotation: APPEND the public hex to PUBLIC_KEYS)
  issue            sign one license key (prints the key on stdout)
  public-key       print the public-key hex for a seed in custody
  verify           check a key against the EMBEDDED app keys (what a
                   buyer's machine will do — fully offline)
  lookup           find issued keys by email in the webhook's
                   append-only issue log (support re-issue)

Examples:

  # first setup: keep the seed offline, embed the public hex
  python3 scripts/license_keygen.py keypair

  # issue a key for a purchase (purchased = today, covers all releases)
  python3 scripts/license_keygen.py issue --secret-file ~/offline/seed.hex \
      --email buyer@example.com --name "Ada Buyer" --version forever

  # support: what key did this buyer get?
  python3 scripts/license_keygen.py lookup --email buyer@example.com
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # package-style import (tests run from the repo root)
    from scripts import license_sign
except ImportError:  # direct execution: python3 scripts/license_keygen.py
    import license_sign

FLUIDVOICE_KEYGEN_SECRET = "LICENSE_KEYGEN_SECRET"


def _read_seed(args: argparse.Namespace) -> bytes:
    """--secret > --secret-file > $LICENSE_KEYGEN_SECRET (hex)."""
    raw: str | None = None
    source = ""
    if getattr(args, "secret", None):
        raw, source = args.secret, "--secret"
    elif getattr(args, "secret_file", None):
        source = f"--secret-file {args.secret_file}"
        try:
            raw = Path(args.secret_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            _die(f"cannot read secret file: {e}")
    else:
        raw = os.environ.get(FLUIDVOICE_KEYGEN_SECRET)
        source = f"${FLUIDVOICE_KEYGEN_SECRET}"
    if not raw:
        _die(f"no signing seed: pass --secret-file, --secret, or set "
             f"{FLUIDVOICE_KEYGEN_SECRET}")
    raw = raw.replace("\n", "").strip()
    try:
        seed = bytes.fromhex(raw)
    except ValueError as e:
        _die(f"{source} is not hex: {e}")
    if len(seed) != 32:
        _die(f"{source}: seed must be 32 bytes (64 hex chars), "
             f"got {len(seed)}")
    return seed


def _die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------

def _cmd_keypair(args: argparse.Namespace) -> int:
    seed = secrets.token_bytes(32)
    pub = license_sign.public_key(seed)
    seed_hex = seed.hex()
    print(f"private seed (hex): {seed_hex}")
    print(f"public key  (hex): {pub.hex()}")
    print()
    print("KEEP THE SEED OFFLINE (encrypted media, never this repo, never "
          "CI).\nIf this is a rotation: APPEND the public hex to "
          "fluidvoice/license.py\nPUBLIC_KEYS — never remove the old entry; "
          "key_id records which one verified.")
    if args.out:
        target = Path(args.out)
        target.write_text(seed_hex + "\n", encoding="utf-8")
        os.chmod(target, 0o600)
        print(f"seed written to {target} (mode 0600) — move it offline now")
    return 0


def _cmd_issue(args: argparse.Namespace) -> int:
    seed = _read_seed(args)
    purchased = (dt.date.fromisoformat(args.purchased)
                 if args.purchased else dt.date.today())
    key = license_sign.make_key(args.email, seed, purchased,
                                args.version, name=args.name)
    # stdout is the key alone (pipeable); the human context goes to stderr
    print(key)
    print(f"issued to {args.email} ({purchased}, coverage "
          f"{args.version!r}) — remember to log/deliver it "
          f"(scripts/license_webhook.py does this automatically for Paddle "
          f"purchases)", file=sys.stderr)
    return 0


def _cmd_public_key(args: argparse.Namespace) -> int:
    seed = _read_seed(args)
    print(license_sign.public_key(seed).hex())
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    from fluidvoice import license as app_license
    try:
        info = app_license.parse_and_verify(args.key)
    except app_license.LicenseError as e:
        print(f"rejected: {e}")
        return 1
    print(app_license.status_text(info))
    return 0


def _cmd_lookup(args: argparse.Namespace) -> int:
    log = Path(args.log)
    if not log.exists():
        print(f"no issue log at {log}")
        return 1
    needle = args.email.lower()
    found = 0
    with log.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                print(f"warning: skipping unparseable log line: "
                      f"{line[:60]!r}", file=sys.stderr)
                continue
            if needle in str(record.get("email", "")).lower():
                found += 1
                print(f"{record.get('email')}  purchased "
                      f"{record.get('purchased')}  coverage "
                      f"{record.get('coverage')}")
                print(f"  event {record.get('event_id')}  txn "
                      f"{record.get('paddle_txn')}")
                print(f"  key {record.get('key')}")
    if not found:
        print(f"no issued key for {args.email}")
        return 1
    return 0


# ---------------------------------------------------------------------------

def _add_secret_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--secret-file", metavar="PATH",
                   help="file holding the 32-byte seed as 64 hex chars "
                        "(offline media)")
    p.add_argument("--secret", metavar="HEX",
                   help="the seed as hex (prefer --secret-file so the "
                        "seed stays out of shell history)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="license_keygen", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("keypair", help="generate a fresh seed + public key")
    p.add_argument("--out", metavar="PATH",
                   help="also write the seed hex to PATH (mode 0600)")
    p.set_defaults(func=_cmd_keypair)

    p = sub.add_parser("issue", help="sign one license key")
    _add_secret_args(p)
    p.add_argument("--email", required=True, help="buyer email")
    p.add_argument("--name", default=None, help="buyer name (optional)")
    p.add_argument("--purchased", default=None, metavar="YYYY-MM-DD",
                   help="purchase date (default: today)")
    p.add_argument("--version", default=license_sign.FOREVER,
                   metavar="N|forever",
                   help="covered major version digit or 'forever' "
                        "(default: %(default)s)")
    p.set_defaults(func=_cmd_issue)

    p = sub.add_parser("public-key",
                       help="print the public hex for a seed in custody")
    _add_secret_args(p)
    p.set_defaults(func=_cmd_public_key)

    p = sub.add_parser("verify",
                       help="verify a key against the embedded app keys")
    p.add_argument("key", help="the license key")
    p.set_defaults(func=_cmd_verify)

    p = sub.add_parser("lookup",
                       help="find issued keys by email in the issue log")
    p.add_argument("--email", required=True)
    p.add_argument("--log", default="issued-keys.jsonl", metavar="PATH",
                   help="issue log from the webhook receiver "
                        "(default: %(default)s)")
    p.set_defaults(func=_cmd_lookup)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
