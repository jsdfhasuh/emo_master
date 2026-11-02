# UI branch consolidation into main

Date: 2026-11-02. The user requested consolidation of the UI design branches into
`main` and explicitly included the uncommitted Designer and Web changes.

## Source inventory

- Starting main: `f0675253631f489998056e5af618378e115efc83`.
- Starting reviewed integration: `050e9d84808364c9d49ca01ca6dbbacd22b0a9ec`, PR #7.
- Designer committed tip: `8df8599fc026c424a49e87cc53fb45f6b371f16f`.
- Web committed tip: `b89826b0841c6b4ef7f9c5f11ac938c6008934c7`.
- Both tips were merged with their history intact. Their current uncommitted
  changes were captured separately and integrated in
  `c2be5767cd796ff26821638b1863488ebaa115b8`.
- [Source snapshots](source-snapshots.json) record the original file hashes:
  101 Designer paths and 47 Web paths, including 27 shared paths. Three shared
  documents differed; the newer Designer parent navigation, whole-page fit and
  resize instructions were retained along with the common expansion contract.
- `codex/panel-protocol-ungrouped-fix` is already an ancestor of the Designer
  tip. Flow-edge work is already an ancestor of main. Camera preview was
  integrated by later main commits. The superseded Web prototype and diagnostic
  branch are not merged over the current implementations.

The original Designer and Web worktrees were not reset, cleaned, or committed.
Their uncommitted changes remain available there. Branches and historical
evidence are retained.

## Integrated behavior

The combined tree includes responsive Designer editing, terminal viewports,
whole-page fitting, floating tools and properties, parent-group navigation,
edge/corner resizing, equal-width-aware manual resizing, shared spacers,
persistent upward canvas expansion, and the opt-in performance probe. Web uses
the matching official SDK 1.3.0 and renders the shared spacer/expansion fields.
Project format remains 2.9 and Presentation remains 1.2.

## Integration checks

Local verification uses Python 3.10/PySide2 with offscreen Qt, isolated Web
dependencies, and headless system Chrome. Software Runtime checks use synthetic
data and loopback; no camera or PLC is accessed. Full source/test Ruff, protobuf
drift and mypy (609 source files) pass. Cross-platform CI is pending on the
updated PR revision; pending work is not counted as passed.

The initial presentation run completed with 540 passes and five failures from
old fixtures/assertions. An explicit grid layout fixes the legacy quota fixture;
image-error checks now assert the exact diagnostic line and source identity in
the richer tooltip. Both affected files then pass all 36 tests. Original failure
logs remain under `build/ui-main-integration/`.

Web verification completed: 148 backend tests passed and two opt-in tests were
skipped; all 63 frontend tests, TypeScript checking and production build passed.
Five Chrome browser suites passed, including 14 spacer/expanded-canvas layout
cases. The four software Runtime tests and the complete browser
Start/result/Stop/ABORTED chain also passed. The independently built Web wheel
installed outside the source checkout and validated SDK 1.3.0, embedded ordinary
Job fixtures, packaged UI and absence of Qt/Runtime imports.

The previous PR CI failure log exposed stale new-project assertions, legacy
fixtures mixing incompatible page versions, uninitialized connection profiles
in tests, and tests assuming the old subscription quota or implicit local source
relocation. These preconditions were updated while preserving the control
deadline, cancellation, resource ownership and explicit source-write assertions.
The affected groups completed 213 and 58 software/Runtime checks, three hardware
fixture package checks, and 12 targeted online/connection checks. The Designer
batch completed 219 passes and one existing skip; its remaining old source-choice
precondition was corrected and passed in the targeted checks. Counts overlap
between groups and are not summed as distinct coverage.

Two startup alias cases fail locally because the host rejects creating a child
directory through a directory symlink with WinError 183. The same failure was
reproduced with standalone Python, without product code. Their assertions remain
enabled for cross-platform CI; they are not reported as local passes.

Integration also fixes two normal UI issues: the old grid editor retains a
64-pixel append row when the whole-page canvas is fitted, with matching candidate
measurement; wrapped configuration errors retain their required height. The
configuration dialog's upgrade text now matches its existing supported upgrade
action. New fixture generation uses the current 2.9/1.2 creation and save path,
with save/reload/compile verification and no rewrite of the historical fixture.

The initial Designer run completed 405 passes and two failures. After the grid
repair, all 54 affected grid/resize/viewport/drop/full-user-path tests pass. The
four Dispatch viewing tests pass with explicit normal-Stop phase assertions for
the existing NOT_EXECUTED or UNKNOWN cancellation outcomes; running failures
remain forbidden. The two older Linux CI drag/layout cases also pass in both
Windows native and offscreen Qt checks. The final local logs are retained under
`build/ui-main-integration/designer/` and `build/ci-*.log`.

An independent read-only review of the test changes found no removed business
assertions or bypassed source ownership/cancellation guarantees.

Real desktop mouse acceptance, physical devices and deployment remain distinct
from the checks above. No release or deployment is part of this merge.

## Native Windows CI retirement follow-up

At `e592f1690cd77073103ce8a7bbcdc801d1be4ed3`, the Windows job
`125307066928` in [CI run 41454334319](https://github.com/jsdfhasuh/emo_master/actions/runs/41454334319)
terminated once with `0xC0000005` at approximately 21% of the suite. This is an
incomplete run, not a pytest assertion failure or a passing suite. The active
test was `testUnsupportedOperatorDoesNotStartWorker`; the immediately preceding
debug execution and close-during-open tests passed. The faulting thread was the
debug RPC executor, with Python reporting `Garbage-collecting` inside JSON
decoding (`contracts._pairs`), while the GUI thread was processing test events.
This identifies a background-GC/native-lifetime investigation direction, not
the collected object or a proven unique root cause. The relevant debug test,
window/service implementation and Designer cleanup fixture were unchanged from
the starting main revision.

Before changing the test, native Windows focused execution of the resource UI
and debug-window files passed all 16 tests with no skips. A separate five-test
sequence used a temporary diagnostic plugin without changing application or
test behavior. It passed and recorded that the close-during-open test returned
with both editor and debug native windows still valid: the editor was waiting
for debug closure and was not disposed, while the debug close ACK had already
marked the debug window disposed. Their native destruction followed during
fixture cleanup on the GUI thread. This establishes incomplete per-test native
retirement; it does not prove that the fixture deleted an unacknowledged owner
or that this caused the CI access violation.

The close-during-open test now retains its accepted-open/stop/resource checks
and original 12-second deadline while waiting for normal queued Qt closure to
destroy both native windows. It reuses the retirement pattern already present
later in the same file. No production behavior, global fixture, GC policy,
assertion or CI skip was weakened. Native Windows verification after this
change passed 35 tests across the preceding bubble/catalog and affected debug
files, plus 14 existing window/scene/runtime Qt ownership and lifecycle tests;
both groups had zero skips and zero failures. A command with a nonexistent
additional test path collected no tests and is retained as a command error,
not included in these passing counts.

The original cloud log and local evidence are retained under `build/`:
`ci-native-debug-cloud-failure.log`, `ci-native-debug-repro.{log,xml}`,
`ci-native-debug-retirement.log`, `ci-native-debug-retired.{log,xml}`,
`ci-native-debug-owners.{log,xml}` and `ci-native-debug-lifecycle.{log,xml}`.
The first cloud failure remains recorded; full CI must validate the subsequent
commit before reporting the consolidation checks as complete.

## Completed Linux full-suite follow-up

The original `e592f169` Ubuntu job completed normally: **8989 passed, 31 skipped,
4 failed, 42 subtests passed** in 2525.75 seconds. Both startup-alias cases that
encountered the local Windows symlink limitation passed in this job.

Its four failures exposed outdated test preconditions. The navigation test's
direct scene flag was restored from the still-checked alignment action when the
editor refreshed; the test now disables snapping through that action and keeps
the exact drag-distance assertion. The short-page/panel test needs enough canvas
height for its content before asserting a 1:1 fit; long-page fitting remains
covered separately. Runtime port-conflict checking now matches the current
address-specific startup diagnostic while still requiring rejection and a live
original host. Stop-all uses real session state plus controlled authoritative
current runs, retaining uncertain-Start reconciliation, all Stop requests before
waiting, the deadline, and independent errors.

Both affected Runtime files pass all seven tests. Both affected UI files pass
all 30 tests locally. Their original exact drag, viewport-fit and no-side-effect
assertions are retained. The content-growth test's existing two-pixel camera
tolerance is now measured in viewport pixels, preserving that threshold when
zoom makes scene units differ from pixel units. The completed Linux failure
log and XML remain under `build/ci-e592f169-ubuntu*`; no skipped or incomplete run
is counted as a pass. Final cross-platform checks are required on the follow-up
revision.
