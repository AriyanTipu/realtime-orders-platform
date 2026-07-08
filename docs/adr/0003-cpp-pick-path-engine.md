# ADR 0003 — C++ (pybind11) for the pick-path optimiser, with a Python twin

**Status:** accepted

## Context

Turning an order into a picking route is a travelling-salesman variant:
minimise picker travel over the warehouse grid visiting every bin. We use
nearest-neighbour construction plus 2-opt refinement — O(n²) per pass, many
passes, pure CPU, executed on the request path for every pick list. This is
the honest case for native code: an interpreter-bound inner loop, not I/O.

## Decision

Implement the optimiser twice, deliberately:

- `pickpath` — pure-Python **reference implementation**, the behavioural
  specification. Runs anywhere, including a laptop with no compiler.
- `pickpath-native` — the same algorithm in C++17, bound with pybind11, built
  by scikit-build-core in the Docker builder stage and in CI. The binding
  releases the GIL during optimisation so a busy web worker keeps serving.

Both are deterministic with identical tie-breaking (lowest index on equal
distance; first-improvement 2-opt that restarts its scan after each applied
swap). `pickpath.optimize_route()` selects the native engine when importable
and falls back to Python otherwise.

## Consequences

- **Testability:** parity tests assert *exact* sequence equality between
  engines across 150 randomized instances, and a Held-Karp exact solver
  provides a true optimum oracle for small instances. "The C++ is correct
  because it is provably the same function" beats "it looks right".
- **Honest speedup story:** the benchmark (`native/bench_pickpath.py`, table
  in the README) measures the interpreter overhead being removed — typically
  one to two orders of magnitude on realistic order sizes.
- Cost: two implementations to keep in sync — mitigated by the parity suite,
  which fails loudly on any behavioural drift.
