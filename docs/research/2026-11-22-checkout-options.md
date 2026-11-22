# Checkout options: MoR vs direct processor — monetization plan question R4

Date: 2026-11-22. Status: research note, verified against official
documentation (every fee/feature claim carries a URL + access date).
This note answers question 4 of the
[monetization plan](../plans/2026-09-11-product-excellence-and-monetization-plan.md):
*how does buying actually work?* The offline license-key system shipped
in v0.9.0 (purchase → key → activation); the missing half is the
checkout that sells the key. It feeds the two processor columns of the
[cost model](2026-11-22-one-time-purchase-cost-model.md) and sits under
the [PRICING.md](../PRICING.md) decision record.

Seller context (fixed for this comparison): sole maintainer based in
the Serbia/Bosnia region, selling a one-time desktop license at
€19–€39 worldwide via the landing site; today an individual, a
registered entity is possible but not free.

## Headline findings (all verified 2026-11-22)

1. **Lemon Squeezy is not an option anymore.** It closed new store
   sign-ups and stops processing payments on 2027-03-31; its successor
   is Stripe Managed Payments
   (<https://www.lemonsqueezy.com/blog/saying-goodbye-to-lemon-squeezy>,
   accessed 2026-11-22). Any MoR comparison written before that
   announcement is stale.
2. **Stripe (direct) is unavailable to a Serbia- or Bosnia-based
   seller** — neither country is on Stripe's supported-countries list
   (<https://stripe.com/global>, accessed 2026-11-22). "Direct
   processor" here means *direct via a foreign entity* (e.g. a Stripe
   Atlas US LLC), not Stripe as a local.
3. **Stripe Managed Payments (the MoR successor) has the same regional
   gap**: its supported-business-locations list (US/CA + EU + a few
   APAC) contains neither Serbia nor Bosnia
   (<https://docs.stripe.com/payments/managed-payments/eligibility>,
   accessed 2026-11-22). It also refuses customers from Kosovo — part
   of our region — which Paddle does not.
4. **Paddle is eligible here.** Paddle "works with software businesses
   anywhere in the world" minus a sanctions-driven unsupported list
   that does not include Serbia or Bosnia
   (<https://www.paddle.com/help/start/intro-to-paddle/which-countries-are-supported-by-paddle>,
   accessed 2026-11-22) — and it is what the v0.9.0 code already
   integrates.

## 1. Two columns, from current official documentation

| | **MoR — Paddle** | **MoR — Stripe Managed Payments** | **Direct — Stripe (via US entity)** |
| --- | --- | --- | --- |
| Fees | 5% + 50¢ per checkout transaction, "all-inclusive", no monthly fee (<https://www.paddle.com/pricing>, accessed 2026-11-22) | +3.5% per successful MoR transaction **on top of** standard Payments fees (<https://stripe.com/managed-payments> FAQ and <https://stripe.com/en-us/pricing>, accessed 2026-11-22) — i.e. ~6.4% + 30¢ domestic, more for international cards | 2.9% + 30¢ domestic cards; +1.5% international cards; +1% currency conversion (<https://stripe.com/en-us/pricing>, accessed 2026-11-22). Most of our buyers are foreign-currency → ~5.4% + 30¢ realistic |
| VAT / sales tax | Paddle is seller of record: collects, files, remits globally; "We collect the correct rate of tax, track the filing deadlines … submit sales tax returns" (pricing FAQ, URL above) | Stripe (via Link) is merchant of record for calculation, collection, filing, and remittance in supported jurisdictions (eligibility + managed-payments pages, URLs above) | **Seller remits.** Stripe Tax only automates calculation/registration help ("businesses remain the merchant of record and keep tax liability" — managed-payments FAQ, URL above); Tax Complete starts at $90/mo on a 1-year contract (pricing page, URL above). This is the cost model's €60/mo seller-side compliance line |
| Refund mechanics | Full or partial refund issued from the dashboard; reason flows into refund webhooks; card refunds credit in 3–5 working days; card refunds only <120 days old (<https://www.paddle.com/help/manage/your-customers/how-do-i-issue-refunds>, accessed 2026-11-22) | "Stripe can issue refunds within 60 days of purchase in certain cases" as an eligibility commitment (eligibility page, URL above); otherwise seller-triggered like Stripe | Seller-triggered via API/dashboard; no stated time limit; seller's call |
| Payout schedule / currency | Monthly only: balance converts on the 1st if over the threshold (min $100), sent by the 15th, up to 3 more working days to arrive; wire or Payoneer; free for most countries, $15 SWIFT fee for some (<https://www.paddle.com/help/manage/get-paid/when-and-how-do-i-get-paid>, accessed 2026-11-22) | "Up to 4x faster than the monthly cycles typical of other providers" (marketing claim, <https://stripe.com/managed-payments>, accessed 2026-11-22); Stripe standard payouts run on a rolling/weekly/monthly basis (pricing page, URL above) | Same Stripe payout rails: rolling, weekly, or monthly, no extra fee on the standard schedule (pricing page, URL above) — but the money lands in the US entity's account |
| Fraud / chargeback liability | Included in the fee: "Fraud protection, chargebacks, and buyer support" (pricing page, URL above); Paddle fights disputes as seller of record | "Disputes handled on your behalf", fraud prevention included (managed-payments page, URL above); eligibility requires keeping a low dispute rate | **Seller liable.** "Stripe debits your balance for the payment amount and dispute fee" (<https://docs.stripe.com/disputes>, accessed 2026-11-22); $15 dispute fee per dispute received, returned only for won disputes; Smart Disputes costs 30% of won amounts (pricing page, URL above) |
| Account-review friction | Manual account verification before live sales (help category exists: <https://www.paddle.com/help/start/account-verification>); "our team may from time to time request further information before … releasing a payment" (countries article, URL above) | Eligibility review "considers factors such as business type and geography" on top of Stripe's normal onboarding (eligibility page, URL above) | Stripe onboarding itself is fast, but for us it is gated behind entity formation: Atlas is $500 + first-year agent, ~1 business day to incorporate, **but the tax ID takes weeks without a US SSN/address/phone**, and Delaware franchise/annual tax plus federal filings recur (<https://stripe.com/atlas>, accessed 2026-11-22) |

(One MoR cell deliberately not claimed: whether Paddle returns its
processing fee on refunded transactions. The official refund article
above is silent on fee clawback; the
[cost model](2026-11-22-one-time-purchase-cost-model.md) already
charges the fee on refunds as the conservative reading, and
[docs/dev/paddle-refunds.md](../dev/paddle-refunds.md) is the
operating runbook.)

## 2. Seller eligibility — Serbia/Bosnia region, verified per provider

- **Paddle — eligible (individual or entity).** The unsupported-countries
  list (sanctions-driven: Afghanistan, Belarus, Cuba, Iran, Russia, …)
  contains neither Serbia nor Bosnia, and Paddle states it works with
  software businesses "anywhere in the world" outside that list; the
  article does not distinguish individual vs registered-entity sellers,
  so the exclusion list applies to both
  (<https://www.paddle.com/help/start/intro-to-paddle/which-countries-are-supported-by-paddle>,
  accessed 2026-11-22). Caveat: eligibility is confirmed only at the
  end of Paddle's manual account verification, not at signup.
- **Stripe direct — ineligible as a local (either form).** Stripe's
  supported-countries page lists neither Serbia nor Bosnia; account
  country is where the business is based, so neither an individual nor
  an RS/BA-registered company can open a Stripe account
  (<https://stripe.com/global>, accessed 2026-11-22). The documented
  workaround is forming a foreign entity: Stripe Atlas creates a
  Delaware C-corp or LLC for $500 from anywhere in the world, no US
  residency needed (<https://stripe.com/atlas>, accessed 2026-11-22) —
  that US entity is then US-based for Stripe's purposes. Ongoing
  costs: $100/yr registered agent after year one, Delaware franchise
  tax (C-corp) or annual tax (LLC), and US federal/state filings
  remain the founder's responsibility (same page).
- **Stripe Managed Payments — ineligible from RS/BA, eligible via a US
  entity.** Supported business locations are US, CA, 30 European
  countries, and 4 APAC ones; Serbia and Bosnia are absent, and the
  review "considers … geography", so this is a hard list, not a
  judgement call (<https://docs.stripe.com/payments/managed-payments/eligibility>,
  accessed 2026-11-22). The Lemon Squeezy wind-down post explicitly
  tells sellers in unsupported countries to either migrate to another
  provider or form an entity in a supported one. Regional extra: MP
  cannot serve **customers** in Kosovo (restricted-country list, same
  page) — a buyer pool Paddle does serve.
- **Lemon Squeezy — closed to everyone.** New sign-ups ended and the
  platform stops processing on 2027-03-31
  (<https://www.lemonsqueezy.com/blog/saying-goodbye-to-lemon-squeezy>,
  accessed 2026-11-22). Included only to close it out.

## 3. Integration map against our key system (v0.9.0 hook points)

What the checkout must deliver, and where it plugs in today:

1. **License-key issuance webhook.** The purchase→key pipeline lives in
   `scripts/license_webhook.py`: `verify_paddle_signature()` (line 69,
   HMAC-SHA256 over `{ts}:{body}`, 300 s tolerance) authenticates the
   Paddle `transaction.completed` delivery, `extract_purchase()`
   (line 105) pulls buyer email + product id, `_coverage()` (line 140)
   maps Paddle product id → covered version (`"1"`/`"forever"`), and
   `handle_event()` (line 212) calls the owner-side keygen
   (`scripts/license_keygen.py`, Ed25519, seed on offline media) and
   appends to the append-only `issued.jsonl` log (`IssueLog`, line
   158) with idempotent redelivery. A different provider must
   reproduce exactly this contract: an authenticated success event
   carrying email + product, everything else stays.
2. **Storefront wiring.** `site/assets/js/store-config.js` (lines 1–29)
   is the single go-live constant block (`mode`, `clientToken`,
   `priceId`, `productId`); `productId` must match a webhook product-map
   key or "purchases never mint keys" (per
   [docs/dev/license-ops.md](../dev/license-ops.md)).
   `site/assets/js/store.js` (lines 20–23) arms `#buy-cta` /
   `[data-store-buy]` as the Paddle.js overlay checkout. Swapping MoR
   = rewrite these two small files + the signature-verification half
   of the webhook; the runner-up (Polar) wiring is already documented
   in license-ops.md:112–120, and Stripe's equivalent would be a
   `checkout.session.completed` / Managed-Payments event with a
   Stripe-side signature scheme.
3. **Refund → key path.** There is **no revocation by design** —
   ADR-0005's "honest, offline, never-gates-dictation" license
   (documented under "Not built (deliberately)" in
   [docs/dev/license-ops.md](../dev/license-ops.md)). Refunds end the
   support entitlement, not the key; the manual runbook is
   [docs/dev/paddle-refunds.md](../dev/paddle-refunds.md) (14-day,
   no-questions policy). If that stance ever changes, the hook exists:
   Paddle emits refund webhooks for full/partial refunds (the refund
   help article above links `transaction adjustment` notifications),
   and `IssueLog`'s per-email/per-transaction records
   (`scripts/license_lookup.py`, line 24) are the index a revocation
   list would build on. A revocation path would also need an app-side
   check — which the shipped verifier (`fluidvoice/license.py`,
   verify-only, offline) does not have; that is a product-principles
   change, not an integration detail.
4. **Purchase-recovery UX.** `scripts/license_lookup.py` re-sends lost
   keys by email substring or transaction/event id from `issued.jsonl`
   (support-side). Buyer-facing recovery is the receipt email + the
   guide at [docs/guides/license.md](../guides/license.md) (lost-key
   and refund terms, lines 41–52). Activation itself:
   `sayit-ermano license <KEY>` (`fluidvoice/cli.py:133`), keyfile at
   `~/.config/sayit-ermano/license.key` 0600
   (`fluidvoice/license.py:227`, path from `fluidvoice/paths.py:102`).

Net: **the checkout choice is deliberately shallow in our stack.** The
MoR-specific surface is ~2 JS files + one signature verifier + the
product map; keys, verification, recovery, and activation are
provider-independent. That is the integration reason the provider can
be chosen on fees/eligibility/tax grounds alone.

## 4. Recommendation and top three risks

**Recommendation: stay on Paddle; do not build the direct column.**
For this seller, the textbook MoR-vs-direct trade-off is distorted by
eligibility: "direct Stripe" is only reachable through a US entity
whose fixed overhead ($500 Atlas + agent + Delaware taxes + US
filings + payout repatriation) is front-loaded before the first sale,
while its realistic per-sale fee for our mostly-international buyers
(~5.4% + 30¢ after international + FX add-ons) lands within ~0.4
points of Paddle's 5% + 50¢ — a gap the cost model already prices as
minor (±25% on the processor percentage moves base-price break-even
by only ~400 of ~3,000 sales). Direct also re-loads VAT/OSS remittance
and chargeback liability onto the seller, which the cost model's
direct column underprices by construction. Stripe Managed Payments is
the wrong direction too: unavailable from RS/BA, ~6.4%+ fees even via
a US entity, and it cannot serve Kosovo buyers. Keep Polar (already
documented in license-ops.md) as the named fallback MoR; revisit
direct only if volume makes the entity overhead amortize (roughly:
when monthly processor-fee savings clear the entity's fixed monthly
costs — at a €39 price that is on the order of 300+ sales/month,
above every modeled bracket).

Top three risks of this recommendation:

1. **Single-provider dependency with discretionary review.** Paddle is
   the only eligible first-party MoR in the codebase, and both account
   activation and payout release sit behind Paddle's manual
   risk/compliance review, which can ask "for further information …
   from any location or jurisdiction" (countries article above). A
   frozen payout mid-month is a cash-flow event at our scale.
   Mitigation: keep the Polar runner-up wiring warm
   (license-ops.md:112–120) and keep the webhook/provider surface
   minimal (it already is).
2. **Payout cadence and currency drag.** Paddle pays monthly, minimum
   $100 balance, sent by the 15th plus up to 3 working days, in USD
   via wire/Payoneer with a possible $15 SWIFT fee — so up to ~3
   weeks of revenue latency plus USD→EUR/banked-currency conversion
   outside our control (payout article above). The cost model's fixed
   monthly costs are EUR; the mismatch is unhedged.
3. **MoR fee and policy drift.** The 5% + 50¢ rate and the tax
   coverage are Paddle's policy, not a contract term we control; the
   refund mechanics also hard-stop at 120 days for cards (refund
   article above), and the cost model conservatively assumes fees are
   not clawed back on refunds. A +1 point fee change moves base-price
   break-even by only ~400 sales, but combined with risk 1 it argues
   against ever treating the checkout layer as settled — re-verify
   the cells in section 1 before any pricing decision, as
   [PRICING.md](../PRICING.md) already mandates for the rate itself.

## Method and limits

- Every fee/feature/eligibility claim above was read from the cited
  official page on 2026-11-22; marketing claims are labeled as such
  ("up to 4x faster"). Paddle's published 5% + $0.50 rate matches the
  2026-11-21 verification recorded in
  [PRICING.md](../PRICING.md).
- Not verified (and not claimed): Paddle's fee treatment on refunds
  (official article silent → cost model stays conservative); Stripe
  Tax Basic's per-transaction price (page truncated at fetch; only
  Tax Complete's $90/mo floor is cited); individual-vs-entity
  distinctions inside Paddle's verification process, which the public
  docs do not document — signup + verification is the only ground
  truth.
- No demand, conversion, or tax advice is implied. The entity-cost
  arithmetic in section 4 is order-of-magnitude planning, same class
  as the cost model's labeled assumptions.
