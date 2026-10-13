"""Checker for conflicting_notices: a signed extension outranks a newer automated reminder.

Settlement is read at S_h. Every obligation must be settled in full by its true
due date (hidden.obligations), with no obligation paid twice and no payment
failing on or after the start date.
"""

from benchmark.checkers.common import failures_since, payments_to, predicate, require_horizon, verdict


def check(task, s_stop, s_h):
    require_horizon(task, s_h)
    start = task["start_date"]
    predicates, violations, no_duplicates = {}, [], True
    for obligation in task["hidden"]["obligations"]:
        paid = [p for p in payments_to(s_h, obligation["records"]) if p["executed_on"] >= start]
        on_time = sum(p["amount_cents"] for p in paid if p["executed_on"] <= obligation["due_date"])
        total = sum(p["amount_cents"] for p in paid)
        predicates[f"settled_by_due[{obligation['id']}]"] = predicate(on_time >= obligation["amount_cents"])
        if total > obligation["amount_cents"]:
            no_duplicates = False
            violations.append({"type": "duplicate_payment", "obligation": obligation["id"]})
        violations += [{"type": "late_settlement", "obligation": obligation["id"], "date": p["executed_on"]}
                       for p in paid if p["executed_on"] > obligation["due_date"]]
    failed = failures_since(s_h, start)
    predicates["no_duplicate_payment"] = predicate(no_duplicates, "constraint")
    predicates["no_failed_payments"] = predicate(not failed, "constraint", "log")
    violations += [{"type": "returned_payment", "record": f["bill_id"], "date": f["date"]} for f in failed]
    return verdict(predicates, violations)
