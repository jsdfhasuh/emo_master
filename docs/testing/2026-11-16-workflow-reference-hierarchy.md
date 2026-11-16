# Readable Workflow Reference Hierarchy

Date: 2026-11-16 (Asia/Shanghai). Executor: Codex.

## Source And Scope

- HEAD: `556acb10760a9463fe832aa4d8e7fc5c0a95a67e`.
- The previous complete-chain implementation is already committed at
  `9a9d0bbab8e7e20944c25ed0d34e7abf5e6787f2`. This change improves its
  presentation and navigation; it does not reimplement path discovery.
- Tested with uncommitted changes in the navigation controller, navigation
  popup, `test_workflow_reference_chains.py`, and
  `test_workflow_history_navigation.py`, plus this record.
- Existing untracked field verification records were preserved. Concurrent
  changes in `workflow_navigation.py` and workflow-tab tests, and additional
  untracked files/logs appeared during this run. They were left in place and
  excluded from this change. Results describe a dirty working tree, not
  acceptance of the clean HEAD or a frozen whole workspace.

## User-Facing Change

- Replaces two dense, horizontal strings with an expanded call hierarchy.
  Each level has a bold workflow name and a separate calling-node subtitle.
  The final level explicitly identifies the current workflow or lookup target.
- Separate paths remain separate trees, with numbered path headings when
  more than one path exists. Cyclic draft paths remain finite and marked.
- Internal IDs and translated call types are in per-step tooltips, not a
  duplicate technical path. Existing duplicate-workflow-name disambiguation
  is retained. Authored business names are not renamed or stripped.
- Clicking any level locates that level's workflow/calling node. Existing
  navigation history and full-path stale-reference validation remain active.
- Search still matches names, node/workflow IDs, and original relation labels.
  It selects the matching step while retaining its whole path for context.
  Collapsed paths expand for matches; hidden/collapsed descendants cannot
  activate through retained selection. Disclosure clicks do not navigate.
- Reuses the existing wrapped relationship-tree delegate and pixel scrolling.
  Flat definition choices continue to use a flat layout in the shared popup.

## Verification

Environment: Windows, Python 3.10.21, PySide2 5.15.2.1, native Qt
(`QT_QPA_PLATFORM=windows`). Interpreter:
`C:\Users\dd109\.conda\envs\emo_master\python.exe`.

- Final 100% scale: 113 passed in 72.83s, covering
  `test_workflow_reference_chains.py`, `test_workflow_history_navigation.py`,
  `test_workflow_navigation.py`, `test_workflow_dependency_tree.py`,
  `test_workflow_relationship_layout.py`, and `test_main_window_workflow_tabs.py`.
- Final 150% scale: 48 passed in 55.61s, covering
  `test_workflow_reference_chains.py` and `test_workflow_history_navigation.py`.
- Targeted Ruff checks and `git diff --check` pass for the changed Python files.
- Inspected native Qt captures of a six-level Chinese example at logical
  widths 380 and 560 at 100%, and 380 at 150%. Names, calling nodes, and the
  final workflow remain readable, with no overlap or horizontal scrolling.
  These are temporary/synthetic test data, not the user's running field UI.
- Captures are under `build/workflow_reference_hierarchy_20261116/scale1/`
  and `scale1.5/`, named `reference-hierarchy-380.png` and
  `reference-hierarchy-560.png`.
- Tests retain project-purity, separate/shared paths, cycle, stale-link,
  loop-reference, empty-search, and long-content assertions. Added checks
  cover per-level navigation/back, hidden technical details, expansion,
  filtering with complete context, and switching back to flat definitions.

Intermediate failures were preserved in the run results: the first broader
run had 112 passes and one assertion that depended on a trailing separator
space in the old horizontal label. The corrected test checks the exact stored
workflow name (the rename API trims it) and labeled node ID in the tooltip.
The first 150% run had 47 passes and one oversized-fixture prerequisite
failure: the newly separated row was 177px high in a 182px viewport. The
fixture name was lengthened; the oversized-row, bottom-visibility, and
final-workflow scrolling assertions remain required and pass.

No full repository suite, packaged/frozen artifact test, user-operated manual
acceptance, or real device test was performed. Temporary projects and stub
Runtime clients were used. No field project was changed, no running Designer
was restarted, and no commit, push, or package was produced.

## Latest-Main Integration

The preceding results describe the initial UI change. On the user's subsequent
request to pull and merge, `main` was fast-forwarded from
`556acb10760a9463fe832aa4d8e7fc5c0a95a67e` to
`82d05914bda9c0390a2f94bcf6b2794e59ff3484` (nine incoming commits).
There were no overlaps with modified or untracked local paths. The full tracked
binary diff was identical immediately before and after the update; no stash or
conflict resolution was needed.

Post-update validation used that exact new HEAD plus the UI changes above and
the still-uncommitted workflow-tab changes. Native Windows Qt at 100% scale:
**144 passed, one warning, in 111.47s**. The run included the six navigation
test modules above, `test_workflow_tab_add_button.py`,
`test_workflow_tab_overflow.py`, and `tests/authoring/test_designer_handoff.py`.
The two upstream handoff checks exercise export, Designer open/edit/save,
resource preservation, and compilation using isolated Runtime instances and
temporary projects, without device connections or jobs. The only warning was
Starlette's deprecated AnyIO `BlockingPortal` alias.

The updated base requires `jsonschema`; version 4.26.0 was installed in the
existing `emo_master` Python environment. Declared test dependencies `httpx`
0.28.1 and `httpx2` 2.14.0 were also installed for the handoff checks. Existing
packages were not upgraded. `pip check`, targeted Ruff, and diff checks pass.
The optional MCP package and the complete authoring/MCP suite were not part of
this integration validation.

Post-update hierarchy screenshots are under
`build/workflow_reference_hierarchy_20261116/post-merge/`; the 560px Chinese
hierarchy capture was inspected again. Only the two navigation implementation
files, their two test files, and this record are selected for the local
integration commit. Other workspace changes and field records are excluded.
No push, package, running-Designer restart, or real-device test was performed.
