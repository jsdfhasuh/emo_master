# Subscribe poll boundary: baseline evidence and design for review

Historical baseline record: the archived gate is not a candidate passing test.
Candidate implementation and checks are recorded in
[the notification report](2026-10-02-subscribe-notification.md).

Status at baseline: cloud-only characterization on production HEAD `655eeac`. No production
implementation, commit, push, full CI, Windows run, user-computer run, or system
trace is part of this change.

## Verified mechanism

`DisplayRpc.Subscribe` obtains its original incremental snapshot, optionally
yields it, and unconditionally calls `time.sleep(0.02)` before the next iteration.
`ResultStore._notify` only advances the global cursor and appends a replay entry.
It does not wake the subscriber. The name `_notify` is not evidence of a thread
notification.

The deterministic controller in
[`baseline_gate.py`](../evidence/r3-subscribe-poll-gate/baseline_gate.py)
replaces only the RPC module's `time` binding with a small namespace. Its `sleep`
method is an event gate; its `perf_counter_ns` remains the real clock. It does not
patch the shared `time` module, the store, cursor updates, deadlines, capture,
sampling, drop behavior, or result conversion. A read-only wrapper records the
completed original `_snapshot` return values. The service shell only supplies
the existing job, runtime identity, and real `ResultStore` needed by this RPC.

The real `Subscribe` generator runs in one bounded consumer thread; the real
`SyncContext` supplies cancellation. No Runtime owner, worker, exporter, gRPC
server, Qt application, image, or user project is started. All event waits and
thread joins have five-second deadlock guards. Their elapsed duration is never
used to establish correctness.

The three passing cases establish these ordered facts:

1. The original empty snapshot completes at cursor 0 with no reset. The consumer
   reaches the existing sleep call, requesting 0.02 seconds. While that precise
   continuation is gated, real `begin` and `close` publish a scalar result. A
   separate real store snapshot already sees cursor 2 and that result, but the
   subscription has performed no second snapshot and delivered no value. Only
   releasing the controller permits the next original snapshot and delivery.
2. After one immediate result at cursor 2, resuming the generator reaches the
   same sleep before another snapshot. Four results published at this gate
   advance the store to cursor 10. Once released, one stream message preserves
   the existing replay of ordinals 2, 3, 4, and 5.
3. Forty results published at that post-yield gate advance the store to cursor
   82. The 32-entry replay window has overflowed. The next message keeps the
   existing reset and authoritative latest result, ordinal 41. History and
   events remain at most 32 entries; metadata stays within its original cap.

These tests prove the continuation ordering and its existing pace/coalescing
boundary. The controller deliberately owns progress, so it is not a latency
benchmark. It does not establish a measured 20 ms penalty, a 200 ms explanation,
a whole-pipeline bottleneck, or any end-to-end improvement from a future change.

## Validation

The existing Python 3.10.21 virtual environment was used, serially, in the
approved cloud runtime slot. Raw command/output records are in
[`validation.txt`](../evidence/r3-subscribe-poll-gate/validation.txt).

- Focused baseline file: **3 passed in 0.05s**
- Focused Ruff: **passed**
- `git diff --check`: **passed**
- Production `src/` diff: **empty**

These are deliberate baseline characterization tests, not an assertion that
polling must remain forever. If the idle notification design is approved, the
empty-snapshot case must become the positive notification regression described
below. The post-yield cases remain useful as cadence/replay regression coverage,
adapted to the reviewed implementation. This report retains the baseline result.

## Existing contract and pace

The [P2 contract](../runtime-pages-p2-contract.md) preserves per-scope latest and
started/closed watermarks, bounded 32-result replay, the shared 8 MiB metadata
budget, reset/expiry fences, and read-only observation. Its network contract
admits at most two display streams and retains their admission until synchronous
work, generator close, cancellation callbacks, and gRPC completion finish.

There is no documented explicit Subscribe frequency contract in the current
contract documents. Nevertheless the code's unconditional sleep is observable
behavior: each subsequent yield requires the preceding yield to resume and a
full requested 0.02-second sleep to finish before another snapshot. It therefore
paces sustained stream traffic and allows intervening changes to coalesce. It
cannot safely be classified as only idle backoff. Replacing every sleep with a
condition wait could accelerate traffic whenever publication is frequent.

## Minimal condition design, pending independent review

Keep the production change limited to `presentation/store.py` and
`presentation/rpc.py`, if approved:

1. Add one `threading.Condition` using the store's existing `RLock`. Add a bounded
   wait helper that acquires this same condition and waits for
   `self.cursor != observedCursor`, with the existing 0.02-second timeout. The
   predicate is only a state comparison; use `wait_for` so a spurious wake does
   not start a busy polling loop. A timeout means recheck ordinary RPC liveness,
   job existence, and snapshot state, not create or drop a result.
2. Extend `_notify` with `notify_all()` after advancing its cursor and appending
   its existing replay record, while still holding the store lock. All current
   production callers already hold that lock: `begin`, result `close`, and the
   service monitor's started-watermark update. No new cursor, event, payload,
   callback, queue, registration map, background thread, or executor is needed.
3. In `Subscribe`, retain the exact existing yield/cursor/runtime-ID sequence
   and the full original sleep after every yield resumes. Only the empty/no-reset
   branch replaces its sleep with the cursor-predicate wait. Every iteration
   still checks `context.is_active()` before the next snapshot. Keep serialization
   and `yield` outside the store lock. No protocol or client change is required.

### Lost wakes and multiple subscribers

The caller passes the cursor from the completed snapshot. If publication occurs
before the wait helper acquires its lock, the changed cursor causes immediate
return. If it occurs after the predicate comparison, the publisher cannot enter
until `Condition.wait` atomically releases the shared lock and begins waiting;
the publisher's state update and `notify_all` then make it observable. This also
covers publication during conversion between the store snapshot and helper.

Use `notify_all`, not `notify`, because each admitted subscriber must advance
independently. One subscriber must not consume another subscriber's signal.
Spurious notifications without cursor changes keep waiting up to the same
timeout. Keep the existing global cursor semantics, including unrelated-job
cursor progress; changing to job-specific cursors is outside this scope.

### Cancellation, job release, service shutdown, expiry, reconnect

Do not introduce a new cancellation callback owner in this minimal version.
Cancellation is still observed at the next loop boundary. An idle helper's
0.02-second timeout preserves that finite opportunity even when no producer
publishes. The post-yield branch keeps its original finite sleep. These are
requested wait intervals, not hard wall-clock guarantees. The existing aio
adapter continues to own pending work and quota until actual retirement.

`PresentationService.release` removes a job without advancing the store cursor.
Keep that behavior: the idle timeout returns to the existing `_snapshot` job
check, which reports NOT_FOUND; do not manufacture a result event just to wake
it. Service shutdown uses the same existing release/cancellation and retirement
paths. The wait must not hold the service lock or prevent a release from taking
the store lock. Idle shutdown must not acquire a new permanently blocked owner.

Result closure and retention trimming already happen under the same store lock.
Although `_notify(result)` currently precedes `_trim`, condition waiters cannot
acquire that lock until trimming and its expiry/reset updates are finished.
They therefore observe the existing atomic post-trim snapshot. Keep expiry
watermarks, overflow reset, authoritative latest, and asset ownership unchanged.
Do not equate an asset lease expiry with a new result event.

Reconnect still starts with the existing requested cursor/runtime identity and
immediately performs a snapshot. Stale or future cursors, runtime-ID mismatch,
and lost payload recovery keep the original reset/replay/latest handling. A
condition notification is merely a recheck hint and carries no durable data.

### Lock order and bounded resources

Reuse `store.lock`; do not add a second lock ordering edge. A subscriber enters
the wait without holding service, supervisor, asset, transport, or context
locks. The predicate reads only the cursor. `_notify` must not invoke callbacks,
serialize results, acquire those other locks, or do network work. The existing
service-to-store acquisition order remains intact.

There is one condition per store and at most one pending wait per admitted
subscription, bounded by the existing two display admissions. A notification
does not enqueue a result or another executor task. Existing history, events,
latest, metadata, image, thread, executor, and client queue limits stay intact.
Keep the full post-yield sleep so frequent producers do not create a new
unpaced sequence of stream responses. No changed sample/drop policy is proposed.

Earlier idle wakeup can still change timing and message composition: it may
publish a started-watermark-only snapshot before result closure, whereas an old
poll might have coalesced both. The following full cooldown can then delay the
closed result. This is a review constraint, not a guaranteed performance win.
If preserving identical temporal coalescing is required, this design cannot be
accepted as equivalent. The invariant proposed here is preserved result rules,
budgets, and post-yield pacing, with explicitly changed idle observation timing.

## Required implementation checks, not run in this baseline-only change

- Gate after the original empty snapshot, publish before helper lock acquisition,
  and prove immediate return from the changed-cursor predicate without relying
  on its timeout; also gate after actual waiter registration and prove notify
  wakes it. Cover begin/high-only and close/result updates separately.
- Register both admitted waiters before one publication; verify both get the
  same authoritative state. Send a spurious notification and verify the
  unchanged cursor does not trigger repeated snapshots or yields.
- Use a controlled wait/clock to flood many notifications within the post-yield
  cooldown. Verify none authorize another snapshot/yield, and each next yield
  still requires the full existing sleep after resumption. Test publication
  immediately before and after the idle/cooldown boundary, including high-only
  followed by closure. Retain both four-result and overflow burst cases.
- Cancel while idle, during cooldown, before wait entry, and immediately after
  publication. Verify the bounded wait path returns, generator retires, and aio
  admission remains owned until real cleanup and completion. Exercise job
  release and service shutdown without requiring a cursor change.
- Retain real-store replay/reset, expiry-under-byte-pressure, quiet-scope latest,
  reconnect identity, and final-payload recovery checks. No timeout, sample,
  capture deadline, replay cap, or resource budget may be enlarged to pass them.

Only after design approval should production code and the bounded implementation
tests above be introduced. Any later performance claim needs separate matched
measurements and cannot be inferred from this ordering proof.
