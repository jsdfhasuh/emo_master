# Device Platform UI, Runtime and Online Configuration Progress

Date: 2026-10-16. Base HEAD: `63070277133b89d61a5d27dde6d5f95494b4dce0`.
Checkout: `C:/Users/jsdfhasuh/my_scripts/emo_master` (the existing D: mirror may appear in test diagnostics).
All changes remain uncommitted. This is a source progress report, **not final human-acceptance readiness**.

## Implemented

- Native third device workspace, metadata-generated forms, references, create/delete/edit,
  atomic node/device wizard, and explicit inherit/constant/input/variable sources.
- Read-only device metadata/state RPCs, project 2.6 preservation, isolated local saves.
- Runtime-owned bounded device FIFO, separate wait/operation budgets, cancellation,
  shared-memory FrameStore with count/byte reservations and actual owner retirement.
- Simulated camera with real spawned Jobs using the same Runtime adapter and executor.
  Adapter constructor failure now preserves the existing device authority.
- Field-level online comparison keyed by stable node ID, selected-change candidates
  based on the online snapshot, and authoritative compilation of the candidate only.
- SQLite online payload, document/execution revisions and committed request receipt
  in one transaction. One writer, revision/instance fences, idempotent request lookup,
  pre-commit cancellation, and committed results surviving response loss/restart.
- Runtime restart reads committed SQLite content, not changes in the original project file.
- New snapshot/validate/write/status/cancel RPCs and matching embedded/remote client APIs.
  The old LoadProject and communication SYNC/UNLOAD paths cannot replace an activated
  2.6 online configuration behind the writer. TCP START/STOP retain their existing owner.
- Normal device-page comparison dialog: explicit selection, validation, confirmation,
  receipt query/cancel, and undoable readback into the draft without writing the local file.
  Normal Run no longer automatically saves/reloads an already activated 2.6 online project.
- Root cycle tokens and plan leases travel through existing Job IPC. Nested calls keep
  the same cycle identity. Simulated device fields declared in `nextCyclePaths` can change
  between continuous root calls while an older root retains its old plan. Worker compilation
  occurs on version change only, preserving accepted resource paths and operator instances.
  Device request admission validates the owner/cycle lease; frame/event metadata carries revision.
- Workflow package 1.2 carries the exact recursive device dependency closure and
  preserves unknown drivers, overrides and dynamic sources. Normal import requires
  explicit per-device reuse/new choices; new definitions are disabled with fresh IDs.
  Same-project workflow copy reuses device IDs. Preview fingerprints fence both the
  source file and target project; catalog/schema/variables/workflows commit as one
  undoable edit, outside the preview modal event loop.
- Typed device `connection/defaults` resource and site targets, installed-metadata
  purpose validation, protected path resolution, file override preservation and
  versioned-secret checks. Runtime packages support 2.6 input relocation, device
  declaration locks and existing-output protection. Initial activation and SQLite
  recovery validate resource integrity before creating adapters.

## Explicitly Incomplete

- M1/M2: trusted custom device UI resources, actual IMV camera Host, complete connection,
  release, idle timeout, pause/recovery policies, and unified preview/debug authority.
- M3: explicit Loop iteration boundaries; ordinary algorithm parameter hot classification;
  complete dependency selection; affected-owner preparation/reconnection and replacement;
  resource/global-variable/TCP/runtime/production/dependency replacement; online read-only
  graphs and historical result interpretation. These replacements reject explicitly today.
- Existing online projects cannot yet be replaced by a different project through the new UI.
  The current durable receipt budget is 4096; admission refuses rather than evicting/replaying.
  Complete session-sequence expiry/receipt-window management is still required.
- Live updates currently cover declared device defaults only. Other execution changes require
  stopped/retired execution. This restriction is an intermediate safety gate, not R2.1 completion.
- When the local and online execution definitions differ, normal Run is disabled in the comparison
  dialog until readback. A separate online read-only monitor is not yet implemented; do not project
  online results onto a different local graph.
- Initial legacy LoadProject device/TCP setup is not a prepared multi-owner atomic replacement.
  First-persistence failure blocks execution with an explicit recovery error. Full activation and
  replacement preparation remains necessary; do not claim hardware rollback.
- M4: managed real IMV/SLMP adapters, persistent SLMP sessions, late-frame fencing, true SDK tests.
- M5: persistent Dispatch/Join consumers and their group/slot/delivery/result ownership.
- M6: complete device migration preview, workflow-level cross-project resource/site
  transfer, dedicated visual binding editor, prepared live resource replacement and
  managed page-release snapshots. Full Runtime project resource packaging is implemented.
- M7: full functional matrix, complete native interaction evidence, frozen Windows artifacts,
  packaging, deployment and physical/manual acceptance.

## Verification

Python: `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe` (3.10.21).
Tests use `QT_QPA_PLATFORM=offscreen`, `HUARAY_CAMERA_SMOKE=0`; native rendering runs separately.
Suites overlap; **do not sum their counts**. XML files contain exact testcase inventories.

| Evidence | Result | Scope |
|---|---|---|
| `build/device-management-config/targeted-complete.xml` | 74 passed | Device metadata/models, selected candidates, Runtime store/cycles/manager/executor/spawn and loopback RPC |
| `build/device-management-config/config-rpc-final.xml` | 12 passed | Latest configuration tests including loopback gRPC and legacy mutation fences |
| `build/device-management-config/ui-final.xml` | 33 passed | Comparison/readback/undo, device wizard/workspace/catalog and workspace selector |
| `build/device-management-config/regression-final.xml` | 51 passed | gRPC, supervisor, Runtime resources, variables and Job lifecycle |
| `build/device-management-config/tcp-workflow-final.xml` | 75 passed | Subflows, operator lifecycle, TCP service/review/fault/external-client regressions |

Earlier increment evidence, not a claim of rerunning all of it against the newest changes:
`build/device-management-runtime/core-final.xml` (209 passed, 1 symlink skip),
`regression-verified.xml` (103 passed), `execution.xml` (102 passed),
`build/device-management-ui/final.xml` (48 passed), `regression-second.xml` (77 passed).

Native source rendering evidence is under `build/device-management-ui/native-config-final-1280/`
and `native-config-final-960-150/`: workspace, binding dialog and comparison dialog; Windows platform,
100%/150% scale, stub metadata, no hardware or Job start. Source hashes and base HEAD are recorded
in each `evidence.json`. These are not human interaction sign-off or frozen-build evidence.

Static verification: changed Python Ruff with generated code excluded; mypy
`--follow-imports=silent` over 52 source files (untyped function bodies not checked);
protobuf regeneration/check and `git diff --check`. Final command results are recorded separately
from pytest; none proves SDK or package acceptance.

## Failures Retained

- Earlier increment: qtbot/QTest fixture assumptions, metadata normalization causing a view-only
  edit, nested wizard transactions, three-button selector regression, flow guard monkeypatch
  compatibility, and simulated-frame payload/schema mismatch were repaired before prior reports.
- `ui-first.xml`: readback produced an extra undo checkpoint when the canvas normalized the loaded
  nodes. Rendering/capture now belongs to the same draft transaction. `ui-fixed.xml` then passed.
- Initial mypy: nullable PageCoordinator access in the new Run branch; an explicit guard fixed it.
- `cycles-expanded-first.xml` / `cycles-final.xml`: new test used incorrect event-store API/field
  names. It now reads the actual RuntimeEvent `payloadJson` via the service history path.
- The following continuous shutdown probe hung. Faulthandler identified project-state lock held
  during shutdown versus Job retirement acquiring that same lock. The isolated diagnostic used
  `-o faulthandler_timeout=20`; the hung test process was interrupted, not counted as passing.
- Fix: separate lease-retirement lock; finish configuration writer shutdown before entering the
  project shutdown lock; make root admission cancellable while waiting for the project lock.
  Dedicated lock-order and real continuous-spawn tests pass. No production process was stopped.
- Workflow package `state-first.xml`: one assertion compared the imported fixture
  against its pre-validation policy defaults (32 passed, 1 failed). The test now
  compares against the actual stored definition; production policy normalization
  was not changed. Initial Ruff fixture-name and mypy catalog typing errors were
  corrected without weakening checks.
- `state-verified.xml`: 55 passed, 1 failed. The new input-source assertion compared
  raw input JSON with normalized model output containing `variableId: null`. It now
  compares validated source models; the final 56-test group passes.

## Workflow Package Increment

New evidence is under `build/device-management-package/`. These groups overlap
earlier reports and must not be added to a cumulative pass count.

- `state-complete.xml`: 56 passed, including 23 device-package tests. Covers recursive
  closure, unknown data, explicit mapping, disabled creation, no endpoint auto-merge,
  preservation of sources, incompatible/stale choices, file/target fingerprint
  changes, late failure rollback, normal-window undo/redo and in-project copy.
- `ui-verified.xml`: 37 passed, existing normal import/export dialogs, tabs and
  Subflow/Loop/global-condition regressions, rerun after target fingerprint fencing.
- Ruff (seven changed source/test/script files), mypy (four source files with
  `--follow-imports=silent`, untyped bodies not checked), and `git diff --check` pass.
- Native rendering: `native-final-1280/` and `native-final-960-150/`, Windows Qt source at
  100%/150%; `workflow-device-mapping.png` was visually inspected at both scales.
  Evidence JSON records source hashes, including import controller and MainWindow.
  These are not frozen-build or human interaction acceptance.
- Reuse is intentionally conservative: type, driver version and defaults must
  match. Workflow-level resource/site transfer, legacy migration, Dispatch closure and
  final deployment acceptance remain incomplete; G02 stays PARTIAL rather than full-plan PASS.

## Device Resource Increment

Evidence is under `build/device-management-resources/`; suites overlap and must not
be added together or to earlier results.

| Evidence | Result | Scope |
|---|---|---|
| `core-final.xml` | 113 passed | Device resource/deployment tests plus existing directory, package, snapshot, device model and registry checks |
| `runtime-final.xml` | 44 passed | 29 device resource tests plus online configuration and device Runtime regressions |
| `designer-final.xml` | 69 passed | Workflow import/copy, device catalog, configuration UI and coordinate presentation |
| `delivery-regression.xml` | 46 passed | TCP delivery, page test packages, isolated debug resources and named-project Runtime |
| `production-final.xml` | 47 passed | Final 30 device-resource tests plus Runtime package and named-project regressions |

The source `.vxpkg` roundtrip was tested with temporary fixture files, not a frozen
application release or a production deployment. One test installs such a package,
loads it with an external trusted metadata fixture using the existing simulator,
and completes a real spawned Job. Tampering with the installed resource then blocks
Runtime recovery before adapter creation and prevents a new Job. No SDK or physical
device is involved. Packaging/installation also pass with the simulator constructor
replaced by a failure sentinel, proving those paths do not construct adapters.

Negative tests cover malformed/mixed targets, old schema and missing-device gates,
overlapping targets, wrong purpose/missing metadata, resource damage, missing files,
protected outputs, secret plaintext, unsupported file hot/dynamic sources, metadata
drift, installer-host path fallback, and output-preserving updates. Removing a device
with resource/site references is rejected. Workflow exports with those declarations
fail explicitly; in-project copies preserve them through shared device references.

Failures retained: initial mypy found five local-variable/schema typing issues,
then one remaining union-inference issue; fixed without disabling checks.
`designer-first.xml` reported 68 passed/1 failed because its staging test still
required blanket deployment rejection. The replacement test asserts missing trusted
metadata fails before resource preparation; page snapshot rejection remains tested.
Final Ruff checks cover 21 changed source/test files; mypy checks 19 source files
with `--follow-imports=silent` (untyped bodies are not checked).

The final production-controller check found a prospective ordering bug by source
inspection: an active managed project could replace directory files before its
legacy reload was rejected. The controller now refuses that update before file
work. Same-path reopen/restart uses committed online state, and different-project
rejection preserves the current directory lease and document. The new test also
changes source JSON between restarts and verifies the committed value wins.

G04 moves from NOT_RUN to PARTIAL. Dedicated binding UI, page release preparation,
live resource replacement, frozen Windows delivery and physical acceptance remain
unfinished. This increment is not final human-acceptance readiness.

## 2026-10-18 Managed SLMP Increment

The original PLC read/write operator IDs now use the project device authority in
2.6 projects. Runtime owns the persistent Binary/ASCII session; spawned Workers
and device-page diagnostics share its FIFO. Unknown write outcomes quarantine the
device and are never replayed. Explicit, generation-fenced recovery prepares a new
adapter without connecting or repeating operations. Legacy projects retain their
existing path, pending full migration. Device-bound general workflow/isolated debug
still rejects; dedicated managed PLC diagnostics is the implemented entry point.

See [the scoped report](2026-10-18-device-platform-managed-slmp.md) for commands,
failure history, native UI evidence and unfinished acceptance gates. The ledger is
now 54 PARTIAL / 34 NOT_RUN / 0 PASS, not physical-acceptance readiness.

## Reproduction

Run each Qt group in a separate process. Examples (PowerShell):

```powershell
$python = 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:HUARAY_CAMERA_SMOKE = '0'
& $python -m pytest tests/runtime/test_project_configuration.py -q -o faulthandler_timeout=30
& $python -m pytest tests/designer/test_project_configuration_ui.py -q
& $python scripts/gen_proto.py --check
```

For native rendering, unset `QT_QPA_PLATFORM`, set `QT_SCALE_FACTOR` to `1` or `1.5`, and run
`scripts/validate_device_workspace.py --output <new-evidence-directory> --width 1280 --height 800`.
The output directory must be new; existing evidence is not overwritten.

No commit/push, dependency changes, frozen application build/release, production
deployment, production-device connections, PLC writes or physical acceptance were
performed. Temporary source project packages were built and installed in tests.
