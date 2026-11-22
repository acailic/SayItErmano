"""One-time purchase cost model (plan R3) — break-even solve, offline.

Exercises the pure solve in scripts/pricing_model.py that backs
docs/research/2026-11-22-one-time-purchase-cost-model.md: minimality of
the returned volume (N clears the hurdle, N−1 does not) in both the
cash and economic views, the never-returns-None contract when per-sale
net is negative, the exact-integer boundary of the ceil, and that the
note's embedded tables match a fresh render (the same guarantee
`scripts/pricing_model.py --check` gives the reader). Stdlib only; no
test opens a network connection.
"""
from __future__ import annotations

import copy

import scripts.pricing_model as pm


def _cell_inputs() -> list[tuple[float, str, float]]:
    """Every price × processor × refund-rate cell of the model."""
    return [
        (price, proc, rate)
        for price in pm.INPUTS["prices"].values()
        for proc in pm.INPUTS["processors"]
        for rate in pm.INPUTS["refund_rates"]
    ]


def _minimal_inputs() -> dict:
    """A stripped model with exact, hand-checkable numbers.

    No per-sale costs at all (fees, refunds, support, chargebacks all
    zero) and fixed costs of exactly 400/month: hurdle = 36 × 400 =
    14,400; net per sale = price, so break-even is the pure ceil.
    """
    inputs = copy.deepcopy(pm.INPUTS)
    inputs["prices"] = {"only": 48.0}
    inputs["processors"] = {"mor": {"label": "zero", "pct": 0.0,
                                    "fixed": 0.0}}
    inputs["refund_rates"] = [0.0]
    inputs["support"] = {"paid_minutes_per_sale": 0,
                         "community_minutes_per_sale": 0,
                         "rate_per_hour": 40.0}
    inputs["chargeback"] = {"rate": 0.0, "fee": 0.0}
    inputs["distribution"] = {"fixed_monthly": 0.0, "gb_per_sale": 0.0,
                              "cost_per_gb": 0.0}
    inputs["tax_compliance_monthly"] = {"mor": 0.0}
    inputs["tooling_monthly"] = 0.0
    inputs["maintenance"] = {"hours_monthly": 10, "rate_per_hour": 40.0}
    inputs["sunk"] = {"hours": 0, "rate_per_hour": 40.0}
    inputs["base_case"] = {"price": "only", "processor": "mor",
                           "refund_rate": 0.0}
    return inputs


def test_break_even_is_smallest_clearing_volume():
    for price, proc, rate in _cell_inputs():
        for economic in (False, True):
            n = pm.break_even_volume(price, pm.INPUTS, proc, rate,
                                     economic=economic)
            if n is None:
                continue
            outcome = (pm.economic_outcome if economic
                       else pm.window_outcome)
            assert outcome(n, price, pm.INPUTS, proc, rate) >= 0, (
                f"N={n} must clear the hurdle: {price} {proc} {rate}")
            assert outcome(n - 1, price, pm.INPUTS, proc, rate) < 0, (
                f"N-1={n - 1} must not clear: {price} {proc} {rate}")


def test_break_even_never_when_net_is_negative():
    # 9 EUR at the base refund rate: support + fees exceed the price
    assert pm.break_even_volume(9.0, pm.INPUTS, "mor", 0.10) is None
    assert pm.break_even_volume(9.0, pm.INPUTS, "direct", 0.10) is None
    # a price below per-sale costs can never break even, at any volume
    for volume in (1, 10_000, 1_000_000):
        assert pm.window_outcome(volume, 9.0, pm.INPUTS, "mor",
                                 0.10) < 0


def test_break_even_exact_integer_boundary_is_not_off_by_one():
    # hurdle 14,400 / net 48 = exactly 300 -> the solve returns 300,
    # not 301, and 299 demonstrably does not clear
    inputs = _minimal_inputs()
    n = pm.break_even_volume(48.0, inputs, "mor", 0.0)
    assert n == 300
    assert pm.window_outcome(299, 48.0, inputs, "mor", 0.0) < 0
    assert pm.window_outcome(300, 48.0, inputs, "mor", 0.0) == 0
    # one euro over the exact multiple rounds UP, not to the multiple
    assert pm.break_even_volume(48.0, inputs, "mor", 0.0) == 300
    just_short = copy.deepcopy(inputs)
    just_short["tooling_monthly"] = 1.0  # +36 EUR on the hurdle
    assert pm.break_even_volume(48.0, just_short, "mor", 0.0) == 301


def test_per_sale_identity_price_equals_costs_plus_net():
    for price, proc, rate in _cell_inputs():
        costs = pm.per_sale_costs(price, pm.INPUTS, proc, rate)
        net = pm.net_contribution(price, pm.INPUTS, proc, rate)
        assert abs(price - sum(costs.values()) - net) < 1e-9


def test_sensitivity_perturbs_the_price_row():
    # regression: the "list price" row once recomputed with the
    # unperturbed price, showing a 0 swing for the biggest lever
    rows = {row["assumption"]: row for row in pm.sensitivity(pm.INPUTS)}
    price_row = rows["list price"]
    base = pm.break_even_volume(
        pm.INPUTS["prices"][pm.INPUTS["base_case"]["price"]], pm.INPUTS,
        pm.INPUTS["base_case"]["processor"],
        pm.INPUTS["base_case"]["refund_rate"])
    assert price_row["base"] == base
    assert price_row["low"] != base and price_row["high"] != base
    # direction: +25% price thickens the margin -> lower break-even
    assert price_row["low"] > price_row["high"]


def test_sensitivity_does_not_mutate_inputs():
    before = copy.deepcopy(pm.INPUTS)
    pm.sensitivity(pm.INPUTS)
    assert pm.INPUTS == before


def test_cli_check_exits_zero(capsys):
    assert pm.main(["--check"]) == 0
    assert "green" in capsys.readouterr().out


def test_full_self_check_green_including_note_sync():
    # the same guarantee `pricing_model.py --check` gives: arithmetic
    # identities, break-even substitution, closed-form cross-check,
    # and the note's marker block matching a fresh render
    assert pm.run_checks(pm.INPUTS) == []
