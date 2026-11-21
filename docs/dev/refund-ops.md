# Refund runbook (owner-only)

How a refund request becomes money back in the buyer's card. The
buyer-facing path is [the site's refund policy](../../site/refund.html)
(reply to the Paddle order email, or a GitHub issue titled "Refund
request" with the order number); this page is what happens after that,
in the Paddle dashboard. Verified against developer.paddle.com
("Refund or credit a transaction"), checked 2026-11-21.

## Policy recap

14 days, no questions asked, full refund. Paddle.com is the merchant
of record, so the refund itself is issued through Paddle — we never
touch card data or payment rails.

## Dashboard steps (verified 2026-11-21)

1. Find the order: **Paddle > Transactions**, search by the buyer's
   email or the order/transaction id (`txn_...`) from their request.
   (If they only have a license key, look up their purchase email with
   `scripts/license_lookup.py <KEY or email> --log issued.jsonl` — the
   issue log carries the transaction id.)
2. Click into the transaction, then **Refund**.
3. Pick a reason from the drop-down (e.g. "customer request" /
   "duplicate").
4. Enter the refund amount per item — full amount for the 14-day
   no-questions policy — then **Continue**.
5. Review, then **Request refund**.

What Paddle does from there:

- Live-account refunds up to $400 USD against a positive balance on a
  verified account are auto-approved; otherwise they sit at
  `pending_approval` until Paddle reviews (the supporter license at
  $39/$49 is under the threshold, so approval is normally instant).
- The money returns to the original payment method, usually within
  5–10 business days.
- Paddle emails the buyer a **credit note PDF** automatically — the
  VAT side of the refund (MoR bookkeeping), nothing for us to send.
- Sandbox accounts: refunds are auto-approved every ~10 minutes.

## After the refund (our side)

- **Keys are not revoked — by design.** The app keeps working (GPL,
  never gates dictation); what ends is the support entitlement and
  update-channel access for that purchase.
- Note the issue-log entry: annotate the buyer's row in the support
  lookup (`issued.jsonl` is append-only — record the refund in the
  support notes/spreadsheet of choice, not by editing history).
- The funnel report counts refunds (decision rule row 4: >5% over a
  4-week window = treat as product/support defect, stop price work).
  Weekly export lands in `quality/funnel/paddle.json`.

## Test once (acceptance for #17)

Place a sandbox test purchase, refund it via the steps above, confirm:
the buyer email receives the credit note, the dashboard shows the
adjustment (`action: refund`, status `approved` after the ~10-minute
sandbox sweep), and the earlier-issued key still verifies offline
(expected — no revocation).

Related: [license-ops.md](license-ops.md) (keys, issue log, lookup),
[first-purchase-email.md](first-purchase-email.md) (what the buyer
gets on the way in).
