# Verification record

Current SDK integration evidence is in the final section. The 2026-10-30 record below is historical only.

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
