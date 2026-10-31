# Verification record

## Current official-contract verification — 2026-10-31

Application commit: `69e3ec000a4299d16dcf27e19237d102062db77d` on
`codex/logging-and-designer-improvements`.
[CI 41159087465](https://github.com/jsdfhasuh/emo_master/actions/runs/41159087465):
**SUCCESS — all three jobs**. The following documentation-only commit preserves
this application code; its exact-head workflow is checked separately before delivery.

| Check | Verified result |
|---|---|
| Ubuntu + Windows gateway | Each 122 tests, 120 executed PASS and 2 opt-in product tests skipped |
| Frontend | 43 tests PASS; TypeScript and production Vite build PASS on both OSes |
| Chromium | Renderer fixtures, full HTTP/WS mock application, and official-package preview CLI/browser suites PASS on both OSes |
| Independent distribution | Web wheel + supplied SDK wheel; fresh outside-checkout install, packaged UI/CLI, no Qt or Runtime imports PASS on both OSes |
| Owning Windows software Runtime | Separate job: 2 actual Runtime tests plus 2 reconciliation regressions PASS; no-Job history/aggregate/opaque images and one Start → capture → same-ID lookup → exact Stop → one Job |
| Visual inspection | Official live/history mobile screenshots inspected; responsive layout and no horizontal overflow; synthetic sample images/data |
| Physical/Designer acceptance | NOT RUN: user's machine, native Designer lifecycle, physical cameras/PLC and production database |

The Runtime subprocess uses unchanged owning source from the selected handoff.
The earlier Windows spawn failure was the **test harness's piped stdin**, resolved
with DEVNULL plus a private shutdown marker; no Runtime patch or relaxed acceptance
assertion was used. UNKNOWN reconciliation never replays Start.

### Matching development artifacts (7-day retention)

- [Windows Web + SDK wheels/docs](https://github.com/jsdfhasuh/emo_master/actions/runs/41159087465/artifacts/13026805895), 187056 bytes, ZIP SHA256 `d205c31d6611b7400fa78c9a799544793debdf1010c045ef9ede53329effcdf7`.
- [Ubuntu Web + SDK wheels/docs](https://github.com/jsdfhasuh/emo_master/actions/runs/41159087465/artifacts/13026194483), 186728 bytes, ZIP SHA256 `4874e05234444337a0c63ffece050cd6fc12162bf0646053354754fdfedd56fb`.
- [Ubuntu browser evidence](https://github.com/jsdfhasuh/emo_master/actions/runs/41159087465/artifacts/13026790480), verified ZIP SHA256 `c849baf1f1080afc26050efc678286ef34c5eb4242ceebda0c37cc4d48588b82`.
- [Actual software Runtime evidence](https://github.com/jsdfhasuh/emo_master/actions/runs/41159087465/artifacts/13025563659), ZIP SHA256 `874af9c432c79373b4fabffb8b6a656bb3c7aaac0a2fcc87e85a3539b833a487`.

These are development artifacts, not a release/deployment. SDK1.1.0's nonempty
`runGroupId` browser-status mismatch remains disclosed in CONTRACT-GAPS.md;
passing software checks do not resolve that protocol-owner correction or prove
physical acceptance. No merge, deployment or user-computer action occurred.

The remaining record below is historical diagnostic evidence, superseded by the
current results above.

## Historical draft — 2026-10-30

## Verified application source

- Branch: `feat/web-operator-panel-20261030`
- Application source commit: `40d2c80982a30364ebb58c2d456faa392a33b475`
- [CI run 41027918639](https://github.com/jsdfhasuh/emo_master/actions/runs/41027918639): **completed / SUCCESS**.
- Ubuntu job `123970370329` and Windows job `123970370431`: all verification/build/install steps **SUCCESS**, not skipped.
- Compare against original main `f0675253631f489998056e5af618378e115efc83`: 52 changed files, all under `web_panel/` except `.github/workflows/web-panel-ci.yml`. Runtime/Designer/proto and their dependencies were not changed.

## Actual evidence

| Level | Result | What it proves |
|---|---|---|
| Backend | 72 tests PASS on both CI OSes (Python 3.10), also locally Python 3.12 | Auth/ACL/account races, request correlation, package security, preview subprocess, mock history, view/resource lifecycle and API integration |
| Frontend | 18 tests PASS, TypeScript/Vite build PASS | Scope preparation, identity/size/digest checks, cancellation, bound styles/components, draft mapping |
| Browser fixtures | Chromium PASS on both CI OSes | Script escaping, 13 renderer types, viewport/navigation, slow-image/reset/freeze cases; HTTP/WS are fixtures |
| Full Web app | Chromium + real FastAPI HTTP/WS PASS on both CI OSes | Login, live mock result, freeze/resume, explicit stop/start, no-run history/start, account and target CRUD/grants, history selection/image, real ZIP upload/activate/version switch/rollback, independent viewer notification, viewer restrictions, mobile width |
| Distribution | Wheel build + fresh independent venv install PASS on both OSes | Bundled UI and CLI available without Node on target; no Qt/Runtime/model imports |
| Real production | **BLOCKED / NOT RUN** | No official SDK, real Runtime, Designer integration or hardware acceptance was supplied/performed |

The short resource exercise is 16 viewers × 2 scopes × 200 updates plus bounded/concurrent byte-cache tests, not a day-long or hardware soak. Windows CI is an actual Windows runner, not the user's Windows machine. The cloud-local Chromium launch was sandbox-blocked; it is not counted as a pass. CI browser results are the evidence.

The final evidence-only change waits for `99.50` and a decoded image before the mobile screenshot, rather than capturing the package-switch transition. It changes no application behavior and is rerun through the same branch CI. Consult the latest branch run for that final documentation/test-only commit.

## CI development artifacts

These are development-preview artifacts, retained for 7 days, not a production release:

- [Windows wheel/docs/draft package](https://github.com/jsdfhasuh/emo_master/actions/runs/41027918639/artifacts/12964373861): 119409 bytes, artifact ZIP SHA256 `fc8b439448a19a55531f7fa5584865318f0e86c5e6d88b93fbc476cd2a90070d`.
- [Ubuntu wheel/docs/draft package](https://github.com/jsdfhasuh/emo_master/actions/runs/41027918639/artifacts/12964079536): 119193 bytes, artifact ZIP SHA256 `042fe93cb45b4c5741761f5241582cec279a5d1fcfeef5bb240becfb9a68fdbc`.
- Browser evidence is attached to the same run. The independent reviewer verified the Ubuntu evidence ZIP digest and inspected the mobile layout.

Hashes above identify the whole GitHub artifact ZIP, not the contained wheel. New runs produce new artifacts; use the matching commit/run metadata rather than mixing versions.

## Why the whole original plan is not declared complete

The independent draft implementation has passing software checks, but Panel Contract v1 has not been provided. All real business RPC adapter methods still return `CONTRACT_UNAVAILABLE` rather than inventing compatible behavior. Formal mobile/export schema, Coverage/leases/cursor replay, authoritative history/events and Dispatch/continuous root identities remain external integration gates.

See [acceptance matrix](ACCEPTANCE-MATRIX.md) and [exact handoff inputs](CONTRACT-GAPS.md). No merge, production deployment, public sharing service, OS configuration or user-computer operation was performed.

## 2026-10-31 official contract integration (supersedes missing-SDK status above)

Base: `codex/logging-and-designer-improvements` at `b9f448c18ed297652cddc3e040ca1db847b0fbfa`.
Official handoff uses clean implementation `d4ab96409e43d7ab6ae566820da357508d01658c`;
all34 file sizes/digests verified. Historical 2026-10-30 results above describe the
old draft only, not the current official adapter.

Local observed checks: 95 gateway/package tests, 18 synthetic loopback/group tests,
and 43 frontend tests passed in their respective existing environments.
TypeScript/Vite builds passed. Local full-environment/CLI/browser checks remain
unverified: isolated dependency installation was blocked by the execution reviewer,
and the permitted clarified retry failed before execution in the sandbox wrapper.
Chromium also could not create its IPC socket. These failures were not bypassed and
are not counted as successful tests. The dedicated CI workflow provides a separate
normal Windows/Linux install and browser validation environment. Final exact-SHA CI
results are pending until recorded below.

No merge, deployment, user-machine operation or physical-device validation performed.

### First integration CI and corrections

[Run 41157631820](https://github.com/jsdfhasuh/emo_master/actions/runs/41157631820)
at `b3927e10059a97b5d8d8dae251c43e34425696aa` was **not successful**.
Ubuntu backend ran 115 tests (2 opt-in product tests skipped); frontend/build passed.
Browser checks caught a Resume regression that discarded the displayed snapshot
while another image was pending; the implementation now preserves the displayed
snapshot, with two additional regression tests and the original browser assertion intact.
Windows exposed implicit text decoding and two legitimate loopback transport timeout
statuses; fixtures now use explicit UTF-8 and the failed-preview test accepts either
UNAVAILABLE or DEADLINE_EXCEEDED while still requiring no ready event and nonzero exit.
The owning Windows Runtime history/image test passed; Start/capture/Stop did not yet
pass. None of these partial results establishes full W1–W6 acceptance.

The follow-up scope-lifecycle review found and fixed dropped snapshot expiry/start
metadata, including empty snapshots. Focused tests cover lossless ordinals, scoped
live retirement, frozen pin preservation and Resume without stale resurrection.
Run 41158645415 passed Ubuntu browser fixtures and full mock Web application E2E;
the official preview browser reached rendered history but failed a test-only nested
locator, now corrected. Owning Runtime Start remains under diagnosis; retained thread
dumps show Windows multiprocessing spawn stalled, not successful capture acceptance.
