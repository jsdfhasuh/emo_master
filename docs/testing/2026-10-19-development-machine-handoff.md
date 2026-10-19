# Development Machine Handoff

Date: 2026-10-19. Branch: `agent/runtime-workflow-architecture-v1`.

## Published Source Scope

- F02 implementation: `bcc37b7c4e21076f55fc650d21ce58264e2538b3`.
- F02 review baseline: `51ccf6995f1ee2b47fd8cb6904431d0578a468ed`.
- The separate handoff commit contains this document and the previously local
  `field_tests/plc_dual_mc/build_project.py` compatibility change. The generator
  deep-copies operator metadata and removes `xDeviceActionFields` from its legacy
  schema 2.4 fixture only. It does not change the plugin registry or saved field
  projects. The existing generated-project equality tests cover both fixtures.
- Root `AGENTS.md` and the normal-operation follow-up plan are already tracked.
  F01 remains deferred and nonblocking; it is not repaired by F02 or this handoff.

## New Windows Development Machine

Install Git and Conda, then use a Conda-enabled PowerShell terminal:

```powershell
git clone --branch agent/runtime-workflow-architecture-v1 --single-branch https://github.com/jsdfhasuh/emo_master.git
Set-Location .\emo_master
conda create -n emo_master python=3.10 -y
conda activate emo_master
python -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
git rev-parse HEAD
python scripts/dev.py run-designer --local
```

Compare HEAD with the final delivery SHA in the handoff response. For an existing
checkout, preserve local work before switching branches and use `git pull
--ff-only`; do not reset or clean the checkout to make it match.

The default local Designer mode uses an embedded Runtime. Start with synthetic
workflows; do not run field workflows without separate hardware authorization.
See `README.md` and `docs/development-guide.md` for environment and external
Runtime details. No submodules are present in this checkout.

## Local-Only Material

Git synchronization is source delivery, not a backup of the entire machine.
Ignored material was preserved, not force-added or deleted. This includes private
`projects/`, models and calibration/device material, local-only migration tests,
`manual_test_workspace/`, historical evidence, `build/`, caches and local settings.
Some private paths are excluded by local Git rules, not the shared `.gitignore`.
Review exclusions before bringing private data into another public checkout.

Transfer any needed private material separately through a private channel, keeping
project-relative resource paths intact. The Conda environment, hardware SDKs,
device connectivity and machine-specific settings are not provided by Git.
Do not commit credentials or private resources to make a local test pass.

## Verification For This Handoff

Windows / Python 3.10 / PySide2, `QT_QPA_PLATFORM=windows`:

```powershell
$env:QT_QPA_PLATFORM='windows'
python -m pytest -q tests/runtime/test_dual_plc_field_project.py tests/designer/test_dual_plc_project_open.py tests/runtime/test_targeted_resource_repair.py tests/runtime/test_project_resource_transfer.py tests/designer/test_targeted_resource_repair_ui.py tests/designer/test_project_configuration_ui.py tests/designer/test_online_run.py --junitxml=build/workspace-handoff-20261019/validation.xml
```

Result: **60 passed in 48.79 seconds**, no failures or skips. This reruns the
49-case F02 scope together with 11 PLC fixture/native Qt cases; do not add the
earlier 49-case count to this result. PLC execution tests retarget all nodes to
loopback simulators; native opening tests prohibit device connections. The
generator's file-writing entry point was not executed. Existing field projects
were not rewritten.

Ruff passed for the generator and the four F02 source/test files. The diff against
the F02 baseline passed `git diff --check` before the handoff document was added;
the final staged diff is also checked before commit.

NOT RUN for this handoff: full repository suite, clean-machine installation,
fresh frozen build/selftest, human UI acceptance or real-device acceptance.
Earlier EXEs do not contain F02; restart the source Designer or rebuild Designer
for EXE deployment. Local raw test output remains ignored; the reproducible
command and summary above are tracked.
