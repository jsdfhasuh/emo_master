# R2.1 Normal Upload Follow-Up Validation

Date: 2026-10-19, Asia/Shanghai. Scope: F02 only.

## Baseline And Rules

The checkout was fast-forwarded from `7cc989caefb1626b0bdb4ea3d7e647cbf648b638`
to `51ccf6995f1ee2b47fd8cb6904431d0578a468ed`. Those two upstream commits only
add the root `AGENTS.md` and the normal-operation follow-up plan. No existing
root AGENTS file was present in the checkout; the already-reviewed upstream
rules were adopted without creating a duplicate document or overriding local
rules. The unrelated field-test script was preserved.

The implementation is the focused commit containing this report, on top of that
baseline. F01 remains DEFERRED_NON_BLOCKING as scoped in the approved follow-up
plan; it was not repaired or promoted to an acceptance prerequisite. No changes
were made to Runtime resource ownership, schema, protobuf, Dispatch/Join or cycle
selection. Historical N01-N03 evidence remains intact.

## Changes

- One client classifier handles COMMIT outcomes and later STATUS/CANCEL results.
  READY and CANCELLED retire only their own pending identity; PENDING and errors
  retain it. Cancellation is not counted as success or proof that COMMIT never
  happened. Unsupported states report their actual state rather than success.
- Manual queries process each saved request independently. A disconnected request
  does not discard confirmed successes/cancellations from another request.
- Explicit cancellation reuses the saved project/instance/transfer identity and
  the existing CANCEL RPC. Selected requests only are cancelled; online-reference
  and instance guards still reject protected operations.
- A failed COMMIT followed by PENDING no longer silently deletes pending and
  issues CANCEL. It returns a pending result and awaits the user's decision.
  Unavailable STATUS retains the original identity and error; completed items
  accompany that error for UI reporting. No BEGIN/COMMIT replay was added.
- The native configuration dialog has a separate Cancel Upload action, distinct
  from cancelling a configuration write. It shows completed/cancelled/remaining
  counts, original request IDs and actual errors. Confirmed terminal results
  restore upload availability; recovery still requires its original conditions.

## Executed Evidence

Environment: Windows, Python 3.10, PySide2, `QT_QPA_PLATFORM=windows`.
Interpreter: the existing `emo_master` Conda environment.
Evidence directory: `build/normal-operation-followup-51ccf69/` (local, ignored).

| Run | Result | Scope |
| --- | --- | --- |
| `before.xml` / `before.log` | 9 failed, 15 deselected | New F02 cases before implementation; includes the old CANCELLED/PENDING behavior and missing new API/result fields, not nine independent product defects |
| `client-first.xml` | 24 passed | Targeted resource repair and new normal-operation client cases |
| `focused.xml` | 44 passed | Client, transfer and native configuration UI suites |
| `final.xml` / `final.log` | 49 passed, zero failures/skips | Final five-module suite below, including online-run protection |

Final command:

```text
python -m pytest -q
  tests/runtime/test_targeted_resource_repair.py
  tests/runtime/test_project_resource_transfer.py
  tests/designer/test_targeted_resource_repair_ui.py
  tests/designer/test_project_configuration_ui.py
  tests/designer/test_online_run.py
  --junitxml=build/normal-operation-followup-51ccf69/final.xml
```

Ruff passes for both changed source files and both changed test files. Scoped
mypy passes for the two changed source files using the repository's existing
configuration (not strict checking of every untyped function). `git diff --check`
passes. Counts above overlap and must not be summed.

## OP Coverage

| Target | Result | Evidence |
| --- | --- | --- |
| OP-01 | PASS | Normal READY query, repeated query without duplicate completion, existing normal upload/reuse integration |
| OP-02 | PASS | CANCELLED retires its identity; a subsequent explicit upload can BEGIN/CHUNK/COMMIT successfully; native query restores upload button |
| OP-03 | PASS | PENDING remains pending; explicit selected CANCEL uses saved identity; subsequent upload works without an automatic COMMIT |
| OP-04 | PASS | Existing successful-COMMIT/lost-reply and read-only reconciliation checks rerun; no extra COMMIT/BEGIN |
| OP-05 | PASS | Unavailable STATUS and lost CANCEL reply preserve identity; native UI keeps query available and confirms CANCELLED on later query |
| OP-06 | PASS | Mixed READY/CANCELLED/PENDING plus a disconnected request returns independent categories and retains only unresolved identities |
| OP-07 | PASS | Native Qt button clicks, No/Yes confirmation, query/cancel/re-upload paths; draft, disk, undo/redo and online snapshot unchanged |
| OP-08 | PASS | Online-reference CANCEL refusal and stale-instance rejection preserve protected resources and pending records; no jobs or revision changes |

The native tests use actual RuntimeClient/RuntimeService and Qt button signals;
fault wrappers model temporary response loss. They are not real network outage
or human acceptance tests. Native screenshots were inspected and button bounds
asserted. No dummy UI callbacks substitute for the cancellation RPC path.

## Preserved Boundaries

The SHA-256 of the pre-existing dirty `field_tests/plc_dual_mc/build_project.py`
matches the pre-change baseline. It is not part of this repair commit. Tests use
temporary synthetic resources and simulated devices, not private field projects.

NOT RUN: full repository suite, a new frozen build/selftest, clean-machine or
manual owner UI acceptance, and real camera/PLC/production validation. No network
address or field device was changed. Earlier EXEs do not include this source-only
repair; source users must restart Designer, and EXE deployment requires rebuilding
Designer. No additional performance or F01 fault-combination gate was introduced.
