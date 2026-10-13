"""Tool definitions and dispatch for v2 tasks (shared by every agent and baseline).

Descriptions are task-neutral and identical for every v2 task. Argument types
are checked here, so a malformed call becomes a scored rejection returned to
the agent instead of a crash.
"""

import json

from lifeadmin.simulator.workspace_v2 import AgentInputError, WorkspaceV2

_NO_ARGS = {"type": "object", "properties": {}}

TOOLS_V2 = [
    {
        "name": "list_documents",
        "description": "List the documents available so far, with their IDs. New documents can arrive as time passes.",
        "input_schema": _NO_ARGS,
    },
    {
        "name": "read_document",
        "description": "Read a document by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {"document_id": {"type": "string"}},
            "required": ["document_id"],
        },
    },
    {
        "name": "check_balance",
        "description": "Get today's date and the available balance in cents.",
        "input_schema": _NO_ARGS,
    },
    {
        "name": "get_activity",
        "description": "Get today's date, the balance, past payments, failed payments, and pending scheduled payments with their dates, amounts, and who set them up.",
        "input_schema": _NO_ARGS,
    },
    {
        "name": "pay_bill",
        "description": "Pay a bill record now from the available balance. Use a bill ID from the task message. amount_cents is optional and defaults to the amount shown on the record. A payment larger than the balance is refused.",
        "input_schema": {
            "type": "object",
            "properties": {"bill_id": {"type": "string"}, "amount_cents": {"type": "integer"}},
            "required": ["bill_id"],
        },
    },
    {
        "name": "schedule_payment",
        "description": "Schedule a payment to a bill record on a future date (YYYY-MM-DD, after today and no later than the end of the evaluation period). The amount is fixed when you schedule it and defaults to the amount shown on the record; funds are checked when the payment runs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bill_id": {"type": "string"},
                "payment_date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                "amount_cents": {"type": "integer"},
            },
            "required": ["bill_id", "payment_date"],
        },
    },
    {
        "name": "cancel_scheduled_payment",
        "description": "Cancel pending scheduled payments that you set up: one by its instruction_id, or all of yours for a bill by bill_id. Payments the biller set up cannot be cancelled here.",
        "input_schema": {
            "type": "object",
            "properties": {"instruction_id": {"type": "string"}, "bill_id": {"type": "string"}},
        },
    },
    {
        "name": "advance_time",
        "description": "Move to the next point when something happens: a deposit, a new document, one of the pending scheduled payments, or the end of the evaluation period. The clock never stops at due dates.",
        "input_schema": _NO_ARGS,
    },
]

_SCHEMAS = {tool["name"]: tool["input_schema"] for tool in TOOLS_V2}
_TYPES = {"string": str, "integer": int}
_ARTICLE = {"string": "a string", "integer": "an integer"}


def _check_arguments(workspace: WorkspaceV2, name: str, arguments) -> dict:
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
        expected = _TYPES[properties[key]["type"]]
        if not isinstance(value, expected) or isinstance(value, bool):
            workspace.reject(name, arguments, f"{key} must be {_ARTICLE[properties[key]['type']]}")
    return arguments


def execute_tool_v2(workspace: WorkspaceV2, name: str, arguments) -> tuple[str, bool]:
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
        else:
            result = workspace.advance_time()
    except AgentInputError as error:
        return json.dumps({"error": str(error)}), True
    return json.dumps(result), False
