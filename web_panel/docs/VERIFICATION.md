# Verification record — 2026-10-30

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
