# Missing Candidate Resource Preflight

Date: 2026-10-23. Source baseline and final checked HEAD:
`e8c24fcfd7ebf964f0f506a7d73a42405069dff9`, plus the relevant uncommitted changes.

## Observed Failure

When replacing the existing online project with a resource-bearing local project,
preflight reported Windows FileNotFoundError for the candidate's managed
`online-projects/<project-id-hash>` directory. The directory was not yet created.

Read-only inspection confirmed that the reported hash matched the candidate
project ID, the online project was different, and the candidate had no resource
transfers. The local model and two sample images were present and valid.

`prepareRuntimeProject(..., allowMissingRoot=True)` permits the future managed
root. With no uploaded resource override, `verifyResources` calls `_inside`,
whose strict root resolution raised FileNotFoundError before producing a located
resource diagnostic. This is a missing-resource preflight path, not a camera,
While, or page-layout failure.

## Changes

- Convert missing-root and missing-file errors to `E_RESOURCE_MISSING` at each
  declared resource path. Collect all missing resources, excluding available
  upload mappings. Do not create a placeholder directory or accept missing files.
- Show the resource filename and an explicit upload/revalidate instruction.
- Route the diagnostic action to the existing, confirmed candidate-resource
  upload workflow without closing the comparison or discarding the draft.
- Preserve the distinct content-digest diagnostic and initial-publication return
  path; keep a legacy null resource plan readable for diagnostic compatibility.
- Document upload, validation, and explicit publication as separate operations.

File size/digest verification, path/link restrictions, publication confirmation,
and the rule that upload/validation does not start a Job remain unchanged.

## Initial Fix Evidence

- Reproduced the missing-root FileNotFoundError with a new schema 2.7 fixture.
- The first integration fixture omitted the explicit replacement policy; after
  correction it also used the initial-publication response shape for replacement
  validation. Both fixture errors were corrected to the existing RPC contracts,
  not treated as product regressions or passing checks.
- Initial affected regression: **114 passed in 55.33s** across 10 test files.
- Covered embedded and loopback gRPC: missing resource, upload, revalidation,
  explicit replacement, unchanged source files, and no implicit Job/device start.
- Native Qt offscreen checks exercised the diagnostic action, existing upload
  confirmation, retained comparison/draft, and successful revalidation.
- The actual face project passed read-only preparation/compilation with its three
  resource paths supplied; a missing mapping located `resources.items.face_model.path`.
  No managed root was created and no camera operator was executed.
- Focused Ruff and `git diff --check` passed.

## Clearer Message Follow-Up

The initial wording, "resource not ready", still did not explain the user's next
step. The follow-up replaces it with a count and relative filenames. The Chinese
primary message has this English equivalent:

```text
Runtime is missing 3 project files; validation cannot complete:
- models/yolov8n-face-detect.onnx
- samples/astronaut.png
- samples/empty.png

Click "Upload Candidate Resources" to transfer local files to Runtime,
then click "Validate Selected Changes".
This operation did not write Runtime, connect devices, or start tasks;
the draft and local project files remain unchanged.
```

- Missing file (`E_RESOURCE_MISSING`), size mismatch (`E_RESOURCE_SIZE`), digest
  mismatch (`E_RESOURCE_DIGEST`), and invalid/non-file input (`E_RESOURCE_INPUT`)
  remain distinct. The core validator does not assume every missing file is an
  upload failure; the UI applies the Runtime or local-upload-validation context.
- Local upload validation retains structured file diagnostics through
  `RuntimeClientError`. A missing or changed local file is named as a local
  problem, without offering a misleading immediate re-upload action.
- Popups show up to five issues and explicitly count any remainder. The existing
  problem selector and expanded details retain every issue, including resource
  IDs containing dots. Raw paths, codes, and traceback stay in expanded details.
- The detail button now uses Chinese labels for "Details" / "Collapse Details".
  Its text update follows Qt's internal detail-toggle handler, which otherwise
  resets the label to English during click dispatch.
- New fixtures use `WorkflowStore().toPayload()` schema 2.7, validate with
  `ProjectDocument`, save and read back, and compile in isolated Runtime tests.
- Focused source/RPC/Qt checks initially passed **25 tests**. Expanded affected
  regression before the final button localization passed **187 tests in 79.82s**.
- Final affected regression, including the localization: **187 passed, 2 skipped
  in 81.39s** across 11 files. The two platform-specific render tests were skipped
  only in this offscreen run and separately passed on Windows as recorded below.
- Final focused Ruff, mypy for the three affected source files, and
  `git diff --check` passed. HEAD remained
  `e8c24fcfd7ebf964f0f506a7d73a42405069dff9`; no commit was created.

### Rendering Evidence and Retained Failures

- `QMessageBox.show()` crashed with an access violation on this Windows
  `offscreen` Qt plugin. A standalone Qt-only example reproduced it; `minimal`
  also failed. This is recorded as unsupported rendering, not a product pass.
- Rendering an unshown message box with `grab()` produced blank text and failed
  geometry assertions. That attempt did not exercise Qt's native show/layout
  lifecycle and is not accepted as rendering evidence.
- Hidden Windows-platform widgets (`QT_QPA_PLATFORM=windows`,
  `Qt.WA_DontShowOnScreen`) exercise the real message-box show/layout lifecycle
  without showing or controlling the user's running window. These rendering
  tests are explicitly skipped in the offscreen suite and run separately.
- The first localization checks caught Qt restoring "Hide Details..." after
  expansion. Changing the signal update to queued delivery fixed that ordering;
  expand/collapse assertions were retained.
- Final hidden Windows Qt checks passed **2 tests at 100%** and **2 tests at 150%**.
  Both three-file and seven-file messages passed text bounds and button-width
  checks. Expanded details retained all diagnostic paths. Screenshots were
  inspected, including the comparison window and expanded details.
- Evidence directories, relative to the repository: initial failed attempts at
  `build/resource-preflight-message-100-20261023` and
  `build/resource-preflight-message-100-render-20261023`; successful final renders
  at `build/resource-preflight-message-windows-100-confirmed-20261023` and
  `build/resource-preflight-message-windows-150-confirmed-20261023`. Earlier
  localization attempts remain in the neighboring `*-final-*` / `*-verified-*`
  directories; they were not overwritten.

## Boundaries

No live Runtime upload/publication, project credential edit, device connection,
data-directory repair, process restart, build, commit, or push was performed.
The delivered `.emoproj` remained byte-identical in the initial check and was not
edited in the message follow-up. This is source, isolated RPC, controlled
offscreen Qt, and hidden Windows-widget evidence, not manual desktop or packaged
EXE acceptance.
Existing processes need reopening to load the improved diagnostics. The existing
upload-candidate-resources action already provides the recovery path without
manually creating hash directories or deleting Runtime data.
