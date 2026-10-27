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
