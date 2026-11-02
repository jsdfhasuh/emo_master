# Contract integration boundaries — 2026-10-31

The missing SDK and ordinary-Job empty-group mismatch are resolved by the official
SDK **1.1.1** delivery `496d4b78ccd6ac40a4fa37b15fb6e78522627cda`.
Source: `95c6371542f73a725c0ca2fd585152c728338dbf`; generated artifacts:
`b9a741f31a7304a3b2902530a9b6acd98755285d`. Panel Contract remains 1.0.

- ZIP: `8f0efaf9b918d44684c5c8c8b3f0d67fee740179ea33d17c2a064c801d9d3b7a` (90,424 bytes).
- SDK wheel: `aed06e1badb4f3fedcca121413391dbf88168cc02dc37fc54f65cbf089c92858` (38,038 bytes).
- All 39 files plus the ZIP manifest are consumed byte-for-byte; `sourceDirty=false`.
- The outer Git manifest has normalized LF line endings; its JSON equals the ZIP
  manifest. The exact ZIP manifest is authoritative for the vendored copy.
- The older 1.1.0 handoff and verification history remain in the repository.

`runtime_client.py` now consumes generated SDK RPCs, decodeResult, CaptureCoverage,
canonical models and PreviewContext. Missing SDK installation fails closed; failed
Runtime connections never use mock results. Browser original `valueJson` and integer
identities retain precision. The gateway exposes fixed business APIs, not arbitrary RPC.

## Remaining validation boundaries

Actual Windows Designer process launch and closure, another physical computer,
real cameras/PLC, production database content and field deployment require their
own explicitly authorized acceptance. Neither cloud software tests nor Windows CI
prove these. No user-computer operation or production deployment is authorized by
this handoff. See VERIFICATION.md for checks actually executed.

Capability strings are surfaced exactly as Runtime returns them; the handoff does
not define an exhaustive enum. Unsupported RPCs fail visibly instead of inferring
support from a synthetic service. Global event order is not invented: event reads
are scoped to an explicitly selected Job and its sequence.

The old `draft-unverified` package and MockRuntime remain solely regression/development
fixtures, clearly labeled. Official package import uses the supplied SDK and the
unchanged published schema. These draft fixtures are not exported as current .emoproj.

## Resolved ordinary-Job identity mismatch

SDK1.1.0 rejected the Runtime’s legitimate empty group for job_state/job_changed.
SDK1.1.1 supplies the owner’s dedicated RunGroupId. Both messages now require the
field and accept exactly `""` for ordinary Jobs, rejecting missing/null. Control
results allow explicit empty identity and retain nullable/omitted semantics.
Nonempty group and other identity validation is unchanged. Web uses official
BrowserMessage validation rather than a fabricated group or local schema fork.
The temporary `UNGROUPED_JOB_SCHEMA_GAP` emission branch is removed. Exact Stop
continues to send `expected_run_group_id=""` with protobuf optional-field presence.

## Gateway-only view lifecycle messages

`scope_status` carries the unchanged Display snapshot's latest-started and expired
ordinal maps, including snapshots with no results. These are gateway-local view
messages, not fabricated members of the SDK BrowserMessage union. Decimal ordinal
strings retain full precision. A newly started result does not erase the last
completed result; expiry retires only live ordinals at or below the supplied scope
threshold. Already pinned frozen views remain valid until their own lease/TTL,
and Resume cannot resurrect a retired live result.

## Functional repair and verification

W1–W3 command settlement, ABORTED reconciliation, current-target refresh and
per-Runtime/project activation remain preserved. W4 consumes the official fixed
source and full delivery without overwriting those repairs. W5’s combined software
Runtime browser test now requires the formal empty-group contract. See
VERIFICATION.md for actual executions and retained historical failures; source
changes alone are not a claim that every acceptance layer ran.
