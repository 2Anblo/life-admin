"""Independent final-state checker for the subscription-management tasks.

The checker reads only the task fixture and the simulator's final result. It
expects ``result`` to contain:

- ``date``: final simulated date (``YYYY-MM-DD``).
- ``balance_cents``: final balance.
- ``subscriptions``: ``{id: {"status": "active" | "cancelled", "plan": str,
  "cancelled_on": str | None}}`` for every subscription in the task.
- ``charges``: ``[{"subscription_id", "date", "amount_cents"}]`` for every
  renewal charge that executed.

Each task's hidden ``expected`` block lists the required final status of every
subscription, optional cancellation deadlines and plans, and the exact set of
charges that a correct run produces.
"""

from collections import Counter


def _charge_key(charge: dict) -> tuple:
    return (charge["subscription_id"], charge["date"], charge["amount_cents"])


def check_success(task: dict, result: dict) -> bool:
    if result["date"] < task["horizon"] or result["balance_cents"] < 0:
        return False

    expected = task["expected"]
    initial_plans = {sub["id"]: sub["plan"] for sub in task["subscriptions"]}
    subscriptions = result["subscriptions"]
    if set(subscriptions) != set(initial_plans):
        return False

    for sub_id, want in expected["subscriptions"].items():
        got = subscriptions[sub_id]
        if got["status"] != want["status"]:
            return False
        if got["plan"] != want.get("plan", initial_plans[sub_id]):
            return False
        if want["status"] == "cancelled" and "cancel_by" in want:
            if got["cancelled_on"] is None or got["cancelled_on"] > want["cancel_by"]:
                return False

    return Counter(map(_charge_key, result["charges"])) == Counter(map(_charge_key, expected["charges"]))
