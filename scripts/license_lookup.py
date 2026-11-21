"""Support lookup over the issued-keys log (issue #16).

The webhook (scripts/license_webhook.py) appends one JSON line per
issued key; this tool finds them again — by buyer email (substring,
case-insensitive), by Paddle transaction id, or by event id — and
prints the key plus the entitlement, ready to re-send to the buyer.

    python scripts/license_lookup.py buyer@example.com --log issued.jsonl
    python scripts/license_lookup.py txn_01hwqz... --log issued.jsonl

Exit code 1 when nothing matches (scriptable in support runbooks).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_LOG_ENV = "LICENSE_ISSUE_LOG"


def lookup_entries(log_path: Path, query: str) -> list[dict]:
    """Matching entries, oldest first: case-insensitive substring on the
    email, exact match on transaction/event id. Unparseable lines are
    skipped (a torn tail from a crash never blocks a lookup)."""
    q = query.strip().lower()
    hits: list[dict] = []
    if not log_path.exists():
        return hits
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            email = str(entry.get("email") or "").lower()
            if (q in email
                    or entry.get("transaction_id") == query.strip()
                    or entry.get("event_id") == query.strip()):
                hits.append(entry)
    return hits


def format_entry(entry: dict) -> str:
    v = entry.get("v")
    covered = "all releases" if v == "forever" else f"major {v} and below"
    name = f" ({entry['name']})" if entry.get("name") else ""
    return (f"{entry.get('email')}{name}\n"
            f"  purchased {entry.get('purchased')} · covers {covered} · "
            f"txn {entry.get('transaction_id')}\n"
            f"  issued {entry.get('ts')}\n"
            f"  key: {entry.get('key')}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="license_lookup.py",
        description="Look up issued license keys (support: lost keys, "
                    "re-sends, entitlement checks)")
    p.add_argument("query",
                   help="buyer email (substring match) or transaction/"
                        "event id (exact match)")
    p.add_argument("--log", metavar="PATH",
                   default=os.environ.get(DEFAULT_LOG_ENV),
                   help=f"issued-keys JSONL (or ${DEFAULT_LOG_ENV})")
    args = p.parse_args(argv)
    if not args.log:
        raise SystemExit(f"error: no log path: pass --log or set "
                         f"${DEFAULT_LOG_ENV}")
    hits = lookup_entries(Path(args.log), args.query)
    if not hits:
        print(f"no issued key matches {args.query!r}", file=sys.stderr)
        return 1
    for entry in hits:
        print(format_entry(entry))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
