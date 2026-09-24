import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from benchmark.checkers.bill_payment import check_success
from lifeadmin.agent.runner import run_agent
from lifeadmin.simulator.workspace import Workspace


def tool_call(call_id, name, arguments):
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=arguments)


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **request):
        self.calls.append(request)
        turn = len(self.calls)
        if turn == 1:
            return SimpleNamespace(stop_reason="tool_use", content=[tool_call("read-1", "read_document", {"document_id": "bill-electric"})])
        if turn == 2:
            results = request["messages"][-1]["content"]
            assert results[0]["tool_use_id"] == "read-1"
            assert "Electric bill" in results[0]["content"]
            return SimpleNamespace(stop_reason="tool_use", content=[
                tool_call("schedule-1", "schedule_payment", {"bill_id": "electric", "payment_date": "2026-09-25"}),
                tool_call("schedule-2", "schedule_payment", {"bill_id": "internet", "payment_date": "2026-09-27"}),
                tool_call("schedule-3", "schedule_payment", {"bill_id": "water", "payment_date": "2026-09-28"}),
            ])
        results = request["messages"][-1]["content"]
        assert len(results) == 3
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Payments scheduled.")])


class AgentLoopTests(unittest.TestCase):
    def test_tool_results_return_to_model_and_task_can_succeed(self):
        task = json.loads((ROOT / "benchmark/tasks/avoid_late_fees_01.json").read_text())
        workspace = Workspace(task)
        messages = FakeMessages()
        client = SimpleNamespace(messages=messages)

        result = run_agent(client, "test-model", task, workspace)
        workspace.run_to_horizon()

        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertEqual(len(result["trace"]), 4)
        self.assertEqual(len(messages.calls), 3)
        self.assertTrue(check_success(task, workspace.result()))


if __name__ == "__main__":
    unittest.main()
