# Benchmark

The benchmark is planned as 20 original, objectively checkable personal-administration tasks. Each task should specify initial state, synthetic documents, tools, deterministic events, evaluation horizon, and success predicates. Parameter variants and repeated runs do not count as distinct tasks.

`tasks/` will hold task fixtures. `checkers/` will hold independent scoring code. These should work without the LifeAdmin agent so another agent can be evaluated against the same environment.

No benchmark task or checker has been implemented yet.
