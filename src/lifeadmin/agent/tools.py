"""Tool definitions and dispatch for the first LifeAdmin task."""

import json

from lifeadmin.simulator.workspace import Workspace


TOOLS = [
    {
        "name": "read_document",
        "description": "Read a document by its ID. Use this to check bill amounts, deadlines, and notices.",
        "input_schema": {
            "type": "object",
            "properties": {"document_id": {"type": "string"}},
            "required": ["document_id"],
        },
    },
    {
        "name": "check_balance",
        "description": "Get the current date and available account balance in cents.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "schedule_payment",
        "description": "Schedule a bill payment for a future date. Funds are checked when the payment executes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bill_id": {"type": "string"},
                "payment_date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
            },
            "required": ["bill_id", "payment_date"],
        },
    },
    {
        "name": "pay_bill",
        "description": "Pay a bill immediately from the current balance.",
        "input_schema": {
            "type": "object",
            "properties": {"bill_id": {"type": "string"}},
            "required": ["bill_id"],
        },
    },
    {
        "name": "advance_time",
        "description": "Advance to the next event or scheduled payment and observe the new state.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def execute_tool(workspace: Workspace, name: str, arguments: dict) -> str:
    if name == "read_document":
        result = {"document_id": arguments["document_id"], "text": workspace.read_document(arguments["document_id"])}
    elif name == "check_balance":
        result = {"date": workspace.today, "balance_cents": workspace.check_balance()}
    elif name == "schedule_payment":
        workspace.schedule_payment(arguments["bill_id"], arguments["payment_date"])
        result = {"scheduled": arguments["bill_id"], "date": arguments["payment_date"]}
    elif name == "pay_bill":
        workspace.pay_bill(arguments["bill_id"])
        result = {"paid": arguments["bill_id"], "date": workspace.today, "balance_cents": workspace.check_balance()}
    elif name == "advance_time":
        workspace.advance_time()
        result = {"date": workspace.today, "balance_cents": workspace.check_balance(), "payments": workspace.payments}
    else:
        raise ValueError(f"Unknown tool: {name}")
    return json.dumps(result)
