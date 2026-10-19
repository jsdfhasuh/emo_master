# Device Plan Gap Repairs

Date: 2026-10-19. Base HEAD: `3396a25d34d1084b56fd8c4867450f0b62e26c64`.
This is a working-tree repair, not a committed or published release. The exact
build inputs are recorded in the local source manifest below, not implied by HEAD.

## Subsequent UI Gap Follow-Up

This earlier report does not cover the eight additional UI gaps identified by
the subsequent plan-to-UI audit. Their implementation and separate verification
are recorded in [Device UI Gap Completion](2026-10-19-device-ui-gaps.md).
The newer public-source snapshot and complete frozen distributions are under
`build/device-ui-gaps/verified/`. The `build/device-plan-audit/verified/` binaries,
hashes and test results below are preserved as historical evidence; they predate
those eight repairs and must not be used to claim their verification.

## Scope And Requirements

This implements the gaps identified in the plan-to-code audit, including the
user's newer explicit requirement that device management is a separate dialog.
The archived R2.1 plan is not rewritten. Existing private projects, models, field
data and historical evidence are untouched. Tests use temporary projects,
simulated devices, SDK substitutes and loopback RPC only.

| Gap | Implemented behavior | Primary evidence |
| --- | --- | --- |
| Device-management placement | One parent-owned window-modal dialog; no third main workspace; close preserves valid draft without publishing | `test_device_workspace.py`, workspace/toolbar tests |
| Hidden/wrong device selection | Search/category clears hidden selection and commands; instance tree and table select the same device ID, including the second device | `test_device_plan_completion_ui.py` |
| Search, instances, status, portable devices | Name search, type/instance tree, policy/owner/queue/error columns; `.emodev` import/export with checked resources, cleared declared site fields, new disabled devices and one undo | `test_device_package.py`, native import/cancel/undo/redo tests |
| References, diagnostics, differences | Clickable workflow/node references with value sources; current-project/connection logs; device revision differences and existing configuration comparison | native reference navigation and device-workspace tests |
| New-device setup | Explicit discovery/adoption and separately confirmed connection test; no publication, capture, PLC write or network-address change | native wizard signals, real Runtime RPC, owner reuse/cleanup tests |
| Camera actual read and temporary trial | Separate read without acquisition; exposure/gain trial on existing managed SDK session; restore pre-trial actual values, trigger/acquisition state and parameter cache | first-trial mismatch, cancellation, real spawn and RPC tests |
| Runtime connection selection | Read metadata first, explicit attach second; preserve draft and old connection on failure; refuse outstanding local operations; no automatic publication or job start | native connection dialog and real transport-switch tests |
| Inline online/history graph | Readonly monitor inside flow area; current Runtime project can differ from draft; Esc does not destroy embedded monitor; editable model stays separate | inline graph, online-run and configuration tests |
| D20 configuration copy | Atomic `online-project.json` after authoritative SQLite commit; separate copy error; restart reconstructs from SQLite | export-copy failure/restart test and packaged synthetic check |

The ordinary online/history graph is inline. The existing **Run Online Version**
window still owns its own start/stop/result workflow and remains separate.
The JSON copy includes configuration and revisions, not resource bytes; it is not
a deployment package or a second authority. Device diagnostics reuse current
in-memory logs, not a new persistent log database.

Camera trials never start by replacing actual values with project defaults.
They snapshot the connected camera first and use the existing configuration
restore mechanism. Actual hardware-trigger state requires explicit synchronization;
it is not silently converted into software triggering. Failed restoration is an
error/quarantine, not a successful recovery. No real SDK or camera was exercised.

## Failure Evidence And Corrections

Local evidence root: `build/device-plan-audit/` (ignored).

- Original hidden-selection probe failed before repair; the original evidence is
  retained in `probe-bootstrapped.log/xml`.
- Initial complete Designer/page suite: **4 failed, 1895 passed**, 3 existing
  JUnit `record_property` warnings, 875.26 seconds (`designer-completion.xml`).
  Two tests still asserted a third workspace: those expectations now assert two
  workspaces plus the separate device-management menu action. Other assertions
  remain intact. The other failures exposed low-zoom saved-center rounding and
  a zero-height toolbox scroll body in a 319-pixel-high window. Geometric center
  capture and reclaiming small-canvas margins fixed those defects.
- Related post-fix Designer regression: **90 passed**, 46.34 seconds
  (`designer-repaired.xml`). The full 1899-test suite was not rerun as one final
  invocation; do not relabel its initial failures as a clean full-suite pass.
- `inline-before.xml`: entering monitor incorrectly used the local project ID.
  The final entry reads the Runtime project first, then pins project/instance.
  Embedded Esc and the existing standalone online-run behavior are tested.
- `multidevice-before-rerun.xml`: second-instance details and highlighted row
  disagreed. The repair selects the matching visible ID, never the first row or
  a hidden instance. Device UI regression: **18 passed**, 9.07 seconds.
- The first multi-device test attempt blocked in native dialog show with its
  parent hidden. Its stack was saved and only that test process was stopped.
  The fixture now shows its parent before the native modal dialog; the functional
  mismatch then reproduced and was fixed. This aborted attempt is not a pass.
- `actual-before.xml`: first trial restored project defaults (10000 exposure,
  zero gain) instead of actual pre-trial values (5678 exposure, gain 1), and left
  trigger configuration changed. The final SDK-substitute test checks the full
  actual-parameter snapshot, stopped acquisition and uninitialized cache.
  Camera/runtime/plugin regression: **47 passed**, 19.99 seconds.
- Earlier Runtime regression: **153 passed**, 87.62 seconds
  (`runtime-completion.xml`), including configuration, resources/repair, owners,
  device network, online graph and IMV. Later camera changes are covered by the
  47-test run and final isolated run; counts overlap and must not be added.

## Final Verification

Final build/test root: `build/device-plan-audit/verified/`.
The public snapshot contains tracked public source/protocol/scripts/tests/examples
and the new public source/test files. It excludes private project directories and
the existing private-project-dependent migration routing test. The tested source
manifest is `source-manifest.json`; each distribution has its own file/size/SHA-256
manifest and builder command/log. The external packager and its configs are not
modified. Earlier build directories are intermediate evidence, not the final build.

- Final isolated regression: **164 passed in 80.77 seconds**, no failures/skips
  (`verified/isolated.xml`). It includes the final multi-device, first-trial,
  camera cancellation, real spawn/RPC, dialog, connection, canvas, toolbox,
  configuration, import/export and online-run changes.
- Ruff passes for all changed/new Python files except generated protocol code.
  Scoped mypy passes for **24 source files**, with the repository's existing
  untyped-body exclusions. Protocol generation `--check` and `git diff --check`
  pass. These do not imply a fresh full-repository test pass.
- The manifest contains **1235 public input files**. Every copied file's SHA-256
  was compared with the current workspace after build: zero mismatches.
- Designer frozen self-test: exit **0**, status `ok`, no errors, real Qt icon
  checks and managed simulated Runtime, including retained cycles, resource
  recovery, device-only import/undo, connection setup and JSON copy checks.
  This does not exercise a successful vendor SDK in the frozen package.
- Independent Runtime frozen self-test: exit **0**, status `ok`, no errors and
  no imported Designer modules. A 60-second first session completed **1123 cycles**;
  **10 restarts** each completed 4 cycles and stopped with `E_CANCELLED`. Every
  worker is reported retired. Retained-cycle, resource recovery, connection setup
  and configuration-copy checks pass; portable-device UI checks run in Designer,
  not the independent Runtime. See `runtime-selftest.json` and its execution record.
- Final process inspection found **0 remaining candidate EXE processes**, including
  children using these distribution paths (`process-cleanup.json`). Other running
  applications were not stopped or replaced.

Final complete onedir distributions:

- `build/device-plan-audit/verified/frozen-designer/dist/EmoMaster/`
- `build/device-plan-audit/verified/frozen-runtime/dist/EmoMasterRuntime/`

EXE SHA-256:

```text
EmoMaster.exe
e834cb64db0a02238a2af82e965af0ed3e07becee1bffe9d4de9905c659b3874
EmoMasterRuntime.exe
03c1bc7eeff8fd6cfe08957f090d10e876a8fde203736ab8b8403821aec89257
```

Use the complete directory including `_internal`, not the EXE alone. Build logs,
commands, full distribution manifests and self-test JSON are under `verified/`.

## Acceptance Boundary

These are Windows/Python 3.10/PySide2 software repairs and native Qt automated
checks. Screenshots at 900x650 and 1280x800 were inspected for overlap and usable
controls. QWidget screenshots omit the OS title bar; dialog flags, parent and
modality are asserted separately. They do not constitute user acceptance.

Manual UI walkthrough, clean-machine installation/launch, real camera/PLC/network
and field acceptance are **NOT RUN**. The formal 88-item R2.1 ledger remains
PARTIAL, not all PASS. The previously accepted/deferred F01 multi-fault resource
pin risk remains deferred; this task does not reopen it or claim to fix it.
No commit, push, Release, hardware access or automatic replacement of an existing
running program is performed. Old EXEs do not hot-load source changes.
