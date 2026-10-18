# R2.1 Dispatch, Configuration Transition, And Publication

## Scope

Canonical checkout: `D:/jsdfhasuh/documents/my_project/emo_master`.
Base commit: `63070277133b89d61a5d27dde6d5f95494b4dce0`, plus the existing dirty
implementation. This work has not been committed, pushed, deployed, or released.
Private field projects and `field_tests/plc_dual_mc/build_project.py` were not
changed by this implementation. Every execution probe uses synthetic devices or
an explicitly injected SDK failure, never a physical camera or PLC.

The earlier replacement report describes the preceding implementation. Its
statements that Dispatch, automatic transitions, and publication are absent are
superseded by the code and scoped evidence below. Its frozen executable is not
the current deliverable. This report does not declare all 88 acceptance IDs PASS.

## Implemented

### Dispatch And Join

- Explicit Dispatch/Join node kinds, typed target mappings, dependency closure,
  bounded target queues, recursion and capacity validation, Designer library
  entry, structured editing, undo, draft-identity fences, and workflow packaging.
- Admission reserves a producer and every declared persistent consumer before
  the first acquisition. Consumers use independent spawned processes, not
  competing readers of one shared work queue. A failed partial startup marks
  unstarted consumer records terminal and retires the admitted group.
- Each delivery has group, dispatch, slot, invocation, cycle, and plan identity.
  Monotonic logical dispatch sequences deduplicate independently of IPC envelope
  sequences. Expired receipts never trigger replay. Failure is terminal; no
  crash-recovery exactly-once guarantee is made.
- Runtime owns bounded original-frame and derived-image shared memory. Queued
  and active consumers retain the exact source plan and image. Each ndarray is
  an isolated copy; an in-place mutation is encoded into a new Runtime-owned
  frame instead of relabeling the original.
- Join validates group/dispatch/cycle/plan identity and returns per-slot results.
  False/NG is a successful result, not an execution failure. Partial delivery,
  queue saturation, worker exit, timeout, and cancellation remain distinct.
- Source termination without Join does not retire downstream ownership. Stop
  cancels unexecuted deliveries; active consumers retain their memory until
  actual retirement. Continuous probes reuse three workers across invocations.

### Configuration And Resources

- Explicit `reject`, `stopAffected`, and `drainAffected` policies in RPC/client/UI.
  Validation/save do not stop Jobs. Full candidate, resource, device identity,
  and page binding validation precedes the requested disruptive transition.
- Affected workflow/device dependency analysis includes Dispatch groups. Resource
  changes target the actual changed resource references or changed bindings.
  Unrelated synthetic continuous Jobs retain their adapters and continue.
- Drain closes root-cycle admission, waits for retained work, then stops affected
  persistent workers. The 30-second drain budget has no implicit force-kill
  fallback. Cycle retirement does not acquire the project lock.
- Disconnect and reconnect execute on the device FIFO. A reconnect checks the
  execution revision, adapter identity, control-intent revision, and pause state
  when it actually runs. A newer committed revision supersedes old queued intent.
  Configuration commit and connection outcome remain separate facts.
- Connection observations are appended to durable receipts. A pending observation
  from another Runtime instance is reported as `UNKNOWN_AFTER_RESTART`, not
  replayed or declared successful. Failure does not pretend stopped Jobs or
  physical state were rolled back.
- Declared operator/device file parameters are copied into immutable, hashed,
  private Runtime paths before installation. Rollback removes only owned
  candidate files. Copying rechecks hashes and size; corrupt retained copies are
  rejected. Content-identical inputs reuse retained paths.
- This Runtime reclaims its obsolete copies only at a proven quiescent boundary,
  after workers, diagnostics, and plan leases retire. It preserves current
  document/device references. The 4 GiB aggregate budget fails closed; it does
  not evict files from under a consumer.
- Partial-write confirmation now finds transitive missing workflow, Dispatch,
  device, resource, resource-target, page source/scope/navigation, and ordering
  dependencies. It never silently copies edits to an existing dependency or
  undoes a selected deletion. Remaining incompatible selections fail validation.
- Runtime and production policies now use the prepared publication path, with
  execution retirement required for non-cycle-safe changes and no implicit start.
- Compatible global-variable definitions are validated without mutation, then
  synchronized in the same SQLite transaction as the configuration and receipt.
  Existing persistent values and removed IDs are retained. Identity/type/lifetime
  changes require a new ID instead of coercion or silent reset. Injected receipt
  failure rolls back definitions, initial values, and configuration together.
- Communication changes reserve the existing manager rather than constructing a
  second listener. Explicit stop/drain releases users and the old listener before
  publication. Previously active enabled listeners are restarted after commit;
  bind failure is a separately persisted connection outcome, never a rollback.
- Operator requirements accept installed IDs or exact operatorId/version locks.
  Missing/mismatched dependencies fail validation, including recovery. This does
  not install or replace executable plugin code.

### Cancellable Hardware Wait

- Device metadata declares a cancellable-wait action, timeout field, and required
  mode. Only that contract admits an unlimited executor budget. Queue budgets and
  connection budgets remain finite. The IMV declaration permits zero capture
  timeout only for hardware-trigger mode; legacy migration preserves this intent.
- The Host wire uses a null relative budget, never JSON Infinity or a remote
  monotonic timestamp. The SDK wrapper polls at most 100 ms per GetFrame call;
  cancellation and the existing stuck-Host retirement path remain active.
- Real spawned Job/Host tests use only a fake SDK. A lost native receipt during
  stop stays UNKNOWN, with device outcome preserved in node diagnostics; retirement
  does not imply that no physical trigger occurred. No capture is replayed.
- Fixed a Windows graceful-stop watcher race with process-handle close: liveness
  observations now use the Supervisor lock and never block join on a handle that
  another retirement thread may close.

### Resource Upload And Recovery

- Added explicit `TransferProjectResource` RPC with LOOKUP, BEGIN, CHUNK,
  COMMIT, STATUS, and CANCEL. Each call is fenced by Runtime instance and online
  project identity. Chunks are at most 512 KiB; declared content, offsets, length,
  SHA-256, a 4 GiB reservation budget, and 4096 transfer records are enforced.
- Repeated accepted chunks must match existing bytes; an incomplete or mismatched
  transfer cannot become READY. Completed content is immutable and cannot be
  removed with CANCEL. Identical completed manifests are reused. No uploaded
  content is installed as executable code or published as configuration.
- Upload owners for the same SQLite store and cache share one weakly registered
  lock. Schema initialization and capacity reservation use immediate SQLite
  transactions. A two-owner concurrency test admits only one full-budget
  reservation; closing the other owner cannot cancel a pending transfer.
- The comparison dialog separately confirms the candidate resource inventory.
  Unselected draft resources stay out. Upload does not advance revisions or
  perform device actions; a later explicit write uses the ordinary commit path.
- Runtime resolves uploaded manifests rather than pretending Designer paths exist
  remotely. A real loopback gRPC test uploads a file absent from the Runtime
  project, commits its binding, removes the Designer source, and recovers the
  committed configuration after a Runtime restart.
- Added a SQLite ownership journal before preparing files. A real spawned test
  process reserves and writes a pending file through that journal, then exits
  with `os._exit`; restart removes only recorded unpublished files and
  leaves unknown files untouched. Failed unlink retains ownership for retry.
  Exposed files require actual owner retirement, not a parent-process crash.
- Normal Runtime close marks its generation retired only after all producer,
  worker, device, and persistence-owner shutdown gates. Clean restart reclaims
  those old copies and reconstructs current immutable inputs. Crash-exposed
  copies remain retained and count against capacity; complete cross-crash child
  ownership proof is still outstanding.
- Pure preflight now reports affected Jobs, device disconnects and TCP listener
  changes without performing them. Unchanged resource-backed device fields reuse
  verified immutable paths, avoiding false adapter replacement. TCP changes are
  scoped to communication users; an explicit STOP supersedes an older automatic
  listener-restart intent.

### Online Graph, Migration, And Pages

- SQLite stores each authoritative document snapshot atomically with its commit.
  Read-only online/history graphs are independent of the local draft. Results
  must match both document and execution revisions. Missing historical data is
  an explicit error, never substituted with a current draft.
- Formal graph ports are read without executing operator parameter validation
  or requiring old input files. Project workflow order and entry selection are
  preserved. Refreshing results does not recreate the same graph or reset zoom.
- Resource/site binding UI supports metadata-declared node and device targets,
  resource imports, explicit binding modes, required fields, and versioned secret
  references. Imports are copied only on confirmation and rolled back on failure.
  Removing a resource registration preserves its disk file and rejects live
  references. Unknown target records remain intact.
- Whole-project migration inventory lists the original parameters and sources.
  The normal node wizard preserves explicit camera `keep` behavior. Supported
  legacy variable targets become device-value sources; resource/site targets
  move to device properties after an explicit shared-device-impact confirmation.
  Node, device, source, and binding edits share one undo transaction and do not
  write the project file before an explicit save.
- Page Designer publishes through the same candidate comparison and authoritative
  commit. Runtime validates complete page bindings. The operator UI closes old
  display sessions before constructing published pages, and does not rebind old
  Job results to new page bindings. Local project files remain unchanged.

## Evidence

Artifacts below are under `build/device-management-imv/`. Counts overlap and
must not be added together. Interpreter:
`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`.
Tests set `HUARAY_CAMERA_SMOKE=0`; ordinary UI tests use offscreen Qt. Native
screenshots explicitly use Windows Qt and are labeled separately.

| Artifact | Result And Boundary |
|---|---|
| `finish-ui-v2.xml` | 4 passed; repaired history default selection and resource-bound page compilation |
| `dependency-closure.xml` | 22 passed; transitive selection and comparison UI |
| `dispatch-resources-v3.xml` | 76 passed; configuration, resources, scheduling, history, package dependencies |
| `dispatch-derived-groups.xml` | 16 passed; actual independent groups and derived images, source/consumer plan retention |
| `page-publication-sessions.xml` | 3 passed; invalid publication does not stop production; old display session retired |
| `migration-bindings.xml` | 29 passed; source/target migration, confirmations, local-only transaction and undo |
| `dispatch-package-source.xml` | 6 passed; source package entry and expanded managed Dispatch/Join self-test |
| `dispatch-final-focused.xml` | 59 passed; final runtime changes, partial startup cleanup, resource corruption rejection |
| `finish-ui-native-final.xml` | 9 passed; native Windows Qt at 150 percent scale, source UI and migration probes |
| `runtime-broad-fixes.xml` | 6 passed; ordinary Job admission and workspace retirement regressions repaired |
| `runtime-dispatch-final.xml` | 2059 passed, 2 skipped, 941.84 seconds; preceding Dispatch baseline, before owner/wait extensions |
| `combined-regression-repairs.xml` | 47 passed; immutable installed resources and three-workspace navigation assertions |
| `configuration-owners-v3.xml` | 45 passed; compatible variables, atomic rollback, policy publication, loopback listener replacement and bind-failure receipt |
| `cancellable-hardware-wait.xml` | 44 passed; executor budget contract, fake SDK Host cancellation and existing camera/metadata/UI regressions |
| `dispatch-crash-and-retirement.xml` | 40 passed; actual consumer process terminated after durable counter effect, no replay, process-handle race regression, migration UI |
| `configuration-ownership-final.xml` | 25 passed; configuration ownership boundaries |
| `configuration-tcp-final.xml` | 89 passed; scoped loopback listener replacement and explicit STOP superseding restart |
| `configuration-preflight-final.xml` | 71 passed; pure impact preflight and stable immutable device paths |
| `metadata-import-final.xml` | 5 passed; fresh-process camera migration and packaged icon checks |
| `runtime-owner-wait-final.xml` | 2079 passed, 2 skipped, 963.58 seconds; owner/wait baseline before the resource journal/upload increment |
| `resource-journal.xml` | 39 passed; preparation process death, known-file cleanup, clean restart, configuration regressions |
| `remote-resource-package-v2.xml` | 26 passed; bounded upload, real gRPC publication/recovery, UI confirmation, source package entry |
| `remote-upload-native.xml` | 5 passed; native Windows Qt at 150 percent scale, resource upload comparison screenshot inspected |
| `adapter-package-final.xml` | 10 passed; legacy IMV adapter and source package-entry probes |
| `remote-resource-lock-final-v2.xml` | 27 passed; final shared-owner locking, capacity reservation, upload/recovery, UI and source package-entry probes |
| `runtime-resource-final.xml` | 2093 passed, 2 skipped, 983.74 seconds; broad Runtime baseline plus separately verified final upload-lock correction |
| `designer-core-ui-owner-final.xml` | 2553 passed, 2 skipped, 3 warnings, 1080.24 seconds; Designer/Core/UI combined run, excluding the nine Git-ignored private tests listed below |

Broad, focused, and frozen evidence are kept separate. Native image
evidence is under `finish-ui-native-final/`; offscreen images were not substituted
for readable native text rendering. Source hashes are in
`dispatch-source-manifest-final.json` for the preceding baseline. The broad runs
started on `resource-final-source-manifest.json`. A late shared-owner upload lock
correction changes only `project_config/transfer.py` among the 572 source/resource/
protocol files; it is separately covered by `remote-resource-lock-final-v2.xml`.
The final frozen build uses `resource-lock-final-source-manifest.json`. Broad runs
are baseline evidence plus this focused final-source regression, not a claim
that every broad test ran exclusively against the final source tree.

The combined Designer/Core/UI exclusions were verified using `git check-ignore`:

```text
tests/designer/test_migration_flow_routing.py
tests/core/test_confirmed_station_recipes.py
tests/core/test_migration_calibration_import.py
tests/core/test_migration_d800_selection.py
tests/core/test_migration_managed_tcp.py
tests/core/test_migration_node132.py
tests/core/test_migration_plc_write.py
tests/core/test_station1_correction_project.py
tests/core/test_two_station_migration_project.py
```

The three warnings concern existing `record_property` use with JUnit xunit2;
they are not hidden failures. These scoped combined runs are not an unrestricted
full-repository pass. Final scoped Ruff, mypy (15 source files), Protobuf
generation check, and `git -c core.safecrlf=false diff --check` passed.

### Frozen Resource Build

- Built with PyInstaller 6.22.2 / Python 3.10.21 using the existing test spec;
  no packager checkout edits, installer, deployment, or Release.
- Executable: `frozen-resource-lock/dist/EmoMaster/EmoMaster.exe`.
  Distribution: 685 files, 327401629 bytes.
- SHA-256: `756dedd5f114d604e7116ddf4408f0b36e16b4e2f49aa2e79a28a07e29432669`.
- `frozen-resource-lock-selftest.json`: exit 0, status `ok`, errors empty.
  Ordinary Job PIDs 22280/47456; Dispatch PIDs 13420/62932/70436;
  injected no-SDK Host PID 67720. Exact executable-path process query afterward
  found no remaining process. This does not inspect unrelated application instances.
- The frozen probe verified resource upload readiness, separate commit R4,
  immutable execution copy, removal of the Designer source, and restart recovery.
  It also retained the existing ordinary Job, Dispatch/Join, FrameStore, Host
  failure retirement, icon/Qt, and ONNX probes. No physical hardware was touched.
- All 572 source manifest entries are checked against the final build input.
  Earlier `frozen-resource-final`, `frozen-owner-wait` and `frozen-dispatch`
  executables are superseded, not current
  verification artifacts. This is not complete frozen GUI or clean-machine acceptance.

## Retained Failures

- The first history screenshot test assumed dictionary order selected the root.
  The dialog now uses project workflow order and the declared entry workflow.
- Initial page publication compiled unresolved canonical resource parameters and
  failed with `imagePath must be non-empty string`. Execution validation now
  compiles the resolved candidate; read-only history uses formal port metadata.
- The first UI probe had blank offscreen font rendering. Native Windows images
  were captured and inspected. Missing synchronous wire routing and overly narrow
  resource-binding columns were repaired, not accepted as visual passes.
- `dispatch-transition-v2.xml`: 29 passed, 1 failed. The test observed that device
  workers were gone before the later group-retirement callback completed. It now
  waits for both actual worker and group retirement; it does not weaken frame or
  plan cleanup assertions.
- `runtime-dispatch-broad.xml`: 2047 passed, 2 skipped, 4 failed, 885.33 seconds.
  This run began before subsequent repairs. Two transition failures were repaired
  in the candidate validation path. A capacity check accidentally changed the
  legacy ordinary-Job rejection contract; group preflight is now group-only.
  The long-path retirement fixture now supplies the new device-manager dependency.
  The completed initial result is retained, not described as a green full run.
- The first normal-window undo test restored the session but did not render the
  canvas before synchronizing again. It now follows the real undo/render boundary.
  The new stale-modal-draft test then passed without overwriting concurrent edits.
- `designer-core-ui-dispatch.xml`: 2704 passed, 2 skipped, 143 failed. Of the
  failures, 141 are in eight Git-ignored private two-station migration test files
  that assert obsolete workflow/node/recipe structures. The remaining failures
  expected an original resource path rather than an immutable Runtime copy and
  two workspace entries rather than the new three. Those assertions were updated
  and the 47-test focused run passed. The original combined failure is retained.
- `configuration-owners.xml` initially had 13 failed, 32 passed because the new
  test helper selected parent paths instead of exact compared leaf changes. The
  follow-up fixture also needed an explicit loopback channel before enabling TCP.
  Corrected fixtures passed; neither validation rule was relaxed.
- One transition run exposed WinError 6 in a graceful-stop watcher racing actual
  process retirement. The watcher was fixed and covered, not suppressed as a warning.
- The first spawned unlimited-wait stop test incorrectly required ABORTED even
  when the IPC receipt was lost. The final test permits only documented cancellation
  outcomes, rejects force-stop timeout, and checks UNKNOWN diagnostics plus real
  Host/frame/cycle retirement. It does not assert physical non-execution.
- `designer-core-ui-repaired.xml`: 2547 passed, 2 skipped, 4 failed. The process
  imported metadata classes before the later cancellableWait field was added,
  then read the updated declaration JSON. Camera migration and icon checks
  reported the new field as forbidden. This mixed-source run is retained;
  fresh focused checks and the later combined run passed. The later upload-lock
  correction has its own focused evidence as described above.
- `remote-resource-transfer.xml`: 17 passed, 1 failed because the new test expected
  ValueError instead of the RuntimeClientError returned by the RPC client. The
  production missing-resource rejection was correct; the fixture was corrected.
- `remote-resource-package.xml`: 25 passed, 1 failed. The new self-test compared
  a normalized resource path to the Windows short-name temporary directory.
  Resolving the temporary root fixed this path assertion; no resource integrity
  check was weakened. The replacement run and frozen self-test passed.

## Remaining Acceptance Boundaries

This is a historical checkpoint. Page-only image/icon materialization, OS-held
consumer leases, bounded connection-only recovery, and explicit Runtime network
discovery/ForceIP were subsequently implemented. See
[current software validation](2026-10-18-device-platform-software-validation.md)
and [handoff](2026-10-18-device-platform-handoff.md) for the superseding evidence.
The list below records the boundary at this checkpoint, not the current backlog.

- No real SDK acquisition, device discovery/network reconfiguration, PLC write,
  hardware trigger, camera reconnect, or field timing acceptance was performed.
- The full driver recovery/retry matrix remains incomplete. Unlimited cancellable
  hardware wait and its explicit migration now have synthetic software evidence,
  but no target SDK timing or cancellation evidence.
- Remote upload/chunk transport and durable preparation ownership now have
  software evidence. Page-only image/icon materialization and complete
  cross-crash consumer lease/reclamation remain unfinished. Exposed resources
  from an unretired generation and unknown pre-existing files are retained.
  Completed uploads are bounded but not automatically evicted. Local-only
  resource inventories still need their original files on recovery; explicitly
  uploaded resources are independently verified and do not need Designer files.
- Migration remains explicit per-node confirmation within the project inventory;
  it is not an automatic hardware-identity merge or a silent bulk rewrite.
- Actual consumer-process death after a durable local counter side effect and
  a spawned journal-preparation process death are now tested. Killing the entire
  Runtime at every copy/commit/publication boundary remains separate. An external PLC side
  effect, complete frozen GUI interaction,
  and clean-machine installation remain separate acceptance scenarios.
- The private two-station migration tests retain their previously documented
  baseline mismatch; their project data was not rewritten to satisfy old tests.

Consequently the implementation is materially beyond the previous replacement
milestone, but the complete R2.1 hardware-handoff gate is not asserted here.
