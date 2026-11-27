## ADDED Requirements

### Requirement: Runtime remains the single execution and resource owner

Runtime SHALL be the authoritative owner of formal project
configuration, Job lifecycle, workflow execution, device lifecycle,
communication services, execution events and presentation resources.
Operator, Designer and Web SHALL use the declared APIs of the Runtime
instance they are observing rather than creating parallel executors,
schedulers, device managers or Job ledgers. Existing Runtime modules
SHALL remain in place unless a confirmed gap in this change requires a
targeted correction.

#### Scenario: Multiple clients observe the same Runtime

-   **WHEN** Operator and another supported client observe the same
    Runtime instance
-   **THEN** they see matching Runtime, project and Job identities from
    the same service
-   **AND** closing an observation client does not stop a Job or release
    resources it does not own

#### Scenario: Operator closes its owned Runtime

-   **WHEN** the Operator host is explicitly closed
-   **THEN** it closes the Runtime instance it owns through the existing
    host lifecycle
-   **AND** its owned workers and device resources are retired according
    to the existing shutdown contract

### Requirement: Runtime readiness and project readiness are distinct

Operator SHALL distinguish the relevant host, service, project, Job,
device and Presentation states using existing authoritative fields and
start-availability rules. Unknown states SHALL remain unknown. A generic
connected flag MUST NOT substitute for these states. Manual and automatic
starts SHALL use the target workflow's lightweight Runtime availability
and block reason alongside the host's existing control conditions. Full
preflight SHALL remain in StartJob.

#### Scenario: Runtime starts without a project

-   **WHEN** Runtime is ready but no project is loaded
-   **THEN** Operator reports Runtime ready and project loading
    available
-   **AND** it does not report a workflow as ready to execute

#### Scenario: Project loading fails

-   **WHEN** project loading or validation fails
-   **THEN** Operator shows the actual failing stage and available
    diagnostics
-   **AND** it does not display a false successful/ready state

#### Scenario: Start a Job through the existing admission path

-   **WHEN** the existing lightweight start-availability rules allow the
    user to request a Job
-   **THEN** Operator invokes the existing StartJob path and leaves the
    full preflight to Runtime
-   **AND** an actual rejection is shown using the returned result
-   **AND** the UI does not add an all-devices-connected prerequisite or
    perform the same full preflight before the request

#### Scenario: The last execution slot is occupied and then released

-   **WHEN** another workflow occupies the only available execution slot
    and the user selects an otherwise idle workflow
-   **THEN** Operator makes that workflow's start control unavailable and
    reports the Runtime concurrency reason
-   **AND** the existing post-load auto-start callback does not submit a
    start for a workflow blocked by this check
-   **AND** when the slot is released, the next normal status refresh
    enables manual start if the other start conditions hold, without
    replaying a previously skipped request

### Requirement: Existing project recovery and auto-start policy remain authoritative

Operator SHALL preserve the existing project recovery and formal
`production.autoStart` behavior. It MUST NOT introduce a duplicate local
auto-start setting or a second execution policy.

#### Scenario: Restore a project with auto-start disabled

-   **WHEN** Operator restores and loads a project whose formal
    `production.autoStart` is false
-   **THEN** the project is loaded according to the existing recovery
    path
-   **AND** no Job is started automatically

#### Scenario: Restore a project with auto-start enabled

-   **WHEN** Operator restores and loads a project whose formal
    `production.autoStart` is true
-   **THEN** the existing start path is invoked only after project
    loading and current start-availability rules permit it
-   **AND** the full `StartJob` preflight remains authoritative

### Requirement: Formal project configuration is owned by Runtime

Runtime SHALL validate and apply formal project configuration through
its existing configuration owner. Draft editing remains in
Designer/Authoring; Operator SHALL NOT silently publish an unconfirmed
draft or introduce a new implicit Job-start path during import or
configuration commit. A host project-load callback SHALL retain the
existing production.autoStart behavior only where that existing policy
applies.

#### Scenario: A project is loaded or updated

-   **WHEN** a formal project is loaded or an update package is applied
-   **THEN** Runtime validates the project and declared resources before
    adopting the configuration
-   **AND** active execution and resource transitions follow the
    existing configuration contract

#### Scenario: A local update package sets the project's auto-start policy

-   **WHEN** Operator applies a valid update package for a single-workflow
    project and the existing host load callback completes
-   **THEN** it creates zero Jobs if the updated production.autoStart is
    false, and exactly one if it is true and Runtime permits the start
-   **AND** the package commit does not issue a separate start request

### Requirement: Job and workflow execution use the existing Runtime scheduler

Runtime SHALL execute workflows through the existing JobSupervisor,
worker, WorkflowRunner, OperatorExecutor and Dispatch mechanisms.
Operator MUST show true Job identity and status and MUST NOT infer
active work from a local UI session alone. The Operator log workbench
SHALL be observation-only. Its selections SHALL leave production start
and stop controls and their target identities with the production page.

#### Scenario: Multiple workflows run concurrently

-   **WHEN** multiple workflows or Jobs are active within configured
    concurrency limits
-   **THEN** each Job remains independently identifiable and observable
-   **AND** stopping one selected Job does not stop unrelated Jobs

#### Scenario: A user views another Job

-   **WHEN** a user selects an existing Job for observation
-   **THEN** the view attaches to that Job's actual presentation and
    capture identity
-   **AND** the selected view does not silently change the current stop
    target

#### Scenario: Reading Job B logs preserves the production stop target A

-   **WHEN** Jobs A and B of the same workflow are running, A is the
    current control target, and the user selects B in the log workbench
-   **THEN** Operator shows B's records and offers no Job start or stop
    control on the log workbench
-   **AND** on return to the production page its existing stop control
    still targets A by default
-   **AND** explicitly stopping A through that control retires A under
    the existing lifecycle and B continues running

#### Scenario: A Job is stopped

-   **WHEN** a stop request is accepted
-   **THEN** the UI distinguishes request acceptance, terminal Job
    status and actual worker/resource retirement
-   **AND** it does not claim resource retirement solely from a terminal
    status

### Requirement: Device and communication state remains Runtime-owned

Runtime SHALL manage registered device definitions, lifecycle,
communication services, device operations and diagnostics through the
existing DeviceManager and communication services. Operator device
management SHALL use published configuration and SHALL NOT become a
second device-definition editor.

#### Scenario: Operator inspects a published device

-   **WHEN** an operator selects a device
-   **THEN** displayed type, current state and available actions come
    from the current Runtime/project scope
-   **AND** the UI does not invent actions absent from the registered
    type or current state

#### Scenario: Device diagnostics are opened from a Job event

-   **WHEN** a user opens device diagnostics from a Job event
-   **THEN** the request is scoped to the matching Runtime instance,
    project load generation and device
-   **AND** the UI explains when the result is a recent device cache
    rather than complete persistent Job history

### Requirement: Runtime presentation and history preserve source identity

Runtime SHALL expose live presentation, captured results and history
through the existing PresentationService and history APIs. Viewing or
querying results MUST NOT create a new production Job or change the
active control target. Returning to the current Job SHALL restore its
previously selected project page by page identity when that page remains
available.

#### Scenario: View a selected Job

-   **WHEN** a user views a selected Job or Dispatch pair
-   **THEN** the page uses the actual Runtime, Job, capture coverage and
    Presentation revision
-   **AND** the observation session is independent of the Job execution
    lifecycle

#### Scenario: Return to the original Job's second page

-   **WHEN** the user is on the second configured production page of
    current Job A, views Job B on the first page, then returns to A
-   **THEN** Operator restores the same second page by its pageId and
    displays A's results under A's observation context
-   **AND** B's page selection does not overwrite A's page selection

#### Scenario: Query historical results

-   **WHEN** a user queries historical data or images
-   **THEN** the query uses declared project/history identity and
    existing server-side pagination
-   **AND** partial results are not presented as a complete execution
    record

### Requirement: Runtime observability keeps distinct data sources

Runtime SHALL keep Job execution events, Runtime system events, local
operational/application JSONL logs and recent device diagnostics
distinguishable. The Operator log tool SHALL offer a selectable Runtime
system-event view independent of the Job view and local-file entry.
Designer-local logs SHALL remain a separate Designer source.

#### Scenario: Inspect a Job execution

-   **WHEN** a user opens a Runtime Job
-   **THEN** Operator can use the shared Runtime Job workbench to
    inspect execution structure, loop iterations, readable node results
    and raw event data
-   **AND** the workbench reads the same Runtime Job event source as
    Designer

#### Scenario: Inspect local Runtime JSONL logs

-   **WHEN** a user selects local Runtime logs
-   **THEN** Operator preserves both existing operational and
    application JSONL sources and their fallback/listing behavior
-   **AND** neither source is misrepresented as complete Job execution
    history

### Requirement: Verification follows confirmed gaps and the actual change impact

This change SHALL retain the Runtime-wide ownership and behavior
contracts while limiting implementation to confirmed gaps and regression
verification to affected boundaries. Delivery SHALL include a
representative native Qt and software Runtime daily operation path.
Unmodified, unaffected Runtime domains MUST NOT become unconditional
reimplementation or new-test workstreams merely because they appear in
the overall capability map.

#### Scenario: Verify the integrated daily operation path

-   **WHEN** the Operator visual shell and shared Job workbench are
    delivered
-   **THEN** native Qt interaction with a software Runtime covers project
    loading, starting, observing another Job, logs and device diagnostics,
    returning to the original context, stopping the intended Job and
    normal host exit
-   **AND** the existing project, resource-owner and cancellation
    contracts are preserved
-   **AND** HTML interaction alone is not recorded as product acceptance

#### Scenario: Retain an unaffected Runtime capability

-   **WHEN** an existing Runtime domain has no code or contract changes
    and no confirmed defect in this change
-   **THEN** its authoritative source and applicable existing evidence
    can be referenced without creating a new exhaustive test suite
-   **AND** checks not rerun in this change are identified as such rather
    than reported as current PASS results
-   **AND** any additional check is tied to a concrete affected boundary,
    observed defect or existing required gate

#### Scenario: Determine whether Web needs additional regression checks

-   **WHEN** the actual patch is reviewed for client impact
-   **THEN** Web-specific regression checks are required only if Web code
    or a contract used by Web is affected
-   **AND** otherwise its existing contract remains authoritative without
    a new mandatory three-client acceptance matrix
-   **AND** the affected Designer shared workbench and Operator integration
    still receive their required regression checks
