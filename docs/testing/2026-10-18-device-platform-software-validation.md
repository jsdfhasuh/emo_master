# Device Platform Software Validation

This continues the [Dispatch/publication checkpoint](2026-10-18-device-platform-dispatch-publication.md).
It separates implementation, synthetic software checks, frozen execution and field
acceptance. No real camera, PLC, SDK binary, network reconfiguration, deployment,
commit, push or Release was performed. Existing private project files were not
rewritten to satisfy obsolete private migration fixtures.

## Implementation

| Area | Implemented boundary |
|---|---|
| Static pages | Image and navigation-icon resource selectors; purpose/dependency validation; private hash-checked files; bounded PNG/JPEG/BMP decode; navigation/hide/close retirement; no capture demand |
| Publication | Appearance-only changes preserve execution revision and live Jobs; appearance plus declared next-cycle parameters is cycle-safe; actual data-source/scope changes remain execution changes |
| Connection recovery | SLMP connection-only retries for declared NOT_EXECUTED errors, finite count/deadline/backoff, cancellable; no read/write/capture replay |
| Cross-crash files | OS-held generation leases acquired before Job/Dispatch/Host/debug consumer entry; live orphan protection; exclusive retirement; durable tombstone and late-start fence |
| Network | Explicit Runtime-host discovery, typed vendor ABI gate, MAC/generation/definition checks, paused maintenance ownership, ForceIP with readback, durable at-most-once request receipts |
| UI | Discovery, adopt-to-draft and physical IP mutation are separate; confirmation and receipt lookup; pending operation fences dialog close |
| Offline identity | Plugin-declared complete identity fields prompt explicit reuse; no driver load/discovery; no merging defaults/policies; duplicate edits roll back atomically |

The persistent Dispatch/Join, scoped stop/drain/reconnect, historical read-only
graphs, migration inventory, resource/site bindings and explicit remote resource
transfer remain implemented as recorded in the preceding checkpoint.

## Evidence

Artifacts are under `build/device-management-imv/`. Python is the existing
`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe` (3.10.21). Source regression
uses `QT_QPA_PLATFORM=offscreen`, `HUARAY_CAMERA_SMOKE=0`; native screenshots use
Windows Qt and `QT_SCALE_FACTOR=1.5`.

| Artifact | Result and scope |
|---|---|
| `static-recovery-final.xml` | 551 passed; earlier static-page/recovery increment, before later lease/network code |
| `resource-leases-final.xml` | 30 passed, 1 skipped; skip is opt-in 1000-execution/50-session acceptance, not a lease test |
| `page-live-lease.xml` | 39 passed; earlier live page/lease increment |
| `network-admission.xml` | 33 passed; real loopback gRPC, injected native API/Host, stale identity, paused admission, durable UNKNOWN-before-call, no duplicate mutation, stranded Host fence |
| `publication-freeze-preflight.xml` | 27 passed; mixed page/parameter publication, source package self-test, actual Runtime parent termination with live child, late-consumer retirement |
| `network-native-final.xml` | 5 passed; normal signal activation and inspected Windows 150% network/static-page screenshots |
| `handoff-ui-final/` | Native device workspace visual probe exited 0; synthetic metadata/diagnostics only |
| `handoff-pages-native.xml` | 8 passed; inspected resource/site, migration inventory and online-history native screenshots |
| `software-handoff-regression.xml` | 4694 passed, 4 skipped, 3 reporting warnings, 2021.69 seconds; complete pre-final-patch source regression |
| `identity-reuse-after.xml` | 66 passed; reuse prompts/atomic catalog edits/metadata/related Designer tests |
| `software-handoff-final-patch.xml` | 240 passed, 82.13 seconds; final affected Designer/core/device/network/recovery/page/lease/package modules |
| `identity-atomic-final.xml` | 7 passed; final offline identity checks, including duplicate commitNode rollback |
| `handoff-final-native.xml` | 13 passed; final identity/network/static-asset UI plus native save-warning test skipped by offscreen broad run |

The broad suite covers `tests/runtime`, `tests/designer`, `tests/core`, `tests/ui`
and `tests/test_windows_package_entry.py`. It excludes the following previously
identified private, obsolete two-station recipe fixtures, without modifying them:

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

## Frozen Candidate

The comprehensive run used the 579-file input manifest
`software-handoff-source-manifest.json` (SHA256
`189653afd0bab7714eea5ff39dbf5f17c1d1004ec571e6a42300279b5e6486ed`).
Those existing files did not change during that run. Afterwards, offline identity
UI/metadata, two icon names and four type annotations were corrected. Final
affected-module regression and a fresh frozen build cover that delta; the broad
result is not presented as a rerun of the final tree.

Final `ruff check src`, protobuf `--check`, and `mypy src` passed; mypy checked
489 source files. The final manifest covers 580 source/proto files and has SHA256
`2a6c64fa180eab53bc79b18b95e911e33ae2b60f70727762d1452ecb06def13b`.
The manifest is `software-handoff-final-source-manifest.json`; all 580 entries
were hash-verified unchanged after the final build and frozen self-test.
The final `git -c core.safecrlf=false diff --check` also passed.

Broad skips: opt-in 1000-execution/50-session stress, opt-in 65-second debugger
lease test, native save-warning under offscreen Qt, and unavailable symlink
creation. The native warning case subsequently passed in `handoff-final-native`.
The three warnings concern pytest record_property with xunit2, not failed product
assertions. The two stress cases and symlink-specific case were not run.

Reproduction from the repository root, with the private exclusions above appended
as one `--ignore=<path>` argument per file for the broad regression:

```powershell
$python = 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:HUARAY_CAMERA_SMOKE = '0'
& $python scripts/gen_proto.py --check
& $python -m ruff check src
& $python -m mypy src
# Append the nine explicit --ignore paths listed above.
& $python -m pytest tests/runtime tests/designer tests/core tests/ui tests/test_windows_package_entry.py -q
```

The commands describe reproducibility; actual completed results are recorded in
the evidence table and final handoff, not inferred from command text.

PyInstaller 6.22.2 with Python 3.10.21 built the final candidate
`frozen-handoff-final/dist/EmoMaster/EmoMaster.exe` from the existing test spec,
without modifying the sibling packaging checkout. The build completed successfully
in `frozen-handoff-final-build.log`. Earlier `frozen-network-leases` results are
historical and are superseded by this final candidate.
`frozen-handoff-final-selftest.json` reports status `ok`, errors empty; the process
exited 0. EXE SHA256 is
`da4b257baa96729ba6a84d6ceae010f6f2ec0a648e2ce6eb9644e1937bbeb675`.
The complete distribution contains 687 files totaling 327439913 bytes, including
the required `_internal` directory. The post-test exact-executable-path process
check found zero remaining processes.
The probe uses two ordinary Jobs, three Dispatch workers, an injected no-SDK
failure Host, injected discovery/ForceIP Hosts and a separate resource consumer.
It checks static assets after source deletion and restart, active-child lease
protection, late-child rejection and actual process retirement. It is not a full
frozen GUI or clean-machine installation test.

Build input is identified by HEAD `63070277133b89d61a5d27dde6d5f95494b4dce0` plus
the dirty-source manifest. A HEAD alone cannot reproduce this uncommitted build.
Final hashes and post-check counts are also recorded in the
[handoff](2026-10-18-device-platform-handoff.md).

## Public Delivery Preflight

The subsequent user-authorized commit/push preparation selected 215 source,
test, plan and documentation files against base
`63070277133b89d61a5d27dde6d5f95494b4dce0`. It excluded the unrelated local edit
to `field_tests/plc_dual_mc/build_project.py`, private projects, private migration
fixtures and build outputs. A 690-file protected baseline remained unchanged;
the candidate credential-pattern scan had no matches. This is a scoped check,
not a guarantee that arbitrary secrets can be detected by pattern matching.

A staged-tree archive was extracted under
`build/device-platform-delivery-20261018/candidate`. Its 1176 tracked source,
protocol, test and script files matched the staged blobs after Git CRLF
normalization. Ruff, protobuf consistency, mypy (489 source files) and the
embedded Designer startup check passed there. No window or Job was started by
the startup check.

The initial selected 283-case regression had 281 passes and two setup errors:
`designerApplication` was unavailable to two new finish-UI cases in the combined
selection, including a canonical-path rerun. The file alone passed. Those two
cases now use the existing root-level `retainedQtApplication` fixture, matching
the other new device UI tests; their assertions were not removed or weakened.
No production source changed during delivery preparation.

- `public-candidate-final-tests.xml`: 283 passed in 88.94 seconds, no skips or errors.
- `public-finish-ui-native.xml`: 3 passed in 3.27 seconds, Windows Qt at 150%.
- The original 580-file frozen-build input manifest still matched every file.
- Initial failing reports remain locally as `public-candidate-tests.xml` and
  `public-candidate-canonical-path-tests.xml`; they are not counted as passes.

These are additional isolated-candidate checks, not another full-suite run or
hardware acceptance. This documentation section was added after those checks.
The earlier no-commit statements describe their respective validation checkpoints;
the subsequent delivery commit is identified by Git history, not by a self-referential
hash in this file. No tag, Release, deployment or physical-device operation is part
of this delivery.

## SDK Header Provenance

The native device-info layout and `IMV_GIGE_ForceIpAddress` signature were read
from vendor `SDK_2.4.2.3_Build20240711.zip`, 70612392 bytes:

```text
http://download.huaraytech.com/pub/sdk/Ver2.4.2/Windows/Base_ver/SDK%E7%AE%80%E6%98%93%E7%89%88%EF%BC%88%E5%85%8D%E5%AE%89%E8%A3%85%EF%BC%89/SDK_2.4.2.3_Build20240711.zip
archive SHA256: b8e5c583d8b49bd94bbafd9bdad35004bbd7ce4c213f20906580067e185ab06f
Development/Include/IMV/IMVDefines.h (51818 bytes):
4ef97b66a9033f625168a4cc2cf3da3692327c684154aa2a03b594e781960d1b
Development/Include/IMV/IMVApi.h (55225 bytes):
a6fc72ad07387720ec9e46aa4127ba37de4f7d0ccb15e9e42dadfdf94adf841a
```

HTTP provides provenance, not cryptographic vendor authentication. HTTPS was not
available. No downloaded DLL/EXE was loaded or installed. The download initially
timed out at 48429323 bytes and then resumed. The typed structure tests verify
GigE=3092, USB=3096, DeviceInfo=10312 bytes and the second list element. Discovery
rejects versions outside the reviewed 2.4.2 series. Header review does not prove
installed SDK or real network behavior.

## Retained Failures And Limits

- An initial resource-crash fixture let both parent and child wait on one
  multiprocessing Event; killing the parent stranded Event notification. Only
  the identified test process tree (PID 68952) was terminated. The fixture now
  leaves the parent sleeping and tests the child independently; subsequent actual
  parent-death tests passed. No application process was broadly terminated.
- Initial native-API test import used the wrong class name; corrected to ImvApi.
- Scoped mypy exposed worker callable/tuple and ctypes field-list inference
  issues; corrections preserve runtime behavior. Whole-source checking is a
  separate gate, not inferred from the scoped pass.
- Whole-source mypy initially found four annotation errors in project save-as,
  Dispatch package reference mapping and device self-test metadata. The initial
  errors are retained here; final correction/recheck is recorded separately.
- `identity-reuse-before.xml` intentionally captured the missing offline reuse
  prompt before wiring it: 6 failed, 1 passed. No Runtime duplicate-identity
  rejection was removed to make the UI tests pass.
- POSIX flock implementation is present but not executed on this Windows host.
- Legacy exposed files without generation leases remain retained. Recovery is
  startup-driven, not a continuous cleanup service. Unknown files are untouched.
- ForceIP does not promise persistent IP; receipt loss remains UNKNOWN. No
  automatic hardware replay or reconnect is introduced.
- SDK/network/PLC/camera/trigger/clean-machine and full manual GUI acceptance
  remain NOT_RUN. Formal A01-G08 statuses are not promoted merely by unit tests.
