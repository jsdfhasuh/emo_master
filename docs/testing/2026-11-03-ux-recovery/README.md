# UX recovery and feedback fixes (2026-11-03)

Base: `2b0033a4a26b5213d0b3ae627d7ca19508b9dba7`, checked against remote main before editing. Scope is the five approved UX fixes only; no merge, deployment, device operation or user-desktop access.

## Changes

1. Typed HTTP errors and centralized 401 session expiry return to the login screen with Chinese guidance. Local control selections are cleared; original request records remain user-keyed in sessionStorage for explicit queries, never automatic replay. Session generations prevent old 401 responses and command callbacks from affecting a new login. Logout clears the local session even when expired; other server logout failures are explicitly disclosed, not presented as successful remote invalidation.
2. Preserve and display Runtime `startBlockReason` alongside a disabled Start. The command handler also retains `canStart=false`; no authority or permissions are expanded.
3. Catalog failures have their own error state. Only a current successful catalog request clears that error, including an empty catalog. Control/validation errors remain visible.
4. Invalid JSON/non-object inputs fail before intent persistence/POST, saying the command has not been sent. Submitted failures retain the original request ID and UNKNOWN tracking without replay.
5. Designer panel menu actions are explicitly paired with panel indices. Their checks no longer depend on positions that now contain Web-preview actions.

## Evidence layers

- `web-before.txt`: same six regression assertions against the unmodified base: 4 fail, 2 pass. Tests execute production closures/JSX with state doubles; they are not browser UI tests.
- `web-initial-harness.txt`: retained initial test harness failure (missing VM exports), corrected before recording the real before/after comparison. No product assertion was relaxed.
- `web-after.txt`: all 75 Node tests pass, including session/logout and late-response isolation tests. SSR checks official blocking reason and disabled Start.
- `web-build.txt`: TypeScript strict no-emit and production Vite build pass.
- `static-checks.txt`: protobuf drift, full src/tests Ruff and mypy (609 source files) pass.
- `designer-before.txt`: new menu tests fail 9/10 on the old code. `designer-after.txt`: new tests plus directly related workspace tests pass 44/44 in Python 3.10 / PySide2 offscreen. Offscreen object/UI automation is not visible desktop acceptance.
- `designer-suite.txt`: the entire page_designer test directory additionally passes 403/403 (287.07 seconds), same offscreen evidence boundary.
- `backend.txt`: isolated Web backend suite runs 150 tests, with 148 pass and 2 explicitly skipped (opt-in product Runtime). Official SDK 1.3.0 is installed in a separate temporary environment.
- `backend-initial-path.txt` and `backend-old-environment.txt`: retained unsuccessful environment attempts, not hidden or counted as product test passes. First used an incorrect source root; second inherited Starlette 1.7.0 without its `httpx2` test dependency. Final isolated environment installed the declared Web dependencies, SDK and `httpx2` for TestClient; no repository dependency was changed.

The existing `catalog_refresh_e2e.py` browser test now checks all four Web fixes through UI events and HTTP fixtures, including different-user pending isolation and explicit query without another Start POST. It remains wired into the existing Web CI workflow; no CI gate was weakened. Local Chromium was **not attempted again** after the previously recorded socket permission failure. Actual browser results must come from CI, not these Node/SSR results. Fixtures do not represent physical hardware, production Runtime, or manual acceptance.

Independent review checked the five-fix scope and ran the 75 Node tests and TypeScript check. The review's logout warning/generation findings were addressed before submission.
