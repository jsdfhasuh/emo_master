# Device catalog and managed driver contract

This guide describes the current project **2.7**, workflow package **1.4** and
DeviceType / driver API **1.1** implementation. It follows the TCP integration
[v1.5 plan](../plans/2026-10-20-tcp-device-integration-plan-v1.md) and the
[device usability follow-up](../plans/2026-10-21-device-usability-improvement-plan-v1.md).
Old-project compatibility is outside this delivery. Existing historical
validation reports describe their own fixed source snapshots.

## Project data and binding

New Designer projects and normal saves use project 2.7. Device definitions live
in `devices.definitions`. Each definition contains `name`, `deviceTypeId`,
`driverVersion`, `enabled`, `description`, `connection`, `defaults` and
`policies`. Names may change; device and selector IDs remain stable. Connections,
handles, threads and observed state never belong in the saved definition.

A node binds a declared slot to a device and optional static selectors:

```json
{
  "deviceBindings": {
    "tcp": {"deviceId": "server-01", "selectors": {"channelId": "station-a"}}
  },
  "deviceOverrides": {"tcp": {"timeoutMs": 1000}},
  "deviceValueBindings": {}
}
```

This is a structural example. Slot names and selector schemas come from the
operator manifest's `deviceRequirements`. Each requirement names
`deviceTypeId`, `requiredCapabilities` and a closed-object `selectorSchema`.
The device declaration maps selectors to stable item IDs with
`bindingSelectors`, for example `connection.channels`, `channelId`, `name`.

The compiler, Designer binding editor and package reuse call the common
`validateDeviceBinding` rule. A required capability must exist and a supplied selector
must name a declared item. Channel deletion/replacement checks existing node
references. Display-name changes preserve IDs and the live TCP connection.
A workflow package needs target registry metadata to offer existing-device reuse;
without it, an editable disabled copy can still be imported.

Designer may save a **pending binding** with a missing slot, or with an existing
deviceId and missing required selector, through “保存待绑定节点”. The editor never
chooses the first device/channel implicitly. A supplied missing ID, wrong type,
unknown selector or invalid parameter source remains an error. Explicitly unbinding
a slot removes that slot's overrides/sources in the same confirmed transaction.
Other missing slots do not prevent a valid device edit.
In pending drafts, a missing required selector stays missing even if its schema
has a default. Optional selector defaults still apply and must pass the normal
declared-item validation. Strict validation keeps its existing default rules.

Pending nodes keep node IDs, ports, edges, positions and supplied sources across
save/reopen and workflow-template import. They reopen in the current binding
editor. Templates remap only actual bindings. Compiler, initial/selected publication
candidates and full Runtime packages remain strict: every required slot/selector
must be valid before execution. An unrelated incomplete local node does not enter
an unselected publication candidate or invalidate the existing online version.

Unknown-plugin definitions retain their JSON for offline editing and round trips.
They are not silently made executable. Execution requires the matching registered
type, driver version, operator and supported platform.

## Parameters and configuration cycles

Connection and policy fields are project configuration. Node work parameters use
the existing inheritance, constant override and dynamic-source rules:

- No override inherits the device default.
- Explicit zero, false and null remain explicit values; the schema determines
  whether they are valid.
- A dotted leaf in `deviceValueBindings[slot]` selects a declared scalar variable
  or input. Constants and dynamic values cannot overlap.
- `overridePaths`, `dynamicPaths`, related parameter rules and
  `nextCyclePaths` continue to constrain editing and online changes.

Effective parameters are copied and validated before admission. Runtime validates
them and action arguments again before side effects. Static selectors never
become dynamic parameters.

ConfigurationCoordinator owns immutable cycle snapshots. Worker requests identify
workflow, node, slot, device and selectors; Runtime checks them against the frozen
cycle. Subflows and ordinary loops inherit the cycle. While-v2 explicit iteration
boundaries and Dispatch/Join retain their existing cycle and resource rules.
Only declared cycle-safe fields may change for a future cycle while work is active.

## DeviceType 1.1 registration

Put `device_types/*.json` beneath an explicitly configured plugin root.
Metadata scanning does not import drivers, create devices or perform I/O.
Conflicting declarations reject the affected type; unrelated types stay usable.

Device configuration forms may mark fields with `xAdvanced: true`. This controls
display only; collapsed controls retain values and participate in validation.
Errors expand the containing group/tab and focus the field. Unmarked third-party
fields remain visible. `xOptionLabels` changes labels while preserving enum data;
the same device declaration version/digest dependency rules still apply. Node
source/constant editors display values directly without an advanced-group wrapper.

A declaration includes closed connection/default/policy schemas and:

| Field | Meaning |
| --- | --- |
| schemaVersion / driverApiVersion | Both 1.1 |
| driverEntry | Importable module and class; construction is free of I/O |
| capabilities / actionSchemas | Business action names and their closed argument schemas |
| controlCapabilities | Supported connect, disconnect, maintenance, resume, recover, test_connection |
| operationScheduling | serial or managed_service |
| connectionLifetime | persistent or per_operation |
| bindingSelectors | Stable project-item selectors |
| managementActions | Optional subset of business actions offered on device pages |
| executionLocation | Existing io_thread or camera_host implementation |
| cancellableWait | Optional declared unlimited wait with required mode fields |

Each business capability requires an action schema. A per_operation type cannot
advertise persistent connect/disconnect controls. A managed_service has a
persistent lifetime. Metadata and driver imports remain separate.

The metadata-only example under `examples/device_plugin` is not a runnable camera.
The built-in `device.simulated.camera` provides synthetic capture. The runnable
[third-party ticker](../../examples/device_service/README.md) shows service
registration without a DeviceManager type branch.

## Driver API 1.1

Implement the public contract in
`emo_master.core.contracts.device_driver`; do not import a Runtime-private base.

- `physicalIdentity(definition)`: side-effect-free resource identity or None.
- `bindRuntime(DeviceRuntimeContext)`: retain the frozen Runtime/project/load
  generation/device/generation scope and event sink.
- `connect(params, cancelled, deadline)`: establish the session without business
  writes, capture or acquisition.
- `validateAction(action, arguments)`: validate action-specific arguments.
- `execute(action, params, cancelled, deadline, *, arguments, operationContext)`:
  perform one admitted operation. OperationContext contains owner, cycle, plan,
  slot, static selectors, source and request identity.
- `revokeOwner(ownerId)`: cancel or release only that owner's references.
- `requestStop(reason)`: request interruption without waiting under manager locks.
- `close(deadline)`: release the session, permitting later explicit reuse.
- `dispose(deadline)`: final release and event-hook removal.
- `snapshot()`: passive readiness and resource state.

The supplied DeviceDriver base supplies common hooks for current drivers.
It is not a compatibility adapter. Drivers must report cleanup failure rather
than pretending resources were released.

## Scheduling and lifecycle

Each serial device owns its FIFO. Camera/PLC operations stay serial per device;
workflow data dependencies establish cross-device business order. There is no
global camera/PLC lock or global trigger protocol.

A managed service retains its listener/background resources. Business calls run
on visiting IPC threads under its per-device admission gate, so one channel's
wait does not block another channel's send. Lifecycle/control operations still
use the device FIFO. Each Job has a 24 MiB TCP bridge for a normal 16 MiB message;
ordinary device/frame-control IPC keeps its 3 MiB bound.

Persistent connection policies are on_project_open, on_project_run, on_first_use
and manual, as permitted by the declaration. TCP server supports on_project_open,
on_project_run and manual. Per-operation TCP devices open and release their
socket per business call. Status reads and window opens never connect.

Disconnect pauses future admission. Maintenance cancels selected-device work,
waits for actual exit and disconnects. Resume restores admission under the
original policy; it does not execute business work. Recover uses the dedicated
reconstruction path and does not reconnect or replay. IMV recovery retains the
external-trigger synchronization confirmation.

Canceling queued work removes it. Active work retains native ownership until it
really exits. An unknown side effect is not replayed. A stopped Job does not
stop an unrelated persistent TCP service. Project/resource replacement waits for
affected users and respects existing configuration leases.

## State, diagnostics and device-page actions

State responses carry Runtime/project/load scope, device generation,
definition/control revision, lifecycle state, driverState, availableControls and
availableManagementActions. Transaction readiness differs from a connected socket.
A TCP server additionally exposes actual channel clients, address and generations.

DeviceRuntimeContext.events records background/lifecycle/action events.
`getDeviceLogger(context, slot)` projects node logs to the device while preserving
Job sequencing. Node failures are projected to their bound slot even if the
operator emitted no explicit failure log.

The bounded diagnostic cache keeps a separate cursor. It does not advance Job
event sequence. Records include frozen attribution, bounded UTF-8/HEX previews
and send receipts. Scope changes discard stale observations. Observers can close
and reopen without disconnecting the backend or installing duplicate hooks.

Declared managementActions use the same argument validation, device admission
and configuration cycle as business calls. They use a diagnostic owner and real
management source, without a fabricated Job/node identity. The owner/cycle retires
after actual operation exit. Both clients share the Qt-free RPC client and a
shared schema dialog. An undeclared action is never offered or admitted.

## Publication and package delivery

Designer retains the standalone device window and Runtime menu. Apply edits the
draft; Save writes the file; explicit Runtime publication writes online state.
The device-window shortcut “查看并写入 Runtime…” yields the observer modal and
enters that same publication dialog. It does not preselect same-project changes,
save the local file, or write without confirmation. Cancel/close returns to the
same device when project/Runtime scope is still current.
Validation is free of I/O. First publication installs the complete candidate and
activates only the selected connection policies, without creating a Job.

A COMMITTED configuration remains committed when later activation fails.
The original write receipt attaches actual PENDING/CONNECTED/LISTENING/FAILED
outcomes. The existing UI reads that original request, warns once for a late
failure and offers ordinary device control. It does not replay configuration.

Device packages, workflow packages 1.4 and full Runtime packages preserve IDs,
selectors, variables, shared devices and declared resources. Reuse packages create
new disabled device IDs; site fields are cleared and listed for the user.
New copies with colliding names receive `（导入副本）`, then `（导入副本 2）`,
reserving suffix space within 160 characters. Allocation considers the current
catalog and the whole import batch. Reused devices and full Runtime installation
keep their names. Each import remains one undoable transaction.
A full Runtime package preserves enabled/connection policies. Target dependency
validation and explicit installation precede execution. Remote empty Runtime
installation follows the execution-side import path; a Designer local path is
not an upload.

Source simulation, native Qt automation, frozen products, manual GUI review and
real hardware are separate evidence levels. The current delivery report records
the actual source SHA and executed checks.
