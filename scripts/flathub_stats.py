#!/usr/bin/env python3
"""Flathub install tracker + weekly funnel report (issue #18).

Server-side funnel measurement with zero client telemetry: every number
comes from a public API pull or a vendor export, never from the app
(the no-telemetry posture of ADR-0001/ADR-0005 holds — see
docs/pricing-funnel-decision-rule.md for what is measured and why).

Each run (daily cron; the report itself is weekly-cadence):

  1. POSTs https://flathub.org/api/v2/search {"query": ...} once per
     tracked app — installs_last_month is the ONLY public install
     metric (GET returns 405; no cumulative total exists). Benchmarks:
     Speech Note (category ceiling) and Speed of Sound ("dictation" #1).
  2. Appends one dated row to docs/quality/funnel/flathub.json — JSONL
     despite the .json name, append-only; a same-day rerun rewrites
     only the trailing row.
  3. Renders the one-page report to docs/quality/funnel/weekly.md:
     installs week-over-week vs benchmarks, store stages from a Paddle
     analytics export when docs/quality/funnel/paddle.json exists
     (NOT-LIVE until the store ships, #17), and decision-rule status.

Usage:
  scripts/flathub_stats.py [--data PATH] [--report PATH] [--paddle PATH]
                           [--quiet]

Exit 0 always — this is a reporter, not a gate (same contract as
competitive-watch.py).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SEARCH_URL = "https://flathub.org/api/v2/search"
UA = {"User-Agent": "sayitermano-funnel/1.0 (+github.com/acailic/SayItErmano)"}

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = REPO_ROOT / "docs" / "quality" / "funnel" / "flathub.json"
DEFAULT_REPORT = REPO_ROOT / "docs" / "quality" / "funnel" / "weekly.md"
DEFAULT_PADDLE = REPO_ROOT / "docs" / "quality" / "funnel" / "paddle.json"

# Store-side decision thresholds, mirrored from
# docs/pricing-funnel-decision-rule.md (the doc is authoritative).
CTR_FLOOR_PCT = 2.0

PADDLE_STAGES = [
    ("store_visits", "Store visits (UTM-tagged links)"),
    ("checkouts_started", "Checkouts started"),
    ("checkouts_completed", "Checkouts completed"),
    ("refunds", "Refunds"),
]


@dataclass(frozen=True)
class TrackedApp:
    key: str            # row field name in flathub.json
    name: str           # exact Flathub display name to match
    query: str          # search-API query that must surface it
    note: str = ""      # report label annotation
    app_id: str | None = None  # exact flatpakAppId when known


TRACKED = [
    TrackedApp(key="sayitermano", name="SayItErmano", query="SayItErmano",
               app_id="io.github.acailic.SayItErmano",
               note="listing submission #12 pending"),
    TrackedApp(key="speech_note", name="Speech Note", query="speech note",
               note="category ceiling"),
    TrackedApp(key="speed_of_sound", name="Speed of Sound",
               query="speed of sound", note="dictation #1"),
]


def fetch_json(url: str, payload: dict):
    """POST a JSON body and parse the JSON response."""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={**UA, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _norm(s: str | None) -> str:
    return " ".join((s or "").casefold().split())


def match_hit(app: TrackedApp, hits: list[dict]) -> dict | None:
    """The hit belonging to a tracked app: exact flatpakAppId when known,
    else exact normalized display name. None = not on Flathub yet —
    the honest state for SayItErmano until submission #12 lands."""
    if app.app_id:
        for h in hits:
            if h.get("flatpakAppId") == app.app_id:
                return h
    for h in hits:
        if _norm(h.get("name")) == _norm(app.name):
            return h
    return None


def snapshot(fetch=fetch_json, today: dt.date | None = None) -> dict:
    """One dated row: per tracked app, listed-ness + installs_last_month.
    Fetch failures are recorded per app, never raised — a reporter."""
    date = (today or dt.date.today()).isoformat()
    apps: dict[str, dict] = {}
    for app in TRACKED:
        try:
            hits = (fetch(SEARCH_URL, {"query": app.query}) or {}).get("hits", [])
        except Exception as e:  # noqa: BLE001 - recorded, not raised
            apps[app.key] = {"listed": False, "installs_last_month": None,
                             "error": str(e)[:120]}
            continue
        hit = match_hit(app, hits)
        apps[app.key] = {"listed": hit is not None,
                         "installs_last_month":
                             hit.get("installs_last_month") if hit else None}
    return {"date": date, "apps": apps}


# ── data file (append-only JSONL) ───────────────────────────────────────────

def append_row(path: Path, row: dict) -> None:
    """Append one dated row; a rerun for the LAST recorded date rewrites
    that trailing row in place (one row per day, history untouched)."""
    lines: list[str] = []
    if path.exists():
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
    if lines and json.loads(lines[-1]).get("date") == row["date"]:
        lines[-1] = json.dumps(row)
    else:
        lines.append(json.dumps(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            rows.append(json.loads(ln))
    return rows


# ── store-side stages (Paddle export, NOT-LIVE until #17) ──────────────────

def load_paddle(path: Path) -> dict | None:
    """The owner-dropped Paddle analytics export (weekly totals), or None
    when absent — the store is not live, so every stage prints NOT-LIVE."""
    if not path.exists():
        return None
    try:
        j = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return j if isinstance(j, dict) else None


# ── report rendering (pure: rows+state -> markdown) ────────────────────────

def _fmt_delta(now, was) -> str:
    if now is None or was is None:
        return "-"
    d = now - was
    return f"{d:+d}" if d else "+0"


def _latest_and_week_prior(rows: list[dict]) -> tuple[dict | None, dict | None]:
    """Latest row + the most recent row at least 7 days older (the
    week-over-week comparison the decision rule needs)."""
    if not rows:
        return None, None
    latest = rows[-1]
    try:
        cutoff = dt.date.fromisoformat(latest["date"]) - dt.timedelta(days=7)
    except (KeyError, ValueError):
        return latest, None
    prior = None
    for row in rows[:-1]:
        try:
            if dt.date.fromisoformat(row["date"]) <= cutoff:
                prior = row
        except (KeyError, ValueError):
            continue
    return latest, prior


def _paddle_value(export: dict | None, key: str):
    if not export:
        return None
    v = export.get(key)
    return v if isinstance(v, int) else None


def render_report(rows: list[dict], paddle: dict | None) -> str:
    latest, prior = _latest_and_week_prior(rows)

    def _installs(row: dict | None, key: str):
        if not row:
            return None
        entry = row.get("apps", {}).get(key, {})
        return entry.get("installs_last_month") if entry.get("listed") else None

    lines = ["# Funnel weekly — Flathub installs → store → purchase", ""]
    if latest:
        lines.append(f"_Latest sample {latest.get('date')}"
                     + (f", week-over-week vs {prior['date']}" if prior
                        else " (need ≥2 samples spanning 7 days for deltas)")
                     + ". Generated unattended by `scripts/flathub_stats.py`;"
                       " source of truth per stage and the decision rule:"
                       " [pricing-funnel-decision-rule.md]"
                       "(../../pricing-funnel-decision-rule.md)._")
    else:
        lines.append("_No samples yet — first run records one dated row into"
                     " `flathub.json`._")
    lines += ["", "## Flathub installs (`installs_last_month`, the only"
              " public metric)", "",
              "| app | latest | week prior | Δ |", "|---|---|---|---|"]
    for app in TRACKED:
        label = app.name + (f" ({app.note})" if app.note else "")
        now, was = _installs(latest, app.key), _installs(prior, app.key)
        if latest and latest.get("apps", {}).get(app.key, {}).get("error"):
            cell = f"ERROR: {latest['apps'][app.key]['error']}"
        elif now is None:
            cell = "NOT-LISTED" if latest else "-"
        else:
            cell = str(now)
        was_cell = str(was) if was is not None else "-"
        lines.append(f"| {label} | {cell} | {was_cell} |"
                     f" {_fmt_delta(now, was)} |")

    lines += ["", "## Store funnel (Paddle checkout analytics — server-side"
              " only, no client telemetry)", ""]
    if paddle:
        lines += ["| stage | this export |", "|---|---|"]
        for key, label in PADDLE_STAGES:
            lines.append(f"| {label} | {_paddle_value(paddle, key)} |")
    else:
        lines += ["| stage | value |", "|---|---|"]
        for _key, label in PADDLE_STAGES:
            lines.append(f"| {label} | NOT-LIVE (store is #17, not launched;"
                         " export `paddle.json` absent) |")

    installs = _installs(latest, "sayitermano") if latest else None
    visits = _paddle_value(paddle, "store_visits")
    lines += ["", "## Flathub → store click-through", ""]
    if visits is None or installs is None:
        why = ("store not live yet (#17)" if visits is None
               else "SayItErmano not listed on Flathub yet (#12)")
        lines.append(f"NOT-LIVE — {why}; the metric is store visits ÷"
                     " installs_last_month once both exist.")
    else:
        ctr = visits / installs * 100 if installs else 0.0
        verdict = ("UNDER floor — listing-copy change per the rule"
                   if ctr < CTR_FLOOR_PCT else "above floor")
        lines.append(f"{visits} visits / {installs} installs ="
                     f" **{ctr:.1f}%** (floor {CTR_FLOOR_PCT}%: {verdict};"
                     " the N-week streak that triggers action is tracked in"
                     " the decision-rule doc).")

    lines += ["", "## Decision rule status", "",
              "Thresholds and the change-order rule live in"
              " [pricing-funnel-decision-rule.md]"
              "(../../pricing-funnel-decision-rule.md) — written before the"
              " data on purpose. Benchmarks accumulate from day one;"
              " the rule arms when the store launches (#17).", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--paddle", type=Path, default=DEFAULT_PADDLE)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    row = snapshot()
    append_row(args.data, row)
    report = render_report(load_rows(args.data), load_paddle(args.paddle))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")

    if not args.quiet:
        print(f"row appended: {args.data} ({row['date']})")
        print(f"report written: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
