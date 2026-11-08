"""Independent checkers for Avoid Late Fees.

``check_success`` scores the v1 fixture (benchmark/legacy/avoid_late_fees_v1.json)
on the v1 simulator's final result. ``check`` scores the benchmark fixture
(benchmark/tasks/avoid_late_fees.json) on v2/v3 snapshots, like every other task.
"""

from collections import Counter

from benchmark.checkers.common import payments_to, predicate, require_horizon, verdict


def check_success(task: dict, result: dict) -> bool:
    if result["date"] < task["horizon"] or result["balance_cents"] < 0 or result["failed_payments"]:
        return False
    payments = result["payments"]
    counts = Counter(payment["bill_id"] for payment in payments)
    if set(counts) != {bill["id"] for bill in task["bills"]} or any(count != 1 for count in counts.values()):
        return False
    bills = {bill["id"]: bill for bill in task["bills"]}
    return all(
        payment["amount_cents"] == bills[payment["bill_id"]]["amount_cents"]
        and payment["date"] <= bills[payment["bill_id"]]["due_date"]
        for payment in payments
    )


def check(task: dict, s_stop: dict, s_h: dict) -> dict:
    """Each bill paid once, in full, by its due date; no failed payment; never overdrawn."""
    require_horizon(task, s_h)
    start = task["start_date"]
    predicates = {}
    for bill in task["bills"]:
        paid = [p for p in payments_to(s_h, [bill["id"]]) if p["executed_on"] >= start]
        predicates[f"{bill['id']}_paid_once_on_time"] = predicate(
            len(paid) == 1 and paid[0]["amount_cents"] == bill["amount_cents"]
            and paid[0]["executed_on"] <= bill["due_date"])
    failures = [f for f in s_h["failed_payments"] if f["date"] >= start]
    predicates["no_failed_payments"] = predicate(not failures, "constraint", "log")
    predicates["balance_never_negative"] = predicate(s_h["min_balance_cents"] >= 0, "constraint")
    violations = [{"type": "failed_payment", "bill_id": f["bill_id"], "date": f["date"]} for f in failures]
    return verdict(predicates, violations)
