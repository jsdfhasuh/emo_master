# Operator Runtime Actions and Project Delivery

This supplements the existing Designer release process. It does not change the
`emo-master` target, its installer, or the application tag-release workflow.

## Two Repositories

- Application: `jsdfhasuh/emo_master`, dedicated entry `scripts/run_operator.py`.
- Packager: `jsdfhasuh/python_build_scripts`, target `emo-master-runtime`.
- Local packager: `D:/jsdfhasuh/documents/my_project/python_build_script`.
- Target config: `configs/emo-master-runtime.json` in the packager.

The Runtime uses Python 3.10 and the existing pinned PyInstaller 6.22.2. Its
directory-based portable output contains `EmoMasterRuntime.exe` and the collected
dependencies. Designer modules and its QSS are excluded. Application-installed
operator resources, SQL migrations, Qt, OpenCV, gRPC and CPU ONNX remain included.
Camera drivers/MVSDK still require separate installation where applicable.

## Build Through Actions

Both repositories' new files must first exist in pushed commits. GitHub Actions
cannot build uncommitted files from this workstation.
The new application workflow must also be present on the application's default
branch before GitHub exposes its manual dispatch button. Before that integration,
use the packager's existing **Release Windows Build**, explicitly select
`target=emo-master-runtime`, the pushed source/packager refs and source version tag,
and set **Publish assets=false**. Do not rely on `auto` from an older workflow
revision that predates the Runtime policy.

In the application repository, run **Build Operator Runtime** and supply:

| Input | Value |
| --- | --- |
| source_ref | Pushed application branch, tag or commit containing this implementation |
| packager_ref | Pushed packager commit containing the Runtime target and verification hook |

The workflow resolves the application ref to its commit, reads the source version,
runs focused Runtime regression, and calls the existing central Windows workflow.
The reusable workflow interface is pinned to `29b375fcd368b1f88be14b3356fe182b8e4e02e0`;
the supplied packager ref determines the build scripts/configuration actually checked out.
Both requested refs are resolved to full checkout SHAs before the reusable call.
The central Runtime build records those SHAs, its workflow identity and run URL in
`runtime-build.json`. Source JUnit, build transcripts and process reports are
uploaded even when acceptance fails. The same central manual entry can run this
chain before the app workflow is integrated into the default branch.

This workflow always uses `publish_release: false`. It uploads the portable ZIP,
manifest and frozen self-test evidence as Actions artifacts, not a public Release.
The central workflow also accepts `target=emo-master-runtime`. Its `auto` policy
builds only; publication requires explicit `true` and existing Master provenance
checks. Do not publish a private engineering project as a Runtime release asset.
Local Runtime publishers also default to build-only; `-Publish` is required for
their explicit publication path. This does not change the Designer publisher.

The Runtime ZIP uses deflate so Windows Explorer and `Expand-Archive` can extract
it. Other targets retain their existing compression policy. Keep the complete
directory; copying just the EXE is unsupported.

## Frozen Acceptance

Before the central publisher archives/uploads the Runtime, it executes:

```powershell
.\scripts\verify_runtime_package.ps1 -Executable "D:\packages\EmoMasterRuntime\EmoMasterRuntime.exe"
```

The app workflow also verifies the ZIP SHA256/source identity and re-runs acceptance
after extracting it to a new directory. The helper clears `PYTHONPATH` and
`PYTHONHOME`, restricts `PATH` to Windows/system directories, waits for the EXE,
and requires `frozen=true` with no imported Designer
modules. Its synthetic project checks CPU ONNX, plugins, SQLite migrations, native
worker spawning, continuous cycles, project-package export/install, custom pages
with nonblank image pixels, repeated normal stops/restarts, and exit.
This uses no real camera, PLC or private model and does not prove field acceptance.
The cloud Runtime-only gate additionally checks manifest/source/packager identities,
ZIP and extracted-EXE hashes, then performs a native Windows Qt 60-second run with
10 restarts. Reports record worker retirement, Runtime closure, exit codes and raw
stdout/stderr. A GitHub-hosted runner is not clean-machine or field acceptance.

The EXE also accepts `--self-test --result-json <new-report-path>`. Reports/workspaces
must be new paths. Self-tests never use the operator's remembered engineering project.

## Engineering Project Packages

From the source checkout (or substitute the frozen EXE for `start_runtime.cmd`):

```powershell
.\start_runtime.cmd "D:\engineering\ProductA" --export-package "D:\delivery"
.\start_runtime.cmd "D:\site\ProductA" --install-package "D:\delivery\runtime-<id>.vxpkg"
.\start_runtime.cmd "D:\site\ProductA" --check
.\start_runtime.cmd "D:\site\ProductA"
```

Export collects declared resources and configured file-input parameters using
operator schema metadata, including files outside the engineering directory. Input
paths in the exported copy become relative; device values, output paths, presentation
and production policy are retained. The source project is not modified. Packages
contain no business output/database or imported plugin code. Large resources are
streamed and ZIP64 is supported; the package limit is 16 GiB and 10,000 entries.
Compatibility checks project/component schemas and installed operator versions,
not exact application-build equality. A package may contain project credentials:
treat it as private data and do not upload it to public Actions/Release artifacts.

For offline installation/update, exit the Runtime first. A loaded production project
holds its existing directory ownership lock. In the operator UI, **Update Project**
is available after stopping; this performs the update while retaining ownership.
Updating in the UI follows the newly loaded project's `autoStart` policy.

Installation verifies package names, file hashes/sizes, required operators, workflow
and page bindings in a sibling staging directory before replacing anything. Updates
require the same project ID; a different project uses a new directory. Existing
business outputs and unrelated files are not replaced or pruned. Configuration is
published last; replaced config/input assets are preserved in a sibling
`.runtime-backup-<id>` directory. Normal write failures restore replaced files.
Process interruption or physical power loss during update has not been validated:
if loading fails, retain the backup and repair config/assets offline. This is not an
atomic whole-directory update or a guaranteed power-loss recovery system.

## Repeatable Resource Measurements

```powershell
python scripts/validate_operator_runtime.py --output "D:\checks\runtime-new" --duration 60 --restarts 10 --qt-platform windows
python scripts/validate_operator_runtime.py --executable "D:\packages\EmoMasterRuntime\EmoMasterRuntime.exe" --output "D:\checks\frozen-new" --duration 60 --restarts 10
```

The measurement records owner/worker RSS, handles, CPU time, session identities,
completed cycles, retained diagnostics and a Qt screenshot. Diagnostic queue capacity
is 64 for continuous jobs: slow consumers apply backpressure instead of creating an
unbounded feeder backlog. Only optional diagnostic heartbeat packets may be skipped
when this queue is full; authoritative shared-cell heartbeats continue independently,
including while terminal enqueue and feeder flushing wait. Business events retain
backpressure, and business outputs/counters are not discarded to reduce load.
Stopping waits for configured graceful timeout plus the existing forced-retirement
budget; the GUI remains asynchronous. Source and frozen measurements are distinct.

These synthetic measurements are not a production soak pass. Before field sign-off,
record the real project/model, resolution, triggers, expected output count, cycle-time
budget, run duration, repeated starts/stops, device disconnect/recovery, disk-full
behavior and resource limits. Do not substitute this fixture for those tests.

## Local Verification Record

See [2026-10-06 frozen/delivery validation](testing/runtime-delivery-2026-10-06.md).
The subsequent [review fixes and rebuilt candidate](testing/runtime-review-fixes-2026-10-06.md)
record bounded-queue liveness, failed project switching and cross-channel identity
regressions, plus the replacement EXE/ZIP hashes and frozen acceptance.
The local ZIP is a private build-only candidate from an uncommitted working tree.
Its legacy manifest records the reference HEAD, not the entire dirty source state;
the separate candidate record captures changed file hashes. Download URLs generated
by the legacy manifest do not mean assets have been uploaded or a Release exists.
