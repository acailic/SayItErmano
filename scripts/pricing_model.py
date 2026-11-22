#!/usr/bin/env python3
"""One-time purchase cost model — monetization plan question R3.

Answers "does a one-time purchase cover its costs?" with arithmetic
only: given the INPUTS dict below, it computes net contribution per
sale, break-even volume per price point, 24-month cash outcomes for
low/base/high volumes, and a one-at-a-time sensitivity ranking of the
assumptions. Every number in
docs/research/2026-11-22-one-time-purchase-cost-model.md is generated
by this script (marker-checked block); `--check` re-verifies the
arithmetic so the note can be regenerated:

    python3 scripts/pricing_model.py           # print the generated tables
    python3 scripts/pricing_model.py --check   # self-verify + note sync

Model shape (a sale = one license at the list price):

- Per-sale cash costs: processor fee (charged on every transaction;
  refunds do not claw back the fee — conservative), refund loss
  (refund_rate x price), paid support minutes x rate, variable
  distribution (GB x cost), chargeback expectation (rate x (price +
  chargeback fee)).
- Hurdle ("target"): fixed monthly cash costs over the 24-month window
  (storage + tooling + seller-side tax compliance + maintenance hours
  valued at the maintainer rate) PLUS a funded maintenance reserve of
  `reserve_months` months of the same fixed costs.
- Break-even N* = smallest integer N with N * net_contribution >=
  target (closed form: ceil(target / net)); None when net <= 0 — the
  price point never covers its per-sale costs.
- Stated separately, never inside per-sale economics: sunk one-off
  development, and unpaid community-support time (valued at the same
  hourly rate for an "economic payback" view only).

All inputs are assumptions, not measurements; see the research note
for provenance and limits. Stdlib only, offline.
"""
from __future__ import annotations

import argparse
import copy
import math
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NOTE_PATH = (REPO_ROOT / "docs" / "research"
             / "2026-11-22-one-time-purchase-cost-model.md")
BEGIN_MARKER = "<!-- BEGIN GENERATED TABLES (scripts/pricing_model.py) -->"
END_MARKER = "<!-- END GENERATED TABLES -->"

# ---------------------------------------------------------------------------
# Inputs — every number here is a labeled assumption, not a measurement.
# ---------------------------------------------------------------------------
INPUTS: dict = {
    "currency": "EUR",
    "window_months": 24,
    # Three price points per the task brief (e.g. 9/19/39 EUR).
    "prices": {"low": 9.00, "base": 19.00, "high": 39.00},
    # Sales of the paid SKU over the full window — planning brackets,
    # not forecasts.
    "volumes": {"low": 300, "base": 1500, "high": 5000},
    # Two processor columns: a merchant-of-record checkout (handles VAT
    # as the reseller, higher take) vs a direct card processor (seller
    # keeps VAT compliance — see tax_compliance_monthly). MoR pct is the
    # conservative ~10% class; "paddle" carries Paddle's published
    # 5% + $0.50 (verified 2026-11-21, docs/PRICING.md; fixed fee taken
    # as €0.50 ≈ $0.50) — it appears in the processor-scenarios table,
    # not the two main columns.
    "processors": {
        "mor": {"label": "MoR ~10% + fixed", "pct": 0.10, "fixed": 0.50},
        "direct": {"label": "Direct ~3% + 30c", "pct": 0.03, "fixed": 0.30},
        "paddle": {"label": "Paddle published 5% + $0.50", "pct": 0.05,
                   "fixed": 0.50},
    },
    "refund_rates": [0.05, 0.10, 0.20],
    # Support: paid-channel minutes per sale; community (free-tier)
    # minutes are unpaid — valued for the economic view only.
    "support": {
        "paid_minutes_per_sale": 15,
        "community_minutes_per_sale": 10,
        "rate_per_hour": 40.0,
    },
    "chargeback": {"rate": 0.005, "fee": 15.0},
    # Distribution: apt repo on GitHub Pages + Flathub CDN => bandwidth
    # ~0; artifact storage/hosting as a fixed monthly line, plus a
    # token per-sale GB figure for release downloads.
    "distribution": {"fixed_monthly": 10.0, "gb_per_sale": 0.3,
                     "cost_per_gb": 0.01},
    # Taxes: the MoR is the seller of record (VAT handled inside its
    # cut); a direct processor leaves EU OSS/VAT filings with the
    # seller — priced as a monthly compliance line, 0 for the MoR.
    "tax_compliance_monthly": {"mor": 0.0, "direct": 60.0, "paddle": 0.0},
    "tooling_monthly": 15.0,
    # Maintenance: hours per month of fixed upkeep (builds, distro
    # rebuilds, security follows) valued at the maintainer rate; the
    # reserve funds `reserve_months` of all fixed monthly costs.
    "maintenance": {"hours_monthly": 10, "rate_per_hour": 40.0},
    "reserve_months": 12,
    # One-off development to date — sunk, stated separately.
    "sunk": {"hours": 1000, "rate_per_hour": 40.0},
    # Base case used for the sensitivity ranking.
    "base_case": {"price": "base", "processor": "mor", "refund_rate": 0.10},
}


# ---------------------------------------------------------------------------
# Pure model
# ---------------------------------------------------------------------------
def fixed_monthly_costs(inputs: dict, processor: str) -> float:
    """Fixed cash cost per month for one processor column."""
    maintenance = inputs["maintenance"]
    return (
        inputs["distribution"]["fixed_monthly"]
        + inputs["tooling_monthly"]
        + inputs["tax_compliance_monthly"][processor]
        + maintenance["hours_monthly"] * maintenance["rate_per_hour"]
    )


def funded_target(inputs: dict, processor: str, include_sunk: bool = False) -> float:
    """The hurdle sales must cover: window fixed costs + reserve (+ sunk)."""
    monthly = fixed_monthly_costs(inputs, processor)
    target = monthly * (inputs["window_months"] + inputs["reserve_months"])
    if include_sunk:
        target += inputs["sunk"]["hours"] * inputs["sunk"]["rate_per_hour"]
    return target


def per_sale_costs(price: float, inputs: dict, processor: str,
                   refund_rate: float) -> dict[str, float]:
    """Expected per-sale cost lines (cash), keyed by line name."""
    proc = inputs["processors"][processor]
    support = inputs["support"]
    dist = inputs["distribution"]
    cb = inputs["chargeback"]
    return {
        "processing": price * proc["pct"] + proc["fixed"],
        "refunds": refund_rate * price,
        "support": (support["paid_minutes_per_sale"] / 60.0
                    * support["rate_per_hour"]),
        "distribution": dist["gb_per_sale"] * dist["cost_per_gb"],
        "chargebacks": cb["rate"] * (price + cb["fee"]),
    }


def net_contribution(price: float, inputs: dict, processor: str,
                     refund_rate: float) -> float:
    """Cash left per sale after per-sale costs, before fixed costs."""
    return price - sum(per_sale_costs(price, inputs, processor,
                                      refund_rate).values())


def unpaid_support_per_sale(inputs: dict) -> float:
    """Value of unpaid community-support time per sale (non-cash)."""
    support = inputs["support"]
    return (support["community_minutes_per_sale"] / 60.0
            * support["rate_per_hour"])


def economic_net_contribution(price: float, inputs: dict, processor: str,
                              refund_rate: float) -> float:
    """Net per sale also valuing unpaid community-support time."""
    return (net_contribution(price, inputs, processor, refund_rate)
            - unpaid_support_per_sale(inputs))


def break_even_volume(price: float, inputs: dict, processor: str,
                      refund_rate: float, economic: bool = False) -> int | None:
    """Smallest N with N * net >= target; None if net <= 0 (never).

    `economic` additionally values unpaid community-support time in the
    per-sale net and includes sunk development in the target.
    """
    if economic:
        net = economic_net_contribution(price, inputs, processor, refund_rate)
        target = funded_target(inputs, processor, include_sunk=True)
    else:
        net = net_contribution(price, inputs, processor, refund_rate)
        target = funded_target(inputs, processor)
    if net <= 0:
        return None
    return math.ceil(target / net)


def window_outcome(volume: int, price: float, inputs: dict, processor: str,
                   refund_rate: float) -> float:
    """Cash at window end: volume * net minus the funded target."""
    return (volume * net_contribution(price, inputs, processor, refund_rate)
            - funded_target(inputs, processor))


def economic_outcome(volume: int, price: float, inputs: dict, processor: str,
                     refund_rate: float) -> float:
    """Economic outcome: also values unpaid support, recovers sunk dev."""
    return (volume * economic_net_contribution(price, inputs, processor,
                                               refund_rate)
            - funded_target(inputs, processor, include_sunk=True))


def _tweak_setters(inputs: dict) -> dict[str, object]:
    """Named setters over a (deep-copied) inputs dict, base-case slots."""
    price_key = inputs["base_case"]["price"]
    proc_key = inputs["base_case"]["processor"]

    def set_price(i: dict, v: float) -> None:
        i["prices"][price_key] = v

    def set_pct(i: dict, v: float) -> None:
        i["processors"][proc_key]["pct"] = v

    def set_refund(i: dict, v: float) -> None:
        i["base_case"]["refund_rate"] = v

    def set_support(i: dict, v: float) -> None:
        i["support"]["paid_minutes_per_sale"] = v

    def set_maintenance(i: dict, v: float) -> None:
        i["maintenance"]["hours_monthly"] = v

    def set_chargeback(i: dict, v: float) -> None:
        i["chargeback"]["rate"] = v

    return {
        "list price": set_price,
        "processor percentage fee": set_pct,
        "refund rate": set_refund,
        "paid support minutes/sale": set_support,
        "maintenance hours/month": set_maintenance,
        "chargeback rate": set_chargeback,
    }


def sensitivity(inputs: dict) -> list[dict]:
    """One-at-a-time ±25% perturbations, ranked by break-even swing.

    Each row perturbs one assumption of the base case and recomputes
    the cash break-even. A side that turns break-even to "never"
    (net <= 0) counts as the largest possible swing.
    """
    base_case = inputs["base_case"]
    base_price = inputs["prices"][base_case["price"]]
    base_be = break_even_volume(base_price, inputs, base_case["processor"],
                                base_case["refund_rate"])
    rows: list[dict] = []
    for name, setter in _tweak_setters(inputs).items():
        entry: dict = {"assumption": name, "base": base_be, "low": None,
                       "high": None, "swing": None}
        for tag, mult in (("low", 0.75), ("high", 1.25)):
            tweaked = copy.deepcopy(inputs)
            setter(tweaked, round(_read_value(inputs, name) * mult, 6))
            # read the (possibly tweaked) price back: the "list price"
            # row must feed the perturbed price into the solve
            be_price = tweaked["prices"][tweaked["base_case"]["price"]]
            entry[tag] = break_even_volume(
                be_price, tweaked, tweaked["base_case"]["processor"],
                tweaked["base_case"]["refund_rate"])
        if base_be is not None:
            if entry["low"] is None or entry["high"] is None:
                entry["swing"] = math.inf
            else:
                entry["swing"] = max(abs(entry["low"] - base_be),
                                     abs(entry["high"] - base_be))
        rows.append(entry)
    rows.sort(key=lambda r: (-(r["swing"] if r["swing"] is not None
                                else -math.inf), r["assumption"]))
    return rows


def _read_value(inputs: dict, name: str) -> float:
    """Read the current base-case value of a tweakable assumption."""
    base_case = inputs["base_case"]
    table = {
        "list price": lambda: inputs["prices"][base_case["price"]],
        "processor percentage fee": lambda: inputs["processors"][
            base_case["processor"]]["pct"],
        "refund rate": lambda: base_case["refund_rate"],
        "paid support minutes/sale": lambda: inputs["support"][
            "paid_minutes_per_sale"],
        "maintenance hours/month": lambda: inputs["maintenance"][
            "hours_monthly"],
        "chargeback rate": lambda: inputs["chargeback"]["rate"],
    }
    return table[name]()


# ---------------------------------------------------------------------------
# Rendering — every table in the note comes from here
# ---------------------------------------------------------------------------
def _eur(x: float) -> str:
    return f"{x:,.2f}"


def _signed(x: float) -> str:
    return f"{x:+,.2f}"


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _be(v: int | None) -> str:
    return "never" if v is None else f"{v:,}"


def render(inputs: dict) -> str:
    """The full generated block embedded in the research note."""
    out: list[str] = []
    cur = inputs["currency"]
    prices = inputs["prices"]
    base_case = inputs["base_case"]
    base_rate = base_case["refund_rate"]
    w = inputs["window_months"]
    r = inputs["reserve_months"]

    out.append("### Fixed monthly costs and the funding hurdle")
    out.append("")
    out.append("| Line | MoR column | Direct column |")
    out.append("| --- | ---: | ---: |")
    m = fixed_monthly_costs(inputs, "mor")
    d = fixed_monthly_costs(inputs, "direct")
    fixed_flat = inputs["distribution"]["fixed_monthly"]
    out.append(f"| Artifact storage/hosting | {_eur(fixed_flat)} "
               f"| {_eur(fixed_flat)} |")
    out.append(f"| Support/tooling subscriptions | "
               f"{_eur(inputs['tooling_monthly'])} "
               f"| {_eur(inputs['tooling_monthly'])} |")
    tax = inputs["tax_compliance_monthly"]
    out.append(f"| Tax compliance (seller side) | {_eur(tax['mor'])} "
               f"| {_eur(tax['direct'])} |")
    mt = inputs["maintenance"]
    mh = mt["hours_monthly"] * mt["rate_per_hour"]
    out.append(f"| Maintenance time ({mt['hours_monthly']} h × "
               f"{_eur(mt['rate_per_hour'])}/h) | {_eur(mh)} | {_eur(mh)} |")
    out.append(f"| **Fixed total per month** | **{_eur(m)}** | **{_eur(d)}** |")
    out.append("")
    out.append(f"Hurdle = {w} months of fixed costs + a {r}-month maintenance "
               f"reserve = {w + r} × monthly: "
               f"**{_eur(funded_target(inputs, 'mor'))} {cur} (MoR)**, "
               f"**{_eur(funded_target(inputs, 'direct'))} {cur} "
               f"(direct)**. Stated separately (never inside per-sale "
               f"economics): sunk development "
               f"{_eur(inputs['sunk']['hours'] * inputs['sunk']['rate_per_hour'])} "
               f"{cur}, and unpaid community-support time "
               f"{_eur(unpaid_support_per_sale(inputs))} {cur}/sale valued at "
               f"the same rate.")
    out.append("")

    out.append(f"### Net contribution per sale (base refund rate "
               f"{_pct(base_rate)})")
    for proc_key in ("mor", "direct"):
        proc = inputs["processors"][proc_key]
        out.append("")
        out.append(f"**{proc['label']}** — all figures {cur}:")
        out.append("")
        out.append("| Cost line | " + " | ".join(
            f"{k} price = {_eur(prices[k])}" for k in
            ("low", "base", "high")) + " |")
        out.append("| --- | ---: | ---: | ---: |")
        lines: dict[str, list[float]] = {name: [] for name in
                                         per_sale_costs(prices["low"], inputs,
                                                        proc_key, base_rate)}
        nets = []
        for k in ("low", "base", "high"):
            costs = per_sale_costs(prices[k], inputs, proc_key, base_rate)
            for name, val in costs.items():
                lines[name].append(val)
            nets.append(net_contribution(prices[k], inputs, proc_key,
                                         base_rate))
        for name, vals in lines.items():
            out.append(f"| {name} | " + " | ".join(_eur(v) for v in vals)
                       + " |")
        out.append("| **Net contribution per sale** | "
                   + " | ".join(f"**{_eur(v)}**" for v in nets) + " |")
    out.append("")

    out.append("### Net contribution per sale vs refund rate")
    out.append("")
    out.append("| Price point × processor | "
               + " | ".join(f"refunds {_pct(rt)}" for rt in
                            inputs["refund_rates"]) + " |")
    out.append("| --- | ---: | ---: | ---: |")
    for proc_key in ("mor", "direct"):
        for k in ("low", "base", "high"):
            cells = [_eur(net_contribution(prices[k], inputs, proc_key, rt))
                     for rt in inputs["refund_rates"]]
            out.append(f"| {_eur(prices[k])} {cur} × {proc_key} | "
                       + " | ".join(cells) + " |")
    out.append("")

    out.append("### Processor scenarios at the base price")
    out.append("")
    out.append(f"Base price {_eur(prices['base'])} {cur}, refunds "
               f"{_pct(base_rate)}. The two main columns use the task's "
               f"conservative classes; the middle row is Paddle's published "
               f"rate as verified 2026-11-21 (docs/PRICING.md):")
    out.append("")
    out.append("| Processor scenario | Fee per sale | Net per sale | "
               "Cash break-even |")
    out.append("| --- | ---: | ---: | ---: |")
    for proc_key in ("mor", "paddle", "direct"):
        proc = inputs["processors"][proc_key]
        fee = per_sale_costs(prices["base"], inputs, proc_key,
                             base_rate)["processing"]
        net = net_contribution(prices["base"], inputs, proc_key, base_rate)
        be = break_even_volume(prices["base"], inputs, proc_key, base_rate)
        out.append(f"| {proc['label']} | {_eur(fee)} | {_eur(net)} | "
                   f"{_be(be)} |")
    out.append("")

    out.append(f"### Break-even volume (base refund rate {_pct(base_rate)})")
    out.append("")
    out.append(f"Sales over the {w}-month window needed to cover the hurdle "
               "(cash), and the full economic payback (also values unpaid "
               "community-support time and recovers sunk development):")
    out.append("")
    out.append("| Price | Cash break-even (MoR) | Cash break-even (direct) "
               "| Economic payback (MoR) |")
    out.append("| --- | ---: | ---: | ---: |")
    for k in ("low", "base", "high"):
        cash_mor = break_even_volume(prices[k], inputs, "mor", base_rate)
        cash_dir = break_even_volume(prices[k], inputs, "direct", base_rate)
        econ = break_even_volume(prices[k], inputs, "mor", base_rate,
                                 economic=True)
        out.append(f"| {_eur(prices[k])} {cur} | {_be(cash_mor)} | "
                   f"{_be(cash_dir)} | {_be(econ)} |")
    out.append("")

    out.append(f"### {w}-month cash outcome by volume (base refund rate "
               f"{_pct(base_rate)})")
    out.append("")
    out.append(f"Window cash = volume × net − hurdle, in {cur}. Negative = "
               "the modeled volume does not cover the modeled costs:")
    out.append("")
    for proc_key in ("mor", "direct"):
        proc = inputs["processors"][proc_key]
        out.append(f"**{proc['label']}**:")
        out.append("")
        out.append("| Price | " + " | ".join(
            f"{name} volume ({vol:,})" for name, vol in
            inputs["volumes"].items()) + " |")
        out.append("| --- | ---: | ---: | ---: |")
        for k in ("low", "base", "high"):
            cells = [_signed(window_outcome(inputs["volumes"][vname],
                                            prices[k], inputs, proc_key,
                                            base_rate))
                     for vname in ("low", "base", "high")]
            out.append(f"| {_eur(prices[k])} | " + " | ".join(cells) + " |")
        out.append("")

    rows = sensitivity(inputs)
    out.append("### Sensitivity — which assumption moves break-even most")
    out.append("")
    out.append(f"One-at-a-time ±25% perturbations around the base case "
               f"({_eur(prices[base_case['price']])} {cur}, "
               f"{base_case['processor']} column, refunds "
               f"{_pct(base_rate)}; base break-even {_be(rows[0]['base'])} "
               f"sales). A side that turns break-even to \"never\" counts "
               f"as the largest possible swing:")
    out.append("")
    out.append("| Assumption (±25%) | Break-even at −25% | Break-even at +25% "
               "| Max swing (sales) |")
    out.append("| --- | ---: | ---: | ---: |")
    for row in rows:
        swing = ("∞ (never)" if row["swing"] == math.inf else
                 f"{row['swing']:,}" if row["swing"] is not None else "—")
        out.append(f"| {row['assumption']} | {_be(row['low'])} | "
                   f"{_be(row['high'])} | {swing} |")
    out.append("")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Self-verification (--check)
# ---------------------------------------------------------------------------
def run_checks(inputs: dict) -> list[str]:
    """Return a list of problems; empty means green.

    Verifies (1) the per-sale identity price == sum(costs) + net, (2)
    break-even minimality by substitution (N* clears the hurdle, N*−1
    does not) in both the cash and economic views, (3) the closed-form
    window outcome against a naive per-sale accumulation loop, (4) the
    note's embedded tables match a fresh render.
    """
    problems: list[str] = []
    tol = 1e-9
    rates = inputs["refund_rates"]
    for proc_key in inputs["processors"]:
        for k in inputs["prices"]:
            price = inputs["prices"][k]
            for rate in rates:
                costs = per_sale_costs(price, inputs, proc_key, rate)
                net = net_contribution(price, inputs, proc_key, rate)
                # fsum re-adds the line items in a different order than
                # net_contribution accumulated them
                if abs(math.fsum([price] + [-v for v in costs.values()])
                       - net) > tol:
                    problems.append(f"per-sale identity drift: {price} "
                                    f"{proc_key} r={rate}")
                for economic, outcome_fn in (
                        (False, window_outcome), (True, economic_outcome)):
                    n = break_even_volume(price, inputs, proc_key, rate,
                                          economic=economic)
                    if n is None:
                        continue
                    if outcome_fn(n, price, inputs, proc_key, rate) < -tol:
                        problems.append(
                            f"break-even {n} does not clear hurdle: {price} "
                            f"{proc_key} r={rate} econ={economic}")
                    if n > 1 and outcome_fn(n - 1, price, inputs, proc_key,
                                            rate) >= -tol:
                        problems.append(
                            f"break-even {n} not minimal: {price} "
                            f"{proc_key} r={rate} econ={economic}")
        # closed form vs naive loop on a representative cell
        price = inputs["prices"]["base"]
        rate = inputs["base_case"]["refund_rate"]
        net = net_contribution(price, inputs, proc_key, rate)
        volume = inputs["volumes"]["base"]
        naive = sum(net for _ in range(volume)) - funded_target(inputs,
                                                                proc_key)
        if abs(naive - window_outcome(volume, price, inputs, proc_key,
                                      rate)) > tol:
            problems.append(f"closed form != naive accumulation: {proc_key}")

    if NOTE_PATH.exists():
        note = NOTE_PATH.read_text(encoding="utf-8")
        match = re.search(re.escape(BEGIN_MARKER) + r"\n(.*?)"
                          + re.escape(END_MARKER), note, re.DOTALL)
        if match is None:
            problems.append(f"note {NOTE_PATH.name} has no generated block")
        else:
            embedded = match.group(1).rstrip("\n")
            fresh = render(inputs).rstrip("\n")
            if embedded != fresh:
                problems.append(
                    f"note {NOTE_PATH.name} tables are stale — regenerate "
                    f"with: python3 {Path(__file__).name}")
    else:
        problems.append(f"note {NOTE_PATH.name} not found")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="self-verify arithmetic and note sync")
    args = parser.parse_args(argv)
    if args.check:
        problems = run_checks(INPUTS)
        for p in problems:
            print(f"FAIL: {p}", file=sys.stderr)
        if problems:
            return 1
        print("pricing_model: all arithmetic checks green, note tables "
              "in sync")
        return 0
    print(BEGIN_MARKER)
    print(render(INPUTS))
    print(END_MARKER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
