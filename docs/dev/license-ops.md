# License operations runbook (owner-only)

How offline license keys get made, delivered, and supported. The
buyer-facing story is [guides/license.md](../guides/license.md); the
decision record is [ADR-0005](../adr/ADR-0005-commercial-sku.md)
(issue #16).

The moving parts:

| Piece | Where | Trust |
|---|---|---|
| Offline verifier (ships to every user) | `fluidvoice/license.py` | embedded public keys |
| Keygen (never ships; owner runs it) | `scripts/license_keygen.py` | private seed, offline media |
| Paddle webhook -> key issuance | `scripts/license_webhook.py` | webhook secret + seed, on the owner's server |
| Support lookup over issued keys | `scripts/license_lookup.py` | issue log (JSONL) |

Everything is stdlib; the Ed25519 math is the RFC 8032 reference
implementation, verified against the official test vectors in
`tests/test_license.py` (both directions) and exercised end-to-end in
`tests/test_license_webhook.py`.

## Key custody

1. Generate **on offline media** (USB, encrypted vault — not this repo;
   `generate` refuses paths inside the repository):
   ```
   python scripts/license_keygen.py generate --private-key /media/key/sayit-ed25519.seed
   ```
   It prints the public key hex. Append it to `PUBLIC_KEYS` in
   `fluidvoice/license.py` (rotation appends, never replaces), ship a
   release, and only then start issuing with that seed.
2. The seed file is one line of 64 hex chars, mode 0600. Back it up;
   losing it means every future key needs a new keypair (old keys keep
   verifying forever — the old public key stays embedded).
3. Issue manually (support re-sends, direct sales, gifts):
   ```
   python scripts/license_keygen.py issue --private-key /media/key/sayit-ed25519.seed \
       --email buyer@example.com --name "Jane Doe" --v 1
   ```
   `--v` is `forever` or a major version digit string and is required —
   no accidental lifetime keys. `verify KEY` re-checks a key exactly
   the way a buyer's app does (embedded public keys, fully offline).

## Webhook: Paddle purchase -> key

Paddle Billing deprecated its built-in license keys; self-issuing on
`transaction.completed` is the documented path. Deploy
`scripts/license_webhook.py` on a small host the owner controls:

```
export PADDLE_WEBHOOK_SECRET=pdl_ntfset_...      # from the Paddle dashboard
export SAYIT_LICENSE_PRIVATE_KEY=/media/key/sayit-ed25519.seed
export LICENSE_PRODUCT_MAP=pro_abc123=1,pro_def456=forever
export LICENSE_ISSUE_LOG=/var/lib/sayit-license/issued.jsonl
python scripts/license_webhook.py --host 127.0.0.1 --port 8080
```

Put TLS in front (Caddy/nginx proxy, or `--tls-cert/--tls-key`), then
register `https://your-host/webhook` in the Paddle dashboard. The
`LICENSE_PRODUCT_MAP` decides coverage per Paddle product id: `1`
covers the 1.x line, `forever` covers all releases (ADR-0005 pricing).

What the endpoint guarantees:

- **Authenticity**: every request must carry a fresh `Paddle-Signature`
  header (`ts=...,h1=...`, HMAC-SHA256 of `{ts}:{body}` with the
  webhook secret). Forged or tampered bodies -> 401, no key, no log
  entry. `--tolerance` (default 300 s) bounds replay; `h2` in the
  header covers webhook-secret rotation.
- **Idempotency**: Paddle redelivers; a repeated event/transaction id
  returns the SAME key (`"duplicate": true`) and never appends a second
  log line.
- **Honest failures**: an unmapped product or a payload without a
  customer email answers 422 (Paddle retries and the owner sees the
  misconfiguration) instead of issuing a wrong key.
- Unknown event types are acknowledged (200) without issuing.

### Sandbox test (acceptance for #16)

1. Point a Paddle **sandbox** webhook at the deployed endpoint and
   create a sandbox test transaction for a mapped product.
2. The endpoint answers 200 with `{"issued": true, "key": "..."}`;
   the key also lands in the issue log.
3. Offline, on the target machine: `sayit-ermano license <KEY>` —
   must import and show in Settings -> About -> License with no
   network. (The same round-trip is pinned by
   `tests/test_license_webhook.py::test_http_endpoint_round_trip`.)

## The issue log + support lookup

`issued.jsonl` is append-only (flock sidecar + fsync — the ADR-0002
transaction pattern), one JSON object per issued key: `ts`,
`event_id`, `transaction_id`, `email`, `name`, `product_id`,
`purchased`, `v`, `key`. It is the entitlement record — back it up
with the seed.

Support flow (lost key, "which key did I get?", entitlement checks):

```
python scripts/license_lookup.py buyer@example.com --log issued.jsonl
python scripts/license_lookup.py txn_01hwqz...   --log issued.jsonl
```

Email matches are substring + case-insensitive; transaction/event ids
match exactly. Re-send the printed key — the copy for that email is
[store-emails.md](store-emails.md). The key never expires and never
needs the server again. Refunds do not revoke keys (the app keeps
working; see the GPL note in the user guide) — they end the support
entitlement; the seller-side refund flow is
[paddle-refunds.md](paddle-refunds.md).

## If the store becomes Polar instead

Runner-up wiring (ADR-0005 decision 3): Polar's License Keys benefit
plus its order webhook become the trigger; the Ed25519 offline key
stays the in-app truth because Polar's validation API is online-only
and this product must work offline. Concretely: swap
`verify_paddle_signature` for Polar's webhook-signature check and the
`extract_purchase` field names for Polar's order payload; issuance,
the issue log, and the app side are unchanged.

## Not built (deliberate)

- `sayitermano://license/KEY` click-to-activate deep link from the
  purchase email (the superwhisper pattern). Needs desktop URI
  handler registration; the CLI import covers the same flow today.
- Any form of key revocation/kill switch — it would break the honest,
  offline, never-gates-dictation design (ADR-0005).
