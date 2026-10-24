"""Independent checker for move_out_closeout (schema_version 3; bills and subscriptions together).

The simulator does not support this task yet. ``result`` must contain:

- ``date``: final simulated date; must equal the task horizon.
- ``min_balance_cents``: lowest balance reached during the run.
- ``payments``: executed bill payments as ``{"bill_id", "amount_cents", "executed_on"}``.
- ``failed_payments``: failed bill payments as ``{"bill_id", "amount_cents", "date"}``.
- ``subscriptions``: ``{id: {"status", "plan", "cancelled_on"}}`` for every subscription.
- ``charges``: executed renewal charges as ``{"subscription_id", "date", "amount_cents"}``.

``task["hidden"]["expected"]`` lists each bill that must be paid once in full by
its due date, the required final status of every subscription (with an optional
cancellation deadline), and the exact set of renewal charges.
"""

from collections import Counter

from benchmark.checkers.common import HarnessError, predicate, verdict


def _charge_key(charge):
    return (charge["subscription_id"], charge["date"], charge["amount_cents"])


def _bill_paid_once_on_time(result, bill, start):
    paid = [p for p in result["payments"] if p["bill_id"] == bill["bill_id"] and p["executed_on"] >= start]
    return (len(paid) == 1 and paid[0]["amount_cents"] == bill["amount_cents"]
            and paid[0]["executed_on"] <= bill["due_date"])


def _subscriptions_ok(task, result, expected):
    initial_plans = {sub["id"]: sub["plan"] for sub in task["subscriptions"]}
    for sub_id, want in expected["subscriptions"].items():
        got = result["subscriptions"][sub_id]
        if got["status"] != want["status"] or got["plan"] != want.get("plan", initial_plans[sub_id]):
            return False
        if want["status"] == "cancelled" and "cancel_by" in want:
            if got["cancelled_on"] is None or got["cancelled_on"] > want["cancel_by"]:
                return False
    return True


def check(task, result):
    if result["date"] != task["horizon"]:
        raise HarnessError("result was not taken at the horizon")
    if set(result["subscriptions"]) != {sub["id"] for sub in task["subscriptions"]}:
        raise HarnessError("result must report every subscription in the fixture")
    expected = task["hidden"]["expected"]
    start = task["start_date"]
    predicates = {
        f"{bill['bill_id']}_paid_once_on_time": predicate(_bill_paid_once_on_time(result, bill, start))
        for bill in expected["bills"]
    }
    predicates["subscriptions_correct"] = predicate(_subscriptions_ok(task, result, expected))
    predicates["charges_exact"] = predicate(
        Counter(map(_charge_key, result["charges"])) == Counter(map(_charge_key, expected["charges"])))
    predicates["no_failed_payments"] = predicate(
        not [f for f in result["failed_payments"] if f["date"] >= start], "constraint", "log")
    predicates["balance_never_negative"] = predicate(result["min_balance_cents"] >= 0, "constraint")
    unexpected = Counter(map(_charge_key, result["charges"])) - Counter(map(_charge_key, expected["charges"]))
    violations = [{"type": "unexpected_charge", "subscription_id": k[0], "date": k[1]} for k in sorted(unexpected)]
    return verdict(predicates, violations)
