# Device Catalog Contract: Offline Editing And Managed Runtime

Status: the normal third workspace, visual node binding/source editor, managed
simulated/SLMP/IMV backends, SQLite online configuration authority, lifecycle
controls, scoped disconnected-adapter replacement, root cycles and explicit
While-v2 iteration cycles are implemented.
Prepared local resource replacement, persistent Dispatch/Join, online historical
graphs, explicit migration and page publication are now implemented. Current
software evidence is recorded in
`docs/testing/2026-10-18-device-platform-software-validation.md`; the Dispatch,
replacement and managed-IMV reports are earlier milestones. Remote resource upload,
OS-held cross-restart resource leases, static page assets and explicit network UI
are implemented. Full manual/frozen GUI and real hardware validation remain
separate acceptance gates.

## Project Format

`migrateProjectPayload(payload, enableDevices=True)` explicitly upgrades to project
2.6. It preserves the previous communication, global-variable, production,
presentation and resource configuration. `devices.bindings` remains unchanged.
Opening an old project never enables device definitions or writes a migration.
The normal save path retains its existing `.bak` behavior.

```json
{
  "devices": {
    "bindings": {},
    "schemaVersion": "1.0",
    "definitions": {
      "camera-01": {
        "name": "Inspection camera",
        "deviceTypeId": "device.example.simulated_camera",
        "driverVersion": "1.0.0",
        "enabled": true,
        "description": "",
        "connection": {"source": "offline"},
        "defaults": {"exposureMode": "manual", "exposureUs": 8000, "gain": 0, "enabled": true},
        "policies": {}
      }
    }
  },
  "nodeExample": {
    "deviceBindings": {"camera": {"deviceId": "camera-01"}},
    "deviceOverrides": {"camera": {"exposureUs": 12000}},
    "deviceValueBindings": {"camera": {"gain": {"source": "input", "inputName": "gain"}}}
  }
}
```

This is a structural excerpt, not an executable project. Slot names come from
operator `deviceRequirements`, e.g. `{"camera":{"deviceTypeId":"..."}}`.
The existing Huaray and SLMP operator IDs declare `deviceBindingSince="2.6"`.
Managed projects use bound devices; older projects retain their legacy editors
and execution behavior rather than being silently upgraded.

Device definitions retain unknown-plugin connection/default/policy JSON for
offline round trips. Editing a definition requires an available matching type
and exact declaration version. Device and slot IDs are nonempty, whitespace-free
strings, independent of names and connection addresses. Runtime state and handles
do not belong in these records.

Trusted connection schemas can mark scalar identity fields with
`xDeviceIdentity=true`. Designer uses the complete declared key for an offline
reuse prompt; IP literals normalize mapped IPv4/IPv6 forms without DNS. Missing or
unmarked keys do not establish an identity. Defaults/policies do not distinguish
physical devices. Reuse requires confirmation and never copies the proposed
defaults over the existing definition. Runtime driver identity checks remain
authoritative and cannot be bypassed by changing these UI hints.

## Parameter Sources

- No node entry means inheritance from the device definition.
- An explicit nested value in `deviceOverrides[slot]` is a constant, even when it
  equals the device default. Zero, false and null are not missing values; the
  device's parameter schema still determines whether each value is allowed.
- `deviceValueBindings[slot]` maps a dotted leaf path to either
  `{"source":"variable","variableId":"id"}` or
  `{"source":"input","inputName":"name"}`. Both sources cannot coexist.
- Empty path segments, overlapping parent/child paths, simultaneous constant and
  dynamic values, and parameter sources without a bound device slot are rejected.
- Sources refer to declared default/work parameters, never connection or policy
  fields. Dynamic parameters currently use the existing scalar variable types.
- `resolveParameters` copies and resolves dynamic values once, validates schema
  limits and related-field rules, and returns request-ready values. It does not
  perform hardware I/O. The operator executor calls this before device enqueueing;
  the Runtime authority validates the effective parameter schema again.
- Related-field checks involving dynamic paths are deferred until resolution;
  constant ranges are still checked while editing. Old `keep` is not reinterpreted
  as a deterministic default and is not automatically filled with an exposure.

## Metadata Discovery

Add `device_types/*.json` under an existing plugin root. Use
`PluginRegistry.scanDeviceTypes(roots)` for metadata-only discovery without
importing operators or SDKs. Normal `scanRoots` also returns `deviceTypes` and
`rejectedDeviceTypes` alongside its existing operator results.

Each declaration specifies an ID/version/core compatibility range, display name,
category/icon key/platforms, connection/default/policy object schemas, allowed
constant/dynamic paths, related parameter rules and declared capabilities. Schema
value validation reuses the existing core parameter validator. Policy enum/default
fields provide allowed/fixed/default values. Runtime validates policy combinations
before constructing adapters; the editor never infers physical availability.

Identical declarations deduplicate; conflicting or incompatible declarations reject
that type rather than selecting the last one. Malformed declarations do not block
unrelated types. Files are bounded to 256 KiB and must resolve inside their plugin
root. Metadata scanning neither creates project devices nor imports a driver.

See `examples/device_plugin/device_types/simulated_camera.json`. It is a metadata
fixture with **no driver or advertised executable capabilities**, not a functioning
simulated capture backend. It is not loaded into normal builtins or user projects.
The separate built-in `device.simulated.camera` type and
`vision.io.simulated_camera` operator provide an executable synthetic backend.
Metadata declares `driverEntry` and `executionLocation`; scanning does not import
that driver. Runtime owns the `io_thread` FIFO; the IMV adapter dispatches only the
admitted operation to its reusable `camera_host` process. Unsupported platforms
and undeclared schema values fail explicitly, never degrade to private connections.
Driver construction and `physicalIdentity(definition)` must be side-effect-free.
Managed drivers must provide `connect(params, cancelled, deadline)` without
capture, acquisition-start, feature writes or PLC writes; `execute(...)` performs
the admitted action, and `close()` reports cleanup failure. Optional
`holdsConnection()` prevents idle release of a retained trigger stream.

Connection policies are `on_project_open`, `on_project_run`, `on_first_use` and
`manual`. Release policies are `on_project_close`, `when_unused`, `idle_timeout`
and `manual`. `idle_timeout` requires `idleTimeoutMs` in 1..86400000.
`when_unused` requires `on_first_use`, avoiding a meaningless connection-event/
immediate-release combination. Queue and operation budgets remain separate.
Static Job references are not keep-alive leases. Manual disconnect pauses auto
admission until an explicit connect; maintenance cancels only the selected
device's requests and waits for real native exit. Status reads do not connect.
Recovery/retry policies, SDK capability discovery and successful frozen SDK execution still need
the remaining implementation/verification work.

An optional `cancellableWait` declaration identifies `action`, `timeoutPath` and
required work parameters. The timeout must be an integer field with minimum zero;
zero requests unlimited waiting only for the declared action and required mode.
The IMV contract requires hardware trigger. Queue and connection budgets are still
finite, cancellation remains mandatory, and no action is replayed. The Host wire
uses null for the unlimited relative budget, not a cross-process timestamp.

## Cycle Boundaries

Managed root calls acquire an immutable Runtime cycle token. While contract v2
can explicitly select `cycleBoundary="iteration"` in schema 2.6; the default is
`inherit`. Each enabled iteration acquires a child token before its condition,
and its body inherits that same token. False conditions and failed bodies release
the child lease; code after the Loop resumes the outer compiled plan. Other Loop
kinds do not advertise explicit cycle switching.

Only declared `nextCycleParameterPaths` and device `nextCyclePaths` can update
while execution remains active. Materialized model/resource paths and stateful
operator instances are retained. Production IMV currently declares no hot-update
default paths; software/free-run adapter parameter tests do not grant that
capability to real hardware. Camera diagnostics use the frozen cycle's defaults,
not whatever definition became current after admission.

## Shared Editing Boundary

`DeviceCatalogEditor` uses `ProjectEditSession.transaction()` for add/update/delete
and `commitNode`. The latter commits an optional new device and a node together;
validation failure rolls back the format upgrade, definition and node. Construct
wizard drafts separately and call it only on Finish. Cancel requires no command.

References are derived from actual node bindings. Renaming preserves IDs; deletion
of a referenced device is rejected with reference locations. Changing defaults
checks existing bound overrides. Variable deletion also accounts for device dynamic
bindings. These are offline operations, not checks of live Runtime occupancy.

The existing WorkflowStore, FlowGraphModel, copy operation, project history and
normal Designer load/save path preserve the new fields. The device workspace uses
the same schema forms and project history. A multi-slot wizard commits only its
referenced new devices and the node on Finish, with one history entry. Async
metadata responses and Finish are fenced against project/Runtime changes.
Page/flow/device switching does not change device ownership or execute hardware.
Opening or saving-as a 2.6 draft no longer releases the online TCP owner.

## Managed Simulation

`DeviceManager` owns one `DeviceExecutor` thread per physical identity. It rejects
duplicate identities instead of silently merging different defaults or policies.
Jobs have separate bounded IPC channels and are authorized only for devices in
their reachable workflow/subflow/loop graph. No socket, driver instance or Qt
object is passed to a spawned Job.

Each operation copies its effective parameters before enqueueing. Queue and
operation timeouts are separate. Cancelling a queued operation removes it before
execution. Abandoning an executing request returns UNKNOWN while the same IO
thread continues to own the device until actual exit. There is no automatic replay.

FrameStore reserves bounded frame count and bytes before capture, copies the
original to Runtime-owned shared memory before releasing the device executor, and
tracks explicit leases. Jobs copy their own image and release their original lease;
abnormal cleanup waits for actual Job and native-operation retirement. This is not
the M5 Dispatch/Join implementation. Managed 2.6 Jobs acquire a root cycle token
from Runtime; subflows and ordinary inner iterations inherit cycleId/planRevision.
An explicitly enabled While-v2 iteration takes a child token as described above.
Dispatch/Join inheritance remains pending.

## Online Configuration Increment

The five snapshot/validate/write/status/cancel RPCs share a single Runtime writer.
The selected candidate is assembled from the online baseline, not the whole local
draft. Complete candidate compilation precedes a SQLite transaction that persists
configuration, documentRevision, executionRevision and the committed request receipt.
Clients query the same request ID after uncertainty; there is no automatic resubmit.
Local save and online readback remain separate from explicit configuration writes.

Device declarations can specify `nextCyclePaths`, a subset of overridable work
parameters. The simulated adapter declares its per-operation fields; a continuous
root call can acquire the newly committed revision without recreating its driver.
Old roots retain their old plan. Fields without a declared handoff are not silently
hot-swapped; full update classification and prepared resource-owner replacement remain pending.

After a 2.6 online project is activated, old LoadProject and TCP SYNC/UNLOAD mutation
paths reject replacement. Runtime restart reconstructs software state from SQLite,
not the original Designer file. Device identity/policy/add/remove changes prepare
disconnected adapters and reserve only changed devices. Their callers must retire
and their connection must be explicitly disconnected before preparation. Unrelated
Jobs continue; publication preserves manual pause and never implicitly reconnects.
Validation/cancellation/rollback dispose candidates; cleanup failure retains the
owner and fences further writes. Runtime, global-variable, resource, dependency
and production replacement remain rejected.
It also lacks different-project replacement and online read-only historical graphs.
The comparison dialog therefore cannot start a differing online execution graph
against the local canvas; readback is required. These are staging limits, not a
claim that the complete online-management contract has been delivered.

`ListDeviceTypes` and `GetDeviceStates` are read-only through both embedded and
gRPC clients. State responses are project-scoped, and the page labels a local
definition mismatch rather than presenting draft properties as applied settings.

## Workflow Package 1.2

Workflow exports from a device-enabled project use package schema `1.2`. The
`devices` map contains exactly the definitions referenced by the packaged root,
subflows and active Loop dependencies. Unknown device types are preserved without
loading their drivers. Node overrides and input/variable parameter sources remain
unchanged; referenced global-variable definitions travel with the package.

The normal import preview requires a choice for every device dependency. Reuse
currently requires the same type ID, driver version and default parameters; no
hardware discovery or IP-based automatic merge occurs. Existing target connection,
policy and enabled state are never overwritten. New devices receive fresh IDs and
are disabled until their site configuration is reviewed. A new disabled definition
is not proof that Runtime can activate it: missing drivers and duplicate physical
identities remain subject to Runtime admission checks.

The source package and target project are fingerprinted at preview and checked
again before import. Import uses a private candidate and publishes device catalog,
schema upgrade, variables and workflows together as one undoable edit. Cancel or
validation failure does not change the project. In-project workflow copy explicitly
reuses device IDs. Old `1.0`/`1.1` package serialization remains supported.

Workflow packages with referenced device resource/site declarations currently fail
explicitly and recommend a full Runtime project package; they never drop those
declarations. In-project copies can reuse the same devices and their existing
resource declarations. Cross-project workflow resource transfer, legacy-device
migration, Dispatch closure and frozen application validation remain pending.

## Device Resource Targets

Project 2.6 resource and site bindings accept a device target alongside the existing
node target. Its shape is explicit and cannot combine device and node fields:

```json
{"deviceId": "camera-A", "section": "connection", "parameterPath": ["calibration"]}
```

Only `connection` and `defaults` are targetable, not identity, enabled state or
policies. Parent/child targets on the same device and section cannot overlap.
The referenced device must exist. Older project versions reject this target.

An installed device declaration authorizes each bound field with `xResourcePurpose`
on its leaf schema. Input resources require a matching purpose and `xWidget=file`,
`xFileMode=open`; output sites require `xFileMode=save`. Project JSON cannot supply
its own trusted schema. Site values are checked as nonempty strings, and
`secret_ref` values must be versioned `secret://...@...` references, never plaintext.
The same secret restriction applies to unbound declared fields and node overrides.

Runtime directory preparation resolves device input/output paths against the
project directory, verifies declared resource sizes/hashes, and protects project
inputs and Runtime data against overlapping outputs. Static node file overrides
are resolved without materializing inherited fields; dynamic and next-cycle file
parameters are rejected. Preparing or packaging does not instantiate a driver.

Runtime `.vxpkg` build/install now supports 2.6. Device input files, including
defaults and static overrides, are collected as package-relative resources. Output,
connection and policy values retain their declared meaning. The manifest locks
each required device type by version and declaration SHA-256. Installation requires
matching locally installed metadata; it never loads code from the archive. Device
inputs cannot escape the package root or use input-directory fallback. Existing
device output paths are protected during offline updates.

An offline file update is not an online configuration commit. The production
controller refuses package updates before touching files when a managed online
configuration is already active. Reopening the same project after restart uses
the recovered committed version, not newly edited source JSON; switching to a
different project remains gated on actual retirement of existing owners.

Initial 2.6 activation and persisted online recovery both validate resources before
creating device adapters. Live declared device files are prepared as immutable
private copies before adapter installation. The resource/site binding editor and
online page publication use the same authoritative comparison/commit path. Remote
resource upload uses the explicit chunked transfer protocol before a separate
configuration commit. Managed projects use full Runtime packages and online page
publication, not the legacy page/test-package format.

## Managed SLMP And Diagnostics

`device.plc.mitsubishi_slmp` owns one lazy Runtime `Slmp3ESession`. Its connection
schema holds literal IP/port, binary or ASCII framing, routing and transport
timeouts. `xAddressLiteral` validates IP syntax without DNS. Mapped IPv4 and IPv4
share an identity; framing/routing differences cannot bypass that endpoint lock.
Connection/release choices are restricted by the installed declaration. Finite
connection recovery has separate retry-count, recovery-deadline and backoff fields.

Device metadata may declare closed `actionSchemas` for its capabilities. Runtime
validates action arguments and cross-field ranges before queue admission; actions
are not device defaults, node overrides or endpoint overrides. Managed PLC exposes
connect/read/write, using words for the existing workflow contracts and native M
bits for diagnostics. Driver construction and explicit recovery do not connect.

Existing PLC operator IDs declare a `plc` device slot and `deviceBindingSince=2.6`.
Old projects remain loadable without an implicit conversion. Managed node schemas
mark action-only fields with `xDeviceActionFields`; the compiler rejects legacy
connection/retry parameters and variable targets on bound nodes. Conversion is an
explicit, undoable node transaction with connection comparison. The project-wide
migration inventory, camera keep/zero-timeout preservation, supported dynamic
sources, and device resource/site targets are covered by the current report.

The device-page diagnostic entry uses the existing PLC diagnostic protocol, with
`params_json` containing only `deviceId` and `definitionRevision` at open. Runtime
checks the committed definition and creates a maintenance lease on DeviceManager.
Read/write operations acquire root cycle leases and enter the same FIFO as Jobs.
Write permission generations, request deduplication, and per-execution `confirmed`
are mandatory. Closing diagnostics retires only its lease, not the shared socket.

Business operations never retry automatically. Only declared connect-only errors
with `NOT_EXECUTED` can retry, with a bounded count and one non-refreshing recovery
deadline. Failed exchanges close the socket
and quarantine the device; an unknown write acknowledgement remains `UNKNOWN`.
`RecoverDevice` requires the current Runtime instance, project, definition revision
and executor generation, and refuses while related Jobs/maintenance or operations
remain. It prepares a new disconnected generation without replay. Reading device
states distinguishes queue idle from physical connection state. SDK recovery is
not implied by this PLC implementation.

## Managed Entry Points

The supported managed routes intentionally retain these ownership fences:

- The compiler requires matching managed type declarations and valid slot/source
  mappings. A caller without this managed metadata still rejects bound nodes with
  `E_DEVICE_RUNTIME_UNAVAILABLE`. Runtime and its device-enabled Jobs pass the
  scanned metadata; they do not fall back to node-private connections.
- Isolated operator debug rejects device-bound drafts before worker admission.
  Managed PLC diagnostics use the dedicated device entry; general workflow and
  single-node managed debugging remain gated. Legacy PLC debug requests cannot
  open private sockets while the Runtime has an active 2.6 project.
- Device page-release snapshots remain explicitly rejected. Full Runtime project
  packages support managed-device resource mapping; the legacy page/test package
  builder retains its modern-project publication guard for 2.6.

Use device-page diagnostics for managed hardware, full Runtime project packages
for deployment, and online configuration for page publication. Private debug
sockets and legacy page-only packages are not alternative device owners.

## Static Page Resources And Consumer Leases

`Props.imageResourceId` is allowed only on an image component without dynamic
bindings. `Props.iconResourceId` is allowed only on a navigation button. Referenced
resources must have `page_image` or `icon` purpose respectively. Runtime materializes
private, hash-verified copies; private execution paths are never serialized into
the canonical document. PNG/JPEG/BMP decoding is size/dimension/budget checked.

Display-only publication does not change the execution digest. Referenced data
sources and result scopes remain execution dependencies. A page-only asset also
bound to an execution input remains part of the execution digest.

Prepared resource generations have an OS-held sentinel lease. Every consuming
worker acquires it before entering its target. Restart recovery obtains exclusive
retirement ownership before deleting exposed files; parent-process death alone
does not prove safety. Late consumers are fenced after retirement. Legacy exposed
generations without sentinels remain conservatively retained.

## Explicit IMV Network Operations

`DeviceNetworkCommand` separates `discover`, `force_ip`, and durable `status`.
Discovery and ForceIP run in a spawned Host; no scan occurs on metadata load or UI
open. The typed device-info ABI is gated to the reviewed IMV 2.4.2 series. A 30-second
discovery identity, current definition/generation, explicit confirmation and paused
device maintenance ownership are mandatory for ForceIP. Native readback must match
cameraKey, MAC and requested network fields. The SDK call is not a promise of
persistent IP configuration. No reconnect or project edit is implicit.

Before mutation, SQLite records an UNKNOWN receipt keyed by project/request ID.
Same-content duplicates return the existing receipt; conflicts reject. Native
receipt loss/readback mismatch remains UNKNOWN and never causes automatic replay.
