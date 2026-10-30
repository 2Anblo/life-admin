# Benchmark

The benchmark is planned as 20 original, objectively checkable personal-administration tasks. Each task should specify initial state, synthetic documents, tools, deterministic events, evaluation horizon, and success predicates. Parameter variants and repeated runs do not count as distinct tasks.

`tasks/` will hold task fixtures. `checkers/` will hold independent scoring code. These should work without the LifeAdmin agent so another agent can be evaluated against the same environment.

The first fixture, `tasks/avoid_late_fees.json`, specifies three bills, one incoming deposit, and a September 28 evaluation horizon. `checkers/bill_payment.py` verifies that all bills settled once, on time, for the correct amount, without overdraft. It is the first of the 20 tasks.

## Running the tasks

All tasks except `avoid_late_fees` (which keeps the v1 simulator) run on `LifeWorkspace` (`src/lifeadmin/simulator/workspace_v3.py`). It extends the v2 payment workspace with subscriptions, refund cases, card disputes and reminders, and every task gets the same tool set (`src/lifeadmin/agent/tools_v3.py`), so the tools on offer never hint at the task type. `python run_task.py benchmark/tasks/<task>.json` runs the reference plan and every floor policy on one task.

A fixture's prose `rules` describe the environment for readers; its `simulation` block is the machine-readable form the workspace uses (refund posting dates, amounts withheld, escalation windows and replies, scheduled card postings, dispute credits). The agent sees neither, nor anything under `hidden`.

Shared environment rules for the new task types:

- A renewal charges on its date. A cancellation stops it only if made at least `cancel_notice_days` (minimum 1) days earlier; a later cancellation is accepted but that renewal still charges. A plan change applies from the next renewal.
- Refund requests are accepted when submitted and decided by the merchant afterwards. Approved money posts later as a card transaction; an ineligible request is declined the next day by a notice document. Each returned item is refunded once.
- An escalation before the merchant's window is rejected and logged with reason `premature`; one after the window is accepted but changes nothing.
- Bills and documents can appear during the run. `advance_time` takes an optional `until` date for waiting when nothing is scheduled.

## Validation

Every task's reference plan passes in the simulator, and replaying its tool calls reproduces both snapshots. Eight floor policies run on all 19 tasks: P0-P5 and P7 (pay every bill, cancel every subscription, escalate and close every case, dispute every charge) fail all of them. P6 replays the reference plan but stops at the first `advance_time`; it fails the four tasks that need information released during the run (`replace_schedule`, `split_tender_refund`, `missing_refund_escalation`, `move_out_closeout`). Each fixture records these outcomes in `hidden.floor_expected`.

| Area | Tests |
|---|---|
| Payment tasks | `tests/test_payment_tasks.py`, `tests/test_easy_payment_tasks.py` |
| Subscription, refund, dispute, reminder and move-out tasks | `tests/test_life_tasks.py` (traces), `tests/test_subscription_checkers.py`, `tests/test_case_task_checkers.py` (hand-built results) |
| Workspace mechanics | `tests/test_core_v2.py`, `tests/test_workspace_v3.py` |
