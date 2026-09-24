"""Small deterministic workspace for the first bill-payment task."""

from __future__ import annotations

from copy import deepcopy
class Workspace:
    def __init__(self, task: dict):
        self.task = deepcopy(task)
        self.today = task["start_date"]
        self.balance_cents = task["initial_balance_cents"]
        self.bills = {bill["id"]: deepcopy(bill) for bill in task["bills"]}
        self.documents = {doc["id"]: doc["text"] for doc in task["documents"]}
        self.pending = {}
        self.payments = []
        self.events_processed = set()
        self.overdraft_count = 0

    def read_document(self, document_id: str) -> str:
        return self.documents[document_id]

    def check_balance(self) -> int:
        return self.balance_cents

    def schedule_payment(self, bill_id: str, payment_date: str) -> None:
        bill = self.bills[bill_id]
        if bill_id in self.pending or any(p["bill_id"] == bill_id for p in self.payments):
            raise ValueError("Bill already scheduled or paid")
        if not self.today < payment_date <= bill["due_date"]:
            raise ValueError("Scheduled payment date must be after today and by the due date")
        if payment_date > self.task["horizon"]:
            raise ValueError("Payment date exceeds task horizon")
        self.pending[bill_id] = payment_date

    def pay_bill(self, bill_id: str) -> None:
        bill = self.bills[bill_id]
        if any(p["bill_id"] == bill_id for p in self.payments):
            raise ValueError("Bill already paid")
        if self.balance_cents < bill["amount_cents"]:
            raise ValueError("Insufficient funds")
        self.balance_cents -= bill["amount_cents"]
        self.payments.append({"bill_id": bill_id, "date": self.today, "amount_cents": bill["amount_cents"]})
        self.pending.pop(bill_id, None)

    def advance_time(self) -> str:
        future = [event["date"] for event in self.task["events"] if event["id"] not in self.events_processed and event["date"] > self.today]
        future += [payment_date for payment_date in self.pending.values() if payment_date > self.today]
        if self.today < self.task["horizon"]:
            future.append(self.task["horizon"])
        if not future:
            raise ValueError("Evaluation horizon reached")
        self.today = min(future)

        # Fixed order for simultaneous events: deposits, then scheduled payments.
        for event in self.task["events"]:
            if event["date"] == self.today and event["id"] not in self.events_processed:
                if event["type"] != "deposit":
                    raise ValueError(f"Unsupported event type: {event['type']}")
                self.balance_cents += event["amount_cents"]
                self.events_processed.add(event["id"])
        for bill_id, payment_date in list(self.pending.items()):
            if payment_date == self.today:
                try:
                    self.pay_bill(bill_id)
                except ValueError:
                    self.pending.pop(bill_id)
        return self.today

    def run_to_horizon(self) -> None:
        while self.today < self.task["horizon"]:
            self.advance_time()

    def result(self) -> dict:
        return {
            "date": self.today,
            "balance_cents": self.balance_cents,
            "payments": deepcopy(self.payments),
            "overdraft_count": self.overdraft_count,
        }
