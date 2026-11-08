# Collapsible Operator Debug Parameters

Date: 2026-11-08 (Asia/Shanghai).

## Source State

- HEAD: `1aa3023d13703dddcf021f9734466cb78f524613`.
- This validation includes uncommitted work from the preceding image-import,
  resource-path, and image-result fixes. It is not validation of the clean HEAD.
- Current change: `ui/debug_parameters.py`, its integration in the operator debug
  window, dedicated tests, and affected input-layout/timing assertions.
- Existing user project files, untracked `test/`, model settings, and the running
  Designer were not changed. No commit, push, or live application restart occurred.

## Behavior

- The input page contains an initially expanded, collapsible operator-parameter
  section below its input ports. It uses the original generic schema form, including
  defaults, file pickers, conditional controls, and variable-source selectors.
- There is no second editable parameter copy. Execution and fixture export still
  collect the editor's current draft. Expanding/collapsing does not mark it dirty.
- During debugging the original editor shows a location placeholder. Normal debug
  close, rejection, or detach returns the same form to its original scroll area
  before the debug dialog is destroyed. Unapplied edits remain unapplied.
- Specialized editors retain their controller and editing UI. Their debug section
  shows a read-only live parameter/source snapshot plus an editor navigation button.
  Invalid custom drafts clear the old snapshot and display the collection error.
- Execution, application-to-project, result history, and resource-close contracts
  are unchanged. No automatic saving or Runtime configuration write was added.

## Verification

Environment: repository Python 3.10 / PySide2; native Windows Qt.

- Six new parameter tests obtained passing results. They cover repeated open/close,
  draft edits used by execution, schema refresh, parent-driven shutdown, complete
  YOLO controls at 620/900-pixel widths, custom editor snapshots, and bound variables.
- New tests plus feedback tests: 11 passed in the first corrected focused run.
- Combined affected-window run: 128 passed, 4 failed in 294.25 seconds. The four
  failures were already-collected old layout assertions expecting three items
  instead of four after adding the parameter section. They were not source failures.
- After updating the layout assertions and adding form-identity checks, the entire
  recovery test module was rerun: 37 passed in 121.36 seconds, including all four
  previously failing cases. This overlaps the combined run; counts are not additive.
  The entire 132-test combination was not rerun as one invocation.
- Earlier test-harness failures were retained and corrected: explicit Qt deferred
  deletion processing for teardown assertions, and waiting for history-image loading
  before clicking the enabled Run button. No behavior assertion was removed.
- Native screenshots at both widths were inspected. The model path is visible on
  expansion; remaining fields scroll without overlapping inputs or the toolbar.
- Ruff F checks on the new module/tests and `git diff --check` passed.

Not performed: full repository suite, packaged-product testing, manual acceptance
in the user's running Designer, or real camera/PLC/model field acceptance.
Normal Designer shutdown/relaunch is required to load the updated source.

## Subsequent Delivery Verification

The later combined delivery run passed all 261 tests, including this parameter
suite and all corrected recovery cases. See `2026-11-08-debug-delivery.md` for
the complete invocation scope. The earlier results above remain historical evidence.
