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

## N03 Checkpoint

LOOKUP distinguishes CORRUPT from missing uploads and preserves other errors.
Explicit same-manifest repair creates another managed object, verifies bytes,
then switches the unique SQLite authority. Concrete pins protect old objects;
unpin/restart reclaims obsolete owned files, not unknown files. Legacy duplicate
selection validates bytes and uses transferId ordering rather than row order.

The new RetryProjectResourceRecovery RPC shares configuration single-writer
admission and result queries. It prepares the committed configuration without a
revision change, variable reset, device activation, listener start or job start.
Quarantined/active/closed device executors and unrelated publication errors remain
blocked. Designer separates byte repair from the explicit readiness action.

- `n03-before.xml`: three failures reproducing the unreachable normal repair path.
- `n03-regression.xml`: 38 passed; resource transfer, preparation, lifecycle and
  project-replacement regressions. `n03-native.xml`: 17 passed, including real gRPC
  and native Windows Qt confirmation/cancel/recovery signals.
- `n03-edge.xml`: 15 passed, including deterministic legacy migration, permission
  error preservation, concurrent repairs, transaction failure, unknown COMMIT
  reconciliation, stale instance/revision, cancellation and unchanged variables.
- `source-final.xml`: 201 passed, no skips, across 27 related test modules with
  native Windows Qt. This is a focused suite, not the repository's entire suite.
- `n01-persistent.xml`: one actual persistent spawned consumer processes queued
  deliveries reordered by the fixture to R2/R1/R2, with real results 99/1/99.
- `n01-depth.xml`: 5 passed, one failed test expectation (3x3 blur instead of the
  declared default 5x5); preserved. `n01-depth-final.xml`: 7 passed after matching
  that default, checking actual original/derived image bytes and three Dispatch
  levels across R2/R3 publications. Counts overlap; do not sum them.
- Source Ruff passes; mypy passes for 493 files with its existing untyped-body
  exclusions. Generated protocol files are included and diff checks pass.

The packaged selftests now contain synthetic retained-loop, package-slot and
same-manifest recovery probes. The Designer source selftest passed after correcting
the first fixture's omitted schema sections (`source-selftest-final.json`).

Recovery result records are bounded and instance-bound, not durable perpetual
idempotency. Unknown replies never authorize another repair; query the original
transfer/request, and recompare after an instance change. This is not a new GC.

Final frozen, isolated-tree and publication evidence is recorded below after it
has actually run. Physical devices, human UI and clean-machine acceptance remain
separate and are not implied by native automated Qt or packaged selftests.
