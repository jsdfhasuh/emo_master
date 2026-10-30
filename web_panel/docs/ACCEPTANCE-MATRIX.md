# W1–W6 acceptance audit — development contract boundary

Updated after independent review on 2026-10-30. `IMPLEMENTED` below means the independent draft behavior exists with tests, not formal Runtime acceptance. `BLOCKED` means required authoritative input is missing. `PENDING CI` is not a pass.

| Stage | Independently implemented and locally tested | Still blocked / unverified |
|---|---|---|
| W1 | Independent FastAPI, accounts/immutable user identity, password hashing/session revocation, target alias CRUD and per-target role-limited ACL, same-origin app, explicit mock/grpc, no Qt imports | Official SDK/manifest absent. **All real business methods**, existing and proposed, currently fail closed. No real RPC is integrated. Simulator is an internal draft object port, not a certified same-contract gRPC Runtime. |
| W2 | Immutable bounded ZIP import, per-type allowlists and references, activation/rollback and target association, all 13 draft renderer types, responsive stable ordering, static/fixed/selected image paths, loopback temporary preview with isolated state | Official published-presentation/schema/mobile overrides and complete export sample absent. Formal mobile overrides rejected instead of guessed. Designer process/launcher integration not validated. |
| W3 | Shared streams per target/run, bounded per-view queues, reliable control notices, per-scope displayed-key freeze, generation guards, byte/TTL budgets, exact cached image pins, SHA256 coalesced resources, snapshot reconnect, blob lifecycle, slow-image/reset tests | Formal resources/lease/Coverage/cursor/replay and Dispatch scope contracts missing. No AcquireLease/ReleaseLease calls or real replay. Frozen missing bytes show unavailable, never latest substitution. |
| W4 | No-run history view, declared columns/filter/sort/page/refresh/selection, fixed and row-linked images, count/sum/avg/min/max with filter policy, Runtime-owned mock counters and bounded cursor event UI | Official authoritative history/image/counter/event DTOs and semantics absent; all current dataset examples are explicit mock fixtures. No real history database read. |
| W5 | Separate workflow/start and selected stop targets, revision/identity checks, declared mock input validation, stable request correlation, persistent unknown state/query without replay, granular target permissions, audit UI, package change notification/pinned old versions | Formal StartJob/GetStartRequest, StopJob extended identity, continuous root/Dispatch group membership and allowed-action semantics unavailable. No guessed group or periodic scheduler. Production controls not validated. |
| W6 | Frontend bundling into independent wheel, CLI/docs/configuration and upgrade instructions, Ubuntu/Windows CI jobs including browser and clean-venv install checks | Source commit 40d2c809 passed Ubuntu and Windows CI, including true browser and clean wheel installation; see VERIFICATION.md. User-selected host/real Runtime/Designer/hardware acceptance not run. |

## Evidence boundaries
- Local backend, frontend tests and build have passed during implementation; final counts and final commit belong in the terminal verification report, not inferred from earlier runs.
- CI run 41027918639 finished SUCCESS on Ubuntu and Windows for source commit 40d2c809; both fixture and real FastAPI HTTP/WS browser checks, wheel build and fresh-venv install passed. Final evidence-only changes are separately checked by branch CI.
- Local Chromium cannot create its singleton socket under this cloud sandbox. It was not escalated around or treated as a successful browser run; CI supplies the actual browser evidence.
- Resource bound test exercises 16 viewers × 2 scopes × 200 updates, plus coalesced concurrent byte reads. This is a reproducible bounded exercise, **not** a day-long production soak or hardware qualification.
- No Runtime/Designer/proto files or their dependencies are modified. No user machine, network/firewall/service or production deployment is operated.

## Minimum external handoff to unblock final acceptance

1. One versioned `panel-contract-v1/` bundle: manifest/source SHA, `emo_panel_protocol` wheel, TS types, JSON schemas, canonical/Coverage logic, errors and full fixtures/export sample.
2. Companion Runtime/Designer plan and confirmed implemented capability/version matrix: instance/project/configuration identity, workflow inputs, run/Dispatch/continuous ownership, StartRequest, history definitions, resources/lease, quotas and events.
3. After adapters are implemented against that bundle, a user-authorized real Runtime test path or a local developer running the same acceptance procedure and returning logs. No permission to operate the user's computer is implied.

Until these are supplied and integration passes, **the complete W1–W6 plan is BLOCKED, not all complete**, even if every independent draft test is green.
