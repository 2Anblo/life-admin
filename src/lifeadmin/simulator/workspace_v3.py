"""LifeAdmin workspace (v3): v2 bill payments plus subscriptions, refund cases,
card disputes and reminders.

Every v2 fixture runs unchanged on this workspace: the payment rules, action log,
document releases and same-day order are inherited from ``WorkspaceV2``. A
fixture adds the parts it needs:

- ``subscriptions`` with ``renewal`` and ``price_change`` events, and ``usage``;
- ``accounts``, ``orders``, ``cases`` and seeded card ``transactions``;
- a ``simulation`` block: the machine-readable form of the fixture's ``rules``
  (refund amounts and posting dates, escalation windows, scheduled postings,
  dispute credits). The agent never sees it.

Rules shared by every task:
- a cancellation stops a renewal only if made at least ``cancel_notice_days``
  (minimum 1) days before it; a later cancellation is accepted, but that
  renewal still charges;
- a plan change applies to the next renewal;
- merchants decide refund requests after the fact: an ineligible request is
  accepted by the form and declined the next day with a notice, so tool
  results never reveal eligibility;
- each returned item is refunded at most once.

Same-day order: deposits, then card postings, then new documents and bills,
then bill updates, then price changes, then renewals, then scheduled payments.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from lifeadmin.simulator.workspace_v2 import (
    AgentInputError,
    FixtureError,
    WorkspaceV2,
    _positive_int,
    is_iso_date,
)

V2_EVENT_TYPES = ("deposit", "release_document", "update_bill")
SUBSCRIPTION_EVENT_TYPES = ("renewal", "price_change")

EVENT_ORDER = {
    "deposit": 0,
    "post_transaction": 1,
    "release_document": 2,
    "release_bill": 2,
    "update_bill": 3,
    "price_change": 4,
    "renewal": 5,
}

DISPUTE_REASONS = ("duplicate", "not_received", "incorrect_amount", "unauthorized")
CASE_RESOLUTIONS = ("received", "withdrawn")
CHECKING_KINDS = ("checking", "debit_card")


def add_business_days(day: str, count: int) -> str:
    current = date.fromisoformat(day)
    while count > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            count -= 1
    return current.isoformat()


def next_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def days_before(day: str, count: int) -> str:
    return (date.fromisoformat(day) - timedelta(days=count)).isoformat()


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _v2_view(task: dict) -> dict:
    """The part of a fixture that v2's lint and setup understand."""
    view = deepcopy(task)
    view.setdefault("bills", [])
    view["events"] = [e for e in task.get("events", []) if e.get("type") in V2_EVENT_TYPES]
    return view


def lint_v3(task: dict) -> None:
    """Raise FixtureError for v3 parts that would silently misbehave (v2 parts are linted by v2)."""
    problems = []

    def need(condition, message):
        if not condition:
            problems.append(message)

    start, horizon = task["start_date"], task["horizon"]
    subs = {s.get("id"): s for s in task.get("subscriptions", [])}
    need(len(subs) == len(task.get("subscriptions", [])), "duplicate subscription ids")
    for sub in subs.values():
        need(sub.get("plan") in sub.get("plans", {}), f"subscription {sub.get('id')}: plan must be one of its plans")
        for plan_id, plan in sub.get("plans", {}).items():
            need(_positive_int(plan.get("price_cents")), f"subscription {sub.get('id')}: plan {plan_id} needs price_cents")
        need(isinstance(sub.get("cancel_notice_days", 0), int), f"subscription {sub.get('id')}: cancel_notice_days must be an integer")
    for event in task.get("events", []):
        if event.get("type") in V2_EVENT_TYPES:
            continue
        need(event.get("type") in SUBSCRIPTION_EVENT_TYPES, f"event {event.get('id')}: unknown type {event.get('type')}")
        need(event.get("subscription_id") in subs, f"event {event.get('id')}: unknown subscription")
        need(is_iso_date(event.get("date")) and start <= event["date"] <= horizon,
             f"event {event.get('id')}: date must be within the run")
        if event.get("type") == "renewal":
            need(event.get("date", "") > start, f"event {event.get('id')}: a renewal must be after start_date")
        if event.get("type") == "price_change":
            sub = subs.get(event.get("subscription_id"), {})
            need(event.get("plan") in sub.get("plans", {}), f"event {event.get('id')}: unknown plan")
            need(_positive_int(event.get("price_cents")), f"event {event.get('id')}: price_cents must be a positive integer")

    accounts = {a.get("id") for a in task.get("accounts", [])}
    for order in task.get("orders", []):
        for tender in order.get("tenders", []):
            need(tender.get("account") in accounts, f"order {order.get('order_id')}: unknown tender account")
    policy = task.get("refund_policy", {})
    if policy.get("default_destination") is not None:
        need(policy["default_destination"] in accounts, "refund_policy.default_destination must be an account")
    for tx in task.get("transactions", []):
        need(tx.get("account") in accounts, f"transaction {tx.get('transaction_id')}: unknown account")
    need(len({c.get("case_id") for c in task.get("cases", [])}) == len(task.get("cases", [])), "duplicate case ids")

    sim = task.get("simulation", {})
    orders = {o.get("order_id") for o in task.get("orders", [])}
    cases = {c.get("case_id") for c in task.get("cases", [])}
    for order_id in sim.get("refunds", {}):
        need(order_id in orders, f"simulation.refunds: unknown order {order_id}")
    for case_id in sim.get("escalations", {}):
        need(case_id in cases, f"simulation.escalations: unknown case {case_id}")
    postings = list(sim.get("postings", []))
    postings += [c["posting"] for c in sim.get("dispute_credits", [])]
    postings += [e["effect"] for e in sim.get("escalations", {}).values() if e.get("effect", {}).get("type") == "credit"]
    for posting in postings:
        need(posting.get("account") in accounts, "simulation posting: unknown account")
        need(is_iso_date(posting.get("date")) and start < posting["date"] <= horizon,
             "simulation posting: date must be after start_date and by the horizon")
    if problems:
        raise FixtureError("; ".join(problems))


class LifeWorkspace(WorkspaceV2):
    def __init__(self, task: dict):
        v2_task = _v2_view(task)
        super().__init__(v2_task)
        lint_v3(task)
        self.task = deepcopy(task)
        self.simulation = deepcopy(task.get("simulation", {}))

        # Bills that appear during the run.
        self.unreleased_bills = {b["id"]: b["release_date"] for b in v2_task["bills"] if b.get("release_date")}
        for bill_id, release in self.unreleased_bills.items():
            self.events.append({"id": f"release-bill-{bill_id}", "date": release, "type": "release_bill",
                                "bill_id": bill_id})

        # Subscriptions.
        self.subscriptions = {}
        for sub in task.get("subscriptions", []):
            self.subscriptions[sub["id"]] = {**deepcopy(sub), "status": "active", "cancelled_on": None}
        self.usage = deepcopy(task.get("usage", {}))
        self.charges = []

        # Money outside the bill-pay service.
        self.accounts = {a["id"]: deepcopy(a) for a in task.get("accounts", [])}
        self.orders = {o["order_id"]: deepcopy(o) for o in task.get("orders", [])}
        self.cases = {c["case_id"]: {**deepcopy(c), "status": "open", "closed_on": None,
                                     "matched_transaction_id": None, "resolution": None}
                      for c in task.get("cases", [])}
        self.ledger = deepcopy(task.get("transactions", []))
        self.refunded_items = set()
        self.withheld = {}          # case_id -> {"account": ..., "amount_cents": ...}
        self.escalated = set()      # cases whose escalation effect already fired
        self.disputes = []
        self.dispute_credits_used = set()
        self.reminders = []
        self.refund_requests = []
        self._counters.update({"generated": 0, "request": 0, "posting": 0, "reminder": 0})

        for event in task.get("events", []):
            if event["type"] in SUBSCRIPTION_EVENT_TYPES:
                self.events.append(deepcopy(event))
        for posting in self.simulation.get("postings", []):
            self._schedule_posting(posting)

        # Events dated on the start date (a price already in effect) apply before the agent starts.
        for event in sorted((e for e in self.events if e["date"] == self.today), key=self._event_key):
            self._apply_event(event, [], "setup")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _event_key(self, event):
        return (EVENT_ORDER[event["type"]], self.events.index(event))

    def _new_id(self, kind, prefix):
        self._counters[kind] += 1
        return f"{prefix}-{self._counters[kind]}"

    def _schedule(self, event_date, kind, **payload):
        """Add an environment event caused by an agent action. Events past the horizon never happen."""
        if event_date > self.horizon:
            return None
        event = {"id": self._new_id("generated", "gen"), "date": event_date, "type": kind, **payload}
        self.events.append(event)
        return event

    def _schedule_posting(self, posting):
        posting = deepcopy(posting)
        posting_date = posting.pop("date")
        posting.setdefault("transaction_id", self._new_id("posting", "tx-sim"))
        return self._schedule(posting_date, "post_transaction", posting=posting)

    def _schedule_notice(self, notice_date, title, text):
        if notice_date > self.horizon:
            return
        doc_id = self._new_id("generated", "notice")
        self.documents[doc_id] = {"id": doc_id, "title": title, "text": text, "release_date": notice_date}
        self._schedule(notice_date, "release_document", document_id=doc_id)

    def _accept(self, action, args, result, **refs):
        self.log("agent", action, args, "accepted", **refs)
        return result

    # ------------------------------------------------------------------
    # Bills that appear during the run
    # ------------------------------------------------------------------

    def _record(self, action, args, bill_id):
        if isinstance(bill_id, str) and bill_id in self.unreleased_bills:
            self.reject(action, args, f"Unknown bill ID: {bill_id}")
        return super()._record(action, args, bill_id)

    # ------------------------------------------------------------------
    # Reading tools
    # ------------------------------------------------------------------

    def get_activity(self) -> dict:
        activity = super().get_activity()
        if self.accounts or self.ledger:
            activity["card_transactions"] = [
                {k: tx[k] for k in ("transaction_id", "date", "account", "amount_cents", "kind",
                                    "descriptor", "reference", "time") if k in tx}
                for tx in sorted(self.ledger, key=lambda t: t["date"]) if tx["date"] <= self.today
            ]
        if self.subscriptions:
            activity["subscription_charges"] = deepcopy(self.charges)
        return activity

    def list_subscriptions(self) -> list:
        self.log("agent", "list_subscriptions", {}, "read")
        return [{"subscription_id": sub_id, "name": sub["name"], "status": sub["status"], "plan": sub["plan"],
                 "plans": deepcopy(sub["plans"]), "billing_period": sub.get("billing_period", "monthly")}
                for sub_id, sub in self.subscriptions.items()]

    def get_usage(self, subscription_id: str) -> dict:
        args = {"subscription_id": subscription_id}
        self._subscription("get_usage", args, subscription_id)
        self.log("agent", "get_usage", args, "read")
        return {"subscription_id": subscription_id, "usage": deepcopy(self.usage.get(subscription_id, {}))}

    def list_cases(self) -> list:
        self.log("agent", "list_cases", {}, "read")
        fields = ("case_id", "merchant", "order_id", "amount_cents", "reference", "status_shown", "opened_on")
        return [{**{k: case[k] for k in fields if k in case}, "status": case["status"]}
                for case in self.cases.values()]

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def _subscription(self, action, args, subscription_id):
        if not isinstance(subscription_id, str) or subscription_id not in self.subscriptions:
            self.reject(action, args, f"Unknown subscription ID: {subscription_id}")
        return self.subscriptions[subscription_id]

    def cancel_subscription(self, subscription_id: str) -> dict:
        args = {"subscription_id": subscription_id}
        sub = self._subscription("cancel_subscription", args, subscription_id)
        if sub["status"] == "cancelled":
            self.reject("cancel_subscription", args, "This subscription is already cancelled")
        sub["status"], sub["cancelled_on"] = "cancelled", self.today
        return self._accept("cancel_subscription", args,
                            {"subscription_id": subscription_id, "status": "cancelled", "cancelled_on": self.today})

    def change_plan(self, subscription_id: str, plan: str) -> dict:
        args = {"subscription_id": subscription_id, "plan": plan}
        sub = self._subscription("change_plan", args, subscription_id)
        if sub["status"] == "cancelled":
            self.reject("change_plan", args, "This subscription is cancelled")
        if plan not in sub["plans"]:
            self.reject("change_plan", args, f"Unknown plan for {subscription_id}: {plan}. Plans: {', '.join(sub['plans'])}")
        sub["plan"] = plan
        return self._accept("change_plan", args, {"subscription_id": subscription_id, "plan": plan,
                                                  "applies_from": "next renewal"})

    def _renew(self, event):
        sub = self.subscriptions[event["subscription_id"]]
        if sub["status"] == "cancelled":
            notice = max(sub.get("cancel_notice_days", 0), 1)
            if sub["cancelled_on"] <= days_before(event["date"], notice):
                return None
        amount = sub["plans"][sub["plan"]]["price_cents"]
        self.balance_cents -= amount
        self.min_balance_cents = min(self.min_balance_cents, self.balance_cents)
        charge = {"subscription_id": sub["id"], "date": self.today, "amount_cents": amount, "plan": sub["plan"]}
        self.charges.append(charge)
        return charge

    # ------------------------------------------------------------------
    # Refunds, cases and disputes
    # ------------------------------------------------------------------

    def _check_allocations(self, action, args, allocations):
        if not isinstance(allocations, list) or not allocations:
            self.reject(action, args, "allocations must be a non-empty list of {account, amount_cents}")
        for allocation in allocations:
            if not isinstance(allocation, dict) or set(allocation) != {"account", "amount_cents"}:
                self.reject(action, args, "Each allocation must be an object with account and amount_cents")
            if allocation["account"] not in self.accounts:
                self.reject(action, args, f"Unknown account: {allocation['account']}")
            if not _positive_int(allocation["amount_cents"]):
                self.reject(action, args, "amount_cents must be a positive whole number of cents")

    def request_refund(self, order_id: str, items: list, amount_cents: int | None = None,
                       allocations: list | None = None) -> dict:
        args = {"order_id": order_id, "items": deepcopy(items)}
        if amount_cents is not None:
            args["amount_cents"] = amount_cents
        if allocations is not None:
            args["allocations"] = deepcopy(allocations)
        case_orders = {c["order_id"]: c for c in self.cases.values() if c.get("order_id")}
        if order_id not in self.orders and order_id not in case_orders:
            self.reject("request_refund", args, f"Unknown order ID: {order_id}")
        if not isinstance(items, list) or not items or not all(isinstance(i, str) and i for i in items):
            self.reject("request_refund", args, "items must be a non-empty list of item IDs")
        if len(set(items)) != len(items):
            self.reject("request_refund", args, "List each item once")
        if amount_cents is not None and not _positive_int(amount_cents):
            self.reject("request_refund", args, "amount_cents must be a positive whole number of cents")
        if allocations is not None:
            self._check_allocations("request_refund", args, allocations)
        order = self.orders.get(order_id)
        if order is not None:
            known = {item["item_id"] for item in order["items"]}
            unknown = [i for i in items if i not in known]
            if unknown:
                self.reject("request_refund", args, f"Not part of order {order_id}: {', '.join(unknown)}")

        request_id = self._new_id("request", "rr")
        self.refund_requests.append({"request_id": request_id, "date": self.today, **deepcopy(args)})
        self._decide_refund(request_id, order_id, order, items, amount_cents, allocations)
        return self._accept("request_refund", args,
                            {"request_id": request_id, "order_id": order_id, "status": "submitted",
                             "message": "Request submitted. The merchant reviews it and posts any approved refund."},
                            request_id=request_id)

    def _decide_refund(self, request_id, order_id, order, items, amount_cents, allocations):
        merchant = order["merchant"] if order else next(c["merchant"] for c in self.cases.values()
                                                        if c.get("order_id") == order_id)
        title = f"{merchant}: refund request {request_id}"
        if order is None:
            self._schedule_notice(next_day(self.today), title,
                                  f"{merchant} - refund request {request_id} for order {order_id}\n"
                                  "Declined: a refund for this order has already been issued or is in progress.")
            return
        policy = self.task.get("refund_policy", {})
        config = self.simulation.get("refunds", {}).get(order_id, {})
        deadline = config.get("request_deadline", policy.get("request_deadline"))
        returns = {r["item_id"]: r for r in order.get("returns", [])}
        prices = {i["item_id"]: i for i in order["items"]}
        approved, declined = [], []
        for item_id in items:
            ret = returns.get(item_id)
            if item_id in self.refunded_items:
                declined.append((item_id, "already refunded"))
            elif ret is None or ret.get("status") != "received":
                declined.append((item_id, "item not received at our returns center"))
            elif deadline and self.today > deadline:
                declined.append((item_id, "request deadline has passed"))
            else:
                approved.append(item_id)
        approved_cents = sum(prices[i].get("refund_cents", prices[i]["price_cents"]) for i in approved)
        lines = [f"{merchant} - refund request {request_id} for order {order_id}"]
        if approved:
            lines.append(f"Approved: {', '.join(approved)} ({money(approved_cents)}).")
        for item_id, reason in declined:
            lines.append(f"Declined: {item_id} ({reason}).")
        if not approved:
            self._schedule_notice(next_day(self.today), title, "\n".join(lines))
            return
        self.refunded_items.update(approved)

        if not allocations:
            destination = policy.get("default_destination")
            allocations = [{"account": destination, "amount_cents": amount_cents or approved_cents}]
        remaining = approved_cents
        withhold = config.get("withhold")
        for allocation in allocations:
            amount = min(allocation["amount_cents"], remaining)
            remaining -= amount
            if amount <= 0:
                continue
            account = self.accounts[allocation["account"]]
            if withhold and account["kind"] in withhold["account_kinds"]:
                kept = amount * withhold["percent"] // 100
                amount -= kept
                held = self.withheld.setdefault(withhold["case_id"], {"account": account["id"], "amount_cents": 0})
                held["amount_cents"] += kept
            post_on = self._refund_post_date(config, account)
            self._schedule_posting({"date": post_on, "account": account["id"], "amount_cents": amount,
                                    "kind": "refund", "descriptor": merchant.upper(), "reference": order_id})
        if declined:
            self._schedule_notice(next_day(self.today), title, "\n".join(lines))

    def _refund_post_date(self, config, account):
        by_kind = config.get("post_dates_by_account_kind")
        if by_kind:
            return by_kind.get(account["kind"], by_kind["default"])
        return add_business_days(self.today, config.get("post_after_business_days", 3))

    def _case(self, action, args, case_id):
        if not isinstance(case_id, str) or case_id not in self.cases:
            self.reject(action, args, f"Unknown case ID: {case_id}")
        case = self.cases[case_id]
        if case["status"] == "closed":
            self.reject(action, args, "This case is closed")
        return case

    def escalate_case(self, case_id: str, note: str | None = None) -> dict:
        args = {"case_id": case_id, **({"note": note} if note is not None else {})}
        self._case("escalate_case", args, case_id)
        config = self.simulation.get("escalations", {}).get(case_id, {})
        earliest, latest = config.get("earliest"), config.get("latest")
        if earliest and self.today < earliest:
            self.log("agent", "escalate_case", args, "rejected", "premature")
            raise AgentInputError("The merchant cannot accept an escalation for this case yet.")
        reply = config.get("reply", "Your case has been escalated. The merchant will review it.")
        if latest and self.today > latest:
            reply = config.get("late_reply", "Your case has been escalated. The merchant has declined to change the outcome.")
        elif case_id not in self.escalated:
            self.escalated.add(case_id)
            effect = config.get("effect")
            if effect and effect["type"] == "credit":
                self._schedule_posting({k: v for k, v in effect.items() if k != "type"})
            elif effect and effect["type"] == "pay_withheld":
                held = self.withheld.get(case_id)
                if held and held["amount_cents"] > 0:
                    merchant = self.cases[case_id]["merchant"]
                    self._schedule_posting({"date": effect["date"], "account": held["account"],
                                            "amount_cents": held["amount_cents"], "kind": "refund",
                                            "descriptor": merchant.upper(), "reference": case_id})
        return self._accept("escalate_case", args, {"case_id": case_id, "status": "escalated", "reply": reply})

    def close_case(self, case_id: str, resolution: str, matched_transaction_id: str | None = None) -> dict:
        args = {"case_id": case_id, "resolution": resolution}
        if matched_transaction_id is not None:
            args["matched_transaction_id"] = matched_transaction_id
        case = self._case("close_case", args, case_id)
        if resolution not in CASE_RESOLUTIONS:
            self.reject("close_case", args, f"resolution must be one of: {', '.join(CASE_RESOLUTIONS)}")
        case.update(status="closed", closed_on=self.today, resolution=resolution,
                    matched_transaction_id=matched_transaction_id)
        return self._accept("close_case", args, {"case_id": case_id, "status": "closed", "closed_on": self.today})

    def open_dispute(self, transaction_id: str, reason: str) -> dict:
        args = {"transaction_id": transaction_id, "reason": reason}
        visible = {tx["transaction_id"] for tx in self.ledger if tx["date"] <= self.today}
        if transaction_id not in visible:
            self.reject("open_dispute", args, f"Unknown transaction ID: {transaction_id}")
        if reason not in DISPUTE_REASONS:
            self.reject("open_dispute", args, f"reason must be one of: {', '.join(DISPUTE_REASONS)}")
        if any(d["transaction_id"] == transaction_id for d in self.disputes):
            self.reject("open_dispute", args, "A dispute is already open for this transaction")
        dispute_id = self._new_id("generated", "dsp")
        self.disputes.append({"dispute_id": dispute_id, "transaction_id": transaction_id, "reason": reason,
                              "opened_on": self.today})
        for n, credit in enumerate(self.simulation.get("dispute_credits", [])):
            if (n not in self.dispute_credits_used and transaction_id in credit["transaction_ids"]
                    and reason == credit["reason"]):
                self.dispute_credits_used.add(n)
                self._schedule_posting(credit["posting"])
        return self._accept("open_dispute", args, {"dispute_id": dispute_id, "transaction_id": transaction_id,
                                                   "status": "under review"}, dispute_id=dispute_id)

    # ------------------------------------------------------------------
    # Reminders
    # ------------------------------------------------------------------

    def create_reminder(self, reminder_date: str, text: str) -> dict:
        args = {"date": reminder_date, "text": text}
        if not is_iso_date(reminder_date):
            self.reject("create_reminder", args, "date must be a calendar date in YYYY-MM-DD format")
        if reminder_date <= self.today:
            self.reject("create_reminder", args, "A reminder date must be after today")
        if not text.strip():
            self.reject("create_reminder", args, "text must not be empty")
        reminder = {"reminder_id": self._new_id("reminder", "rem"), "date": reminder_date, "text": text,
                    "created_on": self.today}
        self.reminders.append(reminder)
        return self._accept("create_reminder", args, deepcopy(reminder), reminder_id=reminder["reminder_id"])

    # ------------------------------------------------------------------
    # Clock
    # ------------------------------------------------------------------

    def advance_time(self, until: str | None = None) -> dict:
        if until is not None:
            args = {"until": until}
            if not is_iso_date(until):
                self.reject("advance_time", args, "until must be a calendar date in YYYY-MM-DD format")
            if until <= self.today:
                self.reject("advance_time", args, "until must be after today")
            if until > self.horizon:
                self.reject("advance_time", args, "until must be no later than the end of the evaluation period")
        return self._advance(clock="agent", until=until)

    def _advance(self, clock: str, until: str | None = None) -> dict:
        stop = self._next_stop()
        if stop is None:
            if clock == "agent":
                self.reject("advance_time", {}, "The evaluation period has ended; time cannot advance further")
            raise FixtureError("advance past the horizon")
        if until is not None:
            stop = min(stop, until)
        if clock == "agent":
            self.log("agent", "advance_time", {"until": until} if until else {}, "accepted", to_date=stop)
        self.today = stop
        todays = sorted((e for e in self.events if e["date"] == stop and e["id"] not in self.events_processed),
                        key=self._event_key)
        new_documents, report = [], {"new_bills": [], "card_postings": [], "subscription_charges": []}
        for event in todays:
            self._apply_event(event, new_documents, clock, report)
        executed, failed = self._execute_due_payments(clock)
        result = {"date": self.today, "balance_cents": self.balance_cents, "new_documents": new_documents,
                  "executed": executed, "failed": failed}
        result.update({k: v for k, v in report.items() if v})
        return result

    def _apply_event(self, event, new_documents, clock, report=None):
        kind = event["type"]
        if kind in V2_EVENT_TYPES:
            return super()._apply_event(event, new_documents, clock)
        report = report if report is not None else {"new_bills": [], "card_postings": [], "subscription_charges": []}
        details = {k: v for k, v in event.items() if k not in ("id", "date", "type")}
        outcome = "applied"
        if kind == "release_bill":
            self.unreleased_bills.pop(event["bill_id"], None)
            bill = self.records[event["bill_id"]]
            report["new_bills"].append({"bill_id": bill["id"], "payee": bill["payee"],
                                        "amount_cents": bill["amount_cents"], "due_date": bill["due_date"]})
        elif kind == "post_transaction":
            posting = {"date": self.today, **deepcopy(event["posting"])}
            self.ledger.append(posting)
            if self.accounts.get(posting["account"], {}).get("kind") in CHECKING_KINDS:
                self.balance_cents += posting["amount_cents"]
            report["card_postings"].append({k: posting[k] for k in ("transaction_id", "account", "amount_cents",
                                                                      "kind", "descriptor") if k in posting})
        elif kind == "price_change":
            self.subscriptions[event["subscription_id"]]["plans"][event["plan"]]["price_cents"] = event["price_cents"]
        elif kind == "renewal":
            charge = self._renew(event)
            if charge:
                report["subscription_charges"].append(deepcopy(charge))
            else:
                outcome = "skipped_cancelled"
        self.events_processed.add(event["id"])
        self.log("environment", f"event:{kind}", details, outcome, clock=clock, event_id=event["id"])

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        snap = super().snapshot()
        actions = []
        for entry in self.action_log:
            if entry["actor"] != "agent":
                continue
            action = {"date": entry["sim_date"], "tool": entry["action"], "args": deepcopy(entry["args"]),
                      "outcome": entry["outcome"]}
            if entry.get("reason"):
                action["reason"] = entry["reason"]
            actions.append(action)
        snap.update(deepcopy({
            "subscriptions": {sub_id: {"status": s["status"], "plan": s["plan"], "cancelled_on": s["cancelled_on"]}
                              for sub_id, s in self.subscriptions.items()},
            "charges": self.charges,
            "ledger": self.ledger,
            "cases": {case_id: {"status": c["status"], "closed_on": c["closed_on"],
                                "matched_transaction_id": c["matched_transaction_id"], "resolution": c["resolution"]}
                      for case_id, c in self.cases.items()},
            "refund_requests": self.refund_requests,
            "disputes": self.disputes,
            "reminders": self.reminders,
            "actions": actions,
        }))
        return snap
