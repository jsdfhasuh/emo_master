# Runtime wait failure diagnostic scope

The SQL failure plugin is default-off. `--sqlite-failure-diagnostics NEW.json`
enables the existing fifteen-module failure observer;
`--sqlite-failure-call-details` additionally enables detailed SQL observations
only for the following nine exact nodeids. The original full pytest order,
assertions, deadlines, native SQL calls and database settings remain unchanged.

## Source-backed targets

All paths below are under `tests/runtime/`. Each parameter is an independent
node/epoch. These are evidenced wait failures, not a claim of shared root cause.

| Exact nodeid suffix | Observed original failure | Source |
| --- | --- | --- |
| `presentation/test_normal_capture.py::testOriginalStartCapturesInstalledOperatorsAndProductionCounter` | First terminal wait; worker exited 0 while parent record remained RUNNING | [6a05885 PR Windows, job 110271295366](https://github.com/jsdfhasuh/emo_master/actions/runs/36832246886/job/110271295366) |
| `presentation/test_normal_capture.py::testExplicitStopReleaseAndRestartNormalJob[graceful]` | Existing 10s first-results wait, before StopJob; historical plain `assert []`, now already a lazy failure helper | [206621e capture diagnostic, job 110287057487](https://github.com/jsdfhasuh/emo_master/actions/runs/36837105087/job/110287057487) |
| `presentation/test_normal_capture.py::testExplicitStopReleaseAndRestartNormalJob[force]` | Same first-results wait, before StopJob; direct cumulative SQL phase evidence | [f892367 SQL diagnostic, job 110310851694](https://github.com/jsdfhasuh/emo_master/actions/runs/36844369726/job/110310851694) |
| `test_legacy_snapshot_policy.py::testAllRunMissingImageCannotRelabelEarlierNodeSnapshot` | First terminal wait, RUNNING | [206621e push Windows, job 110287057439](https://github.com/jsdfhasuh/emo_master/actions/runs/36837104994/job/110287057439) |
| `test_legacy_snapshot_policy.py::testAllThenNoneKeepsHistoryWithoutNewSnapshotsAndRestoresPolicy` | First terminal wait, RUNNING; separate from the A18 target | [88c9cbb A18 diagnostic, job 110341260070](https://github.com/jsdfhasuh/emo_master/actions/runs/36853759902/job/110341260070) |
| `presentation/test_image_demand_client.py::testZeroDemandKeepsFormalResultsAndResumesFinalCapturedResult[normal]` | Terminal wait after earlier successful client progress | [2ddb2da PR Windows](https://github.com/jsdfhasuh/emo_master/actions/runs/36850822805/job/110331781471), [88c9cbb push Windows](https://github.com/jsdfhasuh/emo_master/actions/runs/36853759751/job/110341259077) |
| `presentation/test_image_demand_client.py::testZeroDemandKeepsFormalResultsAndResumesFinalCapturedResult[presentation]` | Same terminal wait, RUNNING | [88c9cbb push Windows, job 110341259077](https://github.com/jsdfhasuh/emo_master/actions/runs/36853759751/job/110341259077) |
| `test_runtime_job_lifecycle_integration.py::testRealSpawnConcurrencyStopsAndCleanup` | Graceful-stop terminal wait, STOPPING; worker exited 0 | [41cfa4a push Windows, job 110083958786](https://github.com/jsdfhasuh/emo_master/actions/runs/36773035419/job/110083958786) |
| `presentation/test_multi_capture.py::testLateResetFromSameRuntimeCannotReplaceNewerScopeState` | `results()` terminal wait before snapshot polling, server/session construction and late-reset assertions | [d539ecc PR Windows, job 110353520709](https://github.com/jsdfhasuh/emo_master/actions/runs/36857538355/job/110353520709) |

Only the f892367 case above directly measured slow returned commits: 69 returns,
9.252s aggregate wall time, 78.125ms thread CPU, maximum 1.453s; the active commit
sample was only 15ms old. Totals span all Jobs on the observed store. Other stack
samples/event tails neither measure native commit latency nor prove a continuous
stall, fsync/checkpoint cause, or the same root cause.

Excluded from detailed scope:

- `presentation/test_multi_capture.py::testTwoExplicitDualImageJobsStayWithinRuntimeBudget`:
  d986fe1 push Ubuntu job 110210970220 failed a returned result's COMPLETE-status
  assertion after the terminal/results waits, not a wait timeout
- `presentation/test_normal_capture.py::testLostStartReplyReconcilesWithoutAnotherExecution`:
  d539ecc PR Windows failed `dispatched.wait(3)` after the expected 0.1s RPC
  deadline, before the failure helper or a captured Job context; no SQL cause
  follows from that failure

## Observation and ownership limits

The existing `jobFailureDetails` boundary runs only after a predicate failed.
No successful-path output or WAL/filesystem metadata probing is added;
opt-in source-identity hashing still reads the reviewed source files. SQL QPC,
connection operation tokens and native `in_transaction` readings are enabled
only for a collected exact detail target. None of these observations adds SQL,
polls, worker threads, waits, retries, history scans or owned Runtime references.

All nine nodes already have `tmp_path`. The plugin reads that existing fixture
argument before the test call; it never requests a fixture or falls back to a
production/default root. WAL metadata is restricted to the exact helper
RuntimeService's SqliteStore beneath that root, with the existing bounded,
read-only Windows metadata contract. Missing root/store, unsupported platform,
changed files or unavailable metadata remain explicitly unavailable.

- Original-start and late-reset use the in-process `channel` fixture's
  `tmp_path/runtime.sqlite3`; fixture teardown closes Runtime then presentation
- Graceful/force own equivalent Runtime/presentation objects in the test and
  close both in `finally`; two Jobs can share one store
- Legacy nodes own `tmp_path/runtime.db` without a presentation ResultStore;
  AllThenNone can later reopen a new Runtime/store lifetime
- Lifecycle owns `tmp_path/runtime.db`; concurrent and sequential Jobs share it
- Image-demand owns parent `tmp_path/state.db`, presentation and a real aio
  server. Session closes before server, Runtime and presentation. Prepared mode
  passes a separate runtimeDbPath to the spawned worker; the helper identifies
  the parent store, not that worker's database

Only the three normal_capture nodes observe existing main-thread
ResultStore.snapshot calls. Original-start's evidenced terminal timeout precedes
those calls: zero observations means `UNAVAILABLE/no_observed_calls`. The other
six nodes do not install a snapshot wrapper and report
`UNAVAILABLE/no_reviewed_main_thread_resultstore_polling_for_target`. No new
RuntimeService.GetJobStatus or DisplaySession.readSnapshot observer is added.
Observed intervals are not a wait-start/deadline record and may span Jobs.

Helper coverage is unchanged. Original-start's later direct `results[0]`
failure, image client condition deadlines, and other failures that do not call
the shared helper remain uncovered, never fabricated as detailed evidence.

## Report contract and interpretation

Detailed reports use schema 3 with `declared_detail_targets`, a nonempty unique
`collected_detail_targets` subset, and each failure's `test` and `call_details`.
The workflow validates fields against the exact set. Focused subsets are allowed;
valid base-only failures in other allowlisted tests remain valid. Base-only
reports retain schema 1. Source hashes include both tmp_path/channel fixture
sources. Existing report quotas, weak store lifetimes, source/patch identity
checks, exception preservation and retirement validation remain in effect.

A passing instrumented run with no artifact is inconclusive. SQL, cached Job
state and WAL are independent, non-atomic observations. Instrumentation perturbs
execution; Linux success or unrelated native WAL success cannot establish that
an archived Windows runtime failure is fixed.
