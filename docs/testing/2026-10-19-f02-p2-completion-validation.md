# F02 P2 Upload Completion Validation

## Scope And Source

Baseline: `a556a1d0a52a684153ebdfc500dd3dc90b4ef84d`.
Implementation: the focused commit containing this report, the two Designer
files, the two targeted test modules and the imported completion plan.
The final handoff response records its full SHA; this document does not claim
that a moving branch is a fixed implementation version.

All four entries in the supplied ZIP's `FILES_SHA256.json` were verified.
The Markdown plan is tracked at
`docs/plans/2026-10-19-f02-p2-upload-completion-plan.md`.
The attachment itself was documentation, not a product patch or test evidence.
Root AGENTS, F01 deferral, Runtime transactions/ownership, schema/protobuf,
N01/N02 and PLC fixtures are unchanged.

## Changes

- Register the existing snapshot/resource/transfer identity before sending BEGIN.
  Unknown BEGIN responses remain queryable; no automatic BEGIN or COMMIT replay.
- Drop registration only for the two proven pre-insertion BEGIN rejection codes:
  `E_CONFIG_INSTANCE` and `E_RESOURCE_TRANSFER_CAPACITY`. UNKNOWN or a generic
  storage/transport failure is not proof that the upload never existed.
- Pre-COMMIT failures after an acknowledged BEGIN make at most one existing CANCEL
  attempt. Confirmed states use the shared classifier; an unconfirmed cleanup
  retains the original identity and reports both upload and cleanup errors.
- One outer batch exception boundary attaches confirmed earlier results and the
  current resource/stage/error to `RuntimeClientError.resourceResult`. Existing
  client errors retain their code/message/cause; ordinary errors are wrapped with
  exception chaining. Preflight/LOOKUP failures create no fake pending upload.
- Preserve the four result categories; a local `aborted` marker lets the existing
  dialog explain that the batch stopped and unstarted items were not uploaded.
  Error details now show the resource, stage and original request. No new RPC,
  background recovery, dependency or upload management UI was added.

## Baseline Failures

Evidence root: `build/f02-p2-completion/` (local and ignored).

- Initial new tests: `before.xml`, with text at `build/f02-p2-before.log`:
  **15 failed, 24 deselected**. These include expectations for the new error
  envelope; they are not 15 independent product defects.
- Direct repro rerun before product edits: `direct-before.xml` / `.log`:
  **9 failed, 30 deselected**. The two repro families capture the original
  exception before checking the required new exception type, exposing the actual
  missing identity/results rather than stopping first at a type mismatch.
- The 600 KiB case proves the Runtime accepted 512 KiB and remains PENDING while
  the baseline client's pending dictionary is empty. The batch cases prove a real
  first COMMIT occurred before the second failure lost its structured results.

No helper pre-populates pending in these new repros. Existing pending fixtures
remain only in the older classification/query regression tests.

## Executed Verification

Environment: Windows, Python 3.10, PySide2; `QT_QPA_PLATFORM=windows`.
Interpreter: the existing `emo_master` Conda environment.

Final command:

```powershell
$env:QT_QPA_PLATFORM='windows'
python -m pytest -q tests/runtime/test_targeted_resource_repair.py tests/runtime/test_project_resource_transfer.py tests/designer/test_targeted_resource_repair_ui.py tests/designer/test_project_configuration_ui.py tests/designer/test_online_run.py --junitxml=build/f02-p2-completion/verified.xml
```

Final result: **65 passed in 39.93 seconds, zero failures/skips**, recorded in
`verified.xml` / `verified.log`. Earlier overlapping
runs are retained: client **48 passed**, first five-module run **65 passed**, and
two isolated native UI runs **1 passed, 4 deselected** each. Do not sum these
counts or reuse the earlier delivery's 60/285 tests as evidence for this patch.

| Target | Result | Evidence |
| --- | --- | --- |
| C-01 | PASS | Real helper, 600 KiB upload, second chunk and cleanup share one simulated connection loss; original pending identity survives; original STATUS/CANCEL retire it; next explicit upload succeeds |
| C-02 | PASS | Accepted BEGIN/lost response; actual Runtime instance and capacity refusals; LOOKUP failure; UNKNOWN after acceptance stays pending; local preflight has no fake pending |
| C-03 | PASS | Real first COMMIT then second LOOKUP/BEGIN/CHUNK or between-file cancellation failure; earlier bytes remain intact, errors include resource/stage, confirmed/unconfirmed cleanup classified; third never begins; explicit retry reuses first without sending its bytes |
| C-04 | PASS | Existing terminal/mixed query, COMMIT and CANCEL response-loss, online-reference rejection and stale-instance tests rerun |
| C-05 | PASS | Actual Qt upload-button signal sends new bytes; error counts/details and original query/cancel work; subsequent click uploads remaining resources; dirty draft, nonempty undo/redo, disk and online snapshot unchanged |
| C-06 | PASS | Existing normal loopback gRPC upload/reuse followed by explicit configuration write and restart; new multi-resource retries also complete without changing online config or starting jobs |

The new interruption cases wrap calls into the real embedded Runtime; they do not
simulate faults by modifying resource files or pre-filling client state. Existing
loopback gRPC tests remain a separate transport layer. The UI test uses actual
Qt signals/client/Runtime, with only confirmation answers and response failure
controlled. It processes layout events before checking status label height and
capturing `ui-partial-failure.png`; the screenshot was visually inspected for the
full error and batch-stop text. This is automation, not human UI acceptance.

Ruff passes for both source and test files. Scoped mypy passes for both source
files under the repository configuration, which does not check all untyped
function bodies. `git diff --check` is checked before the focused commit.

## Acceptance Boundaries

The two client P2 paths are addressed at the tested source/native-Qt layer.
F01 remains DEFERRED_NON_BLOCKING, not fixed. No private project/model/calibration
files, hardware endpoints, PLC fixtures or historical evidence were changed.

NOT RUN: full repository suite, new frozen build/selftest, clean-machine install,
human UI acceptance and camera/PLC/production acceptance. No real device was
connected. Existing EXEs do not hot-load this patch; source users must restart
Designer, and EXE validation requires a build from the new source. This patch
does not create additional hardware acceptance prerequisites or claim production
readiness. No remote publication is implied by the local verification record.
