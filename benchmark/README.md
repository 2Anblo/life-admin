# Benchmark

The benchmark is planned as 20 original, objectively checkable personal-administration tasks. Each task should specify initial state, synthetic documents, tools, deterministic events, evaluation horizon, and success predicates. Parameter variants and repeated runs do not count as distinct tasks.

`tasks/` will hold task fixtures. `checkers/` will hold independent scoring code. These should work without the LifeAdmin agent so another agent can be evaluated against the same environment.

The first fixture, `tasks/avoid_late_fees_01.json`, specifies three bills, one incoming deposit, and a September 28 evaluation horizon. `checkers/bill_payment.py` verifies that all bills settled once, on time, for the correct amount, without overdraft. This is one of the planned 20 distinct tasks; the remaining tasks and common runner are not yet implemented.
