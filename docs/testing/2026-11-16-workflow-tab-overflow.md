# Workflow tab overflow count and spacing

Date: 2026-11-16. Executor: Codex.

## Source and scope

- Base HEAD: `556acb10760a9463fe832aa4d8e7fc5c0a95a67e`.
- Results apply to that HEAD **plus the uncommitted changes** in
  `src/emo_master/apps/designer/ui/workflow_navigation.py`,
  `tests/designer/test_workflow_tab_add_button.py`, and
  `tests/designer/test_workflow_tab_overflow.py`, not to a clean commit.
- Nine pre-existing untracked testing reports were left unchanged.
- Concurrent workspace edits appeared in
  `src/emo_master/apps/designer/controllers/workflow_navigation_controller.py`,
  `src/emo_master/apps/designer/ui/workflow_navigation_controls.py`,
  `tests/designer/test_workflow_history_navigation.py`, and
  `tests/designer/test_workflow_reference_chains.py`, with a separate hierarchy
  report. Their diff was checked and left intact; they are not this task's edits.
  The final expanded regression result includes these concurrent changes.
- Scope: the top workflow/page tab strip, its overflow indicator, and wheel
  navigation. No graph-panning behavior, project format, or device behavior changed.

## Behavior

- The list button shows `+N` for the total number of tabs currently outside the
  fitted range. Its tooltip gives the total and the hidden counts before/after
  that range. Clicking it still opens the existing searchable picker.
- When all tabs fit, the same button shows the total for multiple tabs; a single
  tab or an empty strip keeps the icon-only button.
- The count button reserves width using Qt's styled size hint and the total
  count, so changing the hidden count does not move the commands or clip digits.
- With overflow, the fitted tabs share unused width instead of leaving a
  tab-sized trailing blank. Without overflow, short tabs remain compact.
- Wheel input handles horizontal/vertical angle deltas and pixel-only swipes.
  Empty gesture-end events and attempts to move beyond either end do not change
  selection. Workflow IDs, picker behavior, and runtime state remain unchanged.

## Verification

Environment: Windows, Python 3.10.21, PySide2 5.15.2.1, pytest 9.1.1.
Tests use isolated settings, temporary projects, and stub Runtime clients.

- Focused offscreen tests: **29 passed**.
- Native Windows Qt at 100%: **70 passed**.
- Native Windows Qt at 150%: **68 passed, 2 failed**. Both failures are the
  pre-existing resize test's LTR/RTL variants: a requested 1500-logical-pixel
  top-level window is clamped to 1283 on this 1280-logical-pixel-wide screen.
  A diagnostic confirmed that the selected tab remained fully visible.
- Replacing only the changed navigation module in memory with its exact base
  HEAD source reproduced both 150% resize failures. No worktree files were
  reverted or tests weakened/skipped for this comparison.
- Final expanded offscreen run: **126 passed**, including page navigation.
- An earlier expanded run exposed a reference-popup assertion at
  `tests/designer/test_workflow_history_navigation.py:462`: the test expects a
  trailing space in a displayed workflow name. The same failure was reproduced
  with only the base navigation module substituted in memory (not a clean
  repository baseline). The concurrent hierarchy edits subsequently changed
  this assertion to match the stored, stripped name and tooltip layout; the
  final run passed. This task did not edit that test or fix the hierarchy.
- Ruff F checks for all three changed Python files and `git diff --check` passed.
- Reviewed native 100%/150% tab-strip screenshots and the 100% Designer screenshot.
  Counts and labels are visible and the strip fills its available width.

JUnit records are under `logs/workflow-tab-overflow-*.xml`; the initial failing
development run is retained separately from the final run. Native screenshots
are under `logs/workflow-tab-overflow/native-100/` and `native-150/`.

The 14 new tests cover counts, full-width fitting, both layout directions,
angle/pixel wheel input, empty events, end bounds, resize, rename, runtime
markers, removal, clear/reload, and rendered screenshots. Existing navigation,
entry marker, add-command, history, stylesheet, and page-navigation tests were
also exercised. The screen-limited native widths are not claimed as verified
1500/1864-logical-pixel viewports at 150%.

No full-suite, packaged-product, manual user acceptance, or real-device test was
performed. No live Designer restart, field-project write, commit, push, or
deployment was performed.

## Workspace Delivery Follow-Up

On the subsequent request to organize, commit, and push the workspace, the
31 XML/screenshot artifacts originally under `logs/` were archived to
`docs/testing/2026-11-16-workflow-tab-overflow/evidence/`. All file SHA256
values were checked before and after moving and are unchanged. The original
relative paths, failed development runs, and the two baseline comparison
reports remain present under that archive; the paths above describe their
original execution locations. No test failure evidence was removed.

The implementation, its two test files, this record, and the archived evidence
are selected as one focused delivery commit. The nine offline field-validation
reports are preserved separately; their historical measurements and acceptance
limits are not re-run or reclassified by the workspace cleanup. Current
integration validation and push scope are recorded in
`docs/testing/2026-11-16-workspace-delivery.md`.
