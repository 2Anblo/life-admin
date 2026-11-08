"""LLM baselines that run on the shared harness.

- ``plain_loop_agent``: the adaptive tool-using baseline. Each turn the model sees
  the conversation so far and calls tools; it stops when it answers without a
  tool call, or when a budget runs out.
- ``oneshot_agent``: the non-interactive baseline. The harness reads every
  starting document and the account views through the ordinary tools, the model
  writes one complete plan of tool calls, and the plan runs with no feedback.

Both record every API call (stop reason, tokens, latency, retries) in ``log``.
A network, rate-limit or server error is retried with backoff; if it persists,
``InfraError`` is raised so the runner can restart the trial from a fresh state
and report it separately. Malformed tool calls and exhausted budgets are agent
errors and are scored as failures.
"""

import json
import re
import time

from lifeadmin.agent.tools_v3 import TOOLS
from lifeadmin.harness import DEFAULT_SYSTEM_PROMPT, first_message

TRANSIENT_ERRORS = {"APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError",
                    "OverloadedError", "ServiceUnavailableError"}


class InfraError(RuntimeError):
    """The model API failed after retries. Not the agent's fault; the trial is rerun."""


def _is_transient(error: Exception) -> bool:
    if type(error).__name__ in TRANSIENT_ERRORS:
        return True
    status = getattr(error, "status_code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)


def create_with_retry(client, request: dict, retries: int = 4, sleep=time.sleep):
    """Call the API; retry transient failures with exponential backoff. Returns (response, attempts)."""
    for attempt in range(1, retries + 2):
        try:
            return client.messages.create(**request), attempt
        except Exception as error:  # noqa: BLE001 - classify any SDK error
            if not _is_transient(error):
                raise
            if attempt > retries:
                raise InfraError(f"{type(error).__name__}: {error}") from error
            sleep(min(2 ** attempt, 30))


def _record(log, response, started, attempts):
    usage = getattr(response, "usage", None)
    # Tinker reports prompt tokens under the cache fields (input_tokens is 0), so count all three.
    parts = [getattr(usage, f, None) for f in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
    log.setdefault("api_calls", []).append({
        "stop_reason": response.stop_reason,
        "input_tokens": sum(p for p in parts if isinstance(p, int)) if any(isinstance(p, int) for p in parts) else None,
        "cache_read_input_tokens": parts[2] if isinstance(parts[2], int) else None,
        "output_tokens": getattr(usage, "output_tokens", None),
        "latency_s": round(time.monotonic() - started, 3),
        "attempts": attempts,
    })
    text = [block.text for block in response.content if block.type == "text"]
    if text:
        log.setdefault("text", []).append("\n".join(text))


def _request(model, max_tokens, system, messages, temperature, tools=None):
    request = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
    if tools is not None:
        request["tools"] = tools
    if temperature is not None:
        # anthropic>=1.8 dropped `temperature` from create(); send it in the body for Tinker's endpoint.
        request["extra_body"] = {"temperature": temperature}
    return request


def plain_loop_agent(client, model, system_prompt=DEFAULT_SYSTEM_PROMPT, max_steps=30, max_tokens=2048,
                     temperature=None, max_tool_calls=60, log=None, retries=4, sleep=time.sleep, transcript=None):
    """Observe-act loop: one API call per step, every tool call's result returned to the model.

    ``transcript`` (older interface) also receives {stop_reason, text} for every response.
    """
    log = log if log is not None else {}

    def agent(view, call_tool):
        messages = [{"role": "user", "content": first_message(view)}]
        tool_calls = 0
        for _ in range(max_steps):
            started = time.monotonic()
            response, attempts = create_with_retry(
                client, _request(model, max_tokens, system_prompt, messages, temperature, TOOLS), retries, sleep)
            _record(log, response, started, attempts)
            if transcript is not None:
                transcript.append({"stop_reason": response.stop_reason,
                                   "text": [b.text for b in response.content if b.type == "text"]})
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason != "tool_use":
                return response.stop_reason
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if tool_calls >= max_tool_calls:
                    return "tool_budget"
                tool_calls += 1
                text, is_error = call_tool(block.name, block.input)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": text, "is_error": is_error})
            messages.append({"role": "user", "content": results})
        return "max_steps"

    return agent


ONESHOT_INSTRUCTIONS = """You cannot see any tool results after this point. Write the complete plan now: every tool call needed to finish the request, in order. You may include advance_time calls, but you will not see what happens.

Tools you may use:
{tools}

Answer with only a JSON array, for example:
[{{"tool": "pay_bill", "args": {{"bill_id": "b-1"}}}}, {{"tool": "advance_time", "args": {{}}}}]"""

_OBSERVE = ("get_activity", "list_subscriptions", "list_cases")


def _tool_summary():
    lines = []
    for tool in TOOLS:
        if tool["name"] in ("list_documents", "read_document") + _OBSERVE:
            continue
        props = tool["input_schema"].get("properties", {})
        required = set(tool["input_schema"].get("required", []))
        args = ", ".join(f"{k}{'' if k in required else '?'}: {v['type']}" for k, v in props.items())
        lines.append(f"- {tool['name']}({args}): {tool['description']}")
    return "\n".join(lines)


def parse_plan(text: str):
    """The first JSON array in the answer, as a list of {tool, args}; None if there is none."""
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text[text.find("["): text.rfind("]") + 1]
    try:
        plan = json.loads(candidate)
    except ValueError:
        return None
    if not isinstance(plan, list) or not all(isinstance(s, dict) and isinstance(s.get("tool"), str) for s in plan):
        return None
    return [{"tool": s["tool"], "args": s.get("args") or {}} for s in plan]


def oneshot_agent(client, model, system_prompt=DEFAULT_SYSTEM_PROMPT, max_tokens=4096, temperature=None,
                  max_tool_calls=60, log=None, retries=4, sleep=time.sleep):
    """Read everything available at the start, ask for one plan, execute it blind."""
    log = log if log is not None else {}

    def agent(view, call_tool):
        observed = []
        for doc in view["documents"]:
            text, _ = call_tool("read_document", {"document_id": doc["document_id"]})
            observed.append(f"Document {doc['document_id']}:\n{json.loads(text).get('text', text)}")
        for name in _OBSERVE:
            text, _ = call_tool(name, {})
            observed.append(f"{name} result:\n{text}")
        prompt = (first_message(view) + "\n\nEverything you can see now:\n\n" + "\n\n".join(observed) + "\n\n"
                  + ONESHOT_INSTRUCTIONS.format(tools=_tool_summary()))
        started = time.monotonic()
        response, attempts = create_with_retry(
            client, _request(model, max_tokens, system_prompt, [{"role": "user", "content": prompt}], temperature),
            retries, sleep)
        _record(log, response, started, attempts)
        plan = parse_plan("\n".join(b.text for b in response.content if b.type == "text"))
        if plan is None:
            return "invalid_plan"
        log["plan"] = plan
        for step in plan[:max_tool_calls]:
            call_tool(step["tool"], step["args"])
        return "plan_executed" if len(plan) <= max_tool_calls else "tool_budget"

    return agent
