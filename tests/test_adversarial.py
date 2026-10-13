"""Adversarial regression tests: malformed inputs, replay robustness, cheating and unusual-but-valid plans."""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from lifeadmin.agent.tools_v2 import execute_tool_v2
from lifeadmin.harness import llm_agent, run_trial
from lifeadmin.simulator.workspace_v2 import WorkspaceV2

TASKS = ROOT / "benchmark/tasks"


def scripted(*calls):
    def agent(view, call_tool):
        for name, args in calls:
            call_tool(name, args)
        return "end_turn"
    return agent


def run(task, *calls):
    record = run_trial(TASKS / f"{task}.json", scripted(*calls))
    return record["verdict"]["success"], record["replay_ok"]


class MalformedInputTests(unittest.TestCase):
    def test_every_malformed_call_is_a_scored_rejection(self):
        workspace = WorkspaceV2(json.loads((TASKS / "batch_recovery.json").read_text()))
        calls = [
            ("pay_bill", {"bill_id": "r-482", "amount_cents": 1.5}), ("pay_bill", {"bill_id": "r-482", "amount_cents": True}),
            ("pay_bill", {"bill_id": "r-482", "amount_cents": -5}), ("pay_bill", {"bill_id": "r-482", "amount_cents": 10**30}),
            ("pay_bill", {"bill_id": ["r-482"]}), ("pay_bill", {"bill_id": None}), ("pay_bill", "r-482"), ("nope", {}),
            ("schedule_payment", {"bill_id": "r-482", "payment_date": "2026-10-15", "extra": 1}),
            ("schedule_payment", {"bill_id": "r-482", "payment_date": "2026-02-30"}),
            ("cancel_scheduled_payment", {}), ("read_document", {"document_id": "../../etc/passwd"}),
        ]
        for name, args in calls:
            with self.subTest(call=(name, args)):
                text, is_error = execute_tool_v2(workspace, name, args)
                self.assertTrue(is_error)
                self.assertIn("error", json.loads(text))
        self.assertEqual(workspace.payments[2:], [])  # only the two seeded payments exist


class ReplayTests(unittest.TestCase):
    def test_agent_that_mutates_its_arguments_still_replays(self):
        def agent(view, call_tool):
            args = {"bill_id": "r-482", "payment_date": "2026-10-15"}
            call_tool("schedule_payment", args)
            args["bill_id"] = "r-713"
            call_tool("schedule_payment", args)
            call_tool("advance_time", {})
            return "end_turn"
        record = run_trial(TASKS / "batch_recovery.json", agent)
        self.assertTrue(record["verdict"]["success"])
        self.assertTrue(record["replay_ok"])


class CheatingAndUnusualPlanTests(unittest.TestCase):
    def test_cheating_fails(self):
        cases = {
            "overpay the car loan in one payment": ("conflicting_notices", ("pay_bill", {"bill_id": "r-8w3d"}),
                ("pay_bill", {"bill_id": "r-2k7p", "amount_cents": 60000}), ("schedule_payment", {"bill_id": "r-4c1m", "payment_date": "2026-10-06"})),
            "underpay rent, top up late": ("conflicting_notices", ("pay_bill", {"bill_id": "r-8w3d"}), ("pay_bill", {"bill_id": "r-2k7p"}),
                ("schedule_payment", {"bill_id": "r-4c1m", "payment_date": "2026-10-06", "amount_cents": 100000}),
                ("schedule_payment", {"bill_id": "r-4c1m", "payment_date": "2026-10-09", "amount_cents": 40000})),
            "revised amount but late": ("replace_schedule", ("advance_time", {}), ("advance_time", {}),
                ("cancel_scheduled_payment", {"instruction_id": "sp-1006"}), ("schedule_payment", {"bill_id": "bp-48213", "payment_date": "2026-10-18"})),
            "revised amount split in two": ("replace_schedule", ("advance_time", {}), ("advance_time", {}),
                ("cancel_scheduled_payment", {"instruction_id": "sp-1006"}), ("pay_bill", {"bill_id": "bp-48213", "amount_cents": 4125}),
                ("pay_bill", {"bill_id": "bp-48213", "amount_cents": 4125})),
            "extra phone payment on the last day": ("batch_recovery", ("schedule_payment", {"bill_id": "r-482", "payment_date": "2026-10-15"}),
                ("schedule_payment", {"bill_id": "r-713", "payment_date": "2026-10-15"}), ("schedule_payment", {"bill_id": "r-157", "payment_date": "2026-10-21"})),
            "insurance after its grace period": ("batch_recovery", ("schedule_payment", {"bill_id": "r-482", "payment_date": "2026-10-15"}),
                ("schedule_payment", {"bill_id": "r-713", "payment_date": "2026-10-21"})),
        }
        for name, (task, *calls) in cases.items():
            with self.subTest(case=name):
                success, replay_ok = run(task, *calls)
                self.assertFalse(success)
                self.assertTrue(replay_ok)

    def test_unusual_valid_plans_pass(self):
        cases = {
            "rent paid in two parts by the new deadline": ("conflicting_notices", ("pay_bill", {"bill_id": "r-8w3d"}), ("pay_bill", {"bill_id": "r-2k7p"}),
                ("pay_bill", {"bill_id": "r-4c1m", "amount_cents": 117500}),
                ("schedule_payment", {"bill_id": "r-4c1m", "payment_date": "2026-10-06", "amount_cents": 22500})),
            "cancel by bill, then schedule the last day": ("replace_schedule", ("advance_time", {}), ("advance_time", {}),
                ("cancel_scheduled_payment", {"bill_id": "bp-48213"}), ("schedule_payment", {"bill_id": "bp-48213", "payment_date": "2026-10-17"})),
            "each retry on its last day": ("batch_recovery", ("schedule_payment", {"bill_id": "r-482", "payment_date": "2026-10-16"}),
                ("schedule_payment", {"bill_id": "r-713", "payment_date": "2026-10-20"})),
        }
        for name, (task, *calls) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(run(task, *calls), (True, True))


class FakeMessages:
    """A scripted stand-in for the model API: three turns, then the run ends."""

    def __init__(self):
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        turn = len(self.requests)
        if turn == 1:
            return SimpleNamespace(stop_reason="tool_use", content=[
                SimpleNamespace(type="tool_use", id="t1", name="get_activity", input={})])
        if turn == 2:
            assert request["messages"][-1]["content"][0]["tool_use_id"] == "t1"
            return SimpleNamespace(stop_reason="tool_use", content=[
                SimpleNamespace(type="tool_use", id="t2", name="schedule_payment", input={"bill_id": "r-482", "payment_date": "2026-10-15"}),
                SimpleNamespace(type="tool_use", id="t3", name="schedule_payment", input={"bill_id": "r-713", "payment_date": "2026-10-15"}),
                SimpleNamespace(type="tool_use", id="t4", name="pay_bill", input={"bill_id": "nope"})])
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Done.")])


class ModelLoopTests(unittest.TestCase):
    def test_llm_loop_runs_a_task_and_returns_errors_to_the_model(self):
        messages = FakeMessages()
        transcript = []
        agent = llm_agent(SimpleNamespace(messages=messages), "test-model", transcript=transcript)
        record = run_trial(TASKS / "batch_recovery.json", agent)
        self.assertEqual(record["stop_reason"], "end_turn")
        self.assertTrue(record["verdict"]["success"])
        self.assertTrue(record["replay_ok"])
        first = messages.requests[0]["messages"][0]["content"]
        self.assertIn("Some of the payments in yesterday's scheduled batch bounced", first)
        self.assertNotIn("electric-oct", first)
        history = messages.requests[-1]["messages"]  # the loop shares and extends this list
        results = [m for m in history if m["role"] == "user" and isinstance(m["content"], list)][-1]["content"]
        self.assertEqual([r["is_error"] for r in results], [False, False, True])
        self.assertEqual(len(transcript), 3)


if __name__ == "__main__":
    unittest.main()
