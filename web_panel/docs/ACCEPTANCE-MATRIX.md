# W1–W6 integration matrix — 2026-10-31

Implementation is not the same as field acceptance. Final test counts/commit/run
are recorded in VERIFICATION.md. Runtime/Designer execution source remains unchanged. The protocol owner’s SDK1.1.1
source and generated delivery are integrated without local protocol edits.

| Stage | Web implementation | Evidence boundary |
|---|---|---|
| W1 | SDK1.1.1 grpc.aio adapter, explicit target directory/ACL, accounts, same-origin application, synthetic gRPC tests | SDK and synthetic wire semantics; actual software Runtime separately checked |
| W2 | Official published schema/canonical digest/resources, immutable activation/rollback,13 renderer types and responsive layout, tokenized preview with mandatory context | Same renderer; browser and CLI checks in CI; native Designer acceptance separate |
| W3 | Shared instance/Job stream, snapshots/cursor continuation, Coverage, bounded independent view queues/freezes, exact leased assets, staged browser results | No Start/Stop/ReleaseJob on view close; physical production performance not claimed |
| W4 | Formal-history revision/identity, tables/aggregates/opaque images, stable row refresh, authoritative counter values and per-Job event sequence | Synthetic fixtures and software contract checks are not user database reads |
| W5 | Stable Start intent/CAS and GetStartRequest reconciliation, exact Job/instance/project/group Stop, allowed workflows, accounts/audit, pinned package updates | Concurrent Jobs not globally forbidden; Runtime alone owns continuous execution |
| W6 | Packaged frontend + independent Web/SDK wheels, explicit config and two preview modes, Windows/Linux workflow and isolated installation check | Node only builds UI; no system service, firewall, deployment or merge |

## Integration rules

- Official Runtime/Job identity, scope, revisions, resultOrdinal and source manifest are checked; no silent identity fallback.
- Captures missing from an existing Job show SOURCE_NOT_CAPTURED/definition-change reasons; no automatic restart.
- Page activation and rollback never write formal configuration.
- Control timeout remains UNKNOWN until queried; same intent is never replayed.
- Preview rejects all production mutations even if a privileged account is injected.
- SDK schema and Python canonical algorithm are authoritative. Browser does not recompute revisions or round large integers.
- No real user computer or hardware is operated by cloud tests.
