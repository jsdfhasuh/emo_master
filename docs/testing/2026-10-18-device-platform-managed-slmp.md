# Managed SLMP Increment, 2026-10-18

## Boundary

User requested implementation up to, but not including, physical acceptance.
This increment is source and loopback validation, not completion of that full
boundary. No physical PLC, camera SDK, production endpoint or production data was
accessed. PLC writes below target only the in-process loopback peer.

Workspace: `D:/jsdfhasuh/documents/my_project/emo_master`; HEAD remains
`63070277133b89d61a5d27dde6d5f95494b4dce0`. R2.1 work is uncommitted.
The earlier C: path and D: path were checked with identical file IDs for the
device declaration module and identical SHA-256 for the new SLMP adapter.

## Implemented

- `device.plc.mitsubishi_slmp` metadata is available without importing drivers,
  opening a socket, or creating an engineering device automatically.
- Existing `communication.plc.slmp_read` / `slmp_write` IDs and typed output ports
  are retained. `deviceBindingSince=2.6` requires explicit device bindings in
  managed projects. Older schemas retain their legacy behavior, not an implicit
  hardware migration. Bound nodes reject connection/retry fields and bindings.
- Connection fields reside in the device definition. Node forms expose action
  fields only. Existing-node conversion requires a connection comparison and
  explicit confirmation that automatic retries are removed; cancellation and
  one-step undo are tested. Referenced legacy connection resources/variables
  require explicit migration, not silent deletion. Other legacy PLC nodes in a
  newly upgraded project must also be bound before execution.
- Runtime validates action schemas and address spans before queue admission.
  Constructors are I/O-free. One FIFO owns one lazy `Slmp3ESession`; binary and
  ASCII use existing codecs, including M word semantics and native diagnostic bits.
  Two real spawned Jobs reuse the Runtime socket, not worker-private clients.
- IP literals are mandatory for managed identity; DNS aliases are not guessed.
  IPv4-mapped IPv6 addresses normalize to the same identity. Different route or
  frame-format settings cannot create another owner of the same endpoint.
- Cancellation uses a bounded per-operation closer, joined before the next
  operation. Exchange errors retire the socket and quarantine the device.
  `NOT_EXECUTED`, `REJECTED`, `UNKNOWN`, and acknowledged `COMPLETED` are distinct.
  A local result-formatting failure cannot turn acknowledged writing into
  `NOT_EXECUTED`. No managed operation uses the legacy retry loop.
- Device-page PLC diagnostics reuse the existing controls, write lock generation,
  bounded request history and readback receipts. Each manual write needs explicit
  confirmation. Diagnostic calls share the same queue/socket with production Jobs.
  Closing the diagnostic lease does not close a healthy Runtime-owned connection.
- Managed diagnostic open requests contain a stable device ID and definition
  revision, not endpoint overrides. Legacy private PLC debug RPCs are blocked for
  managed projects, including an old-schema draft attempting to bypass that gate.
- Explicit `RecoverDevice` RPC checks Runtime instance, project, definition
  revision and executor generation. Related Jobs/diagnostic leases must retire.
  Recovery creates a new executor generation without connecting or replaying
  operations. Lost responses are reconciled by reading state, not retrying with a
  new token. Device state distinguishes disconnected, connected and faulted.

## Evidence

Evidence lives under `build/device-management-slmp/`. Suites overlap: do not sum
these counts or add them to earlier reports.

| Evidence | Result | Scope |
|---|---|---|
| `slmp-final-current.xml` | 26 passed | Managed driver, read/write IPC/spawn, native bits, diagnostic sharing/write protection, recovery over gRPC and negative gates |
| `managed-final-current.xml` | 92 passed | Then-24 managed tests, device executor/manager/Runtime, online configuration, resources and registry |
| `legacy-final-current.xml` | 575 passed | Existing PLC debug, isolated debug, workflow integration, dual-PLC fixture, Binary/ASCII codecs and communication operators |
| `designer-final.xml` | 51 passed | Device UI, managed diagnostics, migration confirmation, undo, project open, online UI and workflow packages |
| `legacy-ui-final.xml` | 182 passed | Existing PLC editor, presentation, wheel safety and dual-PLC Designer project |

Native Qt source captures: `native-final-1280/` and `native-final-960-150/`, recorded platform
`windows`, scale factors 1 and 1.5. Both managed PLC node and diagnostic views were
visually inspected; no observed text overlap. The script opens windows and captures
them without connecting or reading/writing a device. This is not human interaction
acceptance, a frozen executable or physical evidence. `evidence.json` records source
hashes and the base HEAD. Earlier `native-1280/` and `native-960-150/` captures are
retained, including the initial visual inspection.

Failure history is retained in the earlier XML files. The first new test exposed
schema numeric normalization turning integer PLC values into floats; fixed by
validating without losing the original integer codec input. Test fixture setup
then needed the existing registry API and required workflow boundary nodes.
The legacy run first exposed a no-argument cleanup stub; legacy StartJob retains
that call shape. The dual-PLC reproducibility fixture intentionally remains 2.4,
so its generator excludes the new managed-only schema hint; checked-in field and
simulation project files were not regenerated or changed.

Static validation: scoped Ruff passes; mypy `--follow-imports=silent` passes on
22 source files (untyped function bodies are not checked). Protobuf regeneration
and `scripts/gen_proto.py --check` pass. `git diff --check` passes with existing
LF-to-CRLF warnings only. Initial mypy annotation issues and one Ruff E731 finding
were corrected without disabling checks.

## Reproduction

Run Qt groups in separate processes. No dependency changes are required.

```powershell
$python = 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:HUARAY_CAMERA_SMOKE = '0'
& $python -m pytest tests/runtime/test_managed_slmp.py -q
& $python -m pytest tests/runtime/test_plc_debug.py tests/runtime/test_plc_operator_debug.py tests/runtime/test_builtin_communication_operator_workflows.py tests/runtime/test_dual_plc_field_project.py tests/plugins/test_slmp_debug_transport.py tests/plugins/test_slmp_ascii.py tests/plugins/test_communication_operators.py -q
& $python -m pytest tests/designer/test_managed_plc_ui.py tests/designer/test_device_workspace.py tests/designer/test_device_catalog.py tests/designer/test_device_project_open.py tests/designer/test_project_configuration_ui.py tests/designer/test_workflow_package_devices.py -q
& $python -m pytest tests/designer/test_plc_editor.py tests/designer/test_plc_presentation.py tests/designer/test_plc_wheel_safety.py tests/designer/test_dual_plc_project_open.py -q
& $python scripts/gen_proto.py --check
```

## Remaining Gates

The full 88-item ledger is 54 PARTIAL / 34 NOT_RUN / 0 PASS. C07, F01 and F02 gained
partial evidence, not blanket acceptance. Remaining implementation includes IMV
Host and late-frame fencing, complete lifecycle policies, full prepared online
replacement and Loop boundaries, historical online graph, Dispatch/Join, full
legacy migration, dedicated resource/site UI, managed page release and frozen
Windows delivery. Legacy pre-2.6 private paths are compatibility staging, not the
final R2.1 migration boundary. General managed workflow/single-node debugging is
still gated; the dedicated PLC diagnostic path is not a substitute for it.

No commit, push, dependency installation, frozen build, deployment or physical
acceptance was performed. Do not start field acceptance based on this report.
