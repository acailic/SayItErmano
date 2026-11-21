# Paddle refund runbook (owner-only)

How a refund request becomes a refunded purchase. The buyer-facing
policy is [site/refund.html](../../site/refund.html) (published on the
landing site); this is the seller side. Paddle is the merchant of
record, so the money movement happens entirely in Paddle — we verify
the request, click the refund, and close the loop on support
entitlement.

## Intake (where requests arrive)

1. A reply to the Paddle order-confirmation email (forwards to the
   seller inbox Paddle has on file), or
2. a GitHub issue titled "Refund request" with the order number from
   the checkout email, or
3. email to the support address with the purchase email + roughly the
   purchase date (enough to find the transaction).

The window: 14 days from purchase, full refund, no questions asked
([site/refund.html](../../site/refund.html)). Requests outside the
window are honored case-by-case; we never dispute an in-window request
(chargeback policy below).

## Processing (Paddle dashboard steps)

Verified against Paddle's "Refund or credit a transaction" guide
(developer.paddle.com, checked 2026-11-21):

1. **Paddle > Transactions** — use the search box (buyer email) and the
   *Filter by* drop-down to find the transaction. The transaction must
   be `completed`.
2. Click into the transaction, then click **Refund**.
3. Choose a **reason** from the drop-down (e.g. *Customer request*).
4. Review the items list, enter the full amount in the **Refund
   amount** box, click **Continue**.
5. Review, then click **Request refund** to confirm.

Paddle returns the money to the original payment method, typically
5–10 business days after approval, and emails the buyer. Nothing else
to do on the money side — VAT/invoice correction is handled by Paddle
as merchant of record.

## After the refund (close the loop)

- **Record it**: reply in the intake thread (email or issue) with the
  refund date, then close the issue. Append the refund note to your
  local copy of the support record — the transaction id is the join
  key into the issue log
  ([license-ops.md](license-ops.md), `scripts/license_lookup.py`).
- **License**: the key keeps working by design (GPL — the app never
  gates dictation, and refunds do not revoke keys; ADR-0005). What
  ends is the support entitlement and the update-channel entitlement
  for future majors. Note the ended entitlement in the same record.
- **Funnel**: refunds feed row 4 of the decision rule
  ([pricing-funnel-decision-rule.md](../pricing-funnel-decision-rule.md)) —
  a refund rate over 5% over any 4-week window is a product/support
  defect signal, not a pricing lever. Count them in the weekly export
  (`quality/funnel/`).

## Acceptance test (once, #17)

During the sandbox test purchase: complete a sandbox transaction, then
run the exact flow above against it in the sandbox dashboard. Confirm
the buyer-side email arrives and the issue-log row for the transaction
can be found by id. Record the run in the #17 ticket (sandbox and,
after go-live, once live).

## Chargebacks

A buyer dispute arrives as a Paddle notification, not a request we
action. Response: never dispute an in-window case — refund it via the
flow above instead (cheaper than a chargeback fee, kinder to the
buyer). Paddle's dispute handling governs the rest; our part is
evidence (delivery email, license issuance log row) if Paddle asks.
