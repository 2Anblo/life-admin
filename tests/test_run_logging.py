import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import run_agent as run_agent_script
from test_agent_loop import FakeMessages


class RunLoggingTests(unittest.TestCase):
    def test_successful_run_writes_record_without_key(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(run_agent_script, "RUNS_DIR", Path(directory)), patch.object(
                run_agent_script, "Anthropic", return_value=SimpleNamespace(messages=FakeMessages())
            ), patch.dict(os.environ, {"TINKER_API_KEY": "test-secret", "TINKER_MODEL": "test-model"}, clear=True):
                run_agent_script.main()

            log_text = next(Path(directory).glob("*.json")).read_text()
            record = json.loads(log_text)
            self.assertEqual(record["status"], "completed")
            self.assertTrue(record["task_success"])
            self.assertEqual(len(record["api_calls"]), 3)
            self.assertEqual(len(record["tool_calls"]), 4)
            self.assertNotIn("test-secret", log_text)

    def test_failed_run_still_writes_log(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(run_agent_script, "RUNS_DIR", Path(directory)), patch.dict(
                os.environ, {"TINKER_MODEL": "test-model"}, clear=True
            ):
                with self.assertRaises(SystemExit):
                    run_agent_script.main()

            record = json.loads(next(Path(directory).glob("*.json")).read_text())
            self.assertEqual(record["status"], "error")
            self.assertIn("TINKER_API_KEY", record["error"])

    def test_tool_failure_keeps_attempted_call_and_partial_state(self):
        bad_response = SimpleNamespace(
            stop_reason="tool_use",
            content=[SimpleNamespace(
                type="tool_use", id="bad-1", name="pay_bill", input={"bill_id": "unknown"}
            )],
        )
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: bad_response))
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(run_agent_script, "RUNS_DIR", Path(directory)), patch.object(
                run_agent_script, "Anthropic", return_value=client
            ), patch.dict(os.environ, {"TINKER_API_KEY": "test-secret", "TINKER_MODEL": "test-model"}, clear=True):
                with self.assertRaises(SystemExit):
                    run_agent_script.main()

            record = json.loads(next(Path(directory).glob("*.json")).read_text())
            self.assertEqual(record["status"], "error")
            self.assertEqual(record["api_calls"][0]["stop_reason"], "tool_use")
            self.assertEqual(record["tool_calls"][0]["input"], {"bill_id": "unknown"})
            self.assertNotIn("output", record["tool_calls"][0])
            self.assertEqual(record["final_state"]["balance_cents"], 12000)


if __name__ == "__main__":
    unittest.main()
