## Context

Product baseline `48b3019`, tested product candidate `7fccb49`, evidence-only descendant `c87c2b0`. Public controller and reusable packager revisions are separately pinned for each run. The frozen evidence matrix is in the existing OpenSpec evidence record; exact trigger conditions and job naming are in `routing-inventory.json`.

## Goals / Non-Goals

- Shift heavy execution to GitHub-hosted public Linux/Windows without changing test order, closure conditions, original assertions, dependencies or scope.
- Keep the production credential and V2 identity outside public workloads and logs.
- Prevent older requests or attempts from writing success for another generation.
- Do not make the product private, enable official routing/required checks, or release software in this review.

## Decisions

1. Keep native trigger selectors at the existing workflow boundary. Derive the route from an authenticated workflow identifier, not a payload route or branch override. Unprotected same-repository branches are not trusted solely because they are internal. An eligible PR must revalidate the merge ref and its ordered parents.
2. Generalize the request validator only under a reviewed private route policy; pin the exact product emitter blob and the immutable registered public worker reference. Check separate product event and execution SHAs, then bind run/attempt and approved result contexts. A green emitter never completes a product check. Automated durable dispatch remains off until a protected, dedicated private ledger and authorized single status writer are evidenced.
3. Store one binding and status decision per generation. Claim a dispatch once before POST; recover ambiguous delivery by reading registered runs, never blindly sending again. Revalidate current generation, exact context/creator and read back every status change. Expired, ambiguous and untrusted updates cannot become success. No public action receives the status credential.
4. No broad PAT or V2 private key enters any public worker. Verify exact authenticated retrieval in the existing private evidence repo first, then verify an approved safe relay with a harmless private fixture. No inference of product Private access from the still-public checkout. Require exact SHA verification before tests and no secrets or raw logs in artifacts.
5. Source-only Release, BuildOnly, authenticated draft writing and actual publication are separate. Release staging accepts six reviewed assets, with version and runner pins, only for an explicitly approved existing draft in the correct Private repository. Resume only when existing remote asset digest and size match. Do not replace assets or publish automatically.

## Risks / Trade-offs

- GitHub Commit Status has no conditional POST. Reserve the contexts to a single protected writer; do not steal a potentially live claim. An exhausted or ambiguous write escalates and remains non-success.
- Outer job deadlines can report cancelled rather than timed_out. Both are non-success; only an authenticated watchdog record can disambiguate cause.
- Proposed route adapters are not production wiring. Public default branch registration, actor permissions, callback trust, and branch rules must be independently inspected.

## Migration Plan

1. Compare current manifests/recipes and effective branch required checks (operator/admin can inspect if the connector returns 403). Keep diagnostics advisory unless their currently verified rule says otherwise. Candidate legacy source names are `ubuntu-latest / Python 3.10` and `windows-latest / Python 3.10`; do not edit branch rules from guessed check names.
2. Preserve original active/archived workflows while validating fresh trusted push, same-repository PR merge and manual shadow routing. Verify success, failed/cancelled/timeout/source failure/missing evidence and replay/retry. Review private ledger, token scopes and public worker allowlist before credentials are enabled.
3. For exact code/recipe/dependencies/runner equivalence, reuse previous evidence. Evidence-only Markdown does not invalidate it; revalidate changed routing and affected worker recipes, not every diagnostic blindly.
4. After approval, register the immutable worker on the public default, deploy approved private reconciler and compare shadow outcomes. Keep only one official execution per route: prepare reversibility first, make new routing authoritative together with its required-check transition, then retire only the replaced original heavy implementations. Do not pause both sides. Observe a complete cycle before closing rollback.
5. Stop if proof is missing/incorrect, callback is stale, duplicate or missing execution occurs, or any evidence leaks. Stop dispatch, fence the writer, restore old route and required checks from saved config, confirm old statuses run, then withdraw the broken replacement. Handle pending generations individually; don't mark success to unblock.
6. After separate Private approval, run exact same authorization and event checks against the real product and verify unauthorized reads fail. After separate release approval, verify existing draft upload/download and all six hashes before any production publication. A failed draft stays unpublished.

## Open Questions

The private ledger ref and enforcement are not yet provisioned; the actual required context configuration was not returned by the current integration. The full private-source relay and hosted durable write path still require verification.
