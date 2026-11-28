## MODIFIED Requirements

### Requirement: Runtime and Designer logs are separate top-level sources

Designer 日志窗口 SHALL 只提供 Runtime 运行日志与 Designer 本机日志两个一级来源，先依据 `originApp` 或可信生产者来源分类，再判断 Runtime 记录的 Job 归属。运行任务 SHALL 是 Runtime 来源内部的主要视图。这个两类一级来源约定限定于 Designer 宿主；Operator 的来源由其宿主接入要求单独规定。

#### Scenario: Open the log window

- **WHEN** 用户打开 Designer 日志窗口
- **THEN** 用户可以选择 Runtime 运行日志或 Designer 本机日志
- **AND** Runtime 运行任务和 Runtime 系统事件属于同一来源
- **AND** Designer 本机日志使用 Designer 自身记录

#### Scenario: Inspect a Designer event carrying a Job ID

- **WHEN** Designer 本机事件携带 Job、工作流或节点标识
- **THEN** 该事件仍属于 Designer 本机日志
- **AND** 不仅因携带 Job ID 就将其归为 Runtime 任务事件

#### Scenario: Runtime system events exclude Job-owned events

- **WHEN** 用户查看 Runtime 系统事件
- **THEN** 只显示没有真实 Job 归属的 Runtime 事件
- **AND** `__runtime__` 分区中 payload 明确携带真实 Job ID 的事件仍归属对应 Job
- **AND** 客户端过滤后的空可见页仍按服务端游标与 `hasMore` 处理，不被误判为历史结束

## ADDED Requirements

### Requirement: Operator reuses the shared Runtime Job workbench

Operator SHALL expose the shared Job workbench, Runtime system events
and existing local JSONL files within `logView`. The workbench SHALL
reuse Designer's Job directory, tree, loop, timeline and event detail
views. The Operator directory SHALL be observation-only. Production
controls SHALL own Job stop actions.

#### Scenario: Open the Operator log tool

-   **WHEN** the user opens Operator's existing log tool
-   **THEN** Runtime Job events, Runtime system events and the existing
    local Runtime JSONL files remain identifiable by their actual source
-   **AND** the Designer-only two-source selector is not copied into
    Operator
-   **AND** this integration does not add Designer editing or
    single-operator-debug controls to Operator
-   **AND** Operator reads these sources independently of Designer

#### Scenario: Select a Job in Operator

-   **WHEN** the user selects a different Job
-   **THEN** the event list, execution tree, selected event, readable
    detail and raw JSON all update to the selected Job
-   **AND** the selected Job identity is shown explicitly
-   **AND** the current stop target remains independent from the viewed
    Job

#### Scenario: Inspect a second active Job from the Operator log directory

-   **WHEN** Job A is the production control target and the user selects
    another active Job B whose Runtime actions include stop
-   **THEN** the directory shows B's logs without displaying or wiring
    a stop action for B
-   **AND** both Jobs keep running while the user reads
-   **AND** after returning to production controls, explicitly stopping
    A leaves the unrelated B running

#### Scenario: Switch readable and raw details

-   **WHEN** the user switches between readable details and raw JSON for
    an event
-   **THEN** both views represent the same selected event and the same
    received data
-   **AND** switching views does not change the selected Job, event,
    filter or reading position

### Requirement: Shared workbench modules do not depend on the Designer application

Shared log models, query adapters and Qt views SHALL be independent of
`apps.designer`. Extraction SHALL cover only Operator's actual dependencies
and preserve existing types, interfaces and behavior. Hosts SHALL use
existing callbacks or thin adapters. This change MUST NOT migrate the
complete Designer RuntimeClient or add a generic SDK, renderer registry
or event protocol.

#### Scenario: Operator opens Runtime logs without Designer

-   **WHEN** the standalone Operator application starts and opens
    Runtime logs
-   **THEN** it loads shared Runtime log components without importing
    `apps.designer`
-   **AND** its required Runtime calls, event normalization and operator
    lookup are provided by the Operator host adapter using the existing
    RPC contracts

#### Scenario: Complete the minimal shared extraction

-   **WHEN** the Operator workbench can use the necessary shared modules
    independently and Designer's existing behavior is preserved
-   **THEN** no further class or package splitting is required solely to
    match a preferred layering scheme
-   **AND** an existing module can be moved intact or retained through a
    thin compatibility import when this preserves the dependency boundary

#### Scenario: Designer uses the shared components

-   **WHEN** Designer opens its existing Runtime log workbench
-   **THEN** it uses the shared components through its existing host
    adapter
-   **AND** its visible layout, event reading, filters, details, export
    and debug behavior remain unchanged
-   **AND** Designer retains its permitted directory stop action and
    checks the selected Job identity before sending the command

### Requirement: Runtime log reading uses one source and preserves live cursor behavior

Within one host and Runtime connection scope, the workbench SHALL reuse
one live-query driver with existing cursor, progress, reset/reconnect,
bounded-window, historical upper-bound, paging, cancellation and merge
semantics. Different hosts MAY observe independently. Integration MUST
preserve Runtime's EventStore-to-JSONL projection and MUST NOT add
duplicate appends, client persistence, a second store or cross-process
query coordination.

#### Scenario: Reconnect the Runtime log reader

-   **WHEN** the Runtime client reconnects or its event cursor resets
-   **THEN** the shared reader follows the existing reset/reconnect
    contract
-   **AND** it does not append duplicate copies of the same Job events

#### Scenario: Designer and Operator observe concurrently

-   **WHEN** Designer and Operator open the same Runtime's Job logs
-   **THEN** each host uses its own existing query lifecycle and reading
    state against the same authoritative Runtime data
-   **AND** closing one host's observation does not cancel the other
    host's reader or the production Job
-   **AND** concurrent observation does not require a new shared daemon,
    cross-process lock or client event store

### Requirement: Operator preserves both local Runtime JSONL families

Operator SHALL preserve its existing operational writer preference,
application writer fallback and historical file enumeration for both
`runtime-*.jsonl` and `runtime_app-*.jsonl`.

#### Scenario: Browse active and historical local files

-   **WHEN** the user opens local Runtime logs
-   **THEN** the active operational file follows the current
    writer/rotation behavior
-   **AND** historical file selection remains fixed to the selected file
-   **AND** filtering, refresh, recent log summary, export, cancel and
    open-folder actions remain available

### Requirement: Runtime system events are classified by trusted source and Job ownership

Operator SHALL provide a directly selectable system-events view inside
its existing log tool. Opening the view SHALL query Runtime records via
the existing system-event query and paging path. The view SHALL determine
trusted Runtime producer source before determining Job ownership. It
MUST NOT import Designer-local logs as an Operator source. A payload Job
ID alone MUST NOT reclassify a Designer-local event as a Runtime Job event.

#### Scenario: View Runtime system events

-   **WHEN** Runtime has a system event without a Job and an event with
    Job A's identity in the `__runtime__` partition, and the user opens
    system events from the native Operator log tool
-   **THEN** the query displays the unowned event under system events
-   **AND** the user can find the Job-owned event in A's Job view and it
    is absent from system events
-   **AND** an empty visible page after ownership filtering advances by
    the server cursor while `hasMore` is true

### Requirement: Device drill-down preserves the original Job context

Device diagnostics opened from a Job event SHALL use the matching
Runtime, project load generation and device identity. Returning from
diagnostics SHALL restore the original Job, event, filter and reading
position.

#### Scenario: Device cache does not match historical scope

-   **WHEN** the selected historical Job event does not match the
    current device cache scope
-   **THEN** the UI explains that recent device cache cannot be used as
    complete historical diagnostics
-   **AND** the persistent Job event remains viewable

### Requirement: Job export scope is explicit

Operator SHALL export either a snapshot of the selected Job's loaded
events matching the current filters and view scope at request time,
or all retained, queryable events for that Job through the upper bound
fixed at export start. Both scopes SHALL reuse the existing writer.
Whole-Job export SHALL preserve server paging and cancellation.
Designer SHALL retain its existing whole-Job export.

#### Scenario: Export a Job with active filters

-   **WHEN** Jobs A and B each have a WARN event, A has another retained
    event outside the loaded window, and the user filters A to WARN and
    saves a file for each scope
-   **THEN** the filtered file contains exactly the IDs matching A's
    loaded window when the export was requested
-   **AND** the whole-Job file contains all queryable A event IDs
    through the fixed upper bound, including the unloaded event
-   **AND** the export result identifies the selected scope and both
    files exclude B's events
-   **AND** whole-Job export excludes events after the bound and its
    scope description excludes pruned and never-recorded data
-   **AND** cancellation or failure is not reported as a successful export
