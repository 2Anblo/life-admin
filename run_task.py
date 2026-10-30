"""Run one task (any fixture except avoid_late_fees, which runs on v1) with a scripted policy, the reference plan, or a Tinker model.

    python run_task.py benchmark/tasks/<task>.json                 # every floor policy + reference
    python run_task.py benchmark/tasks/<task>.json --agent P1_last_allowed_date
    python run_task.py benchmark/tasks/<task>.json --agent llm     # needs TINKER_API_KEY, TINKER_MODEL

Each trial prints its verdict; --save writes a JSON record to results/runs/.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from benchmark.floor_policies import all_floor_policies, reference_policy
from lifeadmin.harness import llm_agent, run_trial

TINKER_BASE_URL = "https://tinker.thinkingmachines.dev/services/tinker-prod/anthropic/api"
RUNS_DIR = ROOT / "results/runs"


def make_agents(task, choice):
    agents = {"reference": reference_policy(task), **all_floor_policies(task)}
    if choice == "llm":
        from anthropic import Anthropic
        client = Anthropic(api_key=os.environ["TINKER_API_KEY"], base_url=TINKER_BASE_URL)
        return {"llm": llm_agent(client, os.environ["TINKER_MODEL"])}
    if choice:
        return {choice: agents[choice]}
    return agents


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("--agent", default=None)
    parser.add_argument("--save", action="store_true")
    options = parser.parse_args()
    task = json.loads(Path(options.task).read_text(encoding="utf-8"))
    for name, agent in make_agents(task, options.agent).items():
        record = run_trial(options.task, agent)
        failed = [k for k, p in record["verdict"]["predicates"].items() if not p["ok"]]
        print(f"{name:22} success={record['verdict']['success']!s:5} replay_ok={record['replay_ok']!s:5} "
              f"failed={failed}")
        if options.save:
            RUNS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            out = RUNS_DIR / f"{stamp}_{task['id']}_{name}_{uuid4().hex[:6]}.json"
            out.write_text(json.dumps({"agent": name, **record}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
