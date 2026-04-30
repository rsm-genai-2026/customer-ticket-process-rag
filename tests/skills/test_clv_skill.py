"""Tests for `skills/example/clv_skill.py` — the CLV calculator."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "example"
    / "scripts"
    / "clv_skill.py"
)
_spec = importlib.util.spec_from_file_location("clv_skill", _MODULE_PATH)
assert _spec and _spec.loader
clv_skill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clv_skill)

calculate_clv = clv_skill.calculate_clv


BASE_ARGS = dict(
    monthly_revenue=23.99,
    service_cost=2.0,
    transaction_frequency=6.1,
    churn_rate=0.0558,
    discount_rate=0.008,
    time_horizon=60,
)


def test_happy_path_positive_value():
    # Typical input should produce a positive CLV.
    assert calculate_clv(**BASE_ARGS) > 0


def test_zero_horizon_returns_zero():
    # No months means no accumulated value, regardless of assumptions.
    assert calculate_clv(**{**BASE_ARGS, "time_horizon": 0}) == 0


def test_single_month_optimistic_equals_monthly_profit():
    # With horizon=1, churn="optimistic" (add=0), discount="optimistic" (add=0)
    # the sum reduces to monthly_profit * 1 / 1 = monthly_profit.
    profit = BASE_ARGS["monthly_revenue"] - BASE_ARGS["service_cost"] * BASE_ARGS[
        "transaction_frequency"
    ]
    result = calculate_clv(
        **{
            **BASE_ARGS,
            "time_horizon": 1,
            "churn_assumption": "optimistic",
            "discount_assumption": "optimistic",
        }
    )
    assert result == pytest.approx(profit)


ASSUMPTION_COMBOS = [
    ("optimistic", "optimistic"),
    ("optimistic", "pessimistic"),
    ("pessimistic", "optimistic"),
    ("pessimistic", "pessimistic"),
]


@pytest.mark.parametrize(("churn", "discount"), ASSUMPTION_COMBOS)
def test_all_assumption_combinations_produce_positive_clv(churn, discount):
    # Sane inputs → positive CLV for every combination of the two flags.
    result = calculate_clv(
        **{**BASE_ARGS, "churn_assumption": churn, "discount_assumption": discount}
    )
    assert result > 0


def test_assumption_ordering_extremes():
    # Switching either flag from optimistic → pessimistic can only reduce CLV,
    # so opt/opt is the global max and pess/pess is the global min.
    results = {
        (c, d): calculate_clv(
            **{**BASE_ARGS, "churn_assumption": c, "discount_assumption": d}
        )
        for c, d in ASSUMPTION_COMBOS
    }
    best = results[("optimistic", "optimistic")]
    worst = results[("pessimistic", "pessimistic")]
    assert best == max(results.values())
    assert worst == min(results.values())
    # Each flag independently reduces CLV when flipped to pessimistic.
    assert results[("optimistic", "pessimistic")] < best
    assert results[("pessimistic", "optimistic")] < best
    assert worst < results[("optimistic", "pessimistic")]
    assert worst < results[("pessimistic", "optimistic")]


def test_assumption_combinations_all_distinct():
    # Every combination should produce a distinct CLV — otherwise the flags
    # aren't doing anything.
    values = {
        (c, d): calculate_clv(
            **{**BASE_ARGS, "churn_assumption": c, "discount_assumption": d}
        )
        for c, d in ASSUMPTION_COMBOS
    }
    assert len(set(values.values())) == 4


def test_full_churn_single_month_only_counts_first_month():
    # churn_rate=1 → retention_rate=0. With optimistic churn (add=0), month 0
    # contributes monthly_profit * 0**0 = monthly_profit; later months contribute 0.
    profit = BASE_ARGS["monthly_revenue"] - BASE_ARGS["service_cost"] * BASE_ARGS[
        "transaction_frequency"
    ]
    result = calculate_clv(
        **{
            **BASE_ARGS,
            "churn_rate": 1.0,
            "discount_rate": 0.0,
            "churn_assumption": "optimistic",
            "discount_assumption": "optimistic",
        }
    )
    assert result == pytest.approx(profit)


def test_cli_prints_calculated_clv(capsys):
    # The CLI should print the same number that calculate_clv returns.
    expected = calculate_clv(**BASE_ARGS, churn_assumption="optimistic")
    clv_skill.main(
        [
            "--monthly-revenue",
            str(BASE_ARGS["monthly_revenue"]),
            "--service-cost",
            str(BASE_ARGS["service_cost"]),
            "--transaction-frequency",
            str(BASE_ARGS["transaction_frequency"]),
            "--churn-rate",
            str(BASE_ARGS["churn_rate"]),
            "--discount-rate",
            str(BASE_ARGS["discount_rate"]),
            "--time-horizon",
            str(BASE_ARGS["time_horizon"]),
            "--churn-assumption",
            "optimistic",
        ]
    )
    captured = capsys.readouterr()
    assert f"${expected:,.2f}" in captured.out
