# Bounded worker event commits: 2026-10-20

Base: 02435e66911f629e1f8ec776e9be874dfc4d8d08. This is a follow-up to retained Windows CI failures, not a timeout relaxation.

## Evidence and scope

The base exact-push Windows run 39376608229 ended with 6352 passed, 4 failed, 5 existing skips, 42 subtests. Failures were the 300-join 90-second watchdog, preview job completion watchdog, slow diagnostic persistence graceful cancellation returning E_STOP_TIMEOUT instead of E_CANCELLED, and the independent P0 transfer correctness check. PR Windows run 39376613528 instead failed its first Designer job's 10-second watchdog (6355 passed). Neither run is erased or treated as acceptance.

The isolated unchanged 300-join diagnostic on Windows (35c3a6938c49a62ee3583fa82b4568013f1bfd6a, run 39381012901) completed in 67.55 seconds. 9323 commit calls accumulated 59.03 seconds wall / 5.42 seconds thread CPU; 9349 connection/configuration calls accumulated 3.59 seconds. These are call timings, not proof of a specific filesystem/native wait.

The candidate collects at most 32 ordinary events already available from one worker queue, without waiting to fill a batch. Job, display, heartbeat, and SQLite-write events remain barriers. The batch gets consecutive sequences in one BEGIN IMMEDIATE / FULL commit. Publication, sinks, follow visibility and success acknowledgement happen only after the commit returns. There is no uncertain-commit replay, asynchronous persistence, lowered durability, or enlarged deadline. An unacknowledged ordinary cohort is atomic rather than independently committed per event; acknowledged events remain durable. Force-stop semantics for unacknowledged queue items are unchanged.

## Verification before full-suite acceptance

51 new regression cases cover rollback, pre-commit visibility, uncertain commit non-replay, synchronous=FULL, concurrency, retention/restart, confirmed events after child os._exit, barriers, bounds, and cancellation. 193 related cases and the additional three original Windows first-red cases passed locally. The original slow-persistence test still blocks job.started on its single-event barrier; it was not bypassed by batching. Independent static integration review found no production-blocking issue. Process abrupt-exit recovery is not a hardware power-loss test.

Candidate diagnostic 479eb668097ace15bc795932fa1642d492cee9a1 (run 39382507758) completed original Windows 300 joins in 30.07 seconds, with 1382 commit calls / 21.47 seconds accumulated commit wall time, 1408 connection/configuration calls / 1.138 seconds, and 9194 inserted events. Base inserted count includes extra heartbeat events during the longer run. Both runs retain all original assertions and FULL. Different hosted runners prevent attributing all elapsed-time change to this patch; reduced transaction count is directly observed. An isolated diagnostic pass is not full-suite acceptance.

P0 is a separate prototype network pipeline, not this event persistence path. Its retained first-red had all 40 rows but both clients missed the same asset RPC deadline after 6,029,312 bytes; no decode occurred for that row. It remains independently investigated with the original 0.5-second RPC and 0.55-second result budgets.

Local complete first run and hosted full acceptance: pending.

P0 diagnostic a1f627cdc84af028f30405ef3df0fd83ca57ccb7 / run 39383374041 did not reproduce the failure: original test 1 passed in 15.26 seconds, 90 client transfers complete across 45 assets. Maximum file-read 3.691ms, executor queue 3.612ms, executor completion-to-yield 15.388ms, yield-resume 24.825ms, client chunk gap 25.092ms, complete transfer 156.708ms and client total 187.002ms. Its 500/550ms budgets were unchanged. This does not explain the prior 503ms miss, so no speculative transport change was made. Preserve it as an unexplained intermittent first-red; if normal full acceptance fails there again, observe that workload rather than repeat isolated passes.

The first local full candidate run retained one failure in the newly added fake-bridge regression: 6388 passed, 1 failed, 23 skipped, 42 subtests in 1620.71s. It incorrectly equated terminal publication with immediate bridge retirement. The fake bridge never sent the real processExited/bridgeStopped notifications, so a competing graceful-stop watcher sometimes happened to retire the handle first. The corrected new test deterministically gates both watcher orderings, proves ownership before exit notifications, supplies the missing lifecycle actions, and retains/adds handle removal, bridge removal, process close and exactly-one-terminal assertions. No production code, pre-existing test assertion or deadline changed. 85 focused cases passed after this test-only correction; independent review confirmed the stronger lifecycle contract. A new full local run and first hosted full candidate validation remain pending at this commit.
