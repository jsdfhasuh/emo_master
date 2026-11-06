# Exporter retirement recovery — focused repair receipt

Base: `cdf1250ccc41b78f79b25b87e6db197809875384` in `repo-repair`.
No commit or push was performed. The fixed baseline checkout was not modified.

## Confirmed defect and local change

The first image (ordinal 33) has a verified private snapshot and its raw-lane
reservation is released. After its completion timeout the encoder is proved
reaped. The result callback can therefore admit ordinal 34 into that lane.
If the replacement encoder then fails its ready handshake and is itself
successfully reaped, the previous implementation unconditionally quarantines
the slab and never finishes normal pending/active/busy retirement. Ordinal 34,
not ordinal 33, is left holding the queued reservation.

The repair preserves `_reap` as the safety proof. A failed replacement whose
canonical process owner was cleared by `_reap` no longer causes quarantine.
The old task completes its retirement without clearing or releasing a
successor's reservation. Each queued descriptor retires through `_run` exactly
once. An expired descriptor does not start another encoder. A later live
normal descriptor can make one local startup attempt; a failed attempt does
not recursively or eagerly retry. If reader disposal is not proved,
quarantine and the held successor credit remain intact. No timeout constant,
semaphore reset, blanket pending clear, or background retry mechanism was added.

Ready EOF is included in the same startup cleanup as a ready timeout. Failed
startup and final retirement details are bounded and emitted after the result
callback, through the existing EventStore. The exact new presentation event
is admitted to the existing JSONL operational-event whitelist. Successful
retirements do not emit this event. Diagnostic sink failure cannot retain raw
ownership or rewrite the already-published result.

`ExportPool` keeps its original constructor signature, including compatibility
with `scripts/r3_measure.py::MeasuredPool`. PresentationService installs the
optional retirement callback after constructing the pool and before admitting
image work.

## Changed implementation and regression files

- `src/emo_master/apps/runtime/presentation/exporter.py`
- `src/emo_master/apps/runtime/presentation/service.py`
- `src/emo_master/apps/runtime/events/jsonl_writer.py` — one exact presentation event whitelist addition
- `tests/runtime/presentation/test_export_restart_retirement.py`

## Exact red/green evidence

`receipts.json` records commands, working directory, interpreter, environment,
exit codes, stdout SHA-256, and tested source/test SHA-256 values.

| Receipt | Result | Meaning |
| --- | --- | --- |
| `red-final.txt` | 2 failed, 1 passed, 4 deselected | Same core tests run against the fixed base exporter using `baseline_runner.py`; ready timeout and EOF fail the no-false-quarantine assertion. Unproved reader death remains correctly quarantined. |
| `green-final.txt` | 60 passed, 5 deselected | Seven new lifecycle/logging regressions, existing presentation owner/credit diagnostics, and existing direct JSONL writer regressions. |

The deterministic process/Pipe scheduler controls only readiness, death, and
clock progression. The tests use the real ResultCollector, shared memory,
BoundedSemaphore, ExportPool submit/spawn/reap/run methods, encodeWorker private
copy, PNG codec, EventStore, SQLite persistence, and JSONL writer. Fresh ordinal
40/41 pixels are decoded to prove the later request exports its own image.

The green run explicitly deselects one real encoder/spawn regression and four
RuntimeService-level JSONL regressions. `--noconftest` bypasses the presentation
conftest import of the unavailable `examples.runtime_pages_p2` compatibility
fixture while the root agent restores the integration environment. These are
self-contained tests; no project fixture was created or reformatted.

Earlier `red.txt`, `green-initial.txt`, and `existing-focused.txt` are retained
as development receipts. `red-final.txt` and `green-final.txt` are the comparable
final receipts for this repair.

## Native encoder follow-up

The root agent subsequently restored the sparse-checkout environment and
authorized short native encoder verification. No further product source change
was made for this follow-up.

| Receipt | Result | Scope |
| --- | --- | --- |
| `native-encoder-pytest.txt` and `.json` | 1 passed in 0.69 s | Previously deselected `testRealFailedEncoderReturnsRawBeforeSlowCallback`, with normal conftest loading and no `--noconftest`. |
| `native-replay.json` and `native-replay-receipt.json` | Both cases passed; 13.01 s wall time | Real `spawn`, Pipe, shared memory, bounded semaphore, collector and encoder; healthy replacement control and a single injected replacement ready timeout. |
| `native-comparison.json` | Recorded invariants match the repair | Preserved old evidence versus repaired state, exact unchanged worker/identity functions, and base source equivalence. |

`replay_restart_recovery.py` retains the original real-process worker and
callback hand-off from `diagnostics/image/repro_restart_quarantine.py`. The
worker and item-identity functions are text-identical. The original file and
its original result JSON remain untouched. The exporter and collector sources
are also identical between the original evidence commit `f308c87b` and repair
base `cdf1250c`; hashes are recorded in `native-comparison.json`.

The original failure injection and production deadlines remain unchanged:
one encoder is stalled after its verified private snapshot, the old encoder
is actually reaped after the 500 ms completion timeout, the callback admits
ordinal 34, and exactly one replacement is prevented from reporting ready.
The native replacement startup failure plus reaping took 10013 ms in this run.

| State after replacement failure and both callbacks | Original retained evidence | Repaired native replay |
| --- | --- | --- |
| Process owner | None | None |
| Quarantined / active / busy | All true | All false |
| Pending raw descriptor | Ordinal 34 in lane 0 | Empty |
| Raw credit available | False | True |
| Ordinal 34 release proof | No release | `not_dispatched` |

After removing only the injected ready failure, the first normal new capture
(ordinal 40) started one encoder locally in about 283 ms and completed within
its unchanged 500 ms budget. Its PNG decoded to the expected new pixel value
241. Ordinal 41 reused that same healthy encoder and decoded to its own value
137. Both returned `AVAILABLE`; no retry loop or deadline extension was used.
These timings describe one controlled verification run, not a performance claim.

Both replay cases prove that every accepted descriptor retires its own credit,
all owner threads exit on close, all native processes are reaped, and all
shared-memory names can no longer be attached after unlink. The execution
receipt records identical product source and original reproduction hashes
before and after the run. The environment no longer blocked this selected
native test; no additional missing paths were observed.

## Remaining validation boundary

The earlier 60-test deterministic receipt used `--noconftest` and retains that
explicit limitation. The selected native regression above loaded conftest
normally. Neither receipt is a whole-suite or long natural-load acceptance
claim. The root agent coordinates independent review and serial integration.
No long Web/Runtime replay, desktop walkthrough, or hardware acceptance was
performed in this repair task.

This fixes the confirmed recovery defect. It does not establish the cause of
the first slowdown in the original incident or claim that the historical
runtime's missing retirement log proves a particular replacement startup error.
