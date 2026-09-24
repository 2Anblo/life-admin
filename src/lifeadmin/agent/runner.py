"""Minimal Anthropic-compatible client-tool loop."""

from lifeadmin.agent.tools import TOOLS, execute_tool
from lifeadmin.simulator.workspace import Workspace


SYSTEM_PROMPT = """You are LifeAdmin. Complete the task using the provided tools.
Read relevant documents before consequential actions. Track dates and balances.
Do not assume a scheduled payment succeeded: advance time and inspect outcomes.
You may stop once the obligations are settled. Future events are only revealed by tools.
"""


def run_agent(client, model: str, task: dict, workspace: Workspace, max_steps: int = 20) -> dict:
    document_ids = list(workspace.documents)
    messages = [{
        "role": "user",
        "content": (
            f"Task: {task['title']}\nGoal: {task['goal']}\n"
            f"Current date: {workspace.today}\nDocument IDs: {document_ids}\n"
            "Use read_document to inspect evidence, then choose and verify actions."
        ),
    }]
    trace = []

    for _ in range(max_steps):
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            final_text = "\n".join(block.text for block in response.content if block.type == "text")
            return {"stop_reason": response.stop_reason, "final_text": final_text, "trace": trace}

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            output = execute_tool(workspace, block.name, block.input)
            trace.append({"tool": block.name, "input": block.input, "output": output})
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": tool_results})

    return {"stop_reason": "max_steps", "final_text": "", "trace": trace}
