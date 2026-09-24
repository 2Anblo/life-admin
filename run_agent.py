"""Run the first task with a Tinker model via its Anthropic-compatible API."""

import json
import os
from pathlib import Path

from anthropic import Anthropic

from benchmark.checkers.bill_payment import check_success
from lifeadmin.agent.runner import run_agent
from lifeadmin.simulator.workspace import Workspace


TASK_PATH = Path(__file__).parent / "benchmark/tasks/avoid_late_fees_01.json"
TINKER_BASE_URL = "https://tinker.thinkingmachines.dev/services/tinker-prod/anthropic/api"


def main() -> None:
    api_key = os.environ["TINKER_API_KEY"]
    model = os.environ["TINKER_MODEL"]
    task = json.loads(TASK_PATH.read_text(encoding="utf-8"))
    workspace = Workspace(task)
    client = Anthropic(api_key=api_key, base_url=TINKER_BASE_URL)

    agent_result = run_agent(client, model, task, workspace)
    for call in agent_result["trace"]:
        print(f"{call['tool']}({call['input']}) -> {call['output']}")
    print(f"Agent stopped: {agent_result['stop_reason']}")
    if agent_result["final_text"]:
        print(f"Agent response: {agent_result['final_text']}")

    workspace.run_to_horizon()
    print(f"Final state: {workspace.result()}")
    print(f"Task success: {check_success(task, workspace.result())}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"LifeAdmin run failed: {error}") from error
