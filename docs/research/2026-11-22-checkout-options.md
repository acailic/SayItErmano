# Checkout options: MoR vs direct processor — monetization plan question R4

Date: 2026-11-22. Status: **WIP — research in progress** (pushed early per
task constraint; fee/eligibility citations below are verified, payout and
integration sections still being filled in).

This note answers question 4 of the
[monetization plan](../plans/2026-09-11-product-excellence-and-monetization-plan.md):
*how does buying actually work?* The offline license-key system shipped in
v0.9.0 (purchase → key → activation); the missing half is the checkout that
sells the key. The structural choice is merchant-of-record (MoR) versus
direct card processor. It feeds the two processor columns of the
[cost model](2026-11-22-one-time-purchase-cost-model.md) and the
[PRICING.md](../PRICING.md) decision record.

Seller context (fixed for this comparison): sole maintainer based in the
Serbia/Bosnia region, selling a one-time €19–€39 desktop license worldwide
via the landing site; no company today, registered entity possible.

## Headline findings (verified 2026-11-22)

1. **Lemon Squeezy is dead as an option.** It closed new store sign-ups
   and stops processing payments on 2027-03-31; its successor is Stripe
   Managed Payments (MoR) — announcement:
   <https://www.lemonsqueezy.com/blog/saying-goodbye-to-lemon-squeezy>
   (accessed 2026-11-22).
2. **Stripe (direct) is not available to a seller based in Serbia or
   Bosnia.** Neither country appears on Stripe's supported-countries
   list — <https://stripe.com/global> (accessed 2026-11-22). The direct
   column therefore means "direct via a foreign entity (e.g. Stripe
   Atlas US LLC)" or a different processor, not Stripe as a local.
3. **Paddle remains the only first-party MoR already wired into the
   codebase** (webhook receiver + store overlay shipped in v0.9.0), at
   5% + 50¢ per transaction — <https://www.paddle.com/pricing>
   (accessed 2026-11-22).

## 1. Two columns, from current official documentation

TODO (in progress): fee/tax/refund/payout/chargeback/friction table for
Paddle vs Stripe (incl. Stripe Managed Payments as the MoR-successor
class), each cell cited with URL + access date.

## 2. Seller eligibility — Serbia/Bosnia region

Verified, not assumed:

- **Stripe direct: NOT available** in Serbia or Bosnia (individual or
  registered entity) — the supported-countries list at
  <https://stripe.com/global> (accessed 2026-11-22) contains neither
  RS nor BA. Workaround paths: Stripe Atlas US incorporation, or a
  supported-country entity.
- **Lemon Squeezy: closed to new sign-ups entirely** —
  <https://www.lemonsqueezy.com/blog/saying-goodbye-to-lemon-squeezy>
  (accessed 2026-11-22). Eligibility is moot.
- **Paddle: TODO — verifying** the seller-country list.
- **Stripe Managed Payments: TODO — verifying** the supported business
  locations list (the Lemon Squeezy wind-down links it at
  <https://docs.stripe.com/payments/managed-payments/eligibility#supported-business-locations>).

## 3. Integration map against our key system

TODO: exact hook points in the v0.9.0 implementation
(`scripts/license_webhook.py`, `scripts/license_keygen.py`,
`site/assets/js/store-config.js`, `scripts/license_lookup.py`).

## 4. Recommendation and top three risks

TODO.
