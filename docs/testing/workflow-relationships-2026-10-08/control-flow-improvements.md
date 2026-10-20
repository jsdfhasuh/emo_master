# Control-flow usability follow-up

Date: 2026-10-08

## Scope

Designer-only presentation and editor behavior for Subflow, Repeat, ForEach,
While, If, and Switch. Runtime implementations, persisted IDs, port keys,
operator manifests, and project format are unchanged. Existing unrelated
working-tree changes were preserved. No commit, push, package, or deployment
was performed for this follow-up.

## Changes

- Hide condition workflow fields for Repeat/ForEach and repeat count for
  While/ForEach. Keep previously supplied inactive values without creating
  new hidden references or defaults on apply.
- Show Chinese loop/If mode labels while retaining the original enum values.
  Disable If comparison input in bool mode without discarding its value.
- Derive ForEach port choices from the draft body selection. Preserve invalid
  old mappings visibly and reject apply until corrected; do not auto-select a
  different port. Keep body workflows with no input ports supported.
- Refresh workflow names/interfaces in reused/open control-flow editors while
  preserving draft values and dirty state. Defer refresh past navigation events.
- Show call targets, repeat count, ForEach mapping, If condition, and Switch
  match values on canvas nodes. Double-click linked workflow names to navigate.
- Show Switch duplicate-match priority without changing first-match execution.
- Refresh canvas descriptions while preserving positions, selection, runtime
  state, port identities, and edge endpoints. Keep legacy view-model adapters.
- Normalize Qt line separators in relationship-tree text layout so loaded
  Windows fonts do not cause inconsistent row-height measurements.

## Verification

- PASS: complete Designer suite, offscreen: **780 passed in 86.18s**.
- PASS: Windows native Qt control-flow/name/relationship UI tests plus Runtime
  loop contracts, image-batch While, and If/Switch plugin tests:
  **61 passed in 9.58s**.
- PASS: Windows native Qt at 150% scale, control-flow/name/relationship tests:
  **30 passed in 15.12s**.
- PASS: Ruff for all eight touched implementation modules and the new test file.
- PASS: mypy for all eight touched implementation modules.
- PASS: scoped git diff --check.
- NOT_RUN: full repository suite outside the listed coverage, packaged/frozen
  application, hardware/field acceptance.

The first expanded Designer run had five failures (legacy model adaptation,
editor signal compatibility, and three font/layout cases) and one PLC async
teardown timeout. The compatibility/layout failures were corrected. The PLC
case passed on isolated rerun and in the subsequent complete suite; PLC code
was not changed and this does not claim to eliminate all timing variability.

## Screenshots

- `control-flow-canvas.png`: all six control-flow node types, rendered using
  the actual Windows Qt scene with synthetic test workflows. Not a capture of
  the user's currently running application and not a mockup.
- `while-relevant-parameters.png`: actual While editor with the irrelevant
  repeat-count field hidden.

The tests verify node child geometry and label non-overlap, live rename with
an unapplied draft, ForEach invalid-port prevention, empty-input compatibility,
canvas navigation, graph preservation, and stable edge endpoints after refresh.
