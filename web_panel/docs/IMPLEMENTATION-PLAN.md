# Development scope and status

User approved the 2026-10-30 independent Web plan, then explicitly selected the existing `jsdfhasuh/emo_master` repository and a new feature branch. All Web code lives under `web_panel/`, independently installable. Runtime, Designer and shared protocol source remain owned by the local developer and are not changed here.

## Delivery stages
- W1: standalone ASGI app, local accounts/roles, explicit mock/grpc ports, session identity. Real SDK binding blocked by handoff.
- W2: bounded ZIP import/activate/rollback, draft component renderer, mobile grid, loopback preview launcher. Formal exported schema and Designer invocation blocked.
- W3: shared upstream mock subscriptions, per-user views/freeze, bounded images/results, reset and disconnect states. Real lease/cursor replay blocked.
- W4: mock history/aggregate/table-image demonstration. Real Runtime authoritative queries and event replay blocked.
- W5: demo target controls with stable request IDs and persisted outcome, permissions and audit; no replay after uncertain outcomes. Production Dispatch/control contracts blocked.
- W6: independently built frontend + Python distribution and instructions. No machine deployment, firewall changes or public hosting.

## Rules
- No UI screenshots presented as proof of Runtime/hardware operation.
- No claim that same-machine venv isolation means no additional local service.
- Use same-origin cookies/CSRF/Origin checks; no generic RPC proxy, raw SQL or paths.
- One Web worker; no external queue or second Runtime scheduler.
- Preserve runtime-owned task lifetimes when views close.
- Security work targets normal imports and ordinary hostile client inputs; no generalized recovery platform.
