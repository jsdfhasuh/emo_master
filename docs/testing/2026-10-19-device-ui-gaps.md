# R2.1 Device UI Gap Completion

## Scope And Identity

Requested scope: the eight UI gaps identified by the read-only comparison with
the original R2.1 plan. Device management remains a separate QWidget dialog,
following the user's newer explicit requirement. Do not reintroduce a third
workspace to match the older illustration.

Baseline: `3396a25d34d1084b56fd8c4867450f0b62e26c64`, plus the existing uncommitted
device-plan implementation. No existing changes, private projects, models,
calibration data, field settings, or historical evidence were reverted.
The implementation/verification phase did not commit, push, publish a Release,
access real devices, write a PLC, or change a network address. The user's
subsequent explicit commit/push request is covered by Publication Verification
below; it does not authorize a Release or hardware operations.

## Implementation Matrix

| Gap | Implementation | Software Evidence |
| --- | --- | --- |
| Production viewing and idle live preview | Existing managed captures publish a bounded display copy only while observed; passive sessions do not own original frames or maintenance admission. UI shows source Job, cycle, execution revision and time. Explicit idle preview has one outstanding request; Runtime refuses new preview when production is admitted or trigger mode is hardware. | Native modal dialog from device action; real Runtime and substituted IMV Host; source and frozen simulated observation checks |
| State freshness | Opening/default refresh reads known states after metadata. Visible dialog refreshes state every two seconds; hidden dialog stops the timer. Reads preserve pending form edits. Failure and scope change invalidate stale states. Nested confirmation dialogs suppress background reads. | Open/refresh/timer signal tests, editing preservation, existing scope-change regression |
| Per-field online comparison | Source editor shows committed Runtime value/source, document revision and unapplied differences. Dynamic bindings are identified as runtime values. Metadata/snapshot reads remain asynchronous and do not mutate the draft. | Source changes, equal values with different sources, dynamic input, real wizard snapshot refresh, frozen source-field check |
| Per-change effects and impact | Runtime preflight reuses cycle-admission and dependency analysis. Six-column comparison displays effect plus device/workflow/Job impact. Selection/policy changes invalidate results. Direct Write preflights before its single write confirmation; dependency confirmation remains explicit. | Real preflight checks, Qt checkbox and button paths, cancelled write with no request created, existing replacement/transition regressions |
| Capability controls | Device operations are enabled by declared capabilities and supported diagnostic/network types. Unsupported controls explain their disabled state. | Simulated type and real builtin IMV metadata; no IMV network action enabled on unsupported types |
| Existing-device connection test | Tests the current unapplied connection form through the existing setup API, after confirmation. Neither form application, local save nor Runtime publication is performed. | Native button cancellation/confirmation, exact submitted draft fields, unchanged session payload |
| Basic device identity | Selected-device summary, type/driver version, basic-information tab and read-only hardware identity. Hardware data comes from the cached explicit Runtime discovery, with its source labeled. Unavailable values are not invented. | Known-state identity integration; simulated model/serial/MAC; native screenshots |
| Information hierarchy | Separate project toolbar and selected-device toolbar; plugin icons, readable connection-policy labels, structured state/reference tables, source links and update status. | Native 900x650 and 1280x800 images, reference-navigation regression, Lucide embedded assets |

Production observation waits for captures arriving after subscription; it does
not manufacture a frame or start acquisition to fill an empty display. The JPEG
copy is limited to 960 pixels on its long side and 2 MiB, with observation encoding
at most five times per second. The existing eight-session diagnostic limit is
retained. Unchanged observation sequence returns no repeated image bytes.
The copy is independent of original-frame release, and closing it never ends a
production consumer. There is no new persistent journal, resource GC, file
watcher, hardware polling, or replacement scheduling framework.

The online source label describes committed configuration, not frame-level
hardware evidence. Parameter readback and trial restoration keep their existing
separate meaning. A stopped/failed preview is not automatically retriggered.
An explicit read-only refresh is available after a configuration/identity change.

## Failure Evidence

Evidence root: `build/device-ui-gaps/` (ignored).

- `before.xml` and `../device-ui-gaps-before.log`: the original five new UI
  regressions all failed before implementation: no state read on open,
  unsupported network action enabled, no field comparison, no effect columns,
  and no passive/live preview loop.
- `first.xml/log`: collection failed because the new Designer and Runtime files
  initially used the same module basename. The Runtime test file was renamed;
  no test assertions were removed.
- `second.xml/log`: six passed and two failed. The hardware-preview failure
  exposed an incorrect `waitMode` lookup, corrected to the declared `triggerMode`.
  The other failure was a simulated fixture using nonexistent `connection.source`
  instead of `connection.serial`; its intended preflight assertions remain intact.
- Existing device-control fixture now explicitly declares the operations it is
  testing, instead of treating an empty capability list as support for every
  operation. Existing control/confirmation assertions remain intact.

## Verification

- Initial focused regression: **60 passed, 33.71 seconds**.
- Extended related regression: **173 passed, 98.09 seconds**
  (`regression-2.xml`). It includes normal device operations, configuration,
  replacement/transition, resources, retained loop/Dispatch ownership, actual
  spawn/IMV RPC with fake SDK, device package, Runtime connection, and Qt paths.
- Source self-test: exit **0**, status `ok`, errors empty. New
  `sourceFieldCompared` and `cameraObserverChecked` are true. It also retains the
  earlier cycle/resource-recovery checks. All hardware is simulated.
- Native Qt uses `QT_QPA_PLATFORM=windows`, Python 3.10 and PySide2. The new
  regression set includes a real nested `exec_()` dialog activated by the device
  toolbar, with automated interaction and Runtime-backed image observation.
- Native device screenshots at 900x650 and 1280x800, plus the production-view
  screenshot, were visually inspected. QWidget screenshots omit OS title bars;
  existing window-parent/modality tests cover the independent-dialog contract.
- Ruff passes for all changed/new Python files except generated protocol code.
  Scoped mypy passes for **11 affected source files** with existing untyped-body
  exclusions. Protocol-generation consistency and whitespace checks pass.

Counts overlap; do not add them or call them a fresh full-repository test pass.
Old `build/device-plan-audit/verified/` EXEs predate these eight gap repairs and
are not evidence for this iteration.

## Final Isolated And Frozen Verification

Final evidence root: `build/device-ui-gaps/verified/` (ignored).

- Final isolated public-source regression: **173 passed, 100.36 seconds**, no
  failures, errors or skips (`isolated.xml`, `isolated.log`). This is the related
  regression set, not a fresh full-repository run.
- The independent snapshot contains **1237 public input files**, excluding
  private project material. Every input was compared to the current workspace
  after build: **zero mismatches**. `source-identity.json` records the base HEAD,
  branch, file count and manifest identity; HEAD alone does not identify these
  uncommitted build inputs.
- The external packager and its configuration were not changed. Each new frozen
  directory contains its build command/log and complete distribution manifest.
- Designer frozen self-test: exit **0**, status `ok`, errors empty, with real
  Qt icon checks and a managed simulated Runtime. New `sourceFieldCompared` and
  `cameraObserverChecked` checks are both true. Existing targeted cycle, resource
  recovery, portable-device, connection and configuration-copy probes also pass.
  See `designer-selftest.json` and `designer-execution.json`.
- Independent Runtime frozen self-test: exit **0**, status `ok`, errors empty,
  `frozen=true`, and **no Designer modules imported**. With a **one-second first
  session and two restarts**, the three sessions completed **18, 4 and 4 cycles**,
  stopped normally with `E_CANCELLED`, and retired all workers. The passive
  camera-observer check passes. Designer-only field comparison is intentionally
  not executed by this independent Runtime. See `runtime-selftest.json` and
  `runtime-selftest.json.execution.json`.
- Final process inspection found **0 remaining candidate EXE processes**
  (`process-cleanup.json`). No unrelated applications were stopped or replaced.
- Native screenshots are retained under `screenshots/`: `gap-device-900.png`,
  `gap-device-1280.png`, and `gap-camera-production.png`. The production image
  uses simulated dark pixels; a non-null rendered pixmap and its production
  provenance are asserted. This is not a real-camera image-quality check.

SHA-256 of `source-manifest.json`:

```text
2d406a06c45fefd70ec93b09d1b46bb23a5e7240423ab472aa52a05ff170d3e6
```

Fresh complete onedir distributions:

- `build/device-ui-gaps/verified/frozen-designer/dist/EmoMaster/`
- `build/device-ui-gaps/verified/frozen-runtime/dist/EmoMasterRuntime/`

EXE SHA-256:

```text
EmoMaster.exe
cd0d141ba54c9943e15cdfcd4d9b4a6e035e4789d68a10958c6a78c461acdb94
EmoMasterRuntime.exe
142414c470486aca7603f0eece6ebaae6ed292a3319175e0c6149405f2a57bca
```

Use the complete directory including `_internal`, not the EXE alone. Existing
running programs do not hot-load these source changes. These new EXEs supersede
the earlier device-plan-audit EXEs only for this software-verification scope;
neither build is a published Release or a completed user/hardware acceptance.

## Publication Verification

After the user's separate commit/push request, the explicit **45-file** public
candidate was staged and its **1237-input** public source snapshot was exported
from the Git index, not copied from an unstaged mixed workspace.
Local publication evidence is under `build/device-ui-gaps/delivery-20261019/`.

- Fresh staged-source related regression: **173 passed, 104.96 seconds**, no
  failures or skips (`staged-regression.xml`, `staged-regression.log`). This is
  another run of the related suite, not a full-repository test pass.
- Ruff passes for all staged changed/new Python files except generated code.
  Scoped mypy passes for **27 affected source files**, retaining the existing
  untyped-body exclusions. Protocol generation `--check` and staged whitespace
  checks pass.
- The staged public source was compared with the recorded frozen-build inputs:
  no code/content differences. **570 text files differ only in checkout line
  endings** (CRLF versus LF). No new binary build or frozen self-test was run
  for this publication-only request; the earlier frozen results above retain
  their stated scope and identity.
- Staged filenames contain no private project, model, binary distribution or
  local evidence paths. The private-path and credential-pattern scan finds no
  matches; this is a scoped check, not a general security certification.
- A before/after SHA-256 baseline covers **19775 protected local files**, including
  private project material, local manual-test workspace and retained historical
  evidence. No protected files changed or disappeared.

This candidate is intended for the existing branch using a normal non-force
push. The actual resulting commit and live remote identity must be confirmed
after publication; the immutable base recorded above is not the delivery SHA.

## Acceptance Boundary

This is software implementation and automated native Qt verification, not user
UI acceptance or a clean-machine/hardware qualification. Manual UI walkthrough,
clean-machine installation, vendor SDK/camera/PLC and field acceptance remain
**NOT RUN**. The formal R2.1 ledger remains PARTIAL. Accepted/deferred F01 resource
pin/corruption/repair/CANCEL combinations remain deferred and nonblocking.
