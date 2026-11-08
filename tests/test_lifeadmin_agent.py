"""Tests for the LifeAdmin agent (loop, no-validator ablation, fixed pipeline) with a scripted fake model."""

import inspect
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from anthropic.resources.messages import Messages

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evaluation.runner import expand_agents, run_eval  # noqa: E402
from lifeadmin.agent.lifeadmin_agent import READER_SYSTEM, VALIDATOR_SYSTEM, parse_object  # noqa: E402

SDK_CREATE_PARAMS = set(inspect.signature(Messages.create).parameters) - {"self"}
SETTINGS = {"model": "fake-model", "temperature": 0.0}
LEDGER = {"facts": [{"id": "F1", "source": "doc", "issued": None, "statement": "something"}],
          "conflicts": [], "todo": [{"id": "T1", "task": "do the task", "when": "now", "sources": ["F1"]}], "do_not": []}


def load(task_id):
    return json.loads((ROOT / f"benchmark/tasks/{task_id}.json").read_text(encoding="utf-8"))


def reply(blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=0, cache_creation_input_tokens=50,
                                                 cache_read_input_tokens=0, output_tokens=10))


def text(value):
    return SimpleNamespace(type="text", text=value)


class FakeModel:
    """Answers by role. The planner replays ``plan`` one call per turn and repeats a step that was blocked.

    ``reject`` lists tool names the Validator rejects the first time it sees them.
    ``stop_rejections`` is how many times the stop check says the work is not finished.
    """

    def __init__(self, plan, reject=(), stop_rejections=0, pipeline_plan=None):
        self.plan, self.reject, self.stop_rejections = plan, set(reject), stop_rejections
        self.pipeline_plan = pipeline_plan
        self.roles, self.planner_calls, self.rejected = [], 0, set()
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **request):
        assert not set(request) - SDK_CREATE_PARAMS, request.keys()
        content = request["messages"][0]["content"]
        if request["system"] == READER_SYSTEM:
            self.roles.append("reader")
            return reply([text("```json\n" + json.dumps(LEDGER) + "\n```")])
        if request["system"] == VALIDATOR_SYSTEM:
            self.roles.append("validator")
            if "## The Planner wants to stop" in content:
                if self.stop_rejections:
                    self.stop_rejections -= 1
                    return reply([text('{"complete": false, "missing": "wait for the refund"}')])
                return reply([text('{"complete": true}')])
            tool = re.search(r"## Proposed action\n(\S+)", content).group(1)
            if tool in self.reject and tool not in self.rejected:
                self.rejected.add(tool)
                return reply([text('{"verdict": "reject", "problem": "wrong amount"}')])
            return reply([text('{"verdict": "approve"}')])
        self.roles.append("planner")
        if "tools" not in request:  # the fixed pipeline's single plan
            return reply([text(json.dumps(self.pipeline_plan))])
        self.planner_calls += 1
        redo = content.count("BLOCKED by the Validator") + content.count("NOT FINISHED, says the Validator")
        step = self.planner_calls - 1 - redo
        if step < len(self.plan):
            item = self.plan[step]
            return reply([text(f"Status: step {step}."), SimpleNamespace(
                type="tool_use", id=f"tu-{self.planner_calls}", name=item["tool"], input=dict(item.get("args", {})))],
                "tool_use")
        return reply([text("All todo items are done.")])


class LifeAdminAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_agent(self, agent, task_id, model):
        return run_eval(self.out, [agent], [task_id], 1, SETTINGS, client=model, progress=lambda line: None,
                        sleep=lambda s: None)[0]

    def test_full_agent_solves_a_task_with_a_correct_planner(self):
        task = load("split_tender_refund")
        model = FakeModel(task["hidden"]["reference_plan"])
        record = self.run_agent("lifeadmin", "split_tender_refund", model)
        self.assertEqual(record["status"], "scored", record.get("traceback"))
        self.assertTrue(record["success"], record["verdict"])
        self.assertTrue(record["replay_ok"])
        log = record["agent_log"]
        self.assertEqual(log["ledger"], LEDGER)
        self.assertEqual(log["ledger_updates"], 2)  # the start documents, then the one that arrives later
        self.assertEqual({c["role"] for c in record["api_calls"]}, {"reader", "planner", "validator"})
        consequential = [c for c in record["tool_calls"] if c["tool"] in ("request_refund", "escalate_case", "close_case")]
        self.assertEqual(len([d for d in log["validator"] if d["kind"] == "action"]), len(consequential))
        self.assertEqual(log["validator"][-1]["kind"], "stop")
        reads = [c["input"]["document_id"] for c in record["tool_calls"] if c["tool"] == "read_document"]
        self.assertEqual(set(reads), {d["id"] for d in task["documents"]})

    def test_a_blocked_action_never_reaches_the_environment(self):
        plan = load("avoid_late_fees")["hidden"]["reference_plan"]
        model = FakeModel(plan, reject={"pay_bill"})
        record = self.run_agent("lifeadmin", "avoid_late_fees", model)
        self.assertTrue(record["success"], record["verdict"])
        self.assertEqual(sum(c["tool"] == "pay_bill" for c in record["tool_calls"]), 1)  # only the approved retry
        rejects = [d for d in record["agent_log"]["validator"] if d["verdict"] == "reject"]
        self.assertEqual([(d["tool"], d["problem"]) for d in rejects], [("pay_bill", "wrong amount")])

    def test_an_exact_repeat_is_blocked_without_asking_the_model(self):
        plan = load("avoid_late_fees")["hidden"]["reference_plan"]
        pay = next(step for step in plan if step["tool"] == "pay_bill")
        model = FakeModel([pay, pay])
        record = self.run_agent("lifeadmin", "avoid_late_fees", model)
        self.assertEqual(sum(c["tool"] == "pay_bill" for c in record["tool_calls"]), 1)
        kinds = [d["kind"] for d in record["agent_log"]["validator"]]
        self.assertIn("duplicate", kinds)
        self.assertEqual(kinds.count("action"), 1)

    def test_stop_check_sends_the_planner_back(self):
        plan = load("avoid_late_fees")["hidden"]["reference_plan"]
        model = FakeModel(plan, stop_rejections=1)
        record = self.run_agent("lifeadmin", "avoid_late_fees", model)
        stops = [d["verdict"] for d in record["agent_log"]["validator"] if d["kind"] == "stop"]
        self.assertEqual(stops, ["reject", "approve"])
        self.assertTrue(record["success"])

    def test_no_validator_ablation_makes_no_validator_calls(self):
        plan = load("avoid_late_fees")["hidden"]["reference_plan"]
        pay = next(step for step in plan if step["tool"] == "pay_bill")
        model = FakeModel([pay, pay], reject={"pay_bill"})
        record = self.run_agent("lifeadmin_no_validator", "avoid_late_fees", model)
        self.assertNotIn("validator", model.roles)
        self.assertNotIn("validator", record["agent_log"])
        self.assertEqual(sum(c["tool"] == "pay_bill" for c in record["tool_calls"]), 2)  # nothing stops the repeat

    def test_pipeline_runs_one_plan_and_skips_blocked_steps(self):
        plan = load("duplicate_charge_dispute")["hidden"]["reference_plan"]
        model = FakeModel([], pipeline_plan=plan)
        record = self.run_agent("pipeline", "duplicate_charge_dispute", model)
        self.assertTrue(record["success"], record["verdict"])
        self.assertEqual(record["stop_reason"], "plan_executed")
        self.assertEqual(model.roles.count("planner"), 1)
        model = FakeModel([], pipeline_plan=plan, reject={"open_dispute"})
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        record = self.run_agent("pipeline", "duplicate_charge_dispute", model)
        self.assertFalse(record["success"])
        self.assertFalse(any(c["tool"] == "open_dispute" for c in record["tool_calls"]))

    def test_finish_tool_ends_the_run_and_never_reaches_the_environment(self):
        pay = next(s for s in load("avoid_late_fees")["hidden"]["reference_plan"] if s["tool"] == "pay_bill")
        model = FakeModel([pay, {"tool": "finish", "args": {"summary": "paid"}}])
        record = self.run_agent("lifeadmin", "avoid_late_fees", model)
        self.assertEqual(record["stop_reason"], "finish")
        self.assertFalse(any(c["tool"] == "finish" for c in record["tool_calls"]))
        self.assertEqual(record["agent_log"]["validator"][-1]["kind"], "stop")

    def test_unchanged_reads_are_answered_by_the_state_manager_and_stall_ends_the_run(self):
        model = FakeModel([{"tool": "get_activity"}] * 10)
        record = self.run_agent("lifeadmin_no_validator", "avoid_late_fees", model)
        self.assertEqual(sum(c["tool"] == "get_activity" for c in record["tool_calls"]), 1)  # the start-up read only
        self.assertEqual(record["stop_reason"], "idle")
        self.assertEqual(model.planner_calls, 4)  # three skipped turns, then the stop

    def test_agents_are_registered(self):
        self.assertEqual(expand_agents(["lifeadmin", "lifeadmin_no_validator", "pipeline"]),
                         ["lifeadmin", "lifeadmin_no_validator", "pipeline"])

    def test_parse_object(self):
        self.assertEqual(parse_object('Sure:\n```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_object('{"verdict": "approve"}'), {"verdict": "approve"})
        self.assertIsNone(parse_object("no json here"))


if __name__ == "__main__":
    unittest.main()
