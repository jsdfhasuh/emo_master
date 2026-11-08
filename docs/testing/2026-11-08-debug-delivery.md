# Debug Improvements Delivery Verification

Date: 2026-11-08 (Asia/Shanghai).

## Source State and Scope

- Base HEAD: `1aa3023d13703dddcf021f9734466cb78f524613` on `main`.
- Validation uses the staged Designer/Runtime debug improvements and their tests,
  not the clean base HEAD. This report is included in the delivery commit.
- Scope: JPEG/alpha-PNG input, project-relative resources, result-image selection,
  and collapsible operator parameters sharing the editor's original draft form.
- Existing `test/test.emoproj` and `test/test.emoproj.bak` remain local and untracked.
  The user's field project, model settings, and running Designer are unchanged.
- Earlier evidence and corrected test failures remain in
  `2026-11-08-debug-resource-images.md` and `2026-11-08-debug-parameters.md`.

## Final Combined Regression

Environment: Python 3.10.21, PySide2 5.15.2.1, native Windows Qt
(`QT_QPA_PLATFORM=windows`), using the `emo_master` conda environment.

The combined invocation covers these modules:

```text
tests/runtime/test_debug_resource_paths.py
tests/runtime/test_operator_debug_resources.py
tests/runtime/test_operator_debug_rpc.py
tests/runtime/test_operator_debug_connection.py
tests/runtime/test_image_loader_debug.py
tests/runtime/test_patchcore_debug.py
tests/designer/test_debug_parameters.py
tests/designer/test_operator_debug_window.py
tests/designer/test_debug_feedback.py
tests/designer/test_debug_dialog_retirement.py
tests/designer/test_debug_window_recovery.py
tests/designer/test_debug_image_results.py
tests/designer/test_debug_input_files.py
tests/designer/test_image_loader_debug_window.py
tests/designer/test_patchcore_inference.py
tests/designer/test_workflow_debug_window.py
tests/designer/test_debug_main_window_boundaries.py
tests/designer/test_operator_editor_manager.py
tests/designer/test_parameter_editor_retirement.py
```

Result: **261 passed in 518.68 seconds (8 minutes 38 seconds)**, exit code 0.
This single invocation includes the previously corrected recovery/layout tests;
earlier run counts are not added to this total.

## Checks and Limits

- Ruff `--select F` passed for all changed/new Python files.
- `git diff --check` and staged diff checks passed before delivery.
- This is targeted source and native Qt automation, not the full repository suite.
- No packaged-product validation, manual acceptance in the running Designer,
  physical camera/PLC acceptance, or inference using the user's actual model.
- Normal Designer shutdown/relaunch is required to load the updated source.
