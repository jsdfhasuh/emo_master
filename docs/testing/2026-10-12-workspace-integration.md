# Workspace Integration Validation

Date: 2026-10-12 (Asia/Shanghai).

## Source Identity

- Branch: `agent/runtime-workflow-architecture-v1`.
- Original local HEAD: `abc7ae0541e997be0bbe3a4794ad604c239de79a`.
- Integrated remote baseline: `4bcce9a`, a fast-forward of five existing commits.
- Operator feature commit: `25918c7`.
- Designer integration and tested source: `5782cb32f40ae1def37edcb7fde94ad355796010`.
- Concurrent upstream delivery: `1f76a94` and `f448c88`, integrated without rewriting
  either history in merge `4e7d609aef41354868770e2e8bd6bbcc7b4ac521`.
- The merge removes one extra EOF blank line from each of
  `navigation_history.py` and `workflow_navigation_controller.py`; it does not
  change executable behavior. `git diff --ignore-blank-lines 5782cb3 4e7d609 -- src`
  is empty. The full-suite result below belongs to `5782cb3`.
- The only post-freeze test change is the upstream expansion of blank-pan coverage
  from one case to four zoom scales. The final test file matches `f448c88` exactly.

## Included Changes

- Conditional value selection and confidence-ranked YOLO crop output, including
  pixel/frame contracts, validation, documentation, and regression tests.
- Workflow tab overflow, search, backward/forward navigation, definition/reference
  navigation, and viewport restoration without editing the workflow graph.
- Persisted project startup workflows, explicit temporary launch overrides,
  multi-entry page source discovery, global-variable bindings, and result messages.
- Canvas navigation, coordinate-mode port presentation, edge routing and focus,
  shared-segment selection, and a device-free flow-edge example.

Four generic navigation/startup test modules now use a deterministic, device-free
29-workflow fixture rather than a private field project. This is portable UI
regression coverage, not field-project acceptance. The toolbar test now verifies
the explicit project startup tooltip instead of expecting the old generic label;
its visibility, size, action identity, enablement, and resize assertions remain.

## Publication Boundary

The destination repository is public. The private `projects/` tree, its backup,
coordinate/calibration/reference files, seven project-specific test modules, and
the original dirty-snapshot evidence/patch archive remain local. The public
`flow-edge-readability-2026-10-12/delivery/` records were already published by the
concurrent upstream delivery and are retained unchanged.
No private fixture is required by the committed tests. None of those local-only
files was deleted or overwritten to obtain a clean published snapshot.

The three local flow-edge documents that collided with upstream publication were
copied with hash verification to
`build/workspace_delivery_20261012/local-docs-before-integration/` and also retained
in the stash named `preserve local flow-edge documents before concurrent upstream
integration`. They were not blindly restored over the newer upstream documents.
The recovery stash object is `9ef7008cba053dd736877bd833e9b7a5c3b59110`.
All 29 private project files and project-specific tests match their initial SHA-256.
The old evidence archive has not been republished or rewritten.

## Validation

- Portable navigation, saved startup, history, and toolbar regression: **69 passed**.
- Protobuf drift, Ruff, and mypy: **PASS**; mypy checked 411 source files.
- Full CI at `5782cb3`: **PASS**, exit 0; **5168 passed, 11 skipped, 28 subtests
  passed** in 1616.29 seconds. It ran in a clean local clone with Git metadata and
  without private/untracked files or old manual-test workspaces. Tracked files
  remained unchanged after the run.
- Merged flow-edge, navigation history, and saved-startup regression at `4e7d609`:
  **39 passed** in 18.17 seconds. The final source/test differences from the full
  run are bounded in Source Identity above; the full suite was not rerun after
  the merge.
- Designer `run-designer --local --check`: **PASS**, without starting a window or Job.
- Final whitespace check: **PASS**. No dependency installation or change was needed.

The 11 skips cover unavailable Windows symlink support, an offscreen QMessageBox
limitation, real Huaray hardware, opt-in long-running execution/debug-lease checks,
and a rejection test intended for a non-Windows host. They are not counted as PASS.

Runtime: existing Conda Python 3.10.21 on Windows. The full check uses
`PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`, `QT_QPA_PLATFORM=offscreen`, and
`PYTEST_ADDOPTS=--junitxml=../full-ci.xml`, then runs:

```text
python -u scripts/ci_check.py
```

The log and JUnit outputs are published in
[`workspace-integration-2026-10-12/evidence.zip`](workspace-integration-2026-10-12/evidence.zip),
with results and checksums in
[`validation.json`](workspace-integration-2026-10-12/validation.json).
Archive SHA-256: `0f50e0b52fb578e547873171ce57f5e4463939f652804f65a9b82edb9c31501e`.
Every archive member was read back and matched to its original SHA-256. Local
originals remain under `build/workspace_delivery_20261012/`.
Overlapping focused/full test counts must not
be added. Software CI does not establish real-desktop, DPI, Debian, packaging,
hardware, or production acceptance. Those remain **NOT_RUN** in this request.

## Independent Desktop Acceptance

The inherited [flow-edge delivery report](flow-edge-readability-2026-10-12/README.md)
records **PERFORMANCE_PRECHECK_PARTIAL_FAILURE**: fixed 9-node/18-edge and
100-node/300-edge local-drag P95 increased by 70.9% and 43.4%, respectively, against
a 20% limit. This request does not remeasure or fix that known performance risk.
Its exact source/environment scope remains the upstream report's, not a fresh
measurement of this combined integration. Passing functional CI cannot close it.

Use the [flow-edge checklist](2026-10-12-dot-flow-edge-readability.md) and
[`examples/flow_edge_readability`](../../examples/flow_edge_readability/README.md).
The upstream checklist uses committed source identities and retains the performance
risk. This report identifies the combined integration for subsequent acceptance.
No task was sent to dot, no user application was restarted, and no
camera, PLC, robot, release, or deployment was operated.
