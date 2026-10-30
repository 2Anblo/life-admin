"""Independent checker for reminder tasks (schema_version 3).

``result`` (the final snapshot S_h) must contain:

- ``date``: final simulated date; must equal the task horizon.
- ``actions``: agent tool calls as ``{"date", "tool", "args", "outcome"}``.

``task["hidden"]["expected"]["reminder_dates"]`` lists the exact dates of the
reminders a correct run creates. Accepted ``create_reminder`` calls must match
them one to one: a missing, extra, duplicate, or misdated reminder fails.
Reminder text is not scored.
"""

from collections import Counter

from benchmark.checkers.common import HarnessError, predicate, verdict


def check(task, result):
    if result["date"] != task["horizon"]:
        raise HarnessError("result was not taken at the horizon")
    created = [a for a in result["actions"] if a["tool"] == "create_reminder" and a["outcome"] == "accepted"]
    got = Counter(a["args"].get("date") for a in created)
    want = Counter(task["hidden"]["expected"]["reminder_dates"])
    predicates = {
        "required_reminders_set": predicate(all(got[d] >= n for d, n in want.items())),
        "no_extra_reminders": predicate(not (got - want), "constraint"),
    }
    violations = [{"type": "missing_reminder", "date": d} for d in sorted(want - got)]
    violations += [{"type": "unexpected_reminder", "date": d} for d in sorted(got - want)]
    return verdict(predicates, violations)
