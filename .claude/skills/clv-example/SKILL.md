---
name: clv-example
description: Calculate Customer Lifetime Value (CLV) for a subscription customer. Use when the user asks about CLV, lifetime value, or "how much is a customer worth" and gives details like monthly revenue, churn, discount rate, or time horizon. The skill gathers any missing inputs from the user and then runs the CLV script.
---

# Customer Lifetime Value (CLV)

Use this skill when the user wants the lifetime value of a customer for a subscription / recurring-revenue business. The actual math lives in `scripts/clv_skill.py`. Your job is to collect the eight inputs from the user and run the script.

## What the script needs

Do **not** run the script until all eight inputs are known. Extract what you can from the user's message; ask for the rest.

| Input | Type | What it is |
| --- | --- | --- |
| `--monthly-revenue` | number | Dollars the customer pays per month. |
| `--service-cost` | number | Dollars it costs to service each transaction. |
| `--transaction-frequency` | number | Average transactions per month. |
| `--churn-rate` | number (0–1) | Fraction leaving per month. Convert "5.58%" → 0.0558. |
| `--discount-rate` | number (0–1) | Monthly discount rate. Convert "0.8%" → 0.008. |
| `--time-horizon` | integer | Months to project over. |
| `--churn-assumption` | `optimistic` or `pessimistic` | `optimistic` = churn at end of month (keep this month's revenue). `pessimistic` = churn at start of month. |
| `--discount-assumption` | `optimistic` or `pessimistic` | `optimistic` = paid at start of month (no discount on month 0). `pessimistic` = paid at end of month. |

## How to use this skill

1. **Extract** whatever the user already gave in their message. Convert percentages to fractions.
2. **Ask** for anything missing — group related questions into a single reply, don't one-at-a-time it.
3. **Translate** plain-English timing into the two assumption flags yourself. For example:
   - "customer pays up front, churn at end of month" → `--discount-assumption optimistic --churn-assumption optimistic`
   - "paid at end of month, churn at start" → `--discount-assumption pessimistic --churn-assumption pessimistic`
4. **Run** the script from the repo root:

   ```bash
   source .venv/bin/activate && uv run python skills/example/scripts/clv_skill.py \
       --monthly-revenue 23.99 --service-cost 2 --transaction-frequency 6.1 \
       --churn-rate 0.0558 --discount-rate 0.008 --time-horizon 60 \
       --churn-assumption optimistic --discount-assumption optimistic
   ```

   To also get a narrated explanation from an LLM, pass `--model`. Two good choices:
   - `--model api-llama-4-scout` — cheap Llama (default for experimentation).
   - `--model claude-opus-4-6-v1` — higher quality, slower.

5. **Report** the printed CLV to the user. Restate the assumptions you inferred so they can sanity-check.

## Example

> User: "What's the CLV for a SaaS customer paying $24/mo, 5% monthly churn, 1% discount, 36-month horizon? They pay up front and churn at end of month."

`monthly_revenue=24`, `churn_rate=0.05`, `discount_rate=0.01`, `time_horizon=36`, `churn_assumption=optimistic`, `discount_assumption=optimistic`. **Missing:** `service_cost` and `transaction_frequency` — ask for both before running.
