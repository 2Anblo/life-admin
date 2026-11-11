"""Run agents on benchmark tasks and save one JSON record per trial.

A trial is ``harness.run_trial`` on a fresh workspace. Records go to
``<out>/<agent>/<task_id>/trial-<n>.json``. A finished record is never rerun,
so an interrupted evaluation resumes where it stopped.

Following the benchmark protocol, a model-API failure that persists after the
client's own retries (``InfraError``) restarts the trial from a fresh state, up
to ``infra_retries`` more times, and is reported separately; it is never scored
as an agent failure. Malformed tool calls and exhausted budgets are agent errors
and are scored. Any other exception is a harness bug: the record keeps the
traceback with status ``error`` and is not scored.

Scripted agents (the reference plan and floor policies) are deterministic, so
they run one trial per task whatever ``trials`` is.
"""

import json
import subprocess
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from benchmark.floor_policies import all_floor_policies, reference_policy
from lifeadmin.agent.baselines import InfraError, oneshot_agent, plain_loop_agent
from lifeadmin.agent.lifeadmin_agent import lifeadmin_agent, pipeline_agent
from lifeadmin.harness import DEFAULT_SYSTEM_PROMPT, run_trial

ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = ROOT / "benchmark/tasks"
TASK_INDEX = ROOT / "benchmark/task_index.json"

FLOOR_NAMES = ["P0_noop", "P1_last_allowed_date", "P2_greedy", "P3_pay_all_now", "P4_newest_document",
               "P5_oldest_document", "P6_open_loop", "P7_act_on_everything"]
LLM_AGENTS = {
    "plain_loop": plain_loop_agent,
    "oneshot": oneshot_agent,
    "lifeadmin": lifeadmin_agent,
    "lifeadmin_no_validator": lambda client, model, **kw: lifeadmin_agent(client, model, validator=False, **kw),
    "pipeline": pipeline_agent,
}
LOOP_AGENTS = {"plain_loop", "lifeadmin", "lifeadmin_no_validator"}  # take a step cap
SCRIPTED_AGENTS = ["reference"] + FLOOR_NAMES

DEFAULT_SETTINGS = {
    "model": None,
    "temperature": 0.0,
    "max_steps": 30,
    "max_tokens": 4096,  # per response in step-by-step calls (the model's hidden reasoning counts toward it)
    "long_max_tokens": 8192,  # single long outputs: a one-shot plan, the Reader's ledger
    "max_tool_calls": 60,
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
}

_print_lock = threading.Lock()


def task_index() -> list:
    return json.loads(TASK_INDEX.read_text(encoding="utf-8"))["tasks"]


def all_task_ids() -> list:
    return [t["id"] for t in task_index()]


def expand_agents(names) -> list:
    """'floors' stands for every floor policy; 'scripted' for the reference plan plus floors."""
    out = []
    for name in names:
        if name == "floors":
            out += FLOOR_NAMES
        elif name == "scripted":
            out += SCRIPTED_AGENTS
        elif name in LLM_AGENTS or name in SCRIPTED_AGENTS:
            out.append(name)
        else:
            raise ValueError(f"Unknown agent: {name}. Known: {', '.join(list(LLM_AGENTS) + SCRIPTED_AGENTS)}, floors, scripted")
    return list(dict.fromkeys(out))


def is_llm(agent_name: str) -> bool:
    return agent_name in LLM_AGENTS


def make_agent(name, task, settings, client, sleep=time.sleep):
    """Returns (agent, log). The log dict collects API usage for LLM agents."""
    log = {}
    if name == "reference":
        return reference_policy(task), log
    if name in FLOOR_NAMES:
        return all_floor_policies(task)[name], log
    factory = LLM_AGENTS[name]
    long_output = name in ("oneshot", "pipeline")
    kwargs = {"system_prompt": settings["system_prompt"],
              "max_tokens": settings["long_max_tokens" if long_output else "max_tokens"],
              "temperature": settings["temperature"], "max_tool_calls": settings["max_tool_calls"], "log": log,
              "sleep": sleep}
    if name in LOOP_AGENTS:
        kwargs["max_steps"] = settings["max_steps"]
    if name in ("lifeadmin", "lifeadmin_no_validator"):
        kwargs["long_max_tokens"] = settings["long_max_tokens"]
    return factory(client, settings["model"], **kwargs), log


def record_path(out_dir: Path, agent: str, task_id: str, trial: int) -> Path:
    return out_dir / agent / task_id / f"trial-{trial}.json"


def run_one(task_id, agent_name, trial, settings, client, infra_retries=2, sleep=time.sleep) -> dict:
    task_path = TASKS_DIR / f"{task_id}.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    record = {"agent": agent_name, "task_id": task_id, "trial": trial,
              "model": settings["model"] if is_llm(agent_name) else None,
              "started_at": datetime.now(timezone.utc).isoformat(), "infra_errors": []}
    started = time.monotonic()
    for attempt in range(1, infra_retries + 2):
        agent, log = make_agent(agent_name, task, settings, client, sleep)
        try:
            result = run_trial(task_path, agent)
        except InfraError as error:
            record["infra_errors"].append(str(error))
            if attempt <= infra_retries:
                sleep(5 * attempt)
                continue
            record["status"] = "infra_error"
            break
        except Exception:  # noqa: BLE001 - a harness bug must be visible, not scored
            record["status"] = "error"
            record["traceback"] = traceback.format_exc()
            break
        record.update({
            "status": "scored",
            "success": result["verdict"]["success"],
            "verdict": result["verdict"],
            "stop_reason": result["stop_reason"],
            "replay_ok": result["replay_ok"],
            "fixture_sha256": result["fixture_sha256"],
            "tool_calls": result["tool_calls"],
            "api_calls": log.get("api_calls", []),
            "agent_text": log.get("text", []),
            "plan": log.get("plan"),
            "agent_log": {k: v for k, v in log.items() if k not in ("api_calls", "text", "plan")},
            "s_h": result["s_h"],
        })
        break
    record["attempts"] = attempt
    record["duration_s"] = round(time.monotonic() - started, 2)
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    return record


def _git_state():
    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def write_config(out_dir: Path, settings: dict, agents: list, task_ids: list, trials: int) -> dict:
    """Save the frozen settings. Resuming with different settings is refused, so runs never mix."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "config.json"
    frozen = {k: settings[k] for k in DEFAULT_SETTINGS}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing["settings"] != frozen:
            raise SystemExit(f"{path} was written with different settings; use a new --out directory.")
        existing["agents"] = sorted(set(existing["agents"]) | set(agents))
        existing["tasks"] = sorted(set(existing["tasks"]) | set(task_ids))
        existing["trials"] = max(existing["trials"], trials)
        config = existing
    else:
        config = {"created_at": datetime.now(timezone.utc).isoformat(), "settings": frozen,
                  "agents": agents, "tasks": task_ids, "trials": trials, "git": _git_state()}
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config


def plan_trials(out_dir: Path, agents: list, task_ids: list, trials: int, rerun_failed_infra=True) -> list:
    jobs = []
    for agent in agents:
        for task_id in task_ids:
            for trial in range(1, (trials if is_llm(agent) else 1) + 1):
                path = record_path(out_dir, agent, task_id, trial)
                if path.exists():
                    status = json.loads(path.read_text(encoding="utf-8")).get("status")
                    if status == "scored" or (status == "infra_error" and not rerun_failed_infra):
                        continue
                jobs.append((agent, task_id, trial))
    return jobs


def run_eval(out_dir, agents, task_ids, trials, settings, client=None, workers=1, progress=print,
             sleep=time.sleep) -> list:
    out_dir = Path(out_dir)
    settings = {**DEFAULT_SETTINGS, **settings}
    if any(is_llm(a) for a in agents) and (client is None or not settings["model"]):
        raise ValueError("LLM agents need a client and a model")
    write_config(out_dir, settings, agents, task_ids, trials)
    jobs = plan_trials(out_dir, agents, task_ids, trials)
    total, done = len(jobs), [0]

    def work(job):
        agent, task_id, trial = job
        record = run_one(task_id, agent, trial, settings, client, sleep=sleep)
        path = record_path(out_dir, agent, task_id, trial)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        with _print_lock:
            done[0] += 1
            outcome = {True: "PASS", False: "fail"}.get(record.get("success"), record["status"].upper())
            progress(f"[{done[0]}/{total}] {agent:22} {task_id:34} trial {trial}: {outcome}"
                     f" ({record.get('stop_reason', '-')}, {record['duration_s']}s)")
        return record

    if workers <= 1:
        return [work(job) for job in jobs]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(work, jobs))
