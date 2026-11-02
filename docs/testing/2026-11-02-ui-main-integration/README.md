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
