"""Shared helpers for v2 checkers: pure functions over saved snapshots.

A checker never receives live objects. It gets the pristine fixture read from
disk and two snapshots, S_stop (when the agent stopped) and S_h (at the horizon,
after the evaluator's rollout); each snapshot carries the action log.
"""


def predicate(ok, kind="goal", checkpoint="horizon"):
    return {"ok": bool(ok), "type": kind, "checkpoint": checkpoint}


def verdict(predicates, violations=()):
    return {
        "success": all(p["ok"] for p in predicates.values()),
        "predicates": predicates,
        "violations": list(violations),
    }


def payments_to(snapshot, records):
    """Every executed payment (any origin) to the given records."""
    records = set(records)
    return [p for p in snapshot["payments"] if p["bill_id"] in records]


def failures_since(snapshot, start_date):
    """Failed executions on or after the start date count against the agent."""
    return [f for f in snapshot["failed_payments"] if f["date"] >= start_date]


def total_paid(snapshot, records, by=None):
    return sum(p["amount_cents"] for p in payments_to(snapshot, records)
               if by is None or p["executed_on"] <= by)



class HarnessError(RuntimeError):
    """The trial cannot be scored (a harness or fixture problem, never the agent's)."""


def require_horizon(task, s_h):
    if s_h["date"] != task["horizon"]:
        raise HarnessError("S_h was not taken at the horizon")
