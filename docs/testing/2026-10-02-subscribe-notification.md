# Subscribe idle notification candidate

Status: local integration on diagnostic-only `ecf8bcfcf193e05807556340d670f05c0f3d9e15`
(parent `655eeac84d0602aa670c76af67679509782bbd7f`). Production and regression-test
bytes are identical to independently reviewed candidate
`7d8c9e9af0901253fce15d7bd65efa66f14e12a8`. Its full cloud CI passed, and one
reviewed cloud/offscreen ABBA observed lower delivery p95. The
[comparison summary](2026-10-02-subscribe-notification-comparison.md) preserves
source identities, hashes, tradeoffs, and the **NOT_ASSESSED** performance status.
A separately reviewed no-capture preflight follow-up,
`4db426f24dd4cc8466a7a1eb7d14c93860aa66fa`, is also integrated: it corrects the
rejected-environment fixture and adds one preflight-only workflow. The other five
diagnostic files and all nine pre-existing workflows, including the original
capture workflow, remain byte-identical to `ecf8bcf`.

This report records the candidate evidence available at integration freeze.
The separate integration CI record must identify the final frozen commit and
its before/after tracked-file hashes; the earlier candidate run alone does not
validate the integrated tree. This local integration is not publication,
a Windows result, a user-computer test, or performance acceptance.

## Change and preserved behavior

Only two production files change:

- `presentation/store.py` creates a `Condition` sharing the existing `RLock`.
  `_notify`, whose existing callers already hold that lock, advances the original
  cursor/replay entry and calls `notify_all`. `waitForChange` uses `wait_for` with
  the cursor predicate and a fixed 0.02-second timeout.
- `presentation/rpc.py` uses that wait only after a completed unchanged-cursor/no-reset
  snapshot (authoritative latest results may still be present). Every resumed yield still updates the original cursor/runtime ID and
  completes the full existing `time.sleep(0.02)` before any further snapshot.

The cursor predicate prevents a missed-wake race: a publication before wait entry is visible to
its predicate; publication after registration releases all subscribed waiters.
A notification carries no payload and cannot authorize replay, change a result,
release admission, or change retention. Serialization and yielding remain
outside the store lock. There is no new callback, thread, queue, admission,
supervisor lock, SQL path, timeout extension, or resource budget.

Idle cancellation, job release and service shutdown retain their original
liveness checks through the finite timeout. Release still does not advance the
cursor. A requested 20 ms wait is not a wall-clock retirement guarantee.

Earlier observation can emit a started-watermark-only update before closure.
The following unchanged cooldown can then delay observation of the closed result.
The measured reduction in the fixed cloud comparison does not establish a
general latency benefit, a 200 ms target, or unchanged temporal coalescing.
Result rules, bounded resources, and post-yield pacing are the preserved invariants.

## Regression evidence

[`test_subscribe_notification.py`](../../tests/runtime/presentation/test_subscribe_notification.py)
executes the real `DisplayRpc.Subscribe`, snapshot/protobuf conversion,
`ResultStore`, and synchronous cancellation token. Narrow service shells are
used where only the RPC boundary is relevant; service-disposal cases use the real
`PresentationService` fixture. The two aio cases use the actual loopback gRPC
server, classified executors, `Subscribe` generator, cancellation and retirement.
They start no execution worker, exporter, Qt application, device or user project.

The controlled `Condition.wait` wrapper announces registration while the shared
lock is held, asserts the helper requested exactly 0.02, and invokes the real
wait with `None`. That test-only gate lets publication acquire the same lock and
release the real condition waiter without relying on controller scheduling
inside a 20 ms window. The return must be `True`, proving notification rather
than timeout. For the spurious-wake case only, the unchanged standard-library
`wait_for` code is rebound to this condition with a local frozen clock. This
permits a second actual wait registration after an unchanged-cursor notification,
without changing the production predicate or a global threading/time binding.
Cleanup advances that invocation's clock and restores finite waiting before
notifying, so a spurious cleanup wake cannot strand a consumer. Other tests use
the original finite wait and unmodified `wait_for`; all controller waits/joins
have generous five-second deadlock guards, never latency assertions.

The 23 passing cases cover:

- Publication after the completed snapshot but before helper entry, with any
  actual condition wait forbidden because the changed cursor must return at once
- Real registered-waiter notifications for begin and close, with one and two
  independent subscribers, and spurious wake re-registration without a snapshot
- The real unchanged-cursor timeout and cancellation before wait, while idle,
  during cooldown, and immediately after publication
- Real service release and close with no cursor increment; the idle stream exits
  through the original NOT_FOUND path
- Begin-only notification followed by close during the full cooldown; four-result
  burst replay and 40-result overflow preserve reset/latest and 32-entry caps
- Immediate reconnect, stale/future cursor and runtime-identity reset, quiet-scope
  latest preservation, final-payload snapshot recovery, and atomic post-trim
  expiry under a lowered test-only byte cap
- Actual aio cancellation holding both display admissions while synchronous
  waiters are still pending, rejection of a third stream, control availability,
  and successful retirement; actual server close with the original finite timeout

The first focused run passed. Static review then identified that a real timeout
may legitimately produce several empty snapshots before the test controller is
scheduled. The cancellation assertion was narrowed to eventual retirement,
unchanged state and no yield for that ungated case; exact snapshot-count checks
remain for the controlled cases. No failure was hidden or retried to claim a pass.

## Validation and historical baseline

[`validation.txt`](../evidence/r3-subscribe-notification/validation.txt) records
historical focused-stage commands and raw-output links. Final focused run:
**23 passed in 0.39s**. Focused Ruff and `git diff --check` passed. Subsequently,
the complete original `scripts/ci_check.py` ran once on clean frozen `7d8c9e9`:
proto drift, Ruff, mypy, and **2488 passed, 4 skipped, 27 subtests passed**. All
1371 tracked-file SHA-256 hashes stayed identical and both owner censuses were
empty. The [full raw CI log](../evidence/r3-subscribe-notification/candidate-full-ci.txt)
and [source evidence index](../evidence/r3-subscribe-notification/source-evidence.json)
identify that run. These checks do not establish end-to-end performance acceptance.

The original poll characterization remains separately archived in
[`baseline_gate.py`](../evidence/r3-subscribe-poll-gate/baseline_gate.py) with its
[original validation](../evidence/r3-subscribe-poll-gate/validation.txt) and
[baseline report](2026-10-02-subscribe-poll-gate.md). It is not collected as a
candidate regression: its empty-branch assertion deliberately describes the old
poll behavior. Its historical three-test pass must not be presented as a pass
against this candidate. The original main checkout and its untracked baseline
files remain unchanged.
