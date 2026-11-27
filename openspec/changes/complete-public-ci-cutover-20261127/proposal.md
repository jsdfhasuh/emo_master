## Why

The same product candidate has hosted evidence for the original workflow inventory, but its day-to-day dispatcher, result controls, protected private source transport and release authorization are not yet deployed. Changing repository visibility before those boundaries are verified can cause missed runs, false-green checks or secret exposure.

## What Changes

- Preserve every original trigger, filter, input, platform, test, build profile and required-versus-advisory distinction in a versioned route inventory.
- Bind every request to authenticated origin metadata and approved immutable worker versions; resolve PR head, merge and status revisions separately.
- Add bounded reconciliation, fail-closed status decisions and a protected writer prerequisite.
- Prepare authenticated private source and a private draft-only release adapter without activating either production route.
- Keep the current product workflows available through a controlled, independently reversible handover.

## Capabilities

### New Capabilities

- `public-ci-routing`: evidence-backed, protected dispatch and status handover for the product CI routes.

### Modified Capabilities

None. Existing product runtime and test capabilities are unchanged.

## Impact

The product, public controller, existing private evidence repository, and Windows packager participate. A test-only rollout proves behavior without writing official statuses. Product visibility, required status changes, official release, and final merge remain separately approved.
