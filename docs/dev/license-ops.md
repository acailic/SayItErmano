# License operations (owner runbook)

How a purchase becomes an offline key. The user-facing half is
[guides/license.md](../guides/license.md); the decision record is
[ADR-0005](../adr/ADR-0005-commercial-sku.md). Issue #16 is the
implementation ticket.

```
Paddle ── transaction.completed webhook ──> scripts/license_webhook.py
   (Paddle-Signature verified)                    │
                                                  ├─ signs Ed25519 key
                                                  │    (seed from env,
                                                  │    never in this repo)
                                                  ├─ appends issued-keys.jsonl
                                                  │    (append-only, flock)
                                                  └─ optional delivery email
buyer ── sayit-ermano license <KEY> ──> fluidvoice/license.py verifies
   OFFLINE against the embedded PUBLIC_KEYS — no server involved
```

## Key custody

- The **private seed** is 32 bytes (64 hex chars). It lives on offline
  media in the owner's custody and nowhere else — never this repo, never
  CI secrets, never a deployed host's disk in plaintext beyond what the
  webhook process reads from its environment.
- The **public halves** are embedded in `fluidvoice/license.py`
  (`PUBLIC_KEYS`). Rotation **appends**, never replaces: `key_id` in the
  verified `LicenseInfo` records which key matched, and old keys must
  keep verifying so buyers' existing keys never break.
- `PUBLIC_KEYS[0]` was generated 2026-11-21 (#16). If its seed was not
  retained in owner custody, run `keypair`, **append** the new public
  hex as the next entry, and issue from the new seed — verification
  tries every embedded key.

## The keygen CLI (owner machine only)

```
python3 scripts/license_keygen.py keypair            # fresh seed + public hex
python3 scripts/license_keygen.py issue --secret-file ~/offline/seed.hex \
    --email buyer@example.com --name "Ada Buyer" --version forever
python3 scripts/license_keygen.py public-key --secret-file ~/offline/seed.hex
python3 scripts/license_keygen.py verify "<KEY>"     # what a buyer's app sees
python3 scripts/license_keygen.py lookup --email buyer@example.com \
    --log issued-keys.jsonl                          # support re-issue
```

`issue` takes the seed from `--secret-file`, `--secret`, or
`$LICENSE_KEYGEN_SECRET` (in that precedence order; prefer the file so
the seed stays out of shell history) and prints the key alone on stdout.
Coverage `--version` is a major-version digit (`"1"` = all 1.x and
below) or `forever`.

The signing code (`scripts/license_sign.py`) is pinned to the official
RFC 8032 §7.1 vectors by `tests/test_license.py`; the installed wheel
and .deb ship the verify half only (`fluidvoice/license.py`), so a
redistributed package is never an issuing oracle.

## The webhook receiver

Paddle Billing's built-in license keys are deprecated; self-issuing on
`transaction.completed` is their documented migration path. Deploy
`scripts/license_webhook.py` (stdlib only) on a small host behind your
TLS reverse proxy (Caddy/nginx terminate TLS; the process binds
127.0.0.1):

```
PADDLE_WEBHOOK_SECRET=pdl_ntfset_...      # from Paddle's webhook page
LICENSE_SIGNING_SECRET=<64-hex seed>      # offline copy stays canonical
LICENSE_ISSUE_LOG=/var/lib/sayit-license/issued-keys.jsonl
LICENSE_PRODUCT_IDS=pro_...               # optional: ignore other products
LICENSE_DEFAULT_COVERAGE=forever          # or a digit, e.g. 1
LICENSE_EMAIL_COMMAND=msmtp -t            # optional; failures never fail the webhook
python3 scripts/license_webhook.py --host 127.0.0.1 --port 8787
```

Properties (all test-covered in `tests/test_license_webhook.py`):

- **Authenticity**: `Paddle-Signature: ts=...;h1=...` where h1 =
  HMAC-SHA256(secret, `ts:body`) — constant-time compare, malformed
  headers rejected.
- **Replay**: timestamps older than `LICENSE_MAX_AGE_SECONDS` (default
  300) are refused; Paddle's *legitimate* redeliveries of a fresh event
  are deduplicated by `event_id` (same key returned, exactly one log
  line).
- **Durability**: the issue log is append-only JSONL with a sidecar
  flock and fsync per append (ADR-0002 pattern). Each record: ts,
  event_id, paddle txn, email, name, purchased date, coverage, key.
- **Honesty**: a delivery email is optional (`LICENSE_EMAIL_COMMAND`
  receives the message on stdin). Whether or not it fires, the key is in
  the log and in the 200 response body — Paddle shows webhook response
  bodies in its dashboard delivery log.

### Sandbox test procedure (before going live)

1. Paddle sandbox → Developer tools → Notifications → add the endpoint
   URL with the `transaction.completed` event; copy the secret into
   `PADDLE_WEBHOOK_SECRET`.
2. Check out a sandbox test order (Paddle's checkout in sandbox mode)
   for your product.
3. Watch the receiver log: expect a 200 with the key; `issued-keys.jsonl`
   gains one line; Paddle's delivery log shows the same body.
4. Import the key offline: `sayit-ermano license '<KEY>'` on a machine
   with no network access to the license host — it must verify and show
   in Settings → About → License.

## Support lookup

"Buyer lost their key" is a log query, not a new issuance:

```
python3 scripts/license_keygen.py lookup --email buyer@example.com
```

Refunds: Paddle is merchant of record (14-day window, ADR-0005). The key
keeps verifying afterwards by design — the key gates nothing; what ends
is the support entitlement (the email is the identity).

## Runner-up store: Polar

If the store moves to Polar.sh, keep this Ed25519 key as the in-app
truth (Polar's License Keys *validation API* is online-only, which would
break the offline promise):

- Create a Polar **License Keys** benefit on the product; Polar emails
  the buyer their Polar-issued key, and Polar's key is what the *store*
  tracks for its own seat/limit tooling.
- Point the webhook receiver (or a sibling route) at Polar's
  `order.polar.updated` / benefit-grant webhook instead of Paddle's;
  verify Polar's signature header (HMAC-SHA256 of the raw body with the
  endpoint secret, plus their timestamp header) exactly as the Paddle
  path does, then issue the SAME offline Ed25519 key payload from the
  order's customer email — the buyer imports that key in-app.
- Everything downstream (embedded `PUBLIC_KEYS`, `sayit-ermano
  license`, issue log, lookup) is store-agnostic and unchanged.

## What this system deliberately does not do

See [guides/license.md](../guides/license.md) — no feature gating, no
revocation list, no phoning home. GPL makes any lock symbolic; the key
records the entitlement (support identity + covered versions) and makes
purchase feel real. Abuse handling is capped at support-refusal cases.
