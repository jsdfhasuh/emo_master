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

### Requirement: Configuration drain preserves accepted writes

When applying a configuration with `drainAffected`, Runtime MUST account for already accepted background tasks of retiring Jobs within the existing transition deadline. User-requested Stop MUST keep its existing cancellation semantics.

#### Scenario: A configuration change arrives while a save is still running

- **WHEN** a continuous Job has finished its workflow cycle and an accepted image save is still writing
- **THEN** a successful configuration drain allows that write to finish and its successful artifact to be handled before the Job retires

#### Scenario: The PNG exists while artifact registration is still running

- **WHEN** a retiring Job has published the image and its Runner is still registering the artifact within the configuration drain deadline
- **THEN** the configuration transition waits for registration and publication of `artifact.created` before requesting the Job to stop, including at a cycle boundary

### Requirement: Dispatch delivers file results owned by the sending Job

Runtime MUST resolve submitted file receipts in Dispatch inputs and consumer outputs on the sending Job before serialization, including receipts nested in containers. Each consumer invocation MUST settle its accepted work, process artifacts and report unobserved failures before confirming its delivery.

#### Scenario: A file result passes through another Job

- **WHEN** a sender passes a submitted image result through Dispatch or returns it to Join
- **THEN** the receiver gets a saved file result readable without querying the sender's task service

#### Scenario: An invocation fails after its last node returns

- **WHEN** a consumer's background write fails while another delivery is queued
- **THEN** the failure is reported for the submitting delivery before it can be acknowledged as completed

#### Scenario: A loop collects more files than the task history retains

- **WHEN** a workflow passes a collection of its registered file outputs to Dispatch after older task records have expired
- **THEN** those retained workflow values still yield confirmed file results and a separate status lookup by an expired ID reports its expiration

### Requirement: Background results reach the owning execution

Runtime MUST poll for background failures during a While iteration interval. Designer MUST attach a saved artifact to its matching retained execution whether the artifact arrives before or after the node completion event.

#### Scenario: A write fails during a While interval

- **WHEN** a write fails during the interval before the next iteration
- **THEN** the loop observes that failure during its wait and preserves its source, including when the next iteration is beyond the loop timeout

#### Scenario: A delayed artifact belongs to an earlier invocation

- **WHEN** a retained execution receives its artifact after node completion or after the same node has run again
- **THEN** Designer exposes the saved image on that execution and keeps the other invocation's image independent

### Requirement: A failed Job gives active file writes bounded cleanup time

Runtime MUST close the publication gate and cancel queued work on ordinary Job failure, then give an active task a bounded opportunity to clean its temporary file. The original workflow failure MUST remain the primary error.

#### Scenario: A downstream node fails during temporary-file cleanup

- **WHEN** a cooperative file task owns a temporary file as the Job fails
- **THEN** the task can release that file before worker exit and cannot publish a new final file after the gate closes

### Requirement: A background image save publishes exclusively to its accepted path

ImageSaver MUST publish a complete image atomically to the path in its submission receipt. If another writer has already created that path, the task MUST preserve the existing file, report `E_OUTPUT_EXISTS` for the originating save, and produce no successful artifact.

#### Scenario: Another writer claims the final path during encoding

- **WHEN** another writer creates the accepted path after submission and before final publication
- **THEN** the existing bytes are preserved and the save fails at that path

### Requirement: Worker retirement reclaims interrupted image staging files

Runtime MUST reclaim each managed temporary image left by a Job or debug worker after confirming its process has exited. Cleanup MUST preserve published files and files owned by other writers.

#### Scenario: A worker is terminated during an image save

- **WHEN** a worker has staged image bytes and is forcibly stopped before publishing them
- **THEN** its temporary image is removed at retirement and the interrupted save creates no final file

#### Scenario: A failed Job exits before the writer can clean up

- **WHEN** a downstream error ends a Job and an active image writer exceeds the cleanup deadline
- **THEN** retirement removes the abandoned temporary image while the Job retains its original error and cleanup diagnostic

### Requirement: Runtime startup recovers abandoned image staging files

Runtime MUST durably register its private staging directory before its first image is created there. On primary startup against the same Runtime data, it MUST discover previously registered directories and remove only abandoned managed staging files, including when the old Runtime and all its workers exited without cleanup. Recovery MUST preserve final images and other writers' files. It MUST NOT publish or replay interrupted saves.

#### Scenario: A power loss or simultaneous process exit interrupts staging

- **WHEN** the data and output volumes return with a durable registration and a staged image but no previous writer
- **THEN** the next Runtime startup removes the staged image, even if the old per-image workspace record is missing

#### Scenario: A local test release starts the image writer through the display service

- **WHEN** a supported project 2.2 test release starts a Job through the display service and both the Runtime and that worker exit during image staging
- **THEN** startup on the same Runtime data reclaims the interrupted staged image and preserves previously published images and other writers' files

#### Scenario: A writer survived or a directory is temporarily unavailable

- **WHEN** an old writer still holds the staging directory lock or cleanup cannot delete a staged file
- **THEN** startup keeps the registration and preserves active writes, and a later startup can finish cleanup when the cause is gone
