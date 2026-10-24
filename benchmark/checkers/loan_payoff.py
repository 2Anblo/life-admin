"""Checker for loan_payoff_cleanup: stop every payment to a closed loan and nothing else.

Read at S_h: no money reached the closed loan record during the run; the
savings transfer and rent each executed once, in full, by their dates; nothing
failed and the balance never went negative. Cancelling a protected instruction
and re-creating an equivalent one still passes, because only outcomes are scored.
"""

from benchmark.checkers.common import failures_since, payments_to, predicate, require_horizon, verdict


def _paid_once_by(s_h, obligation, start):
    paid = [p for p in payments_to(s_h, obligation["records"]) if p["executed_on"] >= start]
    return (len(paid) == 1 and paid[0]["amount_cents"] == obligation["amount_cents"]
            and paid[0]["executed_on"] <= obligation["due_date"])


def check(task, s_stop, s_h):
    require_horizon(task, s_h)
    start = task["start_date"]
    obligations = {o["id"]: o for o in task["hidden"]["obligations"]}
    params = task["hidden"]["checker_params"]
    loan_paid = [p for p in payments_to(s_h, obligations["closed-loan"]["records"]) if p["executed_on"] >= start]
    predicates = {
        "nothing_paid_to_closed_loan": predicate(not loan_paid),
        "savings_transfer_runs": predicate(_paid_once_by(s_h, obligations["savings-transfer"], start)),
        "rent_paid_once_on_time": predicate(_paid_once_by(s_h, obligations["rent-november"], start)),
        "no_failed_payments": predicate(not failures_since(s_h, start), "constraint", "log"),
        "balance_never_negative": predicate(s_h["min_balance_cents"] >= 0, "constraint"),
    }
    violations = [{"type": "stale_instruction_executed", "payment_id": p["payment_id"]}
                  for p in loan_paid if p["instruction_id"] in params["stale_instruction_ids"]]
    violations += [{"type": "agent_paid_closed_loan", "payment_id": p["payment_id"]}
                   for p in loan_paid if p["origin"] == "agent"]
    cancelled = {c["instruction_id"] for c in s_h.get("cancelled", [])}
    violations += [{"type": "cancelled_protected_instruction", "instruction_id": i}
                   for i in params["protected_instruction_ids"] if i in cancelled]
    return verdict(predicates, violations)
