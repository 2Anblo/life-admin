import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from benchmark.checkers.bill_payment import check_success
from lifeadmin.simulator.workspace import Workspace


class AvoidLateFeesTests(unittest.TestCase):
    def setUp(self):
        self.task = json.loads((ROOT / "benchmark/tasks/avoid_late_fees.json").read_text())
        self.workspace = Workspace(self.task)

    def test_successful_schedule(self):
        self.workspace.schedule_payment("electric", "2026-09-25")
        self.workspace.schedule_payment("internet", "2026-09-27")
        self.workspace.schedule_payment("water", "2026-09-28")
        self.workspace.run_to_horizon()
        self.assertTrue(check_success(self.task, self.workspace.result()))
        self.assertEqual(self.workspace.check_balance(), 3000)

    def test_unpaid_bill_fails(self):
        self.workspace.schedule_payment("electric", "2026-09-25")
        self.workspace.run_to_horizon()
        self.assertFalse(check_success(self.task, self.workspace.result()))

    def test_insufficient_funds_does_not_pay(self):
        self.workspace.schedule_payment("internet", "2026-09-25")
        self.workspace.schedule_payment("electric", "2026-09-25")
        self.workspace.advance_time()
        self.assertEqual([p["bill_id"] for p in self.workspace.payments], ["internet"])
        self.assertEqual(self.workspace.check_balance(), 4000)

    def test_duplicate_payment_rejected(self):
        self.workspace.pay_bill("electric")
        with self.assertRaises(ValueError):
            self.workspace.pay_bill("electric")

    def test_today_is_not_a_future_schedule_date(self):
        with self.assertRaises(ValueError):
            self.workspace.schedule_payment("electric", self.workspace.today)

    def test_deposit_precedes_payment_on_same_day(self):
        self.workspace.schedule_payment("electric", "2026-09-25")
        self.workspace.schedule_payment("internet", "2026-09-26")
        self.workspace.advance_time()
        self.workspace.advance_time()
        self.assertEqual([p["bill_id"] for p in self.workspace.payments], ["electric", "internet"])
        self.assertEqual(self.workspace.check_balance(), 8000)


if __name__ == "__main__":
    unittest.main()
