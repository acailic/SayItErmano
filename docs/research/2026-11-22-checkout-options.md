# Checkout options: merchant of record vs direct processor

- Date: 2026-11-22
- Status: research evidence for monetization plan question 4
  ([plan](../plans/2026-09-11-product-excellence-and-monetization-plan.md),
  "How does buying work?") — not a go-live decision; the decision record
  stays [ADR-0005](../adr/ADR-0005-commercial-sku.md)
- Repository inspected: `linux` at `f6bdaec` (v0.9.0 license-key
  implementation in tree; the clone is single-commit, so file paths and
  line numbers are cited from the working tree)
- Rule: every fee/feature claim cites an official URL fetched
  2026-11-22; anything not verifiable today is marked **OPEN**.

<!-- WIP: sections 1, 2, 4 are being researched; section 3 is complete. -->

## 3. Integration map against our key system

The checkout is the only piece the v0.9.0 implementation does not own:
everything after "payment confirmed" already exists. What the checkout
must deliver is therefore narrow — one authenticated, idempotent
"payment completed" event carrying the buyer email, a stable
transaction id, the purchased product, and the time of purchase.

### 3.1 Hook points that exist today (v0.9.0)

| Hook | Where | What it does | Checkout requirement it imposes |
|---|---|---|---|
| Webhook endpoint | `scripts/license_webhook.py:56` (`EVENT_TYPE = "transaction.completed"`), handler `handle_event` at `:212` | Receives one event, verifies it, issues a key | A server-to-server notification with a verifiable signature; Paddle's event name is hard-coded, so another provider needs the name and signature scheme swapped |
| Signature check | `scripts/license_webhook.py:69` `verify_paddle_signature` | HMAC-SHA256 over `{ts}:{body}` against `Paddle-Signature: ts=,h1=`; 300 s replay tolerance (`DEFAULT_TOLERANCE`, `:57`) | Provider must sign raw request bodies with a shared secret and a timestamp. A provider that signs a *canonicalized* payload or uses asymmetric signatures (Stripe-Signature) needs a new verifier function |
| Field extraction | `scripts/license_webhook.py:105` `extract_purchase` | Reads `data.id` (transaction), `data.customer.email`, `data.customer.name`, `items[].price.product_id`, `occurred_at` | Payload must carry: buyer email, product id per line item, transaction id, event time. Missing email/product → 422 (no key minted, provider retries) |
| Product → coverage map | `scripts/license_webhook.py:140` `_coverage`, env `LICENSE_PRODUCT_MAP` (`:60`) | `pro_xxx=1` or `pro_xxx=forever`; unmapped product stops issuance | Each purchasable SKU needs a stable provider product id that the owner maps to a coverage value |
| Key minting | `scripts/license_keygen.py:93` `issue_key`, payload built at `:83` `build_payload` | Signs `{product, email, purchased, v, [name]}` with the Ed25519 seed | Only needs the three fields above; provider-agnostic |
| Issue log / dedup | `scripts/license_webhook.py:158` `IssueLog`, lookup at `:191` `find` | Append-only JSONL keyed by `event_id` + `transaction_id`; redelivery returns the same key | Provider must redeliver failed webhooks with a *stable* event or transaction id (otherwise duplicates mint second keys) |
| HTTP surface | `scripts/license_webhook.py:262` `do_POST` (path `/webhook`), `:276` `do_GET` (`/healthz`), header read at `:270` | One path, one signature header name | A direct processor needs its own path or a dispatcher on the `Stripe-Signature` header |
| Buyer-side import | `fluidvoice/cli.py:114-151` (`license` subcommand), `fluidvoice/license.py:227` `import_key`, storage `fluidvoice/paths.py:102` `license_file()` | Verifies offline, writes `~/.config/sayit-ermano/license.key` (0600) | Nothing from the checkout; the key must arrive out-of-band (email today) |
| About page | `fluidvoice/gtkui/settings_pages/about.py:47-73` | Display-only entitlement row | Nothing from the checkout |
| Store front | `site/assets/js/store.js` (Paddle.js overlay, `Paddle.Checkout.open` at `:72`), `site/assets/js/store-config.js:23` (`SAYIT_STORE`, empty ids = plain-link fallback) | Progressive-enhancement buy button; `checkout.completed` client event only reveals `#purchase-next` (`:57-64`) | Any provider with a hosted/overlay checkout maps onto the same "arm the CTA" pattern; client events are UX only, never the trigger |
| Tests | `tests/test_license.py`, `tests/test_license_webhook.py` (`paddle_event` fixture at `:40`) | Pin RFC 8032 vectors, signature gate, replay, idempotency, HTTP round trip | A provider swap must add a fixture for the new signature scheme and event shape |
| Ops docs | `docs/dev/license-ops.md`, `docs/dev/paddle-refunds.md`, `docs/dev/store-emails.md`, `docs/guides/license.md` | Runbooks: custody, sandbox test, refund, lost key, email copy | Every provider change is a runbook change in four files plus the buyer-facing guide |

### 3.2 What the checkout must deliver

1. **Key issuance webhook** — a server-to-server "payment completed"
   notification that is signed with a shared secret over the raw body
   (`{ts}:{body}` HMAC), carries `email`, product id, transaction id,
   and timestamp, and is redelivered with the same id on failure.
   This is exactly `extract_purchase`'s contract
   (`scripts/license_webhook.py:105-137`). **Signed entitlement**: the
   Ed25519 key *is* the signed entitlement; the webhook is only the
   minting trigger. Nothing in the checkout needs to sign anything.
2. **Idempotent redelivery** — the issue log dedups on
   `event_id`/`transaction_id` (`:191-196`); a provider that sends
   multiple *different* event types for one purchase (e.g.
   `checkout.completed` *and* `payment.succeeded`) must be filtered to
   exactly one (`:225` acks unknown types without issuing).
3. **Buyer email as the identity** — the payload's `email` is the
   support identity (`fluidvoice/license.py:194-196` requires it).
   Providers that do not surface the email on the webhook (PayPal-only
   flows without `payer_email`, anonymous checkouts) cannot mint keys.
4. **Transaction id as the join key** — refunds and support lookups
   join on `transaction_id` (`scripts/license_lookup.py:24-46`,
   `docs/dev/paddle-refunds.md` "After the refund"). The provider
   must expose it on the receipt the buyer sees.
5. **Coverage mapping** — one provider product id per SKU, mapped in
   `LICENSE_PRODUCT_MAP` (`scripts/license_webhook.py:308`
   `parse_product_map`). The `$39` intro and `$49` standard prices
   are two *prices* on one product in Paddle; both can map to the same
   coverage.

### 3.3 Refund → key path (what exists, what does not)

- **Exists:** the money side is entirely in the MoR dashboard
  (`docs/dev/paddle-refunds.md` steps 1–5); after a refund the
  transaction id is looked up in the issue log and the ended support
  entitlement is noted in the support record.
- **Deliberately absent:** key revocation. `fluidvoice/license.py` has
  no revocation list, no expiry check beyond a future-date guard
  (`:203`), and `docs/dev/license-ops.md` "Not built (deliberately)"
  rules out a kill switch — the key never gates dictation (GPL, ADR-0005),
  so revoking it would revoke nothing the app enforces.
- **Consequence for the checkout choice:** a *direct* processor moves
  the refund click and the chargeback response onto the seller. The
  key side is unchanged (keys outlive refunds on both rails), but the
  seller now also owes the buyer a corrected invoice/VAT credit — an
  obligation the MoR absorbs today (`docs/dev/paddle-refunds.md:38-41`).
- **OPEN:** whether the update-channel entitlement (#22) will ever be
  enforced server-side. If yes, a revocation *list of transaction
  ids* (not of keys) would be the right shape, and the checkout must
  expose refund/dispute events to maintain it. Today nothing consumes
  them.

### 3.4 Purchase-recovery UX (exists today, provider-neutral)

| Need | Current path | Cited at |
|---|---|---|
| Lost key | Buyer replies to the purchase email or opens an issue; owner runs `scripts/license_lookup.py <email or txn>` against `issued.jsonl` and re-sends | `docs/guides/license.md` "Lost keys", `docs/dev/license-ops.md` "The issue log + support lookup" |
| Reinstall / new machine | Re-import the same key: `sayit-ermano license <KEY>`; no deactivation, no device limit | `docs/guides/license.md` "Commands" |
| Lost receipt / invoice | MoR support (Paddle receipts carry the VAT invoice; seller must not reissue) | `docs/dev/store-emails.md:8`, `docs/dev/paddle-refunds.md:40-41` |
| Self-service re-download | Not built — email points at the install guide; "never a time-limited link, the key is the entitlement" | `docs/dev/store-emails.md:61-64` |
| Click-to-activate deep link | Not built (needs a desktop URI handler); CLI import covers it | `docs/dev/license-ops.md:124-126` |

The recovery UX is fully manual on the seller side. A provider with a
**customer portal** (order history, receipt re-send, key re-display)
removes the two most common support tickets without touching our code;
a provider without one keeps them on the owner's inbox.

<!-- WIP: sections 1, 2, 4 follow. -->
