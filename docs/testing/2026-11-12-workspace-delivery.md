# Workspace Delivery - 2026-11-12

Executor: Codex. Date: 2026-11-12 (Asia/Shanghai).

## Source And Scope

- Initial HEAD: `51bff936ee2f2a0e4c3712441f8e622e33312c3c`, with existing uncommitted ROI/editor, contour filtering, workflow navigation, page initialization, tests, examples and documentation changes.
- Fetched upstream: `ba30c2ab94a1fbc1d7396c12788dd695183bb88a` (PR #12). Its changed paths did not overlap the existing local changes. The four initial local commits were rebased onto this fixed upstream without conflicts; `git range-diff` confirmed all four patches unchanged. No force push or existing remote-history rewrite was used.
- Tested source HEAD: `fb54ae097cfdd3112d34cbba4be33fede5bb02b3`. The source/tests remained unchanged during the regression runs. The worktree was initially clean at that HEAD; two concurrent documentation-only changes appeared during verification and are included in the final documentation commit. This is not a claim that the whole worktree stayed clean throughout testing.
- `test/test.emoproj` and `test/test.emoproj.bak` were retained locally and excluded by exact entries in `.git/info/exclude`. They were not upgraded, deleted or published.
- Historical verification reports, including the page-switch first-failure XML, were preserved. `.gitattributes` now exempts only that page-switch directory's raw JUnit XML from text conversion and whitespace diagnostics, following the repository's existing evidence convention.
- The concurrent [259-image retest](2026-11-12-station2-1hp-firstshot-259-retest.md) and its link from the prior classification report are archived as separately performed work. Their image-chain executions are not included in the test count below.

## Commit Groups

1. `babc86cd9dd134bd1593e4e57be49c6df8d67853`: ROI composite regions and editing, reusable image widgets, editor binding placement and apply feedback, contour point filtering and merged minimum enclosing circles, examples and regressions.
2. `9a9d0bbab8e7e20944c25ed0d34e7abf5e6787f2`: complete workflow caller chains and scrolling of long results.
3. `f475f34e086c2518540908282690a9b03b85f503`: page initialization/snapshot overhead reductions, lightweight probe default and preserved verification evidence.
4. `32217fc06159e27e69898e84aebfe713c67be5ca`: historical two-station image-recipe verification records.
5. `fb54ae097cfdd3112d34cbba4be33fede5bb02b3`: local ROI typing repair found by the delivery check.
6. The documentation-only commit containing this record also includes the concurrent retest report and classification-report link.

## Delivery Repair

The first full configured mypy run reported 29 errors in the newly changed ROI editor and operator. The repair is limited to three ROI files: schema-dictionary typing, the undo-history tuple, known bound Qt widget/action/canvas casts, and the existing repository convention for PySide2 signal annotations. It does not change ROI calculations, UI behavior, preview/save rules or device behavior. No checker rule was disabled.

## Verification

Environment: Windows, Python 3.10.21, PySide2 5.15.2.1. Regression runs used `QT_QPA_PLATFORM=offscreen`, `QT_SCALE_FACTOR=1`, temporary settings/projects and test Runtime resources. The existing ignored `build/page-switch-test-deps` provided jsonschema and pytest-timeout; no application environment installation was performed in this task.

| Check | Result |
| --- | --- |
| Changed Python files, including examples and measurement script: Ruff default checks | Passed |
| `python -m ruff check src tests`, after upstream integration and typing repair | Passed |
| `python -m mypy --config-file mypy.ini src`, after repair | Passed, 619 source files; default untyped-body notes remain |
| Vision/editor/plugin/preview regressions | 658 passed in 184.88 s |
| Core presentation, page designer, shared renderer and workflow-state regressions | 992 passed in 764.60 s |
| Workflow navigation and reference-chain regressions | 105 passed in 56.47 s |
| `git diff --check` for the complete delivery range | Passed |

The three JUnit files contain **1,755 distinct passing test cases**, deduplicated by `classname + name`, with zero failures, errors or skips. All three runs completed; no interrupted or historical run is counted.

Local raw reports remain under `build/workspace-delivery-20261112/`:

| File | SHA256 |
| --- | --- |
| `vision.xml` | `47f933e1cf9467ef4724b7235eb85a85fb12d94e76b3721d5e6ca8506f57e5e6` |
| `presentation.xml` | `d700d07c12b0345c54f8487b290455df6d724504003e0d976e3ae984b0377128` |
| `navigation.xml` | `8aef1d18531578b5a127b68ce43d7d64fb132fbb086b1ae1d7b0aa73ab8f2125` |

Reproduction commands from the repository root:

```powershell
$python = 'C:\Users\dd109\.conda\envs\emo_master\python.exe'
$env:PYTHONPATH = "$PWD\build\page-switch-test-deps;$PWD\src"
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QT_SCALE_FACTOR = '1'

& $python -m pytest tests/designer/test_image_view.py tests/designer/test_roi_editor_layout.py tests/designer/test_roi_region_editing.py tests/designer/test_roi_preview_export.py tests/designer/test_roi_preview_retirement.py tests/designer/test_operator_editor_bindings_host.py tests/designer/test_operator_editor_manager.py tests/designer/test_operator_modes.py tests/designer/test_builtin_operator_editor_ui.py tests/designer/test_node_image_viewer.py tests/designer/test_operator_apply_feedback.py tests/core/plugin/test_registry.py tests/plugins/test_roi_operator.py tests/plugins/test_roi_regions.py tests/plugins/test_contour_point_filter.py tests/plugins/test_merged_contour_circle.py tests/plugins/test_geometry_bridges.py tests/plugins/test_collection_filter_configuration.py tests/plugins/test_collection_operators.py tests/plugins/test_collection_select_dynamic_index.py tests/plugins/test_operator_modes.py tests/runtime/test_draft_pure_preview.py tests/designer/test_plc_editor.py tests/sqlite_writer/test_editor_layout.py -q --disable-warnings --maxfail=3 --timeout=300 --junitxml=build/workspace-delivery-20261112/vision.xml

& $python -m pytest tests/core/presentation tests/ui/page_designer tests/ui/presentation tests/designer/test_workflow_store.py tests/designer/test_workflow_boundary_nodes.py tests/designer/test_performance_probe.py -q --disable-warnings --maxfail=3 --timeout=300 --junitxml=build/workspace-delivery-20261112/presentation.xml

& $python -m pytest tests/designer/test_workflow_reference_chains.py tests/designer/test_workflow_history_navigation.py tests/designer/test_workflow_navigation.py tests/designer/test_workflow_dependency_tree.py tests/designer/test_workflow_relationship_layout.py tests/designer/test_main_window_workflow_tabs.py -q --disable-warnings --maxfail=3 --timeout=300 --junitxml=build/workspace-delivery-20261112/navigation.xml
```

## Limits

- No full-repository pytest run, fresh native-window/scaling screenshot acceptance, package/frozen-artifact test, manual UI acceptance, real-camera/PLC test or production execution was performed in this delivery task.
- Historical native Qt, performance and field-image records retain their original scope and dates; archiving them is not a rerun or a new field-acceptance claim.
- No field project, model, calibration, credential or device address was changed by this delivery task. Existing Designer/Runtime processes were not restarted.
- Remote CI completion is not part of these local results. No release or deployment was performed.
