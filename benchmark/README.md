# Benchmark

The benchmark is planned as 20 original, objectively checkable personal-administration tasks. Each task should specify initial state, synthetic documents, tools, deterministic events, evaluation horizon, and success predicates. Parameter variants and repeated runs do not count as distinct tasks.

`tasks/` will hold task fixtures. `checkers/` will hold independent scoring code. These should work without the LifeAdmin agent so another agent can be evaluated against the same environment.

The first fixture, `tasks/avoid_late_fees.json`, specifies three bills, one incoming deposit, and a September 28 evaluation horizon. `checkers/bill_payment.py` verifies that all bills settled once, on time, for the correct amount, without overdraft. This is one of the planned 20 distinct tasks; the remaining tasks and common runner are not yet implemented.

## Subscription tasks

`cancel_unused_subscriptions`, `free_trial_conversion`, `price_increase_review`, and `downgrade_by_usage` are scored by `checkers/subscriptions.py`. The simulator does not support them yet. Their fixtures add `subscriptions`, `usage`, `renewal` and `price_change` events, and a hidden `expected` block that is never shown to the agent. The checker requires the simulator's final result to include `subscriptions` (status, plan, `cancelled_on`) and an exact list of executed `charges`. `tests/test_subscription_checkers.py` checks each task's correct final state and the plausible wrong ones.
