# LifeAdmin

LifeAdmin is a proposed stateful agent for simulated personal document and bill management. It will retrieve evidence, track state, plan actions, validate consequential actions, and verify outcomes.

The first benchmark fixture, a small deterministic simulator, an independent checker, and an initial model tool-call loop are implemented. Other benchmark tasks and comparative evaluation are not implemented yet.

## Planned components

- `src/lifeadmin/simulator/`: JSON-backed workspace, tools, and deterministic events.
- `src/lifeadmin/agent/`: reader, state manager, planner/executor, and validator.
- `benchmark/tasks/`: 20 original task fixtures, with initial state, documents, events, horizon, and goals.
- `benchmark/checkers/`: independent final-state success checks.
- `evaluation/`: baseline adapters, runner, and metrics.
- `tests/`: focused tests for simulator and checkers.

The simulator and checkers should remain usable without importing the LifeAdmin agent. Interactive systems will share a common tool interface and evaluation budget.

## First milestone status

1. Complete: JSON fixture for one bill-payment task.
2. Complete for this fixture: state loading, `read_document`, `check_balance`, `schedule_payment`, `pay_bill`, and `advance_time`.
3. Complete for this fixture: deposits before scheduled payments on the same date, plus an independent final-state checker.
4. Complete: successful and failing task paths covered by focused tests.

The current simulator only supports deposit events and bill payments. It does not yet support subscription tools, conflicting notices, user authorization, or late-fee accounting. The agent loop has completed the first task in one live Tinker run; broader reliability has not been evaluated.

See [docs/design.md](docs/design.md) for the proposed behavior and open decisions.

## Development setup

Python 3.11 or newer is required. With `uv` installed, run from the repository root:

```powershell
uv run python run_avoid_late_fees.py
```

Run the first task checks with:

```powershell
uv run python -m unittest discover -s tests -v
```

This demonstration uses a hard-coded payment schedule; it does not run an autonomous agent. There is no full benchmark command yet.

## Run the first task with a model

The agent runner uses Tinker's Anthropic-compatible endpoint. Our default model for day-to-day development is `thinkingmachines/Inkling-Small`. Use the larger `thinkingmachines/Inkling` when investigating tasks that the small model repeatedly fails. For formal comparisons, record the exact model ID and use the same model and settings across the systems being compared.

Set the key and model in your local PowerShell session:

```powershell
$env:TINKER_API_KEY = "your-key"
$env:TINKER_MODEL = "thinkingmachines/Inkling-Small"
uv run python run_agent.py
```

Keep API keys out of the repository. The runner prints each tool call and result, advances the simulator to the task horizon, then uses the independent checker to score the final state. The model sees document IDs and tool results, but not the task's hidden event list or checker.

Each attempt writes a JSON log to `results/runs/`, including failed attempts. Logs contain the model name, API token counts when available, tool calls and results, the final state, and the score. They do not contain the API key. The `results/` directory is ignored by Git.
