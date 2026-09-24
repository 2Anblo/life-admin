"""Run the first benchmark task with a fixed example schedule.

This is a simulator demonstration, not the autonomous LifeAdmin agent.
"""

import json
from pathlib import Path

from benchmark.checkers.bill_payment import check_success
from lifeadmin.simulator.workspace import Workspace


TASK_PATH = Path(__file__).parent / "benchmark/tasks/avoid_late_fees_01.json"


def main() -> None:
    task = json.loads(TASK_PATH.read_text(encoding="utf-8"))
    workspace = Workspace(task)

    print(f"Task: {task['title']}")
    print(f"Start: {workspace.today}; balance: ${workspace.check_balance() / 100:.2f}")
    for bill in task["bills"]:
        print(f"  {workspace.read_document(bill['document_id'])}")

    example_schedule = {
        "electric": "2026-09-25",
        "internet": "2026-09-27",
        "water": "2026-09-28",
    }
    for bill_id, payment_date in example_schedule.items():
        workspace.schedule_payment(bill_id, payment_date)
        print(f"Scheduled {bill_id} for {payment_date}")

    while workspace.today < task["horizon"]:
        workspace.advance_time()
        print(f"Date: {workspace.today}; balance: ${workspace.check_balance() / 100:.2f}")

    for payment in workspace.payments:
        print(f"Paid {payment['bill_id']} ${payment['amount_cents'] / 100:.2f} on {payment['date']}")
    print(f"Task success: {check_success(task, workspace.result())}")


if __name__ == "__main__":
    main()
