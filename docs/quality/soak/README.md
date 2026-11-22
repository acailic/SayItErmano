# Soak evidence (Q9 — long-run memory/fd/socket stability)

Every file here is produced by `scripts/soak_run.py` — a harness that
spawns the REAL daemon (`python -m fluidvoice daemon --no-hotkey
--no-sounds`, model-free, headless-friendly), drives scripted
toggle/cancel takes, and samples RSS / fd count / thread count /
control-socket health. One JSON (samples + verdict) + one Markdown
(human summary) per run.

## The gate, in the open

- **RSS growth** — FAIL when start-to-end growth after warmup exceeds
  **15%** (baseline = median RSS of the warmup window, default the
  first 60 s; end = median of the last 3 samples, so a one-off
  allocation blip can neither pass nor fail the run). The post-warmup
  least-squares slope (MB/h) is reported alongside, but the gated
  number is the total growth.
- **Socket** — every sample round-trips a `status` request over the
  control socket; any failure (or a dead daemon) is red.
- A run too short to have ≥ 2 warmup and ≥ 2 post-warmup samples is
  RED with reason `insufficient-samples` — a soak that proved nothing
  did not pass.

Tolerance and windows are CLI flags (`--tolerance-pct`,
`--warmup-seconds`); the committed evidence files record exactly what
was used.

## Files

- `YYYY-MM-DD-<duration>m.json` / `.md` — one per run (`-2`, `-3` … on
  same-day repeats). The JSON carries every sample, every scripted
  take, the daemon log tail and the full verdict math.

## Reproduce

```sh
python scripts/soak_run.py                       # 10 min, 15 s samples
python scripts/soak_run.py --duration-minutes 1  # CI-length smoke
```

The offline tests for the verdict math and evidence naming live in
`tests/test_q9_soak_upgrade.py`; the real-subprocess lane
(short soak + failure paths) is
`tests/integration/test_q9_harness.py` (`just test-process`).

`scripts/soak.py` is the older PASSIVE monitor for a daemon the owner
already runs (multi-day, CSV); this directory is the CI-able active
counterpart. The 2h pre-release + 24h lifecycle soaks stay open owner
actions — see the [evidence index](../evidence-index.md).
