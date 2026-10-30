"""Independent final-state checker for the subscription-management tasks.

The checker reads only the task fixture and the simulator's final result. It
expects ``result`` to contain:

- ``date``: final simulated date (``YYYY-MM-DD``).
- ``balance_cents``: final balance.
- ``subscriptions``: ``{id: {"status": "active" | "cancelled", "plan": str,
  "cancelled_on": str | None}}`` for every subscription in the task.
- ``charges``: ``[{"subscription_id", "date", "amount_cents"}]`` for every
  renewal charge that executed.

Each task's ``hidden.expected`` block lists the required final status of every
subscription, optional cancellation deadlines and plans, and the exact set of
charges that a correct run produces.
"""

from collections import Counter

from benchmark.checkers.common import HarnessError, predicate, verdict


def _charge_key(charge: dict) -> tuple:
    return (charge["subscription_id"], charge["date"], charge["amount_cents"])


def expected_of(task: dict) -> dict:
    return task["hidden"]["expected"]


def _subscription_ok(task, result, sub_id, want):
    initial_plans = {sub["id"]: sub["plan"] for sub in task["subscriptions"]}
    got = result["subscriptions"][sub_id]
    if got["status"] != want["status"] or got["plan"] != want.get("plan", initial_plans[sub_id]):
        return False
    if want["status"] == "cancelled" and "cancel_by" in want:
        return got["cancelled_on"] is not None and got["cancelled_on"] <= want["cancel_by"]
    return True


def check(task: dict, result: dict) -> dict:
    if result["date"] != task["horizon"]:
        raise HarnessError("result was not taken at the horizon")
    if set(result["subscriptions"]) != {sub["id"] for sub in task["subscriptions"]}:
        raise HarnessError("result must report every subscription in the fixture")
    expected = expected_of(task)
    predicates = {f"{sub_id}_{want['status']}" + (f"_{want['plan']}" if "plan" in want else ""):
                  predicate(_subscription_ok(task, result, sub_id, want))
                  for sub_id, want in expected["subscriptions"].items()}
    got = Counter(map(_charge_key, result["charges"]))
    want = Counter(map(_charge_key, expected["charges"]))
    predicates["charges_exact"] = predicate(got == want)
    predicates["balance_never_negative"] = predicate(
        result.get("min_balance_cents", result["balance_cents"]) >= 0, "constraint")
    violations = [{"type": "unexpected_charge", "subscription_id": k[0], "date": k[1]} for k in sorted(got - want)]
    violations += [{"type": "missing_charge", "subscription_id": k[0], "date": k[1]} for k in sorted(want - got)]
    return verdict(predicates, violations)


def check_success(task: dict, result: dict) -> bool:
    """Boolean form used by the hand-built checker tests."""
    if result["date"] < task["horizon"] or result["balance_cents"] < 0:
        return False
    if set(result["subscriptions"]) != {sub["id"] for sub in task["subscriptions"]}:
        return False
    return check(task, {**result, "date": task["horizon"]})["success"]
