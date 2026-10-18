# R2.1 Targeted Repair Evidence

Baseline: `d75292d00b070e8d264bc0a333335f626175629c`. Windows, Python 3.10,
PySide2. Source, native Qt, frozen and physical acceptance remain separate.
Local logs live under `build/targeted-repair-d75292d/` and are not public evidence.
The plan and 30 coverage targets are in `docs/plans/2026-10-18-r2_1-targeted-repair-*.md`.

## N01 Checkpoint

The coordinator distinguishes owned roots, owned detection iterations and borrowed
scopes. Retained deliveries cannot refresh configuration. Borrowed scopes keep the
source cycle/frame identity and never issue endCycle. Outer scopes restore their
actual document and compiled project, not just a revision label.

- `n01-before.xml`: four failures, including real spawned consumers computing 99
  for R1 inputs whose expected value is 1 (original/derived images and no-Join).
- `n01-first.xml`: 38 passed across targeted inheritance, loop configuration,
  Dispatch retirement, frame dispatch and device-cycle source regressions.
- `n01-rpc.xml`: five passed after adding real gRPC writes and a persistent Runner
  R2/R1/R2 installation/exception-restoration check. Counts overlap, do not sum.
- The existing drain test now checks that nested scopes borrow their outer cycle;
  it no longer incorrectly ends that cycle twice. Drain/300-Join tests still run.

Commands: `python -m pytest -q tests/runtime/test_targeted_cycle_inheritance.py`
and the affected `test_loop_cycle_configuration.py`, `test_review_dispatch_lifecycle.py`,
`test_frame_dispatch.py`, `test_review_cycle_sources.py`. Ruff and diff checks pass.
No physical camera, PLC or network reconfiguration was performed.

## N02 Checkpoint

Workflow import no longer shadows its target workflow with a Dispatch dictionary.
Import-closure slot mappings preserve legitimate sharing while avoiding existing
slots. Join ports, edges and typed SQLite writer sources follow the mapping.

- `n02-before.xml`: three failures; both public entrypoints reproduce the original
  AttributeError, and the new typed-port test requires the mapping interface.
- `n02-first.xml` / `n02-final.xml`: preserved fixture failures (Join output ports
  were absent from the editable fixture, so boundary normalization removed edges).
- `n02-recheck.xml`: 25 passed after supplying actual editor port metadata.
- `n02-native.xml`: 26 passed, including combined original/copy spawn execution,
  legacy package compatibility, resource guards, actual native import cancellation,
  confirmation, undo/redo, save/reopen with Dispatch and resource dependencies.

Command: `python -m pytest -q tests/designer/test_targeted_dispatch_package.py
tests/designer/test_workflow_package.py tests/designer/test_workflow_package_resources.py`
with `QT_QPA_PLATFORM=windows`. Scoped Ruff/mypy pass. Counts overlap.

## N03 Checkpoint

LOOKUP distinguishes CORRUPT from missing uploads and preserves other errors.
Explicit same-manifest repair creates another managed object, verifies bytes,
then switches the unique SQLite authority. Concrete pins protect old objects;
unpin/restart reclaims obsolete owned files, not unknown files. Legacy duplicate
selection validates bytes and uses transferId ordering rather than row order.

The new RetryProjectResourceRecovery RPC shares configuration single-writer
admission and result queries. It prepares the committed configuration without a
revision change, variable reset, device activation, listener start or job start.
Quarantined/active/closed device executors and unrelated publication errors remain
blocked. Designer separates byte repair from the explicit readiness action.

- `n03-before.xml`: three failures reproducing the unreachable normal repair path.
- `n03-regression.xml`: 38 passed; resource transfer, preparation, lifecycle and
  project-replacement regressions. `n03-native.xml`: 17 passed, including real gRPC
  and native Windows Qt confirmation/cancel/recovery signals.
- `n03-edge.xml`: 15 passed, including deterministic legacy migration, permission
  error preservation, concurrent repairs, transaction failure, unknown COMMIT
  reconciliation, stale instance/revision, cancellation and unchanged variables.
- `source-final.xml`: 201 passed, no skips, across 27 related test modules with
  native Windows Qt. This is a focused suite, not the repository's entire suite.
- `n01-persistent.xml`: one actual persistent spawned consumer processes queued
  deliveries reordered by the fixture to R2/R1/R2, with real results 99/1/99.
- `n01-depth.xml`: 5 passed, one failed test expectation (3x3 blur instead of the
  declared default 5x5); preserved. `n01-depth-final.xml`: 7 passed after matching
  that default, checking actual original/derived image bytes and three Dispatch
  levels across R2/R3 publications. Counts overlap; do not sum them.
- Source Ruff passes; mypy passes for 493 files with its existing untyped-body
  exclusions. Generated protocol files are included and diff checks pass.

The packaged selftests now contain synthetic retained-loop, package-slot and
same-manifest recovery probes. The Designer source selftest passed after correcting
the first fixture's omitted schema sections (`source-selftest-final.json`).

Recovery result records are bounded and instance-bound, not durable perpetual
idempotency. Unknown replies never authorize another repair; query the original
transfer/request, and recompare after an instance change. This is not a new GC.

## Coverage Matrix Results

PASS refers to the concrete automated checks named below, including layered
checks where stated. It is not an assertion that every timing permutation,
hardware driver or UI gesture has been exhaustively tested. The original R2.1
acceptance checklist remains independent. Test module names are under `tests/`.

| Target | Result | Current evidence |
| --- | --- | --- |
| N01-T01 | PASS | `runtime/test_targeted_cycle_inheritance.py`: real gRPC publication and spawned While output 1, not 99 |
| N01-T02 | PASS | Same suite: original shared-memory bytes, frame/cycle/plan identities through redispatch |
| N01-T03 | PASS | Same suite: derived frame differs, bytes match declared blur, default coordinate frame matches source dimensions, final leases retire |
| N01-T04 | PASS, layered | Nested lease test plus `test_loop_cycle_configuration.py` real spawn next-iteration switch |
| N01-T05 | PASS | Three Dispatch levels retain R1 across R2/R3 publications |
| N01-T06 | PASS | `test_targeted_persistent_roundtrip.py`: one real worker; fixture explicitly delays/reorders queued arrivals; values 99/1/99 |
| N01-T07 | PASS, layered | Root/consumer cases in targeted, loop and `test_frame_dispatch.py` suites |
| N01-T08 | PASS | Source-ended/no-Join targeted spawn; retained queues and eventual cleanup |
| N01-T09 | PASS, layered | Real Runner exception restoration, cancelled admission, worker stop/retirement tests |
| N01-T10 | PASS | `test_review_dispatch_lifecycle.py`: queued no-Join nested work during drain and drain cancellation |
| N01-T11 | PASS, layered | Held consumer does not block publication; new detection leases refresh while old deliveries remain retained |
| N01-T12 | PASS, layered | `test_review_cycle_sources.py`, global-variable suites: overrides, variable binding, inheritance and live state; simulated drivers only |
| N01-T13 | PASS | 300 sequential Joins with two persistent workers and bounded receipt/expired-request checks |
| N01-T14 | PASS, layered | Compiled/document/revision restoration and `test_online_graph.py` historical document isolation |
| N02-T01 | PASS | `designer/test_targeted_dispatch_package.py` copy and portable import, resource package tests |
| N02-T02 | PASS | Combined original/copy Compiler/groupSlots and spawned group, Join edge and typed writer source mapping |
| N02-T03 | PASS, layered | Resource/device closure suites; external new-device import is editable/disabled, simulated site field then filled and enabled before real run |
| N02-T04 | PASS | `test_workflow_package_resources.py`: malformed hash/dependency/path/budget and install failure rollback |
| N02-T05 | PASS | Native Qt import signals, cancel/confirm/undo/redo/save/reopen with Dispatch and resource bytes |
| N02-T06 | PASS | Legacy package suites and in-project device/resource reuse |
| N03-T01 | PASS | Normal helper and native Qt explicit CORRUPT repair with identical manifest |
| N03-T02 | PASS | Missing/corrupt restart cases; original source directory moved away, user-supplied original uploaded from another directory |
| N03-T03 | PASS, layered | Concrete pin plus concurrent CANCEL/repair; existing materialized image unchanged; active execution leases covered separately |
| N03-T04 | PASS | Chunk/digest/transaction failures; lost reply and unavailable STATUS retain original identity; read-only reconciliation |
| N03-T05 | PASS | Repeated repair, unpin/restart reclamation, quota tests and unknown-file preservation |
| N03-T06 | PASS | Explicit same-configuration recovery, unchanged document/execution revision and variable rows, no jobs/connections |
| N03-T07 | PASS | Barrier-controlled competing repairs, deterministic legacy duplicates, revision/instance fences |
| REG-T01 | PASS | Project replacement, cycle sources, metadata, drain, resource pins and bounded receipt rotation suites |
| REG-T02 | PASS | Online run, history, variable migration, page publication/delivery and native draft/undo preservation |
| REG-T03 | PASS | Scoped source shutdown tests and both fresh frozen executables, no remaining package processes |

Additional runs: `supplement-final.xml` has 19 passes for new-device import,
source-directory removal and pin/CANCEL concurrency; `n01-coordinate-final.xml`
has six passes including actual blur coordinate output; `cross-final.xml` has
82 passes for pages, migration and dynamic variables. Counts overlap.

## Frozen Verification

Completed 2026-10-19 (Asia/Shanghai), using Windows/Python 3.10.21/PySide2 and
PyInstaller 6.22.2. Both builds used new work/dist directories, with a restricted
system PATH and no PYTHONPATH/PYTHONHOME during executable verification.

Build-source commit: `ceb033a98e7962ccdc697480e9efa24bf0e96ef3`.
Supplemental test commit: `747fd025db6b9379c5580ff3a8ee149e3364022d`.
The source/protocol trees are identical between these commits:

- `src`: `0024b7887d833a142b1c9b522c01ee430b98c9ef`
- `proto`: `c52d1979150c1577dc37819c8299d0a510cb0b59`

| Distribution | Result | Complete directory | EXE SHA-256 |
| --- | --- | --- | --- |
| Designer | PASS, exit 0 | `build/targeted-repair-d75292d/frozen-designer/dist/EmoMaster` (687 files, 327469537 bytes) | `7a1f1f076cc4580e6beb07d4f6ea46cd9bc49315236a7189ea26ef116da557ec` |
| Independent Runtime | PASS, exit 0 | `build/targeted-repair-d75292d/frozen-runtime/dist/EmoMasterRuntime` (685 files, 326618661 bytes) | `aa86f0dfa5fd44aa974fb661b96d050a0b65329deca61210d9931bc3192864ec` |

Designer command: `EmoMaster.exe --self-test --managed-runtime --gui-icons
--result-json <report>`. Independent Runtime: `scripts/verify_runtime_package.ps1
-Executable <new-exe> -ReportPath <report> -DurationSeconds 60 -Restarts 10
-QtPlatform windows -TimeoutSeconds 300` with hardware smoke disabled.

Both `targetedRepairs` checks pass: old input computes 1 after publishing 99,
original frames retire, same-manifest bytes are repaired, and the same revision
recovers without hardware access. Designer also checks copied package slots.
Independent Runtime reports `frozen=true`, `designerModules=[]`, 11 retired worker
sessions (first: 1122 cycles; later: 4-5), normal cancellation and closed Runtime.
The native Runtime screenshot was inspected: visible controls and synthetic
image rendered. A subsequent executable-path process check found zero remnants.
Complete distribution and source SHA-256 inventories are retained in the local
evidence directory. Do not distribute the EXE without its `_internal` directory.

## Delivery Boundary

`isolated-delivery.xml`: **285 passed, zero failures/errors/skips**, 148.65 seconds,
32 selected modules, Windows native Qt. This supersedes the earlier 284-pass
isolated run with the final new-device/site-fill and resource-boundary checks.
Public machine-readable counts, testcase results, artifact hashes and frozen
checks are in `2026-10-18-r2_1-targeted-repair-evidence.json` beside this report.

The isolated Git export contains no untracked private project inputs. Its 1192
tracked source/protocol/test/script files match commit `747fd025` after Git's
configured CRLF normalization; protocol regeneration check passes there too.
The initial raw-byte comparison did not normalize Windows archive line endings
and therefore differed from Git blobs; the normalized comparison has zero
mismatches. Both comparison records are retained, not silently discarded.

The 45-file project/field-test SHA-256 baseline is unchanged. The unrelated dirty
`field_tests/plc_dual_mc/build_project.py` remains uncommitted. A scoped scan of
the candidate found no configured private-path/address/credential patterns; this
is not a general secret audit. No real camera/PLC was connected, no network
address was modified, and no private model/calibration/project was published.

NOT RUN: full repository suite, manual owner UI acceptance, clean-machine
acceptance, real equipment/PLC/production-line validation, installer or Release
publication. Native automated Qt, loopback gRPC, injected transport failures and
frozen selftests are software evidence only. No permanent-idempotency, exhaustive
scheduling, or physical safety acceptance claim is made.
