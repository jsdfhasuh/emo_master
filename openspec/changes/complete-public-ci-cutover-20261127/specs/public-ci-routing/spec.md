## ADDED Requirements

### Requirement: A route SHALL bind exact authenticated origin and worker identities
The system SHALL bind the approved source repository, event, request workflow, product head, execution SHA, associated merge parents, controller revision, and the expected run attempt before a result is accepted.

#### Scenario: A reviewed pull request changes during dispatch
- **WHEN** an authenticated pull request no longer has the approved merge ref and ordered parents
- **THEN** the dispatcher rejects the stale request and does not grant source access or write a successful status

### Requirement: Non-success and incomplete evidence SHALL remain non-success
The status writer SHALL require authenticated worker and evidence identities and SHALL reconcile using a bounded protected single-writer protocol.

#### Scenario: A worker report is missing after the deadline
- **WHEN** the end-to-end deadline is reached without a verified matching worker result
- **THEN** the writer records a non-success decision and uses bounded retry or escalates to reconciliation without publishing success

#### Scenario: A newer attempt supersedes a completed worker
- **WHEN** a terminal observation belongs to a superseded attempt
- **THEN** it cannot overwrite the newer generation's status or satisfy its required check

### Requirement: Private source and publication credentials SHALL remain separated
The broad product token and V2 private key SHALL stay in approved private execution, and a release must not be inferred from a source check or nonpublishing build.

#### Scenario: A frozen package has been verified without publication
- **WHEN** both BuildOnly products have valid encrypted receipts
- **THEN** the system records only artifact validation and does not stage or publish an official release without separate authorization

### Requirement: Cutover SHALL preserve the original workflow semantics
The migration SHALL map the twelve original workflow routes and preserve existing trigger/filter, inputs, platform, test and gating behavior until a reviewed production transition.

#### Scenario: A newly routed required workflow does not return its expected result
- **WHEN** the replacement cannot prove the required result for the correct commit
- **THEN** the migration stops and the rollback restores the verified former route and checks without weakening acceptance


#### Scenario: Owner-coordinated isolated Source rehearsal
- **WHEN** the owner explicitly authorizes one fixed Source shadow rehearsal with the existing owner credential
- **THEN** the reviewed private entrypoint verifies actual deployment, Environment, branch and worker protection before any reservation or write
- **AND** it coordinates its one writer using persistent non-force-update claims and independent API readback
- **AND** the result targets only the optional Source shadow context, does not imply platform-level isolation from other owner clients, and does not establish a formal Required Check.
