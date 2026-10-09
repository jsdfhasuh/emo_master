# Workflow relationship visualization

Scope: Designer relationship tree presentation and call-site navigation only.
The project schema, workflow reference model, transfer derivation, and Runtime
loop execution are unchanged by this UI change. Existing workspace edits are
preserved; this is not a packaged release or a production acceptance report.

## Behavior

- Show workflow -> calling node -> target workflow, grouping While condition
  and body references under the same controller node.
- Order While targets as condition, then body, followed by the false exit path.
- Keep data routes collapsed initially; retain expansion, selection, and scroll
  position during refreshes.
- Clicking a caller opens its owning workflow and selects/centers that node.
  Clicking a target opens the target workflow; data rows do not navigate.
- Separate names from role/status subtitles; use existing Lucide icons and
  distinguish loop controllers, workflows, and data rows.
- Measure and paint the same wrapped text, with explicit icon space. Escape
  workflow labels before formatting and preserve Qt Unicode line separators.

## Verification

Environment: Windows, Python 3.10 in the emo_master Conda environment, PySide2.

- PASS: Windows native Qt regression, 108 tests in 15.97 seconds. Includes
  dependency tree, relationship layout, workflow store/boundaries/tabs,
  sidebar navigation/collapse, floating toolbox, visual layout, and actual
  image-batch While Runtime tests.
- PASS: offscreen relationship/dependency tests, 29 tests in 2.94 seconds.
- PASS: Windows native Qt relationship/dependency tests at 150% scaling,
  29 tests in 7.40 seconds.
- PASS: Ruff on the two changed UI modules and two changed test modules.
- PASS: mypy on the two changed UI modules, including followed imports.
- PASS: git diff --check (existing CRLF conversion warnings only).
- NOT_RUN: full repository test suite, packaged application, hardware acceptance.

Layout tests cover 240/308/430 logical-pixel widths, long unbroken strings,
9/12/16-point text, resizing, expanded routes, caller navigation, duplicate
call-site names, non-While loop modes, and view-state preservation.

Screenshots are widget captures from a Windows native Qt test window running
the actual Designer code with a stub Runtime client and the image-batch While
example. They are not mockups or captures of the user's existing application.

- `while-call-hierarchy.png`: initial overview, with data routes collapsed.
- `while-selected.png`: selected workflow and controller hierarchy.
- `while-details-240.png`: expanded data at a narrow logical width.

## Reproduction

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$env:PYTHONUTF8 = '1'
& C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe -m pytest tests/designer/test_workflow_dependency_tree.py tests/designer/test_workflow_relationship_layout.py tests/designer/test_workflow_store.py tests/designer/test_workflow_boundary_nodes.py tests/designer/test_main_window_workflow_tabs.py tests/designer/test_sidebar_node_navigation.py tests/designer/test_sidebar_collapse.py tests/designer/test_floating_toolbox.py tests/designer/test_visual_layout.py tests/runtime/test_image_batch_while.py -q
```
