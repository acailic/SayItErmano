# Pricing — the paid SKU

Decision record: [ADR-0005](adr/ADR-0005-commercial-sku.md). Evidence
base: [2026-11-21 productization verification](research/2026-11-21-productization-verification.md)
(all market numbers below were verified at primary sources on
2026-11-21). Status: prices approved; the Paddle checkout wiring is
in (#17) — landing CTA + funnel tags + comparison table on the site,
refund runbook and first-purchase email in `docs/dev/`. Remaining
owner actions live in the #17 ticket: Paddle account + product,
seller approval, Payoneer payout, sandbox + live test purchase.

Funnel decision rule — thresholds and change-order for listing copy vs
price, written before the data: [pricing-funnel-decision-rule.md](pricing-funnel-decision-rule.md)
(measurement lives in [quality/funnel/](quality/funnel/), no client
telemetry).

## The offer

| | Free (source / GitHub release) | Paid (store build) |
|---|---|---|
| Core dictation loop | Full — hotkey, local STT, insertion, history, settings | Full — identical code, no crippled features |
| Build | Self-built or GitHub release artifact | Tested, signed download from the store page |
| Updates | GitHub Releases, manual | Defined update channel + in-app notice (#22) |
| Support | GitHub issues, best effort | Priority email support with a stated first-response window (#20) |
| License key | None needed | Offline Ed25519 key issued on purchase (#16) |

One-time purchase. No subscription. The audio never leaves the machine
in either tier; AI polish is optional and endpoint-configured in both.

## Prices (proposed)

- **$49 standard** — one-time, per person, all your Linux machines.
- **$39 introductory** — first paid cohort (early adopters), same
  entitlement.
- Refund: 14 days, no questions — processed by Paddle as merchant of
  record.
- Net to the project after Paddle's 5% + $0.50: **$46.05 per $49
  sale**; $46.05/$49 = the economics input for support-cost decisions
  in #20.

## Why these numbers (verified comps, 2026-11-21)

| Product | Offer | Platform | Source |
|---|---|---|---|
| Voicy | $8.49/mo · $82/yr · **$260 lifetime** | **Linux native** | [pricing](https://usevoicy.com/pricing/) |
| MacWhisper | **EUR 64 one-time** (App Store edition: $99.99 lifetime) | macOS | [site](https://macwhisper.com) |
| Vibe Typer | $96/yr | Linux | [pricing](https://vibetyper.com/pricing) |
| OpenWhispr | $80/yr | Linux | [pricing](https://openwhispr.com/pricing) |
| Wispr Flow | $15/mo ($12 annual) — **no Linux client** | Mac/Win/mobile | [pricing](https://wisprflow.ai/pricing) |

$39–59 once equals 5–8 months of any rival's subscription and undercuts
Voicy's lifetime by 4–6×, while staying below MacWhisper's EUR 64.

## GPL statement (goes on the store page verbatim)

> SayItErmano is free software (GPL-3.0). The source is always
> available and always will be. Buying does not buy you restrictions
> or unlock gates — it buys a tested build, a defined update channel,
> priority support, and the project's continued development. If you
> prefer to build it yourself, it works, and that is by design.

## Store page copy draft (for #17)

**Headline:** Press a key. Speak. Polished text lands where your cursor
is — 100% on your machine.

**Sub:** The Linux dictation app that keeps your voice local. Local
Whisper on your GPU. Optional AI polish — on your endpoint or none at
all. One-time purchase. No subscription. No cloud.

**Comparison table:**

| | SayItErmano | Wispr Flow | Voicy | MacWhisper |
|---|---|---|---|---|
| Linux | ✅ | ❌ (waitlist) | ✅ | ❌ |
| Audio processed | On your machine only | Cloud | Cloud (offline mode exists) | On your Mac |
| Price | **$49 once** | $15/mo | $8.49/mo · $260 lifetime | EUR 64 once |
| Open source | GPL-3.0 | No | No | No |

**Privacy section:** Your recordings never leave this computer. No
telemetry, no analytics, no account. Verify it: the network egress
audit (#24) lists every connection the app can make and how to disable
each.

**FAQ anchors:** "Why pay for GPL software?" → quote the GPL statement
above. "What if I lose my key?" → support re-issues from the purchase
record. "Refunds?" → 14 days via Paddle.
