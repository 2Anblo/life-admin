"""Scripted floor policies: cheap heuristics every v2 task must defeat.

Each policy is an agent ``agent(view, call_tool) -> stop_reason`` that uses only
the visible view and the tools, except where noted: P4 and P5 read
``hidden.document_claims`` (what each bill-like document *claims*, not what is
true), and P6 and the reference policy replay ``hidden.reference_plan``.
A task counts toward the 20 only if no floor policy solves its stated challenge.
"""


def p0_noop(view, call_tool):
    return "end_turn"


def p1_last_allowed_date(view, call_tool):
    """Pay each record on its displayed due date (or now, if that date is not in the future)."""
    for bill in view["bills"]:
        last = min(bill["due_date"], view["horizon"])
        if last <= view["start_date"]:
            call_tool("pay_bill", {"bill_id": bill["bill_id"]})
        else:
            call_tool("schedule_payment", {"bill_id": bill["bill_id"], "payment_date": last})
    return "end_turn"


def p2_greedy(view, call_tool):
    """Pay whatever fits now, earliest displayed due date first, then advance; repeat."""
    paid = set()
    while True:
        for bill in sorted(view["bills"], key=lambda b: b["due_date"]):
            if bill["bill_id"] not in paid:
                _, is_error = call_tool("pay_bill", {"bill_id": bill["bill_id"]})
                if not is_error:
                    paid.add(bill["bill_id"])
        if len(paid) == len(view["bills"]):
            return "end_turn"
        _, is_error = call_tool("advance_time", {})
        if is_error:
            return "end_turn"


def p3_pay_all_now(view, call_tool):
    for bill in view["bills"]:
        call_tool("pay_bill", {"bill_id": bill["bill_id"]})
    return "end_turn"


def _document_policy(task, newest):
    claims = task["hidden"].get("document_claims", [])
    start_docs = {d["id"] for d in task["documents"] if d.get("release_date") is None}

    def agent(view, call_tool):
        for bill in view["bills"]:
            own = [c for c in claims if c["bill_id"] == bill["bill_id"] and c["document_id"] in start_docs]
            chosen = (max if newest else min)(own, key=lambda c: c["issued"]) if own else bill
            due = min(chosen.get("due_date", bill["due_date"]), view["horizon"])
            args = {"bill_id": bill["bill_id"], "amount_cents": chosen.get("amount_cents", bill["amount_cents"])}
            if due <= view["start_date"]:
                call_tool("pay_bill", args)
            else:
                call_tool("schedule_payment", {**args, "payment_date": due})
        return "end_turn"

    return agent


def p4_newest_document(task):
    return _document_policy(task, newest=True)


def p5_oldest_document(task):
    return _document_policy(task, newest=False)


def reference_policy(task, stop_at_first_advance=False):
    """Replay the task's reference plan; with stop_at_first_advance it is P6 (open loop)."""

    def agent(view, call_tool):
        for step in task["hidden"]["reference_plan"]:
            if stop_at_first_advance and step["tool"] == "advance_time":
                break
            call_tool(step["tool"], step.get("args", {}))
        return "end_turn"

    return agent


def p6_open_loop(task):
    return reference_policy(task, stop_at_first_advance=True)


def all_floor_policies(task):
    return {
        "P0_noop": p0_noop,
        "P1_last_allowed_date": p1_last_allowed_date,
        "P2_greedy": p2_greedy,
        "P3_pay_all_now": p3_pay_all_now,
        "P4_newest_document": p4_newest_document(task),
        "P5_oldest_document": p5_oldest_document(task),
        "P6_open_loop": p6_open_loop(task),
    }
