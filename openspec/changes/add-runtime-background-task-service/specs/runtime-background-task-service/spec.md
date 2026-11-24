# Runtime Background Task Service

## ADDED Requirements

### Requirement: Plugins declare background task capability

A plugin manifest MAY declare `runtimeCapabilities`; the first supported value is `backgroundTasks`. Missing field means no capability. Runtime MUST expose the client only to operators whose validated PluginManifest declares it.

#### Scenario: Legacy and declared manifests

- **WHEN** a legacy manifest omits runtimeCapabilities or ImageSaver declares backgroundTasks
- **THEN** the legacy plugin remains valid without the client, and ImageSaver receives the client in supported execution modes

#### Scenario: Invalid capability declaration

- **WHEN** the declaration contains an unknown value, duplicate, or non-string item
- **THEN** the operator is rejected with a manifest validation issue

### Requirement: A Job owns one shared background worker

Each Job worker process MUST own one FIFO background worker and bounded queue. Operators in the same Job share it; different Job processes MUST NOT share task objects or threads. Operators MUST NOT create or manage worker threads themselves.

#### Scenario: Same Job and separate Jobs

- **WHEN** two operators in one Job submit tasks, or two independent Jobs submit tasks
- **THEN** the first pair uses the same worker, while each independent Job uses its own worker

### Requirement: Submission is distinct from task completion

The client MUST provide submit, wait, and status. A successful submit only means the task was accepted. Task failures MUST remain observable through wait and Runner polling. Queue capacity MUST be bounded by task count and estimated payload bytes.

#### Scenario: Non-blocking submit and explicit wait

- **WHEN** an operator submits a task while capacity is available
- **THEN** it receives a task ID without waiting for the task function to finish; a later wait returns its result or raises its error

#### Scenario: Queue capacity is reached

- **WHEN** the queue is full or payload budget is exhausted
- **THEN** submit waits for capacity or cancellation; an individually oversized task fails explicitly

### Requirement: Runtime controls task lifetime and final publication

Runtime MUST inject TaskControl into accepted task functions. TaskControl MUST support cancellation checks and a short guarded final-publication operation. Service close and final publication MUST be serialized so an abandoned task cannot publish after close.

#### Scenario: Task finalizes a file

- **WHEN** a task has completed writing its temporary file
- **THEN** it publishes the final path through TaskControl while the service remains active

#### Scenario: Service drain times out

- **WHEN** the internal normal-drain deadline expires
- **THEN** the service becomes abandoned, late final publication is rejected, and normal Job completion fails rather than reporting success

### Requirement: Runtime registers asynchronous artifacts after completion

Runner MUST process task completion on the Runtime execution thread. It MUST register the artifact and publish artifact.created only after the file exists and the task succeeds. Job terminal publication MUST occur after pending tasks and artifacts are drained.

#### Scenario: Artifact success and failure

- **WHEN** an ImageSaver task succeeds or fails
- **THEN** success is registered and previewed after the write; failure produces no successful artifact

### Requirement: SQLite file references wait for pending image writes

Runner MUST defer captureBoundValue for pending ImageSaver outputs mapped to SQLite FILE_REFERENCE. It MUST wait before SQLiteWriter executes, process the completed artifact, then capture the existing saved file reference. Failure MUST flow through the existing SQLite failure policy without inserting a missing file.

#### Scenario: SQLite consumes pending image

- **WHEN** a pending image output is mapped to FILE_REFERENCE
- **THEN** SQLiteWriter starts only after the file task succeeds and the normal BoundValue is created

### Requirement: Debug and preview execution respect capability boundaries

Single-operator and workflow debug MUST receive capability declarations only from Runtime-validated manifests. PurePreview and clone-safe trial MUST NOT receive a usable background submission client.

#### Scenario: Debug and pure preview

- **WHEN** a declared operator runs in supported debug mode or in PurePreview/clone-safe trial
- **THEN** supported debug receives the client, while pure modes remain unable to submit side effects
