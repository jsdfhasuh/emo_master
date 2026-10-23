# 2026-10-23 CI follow-up

## Fixed Source And Scope

- Repository: `emo_master`, branch `agent/runtime-workflow-architecture-v1`.
- Original failing source: `772f22c2c73b8b730791c54cdb3f5642ed1902e6`.
- Final repair source before this evidence-only commit: `020c7135fdffef2e9b7bca64bfdb58dbeed0c884`.
- Original CI: https://github.com/jsdfhasuh/emo_master/actions/runs/39894788254
- Both original platform jobs stopped after 944 passes at the shortcut test.
- Work was reviewed against actual production contracts, not accepted merely because Dot had pushed it.
- Validation uses `build/ci_shortcut_fix_20261023/final-source`, an isolated committed-source snapshot with only these repairs overlaid. Unrelated video, file-list, network-camera, and Runtime changes in the shared checkout are excluded and preserved.
- Only the lifecycle hunk in the shared `param_form.py` was staged; other edits in that file remain uncommitted.

## Production Repairs

### Compiler Error Locations

Commit `83745e40e1551ec55ad754f940d58a794fae81ef` retains workflow, node, and field together in `LoadProject` compiler diagnostics. Previously a field path replaced workflow/node context, preventing the user from locating the offending legacy node. Device binding validation is unchanged.

### TCP Preflight And Error Categories

Commit `43d0dd0b856382cbd32230b0e656c7da3c0b6b23` validates outgoing request size before connecting and ACK size before opening a listener or receiving data. Bind, transport, and cancellation errors keep their established error categories through the managed driver and operator adapter. Unknown send outcomes retain byte counts and `automaticRetry=False`; no retry was added.

### Delivery Operator Version

Commit `f7367e252e350b1935096a5d3ebeb4f14311e5d2` updates the page test-delivery allowlist to the actual Image Saver version `1.1.0`. The stale `1.0.0` entry rejected the bundled operator. Release-host and page-package source tests were rerun; this was not a new packaged release.

### Selftest Identity And Admission

Commit `17a26c4eae723212852e5220e1bafe2e98ac3aee` updates the source selftest to valid schema 2.7 without legacy top-level communication, supplies current registry device requirements to package import, explicitly identifies the existing simulated device when borrowing it, and passes the frozen workflow/node/slot source on capture. Original selftest assertions remain intact.

### Qt Schema Refresh Lifetime

Commit `020c7135fdffef2e9b7bca64bfdb58dbeed0c884` detaches form layout items and defers widget destruction until Qt finishes signal dispatch. Empty rows are then removed normally. This also covers visible controls; the existing detached hidden-row cleanup remains.

Ubuntu CI at `17a26c4e` crashed inside `SchemaParamForm.setSchema()` / `removeRow()`:
https://github.com/jsdfhasuh/emo_master/actions/runs/39917260077/job/120293384038

A new direct signal-refresh regression, executed against the original source, also produced a Windows access violation (exit `-1073741819`). The fixed source passes that regression, the deferred-destruction assertion, and the existing repeated-refresh widget-count assertion. No tests were skipped to suppress the crash.

## Test Contract Corrections

- `173460c6`: canonical global shortcuts and node-local port actions are checked separately; strict unknown global command lookup remains.
- `ba74a66c`: the device diff test checks readable details, explicitly opens Technical Details for exact paths, and returns to readable mode. No-Runtime-write assertions remain.
- Schema tests distinguish current 2.7 new projects from explicitly historical migration inputs. Old TCP configurations are not deleted to bypass migration.
- Real TCP worker tests now load schema 2.7, bind managed devices, and use the current device management RPCs. Exact external-client bytes, connection generations, two-channel isolation, cancellation, backpressure, no replay, and resource release remain covered.
- Legacy communication-management tests use an independent workflow and are labeled as compatibility tests. Designer Save As still exercises the actual window, disk file, listener release, path fencing, and reload; its controller gets a Runtime client that supports project loading.
- Old PLC private-debug transport tests use explicitly isolated historical declarations. Separate tests assert that current production managed declarations reject both private-debug entry points with `E_DEVICE_DEBUG_REQUIRED`; the production guard was not loosened.
- Cycle fixtures forward current source/identity arguments. Rejected jobs assert `REJECTED`, no job ID, and no newly created workspace.
- The page test now describes native editing plus the retained legacy preview backend, not a complete current-menu user journey. It retains real image/count changes, save/reopen, and worker teardown assertions.
- The workflow-history test restores its fake page editor before window teardown instead of leaking it into production `closeEvent`.

The final TCP/native UI test corrections are in `23f33b3`.

## Evidence And Results

Environment: Windows, Python 3.10.21, PySide2 5.15.2.1, pytest 9.1.1, pytest-timeout 2.4.0, `QT_QPA_PLATFORM=offscreen`, `PYTHONUTF8=1`.

Raw logs and JUnit files are retained under `build/ci_shortcut_fix_20261023/`. The old discovery snapshot `source/` and repaired snapshot `final-source/` are distinct.

| Check | Result | Local Evidence |
| --- | --- | --- |
| Original shortcut reproduction | 1 failed, `KeyError: showPorts` | `baseline-failure.txt` |
| Shortcut/menu/port coverage | 88 passed | Earlier focused run |
| First fail-fast continuation | 1078 passed, 1 skipped, 1 failed | `full-ci.txt`, `full.xml` |
| Device publication/navigation | 30 passed | Earlier focused run |
| Full discovery with first two test fixes only | 188 failed, 6968 passed, 13 skipped, 1 teardown error, 41 subtests passed | `full-second.txt`, `full-second.xml` |
| Compiler/schema/package focused fixes | 233 passed | `final-focused.txt`, `final-focused.xml` |
| TCP preflight, operator, admission coverage | 115 passed | `runtime-expanded-final.txt`, corresponding XML |
| PLC, managed SLMP, release-host, cycle, page-package coverage | 434 passed | `plc-release-final.txt`, corresponding XML |
| Lifecycle, debug gates, cycle, real spawn selftests | 66 passed | `tail-final-2.txt`, corresponding XML |
| Final four TCP integration/review files | 61 passed | `tcp-contract-final.txt`, corresponding XML |
| Native UI/history final files | 54 passed | `ui-tail-final.txt`, corresponding XML |
| Schema refresh on original source | Access violation in signal-refresh regression | `form-signal-before.txt` |
| Schema form and surrounding UI after repair | 143 passed | `form-lifecycle-complete.txt`, corresponding XML |
| Protobuf drift | Passed | `final-full-ci.txt` |
| Ruff, `src tests` | Passed | `final-full-ci.txt` |
| Exact CI mypy command, `--config-file mypy.ini src` | Passed, 511 source files | `final-mypy-ci.txt` |

Focused runs overlap; their counts must not be summed as unique full-suite coverage. The complete discovery result is a failure baseline, not a final success result. Initial and intermediate failure logs, including a test snapshot copy-path error and the unavailable PySide2 `takeRow` API attempt, are retained separately from successful reruns.

`f7367e25` initially failed Ruff because of a fixture import alias; `17a26c4e` corrected it. That failed CI run is not reported as passed. The older display/live-contract success at `83745e40` likewise does not establish the status of later source.

At publication, a fresh exact `scripts/ci_check.py` run is in progress against final repair source, recorded in `final-full-ci.txt`. The resulting push starts the unchanged Linux/Windows CI matrix. Consult that commit's run for final full-suite status; this report records verified focused evidence and does not predeclare the pending checks successful.

### PLC Editor Teardown Follow-up

The Windows job at `17a26c4e` passed 1844 tests, then failed while tearing down the high-frequency heartbeat test. The fixture waited for an empty cleanup queue while its 25 ms heartbeat was still producing renewals. It now invokes the existing `onClose()` first, then retains the assertions that operations, cleanup, and active calls all drain. Production heartbeat and timeout settings are unchanged.

An independent committed-source snapshot at `a70c15a8` plus this one fixture correction passed all 163 tests in `tests/designer/test_plc_editor.py` (40.99 s). Evidence: `plc-teardown-final.txt/xml`; original remote failure: `windows-plc-teardown-before.txt`. The already-running full local snapshot is intentionally not modified mid-run. Its source/tests/configuration were audited against 1279 committed blobs before this follow-up.

## Acceptance Boundary

No real camera/PLC connection, field project mutation, network readdressing, credentials change, deployment, frozen EXE build, or Release was performed. Simulated device, localhost socket, source selftest, native offscreen Qt, and remote CI results are separate evidence layers. Skipped tests are not passes. Running application executables do not hot-load these source repairs.

Unrelated dirty files and private/live data are preserved. Commits use scoped staging and normal pushes; no reset, clean, force push, or unrelated merge was used.
