# Refund operations — Paddle dashboard steps (issue #17)

Buyer-facing promise: the [refund page](../../site/refund.html) (the
public policy, 14 days no questions). This is the owner-side runbook
for acting on a request. Paddle is the merchant of record
([ADR-0005](../adr/ADR-0005-commercial-sku.md)), so the money
movement — refund to the original payment method, the VAT/sales-tax
credit note, the fee reversal — happens inside Paddle, not in a bank
app and not in this repo.

## Inbound: how a request arrives

| Channel | What the buyer does | What reaches the owner |
|---|---|---|
| Email | Replies to the Paddle order-confirmation email | lands in the seller-contact inbox Paddle has on file; forward nothing — process it as below |
| GitHub | Opens a "Refund request" issue with the order number from the confirmation email | same processing; the order number is the transaction id |

Either way the buyer must be identifiable — email address or order
number. If neither is present, ask for one before processing.

## Processing a refund (Paddle Billing dashboard)

1. Log in to the Paddle dashboard — the **live** account for real
   sales, sandbox while testing.
2. Open **Transactions** and find the sale: search the buyer's email
   or the order/transaction id from their request.
3. Check it is within the 14-day window (the transaction date is on
   the row). Outside the window, process it anyway if the buyer is
   acting in good faith — the public policy says "no questions".
4. Open the transaction → **Refund** → full amount, with an honest
   reason ("Customer request", "Duplicate", …). Paddle requires a
   reason; it feeds Paddle's audit trail, not ours.
5. Confirm. Paddle returns the money to the original payment method
   (the buyer typically sees it in 5–10 business days) and issues the
   tax credit note as merchant of record.
6. Verify the transaction now reads refunded, and reply to the buyer
   with the ETA above.

Dashboard labels are as verified 2026-11-21; if Paddle renames them,
fix this file in the same change (Paddle review links here).

## What a refund does to the license key

Nothing, cryptographically — by design
([ADR-0005](../adr/ADR-0005-commercial-sku.md)): keys are offline
Ed25519 signatures, the app keeps working, and no dictation feature
is ever license-gated. A refund ends the **support entitlement**:

1. Find the key: `scripts/license_lookup.py <buyer-email> --log
   issued.jsonl` (runbook: [license-ops.md](license-ops.md)).
2. The ledger of truth for money is Paddle's transaction state; the
   issue log is append-only and never rewritten for refunds.
3. Re-issues to refunded buyers (lost-key re-sends, support) are
   refused unless they purchase again — the key itself still
   verifies, only the support channel is closed.

## Test once (acceptance for #17)

Sandbox first, then once live:

1. Complete a sandbox purchase (test card `4242 4242 4242 4242`,
   any future expiry, any email you own).
2. Request a refund through the buyer path — reply to the Paddle
   confirmation email.
3. Process it with the dashboard steps above.
4. Confirm all four: buyer-side refund notice received, transaction
   reads refunded, credit note issued, the issued key still imports
   offline (`sayit-ermano license <KEY>` — expected, by design).
