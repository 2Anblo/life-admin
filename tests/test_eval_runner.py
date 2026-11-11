"""Tests for the evaluation runner, the LLM baselines and the summary, with a fake model client."""

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from anthropic.resources.messages import Messages

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evaluation.runner import expand_agents, record_path, run_eval  # noqa: E402
from evaluation.summarize import load_records, summarize, to_latex, to_markdown  # noqa: E402
from lifeadmin.agent.baselines import InfraError, create_with_retry, parse_plan  # noqa: E402

NO_SLEEP = lambda seconds: None  # noqa: E731

# The fake client accepts only what the installed SDK's Messages.create() accepts.
SDK_CREATE_PARAMS = set(inspect.signature(Messages.create).parameters) - {"self"}


def load(task_id):
    return json.loads((ROOT / f"benchmark/tasks/{task_id}.json").read_text(encoding="utf-8"))


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_block(n, name, args):
    return SimpleNamespace(type="tool_use", id=f"tu-{n}", name=name, input=args)


def response(blocks, stop_reason):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=0, cache_creation_input_tokens=60,
                                                 cache_read_input_tokens=40, output_tokens=20))


class RateLimitError(Exception):
    status_code = 429


class FakeClient:
    """Replays a task's reference plan as one tool call per turn, then answers in text.

    ``fail_first`` makes the first N API calls raise a rate-limit error.
    ``oneshot_text`` is returned instead when the request has no tools (the oneshot baseline).
    """

    def __init__(self, plan, fail_first=0, oneshot_text=None):
        self.plan, self.fail_first, self.oneshot_text = plan, fail_first, oneshot_text
        self.calls = 0
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **request):
        self.calls += 1
        unknown = set(request) - SDK_CREATE_PARAMS
        if unknown:
            raise TypeError(f"Messages.create() got unexpected keyword arguments {sorted(unknown)}")
        if self.calls <= self.fail_first:
            raise RateLimitError("slow down")
        if "tools" not in request:
            return response([text_block(self.oneshot_text)], "end_turn")
        step = sum(1 for m in request["messages"] if m["role"] == "assistant")
        if step < len(self.plan):
            item = self.plan[step]
            return response([text_block("Working."), tool_block(step, item["tool"], item.get("args", {}))], "tool_use")
        return response([text_block("Done.")], "end_turn")


SETTINGS = {"model": "fake-model", "temperature": 0.0}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_llm(self, task_id, client, agent="plain_loop", trials=1):
        return run_eval(self.out, [agent], [task_id], trials, SETTINGS, client=client, progress=lambda line: None,
                        sleep=NO_SLEEP)

    def test_plain_loop_records_usage_and_passes_with_a_correct_model(self):
        records = self.run_llm("split_tender_refund", FakeClient(load("split_tender_refund")["hidden"]["reference_plan"]), trials=2)
        self.assertEqual([r["success"] for r in records], [True, True])
        self.assertTrue(all(r["replay_ok"] for r in records))
        self.assertEqual(len(records[0]["api_calls"]), 7)  # six tool turns and a final answer
        summary = summarize(load_records(self.out))["agents"]["plain_loop"]
        self.assertEqual(summary["success_rate"], 1.0)
        self.assertEqual(summary["mean_input_tokens"], 700)

    def test_resume_skips_finished_trials(self):
        client = FakeClient(load("avoid_late_fees")["hidden"]["reference_plan"])
        self.run_llm("avoid_late_fees", client)
        calls = client.calls
        self.assertEqual(self.run_llm("avoid_late_fees", client), [])
        self.assertEqual(client.calls, calls)

    def test_changed_settings_are_refused(self):
        self.run_llm("avoid_late_fees", FakeClient(load("avoid_late_fees")["hidden"]["reference_plan"]))
        with self.assertRaises(SystemExit):
            run_eval(self.out, ["plain_loop"], ["avoid_late_fees"], 1, {**SETTINGS, "temperature": 1.0},
                     client=FakeClient([]), progress=lambda line: None, sleep=NO_SLEEP)

    def test_transient_api_errors_are_retried_then_restart_the_trial(self):
        plan = load("avoid_late_fees")["hidden"]["reference_plan"]
        record = self.run_llm("avoid_late_fees", FakeClient(plan, fail_first=2))[0]
        self.assertEqual(record["status"], "scored")
        self.assertEqual(record["attempts"], 1)
        self.assertEqual(record["api_calls"][0]["attempts"], 3)
        record = self.run_llm("schedule_window", FakeClient(load("schedule_window")["hidden"]["reference_plan"], fail_first=6))[0]
        self.assertEqual(record["status"], "scored")
        self.assertEqual(record["attempts"], 2)
        self.assertEqual(len(record["infra_errors"]), 1)

    def test_persistent_api_failure_is_not_scored(self):
        record = self.run_llm("avoid_late_fees", FakeClient([], fail_first=10 ** 6))[0]
        self.assertEqual(record["status"], "infra_error")
        self.assertEqual(len(record["infra_errors"]), 3)
        summary = summarize(load_records(self.out))["agents"]["plain_loop"]
        self.assertEqual(summary["unscored"], {"infra_error": 1})

    def test_oneshot_runs_a_parsed_plan(self):
        plan = load("duplicate_charge_dispute")["hidden"]["reference_plan"]
        text = "Here is the plan:\n```json\n" + json.dumps(plan) + "\n```"
        record = self.run_llm("duplicate_charge_dispute", FakeClient([], oneshot_text=text), agent="oneshot")[0]
        self.assertTrue(record["success"], record["verdict"])
        self.assertEqual(record["stop_reason"], "plan_executed")
        self.assertEqual(record["plan"], plan)
        reads = [c for c in record["tool_calls"] if c["tool"] == "read_document"]
        self.assertEqual(len(reads), len(load("duplicate_charge_dispute")["documents"]))

    def test_oneshot_without_a_plan_fails(self):
        record = self.run_llm("duplicate_charge_dispute", FakeClient([], oneshot_text="I am not sure."), agent="oneshot")[0]
        self.assertEqual(record["stop_reason"], "invalid_plan")
        self.assertFalse(record["success"])

    def test_scripted_agents_run_once_and_summaries_render(self):
        run_eval(self.out, expand_agents(["reference", "P6_open_loop"]), ["replace_schedule", "loan_payoff_cleanup"], 3, {},
                 progress=lambda line: None)
        self.assertFalse(record_path(self.out, "reference", "replace_schedule", 2).exists())
        summary = summarize(load_records(self.out), baseline="P6_open_loop")
        self.assertEqual(summary["agents"]["reference"]["success_rate"], 1.0)
        self.assertEqual(summary["agents"]["P6_open_loop"]["per_task"], {"replace_schedule": 0.0, "loan_payoff_cleanup": 1.0})
        self.assertEqual(summary["agents"]["reference"]["vs_baseline"]["difference"], 0.5)
        self.assertIn("| reference |", to_markdown(summary))
        self.assertIn("\\toprule", to_latex(summary))


class BaselineUnitTests(unittest.TestCase):
    def test_parse_plan(self):
        self.assertEqual(parse_plan('[{"tool": "advance_time"}]'), [{"tool": "advance_time", "args": {}}])
        self.assertIsNone(parse_plan("no plan"))
        self.assertIsNone(parse_plan('[{"name": "x"}]'))

    def test_non_transient_errors_are_not_retried(self):
        class Broken:
            def __init__(self):
                self.calls = 0
                self.messages = SimpleNamespace(create=self.create)

            def create(self, **request):
                self.calls += 1
                raise ValueError("bad request")

        client = Broken()
        with self.assertRaises(ValueError):
            create_with_retry(client, {}, sleep=NO_SLEEP)
        self.assertEqual(client.calls, 1)

    def test_tool_call_without_tools_is_an_empty_answer(self):
        class Refusing:
            messages = SimpleNamespace(create=lambda **request: (_ for _ in ()).throw(ToolCallRefused()))

        class ToolCallRefused(Exception):
            status_code = 422

            def __str__(self):
                return "model called a tool when tool_choice='none'"

        response, attempts = create_with_retry(Refusing(), {}, sleep=NO_SLEEP)
        self.assertEqual((response.content, response.stop_reason, attempts), ([], "tool_call_without_tools", 1))

    def test_retries_give_up_with_infra_error(self):
        with self.assertRaises(InfraError):
            create_with_retry(FakeClient([], fail_first=100), {}, retries=2, sleep=NO_SLEEP)


if __name__ == "__main__":
    unittest.main()
