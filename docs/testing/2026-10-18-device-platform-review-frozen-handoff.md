# Independent Review Repairs: Frozen Handoff

Date: 2026-10-18. Local frozen automation is complete; human UI acceptance is
PENDING and field acceptance is NOT_RUN. This is not a release or deployment.
Source repairs and overlapping regression results are documented in
[the repair report](2026-10-18-device-platform-independent-review-repairs.md).
The later Git delivery checkpoint is recorded in
[the publication report](2026-10-18-device-platform-repair-publication.md).
The previous [handoff](2026-10-18-device-platform-handoff.md) retains historical
build evidence and the broader human/field checklist; use the new binaries below.

## Source And Build Identity

- Workspace: `D:/jsdfhasuh/documents/my_project/emo_master`.
- At build time HEAD was `c097c39934955ababc679f2fb704b50dba488030` plus
  uncommitted repairs. That HEAD alone does not reproduce these candidates.
  This is the pre-publication build checkpoint, not the later Git delivery state.
- Local evidence root: `build/independent-review-c097c399/` (ignored build output).
- `frozen-source-manifest.json` identifies 672 non-pyc files under `src`, `proto`
  and `scripts`; it excludes `__pycache__`. Its SHA-256 is
  `de7c170005b1647d0fdd5c0ec041a28b18979398beee8900d60569d17c4504d9`.
- `frozen-source-verification.json` confirms 672 current files and no changed
  manifest entries after both builds and tests. This is not a dependency lock.
- Toolchain: Python 3.10.21, PyInstaller 6.22.2, hooks 2026.8, reused from
  `build/device-management-imv/frozen-toolchain`. The local `frozen-review.spec`
  selects Designer or Runtime with `EMO_BUILD_TARGET` and uses canonical D: paths.
- `frozen-designer-build.log` and `frozen-runtime-build.log` record successful
  builds. Old output directories were not replaced.

Paths below are relative to the local evidence root unless otherwise stated.

| Candidate | Executable | Complete Distribution |
|---|---|---|
| Designer | `frozen-designer/dist/EmoMaster/EmoMaster.exe` | 687 files, 327456326 bytes |
| Independent Runtime | `frozen-runtime/dist/EmoMasterRuntime/EmoMasterRuntime.exe` | 685 files, 326607895 bytes |

Designer EXE SHA-256:
`bd5c23b48010d2eb2f4b711eb1708647c69e810cd850f3dcd99679331977def4`.

Runtime EXE SHA-256:
`25cf8f9facf1ec3d5faf7ba3fbc4347ab949ceef26da9556466286766e5d20f5`.

Keep each complete distribution including `_internal`; an EXE alone is not a
portable distribution. `frozen-candidates.json` records sizes and identities.
No installer, published Release, or clean-machine verification was produced.

## Automated Results

| Evidence | Result And Scope |
|---|---|
| `source-package-selftest.json` | Source self-test passed; not frozen evidence |
| `frozen-designer-selftest-final.json` and `.execution.json` | status ok, exit 0, managed Runtime frozen=true, native Windows Qt, no remaining candidate process |
| `frozen-runtime-native-selftest.json` and `.execution.json` | status ok, exit 0, frozen=true, native Windows Qt, no imported Designer modules |
| `manual-fixtures-validation.json` | Source-only A/main and B/draft-only Jobs completed, explicit cross-project replacement succeeded, Runtime closed |
| `frozen-final-process-check.json` | Both exact candidate executable paths checked; zero remaining processes |

Designer self-test used a dedicated Runtime data directory, cleared Python
environment overrides and a system-only PATH. Checks cover 70 builtins, six
editor resources, CPU ONNX, SVG icon pixels at three scales, spawned shared-device
Jobs, persistent Dispatch/Join, interprocess leases, uploaded-resource decoding
after source removal and restart recovery. Injected no-SDK IMV/network Host
failures are simulated checks, not vendor SDK, discovery or ForceIP acceptance.

The first Designer self-test also returned status ok and exit 0, but its wrapper
incorrectly read a top-level `frozen` field. The actual field is
`checks.managedRuntime.frozen`. That initial report and execution JSON are kept;
the corrected `-final` run passed. This was a wrapper assertion error, not a
product self-test failure.

Independent Runtime ran 60 seconds initially and then ten restarts: 11 sessions,
1165 total simulated cycles, at least four cycles per session. Every session
stopped with `E_CANCELLED` and retired its worker; Runtime closed successfully.
Its package export/install round trip passed. The retained
`frozen-runtime-native-selftest.json.png` was visually inspected: nonblank
synthetic image/result and controls. This is not human interactive acceptance.

## Human Test Entry

Prepared fixtures use only simulated cameras, communication disabled, and
production autoStart=false. They do not represent a migrated field project.

- `manual-fixtures/A/project.emoproj`: project `review-simulated-A`, startup `main`.
- `manual-fixtures/B/project.emoproj`: project `review-simulated-B`, startup
  `draft-only`, different simulated serial/exposure and complete resource bindings.
- Both contain synthetic `assets/input.png` and a persistent counter initially 7.
- `manual-fixtures/portable-workflow.emowf.json`: schema 1.3 embedded resource
  package, cleared declared site values and disabled imported dependent devices.
- `manual-fixtures/fixtures.json` records paths. The local preparation/validation
  scripts are retained alongside the reports; generation refuses to overwrite.

Run from a new PowerShell window when ready for visible human testing:

```powershell
& 'D:/jsdfhasuh/documents/my_project/emo_master/build/independent-review-c097c399/start-review-designer.ps1'
```

The launcher selects the new Designer and dedicated `manual-designer-runtime-data`
and `manual-designer-cache` directories. It clears an external Runtime target and
database override. Designer recent-project QSettings remain normal user
preferences: explicitly choose fixture A, never a recent field/production project.
The launcher was prepared but not executed; no interactive GUI was left running.

## Pending Human Checks

All rows below remain PENDING. Automated tests do not mark these rows accepted.
Use copies of the fixtures for destructive editing or source-removal checks.

1. Open A through the launcher and start its simulated workflow. Change local
   parameters/layout without saving. Open the separate online-version graph,
   exercise stop controls, and check that draft, disk and undo state are unchanged.
2. With A online, open B. Compare both identities, cancel once, then explicitly
   upload candidate resources and confirm complete stop/drain replacement.
   Check B startup targets, project-scoped history/variables and no inherited
   connection/listening/start intent. Verify both stop and drain choices separately.
3. Import the portable workflow into a saved test project. Check resource bytes,
   blank site fields, new disabled device, undo/redo and save/reopen. Fill only
   simulated site bindings before enabling a simulated device.
4. Stop and close, then restart using the same dedicated test Runtime directory.
   Check committed identity/configuration/resource recovery. Source-file removal
   must affect only disposable fixture copies, never production resources.
5. Complete the broader normal-UI checks in the historical handoff using these
   new candidates, including migration, publication and readonly history.

Unknown response/replay behavior has automated coverage, but an interactive lost-
response exercise is not recorded here; query the original request rather than
issuing a fresh start/write request. Human acceptance, clean Windows verification,
real SDK/camera/PLC/network operations and physical reconnection remain separate
gates. No hardware addresses or production data were changed. The unrelated
`field_tests/plc_dual_mc/build_project.py` working-tree modification was preserved.
