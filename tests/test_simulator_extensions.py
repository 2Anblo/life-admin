"""Tests for bill status, explicit payment failures, and event ordering."""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from benchmark.checkers.bill_payment import check_success
from lifeadmin.simulator.workspace import Workspace


def make_task(
    initial_balance_cents=10000,
    events=None,
    bills=None,
):
    """Build a minimal inline task fixture; formal fixtures stay untouched."""
    return {
        "id": "inline_test",
        "title": "Inline simulator test",
        "start_date": "2026-09-24",
        "horizon": "2026-09-28",
        "initial_balance_cents": initial_balance_cents,
        "documents": [
            {"id": "bill-a", "text": "Bill A: $50.00 due September 26, 2026."},
            {"id": "bill-b", "text": "Bill B: $40.00 due September 27, 2026."},
            {"id": "notice-1", "text": "Notice."},
        ],
        "bills": bills or [
            {"id": "a", "amount_cents": 5000, "due_date": "2026-09-26", "document_id": "bill-a"},
            {"id": "b", "amount_cents": 4000, "due_date": "2026-09-27", "document_id": "bill-b"},
        ],
        "events": events or [],
        "goal": "All bills settled on time without failures.",
    }


class BillStatusTests(unittest.TestCase):
    def test_full_lifecycle_unpaid_scheduled_paid(self):
        workspace = Workspace(make_task())
        self.assertEqual(workspace.bill_status, {"a": "unpaid", "b": "unpaid"})
        workspace.schedule_payment("a", "2026-09-26")
        self.assertEqual(workspace.bill_status["a"], "scheduled")
        workspace.advance_time()  # 2026-09-26: payment settles
        self.assertEqual(workspace.bill_status["a"], "paid")

    def test_immediate_payment_marks_paid(self):
        workspace = Workspace(make_task())
        workspace.pay_bill("a")
        self.assertEqual(workspace.bill_status["a"], "paid")
        self.assertEqual(workspace.payments[0]["bill_id"], "a")

    def test_schedule_after_paid_rejected(self):
        workspace = Workspace(make_task())
        workspace.pay_bill("a")
        with self.assertRaises(ValueError):
            workspace.schedule_payment("a", "2026-09-27")

    def test_double_schedule_rejected(self):
        workspace = Workspace(make_task())
        workspace.schedule_payment("a", "2026-09-26")
        with self.assertRaises(ValueError):
            workspace.schedule_payment("a", "2026-09-27")


class ExplicitFailureTests(unittest.TestCase):
    def test_insufficient_funds_recorded_and_bill_returns_to_unpaid(self):
        task = make_task(initial_balance_cents=3000)
        workspace = Workspace(task)
        workspace.schedule_payment("a", "2026-09-26")
        workspace.advance_time()

        self.assertEqual(workspace.payments, [])
        self.assertEqual(
            workspace.failed_payments,
            [{"bill_id": "a", "date": "2026-09-26", "reason": "insufficient_funds"}],
        )
        self.assertEqual(workspace.bill_status["a"], "unpaid")
        self.assertEqual(workspace.pending, {})

    def test_failed_payment_can_be_retried_after_deposit(self):
        events = [{"id": "deposit-1", "date": "2026-09-26", "type": "deposit", "amount_cents": 9000}]
        workspace = Workspace(make_task(initial_balance_cents=3000, events=events))
        workspace.schedule_payment("a", "2026-09-25")  # fails before the deposit arrives
        workspace.advance_time()
        self.assertTrue(workspace.failed_payments)

        workspace.schedule_payment("a", "2026-09-26")  # retry on the deposit date
        workspace.advance_time()
        self.assertEqual(workspace.bill_status["a"], "paid")
        self.assertEqual(len(workspace.payments), 1)

    def test_checker_fails_on_recorded_failure_even_if_paid_later(self):
        # A recorded failure means an obligation was missed once; the checker
        # treats that as task failure even if a retry eventually succeeds.
        events = [{"id": "deposit-1", "date": "2026-09-26", "type": "deposit", "amount_cents": 9000}]
        task = make_task(initial_balance_cents=3000, events=events)
        workspace = Workspace(task)
        workspace.schedule_payment("a", "2026-09-26")
        workspace.run_to_horizon()
        workspace.pay_bill("b")

        self.assertFalse(check_success(task, workspace.result()))

    def test_checker_fails_when_bill_unpaid_at_horizon(self):
        task = make_task(initial_balance_cents=3000)
        workspace = Workspace(task)
        workspace.schedule_payment("a", "2026-09-26")
        workspace.run_to_horizon()

        result = workspace.result()
        self.assertEqual(result["bill_status"]["a"], "unpaid")
        self.assertTrue(result["failed_payments"])
        self.assertFalse(check_success(task, result))

    def test_result_exposes_new_fields(self):
        workspace = Workspace(make_task())
        result = workspace.result()
        self.assertIn("failed_payments", result)
        self.assertIn("bill_status", result)
        self.assertNotIn("overdraft_count", result)


class EventOrderingTests(unittest.TestCase):
    def test_deposits_apply_before_same_day_scheduled_payments(self):
        events = [{"id": "deposit-1", "date": "2026-09-26", "type": "deposit", "amount_cents": 5000}]
        workspace = Workspace(make_task(initial_balance_cents=1000, events=events))
        workspace.schedule_payment("a", "2026-09-26")  # needs the deposit to succeed
        workspace.advance_time()

        self.assertEqual(workspace.bill_status["a"], "paid")
        self.assertEqual(workspace.failed_payments, [])

    def test_multiple_same_day_deposits_all_apply(self):
        events = [
            {"id": "deposit-1", "date": "2026-09-25", "type": "deposit", "amount_cents": 1000},
            {"id": "deposit-2", "date": "2026-09-25", "type": "deposit", "amount_cents": 2000},
        ]
        workspace = Workspace(make_task(events=events))
        workspace.advance_time()
        self.assertEqual(workspace.check_balance(), 13000)

    def test_events_processed_exactly_once(self):
        events = [{"id": "deposit-1", "date": "2026-09-25", "type": "deposit", "amount_cents": 1000}]
        workspace = Workspace(make_task(events=events))
        workspace.run_to_horizon()
        self.assertEqual(workspace.events_processed, {"deposit-1"})
        self.assertEqual(workspace.check_balance(), 11000)

    def test_unsupported_event_type_raises(self):
        events = [{"id": "notice-1", "date": "2026-09-25", "type": "notice"}]
        workspace = Workspace(make_task(events=events))
        with self.assertRaises(ValueError):
            workspace.advance_time()


if __name__ == "__main__":
    unittest.main()
