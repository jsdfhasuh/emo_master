# R2.1 Targeted Repair Evidence

Baseline: `d75292d00b070e8d264bc0a333335f626175629c`. Windows, Python 3.10,
PySide2. Source, native Qt, frozen and physical acceptance remain separate.
Local logs live under `build/targeted-repair-d75292d/` and are not public evidence.
The plan and 30 coverage targets are in `docs/plans/2026-10-18-r2_1-targeted-repair-*.md`.

## N01 Checkpoint

The coordinator distinguishes owned roots, owned detection iterations and borrowed
scopes. Retained deliveries cannot refresh configuration. Borrowed scopes keep the
source cycle/frame identity and never issue endCycle. Outer scopes restore their
actual document and compiled project, not just a revision label.

- `n01-before.xml`: four failures, including real spawned consumers computing 99
  for R1 inputs whose expected value is 1 (original/derived images and no-Join).
- `n01-first.xml`: 38 passed across targeted inheritance, loop configuration,
  Dispatch retirement, frame dispatch and device-cycle source regressions.
- `n01-rpc.xml`: five passed after adding real gRPC writes and a persistent Runner
  R2/R1/R2 installation/exception-restoration check. Counts overlap, do not sum.
- The existing drain test now checks that nested scopes borrow their outer cycle;
  it no longer incorrectly ends that cycle twice. Drain/300-Join tests still run.

Commands: `python -m pytest -q tests/runtime/test_targeted_cycle_inheritance.py`
and the affected `test_loop_cycle_configuration.py`, `test_review_dispatch_lifecycle.py`,
`test_frame_dispatch.py`, `test_review_cycle_sources.py`. Ruff and diff checks pass.
No physical camera, PLC or network reconfiguration was performed.

N02, N03 and final frozen/publication verification are pending at this checkpoint.
