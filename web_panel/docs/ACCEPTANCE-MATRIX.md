# W1–W6 acceptance audit (development preview)

Status meanings: IMPLEMENTED = limited functionality exists and has tests; PARTIAL = scope still missing; BLOCKED = exact external contract/input needed; NOT RUN = evidence not obtained. A mock passing test does not count as Runtime acceptance.

| Stage | Implemented development slice | Independent work still incomplete | External blockers / unverified acceptance |
|---|---|---|---|
| W1 | Independent FastAPI entry, SQLite accounts, role checks, sessions, CSRF/Origin, mock/real explicit selection; no Qt imports | Multiple Runtime alias CRUD; per-target ACL; comprehensive session/audit admin UI; typed official API model mapping | Official SDK absent. All grpc client methods currently fail closed, including existing operations; no real RPC has been integrated. |
| W2 | Bounded immutable ZIP import, activate/rollback, draft version gate, 13 renderer cases, loopback temporary-port preview CLI | Full component-property semantics, responsive override schema, sqlite aggregates beyond demo count; Designer launcher workflow | Official package/schema/examples absent. Draft-only package format is not claimed compatible with Designer exports. Windows preview/installation not run. |
| W3 | Shared single mock upstream per run, bounded per-view queues, owner isolation, single-scope freeze, staged image/value UI | Backend multi-scope freeze and lease lifecycle; comprehensive state/reconnect UX; target switch races and soak coverage | Official results, cursor/replay, resource lease contracts absent. Real image ownership and exactly-once delivery not verified. |
| W4 | History table filtering/sorting/paging, opaque mock image references, count demo | No-run history browsing, full declared aggregates, counter/event/history semantic coverage | Authoritative Runtime query RPC/schema unavailable; current rows are explicit fixtures. |
| W5 | Mock start/stop, stable request ID, durable unknown/success state, owner-only lookup, no automatic replay | Workflow selector/no-active-run start UX; complete input forms, per-target authorization, detailed operation logs | Real Dispatch/StopJob identity contract and real state resolution absent. Persisted local intent is not authoritative Runtime completion. |
| W6 | Frontend build, independent wheel packaging and CI pipeline, startup docs, immutable assets | Windows/Linux installation matrix and endurance/resource budget verification; release-quality manuals and screenshots | First CI: backend/build/fixture-browser passed, full Web browser found missing WS dependency (fixed candidate, rerun required). Clean wheel installation and full browser evidence pending final CI. No deployment performed. |

## Acceptance boundaries
- The implementation is **not all W1–W6 complete** and **not production-ready**.
- Missing SDK does not excuse independent TODOs above. These must be developed and tested separately.
- Current permissions are global viewer/operator/admin over one configured Runtime; do not present them as per-project/per-run authorization.
- Official Panel Contract v1 inputs required: versioned `emo_panel_protocol` distribution, manifest, generated stubs/TS types, schema, error semantics, fixtures, exported package, companion Runtime/Designer plan.
- Root Runtime/Designer/proto source and existing dependencies remain unchanged.
