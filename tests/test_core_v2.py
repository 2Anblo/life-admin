"""Tests for the Core v2 workspace, tools and harness (inline fixtures only)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from benchmark.checkers.common import predicate, verdict
from lifeadmin.agent.tools_v2 import execute_tool_v2
from lifeadmin.harness import first_message, run_trial, visible_view
from lifeadmin.simulator.workspace_v2 import FixtureError, WorkspaceV2


def make_task(**overrides):
    task = {
        "id": "core-v2-inline",
        "instruction": "Pay my bills on time.",
        "start_date": "2026-10-01",
        "horizon": "2026-10-10",
        "initial_balance_cents": 10000,
        "documents": [
            {"id": "doc-a", "text": "Bill A: $50.00 due October 5, 2026.", "release_date": None},
            {"id": "doc-b", "text": "Revised bill A: due October 4.", "release_date": "2026-10-03"},
        ],
        "bills": [
            {"id": "r-a", "payee": "Utility A", "amount_cents": 5000, "due_date": "2026-10-05", "document_id": "doc-a"},
        ],
        "events": [{"id": "dep", "date": "2026-10-03", "type": "deposit", "amount_cents": 2000}],
        "hidden": {"checker": "tests.test_core_v2:always_pass"},
    }
    task.update(overrides)
    return task


def always_pass(task, s_stop, s_h):
    return verdict({"ran": predicate(True)})


def call(workspace, name, **arguments):
    text, is_error = execute_tool_v2(workspace, name, arguments)
    return json.loads(text), is_error


class LintTests(unittest.TestCase):
    def test_rejects_silent_misbehaviour(self):
        bad = [
            {"events": [{"id": "e", "date": "2026-10-01", "type": "deposit", "amount_cents": 1}]},  # on start
            {"events": [{"id": "e", "date": "2026-10-11", "type": "deposit", "amount_cents": 1}]},  # after horizon
            {"events": [{"id": "e", "date": "2026-10-03", "type": "deposit", "amount_cents": -5}]},
            {"events": [{"id": "e", "date": "2026-10-03", "type": "notice"}]},
            {"bills": [{"id": "doc-a", "payee": "X", "amount_cents": 1, "due_date": "2026-10-05"}]},
            {"bills": [{"id": "r", "payee": "X", "amount_cents": 1, "due_date": "2026-10-5"}]},
        ]
        for override in bad:
            with self.subTest(override=override), self.assertRaises(FixtureError):
                WorkspaceV2(make_task(**override))


class PolicyTests(unittest.TestCase):
    def test_no_due_date_check_and_date_validation(self):
        workspace = WorkspaceV2(make_task())
        _, late_ok = call(workspace, "schedule_payment", bill_id="r-a", payment_date="2026-10-09")
        self.assertFalse(late_ok)
        for bad_date in ("2026-10-045", "2026-10-02T00:00", "2026-10-01", "2026-10-11"):
            result, is_error = call(workspace, "schedule_payment", bill_id="r-a", payment_date=bad_date)
            self.assertTrue(is_error, bad_date)

    def test_wrong_argument_type_is_a_scored_rejection(self):
        workspace = WorkspaceV2(make_task())
        result, is_error = call(workspace, "schedule_payment", bill_id="r-a", payment_date=20261005)
        self.assertTrue(is_error)
        self.assertEqual(workspace.action_log[-1]["outcome"], "rejected")

    def test_second_payment_goes_through(self):
        workspace = WorkspaceV2(make_task())
        call(workspace, "pay_bill", bill_id="r-a")
        _, is_error = call(workspace, "pay_bill", bill_id="r-a")
        self.assertFalse(is_error)
        self.assertEqual(len(workspace.payments), 2)
        self.assertEqual(workspace.balance_cents, 0)

    def test_overdraft_refused(self):
        workspace = WorkspaceV2(make_task(initial_balance_cents=1000))
        result, is_error = call(workspace, "pay_bill", bill_id="r-a")
        self.assertTrue(is_error)
        self.assertEqual(result["error"], "Insufficient funds")

    def test_paying_now_does_not_cancel_a_schedule(self):
        workspace = WorkspaceV2(make_task())
        call(workspace, "schedule_payment", bill_id="r-a", payment_date="2026-10-03")
        call(workspace, "pay_bill", bill_id="r-a")
        workspace.run_to_horizon()
        self.assertEqual([p["method"] for p in workspace.payments], ["immediate", "scheduled"])


class TimeAndDocumentTests(unittest.TestCase):
    def test_release_is_a_clock_stop_and_unreleased_looks_unknown(self):
        workspace = WorkspaceV2(make_task())
        hidden, is_error = call(workspace, "read_document", document_id="doc-b")
        missing, _ = call(workspace, "read_document", document_id="doc-zzz")
        self.assertTrue(is_error)
        self.assertEqual(hidden["error"].replace("doc-b", "X"), missing["error"].replace("doc-zzz", "X"))
        advanced, _ = call(workspace, "advance_time")
        self.assertEqual((advanced["date"], advanced["new_documents"]), ("2026-10-03", ["doc-b"]))
        self.assertFalse(call(workspace, "read_document", document_id="doc-b")[1])

    def test_deposit_applies_before_same_day_payment(self):
        workspace = WorkspaceV2(make_task(initial_balance_cents=4000))
        call(workspace, "schedule_payment", bill_id="r-a", payment_date="2026-10-03")
        advanced, _ = call(workspace, "advance_time")
        self.assertEqual(advanced["executed"][0]["bill_id"], "r-a")

    def test_update_never_rewrites_an_accepted_schedule(self):
        events = [{"id": "u", "date": "2026-10-02", "type": "update_bill", "bill_id": "r-a",
                   "fields": {"amount_cents": 7000}}]
        workspace = WorkspaceV2(make_task(events=events))
        call(workspace, "schedule_payment", bill_id="r-a", payment_date="2026-10-04")
        workspace.run_to_horizon()
        self.assertEqual(workspace.payments[0]["amount_cents"], 5000)
        self.assertEqual(workspace.records["r-a"]["amount_cents"], 7000)

    def test_advance_at_horizon_is_returned_not_raised(self):
        workspace = WorkspaceV2(make_task())
        workspace.run_to_horizon()
        result, is_error = call(workspace, "advance_time")
        self.assertTrue(is_error)
        self.assertIn("ended", result["error"])


class HistoryAndCancelTests(unittest.TestCase):
    def test_seeded_history_is_visible_and_biller_schedule_cannot_be_cancelled(self):
        history = {
            "payments": [{"bill_id": "r-a", "amount_cents": 5000, "date": "2026-09-28"}],
            "failed_payments": [{"bill_id": "r-a", "amount_cents": 5000, "date": "2026-09-30"}],
            "pending": [{"bill_id": "r-a", "amount_cents": 5000, "date": "2026-10-04", "origin": "biller"}],
        }
        workspace = WorkspaceV2(make_task(initial_history=history))
        activity, _ = call(workspace, "get_activity")
        self.assertEqual(activity["pending"][0]["set_up_by"], "biller")
        self.assertEqual(len(activity["failed_payments"]), 1)
        _, is_error = call(workspace, "cancel_scheduled_payment", bill_id="r-a")
        self.assertTrue(is_error)

    def test_cancel_own_schedule(self):
        workspace = WorkspaceV2(make_task())
        scheduled, _ = call(workspace, "schedule_payment", bill_id="r-a", payment_date="2026-10-04")
        _, is_error = call(workspace, "cancel_scheduled_payment", instruction_id=scheduled["instruction_id"])
        self.assertFalse(is_error)
        workspace.run_to_horizon()
        self.assertEqual(workspace.payments, [])


class HarnessTests(unittest.TestCase):
    def test_trial_snapshots_replay_and_visible_view(self):
        task = make_task()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task.json"
            path.write_text(json.dumps(task))

            def agent(view, call_tool):
                call_tool("schedule_payment", {"bill_id": "r-a", "payment_date": "2026-10-04"})
                return "end_turn"

            record = run_trial(path, agent)
        self.assertTrue(record["replay_ok"])
        self.assertEqual(len(record["s_stop"]["pending"]), 1)
        self.assertEqual(record["s_h"]["payments"][0]["executed_on"], "2026-10-04")
        self.assertEqual(record["s_h"]["action_log"][-1]["clock"], "evaluator")
        view = visible_view(task)
        self.assertNotIn("hidden", view)
        self.assertNotIn("doc-b", [d["document_id"] for d in view["documents"]])
        self.assertNotIn("hidden", first_message(view))


if __name__ == "__main__":
    unittest.main()
