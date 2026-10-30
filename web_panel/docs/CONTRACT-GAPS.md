# Official contract handoff blockers

This implementation is an explicitly labelled development slice. It is **not W1–W6 complete** and is not connected to the user's Runtime.

Required from the local Runtime/Designer developer:

1. `panel-contract-v1/contract-manifest.json`, source SHA and official versioned `emo_panel_protocol` wheel.
2. Generated TypeScript types, JSON schemas (especially published-presentation), canonical/Coverage rules, error codes and fixtures.
3. The companion `emo-master-runtime-web-integration-plan-20261030.md` referenced by the supplied plan.
4. Definitions and implementation availability for GetRuntimeInfo, ListRuns/GetRunState, history queries/images, StopJob identity extensions, ListWorkflows production inputs, Dispatch identities and start-request lookup semantics.
5. A complete sample .emoweb export, mobile layouts, current quota and history-definition rules, and preview launcher I/O contract.

The `draft-1` HTTP/UI and demo package fixtures are internal adapter examples, not a replacement or fork of Panel Contract v1. They must be aligned after the handoff. No new 2.8 `.emoproj` or purported supported 1.1 presentation is generated. No private database paths or SQL are accepted from the browser.

Real mode creates a grpc.aio channel in ASGI lifespan but deliberately returns CONTRACT_UNAVAILABLE (503) for business RPCs until the official SDK is supplied. It never falls back to mock. No generated Runtime proto has been copied. Actual network handshake, Runtime controls/history/leases and Designer preview integration remain BLOCKED, not PASS.

The existing read/subscribe/resource RPCs are known, but their generated SDK is not available as an independent package in this input. Installing the entire emo_master to bypass this gap would violate the isolation plan.
