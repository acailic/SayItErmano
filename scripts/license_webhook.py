"""Paddle webhook receiver -> offline license key (issue #16, ADR-0005).

Paddle Billing deprecated its built-in license-key feature; the
documented path is to listen for `transaction.completed` and self-issue.
This is that listener: a small stdlib-only HTTPS endpoint the owner
deploys (reverse proxy or --tls-cert), which

1. verifies the `Paddle-Signature` header (ts + HMAC-SHA256 over
   ``{ts}:{body}`` with the webhook secret) and rejects stale
   timestamps, so replays and forgeries never mint keys;
2. extracts buyer email/name, the Paddle product id, and the purchase
   date from the transaction;
3. maps product id -> covered major version (or forever) and issues an
   Ed25519 key with scripts/license_keygen.py's signer;
4. appends one line to an append-only JSONL issue log — flock sidecar +
   fsync, the ADR-0002 transaction pattern — which is the support
   lookup record (scripts/license_lookup.py) and the dedup source:
   a redelivered event returns the SAME key, never a second one.

Runbook incl. sandbox test and the Polar runner-up wiring:
docs/dev/license-ops.md.

    python scripts/license_webhook.py --host 127.0.0.1 --port 8080 \
        --issue-log /var/lib/sayit-license/issued.jsonl
        # secret: $PADDLE_WEBHOOK_SECRET or --secret
        # seed:   $SAYIT_LICENSE_PRIVATE_KEY or --private-key
        # map:    $LICENSE_PRODUCT_MAP or --product-map pro_xxx=1,pro_yyy=forever
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import ssl
import sys
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # license_keygen sibling
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # fluidvoice/

import license_keygen  # noqa: E402 (path bootstrap above)

from fluidvoice import license as lic  # noqa: E402 (path bootstrap above)

log = logging.getLogger("license-webhook")

EVENT_TYPE = "transaction.completed"
DEFAULT_TOLERANCE = 300  # seconds of clock skew / delivery lag accepted

SECRET_ENV = "PADDLE_WEBHOOK_SECRET"
PRODUCT_MAP_ENV = "LICENSE_PRODUCT_MAP"
ISSUE_LOG_ENV = "LICENSE_ISSUE_LOG"
PRIVATE_KEY_ENV = license_keygen.PRIVATE_KEY_ENV


# ---------------------------------------------------------------------------
# Paddle signature verification
# ---------------------------------------------------------------------------

def verify_paddle_signature(header: str, body: bytes, secret: str, *,
                            now: float, tolerance: int = DEFAULT_TOLERANCE
                            ) -> bool:
    """Paddle Billing: `Paddle-Signature: ts=<unix>,h1=<hmac-sha256-hex>`
    where the MAC is over ``{ts}:{body}``. `h2` carries the rotated
    secret during key changes; any configured tag matching accepts."""
    tags: dict[str, str] = {}
    try:
        for part in re.split(r"[;,]", header):
            k, _, v = part.partition("=")
            tags[k.strip()] = v.strip()
        ts = int(tags["ts"])
    except (ValueError, KeyError):
        return False
    if abs(now - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}:".encode() + body,
                        hashlib.sha256).hexdigest()
    return any(k in tags and hmac.compare_digest(tags[k], expected)
               for k in ("h1", "h2"))


# ---------------------------------------------------------------------------
# Event extraction
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Purchase:
    event_id: str
    transaction_id: str
    email: str
    name: str | None
    product_ids: tuple[str, ...]
    purchased: dt.date


def extract_purchase(event: dict) -> Purchase:
    """Pull the issuance-relevant fields out of a transaction.completed
    event. Raises ValueError with a support-facing reason when the
    payload cannot drive issuance."""
    data = event.get("data") or {}
    event_id = (event.get("event_id") or event.get("notification_id")
                or data.get("id"))
    if not event_id:
        raise ValueError("event has no id (event_id/notification_id)")
    if data.get("status") not in (None, "completed"):
        raise ValueError(f"transaction status is {data.get('status')!r}")
    customer = data.get("customer") or {}
    email = customer.get("email")
    if not isinstance(email, str) or not email:
        raise ValueError("transaction has no customer email")
    product_ids: list[str] = []
    for item in data.get("items") or []:
        pid = (item.get("price") or {}).get("product_id") \
            or (item.get("product") or {}).get("id")
        if pid:
            product_ids.append(pid)
    if not product_ids:
        raise ValueError("transaction has no product items")
    occurred = event.get("occurred_at")
    try:
        purchased = dt.datetime.fromisoformat(
            str(occurred).replace("Z", "+00:00")).date()
    except (TypeError, ValueError) as e:
        raise ValueError(f"bad occurred_at: {occurred!r}") from e
    return Purchase(event_id=str(event_id), transaction_id=str(data.get("id")),
                    email=email, name=customer.get("name"),
                    product_ids=tuple(dict.fromkeys(product_ids)),
                    purchased=purchased)


def _coverage(product_ids: tuple[str, ...], product_map: dict[str, str]) -> str:
    """Most generous coverage among the mapped items; unmapped products
    are a configuration gap and stop issuance (surfaces via Paddle
    retries) rather than silently issuing a lesser key."""
    unmapped = [p for p in product_ids if p not in product_map]
    if unmapped:
        raise ValueError(f"product(s) not in the product map: "
                         f"{', '.join(unmapped)}")
    covered = [product_map[p] for p in product_ids]
    if lic.FOREVER in covered:
        return lic.FOREVER
    return str(max(int(c) for c in covered))


# ---------------------------------------------------------------------------
# Append-only issue log (ADR-0002 pattern: sidecar flock, fsync append)
# ---------------------------------------------------------------------------

class IssueLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock_path = self.path.with_name(self.path.name + ".lock")

    def append(self, entry: dict) -> None:
        line = json.dumps(entry, sort_keys=True) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "a") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
            finally:
                fcntl.flock(lockf, fcntl.LOCK_UN)

    def entries(self):
        """Every parseable entry, oldest first; a torn final line from a
        crash mid-append is skipped (the append was never acked)."""
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    log.warning("skipping unparseable issue-log line")

    def find(self, *ids: str) -> dict | None:
        wanted = {i for i in ids if i}
        for entry in self.entries():
            if wanted & {entry.get("event_id"), entry.get("transaction_id")}:
                return entry
        return None


# ---------------------------------------------------------------------------
# Issuance
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WebhookConfig:
    secret: str
    seed: bytes                      # Ed25519 seed (offline custody)
    product_map: dict[str, str]      # paddle product id -> "1" | "forever"
    issue_log: IssueLog
    tolerance: int = DEFAULT_TOLERANCE


def handle_event(body: bytes, signature_header: str, cfg: WebhookConfig, *,
                 now: float | None = None) -> tuple[int, dict]:
    """One webhook delivery -> (http_status, json_response). Pure except
    for the issue-log append; the HTTP layer is a thin wrapper."""
    now = time.time() if now is None else now
    if not verify_paddle_signature(signature_header, body, cfg.secret,
                                   now=now, tolerance=cfg.tolerance):
        log.warning("rejected: bad or stale signature")
        return 401, {"error": "bad signature"}
    try:
        event = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"error": "body is not JSON"}
    if event.get("event_type") != EVENT_TYPE:
        return 200, {"ignored": event.get("event_type")}  # ack, no retry
    try:
        purchase = extract_purchase(event)
        v = _coverage(purchase.product_ids, cfg.product_map)
    except ValueError as e:
        log.warning("unissuable %s event: %s", EVENT_TYPE, e)
        return 422, {"error": str(e)}
    existing = cfg.issue_log.find(purchase.event_id, purchase.transaction_id)
    if existing is not None:
        return 200, {"issued": False, "duplicate": True,
                     "key": existing.get("key"),
                     "email": existing.get("email")}
    key = license_keygen.issue_key(
        cfg.seed, license_keygen.build_payload(
            purchase.email, purchase.purchased, v, purchase.name))
    entry = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(),
             "event_id": purchase.event_id,
             "transaction_id": purchase.transaction_id,
             "email": purchase.email,
             "name": purchase.name,
             "product_id": purchase.product_ids[0],
             "purchased": purchase.purchased.isoformat(),
             "v": v,
             "key": key}
    cfg.issue_log.append(entry)
    log.info("issued key for %s (txn %s)", purchase.email,
             purchase.transaction_id)
    return 200, {"issued": True, "email": purchase.email, "key": key}


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

def make_handler(cfg: WebhookConfig) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 (http.server API)
            if self.path.rstrip("/") != "/webhook":
                self._send(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            try:
                status, payload = handle_event(
                    body, self.headers.get("Paddle-Signature", ""), cfg)
            except Exception:  # noqa: BLE001 — one bad request never kills the endpoint
                log.exception("internal error handling webhook")
                status, payload = 500, {"error": "internal"}
            self._send(status, payload)

        def do_GET(self):  # noqa: N802 (http.server API)
            if self.path.rstrip("/") == "/healthz":
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})

        def _send(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args) -> None:  # route through logging
            log.debug("%s %s", self.address_string(), fmt % args)

    return Handler


def serve(cfg: WebhookConfig, *, host: str, port: int,
          tls_cert: str | None = None, tls_key: str | None = None
          ) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(cfg))
    server.daemon_threads = True
    if tls_cert and tls_key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(tls_cert, tls_key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    return server


def parse_product_map(raw: str) -> dict[str, str]:
    """`pro_a=1,pro_b=forever` -> {'pro_a': '1', 'pro_b': 'forever'}."""
    mapping: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        pid, _, v = pair.partition("=")
        pid, v = pid.strip(), v.strip()
        if not pid or (v != lic.FOREVER and not v.isdigit()):
            raise SystemExit(f"error: bad product map entry {pair!r} "
                             f"(expected product=1 or product=forever)")
        mapping[pid] = v
    if not mapping:
        raise SystemExit("error: empty product map")
    return mapping


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="license_webhook.py",
        description="Paddle transaction.completed -> offline license key")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address (default 127.0.0.1 — put a TLS "
                        "reverse proxy in front, or pass --tls-cert)")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--secret", default=os.environ.get(SECRET_ENV),
                   help=f"Paddle webhook secret (or ${SECRET_ENV})")
    p.add_argument("--private-key", metavar="PATH",
                   default=os.environ.get(PRIVATE_KEY_ENV),
                   help=f"Ed25519 seed path (or ${PRIVATE_KEY_ENV})")
    p.add_argument("--product-map", metavar="MAP",
                   default=os.environ.get(PRODUCT_MAP_ENV),
                   help="paddle product id -> coverage, e.g. "
                        "pro_aaa=1,pro_bbb=forever "
                        f"(or ${PRODUCT_MAP_ENV})")
    p.add_argument("--issue-log", metavar="PATH",
                   default=os.environ.get(ISSUE_LOG_ENV),
                   help=f"append-only issued-keys JSONL (or ${ISSUE_LOG_ENV})")
    p.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE,
                   help="signature timestamp tolerance in seconds "
                        f"(default {DEFAULT_TOLERANCE})")
    p.add_argument("--tls-cert", default=None,
                   help="serve HTTPS directly with this certificate")
    p.add_argument("--tls-key", default=None, help="key for --tls-cert")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    missing = [name for name, val in (("secret", args.secret),
                                      ("private-key", args.private_key),
                                      ("product-map", args.product_map),
                                      ("issue-log", args.issue_log))
               if not val]
    if missing:
        raise SystemExit(f"error: missing {', '.join(missing)} "
                         "(pass the flags or set the env vars)")
    cfg = WebhookConfig(secret=args.secret,
                        seed=license_keygen.load_seed(Path(args.private_key)),
                        product_map=parse_product_map(args.product_map),
                        issue_log=IssueLog(Path(args.issue_log)),
                        tolerance=args.tolerance)
    if license_keygen.public_key(cfg.seed) not in lic.PUBLIC_KEYS:
        log.warning("this seed's public key is not embedded in the app "
                    "(fluidvoice/license.py PUBLIC_KEYS) — issued keys "
                    "will be rejected by current builds")
    server = serve(cfg, host=args.host, port=args.port,
                   tls_cert=args.tls_cert, tls_key=args.tls_key)
    log.info("listening on %s:%d (log: %s)", args.host, args.port,
             args.issue_log)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
