# Benchmark

The benchmark is planned as 20 original, objectively checkable personal-administration tasks. Each task should specify initial state, synthetic documents, tools, deterministic events, evaluation horizon, and success predicates. Parameter variants and repeated runs do not count as distinct tasks.

`tasks/` will hold task fixtures. `checkers/` will hold independent scoring code. These should work without the LifeAdmin agent so another agent can be evaluated against the same environment.

The first fixture, `tasks/avoid_late_fees.json`, specifies three bills, one incoming deposit, and a September 28 evaluation horizon. `checkers/bill_payment.py` verifies that all bills settled once, on time, for the correct amount, without overdraft. This is one of the planned 20 distinct tasks; the remaining tasks and common runner are not yet implemented.

## Subscription tasks

`cancel_unused_subscriptions`, `free_trial_conversion`, `price_increase_review`, and `downgrade_by_usage` are scored by `checkers/subscriptions.py`. The simulator does not support them yet. Their fixtures add `subscriptions`, `usage`, `renewal` and `price_change` events, and a hidden `expected` block that is never shown to the agent. The checker requires the simulator's final result to include `subscriptions` (status, plan, `cancelled_on`) and an exact list of executed `charges`. `tests/test_subscription_checkers.py` checks each task's correct final state and the plausible wrong ones.

## Tasks 11-20

Two more v2 payment tasks run end to end on the current simulator, with reference plans, floor-policy expectations, and trace tests in `tests/test_easy_payment_tasks.py`:

- `credit_card_statement_balance` (`checkers/statement_balance.py`): pay the statement balance, not the minimum shown in Bill Pay or the current balance.
- `loan_payoff_cleanup` (`checkers/loan_payoff.py`): cancel both scheduled payments to a paid-off loan, but keep a look-alike savings transfer to the same credit union and rent.

Eight tasks use `schema_version: 3`. The simulator does not support them yet. Each fixture lists accounts, orders, cases, past transactions, and a `rules` list describing how the environment must respond, so the simulator can be built from the fixture. The hidden `expected` block is scored by an independent checker that reads a final `result` (format documented at the top of each checker):

| Task | Checker | Needs tools |
|---|---|---|
| `refund_within_return_window` | `checkers/merchant_cases.py` | `request_refund` |
| `refund_descriptor_reconciliation` | `checkers/merchant_cases.py` | `close_case`, `escalate_case` |
| `partial_refund_with_fee` | `checkers/merchant_cases.py` | `request_refund` |
| `split_tender_refund` | `checkers/merchant_cases.py` | `request_refund`, `escalate_case`, `close_case` |
| `missing_refund_escalation` | `checkers/merchant_cases.py` | `escalate_case`, `close_case` |
| `duplicate_charge_dispute` | `checkers/merchant_cases.py` | `open_dispute` |
| `renewal_reminders` | `checkers/reminders.py` | `create_reminder` |
| `move_out_closeout` | `checkers/move_out.py` | bill tools plus `cancel_subscription` |

`tests/test_case_task_checkers.py` checks each task's correct final result and the specific mistakes the task is designed to catch. `run_task.py` only runs v2 tasks.
