# Workflow Reference Chains

Date: 2026-11-12 (Asia/Shanghai). Executor: Codex.

## Source And Scope

- HEAD: `51bff936ee2f2a0e4c3712441f8e622e33312c3c`.
- The tested worktree is dirty, not a verification of the clean commit.
  Pre-existing changes include ROI editing, image/editor widgets, plugin
  registry and geometry helpers, collection filtering, documentation, tests,
  and untracked examples/local projects. Additional unrelated page-designer,
  presentation-store, and test/documentation changes appeared during this run.
  All were left untouched; this is not a frozen whole-worktree acceptance.
- This change touches the workflow navigation controller, the navigation
  locations popup, `tests/designer/test_workflow_reference_chains.py`, and
  this record. No project schema, Runtime behavior, or field project changed.

## Behavior

- Each caller result shows its full root-to-target workflow path, including
  every calling node. Separate upstream paths remain separate results, even
  when they share the same immediate caller. The count denotes paths.
- Uses existing dependency references for subflows, loop bodies, executable
  legacy While conditions, and Dispatch. Workflows outside the default entry
  remain discoverable. These are static draft references, not a Runtime stack.
- Searches match names and IDs anywhere in the path. Duplicate workflow names
  use the existing ID-disambiguated labels. Labels wrap and retain full tooltips.
- Selecting a path still locates its immediate calling node; back navigation
  returns to the original position. Every link is revalidated before jumping.
- Queries use the existing detached live-graph capture without applying an
  open parameter editor, saving the project, or starting a job.
- Cyclic editing drafts stop at the repeated workflow and display a cycle
  marker. This does not make recursive workflows executable.
- The popup uses pixel scrolling so a single tall result can be read to its end.

## Verification

Environment: Windows, Python 3.10.21, PySide2 5.15.2.1, using
`C:\Users\dd109\.conda\envs\emo_master\python.exe`.

- Initial native Qt related regression: 104 passed, before the final scrolling
  change and its additional regression test.
- Final native Qt 100% scale: 105 passed in 146.75s, covering
  `test_workflow_reference_chains.py`, `test_workflow_history_navigation.py`,
  `test_workflow_navigation.py`, `test_workflow_dependency_tree.py`,
  `test_workflow_relationship_layout.py`, and `test_main_window_workflow_tabs.py`.
- The new oversized-result test reproduced the old per-item scrolling defect:
  one result exceeded the viewport but the scrollbar maximum remained zero.
  The test now passes with pixel scrolling, including its bottom-visibility
  assertion.
- Final native Qt 150% scale: 40 passed in 71.07s, covering
  `test_workflow_reference_chains.py` and `test_workflow_history_navigation.py`.
- Targeted Ruff default checks and F checks passed for all three changed Python
  files. `git diff --check` passed; Git emitted only an existing unrelated
  ROI UI line-ending warning.
- Inspected native Qt popup captures at logical widths 380 and 560 at 100%,
  plus width 380 at 150%. Complete paths wrap without overlapping or elision.
  Captures: `build/workflow_reference_chains_20261112/scale1/` and `scale1.5/`.
- New tests create device-free temporary projects through `WorkflowStore`;
  assert schema 2.9 / presentation 1.2; validate, compile, save, read back,
  and load through Designer's actual `loadProjectDirectory` entry point.

No full repository suite, packaged/frozen artifact test, manual user-operated
UI acceptance, or real camera/PLC test was performed. Tests use stub Runtime
clients and temporary settings/projects. The user's existing Designer was not
restarted; no commit, push, or release was performed.
