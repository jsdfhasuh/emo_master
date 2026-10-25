# Standalone Runtime Configuration Verification

## Source And Scope

- Date: 2026-10-24.
- Base HEAD: `577450fc19c60ac86989475d76838e952bcc3e86`.
- Branch: `agent/runtime-workflow-architecture-v1`.
- Verification used the dirty working tree, including the local fixes below. The base SHA alone does not contain them.
- Python: `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`; Windows, PySide2 / Qt5.
- Existing unrelated edits were preserved. No commit, package build, deployment, or physical-device access was performed.

## Corrected Normal Paths

- Explicit load/reload of a current 2.7 project commits the selected disk payload through the existing configuration coordinator, instead of silently reusing its previous online payload.
- Switching projects retains the actual selected source path and relative input/output roots, including a copied project with the same identity in another directory.
- Explicit reload refreshes file inputs even if the JSON path has not changed.
- A rejected managed-project candidate retains the previous online project, directory owner, pages, and usable controls.
- A stopped operator-window package update installs files and activates the online configuration together. A pre-commit rejection uses the installer's existing rollback; business output history is preserved.
- No-argument startup restores the committed online state. Explicit selection/reload applies disk changes; startup does not implicitly publish drafts.
- `--check` validates the selected disk project without creating Runtime, connecting devices, or writing the online configuration. It can run while an instance owns the data directory.
- The window displays the committed document revision. Running jobs must still be stopped normally before replacement.

## Test Evidence

Focused regressions were first reproduced on the original source: four failures covering stale reload, both project-switch cases, and package rejection.

The combined source/UI regression completed with **189 passed in 255.63 seconds**, without skips. It covered these files:

```text
tests/runtime/test_production_project_configuration.py
tests/runtime/test_production_continuous.py
tests/runtime/test_production_boundaries.py
tests/runtime/test_named_project_runtime.py
tests/runtime/test_project_configuration.py
tests/runtime/test_initial_project_configuration.py
tests/runtime/test_review_project_replacement.py
tests/runtime/test_review_resource_lifecycle.py
tests/runtime/test_configuration_transition.py
tests/runtime/test_configuration_owners.py
tests/runtime/test_device_replacement.py
tests/core/test_runtime_directory.py
tests/core/test_runtime_package.py
tests/ui/operator_view/test_production_configuration_window.py
tests/ui/operator_view/test_production_window.py
tests/ui/operator_view/test_parallel_workflow_window.py
tests/ui/operator_view/test_basic_runtime_feedback.py
tests/test_operator_runtime_validation.py
```

After strengthening the window assertions, the final Windows-native Qt run used `QT_QPA_PLATFORM=windows` and completed with **48 passed in 51.06 seconds**, without skips:

```text
tests/ui/operator_view/test_production_configuration_window.py
tests/ui/presentation/test_job_status_rendering.py
tests/runtime/presentation/test_live_job_status.py
```

This includes four operator-window tests for reload, rejected/successful selection, package activation with continuous auto-start/stop/restart/close, and remembered startup. The new project fixture uses `WorkflowStore().toPayload()`, schema 2.7 validation, save/re-read, and compilation. Runtime tests also exercise connected simulated-camera replacement, not physical hardware.

The native screenshot was inspected: configuration revision 2, completed task, retained result image, and usable controls are visible without overlap. A temporary assertion expecting Display `resourcesReleased=True` while the result was still retained was incorrect and failed. Existing `DisplayRpc.GetJob` and `testRealCapturedTerminalStatusSurvivesOwnerRelease` establish that this flag includes retained presentation resources. The final test separately asserts worker retirement and continued result availability; no production lifecycle behavior was changed for that assertion.

Local evidence directory: `C:/Users/jsdfhasuh/AppData/Local/Temp/emo-runtime-review-20261024/`.

- `configuration-regression-final.xml`: combined 189-test result.
- `configuration-native-verified.xml`: final 48-test result.
- `configuration-reloaded-verified.png`: final native-window screenshot.
- `configuration-regression.xml` and `configuration-native-retired.xml`: retained intermediate collection/assertion failures, not counted as passes.

Ruff passed for the six changed production modules and three added test modules. Scoped `git diff --check` passed. Mypy did **not** pass: it follows imports into two pre-existing, unrelated dirty files, reporting `core/workflow/compiler.py:358` (`dict-item`) and `apps/runtime/workflow/runner.py:249` (`attr-defined`). Those files were not changed by this fix.

## Acceptance Boundary

These are focused source-level and automated native-Qt checks, not a full repository suite or manual site acceptance. No frozen EXE self-test, packaging, physical camera/PLC/robot verification, or deployment was performed. Existing EXEs require a rebuild to include the changes. See [operator Runtime behavior](../operator-runtime.md).
