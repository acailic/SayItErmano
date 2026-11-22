#!/usr/bin/env python3
"""One-time purchase cost model — monetization plan question R3.

Pure stdlib arithmetic: does a one-time purchase price cover its own
costs? No demand claims, no market sizing — every input below is an
ASSUMPTION to be replaced with measured values; the model only makes
the arithmetic reproducible and checkable.

Cost model per sold license (all EUR):

  net contribution = kept_revenue - processing - support
                                   - distribution - tax_handling
  kept_revenue  = price * (1 - refund_rate)     # refunds return the
    full price; processing fees on refunded transactions are NOT
    returned by either route (charged on every transaction above).
  Fixed monthly costs (route-dependent: tax compliance) are covered by
  a funded maintenance reserve of `maintenance_reserve_months` months;
  break-even volume = units whose net contribution funds that reserve.
  One-off development is SUNK and stated separately — it is not a cash
  obligation of sales, but a recovery line is shown for context.

Usage:
  scripts/pricing_model.py             # human-readable tables (stdout)
  scripts/pricing_model.py --markdown  # markdown block for the note in
                                       # docs/research/2026-11-22-one-time-purchase-cost-model.md
  scripts/pricing_model.py --check     # self-verify sums + break-even
                                       # solve; exit 0 = green

Companion test: tests/test_pricing_model.py (offline, stdlib only).
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass

# ── inputs (ALL assumptions, not measurements) ──────────────────────────────

INPUTS: dict = {
    # Price points under test (EUR, one-time, VAT-exclusive).
    "prices_eur": {"low": 9, "base": 19, "high": 39},

    # Sales volumes over the 24-month horizon (units, assumptions).
    "volumes_24m": {"low": 300, "base": 900, "high": 2500},

    # Payment routes: merchant-of-record vs direct card collection.
    #   MoR    ~10% + €0.50 — provider is the seller of record, handles
    #           VAT/sales tax (that service is inside the 10%).
    #   direct ~3% + €0.30  — we are the seller: VAT/OSS registration,
    #           filings and invoicing remain ours (costed under "tax").
    "payment": {
        "mor": {"label": "MoR (Paddle-class)", "pct": 0.10, "fixed": 0.50},
        "direct": {"label": "Direct (Stripe-class)", "pct": 0.03,
                   "fixed": 0.30},
    },

    # Refund scenarios (fraction of sales refunded, full price returned).
    "refund_rates": {"low": 0.05, "base": 0.10, "high": 0.20},

    # Support: contact minutes per sold unit (refunded buyers contact
    # too, so minutes are charged on ALL sales) x hourly cost.
    "support": {"minutes_per_sale": 6, "hourly_cost_eur": 35},

    # Distribution: apt repo + GitHub Pages + Flathub sit on free/bundled
    # bandwidth (≈ €0 per download); artifact/release storage is a small
    # fixed monthly line (see fixed_monthly).
    "distribution": {"per_sale_bandwidth_eur": 0.0},

    # Taxes: MoR handles them (marginal €0 — paid via the 10%); direct
    # route leaves EU-OSS compliance, invoicing and filings with us.
    "tax": {
        "mor": {"handled_by": "provider", "monthly_compliance_eur": 0.0,
                "per_sale_eur": 0.0},
        "direct": {"handled_by": "seller", "monthly_compliance_eur": 20.0,
                   "per_sale_eur": 0.10},
    },

    # Fixed monthly costs (independent of sales volume).
    "fixed_monthly": {
        "artifact_storage_eur": 2.0,   # release artifacts / hosting
        "web_domain_eur": 1.0,         # landing site domain
        "maintenance_hours": 8.0,      # paid upkeep budget per month
        "maintenance_hourly_eur": 35.0,
    },

    # The reserve a one-time price must fund on top of per-sale costs:
    # N months of fixed costs, banked for the maintenance tail.
    "maintenance_reserve_months": 12,

    # One-off development, SUNK at decision time — stated separately,
    # never inside per-sale economics (shown only as a recovery line).
    "development_sunk": {"hours": 400, "hourly_cost_eur": 35},
}

ROUTES = ("mor", "direct")
PRICE_KEYS = ("low", "base", "high")


# ── model ───────────────────────────────────────────────────────────────────
#
# Every function takes an explicit `inputs` (defaulting to INPUTS) so
# sensitivity variants are computed on merged copies — the module-level
# INPUTS dict is never mutated after import.

def fixed_monthly(route: str, inputs: dict = INPUTS) -> float:
    """Route-dependent fixed cost per month (EUR)."""
    f = inputs["fixed_monthly"]
    base = (f["artifact_storage_eur"] + f["web_domain_eur"]
            + f["maintenance_hours"] * f["maintenance_hourly_eur"])
    return base + inputs["tax"][route]["monthly_compliance_eur"]


def reserve_target(route: str, inputs: dict = INPUTS) -> float:
    """Maintenance reserve the sales must fund (EUR)."""
    return (inputs["maintenance_reserve_months"]
            * fixed_monthly(route, inputs))


def development_sunk(inputs: dict = INPUTS) -> float:
    d = inputs["development_sunk"]
    return d["hours"] * d["hourly_cost_eur"]


@dataclass(frozen=True)
class PerSale:
    """Cost lines and net contribution for one sold license."""
    price: float
    route: str
    refund_rate: float
    kept_revenue: float
    processing: float
    support: float
    distribution: float
    tax_handling: float
    net: float

    def lines(self) -> list[tuple[str, float]]:
        return [("kept revenue (after refunds)", self.kept_revenue),
                ("payment processing", self.processing),
                ("support", self.support),
                ("distribution", self.distribution),
                ("tax handling", self.tax_handling)]


def per_sale(price: float, route: str, refund_rate: float,
             inputs: dict = INPUTS) -> PerSale:
    p = inputs["payment"][route]
    s = inputs["support"]
    kept = price * (1.0 - refund_rate)
    processing = price * p["pct"] + p["fixed"]
    support = s["minutes_per_sale"] / 60.0 * s["hourly_cost_eur"]
    dist = inputs["distribution"]["per_sale_bandwidth_eur"]
    tax = inputs["tax"][route]["per_sale_eur"]
    return PerSale(price=price, route=route, refund_rate=refund_rate,
                   kept_revenue=kept, processing=processing, support=support,
                   distribution=dist, tax_handling=tax,
                   net=kept - processing - support - dist - tax)


def break_even_units(price: float, route: str, refund_rate: float,
                     target: float, inputs: dict = INPUTS) -> int | None:
    """Smallest unit count whose net contribution funds `target`;
    None when per-sale net is zero/negative (price never breaks even)."""
    net = per_sale(price, route, refund_rate, inputs).net
    if net <= 0:
        return None
    return math.ceil(target / net)


def total_contribution(volume: int, price: float, route: str,
                       refund_rate: float, inputs: dict = INPUTS) -> float:
    """Scenario total: per-sale net aggregated over the 24-month volume."""
    return volume * per_sale(price, route, refund_rate, inputs).net


# ── sensitivity (base price, base refund; which assumption moves
#    break-even most) ────────────────────────────────────────────────────────

def _sensitivity_variants() -> list[tuple[str, str, list[dict]]]:
    """(dimension, label, [{input overrides for low}, {for high}]) —
    each override carries a COMPLETE replacement sub-dict, so a shallow
    top-level merge is a sound variant (no cross-dimension leakage)."""
    s = INPUTS["support"]
    f = INPUTS["fixed_monthly"]
    p = INPUTS["payment"]["mor"]
    return [
        ("refund_rate", "refund rate 5% vs 20%",
         [{"refund_rates": {**INPUTS["refund_rates"], "base": 0.05}},
          {"refund_rates": {**INPUTS["refund_rates"], "base": 0.20}}]),
        ("support", "support minutes 3 vs 12 per sale",
         [{"support": {**s, "minutes_per_sale": 3.0}},
          {"support": {**s, "minutes_per_sale": 12.0}}]),
        ("mor_pct", "MoR fee 8% vs 12%",
         [{"payment": {**INPUTS["payment"], "mor": {**p, "pct": 0.08}}},
          {"payment": {**INPUTS["payment"], "mor": {**p, "pct": 0.12}}}]),
        ("maintenance", "maintenance 4h vs 16h per month",
         [{"fixed_monthly": {**f, "maintenance_hours": 4.0}},
          {"fixed_monthly": {**f, "maintenance_hours": 16.0}}]),
    ]


def sensitivity() -> list[dict]:
    """Break-even at base price/MoR route/base refund under each variant
    pair, ranked by absolute swing. Variants are merged COPIES; the
    module-level INPUTS is left untouched."""
    price = INPUTS["prices_eur"]["base"]
    rate = INPUTS["refund_rates"]["base"]
    baseline = break_even_units(price, "mor", rate, reserve_target("mor"))
    rows = []
    for dim, label, pair in _sensitivity_variants():
        vals = []
        for overrides in pair:
            variant = {**INPUTS, **overrides}
            v_rate = variant["refund_rates"]["base"]
            vals.append(break_even_units(price, "mor", v_rate,
                                         reserve_target("mor", variant),
                                         variant))
        rows.append({"dimension": dim, "label": label,
                     "low": vals[0], "high": vals[1],
                     "swing": abs(vals[1] - vals[0]), "baseline": baseline})
    rows.sort(key=lambda r: r["swing"], reverse=True)
    return rows


# ── self-verification (--check) ─────────────────────────────────────────────

def check() -> list[str]:
    """Independent re-derivation of every published number; empty = green.

    Sums are recomputed with math.fsum from the line items, aggregates
    re-derived by iterating unit-by-unit, and each break-even N is
    proven MINIMAL: (N-1)*net < target <= N*net.
    """
    problems: list[str] = []
    tol = 1e-9
    dev = development_sunk()

    for route in ROUTES:
        target = reserve_target(route)
        want = INPUTS["maintenance_reserve_months"] * fixed_monthly(route)
        if abs(target - want) > tol:
            problems.append(f"reserve({route}): {target} != {want}")
        if fixed_monthly("mor") > fixed_monthly("direct") + tol:
            problems.append("MoR fixed monthly must not exceed direct "
                            "(only direct carries seller compliance)")

    for pkey in PRICE_KEYS:
        price = INPUTS["prices_eur"][pkey]
        for route in ROUTES:
            for rkey in ("low", "base", "high"):
                rate = INPUTS["refund_rates"][rkey]
                ps = per_sale(price, route, rate)
                # 1. line-sum identity (fsum, independent order)
                costs = math.fsum([-ps.processing, -ps.support,
                                   -ps.distribution, -ps.tax_handling])
                if abs(ps.kept_revenue + costs - ps.net) > tol:
                    problems.append(f"net sum mismatch {pkey}/{route}/{rkey}")
                # 2. unit-by-unit aggregate == per-sale x volume
                vol = INPUTS["volumes_24m"]["base"]
                loop_total = math.fsum(
                    per_sale(price, route, rate).net for _ in range(vol))
                if abs(loop_total - vol * ps.net) > 1e-6:
                    problems.append(f"aggregate mismatch {pkey}/{route}/{rkey}")
                # 3. break-even minimality
                n = break_even_units(price, route, rate, reserve_target(route))
                if n is None:
                    if ps.net > 0:
                        problems.append(f"break-even None but net>0 "
                                        f"{pkey}/{route}/{rkey}")
                    continue
                if not isinstance(n, int) or n < 1:
                    problems.append(f"break-even not a positive int: {n}")
                if not ((n - 1) * ps.net < reserve_target(route)
                        <= n * ps.net + tol):
                    problems.append(f"break-even not minimal "
                                    f"{pkey}/{route}/{rkey}: N={n}")

    rows = {r["dimension"]: r for r in sensitivity()}
    price_base = INPUTS["prices_eur"]["base"]
    for dim, _label, pair in _sensitivity_variants():
        row = rows[dim]
        for side, overrides in zip(("low", "high"), pair):
            n = row[side]
            # re-merge the variant independently and prove N's minimality
            # against THAT variant's own economics
            variant = {**INPUTS, **overrides}
            v_rate = variant["refund_rates"]["base"]
            v_net = per_sale(price_base, "mor", v_rate, variant).net
            v_target = reserve_target("mor", variant)
            if n is None:
                if v_net > 0:
                    problems.append(f"sensitivity {dim}/{side}: None "
                                    "but variant net > 0")
                continue
            if n < 1 or not isinstance(n, int):
                problems.append(f"sensitivity {dim}/{side} invalid: {n}")
                continue
            if not ((n - 1) * v_net < v_target <= n * v_net + 1e-9):
                problems.append(f"sensitivity {dim}/{side} not minimal:"
                                f" N={n}")

    if abs(dev - INPUTS["development_sunk"]["hours"]
           * INPUTS["development_sunk"]["hourly_cost_eur"]) > tol:
        problems.append("sunk development line does not sum")
    return problems


# ── rendering ───────────────────────────────────────────────────────────────

def _eur(x: float) -> str:
    return f"{x:,.2f}"


def _be(x: int | None) -> str:
    return "never" if x is None else f"{x:,}"


def _route_label(route: str) -> str:
    """Short route name for tables: 'mor' / 'direct'."""
    return INPUTS["payment"][route]["label"].split(" ")[0].lower()


def render(markdown: bool = False) -> str:
    md = markdown
    out: list[str] = []
    h2 = "## " if md else ""
    bold = "**" if md else ""
    price = INPUTS["prices_eur"]
    vol = INPUTS["volumes_24m"]
    base_rate = INPUTS["refund_rates"]["base"]
    dev = development_sunk()

    out += [f"{h2}Assumptions (inputs to replace with measurements)"]
    out += ["",
            f"Prices EUR {price['low']}/{price['base']}/{price['high']}"
            f" (low/base/high), volumes over 24 months"
            f" {vol['low']}/{vol['base']}/{vol['high']} units.",
            f"Refund scenarios {INPUTS['refund_rates']['low']:.0%}/"
            f"{base_rate:.0%}/{INPUTS['refund_rates']['high']:.0%};"
            f" support"
            f" {INPUTS['support']['minutes_per_sale']:g} min x EUR"
            f" {INPUTS['support']['hourly_cost_eur']}/h per sale"
            " (charged on all sales, refunded buyers included).",
            f"Fixed monthly: storage EUR"
            f" {INPUTS['fixed_monthly']['artifact_storage_eur']:g} +"
            f" domain EUR"
            f" {INPUTS['fixed_monthly']['web_domain_eur']:g} +"
            f" maintenance"
            f" {INPUTS['fixed_monthly']['maintenance_hours']:g} h x EUR"
            f" {INPUTS['fixed_monthly']['maintenance_hourly_eur']:g}"
            f" (+ seller tax compliance EUR"
            f" {INPUTS['tax']['direct']['monthly_compliance_eur']:g}/mo"
            " on the direct route only).",
            f"Maintenance reserve to fund:"
            f" {INPUTS['maintenance_reserve_months']} months of fixed"
            " costs. One-off development is SUNK (EUR"
            f" {_eur(dev)}) and excluded from per-sale economics.", ""]

    out += [f"{h2}Per-sale economics (base refund"
            f" {base_rate:.0%}) — EUR, both payment routes", "",
            f"| price | route | kept revenue | processing | support |"
            f" distribution | tax | {bold}net / sale {bold}|",
            "|---|---|---|---|---|---|---|---|"]
    for pkey in PRICE_KEYS:
        for route in ROUTES:
            ps = per_sale(price[pkey], route, base_rate)
            out.append(
                f"| EUR {ps.price:g} | {_route_label(route)} |"
                f" {ps.kept_revenue:.2f} |"
                f" -{ps.processing:.2f} | -{ps.support:.2f} |"
                f" -{ps.distribution:.2f} | -{ps.tax_handling:.2f} |"
                f" {bold}{ps.net:.2f}{bold} |")
    out += ["",
            "(processing = pct x price + fixed, charged on every"
            " transaction including refunded ones; the direct route's"
            " cheaper rate is partly offset by seller-side tax handling.)",
            ""]

    out += [f"{h2}Break-even volume per price point", "",
            "Units whose net contribution funds the"
            f" {INPUTS['maintenance_reserve_months']}-month maintenance"
            " reserve (route-dependent fixed costs; sunk development is"
            " NOT in this target — the last column shows it separately).",
            "",
            "| price | route | reserve target | net / sale |"
            f" {bold}break-even units{bold} | of which to also recover"
            " sunk dev |", "|---|---|---|---|---|---|"]
    for pkey in PRICE_KEYS:
        for route in ROUTES:
            ps = per_sale(price[pkey], route, base_rate)
            target = reserve_target(route)
            n = break_even_units(price[pkey], route, base_rate, target)
            with_dev = (None if ps.net <= 0 else
                        math.ceil((target + dev) / ps.net))
            out.append(f"| EUR {ps.price:g} | {_route_label(route)} |"
                       f" {_eur(target)} | {ps.net:.2f} | {_be(n)} |"
                       f" {_be(with_dev)} |")
    out.append("")

    out += [f"{h2}24-month scenarios (base price"
            f" EUR {price['base']}, base refund {base_rate:.0%})", "",
            "| route | volume | total contribution | reserve | dev"
            " (sunk) | covers reserve? | covers reserve+dev? |",
            "|---|---|---|---|---|---|---|"]
    for route in ROUTES:
        for vkey in PRICE_KEYS:
            v = vol[vkey]
            total = total_contribution(v, price["base"], route, base_rate)
            target = reserve_target(route)
            ok_r = "yes" if total >= target else "no"
            ok_d = "yes" if total >= target + dev else "no"
            out.append(f"| {_route_label(route)} | {v:,} |"
                       f" {_eur(total)} | {_eur(target)} |"
                       f" {_eur(dev)} | {ok_r} | {ok_d} |")
    out.append("")

    rows = sensitivity()
    out += [f"{h2}Sensitivity — which assumption moves break-even most", "",
            f"(base price EUR {price['base']}, MoR route, base refund"
            f" {base_rate:.0%}; baseline break-even"
            f" {_be(rows[0]['baseline']) if rows else '?'} units)", "",
            "| assumption (low vs high) | break-even low | break-even"
            " high | swing (units) |", "|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['label']} | {_be(r['low'])} | {_be(r['high'])}"
                   f" | {r['swing']:,} |")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="One-time purchase cost model (monetization plan R3)")
    ap.add_argument("--markdown", action="store_true",
                    help="emit markdown tables for the research note")
    ap.add_argument("--check", action="store_true",
                    help="self-verify arithmetic and break-even solve")
    args = ap.parse_args(argv)

    if args.check:
        problems = check()
        for p in problems:
            print(f"FAIL: {p}", file=sys.stderr)
        if problems:
            return 1
        print(f"check green: {len(PRICE_KEYS) * len(ROUTES) * 3}"
              " per-sale rows + sensitivity variants — sums, aggregates"
              " and break-even minimality verified")
        return 0

    print(render(markdown=args.markdown), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
