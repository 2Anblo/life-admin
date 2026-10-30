"""Benchmark harness for v2 and v3 tasks.

One trial: a fresh LifeWorkspace from the task file (v2 payment fixtures run on
it unchanged), an agent that sees only the
visible view and a tool function, a snapshot when the agent stops (S_stop), the
evaluator's rollout to the horizon with no agent actions, a second snapshot
(S_h), then the task's checker on a pristine copy of the fixture read from disk.
Every trial is replayed from its tool calls on a fresh workspace; a mismatch
means the harness, not the agent, is broken.

An agent is any callable ``agent(view, call_tool) -> stop_reason`` where
``call_tool(name, arguments) -> (json_text, is_error)``.
"""

import hashlib
import importlib
import inspect
import json
from copy import deepcopy
from pathlib import Path

from lifeadmin.agent.tools_v3 import TOOLS, execute_tool
from lifeadmin.simulator.workspace_v3 import LifeWorkspace

ENVIRONMENT_RULES = """Environment rules (the same for every task):
- You may schedule a payment for any date after today and no later than the end of the evaluation period. Scheduled payments run on their date even after you stop.
- Each day, deposits and newly arrived documents come first, then scheduled payments in the order they were set up.
- Dates use YYYY-MM-DD. A bill is on time if it is paid on or before its due date.
- advance_time moves to the next deposit, new document or pending scheduled payment, or to the end of the evaluation period. It never stops at due dates.
- A payment larger than the available balance is refused. A second payment to the same bill goes through, and the biller keeps it as a credit.
- Subscription renewals charge on their renewal date. A plan change applies from the next renewal.
- Merchants review refund requests after you submit them; approved refunds and other card postings appear in get_activity when they post.
- Tool calls cannot be undone, except cancelling a scheduled payment you set up."""

DEFAULT_SYSTEM_PROMPT = ("You manage the user's bills, subscriptions, refunds and reminders with the tools provided. "
                         "Complete the user's request.")


def _shuffled(task_id: str, ids: list) -> list:
    """Deterministic per-task order, so the author's listing order carries no hint."""
    return sorted(ids, key=lambda item: hashlib.sha256(f"{task_id}:{item}".encode()).hexdigest())


def visible_view(task: dict) -> dict:
    """Everything an agent may see at the start. Nothing from ``hidden`` or ``events``."""
    titles = {doc["id"]: doc.get("title", "") for doc in task["documents"]}
    start_documents = [doc["id"] for doc in task["documents"] if doc.get("release_date") is None]
    order = task.get("display_order") or _shuffled(task["id"], start_documents)
    bills = sorted((b for b in task.get("bills", []) if not b.get("release_date")),
                   key=lambda bill: (bill["due_date"], bill["id"]))
    return {
        "instruction": task["instruction"],
        "start_date": task["start_date"],
        "horizon": task["horizon"],
        "documents": [{"document_id": d, "title": titles[d]} for d in order if d in start_documents],
        "bills": [{"bill_id": b["id"], "payee": b["payee"], "amount_cents": b["amount_cents"],
                   "due_date": b["due_date"]} for b in bills],
        "accounts": [{"account_id": a["id"], "label": a["label"]} for a in task.get("accounts", [])],
    }


def first_message(view: dict) -> str:
    bill_lines = "\n".join(
        f"- {bill['bill_id']}: {bill['payee']}, ${bill['amount_cents'] / 100:.2f} due {bill['due_date']}"
        for bill in view["bills"]
    )
    return (
        f"{view['instruction']}\n\n{ENVIRONMENT_RULES}\n\n"
        f"Today is {view['start_date']}. The evaluation period ends on {view['horizon']}.\n"
        "Documents available now:\n" + "\n".join(f"- {d['document_id']}: {d['title']}" for d in view["documents"]) + "\n"
        f"Bills in your bill-pay service, as the service shows them:\n{bill_lines or '- none'}"
        + ("\nAccounts:\n" + "\n".join(f"- {a['account_id']}: {a['label']}" for a in view["accounts"])
           if view.get("accounts") else "")
    )


def load_checker(task: dict):
    module_name, function_name = task["hidden"]["checker"].split(":")
    return getattr(importlib.import_module(module_name), function_name)


def score(check, task: dict, s_stop: dict, s_h: dict) -> dict:
    """v2 checkers take (task, S_stop, S_h); v3 checkers read only the final result S_h."""
    if len(inspect.signature(check).parameters) == 3:
        return check(task, s_stop, s_h)
    return check(task, s_h)


def replay(task: dict, calls: list) -> tuple[dict, dict]:
    """Re-run logged tool calls on a fresh workspace; returns (S_stop, S_h)."""
    workspace = LifeWorkspace(task)
    for call in calls:
        execute_tool(workspace, call["tool"], call["input"])
    s_stop = workspace.snapshot()
    workspace.run_to_horizon()
    return s_stop, workspace.snapshot()


def run_trial(task_path, agent, checker=None) -> dict:
    path = Path(task_path)
    raw = path.read_bytes()
    task = json.loads(raw)
    workspace = LifeWorkspace(task)
    calls = []

    def call_tool(name, arguments):
        logged = deepcopy(arguments)  # the agent may reuse and change its argument object later
        text, is_error = execute_tool(workspace, name, arguments)
        calls.append({"tool": name, "input": logged, "output": text, "is_error": is_error,
                      "sim_date": workspace.today})
        return text, is_error

    stop_reason = agent(visible_view(task), call_tool)
    s_stop = workspace.snapshot()
    workspace.run_to_horizon()
    s_h = workspace.snapshot()

    pristine = json.loads(path.read_bytes())
    check = checker or load_checker(pristine)
    verdict = score(check, pristine, s_stop, s_h)
    replayed_stop, replayed_h = replay(pristine, calls)
    return {
        "task_id": task["id"],
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "stop_reason": stop_reason,
        "tool_calls": calls,
        "s_stop": s_stop,
        "s_h": s_h,
        "verdict": verdict,
        "replay_ok": replayed_stop == s_stop and replayed_h == s_h,
    }


def llm_agent(client, model, system_prompt=DEFAULT_SYSTEM_PROMPT, max_steps=20, max_tokens=1024,
              temperature=None, transcript=None):
    """A plain observe-act loop over the shared tools (the adaptive tool-using baseline)."""

    def agent(view, call_tool):
        messages = [{"role": "user", "content": first_message(view)}]
        for _ in range(max_steps):
            request = {"model": model, "max_tokens": max_tokens, "system": system_prompt,
                       "tools": TOOLS, "messages": messages}
            if temperature is not None:
                request["temperature"] = temperature
            response = client.messages.create(**request)
            messages.append({"role": "assistant", "content": response.content})
            if transcript is not None:
                transcript.append({"stop_reason": response.stop_reason,
                                   "text": [b.text for b in response.content if b.type == "text"]})
            if response.stop_reason != "tool_use":
                return response.stop_reason
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                text, is_error = call_tool(block.name, block.input)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": text, "is_error": is_error})
            messages.append({"role": "user", "content": results})
        return "max_steps"

    return agent
