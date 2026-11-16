# Workspace Delivery

Date: 2026-11-16 (Asia/Shanghai). Executor: Codex.

## Source And Scope

- Starting HEAD: `dfd196596bcfaace785c1b90cb68f5da92a2d36b`, the local
  caller-hierarchy commit on top of origin/main
  `82d05914bda9c0390a2f94bcf6b2794e59ff3484`.
- At validation time the worktree additionally contained the workflow-tab
  overflow implementation and its tests, nine untracked historical field
  verification reports, and the tab verification report/artifacts. Results
  are not attributed to the clean starting commit.
- The tested tab implementation is committed as
  `969533ab07594d11a7cf74b64697504a8ea5ae2e`. Its functional source/test
  content was not changed during this cleanup.
- The caller hierarchy, tab change, and a separate documentation commit are
  selected for the authorized push to main. No field project, model,
  configuration, device credential, or deployment package is included.

## Organization

- Preserved nine previously untracked field reports dated November 13/16,
  including successful measurements, original failures, subsequent user
  decisions, and unverified items. Their recorded historical HEADs, external
  evidence paths, and acceptance boundaries remain unchanged. This task did
  not re-run those field-image checks or apply their proposed project edits.
- Archived all 31 original tab regression XML/screenshots from `logs/` to
  `docs/testing/2026-11-16-workflow-tab-overflow/evidence/`. SHA256 values
  are identical before/after moving. Git index blobs were also checked against
  raw file bytes. Original failed runs and baseline comparisons are retained.
- Added narrowly scoped `.gitattributes` rules, following existing repository
  evidence conventions, to preserve exact bytes and pytest diagnostic spaces.
  No global whitespace rule, source formatting, or failure assertion changed.
- Final JUnit reports and four representative screenshots are preserved under
  `docs/testing/2026-11-16-workspace-delivery/`, also hash-checked on move.
  Other generated build artifacts remain ignored locally; nothing was deleted.

## Current Verification

Environment: Windows, Python 3.10.21 / PySide2 5.15.2.1, native Qt
(`QT_QPA_PLATFORM=windows`). Interpreter:
`C:\Users\dd109\.conda\envs\emo_master\python.exe`.

- 100% scale: **176 passed, 3 warnings, in 157.00s**, zero failures/skips.
  Modules: `test_workflow_reference_chains.py`,
  `test_workflow_history_navigation.py`, `test_workflow_navigation.py`,
  `test_workflow_dependency_tree.py`, `test_workflow_relationship_layout.py`,
  `test_main_window_workflow_tabs.py`, `test_workflow_tab_add_button.py`,
  `test_workflow_tab_overflow.py`, `test_workflow_entry_markers.py`,
  `tests/ui/page_designer/test_page_navigation.py`, and
  `tests/authoring/test_designer_handoff.py`.
- 150% scale: **51 passed in 55.08s**, zero failures/skips, covering
  reference chains, tab commands, and tab overflow. This focused run does not
  replace or reclassify the original tab report's two screen-limited 150%
  resize failures; those original failures and baseline evidence are archived.
- The three 100% warnings are Starlette's deprecated AnyIO `BlockingPortal`
  alias and two pytest `record_property`/JUnit xunit2 compatibility warnings.
  They are not reported as test failures or suppressed.
- Targeted Ruff for the tab and hierarchy implementation/test files passed.
  `git diff --check` and `pip check` passed. Raw pytest evidence is explicitly
  preserved rather than reformatted to remove diagnostic trailing spaces.
- Inspected the 150% Chinese hierarchy and 100%/150% overflow-strip captures.
  UI tests use temporary projects/settings; handoff tests use isolated Runtime
  instances. No live device connection or job is exercised by this validation.

No complete repository suite, new MCP transport acceptance, packaged/frozen
artifact test, user-operated manual UI acceptance, physical measurement,
camera/PLC/robot test, or production deployment was performed. The user's
running Designer and external field project were not restarted or changed.

## Publication

Push only the reviewed local commits using an ordinary fast-forward update;
do not force-push. Verify local HEAD equals the remote main SHA and that the
worktree has no remaining tracked/untracked changes. Add a corresponding
GitHub commit comment naming Codex and summarizing actual changes, passed
checks, and unperformed acceptance. Remote CI results, if any, are separate
from the local checks recorded here.
