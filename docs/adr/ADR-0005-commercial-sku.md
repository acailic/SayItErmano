# ADR-0005: The commercial SKU is a convenience-and-support bundle sold through Paddle

Date: 2026-11-21
Status: Proposed (architecture of the offer is decided; the exact price points await owner approval)

## Context

The productization board (GitHub Project "SayItErmano —
Productization") plans a paid launch. Verified market context lives in
[2026-11-21-productization-verification.md](../research/2026-11-21-productization-verification.md):
paying comparables exist on every platform including Linux (Voicy
$8.49/mo / $82/yr / $260 lifetime; MacWhisper EUR 64 one-time with a
$99.99 App Store lifetime; Vibe Typer $96/yr; OpenWhispr $80/yr), while
this project is GPL-3.0-or-later and must stay so: the GPLv3 permits
selling copies and support, bars additional restrictions on granted
rights, and requires Corresponding Source for distributed binaries
([GNU FAQ](https://www.gnu.org/licenses/gpl-faq.html#DoesTheGPLAllowMoney);
LICENSE §§4–6, 10).

Lemon Squeezy (the originally assumed rail) closed to new stores with
processing ending 2027-03-31, and its successor excludes Serbia as a
business location. Paddle is the verified rail for a Serbia-based sole
seller: merchant of record, 5% + $0.50 all-in, Serbia sellers served,
Payoneer/wire payouts.

Constraints that shaped the decision:

- Whatever is sold, recipients may redistribute it. Any lock is
  symbolic; the offer must be honest about that.
- The project has a hard no-telemetry ADR; license verification must
  work offline and never gate core dictation.
- The free baseline is strong (Handy, 39.4k stars, MIT). People pay for
  polish, updates, and support, not for the license alone — the
  verified precedents (VoiceInk via Polar, MacWhisper via Gumroad) sell
  exactly that.

## Decision

1. **The paid SKU is a convenience-and-support bundle**, not gated
   software: one downloadable build, preconfigured and tested per
   release; a defined update channel; priority support; and the license
   key that records the entitlement. The GitHub/source build remains
   fully functional forever — no feature of the core dictation loop is
   paid-only.
2. **Price: a one-time purchase in the $39–59 band** — $49 standard,
   $39 introductory (early-adopter cohort), refund window 14 days.
   Paddle is the seller of record; VAT and invoicing are Paddle's.
   Net per $49 sale ≈ $46.05 after Paddle's 5% + $0.50.
3. **Rail: Paddle primary, Polar.sh runner-up.** Purchase triggers the
   `transaction.completed` webhook; our keygen issues an offline
   Ed25519 key (see #16).
4. **Positioning text states plainly**: the source is GPL and always
   available; buying buys convenience, updates, and support; a buyer
   may redistribute their copy, and that is fine.

## Consequences

- `docs/PRICING.md` is the canonical statement of the SKU, prices,
  refund, and the comparison table; the store page (#17) and landing
  CTAs (#19) quote it, never invent numbers.
- The "what money buys" promise must be kept honest: update-channel
  clarity (#22), support operations (#20), and the paid-build packaging
  are funded scope, not marketing.
- No DRM arms race: if keys get shared, the product still works and the
  position still holds; enforcement effort is capped at abuse-of-support
  cases.
- Revisit triggers (from #25): a competitor price move, Wispr shipping
  Linux, or Paddle policy changes reopen this ADR; a superseding ADR
  marks this one Superseded.
