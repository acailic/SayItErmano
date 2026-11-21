# Funnel measurement (issue #18)

Server-side conversion funnel with zero client telemetry: Flathub's
public install metric + Paddle's checkout analytics. Nothing here runs
in the app. The decision rule and stage definitions live in
[pricing-funnel-decision-rule.md](../../pricing-funnel-decision-rule.md).

## Files

- `flathub.json` — dated daily rows (JSONL despite the `.json` name;
  append-only; a same-day rerun rewrites only the trailing row).
  SayItErmano + the two benchmarks (Speech Note, Speed of Sound).
- `weekly.md` — the one-page report, regenerated on every run: installs
  week-over-week vs benchmarks, store stages, CTR verdict vs the rule's
  floor. Committed rows/reports are snapshots, not gates.
- `paddle.json` — owner-dropped Paddle analytics export (weekly totals:
  `store_visits`, `checkouts_started`, `checkouts_completed`,
  `refunds`). Absent until the store ships (#17); the report prints
  NOT-LIVE per stage until it exists.

## Unattended weekly runs (cron)

`scripts/flathub_stats.py` is the collector AND the report renderer;
one cron line covers both (daily pull keeps the data fresh, the report
is week-over-week by construction):

```
30 6 * * * cd /home/<you>/SayItErmano && .venv/bin/python scripts/flathub_stats.py --quiet >> ~/.cache/sayitermano-funnel/cron.log 2>&1
```

- Log lives outside the repo so the tree stays clean; committed
  `flathub.json` rows happen when the owner commits (the data file is
  the history — see the append-only contract above).
- No network egress beyond the two public APIs (Flathub search,
  nothing else); exit code is always 0 (reporter, not a gate).
- Manual run, same thing: `.venv/bin/python scripts/flathub_stats.py`.

## Owner actions (pending)

1. Install the cron line above on the host (this run only ships the
   script, tests, docs, and this README).
2. After the store goes live (#17): drop the weekly Paddle export as
   `paddle.json` (or wire Paddle API keys once the checkout ticket adds
   that export path) — the NOT-LIVE sections fill in automatically.
