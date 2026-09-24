# Design outline

This document records the planned behavior from the project proposal. It is not an implementation status report.

## Simulator and task contract

Each task will define an initial workspace, synthetic documents, available tools, deterministic future events, an evaluation horizon, and observable goal predicates. A run starts from a clean state. Reads and immediate actions do not advance time; `advance_time` moves to the next event. Future events stay hidden until released. After the agent stops or exhausts its budget, the evaluator processes remaining events through the horizon.

The event order for simultaneous deposits, notices, and scheduled payments must be fixed and documented before evaluation. Scheduled payments must recheck funds and bill status when executed. A failed scheduled payment leaves the bill unpaid.

## Agent

The planned loop is retrieve, plan, act, observe, and update. The Workspace Reader retrieves evidence; the State Manager stores facts and tool outcomes; the Planner/Executor chooses actions; the Validator checks consequential actions immediately before execution. The validator uses only ordinary observations, never hidden checker answers.

Planned tools include `read_document`, `check_balance`, `check_usage`, `schedule_payment`, `pay_bill`, `cancel_subscription`, and `advance_time`.

## Evaluation

The benchmark target is 20 distinct tasks. Independent checkers will score final-state success. The primary measure is the unweighted mean of per-task success fractions over three runs per task. Secondary measures include missed obligations, invalid executed actions, evidence quality, monetary outcomes, tool calls, tokens, and API cost.

Planned comparisons include an adaptive tool-using LLM, a fixed-order pipeline, a no-validator ablation, and a non-interactive LLM. Model, sampling settings, token cap, shared tools, and action caps will be fixed before final evaluation.

## Decisions to finalize

- Exact JSON schemas for state, documents, events, actions, and task goals.
- Simultaneous-event ordering and time representation.
- Common agent interface and evaluation command.
- Exact model version and frozen budgets after development pilots.
