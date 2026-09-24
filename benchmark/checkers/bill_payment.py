"""Independent final-state checker for the first benchmark task."""

from collections import Counter


def check_success(task: dict, result: dict) -> bool:
    if result["date"] < task["horizon"] or result["balance_cents"] < 0 or result["overdraft_count"]:
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
