# First actual Windows capture: pinned original baseline

This new workflow prepares the one remaining approved capture. The earlier
`ecf8bcf` job failed its offline fixture preflight before starting any capture.
The corrected `0c1ac2f` no-capture preflight passed its 39 fixtures, existing MSVC
build and read-only QPC/TDH metadata checks. Neither result is real trace evidence.
Independent review and completion of the current normal CI and credit diagnostic
runs are required before publication. There is no automatic capture retry.

## Exact source separation

The workflow has two sibling checkouts, both with credentials disabled:

- `execution`: `ecf8bcfcf193e05807556340d670f05c0f3d9e15`, with its parent
  `655eeac84d0602aa670c76af67679509782bbd7f` retained by depth two
- `tools`: `0c1ac2fddf453913a29e03b2cb84043ca7d92afe`, with its parent `ecf8bcf`
  retained by depth two

Execution `ecf8bcf` adds only the original six diagnostic files to `655eeac`.
Its production source, tests, pytest configuration, dependencies and shared SQLite
adapter have exactly the original baseline Git objects:

| Source | Git object shared by `655eeac` and `ecf8bcf` |
| --- | --- |
| `src` | `ce91274b7b615236f53be4177b4aa03d1925a120` |
| `tests` | `fb3ff6b1f64d1ebf5638b905d9ee72571261a969` |
| `pyproject.toml` | `525ad339f17a6cc23f1a3506b7ec0f7dc5afd5d5` |
| `requirements.txt` | `5e7fdea4df7de62d8e651e86bb307aa15fd6e9ca` |
| `requirements-dev.txt` | `e39a7aea7d76fc6582771fe67d10faf08535a460` |
| `scripts/r3_sqlite_phases.py` | `902092633b6d43dc16be1e4522a02bc85a6b4e97` |

The controller/observer, C++ reader and WPR profile are byte-identical across
`ecf8bcf` and `0c1ac2f`; the shared SQLite adapter also matches. The workflow checks
exact checkout HEADs, parents, clean trees, the six-file baseline difference, these
Git object identities and the matching file bytes before allowing any capture.

Dependencies are installed from `execution/requirements-dev.txt`, exactly as in
the original CI. This file installs dependency requirements, without an editable
project install. The fixed 39 offline fixtures run explicitly in a separate Python
process whose working directory is `tools`; the older baseline fixture file is
never invoked and is outside default pytest discovery.

A separate source preflight runs from `execution`, loads its actual pytest config
(`pythonpath = ["src"]`) without collecting or running tests, and imports the real
observer, adapter, SQLite store and presentation RPC/store modules. Exact module
origins and every imported project package/namespace path must belong to execution.
Unexpected `PYTHONPATH` or `PYTEST_PLUGINS` fails closed. No import path is persisted
between processes and no GitHub environment identity is rewritten. Baseline source
and tests contain no `GITHUB_WORKSPACE` dependency.

The reader is compiled from the verified matching tools source using the existing
MSVC installation, then performs the same read-only native metadata preflight.
The unchanged controller is invoked from `execution` and resolves its repository
from its own `__file__`. Its original parent/message/six-file allowlist preflight
remains valid, runs before capture, and is repeated before valid evidence is emitted.
The notification change and its new tests in `0c1ac2f` never enter execution.

## New one-shot gate, unchanged diagnostic scope

Only `.github/workflows/runtime-kernel-pinned-baseline.yml` is a new workflow.
It requires a push on `agent/runtime-workflow-architecture-v1`, its own changed
path, `github.run_attempt == 1`, and
`github.event.before == 0c1ac2fddf453913a29e03b2cb84043ca7d92afe`. The exact message is:

`diagnostic: one pinned baseline Windows trace 655eeac 19d4e683`

The old capture and no-capture workflows are unchanged. Neither old workflow path
changes, and neither old before/message gate matches this publication. The new
workflow has no dispatch, schedule, pull-request trigger, raw artifact upload,
cache or retry. Original CI is unchanged.

The [original design](2026-10-02-windows-commit-trace-design.md) remains normative
for the two exact target cases, 256 MiB requested circular pool, closed-schema
timing output, owned-instance cleanup, interpretation and all other limits.
The entire new job has the same 30-minute maximum and the same 29-minute internal
budget, which includes both checkouts, dependency setup and all preflights.
Capture stops at the first failed target report, both targets retired, suite end,
deadline or storage guard. The remaining original pytest suite continues under
the unchanged controller budget. No test wait, durability setting, target, system
tool, driver, security setting or user's computer is changed.

Only verified constant source hashes/status, fixed preflight stage/reason/numeric
exit status, validated timing summaries and fixed cleanup status reach diagnostic
logs. Raw ETL, native/test output, provenance errors and all private evidence stay
in the same owned runner-temp directory. No XML is produced. Finally and the
independent always-run cleanup preserve the original finite ownership checks.

## Narrow verification

The new `scripts/diagnostics/windows_pinned_baseline_fixtures.py` is explicitly
invoked and outside default pytest collection. It checks one-shot gates and
separate working directories, runs the exact embedded provenance code against
isolated local checkouts, and rejects wrong pins, dirty source/fixtures, unexpected
import environment and the tools directory being used as execution. It never runs
the controller, test workloads or native tracing. Existing 39 offline fixtures
remain the behavioral coverage for the unchanged controller/reducer.

Preparation checks on the existing cloud Python 3.10.21 environment passed:
39 original corrected offline fixtures (0.15 seconds), nine new source/isolation
gates (1.53 seconds), Ruff on the new gate file and whitespace validation. The
positive provenance gate executed the exact workflow Python against clean
`ecf8bcf` and `0c1ac2f` sibling checkouts and verified actual imported origins.
No controller, Runtime workload or native tracing was executed for these checks.
PowerShell/MSVC/WPR execution of the new workflow itself has not run locally.

Because production, existing tests, controller, reader and profile are unchanged,
preparation uses these narrow checks and existing offline fixtures, without a new
full cloud CI run. Actual Windows capture/profile/allocation/schema behavior stays
unverified until the one approved attempt; a failure is an invalid/incomplete
diagnostic, never permission to broaden the scope or run another capture.
