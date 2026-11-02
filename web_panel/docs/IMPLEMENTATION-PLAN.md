# Current functional repair scope — 2026-10-31

Baseline reviewed by the user: `828633688aafff301e16333bb0a94d07b0194ed0`.
Working branch: `codex/logging-and-designer-improvements`. This repair's W1–W5
labels are distinct from the original delivery plan's W1–W6 below.

- W1: settle accepted commands separately from Job lifetime; common direct/query
  transitions; original-identity Stop reconciliation including ABORTED and actual
  resource flags; truthful rejection and UNKNOWN handling without replay.
- W2: one current-target catalog refresh for identity/project/configuration, Jobs,
  workflows and contextual packages; generation/abort guards, valid-selection and
  edited-input preservation; clear old identity controls/resources; reuse after
  commands/reconnection and manual refresh, not high-frequency full polling.
- W3: SQLite per-Runtime/project activation, additive package grants, project
  validation, scoped notices and pinned old views; minimal explicit legacy migration.
- W4: official SDK 1.1.1 delivery integrated by a two-parent work-branch merge,
  preserving both owners’ changes. Exact wheel/schema/types/fixtures and built-ins
  are updated; ordinary Jobs use formal messages and explicit empty-group Stop.
  The combined browser gate now requires formal ungrouped messages, not a skip.
- W5: accepted/UNKNOWN/ABORTED browser flows, external catalog refresh and A/B
  package isolation; combined real Chromium → Web HTTP/WS → repository software
  Runtime Start/result/exact Stop; retain dual-OS and independent installation.

Execution evidence and exact commits are recorded in VERIFICATION.md. No source
change or passing fixture substitutes for the explicit combined software chain;
Designer/native UI, another physical host and field devices remain separate.

---

# Development scope and status

User approved the 2026-10-30 independent Web plan, then explicitly selected the existing `jsdfhasuh/emo_master` repository and a new feature branch. All Web code lives under `web_panel/`, independently installable. Runtime, Designer and shared protocol source remain owned by the local developer and are not changed here.

## Historical original delivery stages (superseded)
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
