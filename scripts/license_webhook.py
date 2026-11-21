#!/usr/bin/env python3
"""Paddle webhook receiver: purchase -> offline license key (ADR-0005, #16).

Paddle Billing's built-in license keys are deprecated; self-issuing on
the `transaction.completed` webhook is the documented path. This small
stdlib-only endpoint (deploy behind your TLS reverse proxy) does exactly
that:

    Paddle --POST transaction.completed--> here
        1. verify Paddle-Signature (ts;h1 HMAC-SHA256 of "ts:body")
           and reject stale timestamps (replay)
        2. extract buyer email/name + transaction id from the event
        3. sign an offline Ed25519 key with the PRIVATE seed from env
           (the seed itself NEVER lives in this repo — see
           scripts/license_keygen.py for custody)
        4. append one record to the append-only issue log
           (issued-keys.jsonl, flock + fsync, ADR-0002 pattern);
           event_id makes Paddle redeliveries idempotent
        5. optionally pipe a delivery email to $LICENSE_EMAIL_COMMAND
           (e.g. an msmtp/sendmail wrapper); failures never fail the
           webhook — the key is already logged and Paddle shows the
           200 body in its delivery log

The buyer's app then verifies the key fully OFFLINE against the public
keys embedded in fluidvoice/license.py — no validation API, no phoning
home (Polar's License Keys API is online-only, which is why the Ed25519
key stays the in-app truth even if the store is Polar — see
docs/dev/license-ops.md).

Environment:

    PADDLE_WEBHOOK_SECRET    (required) secret from Paddle's webhook page
    LICENSE_SIGNING_SECRET   (required) 64-hex Ed25519 seed (offline copy!)
    LICENSE_ISSUE_LOG        (default issued-keys.jsonl) append-only log
    LICENSE_PRODUCT_IDS      (optional) comma list of YOUR Paddle
                             product_id values; events for other products
                             are acknowledged and ignored
    LICENSE_DEFAULT_COVERAGE (default forever) coverage for new purchases,
                             overridable per price via its custom_data
                             {"license": "1"|"forever"}
    LICENSE_EMAIL_COMMAND    (optional) shell command fed the delivery
                             message on stdin
    LICENSE_MAX_AGE_SECONDS  (default 300) webhook timestamp freshness

Usage:

    python3 scripts/license_webhook.py --host 127.0.0.1 --port 8787
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:  # package-style import (tests run from the repo root)
    from scripts import license_sign
except ImportError:  # direct execution: python3 scripts/license_webhook.py
    import license_sign

MAX_AGE_DEFAULT = 300.0
_FUTURE_SKEW = 60.0  # tolerate small clock skew into the future


class SignatureError(ValueError):
    """The webhook is not from Paddle, or is a stale replay."""


class EventError(ValueError):
    """A genuine webhook we cannot turn into a key."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Issuance:
    email: str
    name: str | None
    purchased: dt.date
    coverage: str
    paddle_txn: str


@dataclass
class Context:
    webhook_secret: bytes
    signing_seed: bytes
    log_path: Path
    product_ids: frozenset[str] = frozenset()
    default_coverage: str = license_sign.FOREVER
    email_command: str | None = None
    max_age: float = MAX_AGE_DEFAULT
    # test/diagnostic seam: what to use as "now"
    clock: callable = time.time  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Paddle signature (https://developer.paddle.com/webhooks/verify)
# ---------------------------------------------------------------------------

def verify_signature(secret: bytes, signature_header: str, body: bytes,
                     *, now: float, max_age: float) -> None:
    """Raises SignatureError unless h1 = HMAC-SHA256(secret, ts ':' body)
    and the timestamp is fresh (Paddle redelivers; stale redelivery of an
    already-processed event is a replay and gets no key)."""
    ts: int | None = None
    h1: str | None = None
    for part in signature_header.split(";"):
        key, sep, value = part.strip().partition("=")
        if key == "ts" and sep:
            try:
                ts = int(value)
            except ValueError as e:
                raise SignatureError("bad ts in signature header") from e
        elif key == "h1" and sep:
            h1 = value
    if ts is None or h1 is None:
        raise SignatureError("signature header missing ts= or h1=")
    if now - ts > max_age:
        raise SignatureError(f"stale webhook timestamp ({now - ts:.0f}s "
                             f"old, max {max_age:.0f}s) — replay")
    if ts - now > _FUTURE_SKEW:
        raise SignatureError("webhook timestamp too far in the future")
    expected = hmac.new(secret, f"{ts}:".encode() + body,
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, h1.lower()):
        raise SignatureError("h1 HMAC mismatch")


# ---------------------------------------------------------------------------
# Event -> issuance
# ---------------------------------------------------------------------------

def _coverage_for(data: dict, default: str) -> str:
    """Per-price override: items[].price.custom_data.license ("1" digit
    string). Falls back to the endpoint default."""
    for item in data.get("items") or []:
        price = item.get("price") or {}
        custom = price.get("custom_data") or {}
        override = custom.get("license")
        if isinstance(override, str) and (override == license_sign.FOREVER
                                          or override.isdigit()):
            return override
    return default


def extract_issuance(event: dict, ctx: Context) -> Issuance:
    if not isinstance(event, dict):
        raise EventError("event is not an object")
    event_type = event.get("event_type")
    if event_type != "transaction.completed":
        # a legitimate Paddle event that is not a purchase: acknowledge
        # and ignore (non-2xx would make Paddle retry it forever)
        raise EventError(f"unsupported event_type {event_type!r}", 202)
    data = event.get("data")
    if not isinstance(data, dict):
        raise EventError("event has no data object")
    if ctx.product_ids:
        ids = {((i.get("price") or {}).get("product_id"))
               for i in data.get("items") or [] if isinstance(i, dict)}
        if not ids & ctx.product_ids:
            raise EventError("not one of our products", 202)
    customer = data.get("customer") or {}
    email = customer.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise EventError(
            "transaction.completed without a customer email — issue via "
            "license_keygen.py lookup/manual and fix the store setup")
    occurred = str(event.get("occurred_at") or "")[:10]
    try:
        purchased = dt.date.fromisoformat(occurred)
    except ValueError:
        purchased = dt.date.today()
    txn = data.get("id")
    if not isinstance(txn, str) or not txn:
        raise EventError("transaction has no id")
    name = customer.get("name")
    return Issuance(email=email, name=name if isinstance(name, str) else None,
                    purchased=purchased,
                    coverage=_coverage_for(data, ctx.default_coverage),
                    paddle_txn=txn)


# ---------------------------------------------------------------------------
# Append-only issue log (ADR-0002 pattern: flock sidecar, fsync, append)
# ---------------------------------------------------------------------------

@dataclass
class IssueLog:
    path: Path
    lock_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def find(self, event_id: str) -> dict | None:
        """The record for a previous delivery of this event, if any."""
        if not self.path.exists():
            return None
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue  # torn tail from a crash: not our event
                if record.get("event_id") == event_id:
                    return record
        return None

    def append(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, separators=(",", ":"),
                          ensure_ascii=False) + "\n"
        with self.lock_path.open("a+b") as lock, \
                self.path.open("a", encoding="utf-8") as log:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                log.write(line)
                log.flush()
                os.fsync(log.fileno())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Orchestration (pure apart from the log + email; the HTTP layer wraps it)
# ---------------------------------------------------------------------------

def _deliver_email(ctx: Context, record: dict) -> None:
    if not ctx.email_command:
        return
    message = (
        f"Subject: Your SayItErmano license key\n"
        f"To: {record['email']}\n\n"
        f"Thank you for buying SayItErmano!\n\n"
        f"Import your license key with:\n\n"
        f"    sayit-ermano license <key>\n\n"
        f"Your key (also on one line below):\n\n"
        f"{record['key']}\n"
    )
    try:
        result = subprocess.run(  # noqa: S603 — owner-configured command
            ctx.email_command, shell=True, input=message.encode(),
            capture_output=True, timeout=60)
        if result.returncode != 0:
            print(f"email command failed "
                  f"({result.returncode}): {result.stderr.decode()[:200]}",
                  file=sys.stderr)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"email delivery failed: {e}", file=sys.stderr)


def process(body: bytes, signature_header: str, ctx: Context) \
        -> tuple[int, dict]:
    """Handle one webhook delivery. Returns (http_status, json_body)."""
    try:
        verify_signature(ctx.webhook_secret, signature_header, body,
                         now=ctx.clock(), max_age=ctx.max_age)
    except SignatureError as e:
        return 401, {"ok": False, "error": str(e)}
    try:
        event = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return 400, {"ok": False, "error": f"body is not JSON: {e}"}

    event_id = event.get("event_id") if isinstance(event, dict) else None
    if not isinstance(event_id, str) or not event_id:
        return 400, {"ok": False, "error": "event has no event_id"}

    try:
        issuance = extract_issuance(event, ctx)
    except EventError as e:
        # 202/Acknowledge-but-ignore and 4xx both stop Paddle's retries;
        # only the email case is worth a human looking at.
        status = getattr(e, "status", 422)
        if status == 202:
            return 200, {"ok": True, "ignored": str(e)}
        return status, {"ok": False, "error": str(e)}

    log = IssueLog(ctx.log_path)
    prior = log.find(event_id)
    if prior is not None:
        # Paddle redelivery: idempotent — same key, no second log line.
        return 200, {"ok": True, "duplicate": True, "key": prior["key"]}

    key = license_sign.make_key(issuance.email, ctx.signing_seed,
                                issuance.purchased, issuance.coverage,
                                name=issuance.name)
    record = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds"),
        "event_id": event_id,
        "paddle_txn": issuance.paddle_txn,
        "email": issuance.email,
        "name": issuance.name,
        "purchased": issuance.purchased.isoformat(),
        "coverage": issuance.coverage,
        "key": key,
    }
    log.append(record)
    _deliver_email(ctx, record)
    return 200, {"ok": True, "duplicate": False, "key": key}


# ---------------------------------------------------------------------------
# HTTP layer (stdlib; TLS terminates at the owner's reverse proxy)
# ---------------------------------------------------------------------------

def make_handler(ctx: Context) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 — stdlib naming
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            status, payload = process(body,
                                      self.headers.get("Paddle-Signature",
                                                       ""), ctx)
            blob = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def do_GET(self) -> None:  # noqa: N802 — stdlib naming
            blob = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def log_message(self, fmt: str, *args) -> None:
            print(f"{self.address_string()} {fmt % args}", file=sys.stderr)

    return Handler


def context_from_env() -> Context:
    secret = os.environ.get("PADDLE_WEBHOOK_SECRET")
    seed_hex = os.environ.get("LICENSE_SIGNING_SECRET", "").strip()
    if not secret:
        raise SystemExit("error: PADDLE_WEBHOOK_SECRET is required")
    if not seed_hex:
        raise SystemExit("error: LICENSE_SIGNING_SECRET is required "
                         "(64-hex seed — keep the copy offline)")
    try:
        seed = bytes.fromhex(seed_hex)
    except ValueError as e:
        raise SystemExit(f"error: LICENSE_SIGNING_SECRET is not hex: {e}")
    product_ids = frozenset(
        p.strip() for p in os.environ.get("LICENSE_PRODUCT_IDS",
                                          "").split(",") if p.strip())
    return Context(
        webhook_secret=secret.encode(),
        signing_seed=seed,
        log_path=Path(os.environ.get("LICENSE_ISSUE_LOG",
                                     "issued-keys.jsonl")),
        product_ids=product_ids,
        default_coverage=os.environ.get("LICENSE_DEFAULT_COVERAGE",
                                        license_sign.FOREVER),
        email_command=os.environ.get("LICENSE_EMAIL_COMMAND"),
        max_age=float(os.environ.get("LICENSE_MAX_AGE_SECONDS",
                                     MAX_AGE_DEFAULT)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="license_webhook", description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default: %(default)s; keep "
                             "behind a TLS reverse proxy)")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    ctx = context_from_env()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(ctx))
    print(f"license webhook listening on {args.host}:{args.port}, "
          f"issue log {ctx.log_path}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
