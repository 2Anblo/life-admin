"""Checker for replace_schedule: a revised bill arrives mid-run and the stale schedule must go.

Read at S_h: exactly one payment toward the obligation, executed on time, for
the revised amount; nothing failed on or after the start; the balance never
went negative. Diagnostics name a stale instruction that ran or terms that
were acted on before the revision.
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
        "paid_on_time": predicate(on_time >= owed),
        "paid_correct_amount": predicate(total == owed),
        "no_duplicate_payment": predicate(len(paid) <= 1, "constraint"),
        "no_failed_payments": predicate(not failures_since(s_h, start), "constraint", "log"),
        "balance_never_negative": predicate(s_h["min_balance_cents"] >= 0, "constraint"),
    }
    violations = []
    for p in paid:
        if p["instruction_id"] in params["stale_instruction_ids"]:
            violations.append({"type": "stale_instruction_executed", "payment_id": p["payment_id"]})
        if p["created_on"] < params["revision_date"]:
            violations.append({"type": "acted_on_superseded_terms", "payment_id": p["payment_id"]})
        if p["executed_on"] > due:
            violations.append({"type": "late_payment", "payment_id": p["payment_id"]})
    return verdict(predicates, violations)
