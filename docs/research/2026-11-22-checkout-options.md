# Checkout options: merchant of record vs direct processor

- Date: 2026-11-22
- Status: research evidence for monetization plan question R4
  ([plan](../plans/2026-09-11-product-excellence-and-monetization-plan.md),
  "How does buying work?") — input to, not a replacement for, the decision
  record [ADR-0005](../adr/ADR-0005-commercial-sku.md)
- Repository inspected: `linux` at `f6bdaec` (the v0.9.0 license-key
  implementation is in tree; the clone is single-commit, so file paths
  and line numbers are cited from the working tree)
- Rule: every fee/feature/eligibility claim cites an official URL
  fetched 2026-11-22; anything not verifiable today is marked **OPEN**.
  No account was created, no seller onboarding started, no purchase made.

## Summary for the commercial decision

1. **A direct processor is not available to this seller.** Stripe's
   supported-business list has no Serbia and no Bosnia and Herzegovina
   ([stripe.com/global](https://stripe.com/global), fetched today), and
   Stripe's own connected-account data gives RS and BA the `transfers`
   capability only — no `card_payments` (§2). "MoR vs direct" is
   therefore a real choice only for a seller with an entity in a
   supported country; for the current owner it is a choice among MoR
   providers.
2. **Stripe Managed Payments — the Lemon Squeezy successor — also
   excludes the region**, and its product rules rule out a bundle that
   includes human support (§2). Lemon Squeezy itself is closed to new
   sign-ups and stops processing 2027-03-31.
3. **Two MoR providers serve RS and BA for individuals and entities,
   verified from primary lists**: Paddle (by exclusion from its
   unsupported-countries list) and Polar (explicit Serbia and Bosnia
   entries,
   both entity types confirmed through Stripe's data). Gumroad is a
   third MoR; its RS/BA payout eligibility was not re-verified today
   (OPEN-3).
4. **Our key system is provider-agnostic after two functions** — the
   webhook signature check and the payload field mapper
   (`scripts/license_webhook.py:69`, `:105`). Everything downstream
   (Ed25519 minting, issue log, dedup, lookup, CLI import, About page)
   needs no change for any provider (§3).
5. **Recommendation: keep Paddle as primary (ADR-0005 stands), but
   promote Polar from "documented" to "sandbox-tested" fallback before
   go-live, and close the Stripe-direct question as "not eligible"**
   (§4). The top three risks are payout friction, MoR refund/chargeback
   autonomy, and single-provider concentration.

---

## 1. Two columns: merchant of record vs direct

"Merchant of record" (MoR) means the provider is the legal seller: it
resells the product, collects and remits VAT/sales tax, issues the
invoice, and owns the payment relationship. "Direct" means the seller
is the merchant of record on a processor's rails and carries those
obligations itself. Fees are the providers' published list rates; actual
rates at onboarding govern.

### 1.1 Merchant-of-record column

| Dimension | Paddle | Polar | Gumroad |
|---|---|---|---|
| Fee | 5% + 50¢ per checkout transaction; no monthly fee ([pricing](https://www.paddle.com/pricing)) | Starter 5% + 50¢, **+1.5% for international (non-US) cards**; Pro $20/mo → 3.8% + 40¢ ([fees](https://polar.sh/docs/merchant-of-record/fees)) | 10% + 50¢ direct; 30% via Discover marketplace ([pricing](https://gumroad.com/pricing)) |
| Who handles VAT/sales tax | Paddle: reseller of record; "collection, filing, and payment" of VAT/sales tax ([legal relationship](https://www.paddle.com/help/start/intro-to-paddle/the-legal-relationship-between-paddle-and-you), [VAT responsibilities](https://www.paddle.com/help/start/intro-to-paddle/how-paddle-is-able-to-take-on-your-vat-and-tax-responsibilities)) | Polar: "liability for international sales taxes" ([supported countries](https://polar.sh/docs/merchant-of-record/supported-countries)); invoices the buyer directly; seller issues a reverse invoice to Polar per payout ([payouts](https://polar.sh/docs/features/finance/payouts)) | Gumroad: MoR since 2025-01-01, "sales tax collection and remittance worldwide" ([pricing](https://gumroad.com/pricing)) |
| Refund mechanics | Seller issues full/partial refunds from the dashboard or API; card refunds 3–5 working days, PayPal ~48 h; card refunds only on transactions < 120 days, PayPal < 179 days ([how do I issue refunds](https://www.paddle.com/help/manage/your-customers/how-do-i-issue-refunds)) | Seller issues full/partial refunds; **Polar may refund at its own discretion up to 60 days** to prevent chargebacks, even under a no-refund policy; benefit revocation is selected by default ([refunds](https://polar.sh/docs/features/refunds)) | Not re-verified today (OPEN-3) |
| Are fees returned on refund? | Not stated on the pages fetched (OPEN-2) | **No** — "initial transaction fees are not refunded" ([fees](https://polar.sh/docs/merchant-of-record/fees)) | Not stated (OPEN-3) |
| Payout schedule | Monthly: balance converts on the 1st if over threshold, sent by the 15th, then up to 3 working days; **minimum $100**; no on-demand withdrawal ([when and how do I get paid](https://www.paddle.com/help/manage/get-paid/when-and-how-do-i-get-paid)) | **Manual withdrawal**; 7-day settlement delay for organizations created on/after 2026-05-12; payouts take 4–7 business days; min balance $10 (USD) ([payouts](https://polar.sh/docs/features/finance/payouts)) | Varies by country (OPEN-3) |
| Payout method / currency | Wire or Payoneer; payout currencies AUD, GBP, CAD, CNY, CZK, DKK, **EUR**, HUF, PLN, ZAR, SEK, CHF, USD — **no RSD, no BAM**; up to 1.5% conversion margin if payout ≠ balance currency ([local currency](https://www.paddle.com/help/manage/get-paid/can-i-be-paid-in-my-local-currency)); no payout fee beyond Paddle's fee, but a **15 USD/EUR/GBP SWIFT fee** when the payout currency is not the bank country's local currency, plus the bank's own charges ([payout fees](https://www.paddle.com/help/manage/get-paid/is-there-a-fee-taken-for-payouts)) | Stripe Connect Express; payout minimums exist for RSD and BAM (40 USD-equivalent each), i.e. local-currency payouts are contemplated ([payouts](https://polar.sh/docs/features/finance/payouts)); Stripe payout fees: $2 per active month, 0.25% + $0.25 per payout, cross-border conversion 0.25% EU / up to 1% elsewhere ([fees](https://polar.sh/docs/merchant-of-record/fees)) | Bank or PayPal by country (OPEN-3) |
| Fraud / chargeback liability | Dispute is raised **against Paddle**, but Paddle **deducts the transaction amount plus the fee** (20 USD/GBP/EUR or 40 CAD/AUD) from the seller's balance; defense is fully automated, seller evidence "not required or accepted"; pre-chargeback alerts trigger a proactive refund with the fee still applied; acceptable chargeback rate < 0.65% of volume ([understanding chargebacks](https://www.paddle.com/help/manage/risk-prevention/understanding-chargebacks-with-paddle)) | $15 per dispute regardless of outcome; Polar holds accounts to a **0.4% chargeback rate**; escalation: refund transactions, hold payouts, pause payments, block account ([fees](https://polar.sh/docs/merchant-of-record/fees), [account reviews](https://polar.sh/docs/merchant-of-record/account-reviews)) | Not re-verified today (OPEN-3) |
| Account-review friction | Three phases: domain review, business verification (**not required for individuals or sole traders**), identity verification; "periodic reviews of our sellers"; may "request further information before processing an order or releasing a payment" ([account verification](https://www.paddle.com/help/start/account-verification), [countries](https://www.paddle.com/help/start/intro-to-paddle/which-countries-are-supported-by-paddle)) | Self-submitted review before accepting payments: product, checkout, **KYC of the owner**, payout account, reachable website, verified support email; initial review "up to 14 days"; continuous reviews at sales thresholds; payouts "Held for review" meanwhile; 48-hour response expectation on support threads ([account reviews](https://polar.sh/docs/merchant-of-record/account-reviews)) | Not re-verified today (OPEN-3) |
| Buyer-side recovery | Seller can resend invoices/emails; Paddle handles billing-related support (invoicing, receipts, lost licences/delivery) as reseller ([legal relationship](https://www.paddle.com/help/start/intro-to-paddle/the-legal-relationship-between-paddle-and-you)) | **Customer portal always on**: purchase history, invoices, receipts, benefits incl. license keys; email one-time-code sign-in ([customer portal](https://polar.sh/docs/features/customer-portal)) | Not re-verified today (OPEN-3) |

**Stripe Managed Payments** is listed here for completeness because it is
the migration target Lemon Squeezy points its sellers to
([lemonsqueezy.com](https://www.lemonsqueezy.com/)): MoR services at
**3.5% per successful transaction in addition to Payments fees**
([stripe.com/pricing](https://stripe.com/pricing)), indirect-tax
compliance and remittance in 75+ countries, fraud, disputes and buyer
support. It is **not eligible** for this seller — see §2.

### 1.2 Direct column (Stripe Payments)

Listed for the structural comparison the plan asked for; it does not
apply to a RS/BA-based seller today (§2). All figures from
[stripe.com/pricing](https://stripe.com/pricing) unless noted.

| Dimension | Stripe Payments (direct) |
|---|---|
| Fee | 2.9% + 30¢ per successful domestic card transaction; **+1.5% international cards; +1% if currency conversion is required**; no monthly fee |
| Tax handling burden | **The seller is merchant of record.** Stripe Tax calculates and monitors thresholds; "You must file and remit the tax you collect for every location where you're registered" — register with each authority yourself, then file via Stripe's automated filing (US; selected Ireland/Canada registrations), a filing partner, or self-file ([file and remit](https://docs.stripe.com/tax/filing)). Stripe Tax price: Tax Basic 0.5% per transaction (no-code) or $0.50 per transaction (API); Tax Complete from $90/mo, 1-year contract ([stripe.com/pricing](https://stripe.com/pricing)) |
| Payout | Rolling/weekly/monthly to a bank account in a supported country ([stripe.com/pricing](https://stripe.com/pricing)); **no RS/BA payout possible** — not a supported business location |
| Fraud / chargeback liability | **Seller's.** "Stripe debits your balance for the payment amount and dispute fee"; $15 per dispute received, $15 per manual response (returned if won), $15 per appeal; optional Smart Disputes 30% of won disputes ([disputes](https://docs.stripe.com/disputes), [pricing](https://stripe.com/pricing)) |
| Integration effort | Hosted Checkout or Payment Links; fulfilment driven by a signed webhook (`Stripe-Signature` header, `t=`/`v1=` entries, HMAC-SHA256 over `{t}.{raw body}`, 5-minute default tolerance; automatic retries for up to three days in live mode) ([webhooks](https://docs.stripe.com/webhooks/signatures)). Our endpoint would need a new verifier and field mapper (§3.5) **plus** the tax registration/filing workflow, dispute-evidence workflow and invoicing that an MoR absorbs |

### 1.3 What the rates mean at our price points

Arithmetic over the list rates above, before any payout fees, FX or
tax-base effects. "Intl card" = Polar's +1.5% (a non-US buyer's card).

| Provider | Fee on $49 | Net on $49 | Fee on $39 | Net on $39 |
|---|---:|---:|---:|---:|
| Paddle 5% + 50¢ | $2.95 | **$46.05** | $2.45 | **$36.55** |
| Polar Starter, US card | $2.95 | $46.05 | $2.45 | $36.55 |
| Polar Starter, intl card | $3.69 | $45.32 | $3.04 | $35.97 |
| Gumroad 10% + 50¢ | $5.40 | $43.60 | $4.40 | $34.60 |
| Stripe Managed Payments (hypothetical) | $3.44 | $45.56 | $2.80 | $36.20 |
| Stripe direct, domestic card, no tax tool (hypothetical) | $1.72 | $47.28 | $1.43 | $37.57 |

The $46.05 matches [PRICING.md](../PRICING.md). Two caveats the table
hides: (a) Polar's own worked example computes its fee on the
**tax-inclusive** total ([fees](https://polar.sh/docs/merchant-of-record/fees));
Paddle's page says "per Checkout transaction" without stating the base —
OPEN-2, and it matters for the [R3 cost model](2026-11-22-one-time-purchase-cost-model.md)
if the fee is charged on a VAT-inclusive amount. (b) The direct row's
~$1.5/sale advantage is erased by one $15 dispute per ten sales, and
by Stripe Tax Basic ($0.25/sale) if used.

---

## 2. Seller eligibility — verified per region

Seller: an individual (sole trader) or a registered entity located in
**Serbia (RS)** or **Bosnia and Herzegovina (BA)**. Each row was checked
against the provider's own list today; "by exclusion" means the
provider publishes an unsupported-countries list and the country is not
on it.

| Provider | Individual, RS | Entity, RS | Individual, BA | Entity, BA | How verified |
|---|---|---|---|---|---|
| **Stripe Payments** (direct) | **Not supported** | **Not supported** | **Not supported** | **Not supported** | stripe.com/global lists supported business countries — Bulgaria, Croatia, Hungary, Romania and Slovenia are present, **Serbia and Bosnia are not**; the page itself offers only "Register interest" to businesses outside the list. Stripe's connected-account data shows RS and BA with capabilities `["transfers"]` only (no `card_payments`) — see method below |
| **Stripe Managed Payments** (MoR) | **Not supported** | **Not supported** | **Not supported** | **Not supported** | Supported business locations: Europe list is AT BE BG CH CY CZ DE DK EE ES FI FR GB GR HR HU IE IT LI LT LU LV MT NL NO PL PT RO SE SI SK — **no RS, no BA** ([eligibility](https://docs.stripe.com/payments/managed-payments/eligibility)). Additionally the product must be "fully automated … If your service involves human intervention … it doesn't qualify", and "Professional services, such as … tech support" are an unsupported category — **our SKU bundles priority support** ([PRICING.md](../PRICING.md)), so even an EU entity would need the offer split. Buyer-side: Kosovo is on the restricted-customer list |
| **Lemon Squeezy** (MoR) | Closed | Closed | Closed | Closed | "no longer accepting new sign-ups and will shut down on March 31, 2027" ([homepage](https://www.lemonsqueezy.com/)) |
| **Paddle** (MoR) | **Supported** (by exclusion) | **Supported** (by exclusion) | **Supported** (by exclusion) | **Supported** (by exclusion) | "works with software businesses anywhere in the world with the exception of the unsupported countries" — the list is sanctions-driven (Afghanistan, Belarus, Cuba, Iran, Iraq, North Korea, Russia, Syria, …) and contains neither RS nor BA; Paddle reserves the right to request more information "for risk and compliance purposes" ([countries](https://www.paddle.com/help/start/intro-to-paddle/which-countries-are-supported-by-paddle)). Account verification is three phases and **business verification is "not required for individuals or sole traders"** ([account verification](https://www.paddle.com/help/start/account-verification)). Paddle publishes no entity-type list, so entity support is inferred from the verification rules, not from an explicit list |
| **Polar** (MoR) | **Supported** (explicit) | **Supported** (explicit) | **Supported** (explicit) | **Supported** (explicit) | Payout list has explicit Serbia and Bosnia and Herzegovina entries; payments/MoR are supported globally except US-sanctioned countries ([supported countries](https://polar.sh/docs/merchant-of-record/supported-countries)). "Individual" vs "company" verified through Stripe's data: for a US platform with an Express **recipient** account and the `transfers` capability, both RS and BA list `entity_type_structures` = `individual`, `company` (sole proprietorship, single/multi-member LLC, private/public partnership/corporation), `non_profit` (see method) |
| **Gumroad** (MoR) | OPEN-3 | OPEN-3 | OPEN-3 | OPEN-3 | Pricing page says payout method "varies by country" and links a help article; the country list was not fetched today |
| **FastSpring** (MoR) | OPEN-3 | OPEN-3 | OPEN-3 | OPEN-3 | Fees are sales-led, no public country list fetched |

Plainly: **if the seller stays in Serbia or Bosnia, every "direct"
option is excluded, and the only MoR options verified today are Paddle
and Polar.**

### Method (reproducible)

- `stripe.com/global` was fetched as text and searched for the country
  names; the supported list is under "Stripe is currently supported in
  the following countries/regions".
- The Stripe verification page
  ([docs.stripe.com/connect/required-verification-information](https://docs.stripe.com/connect/required-verification-information))
  is an interactive form backed by public JSON endpoints — the same
  route Polar's own FAQ tells sellers to use. Queried today for the
  setup Polar prescribes (platform country **US** — Polar is the MoR —
  dashboard `express`, service agreement `recipient`, capability
  `transfers`):

  ```bash
  curl --get https://docs.stripe.com/_endpoint/get-requirement-selections-for-platform-country \
       --data-urlencode platformCountry=US
  # country_map.RS / country_map.BA:
  #   account_types ["express","custom"], capabilities ["transfers"], tos_types ["recipient"],
  #   entity_type_structures {individual, company{...}, non_profit{...}}
  curl --get https://docs.stripe.com/_endpoint/get-requirements-for-setups \
       --data-urlencode "account-setup-A[apiVersion]=v1" \
       --data-urlencode "account-setup-A[platformCountry]=US" \
       --data-urlencode "account-setup-A[accountCountry]=RS" \
       --data-urlencode "account-setup-A[dashboardType]=express" \
       --data-urlencode "account-setup-A[tosType]=recipient" \
       --data-urlencode "account-setup-A[legalEntityType]=individual" \
       --data-urlencode "account-setup-A[capabilities][0]=transfers"
  # requirements: tos_acceptance.ip, tos_acceptance.date, external_account  (same for BA, individual and company)
  ```

  Result: for RS and BA, individual and company alike, the published
  requirements are the terms-of-service acceptance and a bank account
  (`external_account`). For comparison, the same query for HR or SI
  returns `standard` accounts, `card_payments`, and the `full` service
  agreement — i.e. real Stripe availability.
- Limits of this method: it is Stripe's published matrix, not an
  onboarding decision; Polar and Stripe can still ask for more at KYC.
  The `company` structures Stripe lists for RS/BA are generic (LLC,
  private corporation…) and do not name Serbian or Bosnian legal forms
  (preduzetnik, d.o.o.) — the mapping at onboarding is OPEN-4.

---

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
| Signature check | `scripts/license_webhook.py:69` `verify_paddle_signature` | HMAC-SHA256 over `{ts}:{body}` against `Paddle-Signature: ts=,h1=`; 300 s replay tolerance (`DEFAULT_TOLERANCE`, `:57`); splits on `;` or `,` (`:77`) | Provider must sign the raw body with a shared secret and a timestamp. Paddle's current doc shows `ts=…;h1=…`, HMAC-SHA256 over `ts` + `:` + raw body — what the code does ([signature verification](https://developer.paddle.com/webhooks/about/signature-verification)) |
| Field extraction | `scripts/license_webhook.py:105` `extract_purchase` | Reads `data.id` (transaction), `data.customer.email`, `data.customer.name`, `items[].price.product_id`, `occurred_at` | Payload must carry: buyer email, product id per line item, transaction id, event time. Missing email/product → 422 (no key minted, provider retries) |
| Product → coverage map | `scripts/license_webhook.py:140` `_coverage`, env `LICENSE_PRODUCT_MAP` (`:60`) | `pro_xxx=1` or `pro_xxx=forever`; unmapped product stops issuance | Each purchasable SKU needs a stable provider product id that the owner maps to a coverage value |
| Key minting | `scripts/license_keygen.py:93` `issue_key`, payload built at `:83` `build_payload` | Signs `{product, email, purchased, v, [name]}` with the Ed25519 seed | Only needs the three fields above; provider-agnostic |
| Issue log / dedup | `scripts/license_webhook.py:158` `IssueLog`, lookup at `:191` `find` | Append-only JSONL keyed by `event_id` + `transaction_id`; redelivery returns the same key | Provider must redeliver failed webhooks with a *stable* event or transaction id (otherwise duplicates mint second keys) |
| HTTP surface | `scripts/license_webhook.py:262` `do_POST` (path `/webhook`), `:276` `do_GET` (`/healthz`), header read at `:270` | One path, one signature header name | A second provider needs its own path or a dispatcher on the signature header |
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
   multiple *different* event types for one purchase (Paddle sends
   `transaction.paid` *and* `transaction.completed`; Stripe sends
   `checkout.session.completed` *and* `payment_intent.succeeded`) must
   be filtered to exactly one (`:225` acks unknown types without
   issuing). Paddle documents at-least-once delivery and `event_id` as
   the dedup key — the same contract the code implements
   ([how webhooks work](https://developer.paddle.com/webhooks/about/how-webhooks-work)).
3. **Timely ack, then retries** — Paddle expects `200` within five
   seconds and retries live events up to 60 times over about two days
   (`60 × n^1.1` s backoff); after that the notification is `failed`
   and must be replayed by hand
   ([handle webhook delivery](https://developer.paddle.com/webhooks/about/respond-to-webhooks)).
   Our handler answers after one fsync'd append, so the 5 s bound is
   not a concern; the two-day retry window is the recovery budget for a
   webhook-host outage.
4. **Buyer email as the identity** — the payload's `email` is the
   support identity (`fluidvoice/license.py:194-196` requires it).
   Flows that do not surface an email on the webhook cannot mint keys.
5. **Transaction id as the join key** — refunds and support lookups
   join on `transaction_id` (`scripts/license_lookup.py:24-46`,
   `docs/dev/paddle-refunds.md` "After the refund"). The provider must
   expose it on the receipt the buyer sees.
6. **Coverage mapping** — one provider product id per SKU, mapped in
   `LICENSE_PRODUCT_MAP` (`scripts/license_webhook.py:308`
   `parse_product_map`). The $39 intro and $49 standard prices are two
   *prices* on one product; both can map to the same coverage.

### 3.3 Refund → key path (what exists, what does not)

- **Exists:** the money side is entirely in the MoR dashboard
  (`docs/dev/paddle-refunds.md` steps 1–5); after a refund the
  transaction id is looked up in the issue log and the ended support
  entitlement is noted in the support record.
- **Deliberately absent:** key revocation. `fluidvoice/license.py` has
  no revocation list, no expiry check beyond a future-date guard
  (`:203`), and `docs/dev/license-ops.md` "Not built (deliberately)"
  rules out a kill switch — the key never gates dictation (GPL,
  ADR-0005), so revoking it would revoke nothing the app enforces.
- **What the provider does on its own:** Paddle refunds proactively on
  pre-chargeback alerts and deducts the amount plus the fee
  ([chargebacks](https://www.paddle.com/help/manage/risk-prevention/understanding-chargebacks-with-paddle));
  Polar may refund within 60 days at its discretion and revokes its own
  benefits ([refunds](https://polar.sh/docs/features/refunds)). Neither
  is a request we see before it happens. **Consequence:** the support
  record can diverge from the provider's refund state. The runbook
  assumes a refund arrives through intake; provider-initiated refunds
  arrive only as a dashboard adjustment or a refund webhook nobody
  consumes today. A weekly reconciliation (dashboard export vs
  `issued.jsonl`) or a refund-event handler that annotates the issue
  log is the missing piece — OPEN-5.
- **If the store ever moves to a direct processor:** the refund click
  *and* the chargeback evidence *and* the invoice/VAT credit all move
  to the seller; the key side is unchanged (keys outlive refunds on
  every rail).

### 3.4 Purchase-recovery UX (exists today, provider-neutral)

| Need | Current path | Cited at |
|---|---|---|
| Lost key | Buyer replies to the purchase email or opens an issue; owner runs `scripts/license_lookup.py <email or txn>` against `issued.jsonl` and re-sends | `docs/guides/license.md` "Lost keys", `docs/dev/license-ops.md` "The issue log + support lookup" |
| Reinstall / new machine | Re-import the same key: `sayit-ermano license <KEY>`; no deactivation, no device limit | `docs/guides/license.md` "Commands" |
| Lost receipt / invoice | MoR support (Paddle receipts carry the VAT invoice; seller must not reissue) | `docs/dev/store-emails.md:8`, `docs/dev/paddle-refunds.md:40-41` |
| Self-service re-download | Not built — email points at the install guide; "never a time-limited link, the key is the entitlement" | `docs/dev/store-emails.md:61-64` |
| Click-to-activate deep link | Not built (needs a desktop URI handler); CLI import covers it | `docs/dev/license-ops.md:124-126` |

Recovery is fully manual on the seller side. A provider with a
**customer portal** removes the two most common tickets (lost receipt,
lost key) without touching our code — but only for things the portal
knows about. Polar's portal re-displays *Polar-issued* license keys
([license keys](https://polar.sh/docs/features/benefits/license-keys));
whether it can show a key minted by our own Ed25519 keygen is OPEN-6.
Invoices and receipts would be self-service on Polar regardless.

### 3.5 Provider swap matrix (what a change actually touches)

| Piece | Paddle (today) | Polar | Stripe (direct or Managed Payments, if ever eligible) |
|---|---|---|---|
| Signature header | `Paddle-Signature: ts=;h1=` | `webhook-id`, `webhook-timestamp`, `webhook-signature` (Standard Webhooks for secrets created on/after 2026-09-08; older = Polar HMAC) ([webhooks](https://polar.sh/docs/integrate/webhooks/delivery)) | `Stripe-Signature: t=,v1=` |
| Signed payload | `{ts}:{body}` | `"{id}.{timestamp}.{body}"` per Standard Webhooks | `{t}.{body}` (note: dot, not colon) |
| Verdict for `verify_paddle_signature` | — | **Rewrite** (different header names, payload format, and secret encoding `whsec_…`) | **Rewrite** — same HMAC-SHA256, but `t.`-joined and multiple `v1` entries during secret roll; stdlib `hmac` suffices |
| Trigger event | `transaction.completed` | Polar order event (exact name to confirm from its event list at swap time — OPEN-7; `docs/dev/license-ops.md` §"If the store becomes Polar" already scopes it) | `checkout.session.completed` with `payment_status = paid` |
| `extract_purchase` fields | `data.customer.email`, `items[].price.product_id`, `data.id`, `occurred_at` | Polar order payload (`customer.email`, `product_id`, `id`, `created_at`) — field names differ | `data.object.customer_details.email`, `metadata`, `data.object.id`, `created` (snapshot events); thin events require an API fetch |
| Retries | up to 60 over ~2 days; `failed` → manual replay | up to 10, exponential backoff; endpoint auto-disabled after 10 consecutive failures; 10 s timeout | up to 3 days live; event ids for dedup |
| Everything else (keygen, issue log, lookup, CLI, About page) | unchanged | unchanged | unchanged |

Effort estimate: two functions, one test fixture, one `store.js`
config, and four runbooks. Small — but currently **untested** against
any provider other than Paddle (§4, risk 3).

---

## 4. Recommendation and risks

### Recommendation

1. **Keep Paddle as the primary rail — ADR-0005 stands.** It is the
   cheapest published all-in rate that serves RS/BA, its webhook
   contract is the one the code implements and the tests pin, and its
   chargeback defense is automatic (no evidence burden on a solo
   seller).
2. **Close the "direct processor" question as "not eligible".** No
   Stripe account can be opened for a RS/BA seller; Managed Payments
   excludes the region and the support bundle. Do not spend build time
   on it. Reopen only if the owner incorporates in a supported country —
   and then the saving (~$1.5/sale before tax tooling and disputes) is
   small against the filing and dispute work it brings back in-house.
3. **Promote Polar from documented runner-up to a sandbox-tested
   fallback before go-live.** It is the only provider that lists RS and
   BA explicitly, it serves individuals and entities with a bank
   account, its customer portal removes the lost-receipt ticket, and
   the code delta is two functions. "Runner-up" that has never been
   run is a document, not a fallback. Concretely: (a) write the Polar
   verifier + field mapper behind a `--provider` flag on
   `scripts/license_webhook.py`; (b) add the fixture in
   `tests/test_license_webhook.py`; (c) run Polar's sandbox once.
   This is code work for a later task — this note changes no code.
4. **Do not adopt Polar's native license keys as the in-app truth.**
   Polar's validation API is online-only and its keys are Polar-format;
   the offline Ed25519 key stays authoritative for the reasons in
   `docs/dev/license-ops.md`. Use Polar (if ever) as checkout + portal,
   keep minting on our keygen.

### Top three risks

| # | Risk | Why it is real (cited) | Mitigation |
|---|---|---|---|
| 1 | **Payout friction and cash latency** | Paddle pays monthly (balance on the 1st, sent by the 15th, +3 working days), minimum $100, no on-demand withdrawal; RSD/BAM are not payout currencies; a EUR payout to a non-Eurozone bank is a SWIFT transfer with a **15 EUR fee** plus the bank's charges, and up to 1.5% margin if the payout currency differs from the balance currency ([payouts](https://www.paddle.com/help/manage/get-paid/when-and-how-do-i-get-paid), [payout fees](https://www.paddle.com/help/manage/get-paid/is-there-a-fee-taken-for-payouts), [currencies](https://www.paddle.com/help/manage/get-paid/can-i-be-paid-in-my-local-currency)). A sale on the 2nd of the month can take ~45 days to reach a Serbian bank. Paddle may also hold a payout pending "further information" | Set the balance currency to EUR and the payout threshold deliberately (each SWIFT payout costs a fixed 15 EUR — fewer, larger payouts); budget the latency in the [R3 model](2026-11-22-one-time-purchase-cost-model.md); complete identity verification before the first sale; test the first payout end-to-end in the sandbox acceptance for #17 |
| 2 | **MoR refund and chargeback autonomy** | Both MoRs act without asking: Paddle refunds on pre-chargeback alerts and deducts amount + fee (fee never returned, even when Paddle wins); Polar may refund within 60 days at its discretion and holds accounts to 0.4% chargebacks with escalation up to blocking ([Paddle chargebacks](https://www.paddle.com/help/manage/risk-prevention/understanding-chargebacks-with-paddle), [Polar refunds](https://polar.sh/docs/features/refunds), [Polar account reviews](https://polar.sh/docs/merchant-of-record/account-reviews)). Our runbook only knows about refunds that arrive through intake; provider-initiated ones silently end a support entitlement we still promise | Consume refund/adjustment events (or a weekly dashboard-vs-`issued.jsonl` reconciliation — OPEN-5) so the support record matches the provider; keep the 14-day no-questions policy (it is cheaper than any chargeback fee); never dispute an in-window case (already policy in `docs/dev/paddle-refunds.md`) |
| 3 | **Single-provider concentration with an untested exit** | Lemon Squeezy's closure (processing ends 2027-03-31) and its successor's regional exclusion show MoR providers exit markets and migrations have deadlines ([lemonsqueezy.com](https://www.lemonsqueezy.com/)). Paddle's "no lock-in" promise does not make our side portable: a swap touches the verifier, the field mapper, the store JS config and four runbooks, and none of that has been exercised against a second provider. Paddle also runs "periodic reviews" and can request information before releasing a payment | Run the Polar fallback once in sandbox (recommendation 3); keep the issue log and seed in provider-neutral form (already true — `issued.jsonl` carries Paddle ids but our keys verify without them); add "Paddle policy/eligibility change" to the #25 watchlist triggers already named in ADR-0005 |

Not in the top three but worth one line each: the fee base
(tax-inclusive or not — OPEN-2) changes net per sale by a few percent
and should be read off the first sandbox statement; and the webhook
host is a single point of failure with a two-day retry budget — a
monitor on `/healthz` plus a weekly glance at Paddle's
"Events → Notifications" `failed` list is the minimum.

---

## OPEN items

| # | Question | Why open | Suggested resolution |
|---|---|---|---|
| OPEN-1 | Paddle's timestamp tolerance: the page fetched today says SDKs default to "five seconds"; our endpoint accepts 300 s (`scripts/license_webhook.py:57`) | only observable against live deliveries | check `ts` on sandbox deliveries; the wider window only lengthens the replay bound, but match the doc if Paddle confirms 5 s |
| OPEN-2 | Is the Paddle / Polar fee computed on the tax-inclusive total, and are fees returned on a Paddle refund? | Paddle's pricing page does not state the base or the refund treatment; Polar's example uses the tax-inclusive total and says fees are not returned | read the first sandbox statement; if the base is tax-inclusive, update the net figure in `docs/PRICING.md` and the `INPUTS` of `scripts/pricing_model.py` |
| OPEN-3 | Gumroad and FastSpring RS/BA eligibility, refund mechanics, dispute terms | not fetched today (Gumroad's list lives in a help article; FastSpring publishes no fees) | only if both Paddle and Polar fail onboarding |
| OPEN-4 | Mapping of Serbian/Bosnian legal forms (preduzetnik, d.o.o.) onto Stripe Connect Express `company` structures for a Polar payout account | Stripe's matrix lists generic structures | owner confirms at Polar onboarding; individuals are unaffected |
| OPEN-5 | Refund/chargeback events are not consumed; provider-initiated refunds bypass the support record | no handler exists by design (no revocation), but the *record* should still reflect them | weekly reconciliation now; a refund-event handler that annotates `issued.jsonl` later |
| OPEN-6 | Whether Polar's customer portal can display a key minted by our Ed25519 keygen (not a Polar benefit) | Polar docs describe re-display of Polar-issued keys only | check Polar's "create license key" API for custom key values at fallback time; otherwise keep key delivery by email and use the portal for invoices/receipts only |
| OPEN-7 | Exact Polar event name for a paid one-time order, and its payload field names | event list not fetched today | read `polar.sh/docs/integrate/webhooks` when the fallback is implemented |
| OPEN-8 | Serbian/Bosnian VAT and income-tax treatment of MoR payouts (the seller invoices the MoR, not the buyer) | tax advice, not engineering | accountant review before the first payout; both MoRs document the reverse-invoice model ([Polar payouts](https://polar.sh/docs/features/finance/payouts)) |

## Method and limits

- Read-only research: no code changed; this note plus the research
  index entry are the only edits.
- Sources: vendor pricing, help and developer pages fetched
  2026-11-22 (URLs inline); the Stripe connected-account matrix queried
  through the public JSON endpoints its own documentation page uses
  (commands in §2). No third-party summary was trusted for any
  eligibility or fee claim. Where a page could not be fetched or a
  fact was not stated, the item is marked OPEN rather than inferred.
- Not done: account creation, identity verification, seller onboarding,
  sandbox purchases, payout tests, legal or tax review. Fees are list
  rates; statements at onboarding govern.
- Companion notes: [productization verification](2026-11-21-productization-verification.md)
  (first payment-rail pass, 2026-11-21 — this note re-verifies its
  Paddle/Polar rows and adds the Stripe data-endpoint check),
  [one-time purchase cost model](2026-11-22-one-time-purchase-cost-model.md)
  (question 3), [rights & redistribution inventory](2026-11-22-rights-and-redistribution-inventory.md)
  (question 2), [PRICING](../PRICING.md) and [ADR-0005](../adr/ADR-0005-commercial-sku.md)
  (the decision).
