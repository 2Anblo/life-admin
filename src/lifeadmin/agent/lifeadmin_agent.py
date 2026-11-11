"""The LifeAdmin agent: Workspace Reader, State Manager, Planner/Executor and Validator.

The loop is retrieve, plan, act, observe, update:

- **Workspace Reader** (LLM): reads every document available at the start, and
  each new document as it arrives, into an evidence ledger: atomic facts with
  their source, conflicts between sources with the one that governs and why, a
  todo list derived from the user's request, and things that are ruled out.
- **State Manager** (code): keeps the documents, the ledger, the latest result of
  every read tool, bills that arrived later, and a journal of every action and
  its outcome. Each step the Planner gets this state rebuilt as one compact
  message instead of the raw conversation, so earlier tool output that has been
  superseded never competes with the current view.
- **Planner/Executor** (LLM): one call per step with the shared tools; it writes
  a short status against the todo list and calls the next tools.
- **Validator** (code + LLM): checks each consequential action immediately
  before it runs. An exact repeat of an accepted action is blocked by code; the
  LLM check covers target, amount, timing, funds and necessity against the
  ledger. A block is returned to the Planner with the reason, and the action is
  not sent to the environment. When the Planner wants to stop, the Validator
  checks the todo list and can send it back (at most ``max_stop_checks`` times).
  It uses only what the agent has observed, never hidden checker data.

``validator=False`` gives the no-validator ablation (same agent, no checks).
``pipeline_agent`` is the fixed-order pipeline: Reader, one plan, Validator,
execution, with no observe-and-replan loop.
"""

import json
import re
import time

from lifeadmin.agent.baselines import _record, _request, _tool_summary, create_with_retry, parse_plan
from lifeadmin.agent.tools_v3 import TOOLS
from lifeadmin.harness import DEFAULT_SYSTEM_PROMPT, first_message

CONSEQUENTIAL = {"pay_bill", "schedule_payment", "cancel_scheduled_payment", "cancel_subscription", "change_plan",
                 "request_refund", "escalate_case", "close_case", "open_dispute", "create_reminder"}
OBSERVERS = ("get_activity", "list_subscriptions", "list_cases")
RESULT_CLIP = 900
MAX_IDLE_TURNS = 3  # planner turns in a row whose reads were all skipped as unchanged

# Internal to the agent: never sent to the environment. Ends the run through the Validator's completion check.
FINISH_TOOL = {
    "name": "finish",
    "description": "End the task. Call it when every todo item is complete (a scheduled payment that will run on its "
                   "date counts as done) or nothing more can be done. summary: what was done and anything left open.",
    "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
}
PLANNER_TOOLS = TOOLS + [FINISH_TOOL]

READER_SYSTEM = ("You are the Workspace Reader of a personal-administration agent. You turn the user's documents and "
                 "account views into an evidence ledger for the planner. You never act.")

READER_INSTRUCTIONS = """Write the evidence ledger as one JSON object:
{"facts": [{"id": "F1", "source": "<document id or account view>", "issued": "<date the document was sent, or null>", "statement": "<one fact, with exact amounts, dates and ids>"}],
 "conflicts": [{"facts": ["F2", "F5"], "use": "F5", "why": "<why this source governs>"}],
 "todo": [{"id": "T1", "task": "<one thing that must happen for the user's request, with ids and amounts>", "when": "<the earliest and latest allowed date, or what must be observed first>", "sources": ["F1"]}],
 "do_not": ["<anything the user's request or the documents rule out>"]}

Rules:
- Record every amount, date, deadline, window, notice period, plan, price, id and condition that could matter. Keep amounts as written.
- The user's request is a source too (source "request"): record what it says about what to do, what not to touch, and how and when to act.
- When sources disagree, decide which one governs from what the sources themselves say (dates, who issued it, whether one replaces another or says it does not apply), and say why.
- In each todo item's "when", say both when the action must be taken (now, or only after something is observed) and the date it must take effect, if they differ.
- The todo list covers the whole request, including follow-ups that depend on later events (money that must arrive first, a refund that must post before a case is closed). Leave out anything the request does not need.
Answer with only the JSON object."""

PLANNER_GUIDE = """You are the Planner/Executor of the LifeAdmin agent. Every turn you get the whole current state: the task, every document, an evidence ledger built from them, the latest result of each account view, and every action so far with its outcome.
- Work through the todo list and call the tools for the next step. The user's request governs: if the ledger seems to contradict it, follow the request.
- The account views are refreshed after every advance_time; read again only when you need something they do not show.
- When a tool lets you name the evidence or destination (the transaction that settled a case, an account), give it.
- Respect timing: never act before an allowed date or after a deadline. To wait, schedule the payment or use advance_time with until.
- Before paying or scheduling a payment, make sure the balance on that date covers it after every other pending payment.
- Consequential actions (payments, cancellations, plan changes, refund requests, escalations, closing cases, disputes, reminders) are checked by a Validator before they run. If one is blocked, use the reason to fix the action or the plan.
- When every todo item is complete, call finish. A scheduled payment that will run on its date counts as done."""

VALIDATOR_SYSTEM = ("You are the Validator of the LifeAdmin agent. You check a proposed action just before it runs, using "
                    "only the task, the documents, the evidence ledger, the account views and the actions so far. "
                    "Approve unless you find a concrete problem in the evidence.")

ACTION_CHECK = """## Proposed action
{tool} {args}
Planner's status: {notes}

Check the checks that apply, against the evidence:
1. Target: the right bill, subscription, plan, order, items, case, transaction, account or date, as the governing source says.
   Where the tool accepts it, the evidence or destination (matching transaction, account) is given and correct.
2. Amount: exactly what is owed or due, from the governing source; not more and not less.
3. Timing: today ({today}) or the payment date falls inside every window that applies: not before an allowed date, not after a deadline or notice period.
4. Money: the checking balance on that date covers it, counting known deposits before then and every other pending payment.
5. Necessity: the user's request needs it, nothing rules it out, and it does not repeat something already done or pending.
Answer with only JSON: {{"verdict": "approve" or "reject", "problem": "<if reject: the concrete problem, and what would be right>"}}"""

STOP_CHECK = """## The Planner wants to stop
Planner's status: {notes}

Check every todo item in the ledger against the actions so far and the account views. An item is complete when its action was accepted, or when a scheduled payment set up for it will run on its date. It is not complete if an action for it was refused or blocked and not redone, or if it waits for something (money arriving, a refund posting, a document) that has not been observed yet and can still happen before {horizon}: then advance_time is needed.
Answer with only JSON: {{"complete": true or false, "missing": "<if false: what remains and the next step>"}}"""

PIPELINE_INSTRUCTIONS = """You will not see any tool results after this point. Using the evidence ledger, write the complete plan: every tool call needed to finish the request, in order. You may include advance_time calls, but you will not see what happens. Each consequential call is checked by a Validator before it runs; a blocked call is skipped.

Tools you may use:
{tools}

Answer with only a JSON array, for example:
[{{"tool": "pay_bill", "args": {{"bill_id": "b-1"}}}}, {{"tool": "advance_time", "args": {{}}}}]"""


def parse_object(text: str):
    """The first JSON object in the answer, or None."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    try:
        value = json.loads(candidate)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _text(response) -> str:
    return "\n".join(block.text for block in response.content if block.type == "text").strip()


def _clip(text: str, limit: int = RESULT_CLIP) -> str:
    return text if len(text) <= limit else text[:limit] + f"... [{len(text) - limit} more characters]"


def _key(tool, args) -> str:
    return f"{tool} {json.dumps(args, sort_keys=True)}"


class State:
    """What the agent knows: documents, ledger, latest account views, later bills and the action journal."""

    def __init__(self, view):
        self.view = view
        self.today = view["start_date"]
        self.documents = {}
        self.new_bills = []
        self.ledger = None
        self.ledger_text = ""
        self.observations = {}
        self.journal = []
        self.accepted = {}
        self.notes = ""
        self.version = 0  # bumped by every accepted action and every advance of the clock

    def observe(self, tool, args, text):
        if tool == "read_document":
            return
        name = tool if not args else f"{tool} {json.dumps(args, sort_keys=True)}"
        self.observations[name] = {"as_of": self.today, "result": text, "version": self.version}

    def add_document(self, text):
        doc = json.loads(text)
        if doc["document_id"] not in self.documents:
            arrived = None if self.today == self.view["start_date"] else self.today
            self.documents[doc["document_id"]] = {"title": doc.get("title", ""), "text": doc.get("text", ""),
                                                  "arrived": arrived}

    def render(self) -> str:
        parts = [first_message(self.view), f"Current date: {self.today}."]
        if self.new_bills:
            parts.append("## Bills that arrived later\n" + "\n".join(
                f"- {b['bill_id']}: {b['payee']}, ${b['amount_cents'] / 100:.2f} due {b['due_date']}" for b in self.new_bills))
        parts.append("## Documents\n" + "\n\n".join(
            f"### {doc_id}: {doc['title']} ({'available at the start' if doc['arrived'] is None else 'arrived ' + doc['arrived']})\n"
            f"{doc['text']}" for doc_id, doc in self.documents.items()))
        ledger = json.dumps(self.ledger, indent=1, ensure_ascii=False) if self.ledger else self.ledger_text
        parts.append("## Evidence ledger (from the Workspace Reader)\n" + (ledger or "(empty)"))
        if self.observations:
            parts.append("## Latest account views\n" + "\n".join(
                f"### {name} (as of {o['as_of']})\n{o['result']}" for name, o in self.observations.items()))
        parts.append("## Actions so far\n" + ("\n".join(
            f"{n}. [{e['date']}] {e['tool']} {json.dumps(e['args'], ensure_ascii=False)} -> {e['outcome']}: {e['result']}"
            for n, e in enumerate(self.journal, 1)) or "(none)"))
        return "\n\n".join(parts)


class LifeAdminCore:
    """Shared modules for the loop agent and the fixed pipeline."""

    def __init__(self, client, model, system_prompt, max_tokens, temperature, max_tool_calls, log, retries, sleep,
                 validator, reader_max_tokens=8192, validator_max_tokens=None):
        self.client, self.model, self.system_prompt = client, model, system_prompt
        self.max_tokens, self.temperature, self.max_tool_calls = max_tokens, temperature, max_tool_calls
        self.log, self.retries, self.sleep, self.validator = log, retries, sleep, validator
        self.reader_max_tokens, self.validator_max_tokens = reader_max_tokens, validator_max_tokens or max_tokens
        self.tool_calls = 0
        self.blocks = {}

    # ---- plumbing ----------------------------------------------------------

    def ask(self, role, system, content, max_tokens, tools=None):
        started = time.monotonic()
        response, attempts = create_with_retry(
            self.client, _request(self.model, max_tokens, system, [{"role": "user", "content": content}],
                                  self.temperature, tools), self.retries, self.sleep)
        _record(self.log, response, started, attempts, role=role)
        return response

    def budget_left(self) -> bool:
        return self.tool_calls < self.max_tool_calls

    def call(self, call_tool, state, tool, args):
        self.tool_calls += 1
        text, is_error = call_tool(tool, args)
        if not is_error and tool not in CONSEQUENTIAL and tool != "advance_time":
            if tool == "read_document":
                state.add_document(text)
            else:
                state.observe(tool, args, text)
        return text, is_error

    # ---- Workspace Reader ----------------------------------------------------

    def read_workspace(self, view, call_tool, state):
        for doc in view["documents"]:
            if self.budget_left():
                self.call(call_tool, state, "read_document", {"document_id": doc["document_id"]})
        for tool in OBSERVERS:
            if self.budget_left():
                self.call(call_tool, state, tool, {})
        self.update_ledger(state)

    def update_ledger(self, state, new_ids=()):
        previous = ""
        if state.ledger or state.ledger_text:
            previous = ("\n\n## Ledger so far\nUpdate it with the new documents " + ", ".join(new_ids)
                        + ": keep existing ids, add facts, and revise conflicts and the todo list.\n"
                        + (json.dumps(state.ledger, ensure_ascii=False) if state.ledger else state.ledger_text))
        response = self.ask("reader", READER_SYSTEM, state.render() + previous + "\n\n" + READER_INSTRUCTIONS,
                            self.reader_max_tokens)
        text = _text(response)
        ledger = parse_object(text)
        state.ledger, state.ledger_text = (ledger, "") if ledger else (state.ledger, text)
        self.log["ledger"] = state.ledger or state.ledger_text
        self.log["ledger_updates"] = self.log.get("ledger_updates", 0) + 1

    # ---- Validator -------------------------------------------------------------

    def _decision(self, state, kind, tool, args, verdict, problem=""):
        self.log.setdefault("validator", []).append(
            {"date": state.today, "kind": kind, "tool": tool, "args": args, "verdict": verdict, "problem": problem})

    def check_action(self, state, tool, args):
        """Returns None to allow the action, or the reason it is blocked."""
        if not self.validator:
            return None
        key = _key(tool, args)
        if key in state.accepted:
            reason = f"Exact repeat of an action already accepted on {state.accepted[key]}."
            self._decision(state, "duplicate", tool, args, "reject", reason)
            return reason
        if self.blocks.get(key, 0) >= 2:
            self._decision(state, "override", tool, args, "approve", "proposed again after two blocks")
            return None
        response = self.ask("validator", VALIDATOR_SYSTEM, state.render() + "\n\n" + ACTION_CHECK.format(
            tool=tool, args=json.dumps(args, ensure_ascii=False), notes=state.notes or "(none)", today=state.today),
            self.validator_max_tokens)
        answer = parse_object(_text(response)) or {}
        if str(answer.get("verdict", "approve")).lower().startswith("reject"):
            problem = str(answer.get("problem") or "no reason given")
            self.blocks[key] = self.blocks.get(key, 0) + 1
            self._decision(state, "action", tool, args, "reject", problem)
            return problem
        self._decision(state, "action", tool, args, "approve" if answer else "approve_unparsed")
        return None

    def check_stop(self, state):
        """Returns None if the work is complete, or what is missing."""
        response = self.ask("validator", VALIDATOR_SYSTEM, state.render() + "\n\n" + STOP_CHECK.format(
            notes=state.notes or "(none)", horizon=state.view["horizon"]), self.validator_max_tokens)
        answer = parse_object(_text(response)) or {}
        complete = answer.get("complete", True)
        if complete is False or str(complete).lower() == "false":
            missing = str(answer.get("missing") or "some todo items are not complete")
            self._decision(state, "stop", None, None, "reject", missing)
            return missing
        self._decision(state, "stop", None, None, "approve" if answer else "approve_unparsed")
        return None

    # ---- acting and observing ---------------------------------------------------

    def execute(self, call_tool, state, tool, args):
        """Validate, run and record one tool call. Returns False if the planner should replan now."""
        date = state.today
        unchanged = self.unchanged_read(state, tool, args)
        if unchanged:
            state.journal.append({"date": date, "tool": tool, "args": args, "outcome": "not repeated",
                                  "result": unchanged})
            return True
        if tool in CONSEQUENTIAL:
            problem = self.check_action(state, tool, args)
            if problem:
                state.journal.append({"date": date, "tool": tool, "args": args, "outcome": "BLOCKED by the Validator",
                                      "result": problem})
                return False
        text, is_error = self.call(call_tool, state, tool, args)
        if tool == "advance_time" and not is_error:
            self.after_advance(call_tool, state, text)
        if not is_error and (tool in CONSEQUENTIAL or tool == "advance_time"):
            state.version += 1
        if tool in CONSEQUENTIAL and not is_error:
            state.accepted[_key(tool, args)] = date
        if is_error or tool in CONSEQUENTIAL or tool == "advance_time":
            outcome, shown = ("refused" if is_error else "accepted"), _clip(text)
        else:  # reads: the result lives in Documents or Latest account views
            outcome, shown = "read", "see Documents" if tool == "read_document" else "see Latest account views"
        state.journal.append({"date": date, "tool": tool, "args": args, "outcome": outcome, "result": shown})
        return not (is_error and tool in CONSEQUENTIAL)

    @staticmethod
    def unchanged_read(state, tool, args):
        """The State Manager answers a repeated read itself when nothing has changed since the last one."""
        if tool in CONSEQUENTIAL or tool == "advance_time" or not isinstance(args, dict):
            return None
        if tool == "read_document":
            if args.get("document_id") in state.documents:
                return "Already read; the text is under Documents."
            return None
        name = tool if not args else f"{tool} {json.dumps(args, sort_keys=True)}"
        seen = state.observations.get(name)
        if seen and seen["version"] == state.version:
            return (f"Nothing has changed since this was read on {seen['as_of']}; the result is under Latest account "
                    "views. If every todo item is done, call finish.")
        return None

    def after_advance(self, call_tool, state, text):
        result = json.loads(text)
        state.today = result.get("date", state.today)
        state.new_bills += result.get("new_bills", [])
        new_ids = [d for d in result.get("new_documents", []) if d not in state.documents]
        for doc_id in new_ids:
            if self.budget_left():
                self.call(call_tool, state, "read_document", {"document_id": doc_id})
        for tool in OBSERVERS:
            if self.budget_left() and (tool == "get_activity" or json.loads(state.observations.get(tool, {}).get(
                    "result", "{}")).get(tool.removeprefix("list_"))):
                self.call(call_tool, state, tool, {})
        if new_ids:
            self.update_ledger(state, new_ids)


def lifeadmin_agent(client, model, system_prompt=DEFAULT_SYSTEM_PROMPT, max_steps=30, max_tokens=4096,
                    temperature=None, max_tool_calls=60, log=None, retries=4, sleep=time.sleep, validator=True,
                    max_stop_checks=2, long_max_tokens=8192):
    """The full agent (``validator=False``: the no-validator ablation)."""
    log = log if log is not None else {}

    def agent(view, call_tool):
        core = LifeAdminCore(client, model, system_prompt, max_tokens, temperature, max_tool_calls, log, retries,
                             sleep, validator, reader_max_tokens=long_max_tokens)
        state = State(view)
        core.read_workspace(view, call_tool, state)
        stop_checks, idle = 0, 0
        planner_system = system_prompt + "\n\n" + PLANNER_GUIDE
        for _ in range(max_steps):
            response = core.ask("planner", planner_system, state.render(), max_tokens, PLANNER_TOOLS)
            calls = [b for b in response.content if b.type == "tool_use"]
            finish = next((b for b in calls if b.name == "finish"), None)
            summary = finish.input.get("summary", "") if finish and isinstance(finish.input, dict) else ""
            state.notes = _text(response) or str(summary)
            if finish or not calls or idle >= MAX_IDLE_TURNS:
                idle = 0
                if validator and stop_checks < max_stop_checks:
                    stop_checks += 1
                    missing = core.check_stop(state)
                    if missing:
                        state.journal.append({"date": state.today, "tool": "(stop)", "args": {},
                                              "outcome": "NOT FINISHED, says the Validator", "result": missing})
                        continue
                return "finish" if finish else ("idle" if calls else response.stop_reason)
            journal_before = len(state.journal)
            for block in calls:
                if not core.budget_left():
                    return "tool_budget"
                if not core.execute(call_tool, state, block.name, block.input):
                    break
            new = state.journal[journal_before:]
            idle = idle + 1 if new and all(e["outcome"] == "not repeated" for e in new) else 0
        return "max_steps"

    return agent


def pipeline_agent(client, model, system_prompt=DEFAULT_SYSTEM_PROMPT, max_tokens=8192, temperature=None,
                   max_tool_calls=60, log=None, retries=4, sleep=time.sleep):
    """Fixed-order pipeline: Reader, one complete plan, Validator on each consequential step, execution. No replanning."""
    log = log if log is not None else {}

    def agent(view, call_tool):
        core = LifeAdminCore(client, model, system_prompt, max_tokens, temperature, max_tool_calls, log, retries,
                             sleep, validator=True, reader_max_tokens=max_tokens)
        state = State(view)
        core.read_workspace(view, call_tool, state)
        response = core.ask("planner", system_prompt, state.render() + "\n\n"
                            + PIPELINE_INSTRUCTIONS.format(tools=_tool_summary()), max_tokens)
        plan = parse_plan(_text(response))
        if plan is None:
            return "invalid_plan"
        log["plan"] = plan
        for step in plan:
            if not core.budget_left():
                return "tool_budget"
            core.execute(call_tool, state, step["tool"], step["args"])
        return "plan_executed"

    return agent
