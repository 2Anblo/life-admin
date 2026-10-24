"""Checker for credit_card_statement_balance: pay the statement balance, not the minimum or the current balance.

Read at S_h: the agent's payments to the card record that executed by the due
date add up to exactly the statement balance, and nothing more was paid during
the run. Splitting the amount across several payments is allowed. The statement's
"claim" in document_claims is the minimum due, because that is the line the
statement labels as the payment due; the interest-free amount is a separate line.
"""

from benchmark.checkers.common import failures_since, payments_to, predicate, require_horizon, verdict


def check(task, s_stop, s_h):
    require_horizon(task, s_h)
    obligation = task["hidden"]["obligations"][0]
    params = task["hidden"]["checker_params"]
    start, owed, due = task["start_date"], obligation["amount_cents"], obligation["due_date"]
    paid = [p for p in payments_to(s_h, obligation["records"]) if p["executed_on"] >= start]
    on_time = sum(p["amount_cents"] for p in paid if p["executed_on"] <= due)
    total = sum(p["amount_cents"] for p in paid)
    predicates = {
        "statement_balance_paid_on_time": predicate(on_time >= owed),
        "no_more_than_statement_balance": predicate(total <= owed, "constraint"),
        "no_failed_payments": predicate(not failures_since(s_h, start), "constraint", "log"),
        "balance_never_negative": predicate(s_h["min_balance_cents"] >= 0, "constraint"),
    }
    violations = []
    if total == params["minimum_due_cents"]:
        violations.append({"type": "paid_minimum_only"})
    if total == params["current_balance_cents"]:
        violations.append({"type": "paid_current_balance"})
    violations += [{"type": "late_payment", "payment_id": p["payment_id"]} for p in paid if p["executed_on"] > due]
    return verdict(predicates, violations)
