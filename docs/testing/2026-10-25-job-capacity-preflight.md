# Job Capacity Preflight Feedback

Date: 2026-10-25 (Asia/Shanghai).
Source HEAD: `9265547dbe61bc57542d3cf0ea143fcf455600fb`, plus existing and task-local uncommitted changes.

## Report and Evidence

- The reported popup contained `E_TCP_PREFLIGHT: E_MAX_CONCURRENT_JOBS: entire run group exceeds capacity`.
- The capacity guard was inside a generic preflight exception handler. Raising a plain `ValueError` incorrectly added the TCP prefix and discarded all admission counts.
- The existing calculation counts one entry Job, one persistent consumer per Dispatch slot, and all Jobs still owning supervisor resources. No incorrect count was established.
- Read-only inspection of the local embedded Runtime found online project `RTSP Face Detection`, `maxConcurrentJobs=2`, one Dispatch slot, and RUNNING Jobs for `main` and `camera_once`, started at approximately 15:49:55. Their PIDs matched existing worker processes. This establishes the state at inspection, not the exact earlier screenshot state or real-device correctness.
- No live Job was started/stopped, no hardware was contacted, and no field project or online configuration was modified by this task.

## Local Changes

- Capacity rejection now returns `E_MAX_CONCURRENT_JOBS` directly, with required, consumer, owned, configured-limit, and available counts in `diagnostics_json`.
- Designer summarizes the Runtime counts without borrowing the current draft's limit. It distinguishes insufficient configured capacity from capacity occupied by running/retiring Jobs and keeps the raw reason in details.
- Existing admission limits, resource ownership, device preflight, and explicit online publication remain unchanged. No automatic limit increase or replay was added.
- New temporary projects use `WorkflowStore`, schema 2.7, `ProjectDocument`, and `saveProject`, including save/reload and Runtime compilation checks.

## Verification

Python: existing `emo_master` Python 3.10 environment, PySide2 5.15.2.1.

- Initial test setup failure: omitted Runtime instance fencing produced `RESET_REQUIRED`; the test request was corrected without changing product behavior.
- Before the product fix, the focused regression reproduced the reported TCP-wrapped capacity error (`pytest-3458`, one failed test at the expected error-prefix assertion).
- First combined run after the product fix: 30 passed, 3 failed, 4 skipped. The three failures were invalid new test consumers missing workflow boundary nodes; corrected by creating them through `WorkflowStore.addWorkflow`. The four skipped cases required the native Windows Qt platform and were subsequently executed, not counted as passes in the earlier run.
- Native focused run: **39 passed**, no skips, with `QT_QPA_PLATFORM=windows`; files `tests/runtime/test_job_capacity_preflight.py` and `tests/designer/test_run_failure_feedback.py`.
- Related regression run: **86 passed**, no skips; files `test_frame_dispatch.py`, `test_parallel_workflows.py`, `test_dispatch_queue_integration.py`, `test_run_targets_dispatch.py`, `test_runtime_controller.py`, and `test_online_run.py` in their existing runtime/designer test directories.
- A real spawned-process simulation verified that rejecting an additional group leaves the existing entry/consumer running; stopping and retiring that group permits a fresh start.
- Native Qt tests checked labels, buttons, screen bounds, and expanded details. The capacity popup screenshot was also visually inspected.
- Ruff passed for the two changed product files and two changed/added test files. Scoped `git diff --check` passed.

## Boundaries

- No full-suite, packaged EXE, deployment, field-project rerun, or real-camera/PLC acceptance.
- The already-running Designer process was not restarted. It keeps its previously loaded Python code; these source feedback changes take effect after its next normal restart.
- This repair corrects failure classification and actionable feedback; it does not remove a real capacity constraint or prove the screenshot's exact originating UI sequence.
