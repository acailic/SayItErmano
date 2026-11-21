# Paddle refunds runbook (#17)

The refund-request path for the supporter license. Buyer-facing copy:
[site refund policy](../../site/refund.html) · legal basis:
[ADR-0005](../adr/ADR-0005-commercial-sku.md) (14 days, no questions).
Paddle is the merchant of record, so every refund — and the matching
VAT credit note — is issued from Paddle's dashboard, never manually.

## How a buyer requests a refund

1. Reply to the Paddle order-confirmation email (it comes from
   Paddle, not this project), or
2. use Paddle's customer portal at
   [paddle.net](https://paddle.net) → "Contact Paddle support about
   this charge" with the order number, or
3. open a GitHub issue titled "Refund request" with the order number
   (the path documented on the refund page).

Paths 1–2 need no seller action: Paddle support can issue the refund
directly. Path 3 (and any email to support) is executed with the
dashboard steps below.

## Issuing a refund (Paddle dashboard steps)

1. Paddle dashboard → **Transactions**.
2. Find the purchase: search by order number or the buyer's email.
3. Open the transaction → **Refund** (⋯ menu).
4. Full amount, reason code **customer request** (anything else is a
   dispute signal — do not use it for 14-day requests).
5. Confirm. Paddle reverses the charge, emails the buyer, and issues
   the VAT credit note as merchant of record. Money lands back on the
   buyer's payment method in ~5–10 business days.

After issuing: the support entitlement ends. The license key is *not*
revoked — the app keeps working (GPL-3.0; see
[license ops](license-ops.md) — "Refunds do not revoke keys"). Note the
refund in the issue log (append one line carrying the transaction id
and `"refunded": true`) so a later support request is answered
correctly.

## Acceptance: test the path once

Before go-live (#17 checklist): run one sandbox purchase, refund it
with the steps above in the sandbox dashboard, and confirm the buyer
email + credit note arrive. Record the sandbox order id in the issue
linked from #17.

## Chargebacks

Never dispute an in-window request (refund policy). If a chargeback
arrives anyway, answer it in the Paddle dashboard with the refund
policy link and the delivery evidence (transaction receipt email).
