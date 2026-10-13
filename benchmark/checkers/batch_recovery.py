"""Checker for batch_recovery: redo only the bounced part of a seeded batch.

Read at S_h. Obligations still open at the start (their seeded payment failed)
must settle in full by their deadline. No obligation may receive more than it
is owed, counting seeded payments and anything still pending. Seeded failures
(dated before the start) are ignored; any failure on or after it counts.
"""

from benchmark.checkers.common import failures_since, payments_to, predicate, require_horizon, verdict


def check(task, s_stop, s_h):
    require_horizon(task, s_h)
    start = task["start_date"]
    seeded = task.get("initial_history", {}).get("payments", [])
    late = task["hidden"]["checker_params"].get("late_consequences", {})
    predicates, violations = {}, []
    for obligation in task["hidden"]["obligations"]:
        records, amount = set(obligation["records"]), obligation["amount_cents"]
        covered_at_start = sum(p["amount_cents"] for p in seeded if p["bill_id"] in records)
        paid = payments_to(s_h, records)
        if covered_at_start < amount:
            on_time = sum(p["amount_cents"] for p in paid if p["executed_on"] <= obligation["due_date"])
            predicates[f"settled_on_time:{obligation['id']}"] = predicate(on_time >= amount)
            if on_time < amount:
                violations.append({"type": "late_or_missed", "obligation": obligation["id"], **late.get(obligation["id"], {})})
        committed = sum(p["amount_cents"] for p in paid) + sum(
            i["amount_cents"] for i in s_h["pending"] if i["bill_id"] in records)
        predicates[f"not_overpaid:{obligation['id']}"] = predicate(committed <= amount, "constraint")
        if committed > amount:
            violations.append({"type": "overpayment", "obligation": obligation["id"], "excess_cents": committed - amount})
    failed = failures_since(s_h, start)
    predicates["no_agent_failures"] = predicate(not failed, "constraint", "log")
    violations += [{"type": "new_failure", "record": f["bill_id"], "date": f["date"]} for f in failed]
    return verdict(predicates, violations)
