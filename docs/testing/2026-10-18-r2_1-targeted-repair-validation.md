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

## N02 Checkpoint

Workflow import no longer shadows its target workflow with a Dispatch dictionary.
Import-closure slot mappings preserve legitimate sharing while avoiding existing
slots. Join ports, edges and typed SQLite writer sources follow the mapping.

- `n02-before.xml`: three failures; both public entrypoints reproduce the original
  AttributeError, and the new typed-port test requires the mapping interface.
- `n02-first.xml` / `n02-final.xml`: preserved fixture failures (Join output ports
  were absent from the editable fixture, so boundary normalization removed edges).
- `n02-recheck.xml`: 25 passed after supplying actual editor port metadata.
- `n02-native.xml`: 26 passed, including combined original/copy spawn execution,
  legacy package compatibility, resource guards, actual native import cancellation,
  confirmation, undo/redo, save/reopen with Dispatch and resource dependencies.

Command: `python -m pytest -q tests/designer/test_targeted_dispatch_package.py
tests/designer/test_workflow_package.py tests/designer/test_workflow_package_resources.py`
with `QT_QPA_PLATFORM=windows`. Scoped Ruff/mypy pass. Counts overlap.

N03 and final frozen/publication verification are pending at this checkpoint.
