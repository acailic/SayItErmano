"""Soak-run integration (plan P1.4): summarize scripts/soak.py CSVs.

`scripts/soak.py` samples the running daemon's RSS/CPU into a CSV
(`t_s,rss_mb,cpu_s`) for hours-to-days. This module turns such a CSV
into the small summary the evaluation report embeds, so a soak run and
the accuracy metrics land in one document.

Policy (docs/eval/README.md): 2-hour soak before every major release,
24-hour soak for lifecycle changes (idle-unload, model reload, memory
work). The summary is descriptive — drift verdicts stay human.
"""
from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from pathlib import Path


def parse_soak_rows(rows: Iterable[Sequence[str]]) -> list[tuple[float, ...]]:
    """Validate/convert CSV rows (t_s, rss_mb, cpu_s) to float tuples.

    A non-numeric first row is the header scripts/soak.py writes
    (``t_s,rss_mb,cpu_s``) and is skipped.
    """
    out: list[tuple[float, ...]] = []
    for i, row in enumerate(rows):
        if not row or all(not str(c).strip() for c in row):
            continue
        try:
            out.append(tuple(float(c) for c in row[:3]))
        except (TypeError, ValueError) as e:
            if i == 0:
                continue  # header row (scripts/soak.py writes t_s,rss_mb,cpu_s)
            raise ValueError(
                f"soak CSV row {i + 1} is not numeric "
                f"(expected t_s,rss_mb,cpu_s): {list(row)[:3]}") from e
    if not out:
        raise ValueError("soak CSV has no data rows")
    return out


def read_soak_csv(path: Path) -> list[tuple[float, ...]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return parse_soak_rows(csv.reader(fh))


def summarize_soak(rows: Sequence[Sequence[float]],
                   source: str = "") -> dict[str, object]:
    """Summary of a soak run: duration, RSS drift, CPU burn rate."""
    if not rows:
        raise ValueError("soak CSV has no data rows")
    ts = [r[0] for r in rows]
    rss = [r[1] for r in rows]
    cpu_end = rows[-1][2]
    duration_s = ts[-1] - ts[0]
    duration_h = duration_s / 3600.0 if duration_s > 0 else 0.0
    return {
        "source": source,
        "samples": len(rows),
        "duration_s": round(duration_s, 1),
        "duration_h": round(duration_h, 3),
        "rss_start_mb": round(rss[0], 1),
        "rss_end_mb": round(rss[-1], 1),
        "rss_drift_mb": round(rss[-1] - rss[0], 1),
        "rss_max_mb": round(max(rss), 1),
        "cpu_total_s": round(cpu_end, 1),
        "cpu_s_per_hour": round(cpu_end / duration_h, 1) if duration_h > 0
        else None,
    }
