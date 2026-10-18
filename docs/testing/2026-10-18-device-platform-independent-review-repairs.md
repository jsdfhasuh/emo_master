# Independent Review Repairs, Batches 1 And 2

## Scope And Status

Date: 2026-10-18. Source baseline: `c097c39934955ababc679f2fb704b50dba488030`.
This report records the source-repair checkpoint, before commit/push. Subsequent
local frozen builds and self-tests are documented in
[the frozen handoff](2026-10-18-device-platform-review-frozen-handoff.md).
Publication does not constitute a release, deployment or hardware acceptance.
The later isolated staged-tree checks and delivery scope are recorded in
[the publication checkpoint](2026-10-18-device-platform-repair-publication.md).

The independent review was reproduced before changing code: **10 failed,
1 skipped** on Windows / Python 3.10. The skipped test is the review's superseded
low-level R04 probe, not a repaired test. Its valid real-spawn replacement runs.

All ten numbered findings now have source repairs and passing original probes.
Batch 2 adds behavioral coverage for R01/R07/R10 and the workflow-package
dependency gap. This is software evidence, not independent re-review approval,
human UI acceptance or hardware acceptance. Frozen evidence is recorded separately.

| Finding | Current Status | Implementation And Evidence |
|---|---|---|
| R08 / P1 | Repaired; software verified | Explicit legacy migration context. Normal new IMV nodes inherit the selected device without legacy defaults. Native Qt completion/save and legacy migration tests pass. |
| R04 / P1 | Repaired; software verified | Terminal Join retires live deliveries and their leases. A separate bounded read-receipt cache supports duplicate reads without replay. Real Repeat(300), two persistent consumer processes, and image-ownership regressions pass. |
| R05 / P1 | Repaired; software verified | Drain parks the producer between explicit outer iterations. Nested cycles and inherited consumers finish; queued deliveries count as in flight. Real no-Join queued consumers, nested While scopes, cancellation, and unrelated Job tests pass. |
| R09 / P1 | Repaired; software verified | Initial loads, old-snapshot recovery, and writes ingest declared resources into persistent Runtime ownership. Missing original directories do not invalidate those copies. Recovery errors still block running, but allow explicitly validated repairs. |
| R02 / P1 | Repaired; software verified | One capability projection governs both admission and worker installation for defaults, node overrides, and dynamic bindings. A live spawned While switches sources only at the next iteration. |
| R03 / P2 | Repaired; software verified | Device name/description are excluded from execution identity. Live rename preserves execution revision, Job PID, driver identity, and connection generation. |
| R06 / P2 | Repaired; software verified | Explicit cancellation reclaims unreferenced READY uploads. Online references and preparation pins prevent deletion. Cancelled-transfer tombstones are bounded; automatic local ingestion has separate cleanup ownership. |
| R01 / P1 | Repaired; software verified | Full-candidate explicit stop/drain replacement. Identity is execution-significant; all old Jobs retire, adapters are rebuilt, and old connection intents are not carried over. Atomic source path/history/global namespace, staged candidate resources, restart recovery, cancellation and SQLite rollback are tested. |
| R07 / P1 | Repaired; software verified | The online-run button opens a separate snapshot-only monitor and cancels the draft-backed start. Runtime instance and document revision fence the start. Native Qt tests verify draft/disk/undo preservation, online targets/layout, stop, stale rejection and original-request reconciliation after a lost response. |
| R10 / P2 | Repaired; software verified | Durable receipts and memory attempts rotate under revision fences, without increasing limits. Tests cover expired replay, restart recovery, same-revision failures/cancellations and transaction rollback. |

The additional workflow-package gap is implemented as portable schema 1.3:
resource bytes and node/device resource/site dependencies travel together.
Legacy schemas remain readable. Native import confirmation and draft undo/redo
are covered. Portable resource and file-size limits are explicit below.

## Lifetime Contracts

### Dispatch And Drain

- At most 256 active, not-yet-consumed dispatch records remain admitted per group.
  This is no longer a lifetime limit on sequential successful Joins.
- A terminal Join transfers output frame ownership to its source, then retires
  delivery records and delivery-owned frames/cycles. It preserves frame metadata
  needed by diagnostics, but does not retain the full request/cycle snapshot.
- The last 256 consumed Join receipts are available for repeat reads. They own no
  frame leases. Reading an explicitly released image fails; the cache does not
  resurrect it. End-of-cycle/owner retirement removes corresponding receipts.
- A logical dispatch sequence at or below the source high-water mark cannot
  execute again after its receipt expires. Changed duplicate payloads are rejected.
- An explicit While iteration is a legal drain boundary. Its persistent root
  lease is not mistaken for active work once the producer is parked there.
- Consumers execute already-admitted work, including their own iteration scopes.
  Queue/active-slot checks happen outside the cycle lock. Nested work is not
  stranded behind the producer's admission gate.
- Cancellation clears the gate. Jobs already stopped by an explicit transition
  are not restarted automatically; physical effects are not rolled back.

### Persistent Resources

- Declared input resources are size/hash-verified, copied through the bounded
  transfer mechanism, and pinned before candidate preparation. The source path
  remains documentary metadata, not the authoritative copy of declared bytes.
- Missing original directories are accepted only on the online recovery/write
  path. Relative output paths are not silently rebased to Runtime storage, and
  undeclared missing input files still fail validation.
- Initial load and recovery of old online snapshots ingest original resources
  while they are available. An already-broken old snapshot requires explicit
  repair/upload; missing bytes are not reconstructed or guessed.
- Persisted preparation pins protect the transfer copies until discard or
  publication. After atomic configuration commit, the online payload itself
  protects them. Cancellation checks both within its SQLite transaction.
- Automatically ingested local copies have a separate durable ownership ledger.
  Failed/cancelled preparations and startup recovery reclaim unreferenced copies;
  unknown files and user-created READY uploads are not swept by this cleanup.
- User READY uploads require explicit CANCEL when no longer referenced. A copy
  used by the online payload or a prepared candidate cannot be cancelled.
- CANCELLED transfer status keeps a 256-row recent window and does not consume
  active-transfer quota. After expiration, CHUNK/COMMIT fails as unknown; a new
  BEGIN creates an empty transfer, never republishes old content.
- Old execution materializations still use the existing independent resource
  lease journal. Retiring an unreferenced durable input does not delete bytes
  held by a live old Job. Historical graph snapshots do not imply retention of
  every historical input blob for execution.
- Recovery failure keeps StartJob blocked. Explicit selected writes can validate,
  ingest replacement resources, commit, and restore readiness while idle. The
  Designer repair controls remain enabled; its run control remains disabled
  until recovery succeeds.

### Project Replacement And Online Run

- A project-ID change requires the complete normalized candidate plus an
  explicit stop/drain policy. Partial cross-project merges are rejected.
- All old-project Jobs and adapters retire even when device definitions match.
  Communication is reconfigured for the new identity/source root; old listening,
  connected and paused intentions do not auto-start the new project.
- The new project uses a Runtime-owned source root. Declared candidate uploads
  are owned by the candidate identity, not the currently online identity.
  SQLite commits identity, source path, history, persistent variable definitions
  and the write receipt together. Old project variable values stay isolated.
- Failure/cancellation preserves the previous authoritative configuration.
  Already executed stop/disconnect effects are not rolled back or restarted.
- Historical graph lookup validates the requested snapshot's project identity;
  its Job list and history cannot silently mix different projects.
- Online-run uses the snapshot's startup workflows or entry workflow, never
  the draft's targets, layout, parameter values or undo stack. Its graph monitor
  consumes Runtime events; it does not request work-page presentation capture.
- A stale instance/revision rejects startup before creating a Job. A transport
  exception stops the remaining batch and retains the original start request
  for query. No automatic retry or new request is created to resolve uncertainty.

### Configuration Receipt Rotation

- The durable window retains the latest 4096 successful configuration receipts.
  Eviction and online revision advancement share the same SQLite transaction.
- The memory window retains at most 2048 attempts. Older-revision terminal
  attempts can retire; active attempts cannot. Exact expired requests fail the
  original revision/Runtime fence before repeating preparation or effects.
- If failures/cancellations fill the window at one revision, an atomic,
  document-only fence advances that revision and records identical history.
  Execution revision/payload are unchanged; the caller must compare again.
  Transaction failure does not forget attempts or advance in-memory authority.
- A retained ID still detects altered content. An expired ID submitted with
  a new revision is a new request, not perpetual idempotency. Unknown status
  requires checking online authority, not blindly resubmitting a changed ID.
- These limits concern configuration writes, not hardware ForceIP receipts or
  the separate StartJob ledger. Their existing limits are unchanged.

### Portable Workflow Dependencies

- Schema 1.3 closes dependencies over packaged workflows and referenced devices.
  Only referenced resources are embedded; each is checked by size and SHA-256.
  Resource bytes total at most 64 MiB; JSON files are bounded to 96 MiB.
  Larger assets require the existing full-project deployment package.
- Declared site values, including addresses, output paths and secret references,
  are cleared on export. This is not a general secret scanner for undeclared
  arbitrary parameters. Source drafts/files remain unchanged.
- Preview shows resource manifests and site dependencies. Dependent devices
  must be imported as new disabled definitions, not merged over a live site's
  device bindings. Devices without such dependencies keep explicit compatible
  reuse. All imported references and shared site-field IDs are remapped.
- Resource-bearing import requires a saved target directory. It allocates a
  fresh owned resource directory, never overwrites existing files, validates
  the complete draft, and cleans its new files on installation failure.
- Successful import is one undoable draft command. Undo retains bytes for redo;
  it does not garbage-collect assets. Site values must be filled and new devices
  explicitly enabled before use. In-project copies reuse existing device/asset
  identities and remap node dependencies without filesystem writes.

## Verification Evidence

JUnit reports are local to `build/independent-review-c097c399/`; they are not
embedded in a published artifact. Overlapping runs below must not be added up
as distinct coverage. Original failures are preserved, not overwritten.

| Report | Result | Scope |
|---|---|---|
| `windows-independent-recheck.xml` | 10 failed, 1 skipped | Unmodified reviewed source, Windows baseline |
| `repair-independent-recheck.xml` | 5 passed, 5 failed, 1 skipped | First five repairs against the review's original probes |
| `repair-independent-final.xml` | 7 passed, 3 failed, 1 skipped | Original probes; remaining failures are R01/R07/R10 |
| `fix-batch1.xml` | 45 passed, 1 failed, 1 teardown error | Preserved initial frame-diagnostic/UI-fixture failures |
| `fix-batch1-recheck.xml` | 83 passed | Earlier affected regression after corrections |
| `cycle-source-repair.xml` | 20 passed | Live node source transitions and digest contracts |
| `resource-repair-first.xml` | 24 passed, 2 failed | Preserved obsolete source/cancellation expectations |
| `resource-lifecycle-repair.xml` | 17 passed | Resource recovery and preparation pins |
| `dispatch-lifecycle-repair.xml` | 3 failed | Preserved invalid test fixture: iteration boundary on Repeat |
| `dispatch-lifecycle-recheck.xml` | 3 passed | Correct While fixture; real Repeat(300), queued drain/cancel |
| `repair-expanded-regression.xml` | 250 passed, 2 failed | Preserved source-integrity expectation and communication-key regression |
| `repair-expanded-recheck.xml` | 252 passed | Core project, Runtime, devices, resources, dispatch, loop and Designer affected set |
| `repair-native-ui.xml` | 13 passed | Windows Qt platform; IMV/PLC wizard and configuration dialog signal paths |
| `resource-lifecycle-final.xml` | 20 passed | Valid PNGs, real Job after repair, restart, upgrade, SQLite rollback |
| `dispatch-frame-final.xml` | 20 passed | Real consumers plus cached-image release and replay rejection |
| `resource-ownership-final.xml` | 68 passed | Final automatic-ingestion cleanup, restart reclamation, resource/core/communication regressions |
| `repair-independent-owned-final.xml` | 7 passed, 3 failed, 1 skipped | Original review probes rerun after the final ownership cleanup; only R01/R07/R10 fail |
| `batch2-independent.xml` | 10 passed, 1 skipped | Original independent probes after R01/R07/R10 source repairs |
| `batch2-targeted.xml` | 18 passed | Initial replacement, receipt rotation and configuration regressions |
| `batch2-behavior.xml` | 13 passed, 2 failed | Preserved actual Qt failures from mistakenly requesting presentation capture |
| `batch2-ui-diagnosis.xml` | 3 passed, 2 failed | Preserved diagnosis of that startup capability error |
| `batch2-native-final.xml` | 22 passed | Replacement/rotation plus Windows native online-run and comparison controls |
| `package-first.xml` | 37 passed | Existing workflow-package compatibility suite |
| `package-resources-first.xml` | 9 passed, 1 failed | Preserved mutable-model path-validation test failure |
| `package-native-final.xml` | 48 passed | Resource/package compatibility, malformed input, rollback, native import and undo/redo |
| `batch2-native-ui-final.xml` | 23 passed | Native online-run, cross-project readback consent, dependency import and undo/redo |
| `batch2-expanded.xml` | 384 passed, 1 failed | Old test still required rejecting resource-bearing workflow packages; updated to assert dependency preservation |
| `batch2-expanded-final.xml` | 386 passed | Same expanded core/Runtime/Designer set, including the added cross-project consent test; no hardware |
| `batch2-pins-ui-final.xml` | 12 passed | Stronger candidate resource pin/rollback assertions and final native online-monitor controls |
| `batch2-independent-final.xml` | 10 passed, 1 skipped | Original probes rerun after all batch-2 implementation changes |

The expanded suite precedes the final automatic-ingestion ownership cleanup.
Its affected resource/core/communication tests were rerun separately above; it
must not be described as a complete post-cleanup repository regression.

That preceding paragraph refers to the batch-1 `repair-expanded-recheck.xml`.
The batch-2 expanded run includes that cleanup and all listed batch-2 behavioral
changes. Final compact monitor button sizing and stronger pin assertions were
checked again in `batch2-pins-ui-final.xml`. A native monitor capture is retained
as `build/independent-review-c097c399/online-run-native.png` and was visually
inspected. These are overlapping scoped suites, not the entire repository suite.

Static checks: Ruff on source and changed tests, mypy on 491 source files,
`scripts/gen_proto.py --check`, and `git diff --check`. Mypy uses repository
defaults and does not check every untyped function body.

Native Qt automation is not a human desktop acceptance. Spawn tests use existing
simulated devices or scalar/image fixtures, not real cameras, PLCs, vendor SDK
operations, field networking, or hardware address changes. At the source-repair
checkpoint no frozen executable had been rebuilt. The subsequent local Designer
and independent Runtime rebuilds and successful frozen self-tests are recorded in
[the frozen handoff](2026-10-18-device-platform-review-frozen-handoff.md).
Older EXEs and already-running processes still do not contain these changes.

Unrelated pre-existing changes to `field_tests/plc_dual_mc/build_project.py` were
preserved. No commit, push, release, or hardware operation was performed.

## Remaining Acceptance Boundary

1. Local Designer/Runtime rebuilds and frozen self-tests are complete for the
   manifest-identified dirty working tree; see the frozen handoff above. Human
   UI acceptance remains pending. These are not release artifacts built from
   a published repair commit; recheck source identity before any later release.
2. With simulated devices, manually compare A online against a different B
   draft, cancel once, then confirm stop/drain replacement. Check history,
   source identity, isolated variables and no unexpected reconnect or start.
3. Keep unsaved local parameters/layout/startup targets, choose online run,
   verify the separate graph and stop controls, then verify draft undo/redo.
   Exercise a lost response using the original request lookup, not a new start.
4. Export/import a resource-bearing workflow through the normal UI. Confirm
   manifests, new disabled devices and empty site fields, then undo/redo and
   save/reopen the destination. Verify the resource bytes remain available.
5. Only after rebuilt-application and human UI checks, use isolated real-device
   acceptance for vendor SDK, camera/PLC, networking and reconnect behavior.
   No physical outcome is established by the simulated tests in this report.
