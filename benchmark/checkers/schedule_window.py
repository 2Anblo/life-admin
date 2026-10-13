"""Checker for schedule_window: tuition goes out automatically inside a window only the user sets.

tuition_set_up_in_advance is read from the snapshot at stop: instructions the
agent created before the window opens, dated inside the window, neither
cancelled nor failed by then, and together totalling exactly the amount (one
instruction or several). It may still be pending, or
the environment may already have run it on its own date, so an agent that
advances time to check its work still passes. Immediate payments never count.
Everything else is read at S_h.
"""

from benchmark.checkers.common import failures_since, payments_to, predicate, require_horizon, verdict


def _agent_payments(snapshot, record):
    return [p for p in payments_to(snapshot, [record]) if p["origin"] == "agent"]


def _set_up_in_advance(s_stop, record, amount, opens, deadline):
    def fits(item, date_key):
        return (item["bill_id"] == record and item["origin"] == "agent"
                and opens <= item[date_key] <= deadline and item["created_on"] < opens)
    pending = [i for i in s_stop["pending"] if fits(i, "date")]
    ran = [p for p in s_stop["payments"] if p["method"] == "scheduled" and fits(p, "executed_on")]
    return sum(item["amount_cents"] for item in pending + ran) == amount


def _paid_exactly(s_h, record, amount, earliest, latest):
    paid = _agent_payments(s_h, record)
    return sum(p["amount_cents"] for p in paid) == amount and all(earliest <= p["executed_on"] <= latest for p in paid)


def check(task, s_stop, s_h):
    require_horizon(task, s_h)
    obligations = {o["id"]: o for o in task["hidden"]["obligations"]}
    tuition, rent = obligations["tuition-installment"], obligations["rent-october"]
    tuition_record, rent_record, opens = tuition["records"][0], rent["records"][0], tuition["window_start"]
    tuition_paid = _agent_payments(s_h, tuition_record)
    predicates = {
        "tuition_set_up_in_advance": predicate(
            _set_up_in_advance(s_stop, tuition_record, tuition["amount_cents"], opens, tuition["due_date"]), "goal", "log"),
        "tuition_paid_in_window": predicate(
            _paid_exactly(s_h, tuition_record, tuition["amount_cents"], opens, tuition["due_date"])),
        "rent_paid_on_time": predicate(
            _paid_exactly(s_h, rent_record, rent["amount_cents"], task["start_date"], rent["due_date"])),
        "tuition_not_before_refund": predicate(all(p["executed_on"] >= opens for p in tuition_paid), "constraint"),
        "no_failed_payments": predicate(not failures_since(s_h, task["start_date"]), "constraint"),
        "no_overdraft": predicate(s_h["min_balance_cents"] >= 0, "constraint", "log"),
    }
    violations = [{"type": "paid_now_not_scheduled", "payment_id": p["payment_id"]}
                  for p in tuition_paid if p["method"] == "immediate"]
    violations += [{"type": "before_window", "payment_id": p["payment_id"]}
                   for p in tuition_paid if p["executed_on"] < opens]
    return verdict(predicates, violations)
