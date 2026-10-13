"""Core v2 workspace for the payment tasks.

Additive to ``workspace.py`` (v1), which still runs ``avoid_late_fees.json``
unchanged. v2 separates what the bill-pay service shows (``bills``: payable
records with their displayed terms) from what the user actually owes
(``hidden.obligations``, read only by checkers). It records every agent call and
environment execution in an action log, releases documents during a run, seeds
earlier history, and freezes the amount and date of a scheduled payment when it
is accepted.

One environment policy for every v2 task:
- scheduling does not check due dates, so the simulator never reveals one;
- a second payment to the same record goes through (the biller keeps a credit);
- a payment larger than the available balance is refused (no overdraft).

Same-day order: events first (``EVENT_ORDER``, then file order), then
scheduled payments in the order they were accepted.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import date

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

EVENT_ORDER = {
    "deposit": 0,
    "release_document": 1,
    "update_bill": 2,
}

# Who set up a pending payment, as the agent sees it.
SET_UP_BY = {
    "agent": "you",
    "seeded": "you, before this session",
    "biller": "biller",
}


class AgentInputError(ValueError):
    """An agent call the environment refuses. Returned to the agent and scored."""


class FixtureError(ValueError):
    """A malformed task file. A harness problem, never the agent's fault."""


def is_iso_date(value) -> bool:
    if not isinstance(value, str) or not ISO_DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def lint_task(task: dict) -> None:
    """Raise FixtureError for a task file that would silently misbehave."""
    problems = []

    def need(condition, message):
        if not condition:
            problems.append(message)

    required = ("id", "instruction", "start_date", "horizon", "initial_balance_cents",
                "documents", "bills", "events", "hidden")
    for key in required:
        need(key in task, f"missing field: {key}")
    if problems:
        raise FixtureError("; ".join(problems))

    start, horizon = task["start_date"], task["horizon"]
    need(is_iso_date(start) and is_iso_date(horizon) and start < horizon,
         "start_date and horizon must be ISO dates with start_date < horizon")
    balance = task["initial_balance_cents"]
    need(isinstance(balance, int) and not isinstance(balance, bool) and balance >= 0,
         "initial_balance_cents must be a non-negative integer")

    doc_ids = [d.get("id") for d in task["documents"]]
    bill_ids = [b.get("id") for b in task["bills"]]
    event_ids = [e.get("id") for e in task["events"]]
    for kind, ids in (("document", doc_ids), ("bill", bill_ids), ("event", event_ids)):
        need(len(ids) == len(set(ids)), f"duplicate {kind} ids")
    need(not set(doc_ids) & set(bill_ids), "bill ids and document ids must be disjoint")

    for doc in task["documents"]:
        release = doc.get("release_date")
        need(isinstance(doc.get("text"), str) and doc["text"], f"document {doc.get('id')}: text required")
        need(release is None or (is_iso_date(release) and start < release <= horizon),
             f"document {doc.get('id')}: release_date must be null or after start_date and by the horizon")

    for bill in task["bills"]:
        need(isinstance(bill.get("payee"), str) and bill["payee"], f"bill {bill.get('id')}: payee required")
        need(_positive_int(bill.get("amount_cents")), f"bill {bill.get('id')}: amount_cents must be a positive integer")
        need(is_iso_date(bill.get("due_date")), f"bill {bill.get('id')}: due_date must be YYYY-MM-DD")

    for event in task["events"]:
        kind = event.get("type")
        need(kind in EVENT_ORDER, f"event {event.get('id')}: unknown type {kind}")
        need(is_iso_date(event.get("date")) and start < event["date"] <= horizon,
             f"event {event.get('id')}: date must be after start_date and by the horizon")
        if kind == "deposit":
            need(_positive_int(event.get("amount_cents")), f"event {event.get('id')}: deposit amount must be a positive integer")
        if kind == "release_document":
            need(event.get("document_id") in doc_ids, f"event {event.get('id')}: unknown document")
        if kind == "update_bill":
            fields = event.get("fields") or {}
            need(event.get("bill_id") in bill_ids, f"event {event.get('id')}: unknown bill")
            need(fields and set(fields) <= {"amount_cents", "due_date", "document_id"},
                 f"event {event.get('id')}: fields must be amount_cents, due_date and/or document_id")
            need("document_id" not in fields or fields["document_id"] in doc_ids,
                 f"event {event.get('id')}: unknown document")
            need("amount_cents" not in fields or _positive_int(fields["amount_cents"]),
                 f"event {event.get('id')}: amount_cents must be a positive integer")
            need("due_date" not in fields or is_iso_date(fields["due_date"]),
                 f"event {event.get('id')}: due_date must be YYYY-MM-DD")

    history = task.get("initial_history", {})
    for entry in history.get("payments", []) + history.get("failed_payments", []):
        need(entry.get("bill_id") in bill_ids, f"history entry for unknown bill {entry.get('bill_id')}")
        need(is_iso_date(entry.get("date")) and entry["date"] < start,
             "history payments and failures must be dated before start_date")
        need(_positive_int(entry.get("amount_cents")), "history amounts must be positive integers")
    for entry in history.get("pending", []):
        need(entry.get("bill_id") in bill_ids, f"pending entry for unknown bill {entry.get('bill_id')}")
        need(is_iso_date(entry.get("date")) and start < entry["date"] <= horizon,
             "seeded pending payments must be dated after start_date and by the horizon")
        need(_positive_int(entry.get("amount_cents")), "pending amounts must be positive integers")
        need(entry.get("origin", "seeded") in ("seeded", "biller"), "pending origin must be seeded or biller")

    hidden = task["hidden"]
    checker = hidden.get("checker")
    need(isinstance(checker, str) and ":" in checker, "hidden.checker must be 'module:function'")
    for obligation in hidden.get("obligations", []):
        need(set(obligation.get("records", [])) <= set(bill_ids),
             f"obligation {obligation.get('id')}: unknown records")
        for key in ("due_date", "window_start"):
            if obligation.get(key) is not None:
                need(is_iso_date(obligation[key]) and obligation[key] <= horizon,
                     f"obligation {obligation.get('id')}: {key} must be an ISO date by the horizon")

    if problems:
        raise FixtureError("; ".join(problems))


class WorkspaceV2:
    def __init__(self, task: dict):
        lint_task(task)
        self.task = deepcopy(task)
        self.today = task["start_date"]
        self.horizon = task["horizon"]
        self.balance_cents = task["initial_balance_cents"]
        self.min_balance_cents = self.balance_cents
        self.records = {bill["id"]: deepcopy(bill) for bill in task["bills"]}
        self.documents = {doc["id"]: deepcopy(doc) for doc in task["documents"]}
        self.released = [doc["id"] for doc in task["documents"] if doc.get("release_date") is None]
        self.events = self._expand_events(task)
        self.events_processed = set()
        self.payments = []
        self.failed_payments = []
        self.pending = []
        self.cancelled = []
        self.action_log = []
        self._counters = {"payment": 0, "instruction": 0, "seq": 0}
        self._seed_history(task.get("initial_history", {}))

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _expand_events(self, task):
        """Documents with a release_date become release events on that date."""
        events = deepcopy(task["events"])
        explicit = {e["document_id"] for e in events if e["type"] == "release_document"}
        for doc in task["documents"]:
            if doc.get("release_date") and doc["id"] not in explicit:
                events.append({"id": f"release-{doc['id']}", "date": doc["release_date"],
                               "type": "release_document", "document_id": doc["id"]})
        return events

    def _next(self, kind):
        self._counters[kind] += 1
        prefix = {"payment": "pay", "instruction": "sch", "seq": ""}[kind]
        return f"{prefix}-{self._counters[kind]}" if prefix else self._counters[kind]

    def _seed_history(self, history):
        for entry in history.get("payments", []):
            self.payments.append({
                "payment_id": entry.get("payment_id") or self._next("payment"), "bill_id": entry["bill_id"],
                "amount_cents": entry["amount_cents"], "method": "seeded",
                "created_on": entry.get("created_on", entry["date"]), "executed_on": entry["date"],
                "origin": "seeded", "instruction_id": None,
            })
        for entry in history.get("failed_payments", []):
            self.failed_payments.append({
                "bill_id": entry["bill_id"], "amount_cents": entry["amount_cents"],
                "date": entry["date"], "reason": entry.get("reason", "insufficient_funds"),
                "origin": "seeded", "instruction_id": None,
            })
        for entry in history.get("pending", []):
            self._add_instruction(entry["bill_id"], entry["date"], entry["amount_cents"],
                                  created_on=entry.get("created_on", self.today),
                                  origin=entry.get("origin", "seeded"),
                                  instruction_id=entry.get("instruction_id"))

    def _add_instruction(self, bill_id, payment_date, amount_cents, created_on, origin, instruction_id=None):
        instruction = {
            "instruction_id": instruction_id or self._next("instruction"), "bill_id": bill_id, "date": payment_date,
            "amount_cents": amount_cents, "created_on": created_on, "origin": origin,
        }
        self.pending.append(instruction)
        return instruction

    # ------------------------------------------------------------------
    # Action log
    # ------------------------------------------------------------------

    def log(self, actor, action, args, outcome, reason=None, **refs):
        entry = {"seq": self._next("seq"), "sim_date": self.today, "actor": actor,
                 "action": action, "args": deepcopy(args), "outcome": outcome}
        if reason:
            entry["reason"] = reason
        entry.update(refs)
        self.action_log.append(entry)
        return entry

    def reject(self, action, args, reason):
        self.log("agent", action, args, "rejected", reason)
        raise AgentInputError(reason)

    # ------------------------------------------------------------------
    # Agent tools
    # ------------------------------------------------------------------

    def list_documents(self) -> list:
        self.log("agent", "list_documents", {}, "read")
        return [{"document_id": doc_id, "title": self.documents[doc_id].get("title")
                 or self.documents[doc_id]["text"].splitlines()[0][:80]}
                for doc_id in self.released]

    def read_document(self, document_id: str) -> str:
        args = {"document_id": document_id}
        if document_id not in self.released:
            self.reject("read_document", args, f"Unknown document ID: {document_id}")
        self.log("agent", "read_document", args, "read")
        return self.documents[document_id]["text"]

    def check_balance(self) -> dict:
        self.log("agent", "check_balance", {}, "read")
        return {"date": self.today, "balance_cents": self.balance_cents}

    def get_activity(self) -> dict:
        self.log("agent", "get_activity", {}, "read")
        return {
            "date": self.today,
            "balance_cents": self.balance_cents,
            "payments": [{"payment_id": p["payment_id"], "bill_id": p["bill_id"],
                          "amount_cents": p["amount_cents"], "date": p["executed_on"]}
                         for p in self.payments],
            "failed_payments": [{"bill_id": f["bill_id"], "amount_cents": f["amount_cents"],
                                 "date": f["date"], "reason": f["reason"]}
                                for f in self.failed_payments],
            "pending": [{"instruction_id": i["instruction_id"], "bill_id": i["bill_id"],
                         "date": i["date"], "amount_cents": i["amount_cents"],
                         "set_up_by": SET_UP_BY[i["origin"]]}
                        for i in self.pending],
        }

    def _record(self, action, args, bill_id):
        if not isinstance(bill_id, str) or bill_id not in self.records:
            self.reject(action, args, f"Unknown bill ID: {bill_id}")
        return self.records[bill_id]

    def _amount(self, action, args, amount_cents, record):
        if amount_cents is None:
            return record["amount_cents"]
        if not _positive_int(amount_cents):
            self.reject(action, args, "amount_cents must be a positive whole number of cents")
        return amount_cents

    def pay_bill(self, bill_id: str, amount_cents: int | None = None) -> dict:
        args = {"bill_id": bill_id}
        if amount_cents is not None:
            args["amount_cents"] = amount_cents
        record = self._record("pay_bill", args, bill_id)
        amount = self._amount("pay_bill", args, amount_cents, record)
        if self.balance_cents < amount:
            self.reject("pay_bill", args, "Insufficient funds")
        self.balance_cents -= amount
        self.min_balance_cents = min(self.min_balance_cents, self.balance_cents)
        payment = {
            "payment_id": self._next("payment"), "bill_id": bill_id, "amount_cents": amount,
            "method": "immediate", "created_on": self.today, "executed_on": self.today,
            "origin": "agent", "instruction_id": None,
        }
        self.payments.append(payment)
        self.log("agent", "pay_bill", args, "accepted", payment_id=payment["payment_id"])
        return {"payment_id": payment["payment_id"], "bill_id": bill_id, "amount_cents": amount,
                "date": self.today, "balance_cents": self.balance_cents}

    def schedule_payment(self, bill_id: str, payment_date: str, amount_cents: int | None = None) -> dict:
        args = {"bill_id": bill_id, "payment_date": payment_date}
        if amount_cents is not None:
            args["amount_cents"] = amount_cents
        record = self._record("schedule_payment", args, bill_id)
        if not is_iso_date(payment_date):
            self.reject("schedule_payment", args, "payment_date must be a calendar date in YYYY-MM-DD format")
        if payment_date <= self.today:
            self.reject("schedule_payment", args, "A scheduled payment date must be after today")
        if payment_date > self.horizon:
            self.reject("schedule_payment", args, "A scheduled payment date must be no later than the end of the evaluation period")
        amount = self._amount("schedule_payment", args, amount_cents, record)
        instruction = self._add_instruction(bill_id, payment_date, amount, created_on=self.today, origin="agent")
        self.log("agent", "schedule_payment", args, "accepted", instruction_id=instruction["instruction_id"])
        return {"instruction_id": instruction["instruction_id"], "bill_id": bill_id,
                "date": payment_date, "amount_cents": amount}

    def cancel_scheduled_payment(self, instruction_id: str | None = None, bill_id: str | None = None) -> dict:
        args = {key: value for key, value in (("instruction_id", instruction_id), ("bill_id", bill_id)) if value is not None}
        if instruction_id is None and bill_id is None:
            self.reject("cancel_scheduled_payment", args, "Give instruction_id or bill_id")
        if instruction_id is not None:
            matches = [i for i in self.pending if i["instruction_id"] == instruction_id]
        else:
            matches = [i for i in self.pending if i["bill_id"] == bill_id]
        if not matches:
            self.reject("cancel_scheduled_payment", args, "No pending scheduled payment matches")
        mine = [i for i in matches if i["origin"] != "biller"]
        if not mine:
            self.reject("cancel_scheduled_payment", args, "This payment was set up by the biller and cannot be cancelled here")
        for instruction in mine:
            self.pending.remove(instruction)
            self.cancelled.append({**instruction, "cancelled_on": self.today})
        ids = [i["instruction_id"] for i in mine]
        self.log("agent", "cancel_scheduled_payment", args, "accepted", instruction_ids=ids)
        return {"cancelled": [{"instruction_id": i["instruction_id"], "bill_id": i["bill_id"], "date": i["date"],
                               "amount_cents": i["amount_cents"]} for i in mine]}

    def advance_time(self) -> dict:
        return self._advance(clock="agent")

    # ------------------------------------------------------------------
    # Clock and environment execution
    # ------------------------------------------------------------------

    def _next_stop(self):
        stops = [e["date"] for e in self.events if e["id"] not in self.events_processed and e["date"] > self.today]
        stops += [i["date"] for i in self.pending if self.today < i["date"] <= self.horizon]
        if self.today < self.horizon:
            stops.append(self.horizon)
        return min(stops) if stops else None

    def _advance(self, clock: str) -> dict:
        stop = self._next_stop()
        if stop is None:
            if clock == "agent":
                self.reject("advance_time", {}, "The evaluation period has ended; time cannot advance further")
            raise FixtureError("advance past the horizon")
        if clock == "agent":
            self.log("agent", "advance_time", {}, "accepted", to_date=stop)
        self.today = stop
        order = {id(e): n for n, e in enumerate(self.events)}
        todays = sorted((e for e in self.events if e["date"] == stop and e["id"] not in self.events_processed),
                        key=lambda e: (EVENT_ORDER[e["type"]], order[id(e)]))
        new_documents = []
        for event in todays:
            self._apply_event(event, new_documents, clock)
        executed, failed = self._execute_due_payments(clock)
        return {"date": self.today, "balance_cents": self.balance_cents, "new_documents": new_documents,
                "executed": executed, "failed": failed}

    def _apply_event(self, event, new_documents, clock):
        kind = event["type"]
        if kind == "deposit":
            self.balance_cents += event["amount_cents"]
        elif kind == "release_document":
            if event["document_id"] not in self.released:
                self.released.append(event["document_id"])
                new_documents.append(event["document_id"])
        elif kind == "update_bill":
            self.records[event["bill_id"]].update(deepcopy(event["fields"]))
        self.events_processed.add(event["id"])
        self.log("environment", f"event:{kind}", {k: v for k, v in event.items() if k not in ("id", "date", "type")},
                 "applied", clock=clock, event_id=event["id"])

    def _execute_due_payments(self, clock):
        executed, failed = [], []
        for instruction in [i for i in self.pending if i["date"] == self.today]:
            self.pending.remove(instruction)
            amount = instruction["amount_cents"]
            if self.balance_cents < amount:
                failure = {"bill_id": instruction["bill_id"], "amount_cents": amount, "date": self.today,
                           "reason": "insufficient_funds", "origin": instruction["origin"],
                           "instruction_id": instruction["instruction_id"]}
                self.failed_payments.append(failure)
                failed.append({k: failure[k] for k in ("bill_id", "amount_cents", "reason")})
                self.log("environment", "execute_scheduled_payment", {"instruction_id": instruction["instruction_id"]},
                         "failed", reason="insufficient_funds", clock=clock, origin=instruction["origin"])
                continue
            self.balance_cents -= amount
            self.min_balance_cents = min(self.min_balance_cents, self.balance_cents)
            payment = {
                "payment_id": self._next("payment"), "bill_id": instruction["bill_id"], "amount_cents": amount,
                "method": "scheduled", "created_on": instruction["created_on"], "executed_on": self.today,
                "origin": instruction["origin"], "instruction_id": instruction["instruction_id"],
            }
            self.payments.append(payment)
            executed.append({"payment_id": payment["payment_id"], "bill_id": payment["bill_id"], "amount_cents": amount})
            self.log("environment", "execute_scheduled_payment", {"instruction_id": instruction["instruction_id"]},
                     "executed", clock=clock, origin=instruction["origin"], payment_id=payment["payment_id"])
        return executed, failed

    def run_to_horizon(self) -> None:
        """Evaluator rollout: process remaining events and schedules, no agent actions."""
        while self.today < self.horizon:
            self._advance(clock="evaluator")

    def snapshot(self) -> dict:
        return deepcopy({
            "date": self.today,
            "balance_cents": self.balance_cents,
            "min_balance_cents": self.min_balance_cents,
            "payments": self.payments,
            "failed_payments": self.failed_payments,
            "pending": self.pending,
            "cancelled": self.cancelled,
            "records": self.records,
            "released_documents": self.released,
            "events_processed": sorted(self.events_processed),
            "action_log": self.action_log,
        })
