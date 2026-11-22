"""One-time purchase cost model (monetization plan R3) — offline unit tier.

The break-even solve and the arithmetic identities that
`scripts/pricing_model.py --check` self-verifies, pinned here so a
regression in the solver fails CI rather than silently re-basing the
research note. Pure stdlib, no network, no fixtures.
"""

import copy
import math

import scripts.pricing_model as pm

# ---------------------------------------------------------------------------
# break-even solve: minimality is the contract
# ---------------------------------------------------------------------------

def test_break_even_is_smallest_funding_unit_count():
    # net/sale 11.20, target 3396 -> 303 x 11.20 = 3393.60 < target
    n = pm.break_even_units(19, "mor", 0.10, pm.reserve_target("mor"))
    net = pm.per_sale(19, "mor", 0.10).net
    assert n * net >= pm.reserve_target("mor")       # funds it
    assert (n - 1) * net < pm.reserve_target("mor")  # and is minimal


def test_break_even_exact_multiple_rounds_to_exact():
    # economics flattened to net = price exactly -> target 90 = 9 x 10
    flat = {"payment": {"mor": {"label": "flat", "pct": 0.0, "fixed": 0.0}},
            "support": {"minutes_per_sale": 0, "hourly_cost_eur": 35},
            "distribution": {"per_sale_bandwidth_eur": 0.0},
            "tax": {"mor": {"handled_by": "x", "monthly_compliance_eur": 0.0,
                            "per_sale_eur": 0.0}}}
    variant = {**pm.INPUTS, **flat}
    assert pm.per_sale(10, "mor", 0.0, variant).net == 10
    n = pm.break_even_units(10, "mor", 0.0, 90.0, variant)
    assert n == 9 and isinstance(n, int)


def test_break_even_just_short_of_multiple_rounds_up():
    # net 11.20 funds 1000 at 90 units (1008) but 89 is 996.80 < 1000
    n = pm.break_even_units(19, "mor", 0.10, 1000.0)
    assert n == math.ceil(1000.0 / pm.per_sale(19, "mor", 0.10).net) == 90


def test_break_even_impossible_when_net_not_positive():
    # price so low that support alone exceeds kept revenue -> never
    tiny = {"support": {"minutes_per_sale": 600, "hourly_cost_eur": 35}}
    variant = {**pm.INPUTS, **tiny}
    assert pm.per_sale(9, "mor", 0.10, variant).net <= 0
    assert pm.break_even_units(9, "mor", 0.10, 100.0, variant) is None


# ---------------------------------------------------------------------------
# per-sale arithmetic identities (what --check re-derives)
# ---------------------------------------------------------------------------

def test_per_sale_lines_sum_to_net():
    for price in pm.INPUTS["prices_eur"].values():
        for route in pm.ROUTES:
            for rate in pm.INPUTS["refund_rates"].values():
                ps = pm.per_sale(price, route, rate)
                fsum_net = math.fsum(
                    [ps.kept_revenue, -ps.processing, -ps.support,
                     -ps.distribution, -ps.tax_handling])
                assert ps.net == fsum_net or abs(ps.net - fsum_net) < 1e-9


def test_scenario_total_equals_per_sale_times_volume():
    for route in pm.ROUTES:
        for v in pm.INPUTS["volumes_24m"].values():
            total = pm.total_contribution(v, 19, route, 0.10)
            per = v * pm.per_sale(19, route, 0.10).net
            assert abs(total - per) < 1e-9


def test_direct_route_carries_seller_tax_compliance_mor_does_not():
    assert pm.fixed_monthly("direct") > pm.fixed_monthly("mor")
    assert pm.per_sale(19, "direct", 0.10).tax_handling > 0
    assert pm.per_sale(19, "mor", 0.10).tax_handling == 0


# ---------------------------------------------------------------------------
# sensitivity: no variant leakage, ranked output
# ---------------------------------------------------------------------------

def test_sensitivity_variants_do_not_mutate_inputs():
    before = copy.deepcopy(pm.INPUTS)
    rows = pm.sensitivity()
    assert pm.INPUTS == before
    assert len(rows) == len(pm._sensitivity_variants())


def test_sensitivity_rows_ranked_by_swing_and_carry_baseline():
    rows = pm.sensitivity()
    swings = [r["swing"] for r in rows]
    assert swings == sorted(swings, reverse=True)
    baseline = pm.break_even_units(
        pm.INPUTS["prices_eur"]["base"], "mor",
        pm.INPUTS["refund_rates"]["base"], pm.reserve_target("mor"))
    assert all(r["baseline"] == baseline for r in rows)


# ---------------------------------------------------------------------------
# --check self-verification green
# ---------------------------------------------------------------------------

def test_check_reports_no_problems(capsys):
    assert pm.check() == []
    assert pm.main(["--check"]) == 0
    assert "check green" in capsys.readouterr().out
