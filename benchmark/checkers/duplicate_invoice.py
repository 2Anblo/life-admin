"""Checker for duplicate_invoice: pay each real obligation once, whichever record it appears on.

Read at S_h. One water invoice appears on two records and two Northline bills
look identical; each obligation must be paid in full by its due date, and no
obligation may receive more than it is owed.
"""

from benchmark.checkers.common import payments_to, predicate, require_horizon, verdict


def check(task, s_stop, s_h):
    require_horizon(task, s_h)
    start = task["start_date"]
    predicates, violations, no_duplicates = {}, [], True
    for obligation in task["hidden"]["obligations"]:
        paid = payments_to(s_h, obligation["records"])
        on_time = sum(p["amount_cents"] for p in paid if start <= p["executed_on"] <= obligation["due_date"])
        total = sum(p["amount_cents"] for p in paid)
        predicates[f"paid_on_time[{obligation['id']}]"] = predicate(on_time >= obligation["amount_cents"])
        if on_time < obligation["amount_cents"]:
            kind = "late_payment" if total >= obligation["amount_cents"] else "missed_obligation"
            violations.append({"type": kind, "obligation": obligation["id"]})
        if total > obligation["amount_cents"]:
            no_duplicates = False
            violations.append({"type": "duplicate_settlement", "obligation": obligation["id"],
                               "excess_cents": total - obligation["amount_cents"]})
    predicates["no_duplicate_settlement"] = predicate(no_duplicates, "constraint")
    return verdict(predicates, violations)
