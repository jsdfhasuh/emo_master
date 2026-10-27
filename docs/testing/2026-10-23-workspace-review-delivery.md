# Workspace Review and Delivery

## Scope and Fixed Baseline

- User scope: review, organize, commit, and push all pending public code, tests,
  examples, and documentation, not only the earlier workflow-layout comparison.
- Baseline: `a50bfc8809faaf876a37fe979c7588a07edeb784` on
  `agent/runtime-workflow-architecture-v1`.
- Reviewed candidate: 122 files, explicitly staged from the initial inventory.
- Validated source tree: `561644ba7861a356af97bc17c506fedc042b96e6`, exported with
  `git archive` to `D:/tmp/EmoAudit-561644ba7861`. This report is added afterward;
  source, tests, and example trees must remain identical to the validated tree.
- Python 3.10.21 / PySide2 5.15.2.1, using the repository's `emo_master` Conda
  environment. All new test executions use temporary Runtime data and fixtures.

## Review Results

- Fixed one confirmed static-check failure: the new status button accessed
  `PageCoordinator | None` without narrowing it. Its existing native-Qt setup
  now also requires a non-null coordinator. No UI behavior or device access
  policy was changed by this review fix.
- Reviewed file-list/video inputs, input-aware parameter validation, local
  integer loop state, optional selection indices and legacy-node wiring,
  RTSP/audio operators, detection archives, bounded asynchronous snapshots,
  device status, job duration, failure dialogs, and resource preflight.
- Checked cancellation and retirement, no replay after uncertain audio writes,
  credential presentation, source-resource overlap protection, backward port
  contracts, explicit Runtime publication, and schema-2.7 examples.
- Retained prior evidence and accepted limitations. Historical real-camera,
  model, and performance reports are not new hardware acceptance in this review.
- Candidate text scans found only loopback addresses, invalid test hosts,
  documented placeholders, and fake fixture credentials. Public screenshots
  show synthetic states; private media, models, credentials, and projects are
  not part of the candidate.

## Validation Record

- All 29 new/modified test modules: **383 passed, 5 skipped in 206.20 s**.
  Four offscreen skips are the native popup cases independently run below;
  one requires an explicitly supplied private face-detection bundle and was
  not run. It is not counted as a pass.
- Additional compiler/validation/loop-mapping regression: **60 passed in
  7.11 s**. The checked-in video example also passed temporary save/reload and
  compilation without changing the original file.
- Shared-path regression across 24 existing modules: **378 passed in 248.42 s**,
  covering project resources and publication, preview stores and origins,
  inspection, snapshot policy, worker heartbeats and finalization barriers,
  Job lifecycle, operator modes, controller behavior, and configuration UI.
- `ruff check src tests examples`: passed in the isolated candidate.
- `mypy src`: passed for 527 source files after the coordinator fix; the initial
  one-error output is retained separately.
- `python scripts/gen_proto.py --check`: passed in the isolated candidate.
- `python scripts/dev.py run-designer --local --check`: passed; no window or Job
  was started by this import/startup check.
- Native hidden Windows QMessageBox rendering: 4 passed at 100% and 4 passed
  at 150%; resource lists, long errors, complete details, and buttons checked.
- 150% mixed offscreen UI run: 62 passed, 1 failed. The failure expected a
  620-pixel logical dialog on an offscreen screen only about 533 pixels wide;
  existing screen fitting correctly limited it to 509. The 460-pixel case
  passed. No product sizing behavior or assertion was weakened.
- The complete file-pattern form module then passed 22 tests on the hidden
  Windows platform at 150%, including the original 620/460-pixel assertions.
  Actual screenshots of the forms, status bar, tooltips, and dialogs were read.

## Retained Initial Failures

The first broad run was started against the mixed working directory, which
also contains ignored historical migration tests and private project fixtures.
It was interrupted after repeated device-contract failures, not counted as a
completed or passing suite. The representative failure was reproduced against
the exact baseline source archive with a copied private fixture: the schema-2.5
fixture lacks device slots required by current operator declarations. One
initial baseline command also found no test file because that historical test
is intentionally absent from Git; copying the local test into the temporary
baseline reproduced the same failure. No original fixture was migrated or
changed to make the test pass. Public-candidate tests run from the staged archive
instead, without silently adding these ignored local tests to the delivery.

A whole-public-tree invocation collected 7,478 tests but was also interrupted
before completion. Its partial progress is not counted as a passing suite.
This delivery uses the completed changed-module, shared-path, compiler, and
native-Qt runs; it does **not** claim a complete full-suite or remote-CI pass.

Raw logs and temporary fixtures for this audit are retained under
`D:/tmp/emo-review-20261023` and the `D:/tmp/EmoAudit*` test directories. They are
not public deliverables. Two already-existing historical evidence files retain
their original trailing whitespace (`before-fix.txt` and `source-changes.diff`);
`git diff --cached --check` passes for all other candidate files. The historical
logs were not rewritten to hide their original output.

## Preservation and Acceptance Boundary

A SHA-256 inventory covers 9,712 enumerated initial pending and private files.
Verification found no missing files; besides the reviewed `main_window.py` fix,
the initial mixed-directory test regenerated one ignored Python bytecode cache
under `field_tests/plc_dual_mc/__pycache__`. No protected project, model, resource,
or source was changed. Git reported several overlong ignored historical paths
during enumeration, so the inventory is not claimed to cover every local byte.
No cleanup deletion, reset, force push, field connection, package build, release,
deployment, or running-application restart was performed. Existing applications
do not hot-load these changes; source applications need reopening and packaged
applications need a separately authorized rebuild.

## Commit Organization

- Functional implementation, tests, and examples: 86 files in
  `89fe65b48ee232ea041c6a705db5e448d5fe4f36`
  (`feat: deliver workflow operators and runtime diagnostics`).
- Documentation and retained evidence are a separate following commit, including
  this audit. The final source, test, example, and protobuf content is compared
  byte-for-byte through Git trees against the validated candidate before push.
- Delivery uses a normal push to the existing branch, with no merge, rebase,
  force push, Release, or deployment. Live remote SHA and ahead/behind counts
  are checked after pushing; remote CI is not claimed complete by this report.
