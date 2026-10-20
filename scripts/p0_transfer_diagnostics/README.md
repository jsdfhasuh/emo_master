# Opt-in P0 transfer timing diagnostic

This branch only adds files. The original continuous test, scenario, supervisor,
GO handshake, quotas, assertions, 500 ms RPC/read budget and 550 ms result budget
are unchanged. The pytest plugin changes only the continuous scenario subprocess
module entry, which installs timing observers and executes the original module.
Other scenarios and legacy asset RPCs are not instrumented.

Run on Windows with workflow_dispatch, or locally:

    P0_TRANSFER_DIAG_DIR=/tmp/p0-transfer-evidence python -m pytest -p scripts.p0_transfer_diagnostics.plugin tests/p0/test_continuous.py -q

The workflow always uploads the diagnostic JSON, including on test failure.
Artifacts contain at most 256 records, scalar counters/timestamps, phase names,
exception type names and generated asset UUIDs. They never contain image bytes,
image hashes, request contents, job labels or local file paths. Two client
records and two server records correlate through the same asset_id; rpc_id
identifies each observation. Timestamps use the same process monotonic clock.

Interpretation:
- read_max_ns measures a file handle read call, not network transport.
- iterator_max_ns includes advancing the reader generator, including locks and IO.
- executor_queue_max_ns is submit-to-worker-start, including worker availability.
- executor_completion_to_yield_max_ns includes asyncio resumption and bridge work.
- yield_resume_max_ns measures suspension across the async yield: it INCLUDES
  gRPC transport/backpressure and scheduling, and is NOT pure file-read duration.
- client_chunk_gap_max_ns includes receive iteration and client processing since
  the preceding chunk. The phases retain the existing client phase timestamps.
- *_offset and *_end_ns locate the maximum interval. Last executor/read/yield
  timestamps help correlate the final incomplete batch. Queued transport bytes
  may differ from bytes received; neither counter proves delivery.

Observers add overhead and can perturb scheduling; this is diagnostic evidence,
not a performance acceptance result or a corrective change. No file output occurs
inside transfer callbacks. JSON is written at normal process exit, so a hard
watchdog kill may leave no artifact; upload then correctly reports missing data.

Transparency tests:

    python -m pytest scripts/p0_transfer_diagnostics/test_transparency.py -q
