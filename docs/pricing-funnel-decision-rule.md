# Pricing funnel decision rule — written before the data

Status: ACTIVE (armed when the store launches, #17). Owner of the rule:
this doc; changes to thresholds require editing this doc, never a
silent re-tune after numbers arrive. Linked from
[PRICING.md](PRICING.md); measurement mechanics in
[quality/funnel/](quality/funnel/).

Why written up front (issue #18): the plan's biggest unverified
assumption is that Flathub visitors convert to a $39–59 checkout. With
thresholds fixed in advance, a bad week triggers the pre-agreed change
instead of a debate; without them, every number gets narrated into
whatever we already wanted to do.

## The funnel and each stage's source of truth

No client telemetry, ever ([ADR-0001](adr/ADR-0001-no-tcp.md) posture,
reinforced by the no-telemetry constraint in
[ADR-0005](adr/ADR-0005-commercial-sku.md)). Every stage is measured
server-side or not at all:

| Stage | Source of truth | Collector |
|---|---|---|
| Flathub installs | `installs_last_month` via `POST https://flathub.org/api/v2/search` (the only public metric; GET returns 405, no cumulative total) | `scripts/flathub_stats.py`, daily, rows in [quality/funnel/flathub.json](quality/funnel/flathub.json) |
| Benchmarks | Same API: Speech Note (category ceiling, ~8.8k/mo) and Speed of Sound ("dictation" #1, ~1.5k/mo) at 2026-11-21 | same rows, same pulls |
| Store visits | Paddle checkout analytics (views with UTM attribution) | weekly export dropped as `quality/funnel/paddle.json` |
| Checkouts started / completed / refunds | Paddle checkout analytics / transactions | same export |

Store links carry UTM ids so Paddle attributes the source — the
convention every store link uses once #17 lands (landing site, README,
Flathub listing each get their own `utm_source`):

- Flathub listing: `?utm_source=flathub&utm_medium=listing&utm_campaign=store`
- README: `?utm_source=github&utm_medium=readme&utm_campaign=store`
- Landing site: `?utm_source=landing&utm_medium=cta&utm_campaign=store`

## The rule (thresholds fixed 2026-11-21)

Let **CTR** = store visits (UTM-attributed, weekly) ÷ SayItErmano
`installs_last_month` (same week's sample). Actions trigger on the
stated condition holding for the stated number of consecutive weeks;
when two trigger at once, the lower row wins.

| # | Condition (consecutive weeks) | Action |
|---|---|---|
| 1 | CTR < **2%** for **3** weeks | Change the Flathub **listing copy** first (headline, screenshots, summary); do NOT touch price |
| 2 | CTR ≥ 2% but visit → checkout-start < **10%** for **3** weeks | Change the **store page** (price anchoring, GPL statement placement, FAQ order) |
| 3 | Checkout-start → completion < **50%** for **3** weeks | **Price test**: drop to the $39 introductory point (PRICING.md) before touching copy |
| 4 | Refund rate > **5%** of completions over any **4**-week window | Stop acquisition changes; treat as a product/support defect (#20), not a pricing lever |

Ordering rationale, fixed now: copy changes are free and reversible;
price cuts are neither — a cut resets every baseline in this table and
anchors the product lower permanently. Price is the lever of last
resort (row 3) and only after the copy-side rows had their turn.

Weekly report: `scripts/flathub_stats.py` renders
[quality/funnel/weekly.md](quality/funnel/weekly.md) — the current CTR
verdict is printed against the floor automatically; N-week streaks are
read off consecutive report rows (the data file is the history).

## Honest caveats

- `installs_last_month` is a rolling ~30-day window, not calendar
  weekly installs; ratios against it are directional, which is exactly
  what a 3-week trigger tolerance is for.
- Thresholds are judgment calls made without data — that is the point.
  They may be recalibrated ONCE, in writing here, during the first
  week after the store launches; never after.
- Until SayItErmano's Flathub submission (#12) merges, the own-install
  row reads NOT-LISTED and CTR stays NOT-LIVE; benchmarks accumulate
  from day one so launch-week deltas have a baseline.
