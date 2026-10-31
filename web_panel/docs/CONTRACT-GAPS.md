# Contract integration boundaries — 2026-10-31

The earlier missing-SDK blocker is resolved. The selected handoff commit is
`b9f448c18ed297652cddc3e040ca1db847b0fbfa`; its Runtime implementation is
`d4ab96409e43d7ab6ae566820da357508d01658c`.

- ZIP: `2ac8435d0aee3323413be980262eeae97a0aa0282f859ca3e77e6753915b2c5c`.
- SDK wheel: `bb0cff1f007b8c6b850c8e43d792200ee6adebbaa4af23c4a966362b74e697f9`.
- All 34 files match the manifest; `sourceDirty=false`.
- Earlier uploaded ZIP had different provenance and whitespace-only SDK differences. It is not used as the final dependency.

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

## Confirmed ungrouped browser-status schema mismatch

Actual Runtime ordinary Jobs legitimately have `run_group_id=""` (owning
`tests/runtime/test_run_catalog.py` exact-stop path). SDK1.1.0 `messages.py`
BrowserJobState/BrowserJobChanged require `runGroupId: Id` (nonempty). Web
keeps the true empty identity for Stop and exposes gateway-local `type=job_state`
with `contractStatus=UNGROUPED_JOB_SCHEMA_GAP`; it does not emit a fabricated
SDK BrowserMessage or claim that extension passes the official schema. Grouped
Jobs use official BrowserJobState. Owner correction needed: permit empty group
identity for ungrouped Jobs, with regenerated schema/types/fixtures. No vendored
SDK or owning Runtime source is modified to conceal this mismatch.

## Gateway-only view lifecycle messages

`scope_status` carries the unchanged Display snapshot's latest-started and expired
ordinal maps, including snapshots with no results. These are gateway-local view
messages, not fabricated members of the SDK BrowserMessage union. Decimal ordinal
strings retain full precision. A newly started result does not erase the last
completed result; expiry retires only live ordinals at or below the supplied scope
threshold. Already pinned frozen views remain valid until their own lease/TTL,
and Resume cannot resurrect a retired live result.
