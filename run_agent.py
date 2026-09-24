"""Run the first task with a Tinker model via its Anthropic-compatible API."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from anthropic import Anthropic

from benchmark.checkers.bill_payment import check_success
from lifeadmin.agent.runner import run_agent
from lifeadmin.simulator.workspace import Workspace


TASK_PATH = Path(__file__).parent / "benchmark/tasks/avoid_late_fees_01.json"
TINKER_BASE_URL = "https://tinker.thinkingmachines.dev/services/tinker-prod/anthropic/api"
RUNS_DIR = Path(__file__).parent / "results/runs"


def main() -> None:
    started = datetime.now(timezone.utc)
    log_path = RUNS_DIR / f"{started:%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}.json"
    record = {
        "started_at": started.isoformat(),
        "task_id": TASK_PATH.stem,
        "provider": "tinker",
        "model": os.getenv("TINKER_MODEL"),
        "status": "error",
        "api_calls": [],
        "tool_calls": [],
    }
    workspace = None

    try:
        api_key = os.environ["TINKER_API_KEY"]
        model = os.environ["TINKER_MODEL"]
        task = json.loads(TASK_PATH.read_text(encoding="utf-8"))
        workspace = Workspace(task)
        client = Anthropic(api_key=api_key, base_url=TINKER_BASE_URL)

        agent_result = run_agent(
            client, model, task, workspace,
            trace=record["tool_calls"], api_calls=record["api_calls"],
        )
        record["stop_reason"] = agent_result["stop_reason"]
        record["agent_response"] = agent_result["final_text"]
        print(f"Agent stopped: {agent_result['stop_reason']}")
        if agent_result["final_text"]:
            print(f"Agent response: {agent_result['final_text']}")

        workspace.run_to_horizon()
        record["final_state"] = workspace.result()
        record["task_success"] = check_success(task, record["final_state"])
        record["status"] = "completed"
        print(f"Final state: {record['final_state']}")
        print(f"Task success: {record['task_success']}")
    except Exception as error:
        record["error"] = str(error)
        if workspace is not None:
            record["final_state"] = workspace.result()
        raise SystemExit(f"LifeAdmin run failed: {error}") from error
    finally:
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Run log: {log_path}")


if __name__ == "__main__":
    main()
