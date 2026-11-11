# LifeAdmin

LifeAdmin is a benchmark and agent for personal administration: paying bills around deposits, managing subscriptions, refunds, disputes and reminders, using documents that may conflict or arrive over time.

## Status

- **Benchmark:** 20 tasks, each with documents, a deterministic event timeline, hidden expected outcomes, an independent checker and a reference plan (`benchmark/`). All 20 run end to end in the simulator, and every task is checked against eight scripted shortcut strategies.
- **Simulator:** `src/lifeadmin/simulator/workspace_v3.py` (bills, scheduled payments, subscriptions, refund cases, disputes, reminders), with one shared set of 18 tools (`src/lifeadmin/agent/tools_v3.py`).
- **Evaluation:** a common runner and metrics (`evaluation/`), LLM baselines (a plain tool-calling loop and a non-interactive planner), and scripted upper and lower bounds.
- **Agent:** the LifeAdmin agent (`src/lifeadmin/agent/lifeadmin_agent.py`): a Workspace Reader that builds an evidence ledger, a State Manager, a Planner/Executor and a Validator that checks consequential actions before they run, plus a no-validator ablation and a fixed-order pipeline. See [evaluation/README.md](evaluation/README.md#the-lifeadmin-agent).

See [benchmark/README.md](benchmark/README.md) for the task format and validation, and [evaluation/README.md](evaluation/README.md) for running agents and reading results.

## Setup

Python 3.11 or newer. With `uv` installed, from the repository root:

```powershell
uv run python -m unittest discover -s tests
uv run python run_task.py benchmark/tasks/split_tender_refund.json
uv run python evaluation/run_eval.py --agents scripted --out results/eval/scripted
```

`run_task.py` runs the reference plan and every shortcut strategy on one task. `evaluation/run_eval.py` runs any agents on any tasks; see the evaluation README.

## Models

LLM agents use Tinker's Anthropic-compatible endpoint. Our default development model is `thinkingmachines/Inkling-Small`; use `thinkingmachines/Inkling` to investigate tasks the small model repeatedly fails. For formal comparisons, record the exact model id and keep the same model and settings across all compared systems (the runner freezes them in `config.json`).

```powershell
$env:TINKER_API_KEY = "your-key"
$env:TINKER_MODEL = "thinkingmachines/Inkling-Small"
```

Keep API keys out of the repository. Run records go to `results/`, which Git ignores; they never contain the key.

## Legacy

`benchmark/legacy/avoid_late_fees_v1.json` is the first prototype task on the v1 simulator (`src/lifeadmin/simulator/workspace.py`), run by `run_avoid_late_fees.py` (hard-coded schedule) and `run_agent.py` (first model loop). The benchmark version of the task is `benchmark/tasks/avoid_late_fees.json`.
