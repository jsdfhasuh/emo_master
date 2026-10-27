# Run Failure Popup and Node Location

Date: 2026-10-23. Source HEAD: `e8c24fcfd7ebf964f0f506a7d73a42405069dff9`
on `agent/runtime-workflow-architecture-v1`, plus existing uncommitted changes.

## Scope and Behavior

- Runtime Job failures previously updated the panel and logs without notifying
  the user in a popup. Add an owned, asynchronously opened QMessageBox after
  terminal failure status, once per start attempt.
- Preserve the first concrete `node.failed` event. Nested workflow/loop failures
  must not replace the originating child node with the outer call node.
- Show workflow name/ID, node display name/ID, error code, and failure reason.
  Expanded details retain the complete message, Job/call identity, iteration
  path, and diagnostic payload even when the summary is shortened.
- Reuse runtime-log project provenance and node navigation. A location action
  does not edit parameters or the draft. Unknown/other-project identity cannot
  borrow names or navigate to a coincidentally matching current node.
- Startup rejection and failure without node correlation remain explicit;
  the selected node is never invented as the failure source. Missing resources
  are not repaired or uploaded by this change.
- Keep notifications separate per workflow Job, clear failure identity on the
  next start, and suppress duplicate status/worker notifications. Successful or
  cancelled Jobs, observer uncertainty, and closing windows do not raise a Job
  failure popup. Existing logs and result-panel details remain available.

## Verification

Interpreter: `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`, Python 3.10.21,
PySide2 5.15.2.1. Tests use synthetic events/clients and isolated test state.

- Final affected regression: **184 passed, 2 skipped in 47.97s** across 13
  Designer test files covering failure feedback, controllers, workers, Qt
  ownership, logs, snapshot policy, publication, online runs, and inspection.
- The two skips are native QMessageBox rendering tests; the Windows offscreen
  plugin is not used to render message boxes. Separate hidden Windows-platform
  checks passed **2 tests at 100%** and **2 tests at 150%**.
- Native checks cover node failure and long resource-error text, label/button
  geometry, screen-height bounds, complete details, Chinese expand/collapse
  labels, and screenshots. The rendered screenshots were visually inspected.
- Screenshots are under `build/run-failure-windows-100-final-20261023` and
  `build/run-failure-windows-150-final-20261023`; earlier renders are retained.
- Focused Ruff passed. Mypy passed for the runtime controller and popup module.
  Including main_window also reports an existing optional PageCoordinator
  access at the unrelated device-status connection; this was not changed.
- Initial checks caught use of `title` instead of the actual `displayName` node
  field, fixed in source and retained tests. Fixture mistakes about addWorkflow's
  string return and RuntimePanelState's `lastMessage` were corrected without
  weakening assertions. Intermediate failures are not counted as passes.
- `git diff --check` passed; unrelated working-tree changes were preserved.

## Boundaries

This is source, simulated-event, controlled Qt, and hidden native-widget
verification, not manual desktop, frozen-package, camera, or PLC acceptance.
No user process restart, live project/resource mutation, upload, device access,
package build, commit, or push occurred. Reopen a source-launched Designer to
load the change; a packaged Designer needs a separate rebuild.
