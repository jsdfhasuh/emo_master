# 2026-10-27 Workspace Delivery Verification

## Scope And Source

This delivery follows the request to organize, commit and push the existing
workspace. It does not add a new feature milestone, migrate a field project,
build an executable, or deploy/restart a running application.

- Branch: `agent/runtime-workflow-architecture-v1`.
- Starting local and fetched remote SHA: `331c4d3586cb5cd5141871fbe3da353bca9a56b6`.
- Integrated code SHA: `7cb0fa7df5ff714edd753419b92fe8f1cb233390`.
- The four commits below contain 115 files. Verification used their combined
  working-tree contents, not four independently tested intermediate revisions.
- A final documentation-only commit adds this record.

| Commit | Scope |
| --- | --- |
| `1786bc4d00800db59f72745d0c2948819cbbe44c` | Per-launch Designer settings, local data roots, read-only external Runtime connection, startup/frozen-harness tests and documentation |
| `087c4dafa7ba8117273318cb0ffb551d540dc006` | YOLO classification, elapsed time and percentage operators, debug integration, Pillow dependency, transform bounds and ONNX spinning optimizations |
| `8e63d7494e443f364150252b77ea82bb7a020a88` | Asynchronous inspection encoding/adoption, event batching, bounded preview work, exact execution identity and related tests |
| `7cb0fa7df5ff714edd753419b92fe8f1cb233390` | Designer/log refresh, result history, shared-page capture, image loading continuity, rendering and historical verification reports |

Existing source changes were preserved. This turn changed only three integration
tests and removed six trailing-whitespace occurrences from two historical text
outputs. No production behavior was changed during delivery verification.

Explicit file lists were staged and compared with the intended group before each
commit. The final staged whitespace check passed. A scan of the initial delivery
files found no credential-bearing RTSP/HTTP URLs, common token prefixes, or
private-key headers; this is a bounded pattern check, not a comprehensive secret
audit. Ignored field projects, models, credentials, local data, private tests and
build artifacts were neither committed nor deleted.

Historical reports retain their time-scoped statements such as "not committed"
or "not built". Those describe the original task; this table records the later
source delivery without rewriting their earlier evidence.

## Environment And Results

Windows; Conda `emo_master` Python 3.10.21, PySide2 5.15.2.1, pytest 9.1.1,
gRPC 1.78.0 and Pillow 12.3.0. Tests use temporary Runtime roots, temporary
settings, simulated devices and loopback services.

Raw logs and JUnit XML remain under the ignored local directory
`build/workspace-delivery-20261027/`.

| Check | Result | Evidence / scope |
| --- | --- | --- |
| Protobuf consistency | PASS | `python scripts/gen_proto.py --check` |
| Ruff | PASS | `src tests scripts/dev.py scripts/verify_frozen_runtime.py` |
| Mypy | PASS, 560 source files | Repository `mypy.ini`; default limitations on untyped function bodies still apply |
| Designer source startup check | PASS | `scripts/dev.py run-designer --local --check`; no window or Job started |
| Backend first run | 246 passed, 3 failed; 156.90 s | 21 modules, `backend-first.log/xml`; failures described below |
| Backend after assertion updates | 249 passed; 148.30 s | Same 21 modules, `backend-final.log/xml`; no skipped tests |
| Combined native Windows Qt, 100% | 419 passed, 4 failed; 246.59 s | 27 modules, `gui-native.log/xml`; not an all-green run |
| Isolated native log regression, 100% | 89 passed; 17.68 s | `log-native-isolated.log/xml`; unchanged tests and source |
| Isolated offscreen log regression | 89 passed; 23.69 s | `log-offscreen.log/xml`; unchanged tests and source |
| Isolated native log regression, 150% | 89 passed; 37.59 s | `log-native-150.log/xml`; unchanged tests and source |
| Staging checks | PASS | Exact file lists and `git diff --cached --check` |

The repeated log suites overlap with each other and the combined suite. Do not
add their counts into a claimed unique total or use them to erase the mixed
suite's four failures. Source/test file hashes were rechecked before the final
code commit: only the two documented historical text outputs differed.

## Failures And Disposition

### Integration assertions corrected

The first backend run reproduced three outdated expectations:

1. `testAllBuiltinParameterTitlesSurviveRegistration` still expected 78 builtins,
   while all 81 manifests registered successfully. Update the exact count to 81;
   preserve manifest/registration equality, no-rejection and parameter-title checks.
2. `testRuntimeRegistersBuiltinsAfterStrictValidation` also expected 78.
   Update to 81 and explicitly require the classification, elapsed-time and
   percentage IDs. Preserve the no-rejection assertion.
3. `testUncancelledSpawnedDeviceActionFailureStaysFailed` omitted the newly added
   `inspectionFailed: 0` statistic. Add that exact field to the complete expected
   terminal dictionary. FAILED status, original error, UNKNOWN outcome and
   diagnostics remain checked; this is not a relaxed subset comparison.

The full 21-module backend rerun passed. The earlier startup report's corresponding
assertion failure remains in that report as historical evidence.

### Native hover verification remains open

All four failures in the combined GUI run came from the parameterized
`testOpenFilterKeepsHoveredChoiceAndScrollAcrossLiveBatches` at
`tests/designer/test_runtime_log_refresh.py:79`:

- `False-nodeFilter`: hovered row 11 instead of 7.
- `False-eventTypeFilter`: hovered row 9 instead of 7.
- `True-jobFilter`: hovered row 8 instead of 7.
- `True-workflowFilter`: hovered row 6 instead of 7.

The same assertions passed in separate native 100%, native 150% and offscreen
processes without source or test edits. The combined failure was not rerun as a
full 27-module batch. Its cause is **not established**; neither native input
interference nor a product defect is claimed proven. Keep this as an open,
non-blocking verification uncertainty for this source submission, not a fixed
bug or a silently skipped check. No assertion, retry loop or product behavior was
changed to obtain the isolated passes.

Earlier reports also retain their prior mixed-offscreen access violations,
setup failures and unaccepted performance results. This delivery does not
resolve or relabel those historical findings.

### Historical output formatting

Only six trailing-space runs on pytest's `E` separator lines were removed from
`2026-10-26-runtime-log-refresh/before.txt` and `first-fix.txt`. All failure
messages and counts remain. Byte-for-byte originals are preserved locally:

| Original copy under `build/workspace-delivery-20261027/historical-originals/` | SHA256 |
| --- | --- |
| `before.txt` | `a3a2cc7547e89334a263bdfa817e7787dd8fb6d70fbea9f34cd36fc841fd08a7` |
| `first-fix.txt` | `17d0e0873b110fc846e437d6ba3d5e27f9a793e1ceb72b951c179af2fead16be` |

## Reproduction

Use the repository's Python 3.10 development environment. The fixed commit range
selects the same changed public tests; explicit neighbors complete the tested
21-module backend and 27-module GUI collections.

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$env:QT_SCALE_FACTOR = '1'
$env:PYTHONIOENCODING = 'utf-8'
$changed = @(git diff --name-only 331c4d3586cb5cd5141871fbe3da353bca9a56b6..7cb0fa7df5ff714edd753419b92fe8f1cb233390 -- tests |
    Where-Object { $_ -match '(^|/)test_[^/]+\.py$' })
$backend = @($changed | Where-Object { $_ -match '^tests/(runtime|plugins)/' })
$backend += @(
    'tests/core/plugin/test_builtin_parameter_titles.py',
    'tests/runtime/test_preview_snapshot_store.py',
    'tests/runtime/test_preview_snapshot_origins.py',
    'tests/runtime/test_event_bus.py'
)
$backend = @($backend | Sort-Object -Unique)
$gui = @($changed | Where-Object { $_ -match '^tests/(designer/|ui/|test_)' })
$gui += @(
    'tests/designer/test_runtime_connection_ui.py',
    'tests/designer/test_startup_project_entry.py',
    'tests/designer/test_startup_load_feedback.py',
    'tests/designer/test_runtime_validation_feedback.py',
    'tests/designer/test_runtime_qt_ownership.py',
    'tests/designer/test_run_failure_feedback.py',
    'tests/test_dev_launcher.py',
    'tests/test_windows_package_entry.py'
)
$gui = @($gui | Sort-Object -Unique)
$base = Join-Path $env:TEMP ('emo-delivery-backend-' + [guid]::NewGuid().ToString('N'))
python -m pytest -q @backend --tb=short -ra --timeout=90 --basetemp=$base
$base = Join-Path $env:TEMP ('emo-delivery-gui-' + [guid]::NewGuid().ToString('N'))
python -m pytest -q @gui --tb=short -ra --timeout=90 --basetemp=$base
```

The isolated log collection is
`test_runtime_log_refresh.py`, `test_log_dialog.py`,
`test_runtime_log_names.py` and `test_runtime_log_usability.py`, all under
`tests/designer/`. Run it in separate processes with the platform/scale values
shown in the table and a fresh temporary directory for each run.

## Boundaries

- No full-repository pytest run, frozen EXE test, package build, installation,
  deployment, human workflow acceptance or new live throughput benchmark.
- No real camera/PLC access, network readdressing, field project migration,
  Runtime configuration publication, or user-process restart.
- Source applications need a normal restart to load these changes; existing
  packaged EXEs require a separate rebuild.
- The intended push is a normal fast-forward update of the existing feature
  branch, without a tag, release, force push or merge into main.
- Remote CI must be checked against the actual pushed SHA. Local results do not
  establish a successful remote CI run.

## Ubuntu CI Collection Follow-Up

The Ubuntu job for delivered SHA
`1ca69d3697c54a300237a81eca55fe15cf05bdf6` failed during pytest collection,
after dependency installation, protobuf validation, Ruff and Mypy had passed.
Run: [40443182615, Ubuntu job 121988098759](https://github.com/jsdfhasuh/emo_master/actions/runs/40443182615/job/121988098759).
It collected 7910 items before stopping with one `import file mismatch` error.

The new `tests/designer/test_startup_settings.py` collided with the existing
`tests/ui/operator_view/test_startup_settings.py` under pytest's default import
mode. Collecting those two files together reproduced the same error locally on
Windows, so this is not an Ubuntu dependency or operating-system-only failure.
The previous targeted delivery batch did not include the operator-view file and
therefore missed this collection interaction.

The fix renames only the new Designer module to
`tests/designer/test_designer_startup_settings.py`. Git recognizes a 100% rename:
test content and assertions are unchanged. No test is removed or skipped, and
neither the global pytest import mode nor product code is changed. Historical
pre-rename paths in earlier reports remain evidence of their original commands.

Follow-up verification on Windows, Python 3.10.21 and offscreen Qt:

- Before rename: the two-file collection reproduced one import mismatch.
- After rename: both modules executed together, **32 passed in 7.73 s**.
- All **619 tracked public test modules** collected successfully:
  **8241 tests collected in 64.22 s**, no collection errors. This uses tracked
  files matching pytest's `test_*.py` / `*_test.py` patterns and excludes local
  ignored/private tests and unrelated new untracked files.
- Ruff passed for both startup-setting test modules; staged whitespace checks
  passed. The 8241 tests were collected, not all executed in this follow-up.

Raw remote failure output and local before/after evidence are retained under
`build/ubuntu-ci-20261027/`: `ubuntu-1ca69d36.log`, `collection-before.log`,
`startup-pair.log/xml` and `collection-all-tracked.log`. The original remote
failure and the earlier native-hover uncertainty are not relabeled as passes.
A new push reruns CI on the corrected commit; collection success locally does
not establish full Ubuntu or Windows CI success.

## CI Execution Follow-Up

Run [40444071101](https://github.com/jsdfhasuh/emo_master/actions/runs/40444071101)
tested `970107639a23a665c10e840cbbc1fd7f32e61c6d`. The collection collision above
was resolved. Dependency installation, protobuf validation, Ruff and Mypy passed
on both platforms; pytest then reached the same execution failure on both:

`tests/core/project/test_device_resources.py::testProductionReopenUsesCommittedVersionAndRefusesInPlacePackageUpdate`

At line 391 the test expected `installPackage()` to raise `ValueError` containing
`no files changed`, but installation succeeded. Ubuntu reported **1 failed,
454 passed in 42.69 s**; Windows reported **1 failed, 454 passed in 50.60 s**.
This is a test-contract failure, not a binary compilation failure or an
Ubuntu-only dependency problem. `scripts/ci_check.py` uses `pytest -x`, so each
run stops at its first execution failure. The preceding collection-only check
did not execute this assertion.

Commit `f4fc44b6006bfa66f9279f9606a4075ea169e9d2` deliberately added explicit
project reload/switch and stopped package updates. The controller, current
production regression tests and `docs/operator-runtime.md` already agree on
that contract, but this older test still expected all three operations to be
refused or to ignore the selected disk configuration. Product behavior is not
reverted to those obsolete restrictions.

The renamed test now verifies successful stopped installation with the same
directory lease, unchanged persisted project content and consistent effective
device configuration. It explicitly uses `restoreOnline=True` to check that
remembered startup retains the committed version without overwriting a disk
draft, then checks that explicit reload applies that draft and explicit switch
adopts the selected directory and releases the previous owner. Effective
Runtime device settings include normalized policy defaults; persisted project
content is compared with the original persisted document, not that normalized
execution representation. Existing running-job rejection and failed-update
rollback tests remain unchanged and are included in the regression.

Verification on Windows, Python 3.10.21, offscreen Qt:

- The original failing test reproduced locally: **1 failed in 2.16 s**.
- The corrected test passed alone: **1 passed in 2.74 s**.
- A clean `git archive` export of `97010763`, with only the corrected test
  copied in, executed all of `tests/core` plus the five modules below without
  `-x`: **681 passed, 1 skipped in 205.15 s**. This excludes concurrent,
  uncommitted device/RTSP work and ignored/private tests from the tested source.
- The existing `testSymlinkCannotEscapeRoot` skipped because symlink creation
  was unavailable in this Windows environment. It is not counted as passed.
- On the same clean export, Ruff passed for `src tests`, Mypy passed for
  **560 source files**, and `scripts/gen_proto.py --check` passed.

The additional regression modules were:

```text
tests/runtime/test_production_project_configuration.py
tests/runtime/test_production_boundaries.py
tests/runtime/test_project_configuration.py
tests/runtime/test_initial_project_configuration.py
tests/ui/operator_view/test_production_configuration_window.py
```

Raw logs are retained under `build/ci-followup-40444071101/`: `ubuntu.log`,
`windows.log`, `local-before.log`, `contract-final.log` and
`clean-core-regression.log`. Intermediate test-refinement failures involving
raw versus normalized device policies are also retained in
`project-regression.log`, `project-regression-final.log` and
`contract-refinement.log`; the final clean result does not erase those runs.
Only this test and this report are included in the follow-up commit. The full
8241-test collection was not executed in this local follow-up; remote CI must
still finish on the new pushed SHA before it can be called green. No package
build, deployment, real camera/PLC test or user-process restart was performed.

## Small-Canvas CI Failure and Main Integration

Run [40457978617](https://github.com/jsdfhasuh/emo_master/actions/runs/40457978617)
on `d2b8ee8c10171e725478336ed816341d611d1fe7` passed the corrected project test,
then stopped at
`tests/designer/test_floating_toolbox.py::testShortCanvasCanScrollToEveryCategoryAndCard`.
Ubuntu reported **1 failed, 1358 passed in 350.79 s**; Windows reported
**1 failed, 1357 passed, 1 skipped in 503.00 s**. Both failures asserted that a
scrolled category button had an empty visible region.

The toolbox module alone initially passed locally (**20 passed in 6.03 s**),
but running it after `test_canvas_navigation_bounds.py`, which loads a real
Windows font, reproduced the failure (**1 failed, 36 passed in 19.53 s**).
Geometry inspection with real text metrics established a product layout bug:
the short window's visible canvas settled to 75 pixels, while the fixed header
and tab bar left a zero-height library viewport. Missing fonts in an isolated
Windows offscreen process masked the issue; this was not treated as proof that
the CI assertion was wrong.

The short expanded toolbox now places its drag handle and close button beside
the tab bar, removing the separate title row. Normal-height and collapsed
layouts restore their original header controls. The fixed-size drag handle now
provides a size hint so Qt positions it in the tab corner, not over the scroll
body. Layout-only resize events preserve overlay stacking instead of raising
inactive panels over the user's active toolbox. The regression checks the
close button's center remains visible, and reuses the
existing readable-font fixture, requires room for a complete category button,
and tests restoration after enlarging the canvas. Screenshot inspection caught
an intermediate 14-pixel viewport despite passing the old nonempty assertion;
the final 640x319 rendering has a 43-pixel viewport and fully readable category
buttons. The 1280x720 rendering restores the normal header and 325-pixel body.

Final regression on a clean public-source export with the follow-up files:

| Environment | Result |
| --- | --- |
| Windows offscreen, scale 1 | 62 passed in 27.84 s |
| Native Windows Qt, scale 1 | 62 passed in 30.73 s |
| Native Windows Qt, scale 1.5 | 62 passed in 54.60 s |
| CI command/exit-status unit tests | 3 passed in 0.19 s |
| Ruff on src, tests and scripts/ci_check.py | Passed |
| Mypy | Passed, 560 source files |

The GUI collection covers canvas navigation, toolbox, floating run panel and
floating page workspace. The original and intermediate logs and screenshots
remain under `build/ci-followup-40444071101/`; final evidence includes
`toolbox-release-offscreen-1.log`, `toolbox-release-windows-1.log`,
`toolbox-release-windows-1.5.log`. Intermediate compact-layout screenshots are
`toolbox-final-319.png` / `toolbox-final-720.png`; the subsequent drag-handle
placement/click/restore checks are included in the delivery test logs.

CI now executes past ordinary pytest failures instead of using `-x`, retains
the existing per-test timeout and failure exit status, and writes
`build/ci-results.xml`. Each platform uploads its JUnit report even when the
check fails. This improves failure visibility; it does not skip tests, accept
failures, remove timeouts or establish full-suite success locally.

The user authorized continued CI repair, integration into `main` and branch
cleanup. [PR #3](https://github.com/jsdfhasuh/emo_master/pull/3) was opened as a
draft and attached to this chat. Actual head and merge-candidate Ubuntu/Windows
checks and frozen-package validation must pass before merging, followed by
verification of main's CI. A
10-minute thread follow-up is active while this work remains incomplete.

Before branch cleanup, `git bundle create --all` produced
`build/ci-followup-40444071101/branches-before-main.bundle`; `git bundle verify`
confirmed a complete-history bundle. The remote
`fix/windows-package-validation-20261004` branch at `759227a1` was deleted only
after confirming it is an ancestor of `origin/main` (`32c52fa6`) and is not
checked out. No other branch was deleted at this stage:

- Four `agent/diagnostic-*-20261020` branches still contain unique diagnostic
  commits; their changes are not silently merged or discarded.
- `codex/camera-draft-preview` has unique commit `3f6a256a` and a separate
  worktree; it is retained pending review.
- `codex/flow-edge-readability` is already included in main but its worktree
  retains untracked validation images/XML/JSON, which remain untouched.
- The primary checkout's uncommitted device/RTSP work, stashes and tags remain
  intact. The source branch and main have not yet been merged or removed.

### Frozen Startup Selector Follow-Up

PR run [40458907340](https://github.com/jsdfhasuh/emo_master/actions/runs/40458907340)
built the Windows product from `d2b8ee8c` successfully. Frozen icon verification
passed, but native Designer startup verification failed while locating the
local-mode control. Runtime verification was **NOT_RUN**, not passed. Raw
failure output is retained as `frozen-pr3-before.log` in the same evidence root.

An isolated native source dialog reproduced the selector failure and exposed
the actual Windows UI Automation roles: the checkable local-mode `QPushButton`
is a `CheckBox`, while the non-checkable Start button is a `Button`. The harness
incorrectly required `Button` for both. It now selects local mode with the
observed `CheckBox` role. The portable guard test also resolves mocked controls
by both title and control type rather than ignoring the type.

The native probe's new selector resolves the control, but its physical click
encountered a local `SetCursorPos` desktop/input failure. This is retained in
`startup-uia-after.log` and is not reported as successful end-to-end native or
frozen validation. `startup-uia-before.log` retains the original lookup failure.
Temporary UIA clients were installed only under the ignored evidence directory,
not into the shared project environment. The probe used its own temporary
preferences and bounded dialog process; it did not start a Runtime or devices.

The frozen harness, release workflow, Designer startup settings and CI-command
regressions passed together: **38 passed, 2 subtests passed in 2.04 s**
(`frozen-startup-regression.log`). These are source/guard tests, not a rebuilt
EXE acceptance. PR #3 remains a draft pending full remote CI and frozen-package
validation, and the recurring follow-up now explicitly includes those checks.

### Frozen Startup Error Accessibility Follow-Up

Frozen run [40462530091](https://github.com/jsdfhasuh/emo_master/actions/runs/40462530091)
on `a4c243f8ce5153668e7919d90cda689d2c067b21` built successfully and passed
icon verification. The corrected local-mode selector reached project entry in
the first Designer. The second Designer returned to startup settings when its
Runtime database initialization encountered the occupied data directory, but
`busy_designer_settings` timed out while reading the expected warning.
Runtime frozen acceptance was **NOT_RUN**, not passed.

The current base `7a296c92fd6ee26e17e10ca9f9e92ebff0cdaf08` adds only the
separate version bump from 0.6.2 to 0.6.3. Its frozen run
[40462719449](https://github.com/jsdfhasuh/emo_master/actions/runs/40462719449)
also built successfully, passed icons and reproduced the same Designer timeout.
Both runs' logs and artifacts remain under `build/ci-followup-40444071101/`
as `frozen-a4c243f8.log`, `frozen-a4c243f8-evidence/`, `frozen-7a296c92.log`
and `frozen-7a296c92-evidence/`.

An isolated native source probe established that the error QLabel's fixed
accessible name hid the actual warning from Windows UI Automation; its HelpText
was also empty. Removing that redundant name restores QLabel's default
accessible text, including the occupied directory and instruction not to delete
the lock/database. The visible message, directory lease, startup retry flow and
frozen verifier's strict assertions are unchanged. The new QAccessible
regression checks the initial busy-directory message, a replacement error and
clearing the error when another directory is selected without creating it.

Verification on Windows, Python 3.10.21:

- The new regression failed before the fix: **1 failed in 1.35 s**.
- Native source UIA with the unchanged real harness predicate failed before
  the fix and recognized the warning afterwards. This isolated dialog probe
  formats the real busy-directory exception but does not start Runtime/devices.
- A clean public-source export updated to `7a296c92`, with only the two scoped
  follow-up files copied in, passed **93 tests and 2 subtests in 55.10 s** with
  offscreen Qt and **93 tests and 2 subtests in 53.93 s** with native Windows Qt.
  Coverage includes startup settings, directory ownership, connection,
  cancellation/progress, the frozen harness, release workflow and CI guards.
- Ruff passed for `src tests`; Mypy passed for **560 source files**;
  `scripts/gen_proto.py --check` passed on the same clean export.

Local evidence includes `startup-error-accessibility-before.log`,
`startup-busy-uia-before.log`, `startup-busy-uia-after.log`,
`startup-accessibility-regression-offscreen.log` and
`startup-accessibility-regression-windows.log`. These are source/native UIA
results, not a rebuilt frozen EXE acceptance. Full head/merge-candidate CI and
frozen checks must still pass on the new commit before PR #3 can be merged.
Uncommitted RTSP/device work and separately started release activity remain
outside this fix; no branches were removed during this follow-up.

## Authorized Local Device Changes Delivery

After startup-accessibility commit `c33289e414cc5fea84c9d8104cf05272c3ecc4b6`
was pushed, the user explicitly requested committing and pushing the remaining
local changes. The previously excluded device work is now included in this
delivery: bounded RTSP connection/stream recovery, lifecycle state after clean
release, connection-test completion dialogs and their regression tests/docs.
The earlier reports retain their original scope and failure evidence in
`2026-10-27-device-connection-test-feedback.md` and
`2026-10-27-rtsp-connection-recovery.md`.

Verification of the combined working tree based on `c33289e4`, using Python
3.10.21 on Windows:

- Runtime/device regression: **129 passed in 62.34 s**, no skips. This covers
  managed RTSP, recovery, lifecycle, manager/executor, SLMP, driver contracts
  and registry tests with fake devices and local loopback fixtures.
- Native Windows Qt: **150 passed in 58.90 s**, no skips. This covers connection
  feedback including actual result-dialog rendering, device workspace/form
  behavior, RTSP UI and the recent Designer startup settings/accessibility fix.
- Ruff on `src tests`, Mypy on **560 source files** and protobuf drift checks
  all passed. Logs are retained locally as `local-delivery-device-regression.log`,
  `local-delivery-native-designer.log` and `local-delivery-mypy.log` under the
  existing ignored evidence directory.

The Windows offscreen-only QMessageBox rendering workaround remains scoped to
the connection-result dialog; its four layout cases were executed on native
Windows Qt above, not counted as offscreen successes. The two existing manual
RTSP-recovery tests explicitly select zero retries and retain their quarantine,
sanitization and recovery assertions. No device/field configuration, ignored
artifact, credential, stash or local history bundle is added to the delivery.
No real camera/PLC, frozen EXE, full-suite run, deployment or user-process
restart was performed for this local delivery. PR #3 remains subject to full
CI and frozen-package acceptance on the updated commit before merging.

## Complete-Suite and Native Windows Follow-Up

The next independent change, `c745d97cd5a9ddec872c0ac58e52ab5aa03357f7`,
only advances the application version from 0.6.3 to 0.6.4. This follow-up is
based on that commit and preserves it.

### Verified Frozen Acceptance

Frozen runs [40465969703](https://github.com/jsdfhasuh/emo_master/actions/runs/40465969703)
on `eb17b77d` and
[40466371782](https://github.com/jsdfhasuh/emo_master/actions/runs/40466371782)
on `c745d97c` both built and passed actual packaged validation. Downloaded
reports confirm icons, Designer startup and Runtime **PASS**, with no forced
process cleanup. Designer scenarios include cancellation, independent data
directories, an occupied directory followed by reselection, independent close
and directory reuse. Runtime validation confirms the frozen worker executes a
Job, the native OperatorView connects, closing it preserves Runtime, explicit
stop retires the worker and directory locks can be reacquired.

These checks do not cover physical cameras, GPU, Designer main editing,
OperatorView image pixel fidelity or field acceptance. The installer was
hash-verified, not executed. Evidence is retained in `frozen-eb17b77d-evidence/`
and `frozen-c745d97c-evidence/` under the existing local CI evidence directory.

### Remaining Full-Suite Failures

The completed Ubuntu job in PR run
[40462529481](https://github.com/jsdfhasuh/emo_master/actions/runs/40462529481)
on `a4c243f8` reported **4 failed, 8208 passed, 31 skipped, 42 subtests passed**
in 1842.92 s. The later `7a296c92` PR run `40462718941` repeated three failures;
the RTSP timing case did not fail in that run. The matching four tests on the
current local source reproduced **3 failed, 1 passed in 7.60 s** before edits.

- PLC migration lost its warning about removed node-level retries when RTSP
  migration was added. Restore it specifically for SLMP, distinguish device
  connection recovery from replaying business reads/writes, and retain the
  original warning assertion with additional checks for that distinction.
- The log view intentionally gained workflow and node names before its message
  column in `64c334fc`. Update the stale ordinal assertion to check the actual
  five leading columns while retaining the initial 200-pixel visibility guard.
- Five newer operators declare `minCoreVersion=0.6.1` but were missing from the
  old-core rejection fixture. Add those exact IDs; preserve the exact rejection
  set, rejection codes and current-core acceptance assertions. No version gate
  or plugin implementation is relaxed.
- A fake decoder's release event is emitted before its reader thread returns.
  The spawned RTSP stop test now also waits for actual reader ownership to end,
  keeping its final frame, reservation and resource-release assertions. A new
  deterministic test holds the release callback after the event and verifies
  ownership stays true until close can join the reader. The driver is unchanged.

The Windows job of `40462718941` reached the Dispatch page tests and then exited
with access violation `3221225477` while opening the run-failure QMessageBox
during stop cleanup. No JUnit file was produced. A separate minimal local
QMessageBox probe, without Runtime or devices, reproduced the same access
violation under Windows offscreen QPA; identical code rendered and closed under
native Windows QPA. Windows CI now selects `windows`, while Ubuntu keeps
`offscreen`. No tests are deselected and timeouts/failure exit handling remain
unchanged; existing native-only dialog tests become executable on Windows CI.
The CI command regression verifies an explicitly selected backend is preserved.

Validation of the combined follow-up on Windows/Python 3.10.21:

- Four affected cases plus the new reader-ownership case: **5 passed in 7.75 s**.
- Related native GUI, RTSP/SLMP, SQLite writer and plugin regression:
  **473 passed, 1 skipped in 127.40 s**. The skip is the existing icon-resource
  symlink test because local symlink creation is unavailable, not a pass.
- Native Dispatch/normal viewing, failure/resource/connection dialogs, editing
  feedback and CI guards: **160 passed in 95.20 s**, no skips.
- Ruff, Mypy (**560 source files**) and protobuf drift checks passed.

Original logs/JUnit and new local logs remain under
`build/ci-followup-40444071101/`, including `ubuntu-a4c243f8-pr-full.log`,
`ubuntu-a4c243f8-pr-results/`, `ci-7a296c92-pr-results/`,
`windows-7a296c92-pr-full.log`, `full-suite-followup-before.log`,
`full-suite-followup-focused.log`, `full-suite-followup-regression-native.log`,
`full-suite-followup-skip-detail.log`, `windows-offscreen-messagebox-probe.log`,
`windows-native-messagebox-probe.log` and
`windows-native-dialog-dispatch-regression.log`. Prior failures remain evidence;
the new pushed SHA still requires full head/merge-candidate CI and fresh frozen
acceptance. No merge, branch deletion, release publication or real-device
operation was performed during this follow-up.

## Native Windows Desktop and Interrupted-Suite Evidence

Target `8fc4b5229dbb7c6f112e0ab646b9a91d2c2272da` passed both Ubuntu jobs
in branch run [40468735659](https://github.com/jsdfhasuh/emo_master/actions/runs/40468735659)
and PR run [40468740035](https://github.com/jsdfhasuh/emo_master/actions/runs/40468740035).
Both downloaded JUnit reports contain 8325 records, zero failures/errors and
31 skips. Frozen run [40468740762](https://github.com/jsdfhasuh/emo_master/actions/runs/40468740762)
also passed: the downloaded report matches that source SHA and confirms icons,
Designer startup/multi-instance ownership, the Runtime Job and native
OperatorView, explicit stop and ownership release, without forced cleanup.
Installer execution and field acceptance remain untested.

Both native Windows full-suite jobs exceeded their 60-minute job budget while
still progressing at 99%; GitHub annotations explicitly report the one-hour
limit. Neither produced final JUnit. This is **not** an all-tests-passed result:
both logs record six failures in device status/dialog geometry, log-dialog
geometry restoration and workflow-entry markers. The branch job additionally
failed `testClosingDuringConnectionCleansLateSessionWithoutStoppingTest` and
`testThousandNavigationsAndThirtyFloatingCyclesRetireNativeOwners`; those two
passed in the PR job. All original logs remain as
`windows-8fc4b522-{branch,pr}-full.log` in the ignored evidence directory.

The eight affected cases passed locally on native Windows Qt with a 1920x1080
desktop (**8 passed in 43.81 s**). A separate process using
`QT_SCALE_FACTOR=2`, without changing the user's display, reproduced all six
geometry failures (**6 failed in 9.47 s**): native Windows constrained requested
1060/1280/1920-pixel windows to the smaller logical desktop; restoring a larger
saved dialog similarly clamps it. This corroborates a desktop prerequisite,
but the original CI assertions were lost to cancellation, so their exact
failure details were not recovered. The two intermittent cases remain **OPEN**;
isolated local success does not resolve them. No product or existing UI-test
assertion was changed based on those incomplete reports.

The CI follow-up configures its disposable Windows runner to 1920x1080 and
checks the actual native Qt platform, screen geometry, available area and
scale before tests. It increases only the complete Windows job budget from
60 to 90 minutes. Every test retains the 120-second timeout; Ubuntu remains
45 minutes. Test collection/order, assertions and failure exit codes remain
unchanged. An explicitly loaded pytest reporter writes original failure
tracebacks and captured output immediately to `build/ci-failures.log`, anchored
to the invocation directory even when a test changes cwd. Both that file and
final JUnit are uploaded, so a later interruption does not erase prior failures.

Local evidence also includes `windows-native-full-followup-before.{log,xml}`,
`windows-native-small-screen-before.{log,xml}` and
`windows-native-followup-regression.{log,xml}`. The complete affected native
test modules passed **95 tests in 48.27 s**, without skips. The reporter's
subprocess regression deliberately fails a call and fixture setup, verifies
the first failure is saved before the next test starts, and retains pytest's
nonzero result. New remote full-suite results, runner desktop verification
and frozen acceptance are still required before merging PR #3.

The CI/reporter regression passed **8 tests in 0.88 s** with the real reporter
plugin enabled. Ruff, Mypy (**560 source files**) and protobuf drift checks
passed. The local display was not reconfigured; `Set-DisplayResolution` runs
only on GitHub's disposable Windows runner and still requires remote validation.

## Startup Diagnostics and Windows Failure Reporter

This follow-up starts from `462f9bea19ac67a26031066aadc898e6c0cc2e6a`.
Both Windows jobs passed the desktop configuration and actual native Qt geometry
checks. Both Ubuntu jobs passed in branch run
[40477322086](https://github.com/jsdfhasuh/emo_master/actions/runs/40477322086)
and PR run [40477332211](https://github.com/jsdfhasuh/emo_master/actions/runs/40477332211):
each downloaded JUnit contains **8328 records, zero failures/errors, 31 skips**.
Frozen run [40477333331](https://github.com/jsdfhasuh/emo_master/actions/runs/40477333331)
also passed. Its downloaded `frozen-462f9bea-evidence/frozen-validation.json`
matches that exact SHA and confirms icons, Designer startup and multi-instance
ownership, the frozen Runtime Job and native OperatorView, explicit stop,
worker retirement and reacquirable locks, without forced cleanup. As before,
the installer was only hash-verified, and no physical-device or field acceptance
is implied. These results do not validate the subsequent source change.

The branch Windows job failed after **1 failed, 7959 passed, 5 skipped and
42 subtests passed in 2286.97 s**. JUnit has **8008 records, 1 failure and
1 internal error**; the suite did not finish. The original full log, JUnit and
UTF-8 failure artifact remain under `windows-462f9bea-branch-full.log` and
`ci-462f9bea-branch-results/` in the ignored evidence directory.

- `testFailedInitializationKeepsToolsAndCanRetrySameTarget[database]` found an
  empty startup-diagnostic view. The Runtime window updated its in-memory
  diagnostics but never emitted the existing log-feed update signal. Rendering
  therefore depended on an unrelated asynchronous log-file read completing.
  A new deterministic regression initializes against the same database path
  collision while the window is hidden, verifies no file reader has run, and
  requires the error text to be ready before opening the log tool. It reproduced
  the empty view before the fix. The local fix emits the existing update signal;
  it does not add sleeps, alter retries or weaken the original assertion.
- The immediate reporter added in `462f9bea` successfully saved that original
  failure as UTF-8, then its plain `print()` raised `UnicodeEncodeError` on the
  Windows CP1252 console. That reporter bug, not another product test, caused
  pytest's internal error and prevented the remaining suite from running.
  The reporter now uses pytest's existing terminal writer, which escapes text
  unsupported by the console, while preserving the original UTF-8 artifact.
  Real subprocess regressions exercise both strict CP1252 and UTF-8, retain
  failure exit code 1, verify the next test executes, and preserve full Chinese
  assertion, setup-error and captured-output evidence even after cwd changes.

The combined new regressions produced **2 failed, 2 passed in 3.03 s** before
the fixes and **4 passed in 2.71 s** afterward with the actual reporter plugin
enabled. Evidence is retained as `windows-diagnostics-reporter-before.{log,xml}`
and `windows-diagnostics-reporter-focused.{log,xml}`. Historical Windows
late-session-close and retained-native-owner failures remain open: the branch
run stopped before reaching them, so this interrupted run cannot resolve them.

The related native Windows regression passed **95 tests in 134.84 s**, with
no skips: all OperatorView/window tests, Runtime settings and log-reader tests,
CI command/reporter tests and Runtime validation guards. This includes reading
state preservation, failed initialization and same-target retry, tool navigation,
parallel workflow ownership and shutdown. The actual reporter was enabled;
logs and JUnit are retained as `windows-diagnostics-reporter-regression.{log,xml}`.
Ruff, Mypy (**560 source files**) and protobuf drift checks passed. No field
project, credential or real device was accessed. New full-suite head/PR results
and fresh frozen acceptance remain required before merging PR #3.

## Completed Windows Suite and Remaining Incomplete Result

The older PR run [40477332211](https://github.com/jsdfhasuh/emo_master/actions/runs/40477332211)
on `462f9bea` eventually completed its Windows suite: **2 failed, 8279 passed,
5 skipped, 42 subtests passed in 3428.85 s**. Its JUnit contains **8328 records,
2 failures, zero errors**. The original log and artifacts are retained as
`windows-462f9bea-pr-full.log` and
`ci-462f9bea-pr-results/test-results-windows-latest-py3.10/`. The earlier
late-session-close and retained-native-owner tests passed in this complete run;
that observation does not erase their older intermittent failure evidence.

- `testGlobalCountersDialogNewSetAndResetUseBackgroundClient` called
  `createCounter()` after a result was displayed but before the worker-finished
  callback had retired the in-flight request. The same rejection reproduced
  locally on `f72d7d3b`. The product correctly serializes requests and keeps its
  controls disabled until the worker finishes. The test now waits for that
  actual readiness before new/set operations, preserving all original write
  and value assertions. A deterministic companion holds the worker after
  emitting its result and verifies it remains busy, rejects overlapping work
  without side effects, and accepts exactly one new request once retired.
  Product worker ownership and request serialization are unchanged.
- `testBusinessNgDoesNotStopAndSharesInvocationWithImageAndCount` received
  `INCOMPLETE` instead of `COMPLETE`. Its original failure diagnostic contains
  only `diagnostic budget exceeded`, so the unavailable source and reason
  cannot be recovered from that artifact. This remains **OPEN / root cause not
  established**. The initial two-case local run reproduced the counter failure
  but this production case passed; it also passed in the related module run
  and in **10 separately launched processes**. Those passes are not a fix.
  No image-export deadline, result status or business assertion is changed.

The existing test-only `jobFailureDetails()` helper now retains its core Job
and failed-result metadata when detailed closure/export context cannot fit
the existing 16 KiB output budget. It still avoids RPC, file reads, payload
values and Runtime locks. A synthetic repeated-diagnostics regression first
reproduced the loss of the result; afterward it retains the precise result
identity, source state and reason code. The synthetic reason used by that test
is not evidence of the unrecovered CI failure's actual cause.

Local native Windows regression: **31 passed in 35.01 s**, no skips, covering
the counter dialog, bounded diagnostics, production boundaries and reporter.
Related first-result/SQLite diagnostic-wrapper regression: **90 passed in
1.62 s**, no skips. Ruff and protobuf drift checks passed. Retained evidence
includes `windows-complete-suite-followup-before.{log,xml}` (1 failed, 1 passed),
`result-diagnostics-budget-before.{log,xml}` (1 failed),
`windows-complete-suite-followup-regression.{log,xml}`,
`diagnostic-helper-followup-regression.{log,xml}`, and
`business-ng-followup-process-{1..10}.{log,xml}`. The earlier attempted repeated
node invocation collected only one test and is not counted among those ten.

Before this test-only follow-up, frozen run
[40484287700](https://github.com/jsdfhasuh/emo_master/actions/runs/40484287700)
on exact source `f72d7d3b22fa5ac5565f2c2816a2c5805e33110b` passed, with the
downloaded report in `frozen-f72d7d3b-evidence/`: icons, native packaged Designer
startup/multi-instance ownership, Runtime Job, native OperatorView, explicit
stop, worker retirement and reacquirable locks, without forced cleanup.
The installer was hash-verified only, not executed. Windows display contract
run [40484279424](https://github.com/jsdfhasuh/emo_master/actions/runs/40484279424)
also passed on that SHA: downloaded JUnit has **71 passed, zero failures/errors
or skips**, and the source identity matches. Its artifacts remain under
`display-contract-f72d7d3b-evidence/`. These are source/native and frozen checks,
not real-device or field acceptance. Updated head/merge-candidate full CI and
frozen verification remain necessary before merge; no branch was deleted.

## Main Integration and Reset Generation Race

The final source `d76acfded20c008a4e8510c482042b52536d6a2d` passed branch CI
[40487163044](https://github.com/jsdfhasuh/emo_master/actions/runs/40487163044)
and PR CI [40487169417](https://github.com/jsdfhasuh/emo_master/actions/runs/40487169417)
on both platforms. Each downloaded JUnit contains 8332 records, zero failures
and zero errors, with 31 existing Ubuntu skips and 5 existing Windows skips.
Both Windows suites executed and passed the historical counter, BusinessNg,
late-session-close and retained-native-owner cases. The earlier unrecoverable
INCOMPLETE reason remains unestablished; these passes do not prove its cause.
Frozen run [40487170557](https://github.com/jsdfhasuh/emo_master/actions/runs/40487170557)
also passed on exact source d76acfde, with downloaded icons/Designer/Runtime
acceptance evidence and no forced cleanup.

PR #3 was normally merged at 2026-10-27T11:48:07Z as
`4a8bbfc8fc0bb9bae5fdb1e8a3afe66b91d79c53`. Its tree
`cdc5c28d39b78f7b2c4a2b1d9036ac3e1b2fa0b0` equals the tested PR candidate
`fe088a271aff9337c1032643810d70f7c67e11e9` and the source tree.

Post-merge main CI
[40498867142](https://github.com/jsdfhasuh/emo_master/actions/runs/40498867142)
then completed Ubuntu with **1 failed, 8258 passed, 31 skipped, 42 subtests
passed in 2058.14 s**. Its JUnit has 8332 records, one failure and zero errors.
Windows was still running when this failure was investigated. Evidence is
retained under `build/ci-followup-40444071101/` as
`ubuntu-main-4a8bbfc8-full.log` and
`ci-main-4a8bbfc8-results/test-results-ubuntu-latest-py3.10/`.

`testGrpcMultipartAssetExceedsUnaryLimitAndExpiresAfterReset` exposed a normal
reset race in Designer's `DebugConnection`: GetOperatorDebugSession returned
generation 1, the Runtime completed the reset to generation 2, and the next
ReadOperatorDebugEvents correctly rejected generation 1. Cleanup then tried
CloseOperatorDebugSession with that stale generation and failed as well.
Real Runtime regressions deterministically reproduce both the between-read
transition and a close after reset without an intervening poll.

The local repair refreshes a snapshot once after the explicit stale-session
rejection and pins its component reads to the returned generation. Close can
refresh the same Runtime/session once after a pre-dispatch generation rejection,
retaining its request ID. Runtime replacement, expired results, transport loss
and repeated stale replies still fail; execute/upload/reset mutations are not
replayed. Server generation guards, deadlines, the original acceptance test
and resource-ownership checks are unchanged.

New regressions before the repair: **4 failed, 6 passed** (including both real
Runtime failures). Afterward, those tests plus the original multipart gRPC
acceptance case passed **11 tests in 8.39 s**. Logs and JUnit are retained as
`debug-reset-generation-before.{log,xml}` and
`debug-reset-generation-focused.{log,xml}`. Ruff, Mypy (560 source files) and
protobuf drift checks passed. The related native Windows regression passed
**210 tests in 244.00 s**, with one existing opt-in stress-test skip. It covers
operator sessions, RPC, assets, retirement, recovery windows and inherited
workflow-debug cleanup; logs/JUnit are retained as
`debug-reset-generation-regression.{log,xml}`. New remote CI and frozen-package
validation remain pending. No physical device was accessed, and these local
passes do not authorize branch cleanup while main CI is failing.

## Pytest 9.2 Coordinate Assertions and Recurrent Native-Owner Evidence

PR #4 merged normally as `1aef380fd0528f1484461aaa2f23da596eb35b89`.
Before merge, CI [40505503943](https://github.com/jsdfhasuh/emo_master/actions/runs/40505503943)
passed both platforms (8342 JUnit records each, no failures/errors; Ubuntu 31
and Windows 5 existing skips), and frozen run 40505519920 passed on exact
source `50495e92b2c9bc23a2190a94dd5ed41db7de8fc1`, without forced cleanup.

Post-merge run [40609824498](https://github.com/jsdfhasuh/emo_master/actions/runs/40609824498)
installed pytest **9.2.0** on both platforms. Ubuntu finished with **6 failed,
8263 passed, 31 skipped, 42 subtests passed**; Windows finished with **7 failed,
8288 passed, 5 skipped, 42 subtests passed**. Each downloaded JUnit has 8342
records and no errors. Full logs and JUnit/immediate failure logs are retained
in the primary checkout's `build/ci-followup-40444071101/`, under
`{ubuntu,windows}-main-1aef380f-full.log` and `ci-main-1aef380f-results/`.

Both platforms rejected the same six `pytest.approx(list_of_tuples)` calls
in coordinate calculator, minimum enclosing circle and Runtime coordinate
workflow tests. An isolated pytest 9.2.0 installation reproduced **6 failed,
12 passed** on current main `443f1d5555bb7812a11afb5d991a190622f84724`; the
shared project interpreter remains on pytest 9.1.1. Comparing a list of
individual `pytest.approx(tuple)` values preserves list length/order, both
coordinates, the expected numbers and the default numeric tolerances. The
three affected modules then passed **18 tests** under each pytest version.
No product code, dependency constraint or test selection was changed.

Windows' seventh failure was again
`testThousandNavigationsAndThirtyFloatingCyclesRetireNativeOwners`: during
navigation, handles rose from **21534 to 21538** and native thread ID **8328**
appeared (44 to 45 native threads; Python threads remained 43). The handle
assertion ran before the existing thread diagnostic checkpoint, so the exact
navigation index and start module were not recorded. This remains **OPEN**;
it is not evidence of a particular Qt/backend cause. The older floating-cycle
failure with thread ID 9772 also remains preserved and unexplained.

The CI failure reporter now reuses the existing bounded, read-only native
thread helper only after this exact test's call fails. It extracts the failed
snapshot pair and phase from the traceback, including a handle failure before
the thread assertion, and adds a diagnostic section to the existing immediate
failure log. It performs no replacement census or successful-test query and
does not alter the test, assertion, result, timeout or cleanup. Collection is
after the test's `finally` cleanup: an ID may have retired/reused, and a start
module does not prove the creator, owner or retention cause.

Local native Windows validation retained an initial combined run of **88
passed, 1 failed**: the owner test saw a `(0, 0)` image instead of `(1920,
1080)`. This additional observation is not claimed fixed. A comparison run
using the unchanged main reporter/test modules passed **64 tests**, and the
candidate with seven new reporter regressions passed **71 tests**. The
original owner module also passed separately (16 tests). Keep these distinct
from the remote failures; passing reruns do not establish intermittent causes.
Evidence files include `coordinate-approx-before.{log,xml}`,
`coordinate-approx-after-pytest{920,911}.xml`,
`coordinate-and-native-diagnostic-regression.{log,xml}`,
`native-diagnostic-order-{baseline,candidate}.{log,xml}` and
`retained-owners-local-followup.{log,xml}`. Ruff, Mypy (560 source files) and
protobuf checks passed. Full candidate CI and frozen acceptance remain pending.

Another authorized release task advanced main through `07a16092` to
`443f1d55` and version 0.6.5; those commits, the release tag/workflow and its
primary `codex/release-v0.6.5` checkout are preserved. This follow-up uses
the separate `codex/ci-coordinate-assertions` worktree. No branches have been
deleted, and cleanup still requires the actual latest main to pass both full
platform suites.

## Node Viewer Repeated-Run Readiness

PR #5 source `e422fa359598f57fbbf5f4fd278a5f8198203531` completed full CI
[40617456311](https://github.com/jsdfhasuh/emo_master/actions/runs/40617456311):
both downloaded JUnit files have **8362 records, zero failures/errors**, with
31 existing Ubuntu skips and 5 existing Windows skips. Both platforms executed
the original two-run viewer, BusinessNg and retained-native-owner cases.
Frozen [40617468778](https://github.com/jsdfhasuh/emo_master/actions/runs/40617468778)
also passed on exact e422fa35: icons, packaged Designer startup/multi-instance,
Runtime Job and OperatorView, without forced cleanup. Installer validation was
hash-only. Evidence is in `ci-e422fa35-pr-results/`,
`frozen-e422fa35-evidence/` and `e422fa35-gate-evidence-0204.json`.

The concurrent v0.6.5 release run
[40614916357](https://github.com/jsdfhasuh/emo_master/actions/runs/40614916357)
on older main `443f1d55` stopped at source validation; packaging/publication
was skipped. Alongside the six old coordinate assertion errors, it exposed
`testTwoRealRunsFollowSelectionWithoutExtraReadsAndViewerCloseKeepsRuntime[False]`
waiting for a second Job ID that never appeared. Its JUnit has 8355 records,
7 failures, 0 errors and 5 skips (8301 passed, 42 subtests passed). The same
main's ordinary Windows CI had only the six coordinate failures; its native
owner test passed. Logs/JUnit remain in `release-443f1d55-source-results/`,
`windows-release-443f1d55-full.log` and `ci-main-443f1d55-results/`.

The viewer failure reproduced locally without instrumentation (1 failed,
1 passed). The test waited for an image and `_jobActive == False`, which can
precede the monitor's queued `finished` delivery. A bounded, test-only probe
held the first real RuntimeWorker after its normal run returned: the attempted
second start observed `jobActive=False`, `workerPresent=True`,
`workerRunning=True`, `canStart=False`, and reproduced the same timeout.
Releasing the old monitor did not replay the rejected start. The probe is
retained privately as `viewer_start_probe.py`; it is not installed in CI or
the product. The earlier trace-only probe passed and is also preserved.

The original test now also waits for `runtimeController.canStart()`, the same
gate used by the Run action, before advancing to the next run. Its watchdog,
terminal-state check, image identity/pixels, read counts, two-Job count and
Runtime-preserving close assertions are unchanged. The identical held-exit
probe then passed, with no worker present at the second start. This is a test
precondition repair, not a change to scheduling or side-effect retry policy.
Evidence: `node-viewer-two-runs-before.{log,xml}`,
`node-viewer-start-probe-before.{log,xml}` and
`node-viewer-held-exit-{before,after}.{log,xml}`. The changed source still needs
fresh full CI and frozen validation before PR #5 is merged; e422fa35 results
are retained as evidence for that exact earlier source. The uninstrumented
native viewer and Runtime/workflow-controller regression passed **31 tests
in 27.04 s**, without skips; evidence is
`node-viewer-readiness-regression.{log,xml}`. Focused Ruff and diff checks passed.

## Main 0.6.6 Two-Scope Image Failure Investigation

PR #5 merged as `58d859b0b813645a068ec8201c4ef2f3e9f40305` after both
platforms and exact-source frozen validation passed. Its main Windows run
40628072588 still reported one retained-native-owner failure: navigation
index 699, four extra handles and one native thread. The post-failure query
located its entry in ntdll.dll; that does not identify the creator or cause.
The local full suite later passed that target, but failed the separate
30-second two-Designer startup test. Both intermittent causes remain open.

The independent release task advanced main to
`25c82042ce3cfb83e16ede8ea2cb2bcbb2110ce4`, changing only the version to
0.6.6. Exact-source frozen validation 40640654916 passed icons, native
Designer startup/multi-instance, Runtime Job and OperatorView, with explicit
stop, worker retirement and reacquired owner locks, without forced cleanup.
Installer verification remains hash-only.

Main CI 40639949885's Ubuntu job 122655518469 failed
`testNormalTwoScopeOverviewButtonsPinGuiNgDespiteNewerBackgroundOk` at the
second-cycle image comparison with `KeyError: 'a-image'`: 8288 passed,
one failed, 31 skipped and 42 passing subtests. The old report does not retain
the failed view's source outcomes, read errors or image-demand state, so it
does not establish why the image was missing. A single local offscreen run
and eight bounded repetitions passed; their 36 total image reads succeeded.
These results do not resolve the Ubuntu failure.

The diagnostic change checks image presence immediately before the original
pixel assertion and includes the same immutable result, decoded source IDs,
read failures, image states and matching decode records on failure. It adds
no retry, wait, product change or replacement snapshot. All existing identity,
pixel, navigation, pin, resume and retirement assertions remain. A temporary
PR-only Ubuntu workflow executes at most eight original cases and stops at
the first failure, preserving its exit status and reports. This diagnostic
run is not a replacement for either full platform suite. The original module
passes locally on native Windows (2 tests); focused Ruff and diff checks pass.

Private evidence remains under `build/ci-followup-40444071101/`, including
`ubuntu-main-25c82042-full.log`, `ci-main-25c82042-results/`,
`multiscope-25c82042-before.{log,xml}`, `multiscope-25c82042-bounded.{log,xml}`,
the numbered private trace JSON files, and
`multiscope-diagnostics-native-regression.{log,xml}`. No branch cleanup is
permitted while the actual latest main has a failing platform.

The diagnostic PR's full Windows job 122673055292 (run 40645439287,
source `52fb7fd1506dca87f5e32e5df875be6542b722ff`, merge candidate
`bea21c08ab0b997886178cfa91cc851f3bfae4e5`) reproduced the same missing
second-cycle `a-image`: 8314 passed, one failed, five skipped and 42 passing
subtests. The captured Runtime result is `INCOMPLETE`; its image source is
`UNAVAILABLE / EXPORT_TIMEOUT`, with reason "assembler waiting for export
callback exceeded seal deadline". The scope ended 666.9631 ms before closure.
There is no image reference, client read failure or pending image-demand
state in that failed view. This identifies a Runtime export/closure failure,
not a client decode-readiness failure. It does not yet distinguish export
queueing, encoding, callback contention or scheduling delays. The earlier
Ubuntu main failure lacks these fields and cannot be assigned this cause
with certainty.

The matching Runtime closure diagnostic already retains the export owner's
state at closure, but the first assertion did not include it. The failure
message now also retains that existing record and matching export retirement
records. The temporary bounded job moves to native Windows at 1920 x 1080,
where the detailed failure actually reproduced. All original deadlines,
assertions and production code remain unchanged. Seven native normal-viewing
and multi-scope tests pass locally in 32.49 s; a private observation probe
records all four successful exports, with no failed closure. That observation
does not establish the CI cause or a fix.

The diagnostic PR's Ubuntu full suite passed 8289 tests with 31 existing skips
and 42 passing subtests. The Windows native-owner and two-Designer startup
targets also passed in this run; their historical failures remain unresolved.
New private evidence: `52fb7fd-windows-failure-evidence-0728.json`,
`windows-pr-52fb7fd-full.log`, `ci-52fb7fd-pr-results/`, and
`multiscope-export-owner-native-combination-0733.{log,xml,json}`. PR #6 remains
a diagnostic draft rather than a claimed product correction.

### 2026-10-28: native-owner recurrence and bounded creation-stack capture

Full CI 40651063012 at diagnostic source
`3ab56134d97d72392bfdce562820d0fadfbbe480` (candidate
`9efccd3889a0f56210bbccb9ac94a1345df5212c`) passed Ubuntu with 8289 passed,
31 existing skips and 42 passing subtests. Windows job 122690969789 failed
only the native-owner test: 8314 passed, one failed, five existing skips and
42 passing subtests. Both JUnit reports contain 8362 tests and no errors.
Windows navigation index 99 observed handles 21683 -> 21687 and native
threads 43 -> 44, with Python threads 45, widgets 296 and windows 4 unchanged.
The post-cleanup query found new thread 7196 at ntdll.dll + 0x845d0. This
again identifies an entry module, not the creator or reason for retention.
The two-scope image test passed in 5.156 s; no new failed export timeline was
captured. Neither intermittent cause is established as fixed.

PR6 now temporarily enables WPR only around the original native-owner test
call in its normal Windows full-suite order. A 32 MiB memory profile records
process/thread and loader events with ThreadCreate stacks; there is no file,
network or sampling provider. A separate short smoke capture verifies WPR
start/stop before spending time on the full run. Each capture owns a fresh
named instance; stop/cancel always specifies that name. The artifact includes
the original ETL, command outcomes, target PID and wall/monotonic boundaries.
Original assertions, 1000 navigations, 30 floating cycles, timeout and exit
status are preserved. A failed capture cannot turn a failing test green, and
an otherwise successful suite with invalid capture is reported as invalid.
The observer changes timing and is diagnostic only; lost events, missing
stacks or a passing instrumented run cannot establish a fix. It is enabled
only for this investigation PR on Windows Actions, not local/native release
acceptance. Remove these temporary CI hooks and reassess the bounded
two-scope workflow before final acceptance and delivery.

Private original logs, JUnit and immediate failure output are retained as
`windows-pr-3ab56134-full.log`, `ci-3ab56134-pr-results/`, and
`3ab56134-windows-failure-evidence-0840.json`. No product change, merge,
release or branch cleanup accompanies this diagnostic revision.

Local validation of the new observer and existing CI/report integration:
23 tests passed, with no skipped tests. This uses mocked WPR commands for
ownership and failure-path checks, not a local kernel capture. The installed
WPR accepted the profile and confirmed ProcessThread/Loader plus ThreadCreate
stacks. Focused Ruff, workflow/embedded-Python validation and diff checks
passed. Actual capture capability is checked by the Windows Actions smoke
step; no local system-wide trace was started.
