"""One tool set for every task on the LifeAdmin workspace (v3).

The v2 payment tools keep their names and behaviour; ``advance_time`` gains an
optional ``until`` date. Every task gets the same list, so the tools on offer
never hint at what a task is about. Argument types are checked here, so a
malformed call becomes a scored rejection returned to the agent.
"""

import json
from copy import deepcopy

from lifeadmin.agent.tools_v2 import TOOLS_V2
from lifeadmin.simulator.workspace_v2 import AgentInputError
from lifeadmin.simulator.workspace_v3 import CASE_RESOLUTIONS, DISPUTE_REASONS, LifeWorkspace

_NO_ARGS = {"type": "object", "properties": {}}

_ADVANCE = {
    "name": "advance_time",
    "description": "Move to the next point when something happens: a deposit, a new document or bill, a card posting, "
                   "a subscription renewal, one of the pending scheduled payments, or the end of the evaluation period. "
                   "Give until (YYYY-MM-DD) to stop no later than that date. The clock never stops at due dates.",
    "input_schema": {"type": "object", "properties": {"until": {"type": "string", "description": "Date in YYYY-MM-DD format"}}},
}

NEW_TOOLS = [
    {
        "name": "list_subscriptions",
        "description": "List the user's subscriptions with their status, current plan, available plans and prices.",
        "input_schema": _NO_ARGS,
    },
    {
        "name": "get_usage",
        "description": "Get the usage records the service keeps for one subscription.",
        "input_schema": {"type": "object", "properties": {"subscription_id": {"type": "string"}},
                         "required": ["subscription_id"]},
    },
    {
        "name": "cancel_subscription",
        "description": "Cancel a subscription. A renewal still charges if the cancellation comes later than the service's notice period allows.",
        "input_schema": {"type": "object", "properties": {"subscription_id": {"type": "string"}},
                         "required": ["subscription_id"]},
    },
    {
        "name": "change_plan",
        "description": "Switch a subscription to another of its plans. The new plan applies from the next renewal.",
        "input_schema": {"type": "object", "properties": {"subscription_id": {"type": "string"}, "plan": {"type": "string"}},
                         "required": ["subscription_id", "plan"]},
    },
    {
        "name": "list_cases",
        "description": "List the user's open and closed refund cases with merchants.",
        "input_schema": _NO_ARGS,
    },
    {
        "name": "request_refund",
        "description": "Submit a refund request for items of an order. allocations says where the money goes, as a list of "
                       "{account, amount_cents}; without it the refund goes to the merchant's default destination. "
                       "The merchant reviews the request afterwards and posts any approved refund.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "items": {"type": "array", "items": {"type": "string"}},
                "amount_cents": {"type": "integer"},
                "allocations": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"account": {"type": "string"}, "amount_cents": {"type": "integer"}},
                    "required": ["account", "amount_cents"]}},
            },
            "required": ["order_id", "items"],
        },
    },
    {
        "name": "escalate_case",
        "description": "Escalate an open refund case to the merchant, with an optional note.",
        "input_schema": {"type": "object", "properties": {"case_id": {"type": "string"}, "note": {"type": "string"}},
                         "required": ["case_id"]},
    },
    {
        "name": "close_case",
        "description": f"Close a refund case. resolution is one of: {', '.join(CASE_RESOLUTIONS)}. "
                       "matched_transaction_id optionally names the card transaction that settled it.",
        "input_schema": {"type": "object", "properties": {
            "case_id": {"type": "string"}, "resolution": {"type": "string"},
            "matched_transaction_id": {"type": "string"}}, "required": ["case_id", "resolution"]},
    },
    {
        "name": "open_dispute",
        "description": f"Dispute one card transaction with the card issuer. reason is one of: {', '.join(DISPUTE_REASONS)}.",
        "input_schema": {"type": "object", "properties": {"transaction_id": {"type": "string"}, "reason": {"type": "string"}},
                         "required": ["transaction_id", "reason"]},
    },
    {
        "name": "create_reminder",
        "description": "Create a reminder for the user on a future date (YYYY-MM-DD).",
        "input_schema": {"type": "object", "properties": {"date": {"type": "string"}, "text": {"type": "string"}},
                         "required": ["date", "text"]},
    },
]

TOOLS = [deepcopy(t) for t in TOOLS_V2 if t["name"] != "advance_time"] + NEW_TOOLS + [_ADVANCE]

_SCHEMAS = {tool["name"]: tool["input_schema"] for tool in TOOLS}
_TYPES = {"string": str, "integer": int, "array": list, "object": dict}
_ARTICLE = {"string": "a string", "integer": "an integer", "array": "a list", "object": "an object"}


def _check_arguments(workspace: LifeWorkspace, name: str, arguments) -> dict:
    if name not in _SCHEMAS:
        workspace.reject(name, arguments if isinstance(arguments, dict) else {}, f"Unknown tool: {name}")
    if not isinstance(arguments, dict):
        workspace.reject(name, {}, "Arguments must be a JSON object")
    schema = _SCHEMAS[name]
    properties = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in arguments:
            workspace.reject(name, arguments, f"Missing argument: {key}")
    for key, value in arguments.items():
        if key not in properties:
            workspace.reject(name, arguments, f"Unexpected argument: {key}")
        expected = properties[key]["type"]
        if not isinstance(value, _TYPES[expected]) or isinstance(value, bool):
            workspace.reject(name, arguments, f"{key} must be {_ARTICLE[expected]}")
    return arguments


def execute_tool(workspace: LifeWorkspace, name: str, arguments) -> tuple[str, bool]:
    """Run one tool call. Returns (JSON text for the agent, is_error)."""
    try:
        args = _check_arguments(workspace, name, arguments)
        if name == "list_documents":
            result = {"documents": workspace.list_documents()}
        elif name == "read_document":
            text = workspace.read_document(args["document_id"])
            result = {"document_id": args["document_id"],
                      "title": workspace.documents[args["document_id"]].get("title", ""), "text": text}
        elif name == "check_balance":
            result = workspace.check_balance()
        elif name == "get_activity":
            result = workspace.get_activity()
        elif name == "pay_bill":
            result = workspace.pay_bill(args["bill_id"], args.get("amount_cents"))
        elif name == "schedule_payment":
            result = workspace.schedule_payment(args["bill_id"], args["payment_date"], args.get("amount_cents"))
        elif name == "cancel_scheduled_payment":
            result = workspace.cancel_scheduled_payment(args.get("instruction_id"), args.get("bill_id"))
        elif name == "list_subscriptions":
            result = {"subscriptions": workspace.list_subscriptions()}
        elif name == "get_usage":
            result = workspace.get_usage(args["subscription_id"])
        elif name == "cancel_subscription":
            result = workspace.cancel_subscription(args["subscription_id"])
        elif name == "change_plan":
            result = workspace.change_plan(args["subscription_id"], args["plan"])
        elif name == "list_cases":
            result = {"cases": workspace.list_cases()}
        elif name == "request_refund":
            result = workspace.request_refund(args["order_id"], args["items"], args.get("amount_cents"),
                                              args.get("allocations"))
        elif name == "escalate_case":
            result = workspace.escalate_case(args["case_id"], args.get("note"))
        elif name == "close_case":
            result = workspace.close_case(args["case_id"], args["resolution"], args.get("matched_transaction_id"))
        elif name == "open_dispute":
            result = workspace.open_dispute(args["transaction_id"], args["reason"])
        elif name == "create_reminder":
            result = workspace.create_reminder(args["date"], args["text"])
        else:
            result = workspace.advance_time(args.get("until"))
    except AgentInputError as error:
        return json.dumps({"error": str(error)}), True
    return json.dumps(result), False
