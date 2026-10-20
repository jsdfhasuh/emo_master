# R2.1 Managed IMV and Cycle Parameters

Status: implementation and software verification in progress. This is not a
hardware-acceptance handoff and does not supersede the remaining R2.1 plan.

Base HEAD: `63070277133b89d61a5d27dde6d5f95494b4dce0`. Changes remain uncommitted.
No physical camera enumeration, PLC writes, deployment, or publication was run.

## Implemented

- `device.camera.huaray_imv` metadata and the existing camera operator ID now
  support managed schema-2.6 bindings. Older projects retain the legacy editor
  and runtime path. The binding-version condition crosses the RPC/catalog/UI
  boundary, rather than upgrading legacy nodes implicitly.
- A lazy reusable spawn Host executes all IMV SDK work on its fixed main thread.
  DeviceExecutor remains the only production queue. Cancellation uses a separate
  multiprocessing Event; a stuck Host is terminated and its exit checked before
  releasing its shared-memory mapping. Failed operations are never replayed.
- Software triggers become outcome-unknown when their frame/receipt is lost.
  The damaged stream is quarantined. Recovery uses a new adapter/generation and
  requires explicit external-trigger/synchronization confirmation at the RPC.
- Hardware-trigger requests preserve their continuous frame queue. Acquisition
  setting changes are rejected with `E_CAMERA_SYNC_REQUIRED`, without clearing
  that queue. Software/free-run changes stop, clear, apply changed fields, and
  restart with a new stream generation. This synchronization contract still needs
  verification on the target IMV SDK/camera.
- Frame sequence checks belong to the stream, not a workflow node. Metadata
  distinguishes request exposure, configuration readback, and unavailable
  per-frame exposure evidence. Native frame dimensions are checked against the
  reserved capacity before conversion, and SDK buffers are released on failure.
- The device page has confirmed single-frame diagnostics through the same
  Runtime owner, with bounded JPEG preview, per-session sequence deduplication,
  definition/generation fences, and explicit close/retirement. It does not expose
  arbitrary connection overrides or silently switch hardware trigger to free run.
- Legacy private camera preview/configuration and debug entry points are gated
  for managed projects. The legacy entry points remain usable for old projects.
- Camera migration preserves `keep` as an explicit source, warns about shared
  state, and refuses implicit conversion of unbounded hardware waiting. A value
  override requires its applicable manual/custom mode. Full-project migration
  is still outstanding.
- Six audited ordinary operators declare cycle-safe scalar parameters:
  threshold, blur, Canny, resize, number value, and number comparison. A worker
  installs only those fields at its next root cycle, retaining prepared paths
  and other operator state. Mixed display-name/layout changes do not unnecessarily
  require a stop. Model and stateful-operator replacement are not implemented here.
- Frozen dependency discovery has explicit import edges for built-in adapters.
  Package self-test now checks device declarations and pure adapter construction.
  This is not evidence that an actual frozen package has been built or run.

## Lifecycle And Explicit While Cycles

- Built-in simulated, SLMP and IMV metadata now offers `on_project_open`,
  `on_project_run`, `on_first_use`, and `manual` connection policies, plus
  project-close, unused, idle-timeout and manual release policies. Unknown values,
  unbounded idle budgets and immediate event-connect/unused-release combinations
  are rejected. This is not the complete recovery/retry policy matrix.
- Activation and run connection events are distinct from status reads. IMV has a
  connect-only Host request: it does not set work parameters, start acquisition,
  trigger, or allocate image staging memory. Idle release runs on the device's
  FIFO thread; static Job references and retained images are not keep-alive leases.
  Active operations, queued operations and explicitly retained hardware-trigger
  streams prevent idle release. Cleanup failures remain visible and quarantine
  the device rather than pretending that it disconnected.
- Confirmed, generation/definition/Runtime-fenced controls connect, disconnect,
  or enter maintenance through the normal device page and local/remote RPC.
  Disconnect pauses automatic admission until explicit reconnect. Maintenance
  cancels this device's pending/active operations, waits for actual native exit,
  and does not hold the project lock or stop an unrelated device. Already-cancelled
  acquisition is rejected before even opening a connection.
- Schema-2.6 While contract v2 supports explicit `cycleBoundary=iteration`.
  A child lease is acquired before the condition; condition/body share its token.
  False conditions and failures release that lease. Nested leases retain the
  outer plan, and code after the Loop returns to its outer compiled plan. Other
  Loop kinds inherit their caller; unsupported boundary declarations are rejected.
  The normal While editor exposes this setting for managed projects.
- Camera diagnostics now resolve defaults from their frozen cycle snapshot,
  not a later live DeviceManager definition. A fake SDK explicitly opted into
  cycle-safe exposure updates exercises the concurrent-write race. **Production
  IMV metadata still has no next-cycle default paths**: changes require stopped
  execution/retired diagnostic leases. This test does not establish F08 hardware
  trial restoration or authorize real-camera hot updates.
- The third workspace now uses compact labels below 600 px, keeping production
  Stop visible at 480 px. Icons use embedded Lucide 0.468.0 assets. A saved file is
  no longer reported failed merely because post-save path identity probing raises.
  Device-page asynchronous results also fence the current project identity.

## Evidence

All files below are under `build/device-management-imv/`. Test counts overlap and
must not be added together.

- `managed-final-v2.xml`: 91 passed on the then-current source, covering managed
  camera/PLC, executor, manager, Job IPC, configuration and selected contracts.
- `configuration-final.xml`: 137 passed, 1 skipped. The skipped check is not a pass.
- `contracts-final.xml`: 84 passed, covering device/resource/metadata contracts,
  source package self-test and managed camera UI.
- `editor-regression.xml`: 16 passed, including the real legacy camera editor
  button path, managed camera confirmation and migration, and managed PLC UI.
- `package-contract.xml`: 25 passed; source package self-test and fake-API spawn
  checks only, not a frozen executable.
- `native-1280/` and `native-960-150/`: Windows native Qt captures at 100% and 150%.
  The camera image is a synthetic UI fixture. Both diagnostic views were inspected
  for clipping/overlap. Evidence JSON includes source hashes and display scale.
- Scoped Ruff passed. Mypy passed for 29 source files with
  `--follow-imports=silent`; unannotated function bodies are not fully checked.

Additional runs (counts overlap with the preceding evidence):

- `save-as-regression.xml`: 33 passed.
- `core-broad.xml`: 281 passed, 1 skipped on the preceding source.
- `runtime-broad.xml`: 2657 passed, 3 skipped, 2 failed. This was an in-progress
  broad run, not a final-source full-suite pass. The registration count omitted
  the new simulated operator and was updated to explicitly require it. The empty
  infinite-Loop debug-pause test returned `E_DEBUG_STALE_SESSION`; the focused
  16-test `broad-failures-rerun.xml` passed, but that does not erase the first run.
- `lifecycle-focused.xml`: 34 passed (lifecycle plus managed IMV).
- `loop-initial.xml`: 54 passed (existing Loop/compiler/Designer/config contracts).
- `loop-cycle-focused-v2.xml`: 7 passed, including a real spawned single Job whose
  While body adopted a new scalar value, condition/body tokens agreed, and a later
  condition update ended the Loop and retired all leases.
- `lifecycle-loop-scoped-v2.xml`: 475 passed, 1 skipped. Scope is core plugin,
  project and contract directories plus listed runtime/camera/Loop tests, not all
  core tests and not the whole repository.
- `lifecycle-loop-scoped-final.xml`: 479 passed, 1 skipped on the later source,
  using that same scoped selection. The following narrow checks cover subsequent
  cancellation/driver-contract changes; these counts must not be added.
- `cancellation-final.xml`: 38 passed; cycle admission checks cancellation even
  when it acquires the project lock immediately.
- `driver-contract-final.xml`: 22 passed. Managed drivers without a connect-only
  contract are rejected instead of being marked connected without driver work.
- `managed-lifecycle-final.xml`: 63 passed after using the mandatory connect-only
  method directly and distinguishing maintenance-pending from already-paused state.
- `lifecycle-loop-boundaries-v2.xml`: 29 passed, including paused admission,
  old queued acquisition versus newly committed cycle parameters, outer-plan
  restoration on exception, and confirmed UI controls with no draft mutation.
- `camera-toolbar-regression.xml`: 51 passed, covering the diagnostic race,
  narrow-toolbar repair, PLC labels and workspace-switch regression.
- `lifecycle-native-1280/`, `lifecycle-native-960-150/` and
  `lifecycle-native-960-150-v2/`: native Windows source renders at 100%/150%; the
  device workspace and policy editor were visually inspected. The v2 probe also
  checks toolbar button rectangles do not intersect. All devices are fixtures.
- Scoped mypy passed for 19 source files with untyped-body limitations; the two
  new lifecycle/cycle-plan modules separately passed `--check-untyped-defs`.

## Failed And Interrupted Runs

- Initial camera tests rejected the operator because manifest and operator
  parameter metadata differed. After that fix, legacy connection validation was
  incorrectly applied to a managed camera node. A separate managed parameter
  validation entry fixed that compiler path.
- A subsequent test edit inserted a new test in the middle of a spawn test.
  `managed-final.xml` retains the resulting failure (70 passed, 1 failed). The
  test layout was corrected; `managed-final-v2.xml` supersedes that run.
- The initial full-repository run encountered failures in the private, ignored
  two-station project tests. A focused reproduction of the old revision-10 test
  against the present project returned `E_INPUT_MISSING` where it expected
  `E_MIGRATION_OUTPUT`. The private project was not modified to satisfy old tests.
- Both the first full-suite and first Designer-wide run then waited indefinitely
  in the old camera-editor test because the modal managed-node wizard was opened
  unconditionally. Their exact pytest processes (51816 and 44948) were identified,
  verified to have no children, stopped, and verified absent. Neither run has a
  completed full-suite result. This was a real UI compatibility regression, not
  a successful test or an accepted skip; the corrected path passed the focused
  16-test editor regression. Broader reruns are recorded separately.
- The next Designer run stopped at 1216 passed, 1 skipped, 3 failed. Two failures
  were the private two-station graph edge counts (33/34 versus 30). The third was
  the actual post-save `Path.resolve()` regression repaired above.
- `lifecycle-initial.xml`: 74 passed, 2 failed because tests counted Host transport
  messages as captures; connect-only now adds one message without a frame. The
  assertions were updated to count connect plus captures, with separate no-frame
  connection tests.
- `lifecycle-loop-regression.xml`: 500 passed, 1 skipped, 5 failed, stopping in the
  private confirmed-station recipe tests before the new runtime tests. Those
  failures retain `E_INPUT_MISSING` versus `E_MIGRATION_OUTPUT`. No private project
  was edited and no baseline private-project version is available in HEAD.
- `lifecycle-loop-scoped.xml` is a no-tests collection failure caused by naming a
  nonexistent `tests/core/workflow` directory. The v2 scoped run supersedes it.
- `loop-cycle-focused.xml` retained a fixture error: explicit Loop interface ports
  were omitted, so compilation rejected `E_LOOP_PORTS_STALE`. The v2 fixture has
  the correct declared number state ports; the compiler was not relaxed.
- `designer-lifecycle-loop.xml`: 1315 passed, 1 skipped, 5 failed before maxfail.
  Four label assertions expected old PLC captions, while the unchanged production
  presentation layer uses `storage area`/`numeric type`/`read count` captions in
  Chinese. Tests now derive expected captions from that existing presentation
  schema and retain all clipping/visibility assertions. The fifth exposed the
  real 480 px Stop overflow caused by the third workspace. `ui-broad-failures.xml`
  reproduces those failures including the running case.
- `lifecycle-loop-camera-final.xml`: 44 passed, 1 failed. Its initial camera-race
  test tried to write exposure with production IMV metadata that intentionally
  does not declare hot-update support. The fake-SDK test now explicitly opts into
  that capability; production metadata remains restrictive. The later 51-test
  camera/toolbar run includes the corrected race test.

## Remaining Before Handoff

The later [replacement/frozen report](2026-10-18-device-platform-replacement-freeze.md)
supersedes the device replacement and missing-local-packager limits below. It also
records the completed Designer-wide rerun: **1680 passed, 1 skipped**, and the
38-test admission/UI result. Earlier evidence and failures are retained.

Prepared resource replacement, scoped configuration stop/reconnect,
superseded connection-control fencing, online historical/read-only graphs,
persistent Dispatch/Join consumers, full migration/resource/site-binding UI,
page publication and the remaining recovery/wait-policy matrix remain incomplete.
Only While v2 exposes an explicit iteration boundary. Production IMV online default
updates still require a stop. Local PyInstaller and Nuitka were absent from the application
environment when checked; the external packager config was inspected read-only.
No dependencies were installed and no release workflow was dispatched.

Source/fake SDK tests, native Qt screenshots, source self-tests, frozen product
checks, and real-device acceptance remain separate evidence layers.
