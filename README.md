# LifeAdmin

LifeAdmin is a proposed stateful agent for simulated personal document and bill management. It will retrieve evidence, track state, plan actions, validate consequential actions, and verify outcomes.

This repository is an initial project scaffold. The simulator, agent, benchmark tasks, checkers, and evaluation results are not implemented yet.

## Planned components

- `src/lifeadmin/simulator/`: JSON-backed workspace, tools, and deterministic events.
- `src/lifeadmin/agent/`: reader, state manager, planner/executor, and validator.
- `benchmark/tasks/`: 20 original task fixtures, with initial state, documents, events, horizon, and goals.
- `benchmark/checkers/`: independent final-state success checks.
- `evaluation/`: baseline adapters, runner, and metrics.
- `tests/`: focused tests for simulator and checkers.

The simulator and checkers should remain usable without importing the LifeAdmin agent. Interactive systems will share a common tool interface and evaluation budget.

## First milestone

1. Define the JSON format for one complete bill-payment task.
2. Implement state loading, `read_document`, `check_balance`, `schedule_payment`, `pay_bill`, and `advance_time`.
3. Implement deterministic event ordering and one independent success checker.
4. Run the first task end-to-end before expanding to 20 distinct tasks.

See [docs/design.md](docs/design.md) for the proposed behavior and open decisions.

## Development setup

Python 3.11 or newer is recommended. From the repository root:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

No runnable agent or benchmark command is provided yet.
