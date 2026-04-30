"""Customer Lifetime Value (CLV) calculator.

Two functions:
- `calculate_clv`: pure-Python arithmetic, no LLM.
- `summarize_clv`: optional — asks an LLM (via `utils.connect.ask`) to write a
  short narrative of the result. Works with any model supported by
  `utils/connect.py` (e.g. `api-llama-4-scout`, `claude-opus-4-6-v1`).

The skill's orchestration (what to ask the user, how to translate their words
into the 8 inputs) lives in `../SKILL.md`, not in this file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python scripts/clv_skill.py ...` to import utils/ from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def calculate_clv(
    monthly_revenue: float,
    service_cost: float,
    transaction_frequency: float,
    churn_rate: float,
    discount_rate: float,
    time_horizon: int,
    churn_assumption: str = "optimistic",
    discount_assumption: str = "pessimistic",
) -> float:
    """Return the Customer Lifetime Value over `time_horizon` months.

    `churn_assumption` = "optimistic" → churn at end of month
    (keep this month's revenue). `discount_assumption` =
    "optimistic" → paid at start of month (no discount applied
    to month 0).
    """
    monthly_cost = service_cost * transaction_frequency
    monthly_profit = monthly_revenue - monthly_cost
    retention_rate = 1 - churn_rate

    churn_add = 0 if churn_assumption == "optimistic" else 1
    discount_add = 0 if discount_assumption == "optimistic" else 1

    clv = 0.0
    for month in range(int(time_horizon)):
        clv += (
            monthly_profit
            * (retention_rate ** (month + churn_add))
            / ((1 + discount_rate) ** (month + discount_add))
        )
    return clv


def summarize_clv(
    inputs: dict,
    clv: float,
    model: str = "api-llama-4-scout",
) -> str:
    """Ask an LLM to write a short narrative of the CLV result."""
    # Pick up TRITONAI_API_KEY from the repo's .env if present.
    try:
        from dotenv import load_dotenv

        load_dotenv(_REPO_ROOT / ".env")
    except ImportError:
        pass

    from utils.connect import ask

    prompt = (
        f"A Customer Lifetime Value calculation produced ${clv:,.2f}.\n"
        f"Inputs used:\n{json.dumps(inputs, indent=2)}\n\n"
        "Write 2–3 sentences: restate the key assumptions in plain English and give the CLV. No preamble."
    )
    return ask(prompt, model=model, temperature=0.3) or ""


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Calculate Customer Lifetime Value.")
    p.add_argument("--monthly-revenue", type=float, required=True)
    p.add_argument("--service-cost", type=float, required=True)
    p.add_argument("--transaction-frequency", type=float, required=True)
    p.add_argument("--churn-rate", type=float, required=True, help="fraction, e.g. 0.05")
    p.add_argument("--discount-rate", type=float, required=True, help="fraction, e.g. 0.008")
    p.add_argument("--time-horizon", type=int, required=True)
    p.add_argument(
        "--churn-assumption",
        choices=["optimistic", "pessimistic"],
        default="optimistic",
    )
    p.add_argument(
        "--discount-assumption",
        choices=["optimistic", "pessimistic"],
        default="pessimistic",
    )
    p.add_argument(
        "--model",
        default=None,
        help="If set, also ask this model (via utils.connect.ask) to narrate the result. "
        "E.g. api-llama-4-scout or claude-opus-4-6-v1.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    inputs = {
        "monthly_revenue": args.monthly_revenue,
        "service_cost": args.service_cost,
        "transaction_frequency": args.transaction_frequency,
        "churn_rate": args.churn_rate,
        "discount_rate": args.discount_rate,
        "time_horizon": args.time_horizon,
        "churn_assumption": args.churn_assumption,
        "discount_assumption": args.discount_assumption,
    }
    clv = calculate_clv(**inputs)
    print(f"CLV: ${clv:,.2f}")
    if args.model:
        print()
        print(summarize_clv(inputs, clv, model=args.model))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
