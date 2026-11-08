# Evaluation

`run_eval.py` runs agents on the 20 benchmark tasks and saves one JSON record per trial. `summarize.py` turns a results directory into the metrics defined in the benchmark paper (Section "Evaluation Protocol").

## Agents

| Name | What it is |
|---|---|
| `plain_loop` | Adaptive tool-using LLM baseline: each turn the model sees all tool results so far and calls tools (`src/lifeadmin/agent/baselines.py`). |
| `oneshot` | Non-interactive LLM baseline: the harness reads every starting document and account view through the ordinary tools, the model writes one complete plan of tool calls, and the plan runs with no feedback. |
| `lifeadmin` | The LifeAdmin agent (`src/lifeadmin/agent/lifeadmin_agent.py`): Workspace Reader, State Manager, Planner/Executor and Validator. See below. |
| `lifeadmin_no_validator` | Ablation: the same agent with the Validator removed (no action checks, no duplicate block, no completion check). |
| `pipeline` | Fixed-order pipeline: the Reader's ledger, one complete plan, the Validator on each consequential step, then execution, with no observe-and-replan loop. |
| `reference` | Replays each task's hidden reference plan: the upper bound. |
| `P0_noop` … `P7_act_on_everything` | Scripted shortcut strategies (`benchmark/floor_policies.py`): the lower bound. `floors` selects all eight, `scripted` adds `reference`. |

Every agent sees the same first message, the same 18 tools and the same environment. Scripted agents are deterministic and run once per task; LLM agents run `--trials` times (default 3).

### The LifeAdmin agent

One run is retrieve, plan, act, observe, update:

1. **Workspace Reader** (LLM): reads every starting document, and each new one as it arrives, into an evidence ledger: atomic facts with their source, conflicts between sources with the one that governs and why, a todo list derived from the request, and what is ruled out.
2. **State Manager** (code): holds the documents, the ledger, the latest result of each read tool (refreshed after every `advance_time`), bills that arrived later, and a journal of every action and its outcome. Each step the Planner gets this state as one rebuilt message, not the raw conversation. A repeated read with nothing changed since is answered from the state instead of the environment; three such turns in a row count as an attempt to stop.
3. **Planner/Executor** (LLM): one call per step with the 18 shared tools plus an internal `finish` tool, which never reaches the environment.
4. **Validator** (code + LLM): before each consequential action (payments, cancellations, plan changes, refund requests, escalations, case closings, disputes, reminders), code blocks exact repeats of an accepted action and an LLM call checks target, amount, timing, funds and necessity against the evidence. A blocked action is not sent to the environment; the reason goes back to the Planner. An action blocked twice and proposed a third time is let through, so a wrong Validator cannot stall the run. When the Planner stops, the Validator checks the todo list and can send it back up to twice. It uses only what the agent observed.

API calls are logged with their role (`reader`, `planner`, `validator`). The record's `agent_log` holds the final ledger, the number of ledger updates and every Validator decision.

## Running

The scripted bounds need no API key:

```powershell
uv run python evaluation/run_eval.py --agents scripted --out results/eval/scripted
uv run python evaluation/summarize.py results/eval/scripted
```

LLM baselines use Tinker's Anthropic-compatible endpoint. `run_eval.py` reads `TINKER_API_KEY` and `TINKER_MODEL` from the environment or from a `.env` file at the repository root (ignored by Git); the environment wins if both are set:

```powershell
$env:TINKER_API_KEY = "your-key"
uv run python evaluation/run_eval.py --agents plain_loop --tasks avoid_late_fees split_tender_refund --trials 1 --model thinkingmachines/Inkling-Small --out results/eval/pilot
uv run python evaluation/run_eval.py --agents plain_loop oneshot --trials 3 --model thinkingmachines/Inkling-Small --workers 4 --out results/eval/inkling-small
uv run python evaluation/summarize.py results/eval/inkling-small --baseline plain_loop
```

- **Resume:** rerunning the same command keeps finished trials and runs only the missing ones (and any that ended in an infrastructure error).
- **Frozen settings:** model, temperature (default 0), step cap (30 planner turns), token cap per response in step-by-step calls (4096), token cap for one-shot outputs such as a oneshot or pipeline plan and the Reader's ledger (8192), tool-call cap (60) and system prompt are written to `<out>/config.json` with the git commit. A rerun with different settings is refused; use a new `--out`.
- **Failures:** a network, rate-limit or server error is retried with backoff; if it persists, the trial restarts from a fresh state up to twice more and is then reported as `infra_error`, not scored. Malformed tool calls and exhausted budgets are scored as agent failures. An unexpected exception is a harness bug: the record has status `error` with the traceback.

## Records

`<out>/<agent>/<task_id>/trial-<n>.json` holds the verdict (every predicate), stop reason, every tool call with its result, every API call (stop reason, input and output tokens, latency, retries), the model's text, the oneshot plan, and the final snapshot `S_h` with the full action log. These are the source for qualitative examples in the paper. `results/` is ignored by Git.

## Metrics (`summarize.py`)

Written to `summary.json`, `summary.md` and `results_table.tex` (a LaTeX `tabular` for the paper):

- **Success rate:** unweighted mean over tasks of each task's success rate, with a 95% bootstrap confidence interval over tasks.
- **Consistency:** fraction of tasks solved in every trial.
- **Goal completion:** mean fraction of goal predicates met (partial credit).
- **Constraint violations:** fraction of trials with a failed constraint predicate (overdraft, failed payment, premature escalation, wrong dispute...).
- **Rejected calls:** tool calls the environment refused, over all tool calls.
- **Efficiency:** tool calls, API calls, input/output tokens and wall-clock time per trial.
- **Breakdowns** by category and difficulty (`benchmark/task_index.json`), a per-task table, and with `--baseline` a paired bootstrap of each agent's difference from the baseline.
