"""Unit tests for LifeWorkspace mechanics that the task traces do not isolate."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lifeadmin.agent.tools_v3 import TOOLS, execute_tool  # noqa: E402
from lifeadmin.harness import first_message, visible_view  # noqa: E402
from lifeadmin.simulator.workspace_v3 import LifeWorkspace, add_business_days  # noqa: E402

TASKS = ROOT / "benchmark/tasks"


def load(task_id):
    return json.loads((TASKS / f"{task_id}.json").read_text(encoding="utf-8"))


def call(workspace, name, args=None):
    text, is_error = execute_tool(workspace, name, args or {})
    return json.loads(text), is_error


class WorkspaceV3Tests(unittest.TestCase):
    def test_business_days_skip_weekends(self):
        self.assertEqual(add_business_days("2026-10-10", 3), "2026-10-14")  # Saturday -> Wednesday
        self.assertEqual(add_business_days("2026-10-14", 3), "2026-10-19")  # Wednesday -> Monday

    def test_ineligible_refund_is_accepted_then_declined_by_notice(self):
        workspace = LifeWorkspace(load("refund_within_return_window"))
        result, is_error = call(workspace, "request_refund", {"order_id": "BW-55120", "items": ["C-311"]})
        self.assertFalse(is_error)
        self.assertEqual(result["status"], "submitted")
        advanced, _ = call(workspace, "advance_time")
        self.assertEqual(advanced["date"], "2026-10-11")
        notice = workspace.read_document(advanced["new_documents"][0])
        self.assertIn("Declined: C-311", notice)
        workspace.run_to_horizon()
        self.assertFalse([tx for tx in workspace.ledger if tx["kind"] == "refund"])

    def test_refund_to_debit_card_raises_checking_balance(self):
        workspace = LifeWorkspace(load("split_tender_refund"))
        before = workspace.balance_cents
        call(workspace, "request_refund", {"order_id": "SH-61904", "items": ["H-900"],
                                           "allocations": [{"account": "debit-5520", "amount_cents": 18000}]})
        workspace.run_to_horizon()
        self.assertEqual(workspace.balance_cents - before, 16200)  # 10% wrongly withheld, never escalated

    def test_unreleased_bill_is_hidden_until_its_date(self):
        task = load("move_out_closeout")
        self.assertEqual([b["bill_id"] for b in visible_view(task)["bills"]], ["re-oct"])
        workspace = LifeWorkspace(task)
        _, is_error = call(workspace, "pay_bill", {"bill_id": "re-final"})
        self.assertTrue(is_error)
        for _ in range(4):
            advanced, _ = call(workspace, "advance_time")
        self.assertEqual(advanced["new_bills"][0]["bill_id"], "re-final")
        _, is_error = call(workspace, "pay_bill", {"bill_id": "re-final"})
        self.assertFalse(is_error)

    def test_cancellation_notice_period(self):
        workspace = LifeWorkspace(load("free_trial_conversion"))
        call(workspace, "advance_time", {"until": "2026-10-05"})
        call(workspace, "cancel_subscription", {"subscription_id": "snapedit"})
        workspace.run_to_horizon()
        self.assertIn("snapedit", [c["subscription_id"] for c in workspace.charges])

    def test_price_change_on_start_date_applies_before_the_agent_acts(self):
        workspace = LifeWorkspace(load("price_increase_review"))
        subs = {s["subscription_id"]: s for s in workspace.list_subscriptions()}
        self.assertEqual(subs["photovault"]["plans"]["2tb_annual"]["price_cents"], 11900)

    def test_advance_until_is_validated(self):
        workspace = LifeWorkspace(load("missing_refund_escalation"))
        for bad in ("2026-10-10", "2026-10-30", "Oct 16"):
            _, is_error = call(workspace, "advance_time", {"until": bad})
            self.assertTrue(is_error, bad)
        result, _ = call(workspace, "advance_time", {"until": "2026-10-12"})
        self.assertEqual(result["date"], "2026-10-12")

    def test_premature_escalation_is_logged_with_reason(self):
        workspace = LifeWorkspace(load("missing_refund_escalation"))
        _, is_error = call(workspace, "escalate_case", {"case_id": "case-cv-118"})
        self.assertTrue(is_error)
        self.assertEqual(workspace.snapshot()["actions"][-1]["reason"], "premature")

    def test_payment_tasks_keep_their_v2_activity_output(self):
        workspace = LifeWorkspace(load("conflicting_notices"))
        activity = workspace.get_activity()
        self.assertEqual(set(activity), {"date", "balance_cents", "payments", "failed_payments", "pending"})

    def test_one_tool_set_and_no_hidden_data_in_first_message(self):
        names = [t["name"] for t in TOOLS]
        self.assertEqual(len(names), len(set(names)))
        for task_id in ("split_tender_refund", "move_out_closeout", "downgrade_by_usage"):
            message = first_message(visible_view(load(task_id)))
            for secret in ("hidden", "simulation", "expected", "reference_plan"):
                self.assertNotIn(secret, message)
        self.assertIn("soundhub-gift-card", first_message(visible_view(load("split_tender_refund"))))


if __name__ == "__main__":
    unittest.main()
