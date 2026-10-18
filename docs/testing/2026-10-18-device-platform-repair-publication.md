# Review Repair Publication Checkpoint

Date: 2026-10-18. Repository: `https://github.com/jsdfhasuh/emo_master`.
Branch: `agent/runtime-workflow-architecture-v1`.
Repair comparison base: `c097c39934955ababc679f2fb704b50dba488030`.
The Git commit containing this document identifies the delivered repair snapshot.
The original feature comparison base is
`63070277133b89d61a5d27dde6d5f95494b4dce0`; use it only for a broader feature audit.

## Scope

The delivery includes R01-R10 repairs, workflow-package resource/site dependency
handling, related regression tests, and updated documentation. See
[the repair report](2026-10-18-device-platform-independent-review-repairs.md)
for finding-by-finding implementation details and earlier overlapping checks.
Local frozen build evidence and pending manual checks are documented in
[the frozen handoff](2026-10-18-device-platform-review-frozen-handoff.md).
Its pre-commit/dirty-tree statements describe build time, not delivery status.

Only explicitly selected source, protocol, tests and documentation are staged.
The unrelated `field_tests/plc_dual_mc/build_project.py` modification is excluded.
Local projects, models, coordinates, calibration data, private tests, build
artifacts and raw local evidence are not added by this delivery. A before/after
SHA-256 check covered 4320 existing protected files with no changes. A targeted
credential/private-reference pattern scan of candidate source/tests/protocol
found no matches; it is not a universal secret-detection guarantee.

## Independent Staged-Tree Validation

Tests ran in a fresh `git checkout-index` export, using Python 3.10.21 and native
Windows Qt, not the mixed development working directory. Test input is exactly
the changed Python test files under `tests/` in this repair delta (17 files).

- Final scoped regression: **149 passed** in 85.38 seconds.
- Ruff: source and all changed tests passed, including the final test correction.
- Mypy: 491 source files passed under repository defaults; untyped bodies are not
  all checked.
- `scripts/gen_proto.py --check`: passed.
- `git diff --cached --check`: passed.
- Product inputs still match the earlier 672-file frozen source manifest.
  Publication adds a deterministic test clock and documentation, not a new
  product-code change after those frozen builds.

The first candidate run was **148 passed, 1 failed**. The portable workflow test
compared two `WorkflowStore.toPayload()` results across a wall-clock second;
serialization intentionally refreshes `project.updatedAt`. A diagnostic rerun
passed 79 overlapping tests. The affected module now fixes only the serialization
clock, retaining full-payload equality checks instead of dropping comparisons.
The final 149-test run above includes that correction. Original failure logs are
preserved locally and not relabeled as passing.

Local evidence directory:
`build/independent-review-c097c399/publication-20261018/`.
It contains `candidate-regression.xml`, `candidate-ui-diagnosis.xml`,
`candidate-regression-final.xml`, associated logs, static-check logs and protected
file hashes. These ignored local files are **not available to a GitHub-only
reviewer**. Treat the counts above as recorded developer results, not independent
verification; run the committed tests or explicitly report inability to run them.

## Review And Acceptance Boundary

Review the exact commit rather than a moving branch tip. Check the original
finding's failure path and realistic counterexamples, not just its new happy-path
test. The original review's superseded low-level R04 probe was skipped in earlier
runs; real-spawn replacement coverage is committed. A skipped probe is not a pass.

No full-repository regression, independent reviewer approval, human interactive
acceptance, clean-machine installation, real SDK/camera/PLC test, deployment or
Release is established by this publication check. Simulated/native automated
tests and local frozen self-tests remain distinct from those acceptance gates.
