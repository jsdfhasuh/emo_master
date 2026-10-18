# Device Configuration Replacement And Frozen Probe

## Boundary

This is incremental R2.1 software evidence, not the hardware handoff gate.
The source baseline remains `63070277133b89d61a5d27dde6d5f95494b4dce0` plus the
uncommitted implementation. No physical camera enumeration, PLC writes, release
publication, deployment, commit or push was performed. The private two-station
project and its unrelated historical-test discrepancies were not changed.

## Implemented

- Device additions, removals, identity/connection, policy and enabled-state changes
  now have a prepared adapter contract. Metadata, platform, driver contract and
  duplicate physical identity are validated before construction. The candidate
  adapter does not connect or acquire frames.
- Only changed devices are reserved. Their callers must first retire and their
  connections must be explicitly disconnected. Unrelated device operations and
  continuous Jobs retain their original adapters and continue running. FrameStore
  is not replaced, so downstream originals remain valid.
- Validation-only requests dispose their candidates. Cancellation, preparation
  failure and SQLite rollback preserve old authority and release prepared threads.
  Cleanup failure retains ownership for retry instead of forgetting live objects.
  A committed receipt remains committed even if subsequent cleanup fails; further
  writes and cycle admission then fail closed until recovery.
- Explicit maintenance pause survives adapter replacement. Publication performs no
  implicit reconnect: the device-page connect control remains an explicit action.
  This is not yet automatic scoped stop/drain/reconnect orchestration.
- Live Jobs tolerate unrelated device additions at cycle boundaries. Device-only
  replacement admission does not bypass undeclared default-parameter hot-update
  restrictions. Production IMV `nextCyclePaths` remains empty.
- Lifecycle controls carry a control-intent revision in addition to Runtime,
  project, definition and executor generation fences. A delayed command based on
  an older device-state read is rejected. A connection that returns after control
  cancellation is disconnected on the FIFO thread and does not unpause admission.
  This is not the R11/R12 deferred configuration-control queue required by D25.
- The comparison table distinguishes device defaults, connection/policy, node
  parameter sources, bindings, structure, layout, resources and presentation.
  Selecting a reference to a new device offers its exact missing definition for
  explicit confirmation. It does not copy unrelated existing-device edits or
  unfinished nodes. General workflow/resource dependency closure remains pending.

## Frozen Probe

The pinned PyInstaller 6.22.2 toolchain was installed only under
`build/device-management-imv/frozen-toolchain/`, without modifying the application
Conda environment or project dependency files. The external packager's existing
`configs/emo-master.json` and `build_pyinstaller_command` were used read-only,
with all outputs redirected beneath this project's evidence directory. Its config
and build scripts were not edited; no installer or public release was generated.

The application has an opt-in, hardware-free self-test:

```powershell
EmoMaster.exe --self-test --gui-icons --managed-runtime --result-json result.json
```

The managed test uses a temporary synthetic project and Runtime database. Two
independent spawned Jobs share one synthetic adapter; both original-frame leases
and workers retire. A disconnected adapter replacement then commits through the
same SQLite configuration coordinator. A separate spawned IMV Host uses an
injected API factory that raises before SDK loading, proving the expected error,
quarantine, process exit and staging cleanup. This negative Host probe does not
prove successful real-SDK capture, hardware triggers, or actual camera recovery.

The existing resource, Qt icon pixel and tiny CPU ONNX self-tests also run. This
is not complete frozen GUI/operator-view acceptance, an installer test, a clean
Windows-machine test, or field acceptance.

## Evidence

Files below are under `build/device-management-imv/`. Counts overlap and must not
be added together.

- `designer-lifecycle-loop-v2.xml`: 1680 passed, 1 skipped. Completed the preceding
  Designer-wide run, excluding only the known private migration-routing module.
- `admission-ui-final.xml`: 38 passed on the preceding lifecycle source.
- `replacement-initial.xml`: 39 passed for existing lifecycle, configuration and
  Loop regression after the first prepared-replacement integration.
- `replacement-focused.xml`: 10 passed for candidate replacement and rollback.
- `replacement-control-v2.xml`: 58 passed, including independent spawned continuous
  execution across unrelated additions/replacement and stale control rejection.
- `replacement-selftest.xml`: 32 passed, including source package subprocess,
  explicit Host failure probe, candidate cleanup ownership and durable receipts.
- `replacement-dependency-ui.xml`: 51 passed for configuration selection, explicit
  dependency confirmation, replacement, source package probe and RPC contracts.
- `replacement-final-scoped.xml`: 451 passed, 1 skipped on final source, covering
  core plugin/project/contracts, managed devices/configuration/Loop, selected UI
  and package-entry tests. This is not the full repository.
- `runtime-replacement-broad.xml`: 2041 passed, 2 skipped in 883.03 seconds. Scope
  is all `tests/runtime` plus the selected device/configuration/camera/PLC UI
  modules. This run began before the later comparison/self-test edits; the final
  scoped and production-outcome runs cover those later changes. It is not a
  final-source full-repository pass and does not erase earlier failed broad runs.
- `production-outcomes.xml`: 17 passed. The new spawned synthetic production test
  continues across at least three normal false/NG results, then terminates with
  the injected device error rather than NG, without replay and with frame cleanup.
  The run also includes odd-width, non-contiguous readonly grayscale/BGR original
  ownership tests and existing continuous-production lifecycle tests.
- `source-managed-selftest.json`: source-mode package/GUI-icon/synthetic Job probe.
- `frozen-build.log`, `frozen-build-v2.log`: successful local onedir builds.
- `frozen-managed-selftest.json`: first frozen probe passed; two independent EXE
  Job processes, synthetic acquisitions and configuration replacement.
- `frozen-managed-selftest-v2.json`: also passed the explicit no-SDK Host failure
  and retirement probe.
- `frozen-build-final.log` and `frozen-managed-selftest-final.json`: final-source
  rebuild and the same complete probe passed with exit 0. Job PIDs 41628/66932 and
  Host PID 65016 exited; no instance of this exact executable remained afterward.
- `frozen-source-manifest-final.json`: hashes for 557 source files, verified
  unchanged after building. `frozen-inventory-final.json`: 680 onedir files,
  327129271 bytes. EXE SHA-256:
  `cd34a3960d9a4622c55c66bb1329d599ef0611c1d1bca401ce0f8b989a55ad4c`.
- Protobuf generation check and scoped Ruff passed. Mypy with
  `--follow-imports=silent --check-untyped-defs` passed for the five selected
  replacement/configuration/self-test modules; dynamically typed integration
  still limits what static checking can prove.
- `toolbar-native-480-v3/`: native Windows Qt at 480 x 600, clean process exit and
  empty probe failure list; Run/Stop visibility was inspected. A narrow screenshot
  is not a claim that every main-window panel fits without scrolling.
- `replacement-native-960-150/`: inspected native comparison dialog at 150% scale,
  including the new category column; fixture-only evidence.

## Retained Failures

- The initial visual fallback created scene items absent from the graph model,
  producing `ValueError: unknown node: camera`. The script now populates both.
- The next probe rendered successfully but hung on its hidden unsaved-fixture
  prompt during cleanup. Exact Python PID 66032 was identified, verified to have
  no children, stopped and verified absent. The script now discards only its own
  in-memory fixture draft on exit; the v3 run completed with exit 0.
- Initial scoped Ruff found four F811 fixture-import shadowing errors. The fixture
  is now exposed through a module alias. Initial strict Mypy found two existing
  coordinator narrowing errors and new self-test annotation errors; those were
  repaired. A later missing dependency-result annotation was also repaired.
- Prior broad and private-recipe failures remain in the managed-IMV report; this
  report does not erase them or label the entire repository green.
- An additional environment-stripped frozen probe was rejected by the tool's
  command policy before execution. It was not rerouted. The ordinary frozen
  self-test passed separately, but clean-machine/no-development-PATH validation
  remains NOT_RUN. Build logs retain the missing optional `tzdata` warning.

## Remaining Gate

Resource preparation/replacement, automatic scoped stop/drain/reconnect, deferred
configuration-control supersession, historical/read-only online graphs,
persistent Dispatch/Join consumers, full migration/resource/site-binding UI,
page publication and remaining recovery/wait-policy combinations are not complete.
Only While v2 has an explicit nested iteration cycle boundary. Full frozen GUI,
real SDK and hardware evidence remain separate outstanding gates.

The 88-ID acceptance ledger now contains 67 PARTIAL and 21 NOT_RUN, with no item
promoted to complete full-plan acceptance. D11, F04 and F05 gained partial software
evidence in this continuation; all remaining requirements still need their own
implementation and evidence before declaring hardware-handoff readiness.
