# Runtime Background Task Service

## ADDED Requirements

### Requirement: Plugins explicitly declare permission to use background tasks

A plugin manifest MAY declare runtimeCapabilities. The first supported capability MUST be backgroundTasks. This capability MUST permit submitting, waiting for, and querying Runtime-managed tasks. A manifest without the field MUST be treated as declaring no Runtime background capabilities. Runtime MUST expose the background task client only to an operator whose validated PluginManifest declares backgroundTasks.

#### Scenario: A plugin declares backgroundTasks

- **WHEN** a manifest contains runtimeCapabilities with backgroundTasks
- **THEN** registration MUST preserve the declaration in PluginManifest
- **AND** the operator execution context MUST expose the background task client in supported execution modes

#### Scenario: A legacy manifest omits runtimeCapabilities

- **WHEN** a manifest has no runtimeCapabilities field
- **THEN** it MUST remain valid and behave as an empty capability list
- **AND** its operator MUST NOT receive the background task client

#### Scenario: A manifest declares an unsupported capability

- **WHEN** runtimeCapabilities contains an unknown value, a duplicate, or a non-string item
- **THEN** manifest validation MUST reject that operator with a specific validation issue

#### Scenario: Capability is not declared

- **WHEN** an operator attempts to access backgroundTasks without declaring the capability
- **THEN** the runtime context MUST NOT provide the client
- **AND** the operator MUST continue to work normally if it uses only its existing synchronous behavior

### Requirement: Runtime exposes a Job-scoped background task capability to declared operators

Runtime MUST provide a background task client through the per-invocation operator runtime context during supported Job execution. The client MUST submit work to a service owned by the current Job worker process. Operators MUST NOT create or manage the service thread themselves.

#### Scenario: Multiple operators submit work within one Job

- **WHEN** two or more declared operators in the same Job submit background tasks
- **THEN** their tasks MUST use the same Job-scoped service and FIFO worker
- **AND** task ownership MUST remain within that Job process

#### Scenario: Different Jobs run concurrently

- **WHEN** two Jobs execute in separate worker processes
- **THEN** each Job MUST own an independent background task service
- **AND** no task or Python object MUST be transferred between Job processes by this service

### Requirement: Submission and completion are distinct states

The service MUST return a task identifier when a task is accepted. Acceptance MUST NOT be represented as successful task completion. The operator client MUST support waiting for a task result by identifier and querying its current state.

#### Scenario: Operator submits a task without waiting

- **WHEN** an operator submits a task and returns to the workflow
- **THEN** the returned task identifier MUST identify an accepted task
- **AND** the workflow MAY continue without waiting for the task to finish

#### Scenario: A later operator requires the task result

- **WHEN** an operator waits for a previously submitted task identifier
- **THEN** the call MUST wait for that task to reach a terminal state
- **AND** it MUST return the task result on success or raise a task error on failure
- **AND** the wait MUST respond to the current Job cancellation

#### Scenario: A task record has expired

- **WHEN** a caller queries or waits for a task whose terminal record has been evicted by the bounded retention policy
- **THEN** Runtime MUST return E_BACKGROUND_TASK_EXPIRED
- **AND** it MUST NOT infer success from the missing record

### Requirement: Background task execution is bounded and does not silently discard accepted work

The first implementation MUST use one FIFO worker thread per Job and a bounded pending queue constrained by both task count and estimated pending payload bytes. A full queue MUST apply backpressure to the submitting operator, with cancellation support. A single task exceeding the configured byte limit MUST fail explicitly. Accepted tasks MUST NOT be silently dropped or automatically retried. Terminal task records MUST also have bounded retention; running tasks and records pinned by pending artifact/binding dependencies MUST NOT be evicted.

#### Scenario: Queue reaches capacity

- **WHEN** a producer submits while the pending queue is full
- **THEN** submission MUST wait for capacity or cancellation
- **AND** it MUST NOT report the task as accepted before it is enqueued

#### Scenario: Task raises an exception

- **WHEN** a background task raises an exception
- **THEN** the task MUST enter FAILED state and publish a Runtime failure event with its task ID and captured source identity
- **AND** waiting for the task MUST raise a task error that preserves the original cause

#### Scenario: A task exceeds the byte budget

- **WHEN** one submitted task's estimated payload exceeds the configured per-task limit
- **THEN** submission MUST fail with E_BACKGROUND_TASK_TOO_LARGE
- **AND** it MUST NOT wait forever or exceed the service memory budget

#### Scenario: A task function recursively waits on the same service

- **WHEN** a running background task attempts to submit or wait for another task on the same single-worker service
- **THEN** Runtime MUST reject the recursive operation rather than deadlock the worker

#### Scenario: Service close abandons an active task

- **WHEN** service close reaches its drain deadline while a task is still active
- **THEN** Runtime MUST mark the task abandoned and reject any later TaskControl.publish finalization
- **AND** a normal Job MUST fail with E_BACKGROUND_TASK_DRAIN_TIMEOUT rather than publish job.completed

### Requirement: Job lifecycle owns all accepted background tasks

The Job worker MUST close the service before publishing its terminal Job event. Normal completion MUST drain accepted tasks and process all resulting task events and deferred artifacts before the terminal event. Cancellation or an existing execution failure MUST stop admission, cancel tasks that have not started, and allow the active task to finish within the existing Job graceful-stop policy.

#### Scenario: Job completes normally with pending tasks

- **WHEN** the workflow graph finishes while accepted background tasks remain queued or running
- **THEN** the Job MUST wait for those tasks to finish before publishing job.completed
- **AND** Runner MUST process all task completion records and deferred artifacts before job.completed
- **AND** any unobserved task failure MUST cause a FAILED Job terminal with E_BACKGROUND_TASK_FAILED

#### Scenario: Job is cancelled while tasks are queued

- **WHEN** Job cancellation is requested
- **THEN** the service MUST reject new submissions and cancel tasks that have not started
- **AND** an already-running task MUST be allowed to finish within the existing graceful-stop timeout
- **AND** if the existing Supervisor force-terminates the worker after that timeout, Runtime MUST NOT publish the task or file as successfully completed

#### Scenario: Job already has a primary workflow failure

- **WHEN** background cleanup also reports a failure
- **THEN** the original workflow failure MUST remain the primary error
- **AND** background cleanup details MUST be attached without replacing it

### Requirement: Task events preserve the originating operator identity

The service MUST capture the submitting invocation identity at submission time. Completion and failure events MUST use that captured identity rather than a logger or mutable context that may already have been closed. WorkflowRunner MUST process task completion records on its Runtime execution thread rather than invoking Runner, ResultCollector, ArtifactStore, or preview objects directly from the background worker thread.

#### Scenario: Task finishes after executeNode returns

- **WHEN** a task completes after the submitting operator has returned
- **THEN** the completion event MUST retain the original Job, workflow, node and node-run identity
- **AND** it MUST NOT depend on the submitting OperatorLogger remaining open

#### Scenario: Task completes while another node is running

- **WHEN** a background task completes while the main workflow thread is executing another node
- **THEN** its completion record MAY be processed at the next node boundary
- **AND** all completion records MUST be processed before the Job terminal event

#### Scenario: An unobserved task fails in a continuous Job

- **WHEN** a background task fails and no operator has waited for/handled that failure
- **THEN** Runner MUST detect it at the next node boundary, next workflow-run start, or before workflow completion
- **AND** the active execution MUST fail with E_BACKGROUND_TASK_FAILED without waiting for the continuous Job to be manually stopped

#### Scenario: A task fails while the Job is between workflow cycles or waiting for a Dispatch invocation

- **WHEN** an unobserved task fails while the worker is in a cycle delay or Dispatch IDLE wait
- **THEN** the worker MUST poll the task service during that idle period
- **AND** the Job MUST fail without waiting indefinitely for another cycle or external dispatch

### Requirement: ImageSaver supports opt-in background writing without changing its default behavior

ImageSaver MUST declare backgroundTasks in its manifest and retain synchronous saving as its default. Its opt-in background mode MUST use unique naming, write to a task-specific temporary file, atomically publish the final file only after a complete write, return an accepted task ID and pending state, and MUST only report saved success after the write task succeeds.

#### Scenario: Existing project uses default ImageSaver settings

- **WHEN** an existing project runs ImageSaver without selecting background mode
- **THEN** it MUST retain the existing synchronous write and result behavior

#### Scenario: ImageSaver uses background mode

- **WHEN** ImageSaver submits a copied image for background encoding and writing
- **THEN** it MUST return accepted=true, saved=false, state=SUBMITTED, the target path and task ID
- **AND** it MUST NOT claim the file already exists or has been saved

#### Scenario: Background image write completes

- **WHEN** the ImageSaver task succeeds
- **THEN** Runtime MUST publish the corresponding task completion
- **AND** the existing artifact registration and preview path MUST run only after the file is ready

#### Scenario: Background image write fails

- **WHEN** the ImageSaver task fails
- **THEN** Runtime MUST publish the task failure and MUST NOT publish artifact.created for that path

#### Scenario: Background ImageSaver is configured with fixed naming

- **WHEN** saveMode is background and namingMode is fixed
- **THEN** ImageSaver MUST return a clear parameter error
- **AND** synchronous fixed naming MUST remain supported

#### Scenario: Background image write is interrupted before publication

- **WHEN** a background image write fails or is terminated before its temporary file is complete
- **THEN** the final output path MUST NOT expose a partial image
- **AND** Runtime MUST NOT publish saved success or artifact.created

#### Scenario: Close timeout occurs while image encoding is active

- **WHEN** service close abandons an ImageSaver task before final publication
- **THEN** its TaskControl.publish operation MUST be rejected
- **AND** the task MUST NOT create the final path after the Job has entered terminal cleanup

### Requirement: Deferred file artifacts are registered only after their task succeeds

WorkflowRunner MUST recognize the explicit pending-artifact output contract and defer ArtifactStore registration, checksum calculation, artifact.created publication, and artifact preview capture until the corresponding task succeeds. Completion handling MUST run on the Runtime execution thread.

#### Scenario: Pending artifact task succeeds

- **WHEN** a pending ImageSaver artifact task succeeds
- **THEN** Runner MUST register the real file through the existing ArtifactStore
- **AND** Runner MUST publish artifact.created and capture the artifact only after registration

#### Scenario: Pending artifact task fails or is cancelled

- **WHEN** a pending artifact task fails or is cancelled before producing a completed file
- **THEN** Runner MUST NOT publish artifact.created for that pending path

### Requirement: SQLite FILE_REFERENCE bindings wait for pending image files before capture

WorkflowRunner MUST defer captureBoundValue for a pending ImageSaver output used by a SQLite FILE_REFERENCE mapping. It MUST wait for the task before the target SQLiteWriter node executes, then pass the completed saved path through the existing captureBoundValue/toStorage contract.

#### Scenario: SQLite consumes a pending image reference

- **WHEN** a pending ImageSaver output is mapped to a SQLite FILE_REFERENCE field
- **THEN** Runner MUST retain the task ID and binding identity instead of immediately calling captureBoundValue
- **AND** Runner MUST wait for the task before the SQLiteWriter node starts
- **AND** after success it MUST capture a normal saved file reference and preserve the existing SQLiteWriter transaction behavior

#### Scenario: Pending image save fails before SQLite consumes it

- **WHEN** the image save task fails
- **THEN** Runner MUST provide an error BoundValue to the SQLiteWriter mapping
- **AND** SQLiteWriter MUST NOT insert a nonexistent file reference

### Requirement: Pure preview remains free of background side effects

Pure operator preview and clone-safe workflow trial MUST NOT receive a usable background submission capability, even when the plugin manifest declares backgroundTasks.

#### Scenario: Pure preview requests background execution

- **WHEN** an operator is executed through previewMode=pure and requests a background task
- **THEN** the operation MUST be unavailable for pure preview
- **AND** no file-writing task may outlive the preview request

#### Scenario: Workflow debug trial executes a clone-safe operator

- **WHEN** a clone-safe trial runs an operator
- **THEN** it MUST NOT inherit the parent workflow's background task service
