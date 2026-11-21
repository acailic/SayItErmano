# Funnel weekly — Flathub installs → store → purchase

_Latest sample 2026-11-21 (need ≥2 samples spanning 7 days for deltas). Generated unattended by `scripts/flathub_stats.py`; source of truth per stage and the decision rule: [pricing-funnel-decision-rule.md](../../pricing-funnel-decision-rule.md)._

## Flathub installs (`installs_last_month`, the only public metric)

| app | latest | week prior | Δ |
|---|---|---|---|
| SayItErmano (listing submission #12 pending) | NOT-LISTED | - | - |
| Speech Note (category ceiling) | 8773 | - | - |
| Speed of Sound (dictation #1) | 1507 | - | - |

## Store funnel (Paddle checkout analytics — server-side only, no client telemetry)

| stage | value |
|---|---|
| Store visits (UTM-tagged links) | NOT-LIVE (store is #17, not launched; export `paddle.json` absent) |
| Checkouts started | NOT-LIVE (store is #17, not launched; export `paddle.json` absent) |
| Checkouts completed | NOT-LIVE (store is #17, not launched; export `paddle.json` absent) |
| Refunds | NOT-LIVE (store is #17, not launched; export `paddle.json` absent) |

## Flathub → store click-through

NOT-LIVE — store not live yet (#17); the metric is store visits ÷ installs_last_month once both exist.

## Decision rule status

Thresholds and the change-order rule live in [pricing-funnel-decision-rule.md](../../pricing-funnel-decision-rule.md) — written before the data on purpose. Benchmarks accumulate from day one; the rule arms when the store launches (#17).
