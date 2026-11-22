# Checkout options: merchant of record vs direct processor — monetization plan question R4

- Date: 2026-11-22
- Status: research evidence for monetization plan question 4
  ([plan](../plans/2026-09-11-product-excellence-and-monetization-plan.md),
  "How does buying work?")
- Scope: the checkout that sells the license key, against the offline
  key system that shipped in v0.9.0 (issues #16, #17,
  [ADR-0005](../adr/ADR-0005-commercial-sku.md)). Sibling notes: the
  rights inventory (question 2,
  [R2](2026-11-22-rights-and-redistribution-inventory.md)), the cost
  model (question 3, [R3](2026-11-22-one-time-purchase-cost-model.md)).
- Rule: every fee/feature/eligibility claim below cites an official URL
  fetched **2026-11-22** (one access date for the whole note). Anything
  not verifiable today is marked **OPEN**. Read-only research: no code
  changed, no account created, no onboarding started.

## Summary for the commercial decision

1. **Direct processing is not available to this seller.** Stripe
   supports neither Serbia nor Bosnia and Herzegovina, for individuals
   or registered entities alike; its merchant-of-record product
   (Stripe Managed Payments, the Lemon Squeezy successor) lists neither
   country either. Lemon Squeezy is closed to new sign-ups and shuts
   down 2027-03-31. The live "MoR vs direct" choice is therefore
   **Paddle vs Polar**, and a direct path would first need a legal
   entity in a supported country — not evaluated here (OPEN-1).
2. **Both remaining MoRs accept the region.** Paddle uses an
   exclusion list that contains neither Serbia nor Bosnia, and
   explicitly onboards individuals and sole traders. Polar lists both
   countries in its payout coverage and answers "any individual or
   company" for Stripe-invite-only countries ([§2](#2-seller-eligibility-serbia--bosnia-and-herzegovina)).
3. **Fees converge at our price point.** Paddle: 5% + 50¢, all-in at
   checkout ($2.95 on $49). Polar: 5% + 50¢ **+ 1.5% on non-US
   cards** + per-payout fees. Stripe's hypothetical list rate is
   $1.72 on a US domestic card but ≈ $3.19 once international-card,
   currency-conversion and Tax fees apply — before the seller's own
   VAT registrations and filings ([§1](#1-two-columns-merchant-of-record-vs-direct-processor)).
4. **Refunds do not return the MoR's fee at Polar (stated) and, by
   Paddle's worked example, only pro rata at Paddle** (OPEN-2), and
   both pass chargeback fees through: Paddle 20 USD/GBP/EUR per
   chargeback regardless of outcome, Polar $15 per dispute.
5. **Recommendation: keep ADR-0005's rail (Paddle), keep Polar as the
   pre-vetted fallback, do not build a direct-Stripe path.** The top
   three risks are approval friction on a `github.io` subpath, a
   fulfilment gap (keys are emailed by hand today, refund events are
   ignored by the webhook), and low-volume payout/refund mechanics
   ([§4](#4-recommendation-and-top-three-risks)).

---

## 1. Two columns: merchant of record vs direct processor

Four columns are kept because the two MoRs named in the brief are in
different states: Lemon Squeezy is a wind-down, so its cells are
"n/a" by fact rather than by choice. Polar is included because
ADR-0005 already names it the runner-up.

### 1.1 Fees, tax, refunds

| | **MoR — Paddle** | **MoR — Polar** | **MoR — Lemon Squeezy** | **Direct — Stripe** |
|---|---|---|---|---|
| Published fee | 5% + 50¢ per checkout transaction; no monthly fees ([pricing](https://www.paddle.com/pricing)). Worked example in the docs bills the fee on the **tax-inclusive** total: 652.15 → fee 33.11 ([adjustments guide](https://developer.paddle.com/build/transactions/create-transaction-adjustments)) | 5% + 50¢ on the free Starter plan (default for organisations created on/after 2026-05-27) **+ 1.5% for non-US cards**; fee shown on the tax-inclusive total ($37.50 → $2.94 all-in on Starter) ([fees](https://polar.sh/docs/merchant-of-record/fees)) | Legacy: 5% + 50¢ "with edge cases where small additional fees may need to be applied" — **no new sign-ups** ([homepage](https://www.lemonsqueezy.com/)) | 2.9% + 30¢ domestic cards; + 0.5% manually entered; + 1.5% international cards; + 1% if currency conversion is required; "country-specific rates" exist ([pricing](https://stripe.com/pricing)) |
| Who charges and remits VAT/sales tax | Paddle, as MoR: "registered in over 100 jurisdictions", calculates, charges, remits, issues invoices; "all the tax-related risk rests with Paddle, not with you" ([VAT on your behalf](https://www.paddle.com/help/sell/tax/how-paddle-handles-vat-on-your-behalf)). Seller→Paddle is a B2B reverse-charge sale ([payout VAT](https://www.paddle.com/help/manage/get-paid/should-i-charge-paddle-vattax-for-payouts)) | Polar, as MoR: "we take on the liability for international sales taxes"; "all payments from customers are made to Polar (US)" ([supported countries](https://polar.sh/docs/merchant-of-record/supported-countries)) | n/a — closed to new sellers | **The seller.** Stripe Tax charges 0.5% per transaction where you are registered (Tax Basic) or $0.50 per API transaction; "You must file and remit the tax you collect for every location where you're registered"; automated filing exists for the US and "selected registrations in Ireland and Canada" only ([pricing](https://stripe.com/pricing), [file and remit](https://docs.stripe.com/tax/filing)) |
| Refund mechanics | Full or partial from the dashboard; card refunds 3–5 working days, PayPal ~48 h; cards refundable only on transactions < 120 days old, PayPal < 179 days ([issue refunds](https://www.paddle.com/help/manage/your-customers/how-do-i-issue-refunds)). "Most refunds for live accounts require approval from Paddle" (`pending_approval`); auto-approved when the account is verified, the refund is ≤ 400 USD or equivalent, **below your balance**, and not a bank transfer ([adjustments guide](https://developer.paddle.com/build/transactions/create-transaction-adjustments)) | Full or partial; "the initial transaction fees are not refunded to you"; Polar may refund on its own initiative up to 60 days after purchase and revoke benefits, "even if you have a 'no refunds' policy" ([fees](https://polar.sh/docs/merchant-of-record/fees), [refunds](https://polar.sh/docs/features/refunds)) | n/a | API/dashboard refund; "Stripe's processing fees from the original transaction aren't returned"; refunds draw on the available balance and are held pending if it is insufficient ([refunds](https://docs.stripe.com/refunds)) |
| Fee returned on refund? | Not stated in prose. **OPEN-2**: the docs' refund example records `fee: 13.54` on a 266.66 partial refund — exactly the pro-rata share of the original 33.11 fee (33.11 × 266.66 / 652.15 = 13.54), which indicates a pro-rata reversal. The [R3 model](2026-11-22-one-time-purchase-cost-model.md) keeps the conservative "fee not returned" assumption. | No — stated ([fees](https://polar.sh/docs/merchant-of-record/fees)) | n/a | No — stated ([refunds](https://docs.stripe.com/refunds)) |

### 1.2 Payouts, fraud, chargebacks, review friction

| | **MoR — Paddle** | **MoR — Polar** | **MoR — Lemon Squeezy** | **Direct — Stripe** |
|---|---|---|---|---|
| Payout schedule | Monthly, not on demand: balance over threshold on the 1st converts to a payout; sent by the 15th; up to 3 working days to land ([get paid](https://www.paddle.com/help/manage/get-paid/when-and-how-do-i-get-paid)) | Manual withdrawals, after a **7-day settlement delay** (default for organisations created on/after 2026-05-12); paid out via Stripe Connect Express ([payouts](https://polar.sh/docs/features/finance/payouts)) | n/a | First payout typically 7–14 days after the first live payment; later payouts follow the account's schedule ([payouts](https://docs.stripe.com/payouts)) |
| Minimum / fees | Minimum $100 (or £/€100 by balance currency), carried over below that. Wire or Payoneer; no Paddle fee for most countries, "$15 SWIFT fee may be applicable" for some. A wire is free only when the payout currency is the bank country's local currency (USD→US via ACH, EUR→Eurozone via SEPA, …) — otherwise "$/€/£15". Up to 1.5% conversion margin if the payout currency differs from the balance currency ([get paid](https://www.paddle.com/help/manage/get-paid/when-and-how-do-i-get-paid), [payout fees](https://www.paddle.com/help/manage/get-paid/is-there-a-fee-taken-for-payouts)) | Minimum USD $10 / EUR $13 / **RSD $40 / BAM $40** equivalents; "Stripe payout fees: $2 per month in which you have at least one active payout, 0.25% + $0.25 per payout, cross-border (currency conversion) 0.25% within the EU, up to 1% elsewhere". Reverse invoice from the seller to Polar needed for accounting ([payouts](https://polar.sh/docs/features/finance/payouts)) | n/a | Account-specific; not applicable while the region is unsupported |
| Payout currency | AUD, GBP, CAD, CNY, CZK, DKK, EUR, HUF, PLN, ZAR, SEK, CHF, USD — **no RSD, no BAM**; the seller's bank converts ([local currency](https://www.paddle.com/help/manage/get-paid/can-i-be-paid-in-my-local-currency)) | Payout thresholds exist for RSD and BAM (above), so local-currency payout is possible via Stripe Connect Express ([payouts](https://polar.sh/docs/features/finance/payouts)) | n/a | n/a |
| Fraud screening | Paddle scores every transaction and rejects high-score ones ([fraud prevention](https://www.paddle.com/help/manage/risk-prevention/how-does-paddle-prevent-fraud)) | Continuous transaction monitoring and account reviews ([account reviews](https://polar.sh/docs/merchant-of-record/account-reviews)) | n/a | Seller's responsibility (Radar and rules configured by the seller — pricing not verified today, **OPEN-3**) |
| Chargeback liability | "the dispute is raised against Paddle rather than you"; Paddle deducts the transaction amount **and** the fee from the seller balance; Paddle's "Dispute Defense System automatically contests chargebacks … additional evidence submitted by sellers is not required or accepted"; if won, the amount returns but "the chargeback fee is not refunded"; **fee 20 USD/GBP/EUR (40 CAD/AUD)**; acceptable rate below 0.65% of transaction volume ([chargebacks](https://www.paddle.com/help/manage/risk-prevention/understanding-chargebacks-with-paddle)) | "$15 per dispute regardless of outcome"; accounts held to a 0.4% chargeback rate; escalation order: refund transactions, hold payouts until the 120-day window closes, pause payments, block the account ([fees](https://polar.sh/docs/merchant-of-record/fees), [account reviews](https://polar.sh/docs/merchant-of-record/account-reviews)) | n/a | "Stripe debits your balance for the payment amount and dispute fee"; the seller gathers and submits the evidence ([disputes](https://docs.stripe.com/disputes)) |
| Account-review friction | Three phases: domain review, business verification "not required for individuals or sole traders", identity verification ([account verification](https://www.paddle.com/help/start/account-verification/what-is-account-verification)). Domain review needs a live HTTPS site with product description, pricing, features, T&Cs, refund and privacy policy, and the brand/legal name in the T&Cs; "automatically approve most domain submissions", manual review "estimated 5-7 business days"; selling allowed only from approved domains ([domain review](https://www.paddle.com/help/start/account-verification/what-is-domain-verification)). Manual reviews: business 2–4 business days, identity 1–3 business days ([business](https://www.paddle.com/help/start/account-verification/what-is-business-verification), [identity](https://www.paddle.com/help/start/account-verification/what-is-identity-verification)) | Review **before** the first payment can be accepted; checklist = product, checkout, Stripe Identity KYC of the organisation owner, payout account, product description, reachable website, verified support email; "Initial reviews can take up to 14 days"; later reviews at sales thresholds can hold payouts ("Held for review"); support replies expected within 48 hours, "Repeated unresponsiveness leads to offboarding" ([account reviews](https://polar.sh/docs/merchant-of-record/account-reviews), [checklist](https://polar.sh/docs/merchant-of-record/review-checklist)) | n/a | n/a for the region |
| Fit for our offer shape | AUP: built for software companies; "If your company's primary offering is human services (such as consultation, support, or design services) … Paddle is not a good fit"; prohibited where "there is no bona fide software or service sold, including … donations, crowdfunding, community access" ([AUP](https://www.paddle.com/help/start/intro-to-paddle/what-am-i-not-allowed-to-sell-on-paddle)). Our SKU is software **plus** support, so the store page must lead with the software, not the pledge | Polar "resells your digital goods and services" and reviews every product against its Acceptable Use Policy ([account reviews](https://polar.sh/docs/merchant-of-record/account-reviews)) | n/a | Managed Payments (MoR) requires a "fully automated digital product": "If your service involves human intervention … it doesn't qualify" ([eligibility](https://docs.stripe.com/payments/managed-payments/eligibility)) — irrelevant for direct Stripe, relevant if anyone proposes Managed Payments |

### 1.3 What the fee columns mean at our prices

Arithmetic on the list rates above (not a quote; actual statements at
onboarding govern):

| $49 sale | Fee | Net to project |
|---|---:|---:|
| Paddle, 5% + 50¢ | $2.95 | $46.05 (matches [PRICING.md](../PRICING.md)) |
| Polar Starter, US card | $2.95 | $46.05, minus per-payout fees |
| Polar Starter, non-US card (+1.5%) | $3.69 | $45.31, minus per-payout fees |
| Stripe, US domestic card in USD (hypothetical) | $1.72 | $47.28, before Tax and the seller's own compliance |
| Stripe, non-US card + currency conversion + Tax Basic (hypothetical) | $3.19 | $45.81, before the seller's own compliance |

At the R3 base volume (1,500 sales over 24 months) the Paddle–Polar
difference for a non-US-card buyer is ≈ $1,110 gross over the window;
the [R3 sensitivity table](2026-11-22-one-time-purchase-cost-model.md)
already shows processor percentage moving break-even by only ~400
sales against a ±25% perturbation. Fee differences are a second-order
input here; eligibility and fulfilment are first-order.

---

## 2. Seller eligibility: Serbia / Bosnia and Herzegovina

Verified against each provider's own list today, separately for
individuals and registered entities. "Region" is read as the two
countries the project's translations and the
[productization check](2026-11-21-productization-verification.md) name:
Serbia (the seller per ADR-0005) and Bosnia and Herzegovina.

| Provider | Serbia — individual / sole trader | Serbia — registered entity | Bosnia & Herzegovina | Basis |
|---|---|---|---|---|
| **Paddle** | **Supported** (not excluded). Onboarding path exists: business verification "not required for individuals or sole traders"; the individual does the identity check | **Supported** (not excluded). Needs government-issued registration documents and an ownership breakdown for holders of ≥ 25%, plus identity verification of at least one owner | **Supported** (not excluded) — same list | Exclusion-list model: "Paddle works with software businesses anywhere in the world with the exception of the unsupported countries listed below" — Afghanistan, Antarctica, Belarus, Burma, Central African Republic, Cuba, Crimea, DR Congo, Donetsk, Haiti, Iran, Iraq, Kherson, Libya, Luhansk, Mali, Netherlands Antilles, Nicaragua, North Korea, Russia, Somalia, South Sudan, Sudan, Syria, Venezuela, Yemen, Zaporizhzhia, Zimbabwe ([countries](https://www.paddle.com/help/start/intro-to-paddle/which-countries-are-supported-by-paddle)); verification phases ([account verification](https://www.paddle.com/help/start/account-verification/what-is-account-verification)) |
| **Polar** | **Supported.** "residents or businesses"; "any individual or company operating in our supported countries can receive payouts from Polar even if Stripe standalone is invite-only there"; owner passes Stripe Identity; "For individual accounts, yes" a personal bank account works | **Supported.** Same list; "For business accounts, you will need a business bank account" | **Supported** — 🇧🇦 is in the same list; BAM payout threshold $40 | Inclusion list: "Polar uses Stripe Connect Express to issue payouts to residents or businesses in any of the countries below" — the list contains 🇷🇸 Serbia and 🇧🇦 Bosnia and Herzegovina ([supported countries](https://polar.sh/docs/merchant-of-record/supported-countries)); payouts ([payouts](https://polar.sh/docs/features/finance/payouts)) |
| **Lemon Squeezy** | **Not available** — to anyone | **Not available** | **Not available** | "Lemon Squeezy is no longer accepting new sign-ups and will shut down on March 31, 2027"; "Lemon Squeezy sign-ups are now closed" ([homepage](https://www.lemonsqueezy.com/)) |
| **Stripe (direct)** | **Excluded.** Neither country is in the supported list | **Excluded** | **Excluded** | "Stripe is currently supported in the following countries/regions" — the full list (Australia, Austria, Belgium, Brazil, Bulgaria, Canada, Côte d'Ivoire †, Croatia, Cyprus, Czech Republic, Denmark, Estonia, Finland, France, Germany, Ghana †, Gibraltar, Greece, Hong Kong, Hungary, India ‡, Indonesia ‡, Ireland, Italy, Japan, Kenya †, Latvia, Liechtenstein, Lithuania, Luxembourg, Malaysia, Malta, Mexico, Netherlands, New Zealand, Nigeria †, Norway, Poland, Portugal, Romania, Singapore, Slovakia, Slovenia, South Africa †, Spain, Sweden, Switzerland, Thailand, UAE, UK, US; † "extended network", ‡ "preview") has no Serbia and no Bosnia and Herzegovina; the page tells such businesses "Payments not supported yet. Register interest" and advertises Stripe Atlas for incorporating a US company ([stripe.com/global](https://stripe.com/global)) |
| **Stripe Managed Payments** (MoR; Lemon Squeezy's successor) | **Excluded** | **Excluded** | **Excluded** | "Your business must be based in one of the supported business locations" — CA, US; AT, BE, BG, CH, CY, CZ, DE, DK, EE, ES, FI, FR, GB, GR, HR, HU, IE, IT, LI, LT, LU, LV, MT, NL, NO, PL, PT, RO, SE, SI, SK; AU, HK, JP, SG ([eligibility](https://docs.stripe.com/payments/managed-payments/eligibility)) |

Plainly: **Stripe's lists exclude Serbia and Bosnia and Herzegovina**
— this is an absence from an inclusion list, not an inference. The
only way to reach Stripe is a legal entity in a supported country
(e.g. a US company via Atlas, which the page offers); that adds
incorporation, US filing and banking obligations this note does not
cost (**OPEN-1**).

Two caveats on the "supported" cells:

- Paddle's is an exclusion list that is "subject to change", and
  Paddle "may from time to time request further information before
  processing an order or releasing a payment from any location or
  jurisdiction". Verified as *not excluded today*; the final say is
  Paddle's verification, which no research can pre-empt.
- Serbia is outside the Eurozone, so a EUR wire from Paddle to a
  Serbian bank is an international SWIFT transfer with the $/€/£15
  fee plus the receiving bank's charges; Payoneer avoids the wire fee
  but carries Payoneer's own fees
  ([payout fees](https://www.paddle.com/help/manage/get-paid/is-there-a-fee-taken-for-payouts)).
  Whether PayPal is offered for Serbian payouts was not verified
  (**OPEN-4**).

Tax note that applies to every MoR row: the MoR handles the *buyer's*
VAT/sales tax. The payout the seller receives is business income in
the seller's own country (Paddle frames it as a B2B reverse-charge
sale to Paddle in the UK; Polar asks the seller to issue it a reverse
invoice). Serbian/Bosnian income-tax and any local VAT treatment of
that B2B supply is the seller's own obligation — **OPEN-5**, a local
accountant question, not a provider question.

---

## 3. Integration map against our key system

### 3.1 What v0.9.0 shipped (the hook points)

| Piece | File (v0.9.0) | Hook |
|---|---|---|
| Webhook receiver | [`scripts/license_webhook.py`](../../scripts/license_webhook.py) | `EVENT_TYPE = "transaction.completed"` (`:56`); `verify_paddle_signature()` (`:69`, HMAC-SHA256 over `{ts}:{body}` with the `Paddle-Signature` header, `h1`/`h2`, 300 s tolerance); `extract_purchase()` (`:105`, reads `data.customer.email`, `data.items[].price.product_id`, `occurred_at`); `_coverage()` (`:140`, product id → covered major via `LICENSE_PRODUCT_MAP`); `IssueLog` (`:158`, append-only JSONL, flock sidecar + fsync); `handle_event()` (`:212`, issues on first delivery, returns the **same** key on redelivery); HTTP layer `POST /webhook`, `GET /healthz` (`:260`) |
| Keygen (owner side) | [`scripts/license_keygen.py`](../../scripts/license_keygen.py) | `build_payload()` (`:83`: product, email, name, purchased, `v`); `issue_key()` (`:93`, `payload.signature` Ed25519); `load_seed()` (`:106`) |
| Offline verifier (ships) | [`fluidvoice/license.py`](../../fluidvoice/license.py) | `PUBLIC_KEYS` (`:46`); `parse_and_verify()` (`:174`); `import_key()` (`:227`); `status()` (`:252`); `status_text()` (`:239`) |
| Key storage | [`fluidvoice/paths.py`](../../fluidvoice/paths.py) | `license_file()` (`:102`) → `~/.config/sayit-ermano/license.key`, mode 0600 |
| CLI import | [`fluidvoice/cli.py`](../../fluidvoice/cli.py) | `license` subcommand (`:114`); `_cmd_license()` (`:133`) |
| Settings surface | [`fluidvoice/gtkui/settings_pages/about.py`](../../fluidvoice/gtkui/settings_pages/about.py) | License row (`:47`); `refresh_license_row()` (`:69`) |
| Support lookup | [`scripts/license_lookup.py`](../../scripts/license_lookup.py) | `lookup_entries()` (`:24`): email substring / transaction id / event id over the issue log |
| Checkout (site) | [`site/assets/js/store-config.js`](../../site/assets/js/store-config.js), [`site/assets/js/store.js`](../../site/assets/js/store.js) | `clientToken` / `priceId` / `productId`; `productId` "must be a key in the webhook's product map" (`store-config.js:13-15`); `customData` carries the funnel tags |
| Runbooks | [`docs/dev/license-ops.md`](../dev/license-ops.md), [`docs/dev/paddle-refunds.md`](../dev/paddle-refunds.md), [`docs/dev/store-emails.md`](../dev/store-emails.md), [`docs/guides/license.md`](../guides/license.md) | custody, sandbox test, refund flow, first-purchase email, buyer guide |

### 3.2 What the checkout must deliver

The issuance core is provider-agnostic: it needs a **server-to-server,
signed** event carrying a buyer email, a product id, a stable
transaction id and a timestamp. Everything else in `handle_event()`
(dedup, coverage, key minting, logging) is ours already.

| Requirement | Paddle (current) | Polar (fallback) | Stripe direct |
|---|---|---|---|
| Issuance event | `transaction.completed` — handled today | `order.created` / `order.paid`; signature via `webhook-id`, `webhook-timestamp`, `webhook-signature` headers (secrets created on/after 2026-09-08 use the Standard Webhooks scheme) ([events](https://polar.sh/docs/integrate/webhooks/events), [delivery](https://polar.sh/docs/integrate/webhooks/delivery)) | Checkout-session event — the event reference page was not reachable today (**OPEN-6**) |
| Code change to switch | — | Replace `verify_paddle_signature()` and the field names in `extract_purchase()`; issuance, log and app side unchanged (as `license-ops.md` §"If the store becomes Polar" already specifies) | Same swap, plus everything in the next rows |
| Built-in license keys | **Deprecated** — "License key generation and activation — Deprecated in Paddle Billing" ([feature comparison](https://developer.paddle.com/migrate/paddle-classic/learn/feature-comparison)); self-issue is the documented path | Native benefit (prefixes, expiry, activation limits, custom validation conditions) ([license keys](https://polar.sh/docs/features/benefits/license-keys)); [license-ops.md](../dev/license-ops.md) records Polar's key validation as online-only, so our offline Ed25519 key stays the in-app truth | None — build it |
| Receipt / VAT invoice | Automatic from Paddle ([store-emails.md](../dev/store-emails.md)) | Polar invoices the customer as MoR ([payouts](https://polar.sh/docs/features/finance/payouts)) | **Seller builds it** (receipts, invoices with the right VAT rate per buyer country) |
| Signed entitlement | Ours: `payload.signature` Ed25519, verified offline by `parse_and_verify()` — unchanged for any provider | Same | Same |

One consistency hook worth naming: `site/assets/js/store-config.js`
`productId` and the webhook's `LICENSE_PRODUCT_MAP` must carry the
**same** Paddle product id, or `_coverage()` raises and every purchase
answers 422 without minting (pinned by
`tests/test_license_webhook.py::test_unknown_product_is_422_and_logs_nothing`).

### 3.3 Refund → key revocation path

Design constraint first: **keys are never revoked.** ADR-0005 rules
out a kill switch, `license-ops.md` lists "Any form of key
revocation/kill switch" under *Not built (deliberately)*, and
`paddle-refunds.md` says the key keeps working after a refund while
"the support entitlement and the update-channel entitlement for future
majors" end. Because `fluidvoice/license.py` verifies fully offline,
a technical revocation does not exist to hook into — and under GPL it
should not.

So "refund → revocation" is a **bookkeeping** path: the entitlement
record must show the refund so support and the update channel can
honour it. Today that record is the issue log, and it does not learn
about refunds automatically:

- `handle_event()` handles only `transaction.completed`; every other
  event type is acknowledged with `200 {"ignored": …}`
  (`scripts/license_webhook.py:225-226`, pinned by
  `tests/test_license_webhook.py::test_other_event_types_are_acked_without_issuing`).
- Paddle delivers refunds and chargebacks as **adjustments**:
  `adjustment.created` / `adjustment.updated` with `action` =
  `refund` (status `pending_approval` → `approved` / `rejected`),
  `chargeback`, or `chargeback_warning`
  ([adjustment.created](https://developer.paddle.com/webhooks/adjustments/adjustment-created);
  "refunds and chargebacks are categorized under adjustments in Paddle
  Billing" — [dispute notifications](https://www.paddle.com/help/manage/risk-prevention/how-do-i-get-notified-about-disputes)).
- `paddle-refunds.md` therefore relies on the owner appending a
  manual note after clicking Refund.

Proposed hook (docs-only today; the smallest change that closes it):
add a second branch in `handle_event()` for `adjustment.created` /
`adjustment.updated` whose `data.action` is `refund`, `chargeback`
or `chargeback_warning` and whose status has reached `approved`
(behind `pending_approval`); look
the transaction up with the existing `IssueLog.find(transaction_id)`
and append one line to the same append-only log
(`{"type": "refund" | "chargeback", "transaction_id", "action",
"amount", "ts"}`). `license_lookup.py` then prints the state next to
the key; no app-side change, no key change, ADR-0005 intact. Polar's
equivalents are `order.refunded` / `refund.created`
([events](https://polar.sh/docs/integrate/webhooks/events)).

Two mechanics to write into the runbook, not the code:

- **Paddle approval gate.** A refund is auto-approved only when ≤ 400
  USD and **below your balance**. At launch the balance is small; a
  refund of an already-paid-out sale goes to `pending_approval` until
  Paddle reviews it. The public promise ("14 days, no questions")
  is about *our* decision; Paddle's review adds latency the refund
  page should mention.
- **Chargeback window.** Polar states that customers "can file
  chargebacks up to 120 days after a transaction"
  ([account reviews](https://polar.sh/docs/merchant-of-record/account-reviews));
  the Paddle pages read today give no window. Each chargeback
  carries the 20 USD/GBP/EUR fee. The issue log should carry the
  chargeback state for the same reason it carries the refund state.

### 3.4 Purchase-recovery UX

| Scenario | Today (v0.9.0) | Gap |
|---|---|---|
| Paid, waiting for the key | The webhook mints and logs the key; **the owner composes the delivery email by hand** from the issue-log row ([store-emails.md](../dev/store-emails.md)) | No automated sender — the webhook returns the key in its JSON response and logs it, nothing mails it. A buyer can pay and wait on a human |
| Lost key / "which key did I get?" | `scripts/license_lookup.py <email|txn>` re-sends the **same** key; redelivery of the webhook returns the same key (`license_webhook.py:233-237`) | None in the lookup; none self-serve |
| Reinstall / new machine | The key file is machine-local (`paths.license_file()`); the buyer re-imports the same key — no device count, no deactivation, fully offline | Deliberate; the `sayitermano://license/KEY` deep link is listed as not built (`license-ops.md`) |
| Receipt / VAT invoice | Paddle sends it automatically on `transaction.completed` ([store-emails.md](../dev/store-emails.md)) | None |
| Refund request | Reply to the order email, a "Refund request" issue, or support email; processed in the Paddle dashboard ([paddle-refunds.md](../dev/paddle-refunds.md)) | The manual note step above |
| Corrupted key file | Reported as "no license"; re-import fixes it ([guides/license.md](../guides/license.md)) | None |

---

## 4. Recommendation and top three risks

**Recommendation: keep Paddle (ADR-0005 decision 3), keep Polar as the
pre-vetted fallback, and spend no engineering on a direct-processor
path.**

The reasoning, without adornment:

- The direct column is closed to this seller's region; reopening it
  means a foreign entity first. The structural "MoR vs direct" tax
  trade the brief asks about is therefore not on the table — an MoR
  is the only checkout this seller can open.
- Between the two open MoRs, Paddle is cheaper on every non-US-card
  sale (no +1.5%), publishes a shorter review (most domain reviews
  auto-approved, a manual one "estimated 5-7 business days", vs
  Polar's "up to 14 days" for the initial review), and is the one
  the v0.9.0 pipeline, site and runbooks are wired for.
  Polar's advantages — local-currency payout thresholds for RSD and
  BAM, native license keys, a customer portal — are either not needed
  (we self-issue offline keys) or minor (the payout currency).
- What should change is not the provider but two fulfilment gaps
  identified in [§3](#3-integration-map-against-our-key-system):
  automated key delivery and adjustment-event bookkeeping.

Top three risks:

1. **Approval on a shared subdomain.** The store is
   `acailic.github.io/SayItErmano/`. Paddle's domain review requires
   a live HTTPS site with the product, pricing, T&Cs, refund and
   privacy policies and the seller's brand/legal name in the T&Cs,
   and only approved domains may launch a checkout; Polar similarly
   requires a reachable website and a verified support email. A
   `github.io` path is a domain the project does not control at the
   registry, and neither provider states how it treats such hosts.
   Mitigation: run verification early (it is on the #17 list), keep
   `site/terms.html`, `site/refund.html` and `site/privacy-policy.html`
   one click from the store CTA, and have a custom domain budgeted as
   the fallback. Residual: **OPEN-7**.
2. **Fulfilment gap at the moment of truth.** Keys are emailed by a
   human after the webhook logs them; nothing sends mail, and refund
   and chargeback events are acknowledged and dropped. A launch-day
   spike, a holiday, or a missed refund turns into paid-without-key
   or refunded-without-record tickets, which is also what the R3
   model prices as the dominant cost (support minutes). Mitigation:
   automate the delivery email from the webhook host (the template
   exists), add the adjustment branch in [§3.3](#33-refund--key-revocation-path).
3. **Low-volume money mechanics.** Payouts are monthly with a $100
   minimum; a EUR wire to a Serbian bank costs $/€/£15 plus the
   receiving bank's fee (Payoneer sidesteps it); RSD is not a payout
   currency; refunds need Paddle approval unless below the balance;
   each chargeback costs 20 USD/GBP/EUR whatever the outcome, and a
   rate above 0.65% triggers a process review. At $46.05 net per sale
   these are small in absolute terms but they hit hardest exactly in
   the first months, when the balance is thin. Mitigation: set
   Payoneer as the payout method, keep the $100 threshold (three
   sales), and state the Paddle review latency on the refund page.

---

## Method and limits

- Read-only research: no code changed; this note is the deliverable.
- Pages were fetched 2026-11-22 with a plain HTTP client (no
  JavaScript execution). Where a page renders its list client-side
  (Stripe Managed Payments), the page's Markdown sibling
  (`…/eligibility.md`) was read instead. All URLs are the providers'
  own documentation; no third-party summary was trusted for any
  fee or eligibility claim.
- No account was created, no onboarding started, no sandbox
  transaction run. Fees are the providers' published list rates;
  "the actual statements at onboarding govern".
- Fee arithmetic in [§1.3](#13-what-the-fee-columns-mean-at-our-prices)
  is on the list rates, excluding VAT the buyer pays on top.
- The Stripe event reference and the European Commission's VAT /
  One-Stop-Shop pages did not respond during this session; the
  direct column therefore omits the event name and the EU
  registration duty a non-EU seller would carry (**OPEN-6**,
  **OPEN-8**).

## OPEN items

| # | Question | Why open | Suggested resolution |
|---|---|---|---|
| OPEN-1 | Cost and obligations of a foreign entity (e.g. Stripe Atlas US company) to reach Stripe directly | out of scope today; only matters if the MoR path is rejected | price only after an MoR rejection; compare against the MoR fee delta (~$1/sale) |
| OPEN-2 | Does Paddle return its fee pro rata on refunds? | docs show it in a worked example, not in prose | one sandbox refund on a mapped product; compare `adjustment.totals.fee` with the transaction's; keep the R3 conservative assumption until confirmed |
| OPEN-3 | Stripe Radar / fraud tooling pricing for the direct column | page not read today | irrelevant unless OPEN-1 is pursued |
| OPEN-4 | Is PayPal offered as a Paddle payout method for Serbian sellers? | payout pages list wire and Payoneer only; country availability not shown | check Transfer Preferences at onboarding |
| OPEN-5 | Serbian/Bosnian tax treatment of the MoR payout (income tax; VAT on the B2B supply to Paddle/Polar) | legal/accounting, not provider documentation | local accountant before the first payout |
| OPEN-6 | Exact Stripe Checkout webhook event and signature scheme | event reference page unreachable this session | re-fetch `docs.stripe.com` event types before any direct-path design |
| OPEN-7 | Whether Paddle/Polar approve a `github.io` store domain | neither provider documents subdomain-of-shared-host treatment | submit the domain in verification; keep a custom domain as fallback |
| OPEN-8 | EU VAT registration duty for a non-EU seller under a direct processor | EC pages unreachable this session | read the Commission's VAT e-commerce pages before any direct-path design |

Related evidence: [productization verification](2026-11-21-productization-verification.md)
(the rail table this note re-verifies and extends),
[one-time purchase cost model](2026-11-22-one-time-purchase-cost-model.md)
(the fee inputs), [PRICING](../PRICING.md) (the approved offer),
[ADR-0005](../adr/ADR-0005-commercial-sku.md) (the decision record),
[license-ops](../dev/license-ops.md) (the runbook this note's
integration section maps).
