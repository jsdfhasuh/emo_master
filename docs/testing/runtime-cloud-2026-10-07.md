# Runtime Cloud Windows Build Acceptance (2026-10-07)

## Scope and Candidate

The independent operator Runtime cloud Windows build, portable archive and real
frozen worker acceptance are **PASS**. No tag or formal Release was created,
published or overwritten. Clean-machine and field acceptance remain **NOT_RUN**.

This task verified `D:/jsdfhasuh/documents/my_project/emo_master`, branch
`agent/runtime-workflow-architecture-v1`, initially at
`1450907105d4843a20f14676c1a399750d9b1eb3`. It did not read the rejected C-drive
application checkout or assume the two paths were identical. Existing Designer
commits and later unrelated PLC debugging changes were preserved.

| Identity | Exact Revision |
| --- | --- |
| Built application source | `88ae05a55ed03e2eb9ab84928fe8af9f2870505c` |
| Checked-out packager | `29b375fcd368b1f88be14b3356fe182b8e4e02e0` |
| Executed central workflow | `29b375fcd368b1f88be14b3356fe182b8e4e02e0` |
| Target/version label | `emo-master-runtime` / `v0.6.1` |
| Publication input | Explicit `publish_release=false` |

Both repository pushes were independently verified with `git ls-remote`.
The source workflow integration is commit `f1c85c22cbbebe654bee1b701f6da2a1e7574cf4`;
the launcher-test portability fix is the built source commit above. Evidence and
documentation are committed later and do not change the built candidate identity.
Historical PR #2 in either repository was not recreated or repaired again.

## Implemented Chain

- Runtime-only target: onedir, no console, CPU ONNX, no Designer application modules.
- Action `auto` and local Runtime publishers default to build-only. Explicit
  publication retains the existing Master provenance guards; other targets retain
  their previous defaults. No publication path was exercised here.
- The app wrapper resolves source and packager refs to full checkout SHAs and pins
  the reusable workflow interface to the packager SHA above.
- The central workflow records requested/resolved refs, workflow identity, run URL
  and publication intent in `runtime-build.json`.
- Source JUnit, build transcript, frozen reports, exit codes, raw stdout/stderr and
  archive/native evidence are uploaded with `always()`, including on failure.
- Before archiving: frozen EXE acceptance. After archiving: manifest and ZIP hash,
  embedded/extracted EXE identity, extracted offscreen acceptance, then native Qt
  60-second continuous execution and ten restarts.

The app workflow is not yet on application default branch `main`, so its direct
manual dispatch is **NOT_RUN**. This task used the existing central default-branch
entry, which executes the same source-owned regression and archive/native gates.
No main-branch integration was performed merely to expose a dispatch button.

## Cloud Runs

| Run | Result | Original Evidence |
| --- | --- | --- |
| [37558377392](https://github.com/jsdfhasuh/python_build_scripts/actions/runs/37558377392) | FAIL: 108 passed, one launcher test failed because the hosted runner has no developer Conda path | [First run](runtime-cloud-2026-10-07/run-37558377392/) |
| [37559011805](https://github.com/jsdfhasuh/python_build_scripts/actions/runs/37559011805) | PASS: source, build, frozen, archive/native gates and upload | [Successful run](runtime-cloud-2026-10-07/run-37559011805/) |

The first run successfully uploaded failure artifact `11455549666`. Its build,
ZIP and native acceptance were **NOT_RUN**, not passing. The fix passes
`EMO_MASTER_PYTHON=sys.executable` only to the launcher test and reports stdout
alongside stderr. Production launcher defaults and assertions were not weakened.

The successful run used Python 3.10.11 and PyInstaller 6.22.2. Source regression:
**109 passed, no failures or skips**, 74.99 seconds. The unrelated Vision Train job
and the disabled Runtime installer step were skipped by target selection, not
counted as acceptance passes.

## Download and Byte Identity

[Download the successful Actions artifact](https://github.com/jsdfhasuh/python_build_scripts/actions/runs/37559011805/artifacts/11455793477).
It requires GitHub artifact access and expires on `2027-01-05T01:49:46Z` under the
current retention policy. The artifact contains the Runtime ZIP and raw evidence.
Its outer Actions-artifact digest is recorded in [artifacts.json](runtime-cloud-2026-10-07/artifacts.json);
that service-provided digest is distinct from the independently checked Runtime ZIP.

| File | Bytes | Independently Checked SHA256 |
| --- | --- | --- |
| `emo-master-runtime-windows-v0.6.1.zip` | 124132917 | `5187a5c1e0b428efa7d2c40886c7bdd9c2e75d2e79893f79f8b59c145c1c774a` |
| `EmoMasterRuntime/EmoMasterRuntime.exe` | 4860261 | `90fce6dc51b06c8e70a299cb887fddee9e0717c65b5615963147e8c377b78a36` |

The downloaded ZIP hash equals the manifest and cloud validation record. The EXE
stream inside the ZIP equals the cloud direct/extracted execution hashes and the
independently extracted local file. Source, packager and workflow SHAs match the
explicit dispatch inputs. See [download-audit.json](runtime-cloud-2026-10-07/download-audit.json).

The legacy manifest still contains a generated Release download URL. That URL is
**not** a published Runtime asset; use the Actions artifact link above. The
`release_tag` input is a source-version label, not a tag-creation operation.
Keep the full `EmoMasterRuntime` directory including `_internal`; copying only the
EXE is unsupported. A complete private engineering project is delivered separately.
The 124 MB ZIP is not committed to Git.

## Frozen and Extracted Execution

All three cloud EXE gates report `frozen=true`, no imported Designer modules,
CPU ONNX execution, builtin/plugin discovery, SQL migrations, project-package
round-trip, custom pages with nonblank image pixels and normal Runtime closure.

| Check | Sessions | Observed Completed Cycles | Exit/Stop |
| --- | --- | --- | --- |
| Cloud pre-archive offscreen EXE | 3 | 11, 4, 4 | Main exit 0; all `E_CANCELLED`, workers retired |
| Cloud extracted offscreen EXE | 3 | 11, 4, 4 | Main exit 0; all `E_CANCELLED`, workers retired |
| Cloud extracted native Qt, 60 s plus ten restarts | 11 | First 712; subsequent ten each 4 | Main exit 0; all `E_CANCELLED`, workers retired |
| Locally downloaded/extracted native Qt, 60 s plus ten restarts | 11 | 1101, 4, 4, 4, 5, 5, 4, 4, 4, 5, 4 | Main exit 0; all `E_CANCELLED`, workers retired |

These are real spawned frozen workers running the **synthetic-two-blobs** fixture,
not mocked processes. Each report records actual worker PIDs and distinct Job IDs,
continuous cycle counts, retained diagnostics, `workerRetired=true` and
`runtimeClosed=true`. Checks run in fresh temporary project/work directories with
`PYTHONPATH`/`PYTHONHOME` removed and system-only `PATH`.

The local run used the downloaded cloud binary, not the old developer-built EXE.
Its post-exit candidate-process query found zero remaining candidate Runtime
processes. See [local extracted audit](runtime-cloud-2026-10-07/local-extracted/local-extracted-audit.json)
and the original local process/report/stdout/stderr files in that directory.

The [cloud native screenshot](runtime-cloud-2026-10-07/run-37559011805/archive-check/native.json.png)
was inspected: operator controls, count 2 and both blob images are visible without
overlap; no design editor is shown. Screenshot inspection is not field acceptance.

## Local Regression History

| Evidence | Result |
| --- | --- |
| `local-tests/source-final/source-regression.*` | Current built source: 109 passed in 79.90 s, zero skips |
| `local-tests/packager-tests-final.log` | Fresh full suite: 367 executed, 366 passed, one directory-symlink skip |
| `local-tests/packager-tests.log` | Earlier FAIL retained: old in-memory workflow assertion while files changed; not counted as passing |
| `local-tests/verifier-tests.xml` | Earlier FAIL retained: two helper evidence-capture defects |
| `local-tests/verifier-tests-fixed.xml` | Four helper regression cases passed after fixes |
| `local-tests/final-pinned-validation.xml` | Seven helper, self-test and workflow cases passed |
| `local-tests/launcher-regression.xml` | Intermediate FAIL retained: missing `os` import in test-only portability patch |
| `local-tests/launcher-regression-final.*` | 12 launcher/boundary cases passed after the import fix |

Collections overlap; do not add their counts together. Changed Python Ruff checks,
Mypy under repository settings for 334 source files, PowerShell parse checks and
diff checks passed during implementation. Full application/Designer CI was not
rerun by this task. [Packager unit CI](https://github.com/jsdfhasuh/python_build_scripts/actions/runs/37558225797)
also passed at the exact packager commit.

## Nonpublication and Reproduction

[Before](runtime-cloud-2026-10-07/release-before.json),
[after](runtime-cloud-2026-10-07/release-after.json) and
[comparison](runtime-cloud-2026-10-07/release-preservation.json) independently verify
unchanged application tag refs, Release ID/body hash, and asset IDs/sizes/update
timestamps. The packager still has no tags or Releases. Formal publication and tag
creation are **NOT_RUN** by explicit scope, not validation failures.

```powershell
gh workflow run release-windows.yml -R jsdfhasuh/python_build_scripts --ref master `
  -f target=emo-master-runtime `
  -f source_ref=88ae05a55ed03e2eb9ab84928fe8af9f2870505c `
  -f packager_ref=29b375fcd368b1f88be14b3356fe182b8e4e02e0 `
  -f release_tag=v0.6.1 -f release_repo=jsdfhasuh/emo_master -f publish_release=false
```

This reproduces the tested inputs while `master` still resolves to the recorded
workflow SHA. If it advances, select a pushed branch at that exact workflow commit
and independently confirm the resulting run's workflow SHA. Source/packager refs
alone do not pin the workflow revision selected by `--ref`.

## NOT_RUN Boundaries

- Clean target Windows machine without developer tooling; driver/prerequisite installation.
- Real engineering project, private model and production input/output contract acceptance.
- Camera/PLC connectivity, trigger behavior, device disconnect/recovery and physical outputs.
- Production cycle-time budget, long-duration soak/resource stability and field sign-off.
- Disk-full behavior, interrupted project updates and physical power-loss recovery.
- Direct dispatch of the app wrapper before default-branch integration; main-branch integration.
- Full application/Designer CI at this candidate, tags and formal Release publication.

Cloud-hosted Windows, native Qt, synthetic model checks and 60-second measurements
are separately identified here; none substitutes for those unexecuted checks.
