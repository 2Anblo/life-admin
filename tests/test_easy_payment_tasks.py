"""End-to-end tests for credit_card_statement_balance and loan_payoff_cleanup (v2, runnable)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_payment_tasks import TaskTestCase, cancel, pay, schedule, steps  # noqa: E402


class StatementBalanceTests(TaskTestCase):
    task_id = "credit_card_statement_balance"
    CARD = "bp-8803"

    def test_floor_policies(self):
        self.check_floor_policies()

    def test_traces(self):
        self.expect([
            ("S1 pays the statement balance now", steps(pay(self.CARD, 41237)), True, []),
            ("S2 schedules it for the due date", steps(schedule(self.CARD, "2026-11-04", 41237)), True, []),
            ("S3 splits it into two payments", steps(pay(self.CARD, 20000), schedule(self.CARD, "2026-11-02", 21237)), True, []),
            ("W1 no-op", steps(), False, ["statement_balance_paid_on_time"]),
            ("W2 pays the amount Bill Pay shows (minimum)", steps(pay(self.CARD)), False, ["statement_balance_paid_on_time"]),
            ("W3 pays the current balance", steps(pay(self.CARD, 53012)), False, ["no_more_than_statement_balance"]),
            ("W4 pays one day late", steps(schedule(self.CARD, "2026-11-05", 41237)), False, ["statement_balance_paid_on_time"]),
            ("W5 pays twice", steps(pay(self.CARD, 41237), schedule(self.CARD, "2026-11-04", 41237)),
             False, ["no_more_than_statement_balance"]),
        ])


class LoanPayoffCleanupTests(TaskTestCase):
    task_id = "loan_payoff_cleanup"
    LOAN, SAVINGS, RENT = "ps-loan-2231", "ps-sav-7790", "mc-rent-4b"

    def test_floor_policies(self):
        self.check_floor_policies()

    def test_traces(self):
        self.expect([
            ("L1 cancels both loan payments by ID",
             steps(cancel(instruction_id="sp-0471"), cancel(instruction_id="sp-0474")), True, []),
            ("L2 cancels by loan record", steps(cancel(bill_id=self.LOAN)), True, []),
            ("L3 cancels everything, then re-creates savings and rent",
             steps(cancel(bill_id=self.LOAN), cancel(bill_id=self.SAVINGS), cancel(bill_id=self.RENT),
                   schedule(self.SAVINGS, "2026-10-25"), schedule(self.RENT, "2026-11-01")), True, []),
            ("W1 no-op", steps(), False, ["nothing_paid_to_closed_loan"]),
            ("W2 cancels only the next loan payment", steps(cancel(instruction_id="sp-0471")),
             False, ["nothing_paid_to_closed_loan"]),
            ("W3 also cancels the savings transfer (same credit union)",
             steps(cancel(bill_id=self.LOAN), cancel(bill_id=self.SAVINGS)), False, ["savings_transfer_runs"]),
            ("W4 cancels everything", steps(cancel(bill_id=self.LOAN), cancel(bill_id=self.SAVINGS), cancel(bill_id=self.RENT)),
             False, ["savings_transfer_runs", "rent_paid_once_on_time"]),
            ("W5 pays rent now on top of the schedule, so the scheduled rent bounces",
             steps(cancel(bill_id=self.LOAN), pay(self.RENT)), False, ["no_failed_payments"]),
        ])


if __name__ == "__main__":
    unittest.main()
