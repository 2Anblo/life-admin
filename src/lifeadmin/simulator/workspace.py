"""Deterministic workspace simulating personal bill management.

Bill lifecycle: ``unpaid`` -> ``scheduled`` -> ``paid``, or back to ``unpaid``
when a scheduled payment fails (for example, insufficient funds). Failures are
recorded in ``failed_payments`` instead of being silently dropped.

Same-day ordering is fixed: external events first (ordered by ``EVENT_ORDER``),
then scheduled payments. This ordering is part of the task contract and is
asserted by the tests.
"""

from __future__ import annotations

from copy import deepcopy

# Same-day event ordering: lower numbers run first. New event types must be
# registered here and given an ``_apply_<type>`` handler on Workspace.
EVENT_ORDER = {
    "deposit": 0,
}


class Workspace:
    def __init__(self, task: dict):
        self.task = deepcopy(task)
        self.today = task["start_date"]
        self.balance_cents = task["initial_balance_cents"]
        self.bills = {bill["id"]: deepcopy(bill) for bill in task["bills"]}
        self.documents = {doc["id"]: doc["text"] for doc in task["documents"]}
        # Bill state machine: unpaid | scheduled | paid.
        self.bill_status = {bill["id"]: "unpaid" for bill in task["bills"]}
        self.pending = {}
        self.payments = []
        # Scheduled payments that failed at execution time, newest last.
        self.failed_payments = []
        self.events_processed = set()

    def read_document(self, document_id: str) -> str:
        return self.documents[document_id]

    def check_balance(self) -> int:
        return self.balance_cents

    def schedule_payment(self, bill_id: str, payment_date: str) -> None:
        bill = self.bills[bill_id]
        if self.bill_status[bill_id] != "unpaid":
            raise ValueError("Bill already scheduled or paid")
        if not self.today < payment_date <= bill["due_date"]:
            raise ValueError("Scheduled payment date must be after today and by the due date")
        if payment_date > self.task["horizon"]:
            raise ValueError("Payment date exceeds task horizon")
        self.pending[bill_id] = payment_date
        self.bill_status[bill_id] = "scheduled"

    def pay_bill(self, bill_id: str) -> None:
        bill = self.bills[bill_id]
        if self.bill_status[bill_id] == "paid":
            raise ValueError("Bill already paid")
        if self.balance_cents < bill["amount_cents"]:
            raise ValueError("Insufficient funds")
        self.balance_cents -= bill["amount_cents"]
        self.payments.append({"bill_id": bill_id, "date": self.today, "amount_cents": bill["amount_cents"]})
        self.bill_status[bill_id] = "paid"
        self.pending.pop(bill_id, None)

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def _apply_deposit(self, event: dict) -> None:
        self.balance_cents += event["amount_cents"]

    def _apply_event(self, event: dict) -> None:
        """Dispatch one external event to its ``_apply_<type>`` handler."""
        handler = getattr(self, f"_apply_{event['type']}", None)
        if handler is None:
            raise ValueError(f"Unsupported event type: {event['type']}")
        handler(event)
        self.events_processed.add(event["id"])

    def _execute_payment(self, bill_id: str) -> bool:
        """Settle one scheduled payment, recording an explicit failure result."""
        bill = self.bills[bill_id]
        if self.balance_cents < bill["amount_cents"]:
            self.failed_payments.append({
                "bill_id": bill_id,
                "date": self.today,
                "reason": "insufficient_funds",
            })
            self.bill_status[bill_id] = "unpaid"
            return False
        self.balance_cents -= bill["amount_cents"]
        self.payments.append({"bill_id": bill_id, "date": self.today, "amount_cents": bill["amount_cents"]})
        self.bill_status[bill_id] = "paid"
        return True

    def _settle_scheduled_payments(self) -> None:
        """Execute every scheduled payment due today, in scheduling order."""
        for bill_id, payment_date in list(self.pending.items()):
            if payment_date == self.today:
                self._execute_payment(bill_id)
                self.pending.pop(bill_id)

    def advance_time(self) -> str:
        future = [event["date"] for event in self.task["events"] if event["id"] not in self.events_processed and event["date"] > self.today]
        future += [payment_date for payment_date in self.pending.values() if payment_date > self.today]
        if self.today < self.task["horizon"]:
            future.append(self.task["horizon"])
        if not future:
            raise ValueError("Evaluation horizon reached")
        self.today = min(future)

        # Fixed same-day order: external events sorted by EVENT_ORDER, then
        # scheduled payments (which recheck funds and bill status).
        todays_events = [
            event for event in self.task["events"]
            if event["date"] == self.today and event["id"] not in self.events_processed
        ]
        todays_events.sort(key=lambda event: EVENT_ORDER.get(event["type"], len(EVENT_ORDER)))
        for event in todays_events:
            self._apply_event(event)
        self._settle_scheduled_payments()
        return self.today

    def run_to_horizon(self) -> None:
        while self.today < self.task["horizon"]:
            self.advance_time()

    def result(self) -> dict:
        return {
            "date": self.today,
            "balance_cents": self.balance_cents,
            "payments": deepcopy(self.payments),
            "failed_payments": deepcopy(self.failed_payments),
            "bill_status": dict(self.bill_status),
        }
