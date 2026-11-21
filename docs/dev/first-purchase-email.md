# First-purchase email (owner-only)

What a buyer receives right after paying (#17 acceptance: "first-purchase
email reads well"). Two emails are involved; Paddle sends the first
automatically, we draft the second.

## 1. Paddle's receipt — automatic, nothing to build

Paddle Billing sends the customer a receipt email automatically when a
transaction completes, including a PDF invoice with the VAT/sales-tax
breakdown (Paddle is merchant of record — "Paddle automatically sends
customers an email receipt when a transaction is completed … receipts
include a PDF invoice", developer.paddle.com, checked 2026-11-21).
Branding (logo, colors) is set in the Paddle dashboard under
**Paddle > Checkout > Checkout configuration**; there is no free-text
body template to fill in Paddle Billing.

Owner action: upload the SayItErmano logo/brand color there, in both
sandbox and live accounts (settings are separate).

## 2. License-key delivery — our email, draft copy

The license key comes from our side (`transaction.completed` webhook →
Ed25519 key, [license-ops.md](license-ops.md)). Until sending is
automated, the owner sends this by hand from the issue log (the flow
`scripts/license_lookup.py` prints everything the draft needs:
buyer email, name, key, product, purchase date). The draft:

**Subject:** `Your SayItErmano license key (and download)`

    Hi {name},

    Thanks for supporting SayItErmano! Your purchase is complete —
    this email has your license key and where to get the app.

    Your license key (keep this email):

        {KEY}

    What it does: it records your entitlement to packaged builds with
    the update channel and priority support. It never gates dictation —
    every feature works with or without it, offline, forever. That is
    a GPL obligation and a design choice.

    Get the app:
      - Packaged builds + update channel: https://github.com/acailic/SayItErmano/releases
      - Install guide: https://github.com/acailic/SayItErmano/blob/linux/docs/guides/install.md

    Import the key:  sayit-ermano license {KEY}
    (or Settings → About → License in the app)

    Two housekeeping notes:
      - Your payment receipt and VAT invoice come from Paddle.com, the
        merchant of record for this sale (check spam if you don't see
        it within the hour; it also lists your order number).
      - Not what you expected? 14-day refund, no questions asked:
        https://acailic.github.io/SayItErmano/refund.html

    Happy dictating,
    {sender} — SayItErmano

Copy rules it follows (same as the site): no feature-gating claims,
GPL stated plainly, Paddle named as MoR, refund path one click away,
every link works without the key.

Notes for automating later (NOT built yet, deliberately): the webhook's
issue-log append is the natural hook — a small mail-sender reading the
same JSONL entry would send this template. The `sayitermano://license/`
deep link stays out until a URI handler exists
([license-ops.md](license-ops.md) "Not built").

## Sandbox test (acceptance for #17)

1. Place a sandbox test order (card `4242 4242 4242 4242`, any future
   expiry, CVC `100`).
2. Paddle's receipt email arrives at the address used (sandbox emails
   are delivered normally).
3. The webhook issues the key; send the draft above by hand and check
   it imports with `sayit-ermano license <KEY>` offline.
