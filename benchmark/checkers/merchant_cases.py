"""Independent checker for refund and dispute tasks (schema_version 3).

The checker reads only the task
fixture and the final snapshot S_h produced by LifeWorkspace:

- ``date``: final simulated date; must equal the task horizon.
- ``actions``: every agent tool call in order, as
  ``{"date", "tool", "args", "outcome": "accepted" | "rejected", "reason"?}``.
  Read-only calls may be included and are ignored. A rejected
  ``escalate_case`` must carry ``reason: "premature"`` when it was refused for
  being early.
- ``ledger``: every posting dated on or after the start date, as
  ``{"transaction_id", "date", "account", "amount_cents", "kind", ...}``;
  credits are positive and refunds have ``kind: "refund"``.
- ``cases``: ``{case_id: {"status": "open" | "closed", "closed_on",
  "matched_transaction_id"}}`` for every case in the fixture.

``task["hidden"]["expected"]`` describes a correct run:

- ``refund_requests``: the exact accepted ``request_refund`` calls, as
  ``{"order_id", "items", "allocations": [{"account", "amount_cents"}], "by"}``.
  A call without ``allocations`` goes to ``refund_policy.default_destination``.
- ``escalations``: the exact accepted ``escalate_case`` calls, as
  ``{"case_id", "from", "to"}`` (inclusive date window).
- ``closures``: cases that must end closed, as ``{"case_id", "not_before",
  "matched_transaction_id"?}``. Every other case must end open.
- ``disputes``: the exact accepted ``open_dispute`` calls, as
  ``{"transaction_ids": [...any one of...], "reason"}``.
- ``refund_credits``: refund money that must arrive, as ``{"account",
  "amount_cents", "by"}``. Refund credits in the run must match these totals
  exactly, so extra or misdirected refunds fail.
"""

from collections import Counter, defaultdict

from benchmark.checkers.common import HarnessError, predicate, verdict


def _accepted(result, tool):
    return [a for a in result["actions"] if a["tool"] == tool and a["outcome"] == "accepted"]


def _match_exactly(actual, expected, matches):
    """True when every expected item is matched by a distinct actual item and nothing is left over."""
    if len(actual) != len(expected):
        return False
    remaining = list(actual)
    for want in expected:
        hit = next((a for a in remaining if matches(a, want)), None)
        if hit is None:
            return False
        remaining.remove(hit)
    return True


def _allocations(task, args):
    allocations = args.get("allocations")
    if not allocations:
        default = task.get("refund_policy", {}).get("default_destination")
        allocations = [{"account": default, "amount_cents": args.get("amount_cents")}]
    return Counter((a.get("account"), a.get("amount_cents")) for a in allocations)


def _request_matches(task):
    def matches(action, want):
        args = action["args"]
        return (args.get("order_id") == want["order_id"]
                and sorted(args.get("items") or []) == sorted(want["items"])
                and _allocations(task, args) == Counter((a["account"], a["amount_cents"]) for a in want["allocations"])
                and action["date"] <= want["by"])
    return matches


def _escalation_matches(action, want):
    return action["args"].get("case_id") == want["case_id"] and want["from"] <= action["date"] <= want["to"]


def _dispute_matches(action, want):
    args = action["args"]
    return args.get("transaction_id") in want["transaction_ids"] and args.get("reason") == want["reason"]


def _closures_ok(task, result, expected):
    closures = {c["case_id"]: c for c in expected.get("closures", [])}
    closed_by_agent = defaultdict(list)
    for action in _accepted(result, "close_case"):
        closed_by_agent[action["args"].get("case_id")].append(action)
    for case in task.get("cases", []):
        case_id = case["case_id"]
        final = result["cases"][case_id]
        want = closures.get(case_id)
        if want is None:
            if final["status"] != "open" or closed_by_agent[case_id]:
                return False
            continue
        if final["status"] != "closed" or len(closed_by_agent[case_id]) != 1:
            return False
        if closed_by_agent[case_id][0]["date"] < want["not_before"]:
            return False
        if "matched_transaction_id" in want and final.get("matched_transaction_id") != want["matched_transaction_id"]:
            return False
    return True


def _refund_credits_ok(task, result, expected):
    start = task["start_date"]
    refunds = [e for e in result["ledger"] if e["kind"] == "refund" and e["amount_cents"] > 0 and e["date"] >= start]
    want_total = Counter()
    for want in expected.get("refund_credits", []):
        want_total[want["account"]] += want["amount_cents"]
        arrived = sum(e["amount_cents"] for e in refunds if e["account"] == want["account"] and e["date"] <= want["by"])
        if arrived < want["amount_cents"]:
            return False
    got_total = Counter()
    for entry in refunds:
        got_total[entry["account"]] += entry["amount_cents"]
    return +got_total == +want_total


def check(task, result):
    if result["date"] != task["horizon"]:
        raise HarnessError("result was not taken at the horizon")
    if set(result["cases"]) != {c["case_id"] for c in task.get("cases", [])}:
        raise HarnessError("result must report every case in the fixture")
    expected = task["hidden"]["expected"]
    premature = [a for a in result["actions"]
                 if a["tool"] == "escalate_case" and a["outcome"] == "rejected" and a.get("reason") == "premature"]
    predicates = {
        "refund_requests_correct": predicate(_match_exactly(
            _accepted(result, "request_refund"), expected.get("refund_requests", []), _request_matches(task))),
        "escalations_correct": predicate(_match_exactly(
            _accepted(result, "escalate_case"), expected.get("escalations", []), _escalation_matches)),
        "cases_closed_correctly": predicate(_closures_ok(task, result, expected)),
        "disputes_correct": predicate(_match_exactly(
            _accepted(result, "open_dispute"), expected.get("disputes", []), _dispute_matches), "constraint"),
        "refund_money_correct": predicate(_refund_credits_ok(task, result, expected)),
        "no_premature_escalation": predicate(not premature, "constraint", "log"),
    }
    violations = [{"type": "premature_escalation", "date": a["date"], "case_id": a["args"].get("case_id")}
                  for a in premature]
    return verdict(predicates, violations)
