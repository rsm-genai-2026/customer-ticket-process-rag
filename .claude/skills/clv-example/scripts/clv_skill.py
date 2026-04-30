"""Customer Lifetime Value (CLV) skill using Llama via tool calling.

This module exposes:
- `calculate_clv`: a pure-Python CLV calculator.
- `CLV_TOOL`: the JSON-schema tool definition the LLM uses to call it.
- `chat_with_tools`: sends a prompt to the Llama endpoint, runs the tool
  when requested, and returns the assistant's final text reply.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI


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

    `churn_assumption` = "optimistic" means churn happens at end of month
    (we still get this month's revenue from churning customers).
    `discount_assumption` = "optimistic" means payment is at the beginning
    of the month (no discounting applied to the first month).
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


CLV_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "calculate_clv",
        "description": "Calculate Customer Lifetime Value.",
        "parameters": {
            "type": "object",
            "properties": {
                "monthly_revenue": {
                    "type": "number",
                    "description": "The monthly revenue the customer generates",
                },
                "service_cost": {
                    "type": "number",
                    "description": "The cost of servicing the customer",
                },
                "transaction_frequency": {
                    "type": "number",
                    "description": "The average customer frequency of transactions per month",
                },
                "churn_rate": {
                    "type": "number",
                    "description": "The percentage of customers that stop using the service each month",
                },
                "discount_rate": {
                    "type": "number",
                    "description": "The monthly discount rate to apply to future cash flows",
                },
                "time_horizon": {
                    "type": "number",
                    "description": "The time horizon in months over which to calculate CLV",
                },
                "churn_assumption": {
                    "type": "string",
                    "description": (
                        "Assumption for when churn occurs. 'optimistic' = churn at "
                        "end of month (keep this month's revenue); 'pessimistic' = "
                        "churn at start of month (lose this month's revenue)."
                    ),
                },
                "discount_assumption": {
                    "type": "string",
                    "description": (
                        "Assumption for when payment occurs. 'optimistic' = paid at "
                        "beginning of month (no discount on first month); "
                        "'pessimistic' = paid at end of month (discount every month)."
                    ),
                },
            },
            "required": [
                "monthly_revenue",
                "service_cost",
                "transaction_frequency",
                "churn_rate",
                "discount_rate",
                "time_horizon",
                "churn_assumption",
                "discount_assumption",
            ],
        },
    },
}


def _coerce_arg(key: str, value: Any) -> Any:
    """LLMs sometimes return numbers as strings — coerce numeric args to float."""
    if key in {"churn_assumption", "discount_assumption"}:
        return value
    return float(value)


def _build_client() -> "ChatOpenAI":
    from dotenv import load_dotenv
    from langchain_openai import ChatOpenAI

    load_dotenv()
    return ChatOpenAI(
        model="llama-3",
        api_key=os.getenv("LLAMA_API_KEY"),
        openai_api_base="https://traip13.tgptinf.ucsd.edu/v1",
    )


def chat_with_tools(prompt: str, client: "ChatOpenAI | None" = None) -> str:
    """Send `prompt` to the model, run the CLV tool if asked, and return the text reply."""
    client = client or _build_client()
    client_with_tool = client.bind_tools([CLV_TOOL])
    messages: list[Any] = [{"role": "user", "content": prompt}]

    while True:
        response = client_with_tool.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            return response.content

        call = response.tool_calls[0]
        if call["name"] == "calculate_clv":
            args = {k: _coerce_arg(k, v) for k, v in call["args"].items()}
            result = calculate_clv(**args)
            messages.append(
                {
                    "role": "function",
                    "name": call["name"],
                    "content": json.dumps(result),
                }
            )
            continue

        return response.content


if __name__ == "__main__":
    base_prompt = (
        "Calculate the Customer Lifetime Value for a subscription-based software "
        "company. The monthly revenue per customer is $23.99. Service cost for each "
        "customer is $2 per transaction. Average customer transaction frequency is "
        "6.1 times per month with churn rate 5.58% per month. The monthly discount "
        "rate is 0.8% and calculations should consider a 60 month time horizon."
    )
    scenario = (
        " The customer pays at the beginning of the month and churn occurs at "
        "the end of the month."
    )
    print(chat_with_tools(base_prompt + scenario))
