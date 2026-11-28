# Runtime 日志工作台 Operator 收尾

## Purpose

补齐 R3 后 Operator 日志工具的默认入口与 reader 可见生命周期，不改变既有
Job 日志、Runtime 系统事件、本机 JSONL 和 Designer 日志合同。

## ADDED Requirements

### Requirement: Operator opens the Runtime Job workbench by default

Operator SHALL open the existing Runtime Job workbench as the default
source when the user enters the log tool. Resetting Job filters SHALL
return to the Job workbench rather than switching to local JSONL.
Explicit device-log navigation SHALL continue to open the local JSONL
source.

#### Scenario: Open logs from StartupPage

-   **WHEN** the user chooses to view all logs from StartupPage or the
    Operator toolbar
-   **THEN** the log tool opens with the Runtime Job workbench selected
-   **AND** the user can immediately see active and recently ended Job
    groups

#### Scenario: Reset Job filters

-   **WHEN** the user resets filters from the Job workbench
-   **THEN** Job filters are reset
-   **AND** the selected source remains the Runtime Job workbench

#### Scenario: Open device logs explicitly

-   **WHEN** an existing device-log action calls the Operator log view
    with a device ID
-   **THEN** the local JSONL source is selected and filtered by that
    device ID
-   **AND** the default Job source rule is not applied over this
    explicit navigation

### Requirement: Operator Runtime Job reader follows source and tool visibility

The Operator Job event reader SHALL run only while the log tool is
visible and the Runtime Job source is selected. When the user leaves the
Job source, hides the log tool or closes the Operator, the current
reader SHALL be asked to stop. The host SHALL preserve the last
confirmed Runtime instance and cursor and SHALL NOT start a second
reader until the previous reader has finished.

#### Scenario: Leave the Job source

-   **WHEN** the user switches from Runtime Job logs to Runtime system
    events or local JSONL
-   **THEN** the Job reader stops issuing new Runtime queries after the
    current in-flight call is cancelled or completed
-   **AND** queued events/progress are drained before the reader is
    retired
-   **AND** the local JSONL/system event view remains usable

#### Scenario: Hide and reopen the log tool

-   **WHEN** the user returns to the production page while the Job
    reader is active
-   **THEN** the Job reader is asked to stop and its last confirmed
    Runtime instance/cursor is retained
-   **AND** no new Job reader is created while the previous reader
    remains active

#### Scenario: Resume the Job source

-   **WHEN** the user reopens the Job source after the previous reader
    has finished
-   **THEN** the reader resumes from the retained Runtime instance and
    cursor
-   **AND** already delivered event IDs are not duplicated
-   **AND** events after the retained cursor are not skipped

#### Scenario: An export is active while the Job source is hidden

-   **WHEN** the user hides the Job workbench while a user-requested
    export is in progress
-   **THEN** reader pause does not silently cancel the export
-   **AND** the existing export cancellation/close contract remains
    authoritative

### Requirement: Shared Runtime log connection notices use neutral event identity

The shared Runtime log reader SHALL identify its local connection,
disconnection, recovery and stop notices with a Runtime-neutral event
type. It MUST NOT label the shared Runtime reader's notices as
Designer-only events.

#### Scenario: Runtime log reader disconnects and recovers

-   **WHEN** the shared reader reports connection, disconnection or
    recovery
-   **THEN** the local notice uses `runtime.logs.connection`
-   **AND** the existing level, error code, payload and retry behavior
    remain unchanged
