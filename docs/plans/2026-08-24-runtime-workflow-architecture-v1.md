# EmoMaster Runtime 工作流架构 v1 实现计划

- 状态：`IMPLEMENTATION_READY`
- 基线分支：`main`
- 基线提交：`7f141ce627da39d9e174e4a79344fc468607ebef`
- 实现分支：`agent/runtime-workflow-architecture-v1`
- 目标版本：`0.2.0`
- 目标性质：运行架构升级，不是单个算子或单项 UI 功能

## 1. 目标

将当前“单项目、单流程、同步顺序执行的一次性 DAG Runtime”升级为：

1. 一个项目包含多张工作流；
2. 可以选择入口工作流启动 Job；
3. 工作流可以调用子工作流；
4. 支持结构化 `Repeat`、`ForEach`、`While`；
5. `StartJob` 立即返回，Job 在后台运行；
6. 每个 Job 在独立子进程中执行；
7. 多个 Job 可以受限并发运行；
8. 支持 graceful stop 和 force terminate；
9. 运行事件可以实时订阅、重放并持久化；
10. Designer 能编辑多工作流并在运行期间保持响应；
11. 旧版 `project.json v1` 可以无损迁移；
12. 当前 Image Loader、Canny、Image Saver、If、Switch 和旧 `executeGraph()` 调用不回退。

本计划是 Luna/Codex 的实现契约。除非代码事实证明某项无法成立，否则不得临时改成另一套运行语义。

---

## 2. 已审查的当前实现与结论

### 2.1 Runtime 仍是单全局实例状态

文件：

- `src/emo_master/apps/runtime/grpc_server/service.py`
- `src/emo_master/apps/runtime/scheduler/job_service.py`
- `src/emo_master/apps/runtime/scheduler/state_machine.py`

当前事实：

- `RuntimeService` 只有一份 `loadedProjectPath`、`loadedGraph` 和 `currentStatus`；
- `StartJob()` 忽略 `request.project_id`，直接运行当前全局 `loadedGraph`；
- `StartJob()` 内同步调用 `_runLoadedGraph()`，执行完成后才返回；
- `StopJob()` 只改状态，没有向执行器发出取消信号；
- force 模式也没有真实终止正在运行的代码；
- `JobService` 只在内存字典中保存状态；
- `PAUSED` 已写入状态机，但没有完整 RPC、调度暂停点和资源一致性保障。

结论：不能在现有 `RuntimeService` 上直接堆 While 或 multiprocessing；必须引入 ProjectRegistry、JobManager、JobSupervisor 和独立 WorkflowRunner。

### 2.2 执行器是严格同步 DAG

文件：

- `src/emo_master/apps/runtime/execution/dag_executor.py`
- `src/emo_master/apps/runtime/execution/models.py`
- `src/emo_master/core/graph/validator.py`

当前事实：

- 先拓扑排序，再 `for nodeId in orderedNodeIds` 顺序执行；
- 遇到图环立即 `cycle detected`；
- 没有取消令牌、超时、节点开始事件和运行回调；
- If/Switch 分支识别硬编码在执行器；
- `parseRuntimeGraph()` 会静默跳过格式错误节点和边；
- 运行结果主要是最终 `nodeStatus/artifacts/branchHits` 汇总。

结论：每张普通工作流继续保持 DAG；循环必须是结构化控制节点，通过重复调用子工作流实现，禁止放开普通回连。

### 2.3 项目存在两套不兼容真相

当前主线：

- `src/emo_master/apps/designer/state/project_store.py`
- `src/emo_master/apps/designer/controllers/project_controller.py`
- 使用 `project.json + assets/ + outputs/`；
- 只有一组 `designer.nodes/edges`。

另一套骨架：

- `src/emo_master/core/project/repository.py`
- `src/emo_master/core/project/package_builder.py`
- 使用 `project.yaml + graph/ + params/ + devices/ + plugins.lock`。

对应测试也分成两套，互不贯通。

结论：本次以 `project.json v2` 为唯一项目源。`ProjectRepository` 与 `package_builder` 必须迁移到 v2；不得继续扩展 YAML 双轨模型。工作目录中的 `plugins.lock` 不再是源数据，发布包可以从 `project.json.dependencies` 派生生成锁文件。

### 2.4 事件与持久化只是骨架

文件：

- `src/emo_master/apps/runtime/events/event_bus.py`
- `src/emo_master/apps/runtime/context/sqlite_store.py`
- `src/emo_master/apps/runtime/context/migrations/001_init.sql`

当前事实：

- EventBus 是 `dict[jobId, list[event]]`；
- 没有锁、Condition、sequence、真实 timestamp、订阅等待和断线续传；
- gRPC `StreamJobEvents` 只遍历已有列表后结束；
- `timestamp_ms` 固定为 0；
- SQLite 表已经存在，但 RuntimeService 没有接入；
- `updateJobStatus()` 不写 `endAt/durationMs/errorMessage`；
- 没有 schema migration 版本表。

结论：实现 EventStore + JobRepository，并让内存实时订阅与 SQLite 持久化共用同一事件模型。

### 2.5 外部 gRPC 契约必须先加固

文件：

- `src/emo_master/apps/designer/services/runtime_client.py`
- `src/emo_master/apps/designer/main.py`
- `proto/runtime.proto`
- `scripts/gen_proto.py`

当前事实：

- RuntimeClient 使用动态 `type(...)` 对象作为请求，本地直接调用 service 可以工作，真实 gRPC stub 需要 protobuf request；
- RuntimeClient 尝试给 protobuf JobEvent 动态增加 `payload` 字段；
- 当前测试多为假 Stub 或进程内 service，没有完整真实网络回归；
- `pyproject.toml` 与生成代码要求的 grpc/protobuf 版本不完全一致；
- 生成的 `runtime_pb2_grpc.py` 依赖顶层兼容 shim。

结论：Phase 0 必须先修复请求 DTO、事件 DTO、生成代码导入、依赖版本和真实 gRPC 测试，否则后续异步事件与多进程问题无法可靠定位。

### 2.6 Designer 是单工作流、同步运行

文件：

- `src/emo_master/apps/designer/state/flow_graph_model.py`
- `src/emo_master/apps/designer/controllers/project_controller.py`
- `src/emo_master/apps/designer/controllers/runtime_controller.py`
- `src/emo_master/apps/designer/ui/main_window.py`

当前事实：

- 主窗口只持有一个 FlowGraphModel 和一个 FlowScene；
- 项目保存恢复只处理一张图；
- RuntimeController 在 UI 主线程同步 Start、GetStatus、读取全部事件；
- `_syncRuntimeProjectBeforeRun()` 的返回值未形成严格启动门禁；
- 节点详情只按 nodeId 保存最终状态，没有 workflowRunId/iteration 上下文。

结论：新增 WorkflowStore、WorkflowController 和 RuntimeWorker；不要继续把多工作流逻辑直接堆进 `main_window.py`。

### 2.7 插件与多进程边界

文件：

- `src/emo_master/core/plugin/registry.py`
- `src/emo_master/core/plugin/validator.py`
- `src/emo_master/core/contracts/operator_protocol.py`

当前事实：

- Runtime 主进程扫描并持有 operator class；
- operator API 是 `executeNode(inputs, params, runtimeContext)`；
- 已有算子依赖 NumPy/OpenCV 对象，不适合通过 multiprocessing Queue 在节点之间传输。

结论：一个 Job 一个进程，而不是一个节点一个进程。子进程根据字符串 plugin roots 重新扫描插件，不能把 class、Qt 对象、gRPC channel 或 lambda 传进 spawn 子进程。保留旧 operator API，通过扩展 runtimeContext 兼容当前算子。

---

## 3. 本次范围

### 3.1 必须实现

- Runtime/gRPC 契约加固；
- `project.json v2` 与 v1 迁移；
- 多工作流模型；
- 工作流接口节点；
- 子工作流调用；
- Repeat、ForEach、While；
- 循环最大轮次、超时与取消检查；
- 异步 Job；
- 真事件流；
- SQLite 作业和事件持久化；
- 一个 Job 一个独立进程；
- graceful/force stop；
- 进程心跳、异常退出检测和回收；
- 多 Job 受限并发；
- Designer 多工作流标签页和后台事件消费；
- 旧功能兼容测试；
- Windows/Ubuntu CI。

### 3.2 明确不实现

- 普通连线形成任意环；
- 一个节点一个进程；
- 节点级并行调度、Fork/Join；
- 分布式多机执行；
- 第三方插件安全沙箱；
- 真正的 Pause/Resume；
- Break/Continue/Return 控制节点；
- 相机、PLC、机器人和 GPU 资源锁；
- SharedMemory 图像池；
- Job 断点恢复；
- 远程认证和 TLS。

节点级并行与资源锁在本架构稳定后单独规划为 v0.3。本次只允许不同 Job 通过不同进程并发，同一工作流内部仍保持确定性的顺序 DAG 执行。

---

## 4. 不可违反的架构约束

1. `core` 不得 import `apps`；
2. protobuf 只能出现在 gRPC client/service 边界，核心层不得依赖 protobuf；
3. 磁盘输入使用 Pydantic v2 严格校验，`extra="forbid"`；
4. 编译后的执行计划使用不可变 dataclass；
5. 每张普通工作流必须是 DAG；
6. 循环只能通过 LoopNode 调用其他工作流；
7. 第一版禁止直接或间接递归子工作流；
8. 每个 Loop 必须有 `maxIterations`，并支持 `timeoutMs`；
9. 每轮循环和每个节点前后都检查 CancellationToken；
10. graceful stop 不承诺中断正在执行的第三方 C 算子；force stop 必须依赖进程终止；
11. multiprocessing 固定使用 `spawn`；
12. 进程参数只能是可序列化 DTO 和字符串路径；
13. 不通过普通 Queue 高频传输 NumPy 图像；
14. RuntimeService 只做 RPC 适配，不再持有全局 loadedGraph/currentStatus；
15. 新代码使用 4 空格缩进，不格式化无关旧代码；
16. 不允许 TODO、假实现、固定 sleep 伪造运行状态或只为测试写死特例。

---

## 5. 目标目录结构

```text
src/emo_master/
├── core/
│   ├── contracts/
│   │   ├── error_codes.py
│   │   ├── execution.py                 # 新增统一结果/错误 DTO
│   │   └── operator_protocol.py
│   ├── project/
│   │   ├── models.py                    # v2 Pydantic 文档模型
│   │   ├── migration.py                 # v1 -> v2
│   │   ├── repository.py                # 改为 project.json v2
│   │   └── package_builder.py           # 改为 v2 发布包
│   └── workflow/
│       ├── models.py                    # Workflow/Node/Edge 定义
│       ├── validation.py                # 图、接口、调用图校验
│       ├── compiler.py                  # 编译不可变执行计划
│       └── errors.py
│
├── apps/runtime/
│   ├── projects/
│   │   ├── registry.py                  # 多项目/版本注册
│   │   └── snapshot.py                  # 不可变 Job 执行快照
│   ├── workflow/
│   │   ├── context.py                   # RunContext/iterationPath
│   │   ├── cancellation.py
│   │   ├── runner.py                    # 工作流顺序 DAG 执行
│   │   ├── subflow_runner.py
│   │   └── loop_runner.py
│   ├── jobs/
│   │   ├── models.py
│   │   ├── manager.py
│   │   ├── repository.py
│   │   ├── supervisor.py
│   │   ├── worker_main.py               # 顶层 spawn 入口
│   │   └── event_bridge.py
│   ├── events/
│   │   ├── models.py
│   │   └── event_store.py
│   ├── artifacts/
│   │   ├── models.py
│   │   └── store.py
│   ├── context/
│   │   ├── migrations/001_init.sql
│   │   ├── migrations/002_runtime_workflow.sql
│   │   └── sqlite_store.py
│   └── grpc_server/service.py           # 收缩为薄适配层
│
└── apps/designer/
    ├── state/
    │   ├── workflow_store.py
    │   ├── flow_graph_model.py
    │   └── project_store.py
    ├── controllers/
    │   ├── workflow_controller.py
    │   ├── project_controller.py
    │   └── runtime_controller.py
    ├── services/
    │   ├── runtime_client.py
    │   └── runtime_worker.py
    └── ui/
        ├── workflow_tabs.py
        ├── param_form.py
        └── main_window.py
```

名称允许在不改变职责边界的前提下微调，但不得把所有新能力放回一个大文件。

---

## 6. `project.json v2` 契约

目标结构：

```json
{
  "schemaVersion": "2.0",
  "project": {
    "projectId": "uuid",
    "name": "demo",
    "revision": 1,
    "createdAt": "ISO-8601",
    "updatedAt": "ISO-8601"
  },
  "entryWorkflowId": "main",
  "workflowOrder": ["main", "inspect_once"],
  "workflows": {
    "main": {
      "name": "主工作流",
      "inputs": {},
      "outputs": {},
      "nodes": [],
      "edges": [],
      "layout": {
        "nodePositions": {}
      }
    }
  },
  "runtime": {
    "maxConcurrentJobs": 2,
    "gracefulStopTimeoutMs": 5000,
    "eventRetentionPerJob": 10000
  },
  "dependencies": {
    "operators": []
  },
  "devices": {
    "bindings": {}
  }
}
```

### 6.1 节点类型

```text
operator
workflow_input
workflow_output
subflow
loop
```

普通 operator 节点：

```json
{
  "nodeId": "canny-1",
  "kind": "operator",
  "operatorId": "vision.edge.canny",
  "params": {}
}
```

子工作流节点：

```json
{
  "nodeId": "call-inspect",
  "kind": "subflow",
  "targetWorkflowId": "inspect_once"
}
```

Subflow 输入、输出端口由目标工作流 `inputs/outputs` 派生，不信任项目文件中重复保存的端口副本。

Loop 节点：

```json
{
  "nodeId": "loop-inspect",
  "kind": "loop",
  "loop": {
    "mode": "repeat",
    "bodyWorkflowId": "inspect_once",
    "maxIterations": 10,
    "timeoutMs": 60000,
    "repeatCount": 3
  }
}
```

### 6.2 工作流边界节点

每张工作流必须有且只有一个 `workflow_input` 和一个 `workflow_output` 核心节点；其端口由工作流接口定义派生。空接口允许零端口。

这样 Designer 可以在画布上连接输入、输出，Runtime 也能明确收集子流程调用参数和返回值。

### 6.3 v1 迁移

- 将旧 `designer.nodes/edges` 包装为 `workflows.main`；
- 所有旧节点默认 `kind="operator"`；
- 旧节点 `x/y` 移入 `layout.nodePositions`；
- 旧 `runtime.sourceImagePath` 作为兼容字段读取但不写入 v2；
- 迁移在内存完成，加载时不得自动覆盖旧文件；
- 用户保存时写 v2，并先创建 `.bak`；
- 保存采用临时文件 + flush + `os.replace()` 原子替换。

### 6.4 处理旧 YAML 项目骨架

- `ProjectRepository` 保留类名以减少调用方破坏，但内部改为 v2 `project.json`；
- 删除新代码对 `project.yaml` 的依赖；
- 原 YAML 测试改为 v2 测试；
- `package_builder` 打包 `project.json`、assets、manifest 和 checksums；
- `plugins.lock` 由 `dependencies.operators` 派生写入包内，不再要求工作目录预先存在。

---

## 7. 工作流编译与校验

新增 `WorkflowCompiler`，执行前必须完成：

1. schemaVersion 校验；
2. projectId、workflowId、nodeId 唯一性；
3. entryWorkflowId 存在；
4. workflowOrder 无重复且覆盖所有工作流；
5. 每张工作流只有一个 input/output 边界节点；
6. edge 引用节点和端口存在；
7. 端口类型兼容；
8. 每个普通输入端口最多一条入边；
9. operator 已注册且版本兼容；
10. operator params 满足 schema 和 `validateParams()`；
11. 每张普通工作流无环；
12. Subflow 目标存在，接口匹配；
13. 工作流调用图无直接或间接递归；
14. Loop body/condition 目标存在并符合接口契约；
15. Repeat count、maxIterations、timeoutMs 合法；
16. ForEach/While 的保留端口和返回类型正确。

禁止像当前 `parseRuntimeGraph()` 一样静默丢弃非法数据。所有错误返回结构化 `ValidationIssue`：

```text
code
message
projectId
workflowId
nodeId
fieldPath
```

编译结果使用 frozen dataclass，并预计算：

```text
nodeById
incomingEdges
outgoingEdges
topologicalOrder
workflowCallGraph
```

---

## 8. 子工作流执行语义

`WorkflowRunner.run()` 输入：

```text
compiledProject
workflowId
inputs
RunContext
CancellationToken
EventPublisher
```

返回：

```text
WorkflowResult(outputs, metrics)
```

RunContext 至少包含：

```text
jobId
workflowId
workflowRunId
parentWorkflowRunId
callerNodeId
nodeRunId
callDepth
iterationPath
```

规则：

- 每次子工作流调用生成新的 workflowRunId；
- 子工作流输入严格匹配目标 inputs；
- 子工作流只返回目标 outputs；
- 最大调用深度默认 32；
- 编译时禁止递归，运行时仍保留深度防线；
- 事件必须携带调用上下文，不能只记录 nodeId。

---

## 9. 循环执行语义

### 9.1 Repeat

- 必填：`bodyWorkflowId`、`repeatCount`、`maxIterations`；
- 每轮输入 = Loop 节点输入 + `__iteration__`；
- 下一轮默认继续使用原始共享输入；
- Loop 输出采用最后一轮 body outputs；
- repeatCount 不得超过 maxIterations；
- repeatCount=0 允许直接返回空结果，但必须有明确定义的默认输出。

### 9.2 ForEach

- Loop 输入必须有 `items:list`；
- body 工作流必须接受 `item:object` 和 `index:integer`，可额外接受 shared 输入；
- 每轮输出追加到 `results:list[json]`；
- 顺序执行，保证结果顺序与 items 一致；
- items 长度不得超过 maxIterations。

### 9.3 While

While 使用独立 condition workflow 与 body workflow：

```text
conditionWorkflowId
bodyWorkflowId
maxIterations
timeoutMs
```

契约：

- Loop 输入和输出均为 `state:object`；
- condition workflow 输入 `state:object`，输出 `continue:boolean`；
- body workflow 输入 `state:object` 和 `__iteration__:integer`，输出 `state:object`；
- 每轮先执行 condition，再决定是否执行 body；
- 最终输出最后的 state；
- condition 不是 bool 时立即失败。

### 9.4 通用限制

- 每轮开始、结束都检查取消；
- 每轮发出 iteration 事件；
- 达到 maxIterations 不是成功，返回 `E_LOOP_LIMIT_REACHED`；
- 达到 timeout 返回 `E_LOOP_TIMEOUT`；
- 嵌套循环的 iterationPath 例如 `[2, 5]`；
- v1 不实现 Break/Continue/Return，不能用字符串输出偷渡控制信号。

---

## 10. Operator 兼容策略

保留当前：

```python
executeNode(inputs, params, runtimeContext)
```

不得要求现有算子一次性迁移到新类。

新的 runtimeContext dict 增加：

```text
jobId
workflowId
workflowRunId
nodeId
nodeRunId
iterationPath
workspacePath
isCancellationRequested
```

运行器在节点前后检查取消。长耗时旧算子如果不主动检查，只能在返回后 graceful 退出；force stop 由 Job 子进程终止兜底。

统一解析算子返回中的：

```text
status
outputs
error
metrics
diagnostics
```

把 error code/message、metrics、diagnostics 写入 node.completed/node.failed 事件，不再丢弃。

---

## 11. 异步 Job 与状态机

新状态：

```text
ACCEPTED
STARTING
RUNNING
STOPPING
COMPLETED
FAILED
ABORTED
```

不在 v1 暴露 PAUSED。

`StartJob`：

1. 根据 projectId/revision 取得已验证项目；
2. 确认 workflowId，空值使用 entryWorkflowId；
3. 校验 inputs_json；
4. 检查 maxConcurrentJobs；
5. 创建 jobId 和不可变执行快照；
6. 状态写 ACCEPTED；
7. 交给 JobSupervisor；
8. 立即返回，不等待子进程结束。

`GetJobStatus` 从 JobManager/Repository 查询单个 Job，不再读取全局 currentStatus。

`StopJob(graceful)`：

- RUNNING/STARTING -> STOPPING；
- 设置 cancelEvent；
- 返回 STOPPING；
- 子进程协作退出后写 ABORTED。

`StopJob(force)`：

- 先设置 cancelEvent；
- 等待配置的短暂窗口；
- 仍存活则 `terminate()`；
- 事件桥写 process.terminated 与 job.aborted；
- 必须清理句柄和临时目录。

所有终态转换必须幂等，重复 Stop 不得产生矛盾事件。

---

## 12. 多进程 JobSupervisor

### 12.1 粒度

固定为一个 Job 一个进程。同一 Job 内部继续顺序执行 DAG 和循环。

### 12.2 spawn 入口

新增顶层函数：

```python
def runJobProcess(spec: JobProcessSpec, cancelEvent, eventQueue) -> None:
    ...
```

`JobProcessSpec` 只能包含：

```text
jobId
projectSnapshotPath
workflowId
inputsJson
pluginRootPaths
jobWorkspacePath
heartbeatIntervalMs
```

子进程必须：

1. 从 snapshot 读取编译输入；
2. 重新扫描字符串 plugin roots；
3. 创建本地 WorkflowRunner；
4. 启动心跳线程；
5. 执行工作流；
6. 只向 Queue 写 JSON-safe 事件字典；
7. 捕获 BaseException，发送 job.failed 后以非零码退出；
8. 正常终态后退出。

### 12.3 EventBridge

主进程为每个 Job 启动事件桥线程：

- drain multiprocessing Queue；
- 校验事件结构；
- 在主进程分配单调 sequence；
- 补真实 timestamp；
- 写 EventStore 和 SQLite；
- 根据 terminal event 更新 Job 状态；
- 不把 protobuf 对象放进 Queue。

### 12.4 监督与回收

Supervisor 必须处理：

- process 正常结束但没有 terminal event；
- 非零 exit code；
- heartbeat 超时；
- force terminate；
- Runtime 关闭时清理所有进程；
- 重复 jobId；
- 达到 maxConcurrentJobs；
- 已结束进程的 join/reap。

Runtime 启动时，SQLite 中残留的 RUNNING/STARTING/STOPPING Job 应标记为 FAILED，错误码 `E_RUNTIME_RESTARTED`，不能继续假装运行。

---

## 13. 图像和 Artifact 边界

本次不让大图跨 Job 进程边界流动。一个 Job 的节点都在同一子进程内，NumPy 图像仅在该进程内传递。

需要跨回主进程/Designer 的结果必须写 Artifact：

```text
<runtime-workspace>/jobs/<jobId>/artifacts/<artifactId>.*
```

事件只传：

```text
artifactId
kind
path
mimeType
sizeBytes
checksum
nodeId
workflowRunId
```

将当前通过 `"output image saved: ..."` 文本解析预览路径的逻辑迁移到 `artifact.created` 结构化事件。保留旧消息解析一个版本作为兼容 fallback，但新代码不得依赖该文本。

---

## 14. EventStore 与 gRPC 协议

### 14.1 RuntimeEvent

至少包含：

```text
sequence
timestampMs
jobId
projectId
workflowId
workflowRunId
parentWorkflowRunId
nodeId
nodeRunId
iterationPathJson
eventType
level
code
message
payloadJson
```

### 14.2 事件类型

```text
job.accepted
job.process.started
job.started
job.stopping
job.completed
job.failed
job.aborted
process.heartbeat
process.exited
workflow.started
workflow.completed
workflow.failed
subflow.started
subflow.completed
loop.started
loop.iteration.started
loop.iteration.completed
loop.completed
loop.limit_reached
loop.timeout
node.started
node.completed
node.skipped
node.failed
artifact.created
```

### 14.3 StreamJobEvents

更新 proto：

```text
job_id
after_sequence
follow
```

服务行为：

1. 重放 sequence > after_sequence；
2. follow=true 时通过 Condition 等待新事件；
3. Job 终态且事件已发送完后结束；
4. context cancelled 时退出订阅；
5. 单 Job 内存保留有界，历史完整数据在 SQLite；
6. 慢订阅者不能阻塞运行线程。

### 14.4 其他 proto 改动

`StartJobRequest` 增加：

```text
project_id
workflow_id
inputs_json
```

`StartJobReply` 返回：

```text
ok
job_id
status
message
```

`GetJobStatusReply` 增加：

```text
project_id
workflow_id
pid
accepted_at_ms
started_at_ms
ended_at_ms
error_code
message
```

新增 `ListWorkflows(project_id)`，供 Designer 读取 Runtime 已加载工作流信息。

重新生成 proto，并修复包内导入；删除对顶层 shim 的新增依赖，旧 shim 暂留兼容。

---

## 15. SQLite 迁移与 Repository

新增 `schemaMigrations` 表和 `002_runtime_workflow.sql`。

Jobs 至少需要：

```text
jobId PK
projectId
projectRevision
workflowId
status
pid
acceptedAt
startAt
endAt
durationMs
errorCode
errorMessage
stopMode
```

JobEvents 至少需要：

```text
jobId
sequence
workflowId
workflowRunId
parentWorkflowRunId
nodeId
nodeRunId
iterationPathJson
eventType
level
code
message
payloadJson
timestamp
UNIQUE(jobId, sequence)
```

要求：

- WAL、busy_timeout 保留；
- 每次操作独立短事务；
- Job 状态和 terminal event 更新应尽量在同一事务；
- 增加查询：getJob、listJobEventsAfter、markOrphanedJobsFailed；
- EventStore 先写数据库成功再通知订阅者，或明确定义失败补偿；
- 测试重复 sequence、并发 append、Runtime 重启恢复。

---

## 16. Designer 改造

### 16.1 WorkflowStore

新增项目级状态：

```text
workflowId -> FlowGraphModel
workflowOrder
activeWorkflowId
entryWorkflowId
workflow interfaces
```

保持当前 FlowGraphModel 的单图职责，不把多图字典直接塞进它。

### 16.2 WorkflowController

负责：

- 新建、复制、重命名、删除工作流；
- 设置入口工作流；
- 删除前检查 Subflow/Loop 引用；
- 切换当前 FlowModel 和 FlowScene；
- 生成 Subflow/Loop 动态端口；
- 刷新 workflow selector。

### 16.3 UI

在画布上方增加工作流标签栏：

```text
[主工作流] [单次检测] [+]
```

第一版至少支持：

- 切换；
- 新建；
- 重命名；
- 删除；
- 设置入口；
- 显示 Subflow、Repeat、ForEach、While 节点；
- workflow-select 参数控件。

核心控制节点从本地 system node catalog 提供，不伪装成第三方插件，也不进入 PluginRegistry。

### 16.4 RuntimeClient/RuntimeWorker

- RuntimeClient 只构造真实 protobuf request，并转换为普通 DTO；
- 不修改 protobuf message；
- 增加 RPC deadline 和 grpc.RpcError 映射；
- RuntimeWorker 使用 QThread/QObject 信号运行 Start、状态轮询和事件流；
- 主线程只处理 UI；
- 关闭窗口时取消事件订阅并关闭 channel；
- 启动前保存/校验失败必须阻止 StartJob。

### 16.5 运行状态键

当前 nodeId 在多次子流程调用和循环中不唯一。UI 缓存键必须至少包含：

```text
workflowRunId + nodeId
```

画布默认展示当前工作流最近一次运行实例；右侧详情显示 workflowRunId、iterationPath 和最后事件。

---

## 17. 分阶段实施顺序

每个阶段完成后必须保持全量测试可运行，不能等最后一起修。

### Phase 0：运行契约加固

修改：

- `pyproject.toml`
- `requirements.txt`
- `requirements-dev.txt`
- `scripts/gen_proto.py`
- `proto/runtime.proto`（只做兼容准备）
- `runtime_client.py`
- generated proto import

测试：

- 真实随机端口 gRPC server/client；
- RuntimeClient 使用真实 request；
- protobuf JobEvent 转 DTO，不动态写字段；
- embedded Runtime 与 external Runtime 行为一致；
- proto 生成后工作树无差异。

### Phase 1：Project v2、迁移、统一 Repository

新增/修改：

- core project models/migration/repository/package_builder；
- project_store/project_controller；
- v1/v2 fixture。

测试：

- v1 -> v2；
- v2 round-trip；
- 原子保存与备份；
- project.yaml 双轨测试迁移；
- v2 package/checksum/dependencies。

### Phase 2：WorkflowCompiler 与多工作流模型

新增 core/workflow。

测试：

- 多工作流解析；
- entryWorkflowId；
- 接口节点；
- 节点/端口/参数校验；
- 调用图递归拒绝；
- 单工作流 DAG 环拒绝；
- 编译结果不可变。

### Phase 3：WorkflowRunner 与 Subflow

新增 runtime/workflow context、runner、subflow_runner。

要求：

- 复用当前 operator 执行；
- `executeGraph()` 保留为兼容 wrapper；
- 事件开始具有 workflowRunId；
- 多层 subflow 正确传参返回。

测试：

- main -> child；
- main -> child -> grandchild；
- 输入/输出类型错误；
- 最大深度；
- 旧线性图结果不变。

### Phase 4：结构化 Loop

新增 loop_runner。

测试：

- Repeat 0/1/N；
- Repeat 超限；
- ForEach 顺序和结果集合；
- While 条件 false、N 次、类型错误；
- timeout；
- cancellation；
- 嵌套 iterationPath；
- body/condition 异常传播。

### Phase 5：异步 Job、EventStore、SQLite

新增 jobs manager/repository、events store/models，重构 service。

测试：

- StartJob 在执行完成前返回 ACCEPTED；
- 单调 sequence；
- follow stream；
- after_sequence 重放；
- terminal 后结束；
- SQLite job/event round-trip；
- orphaned job recovery；
- 不再依赖全局 currentStatus。

### Phase 6：Multiprocess JobSupervisor

新增 supervisor、worker_main、event_bridge、snapshot。

测试：

- spawn 正常执行；
- 两 Job 并发且 PID 不同；
- maxConcurrentJobs；
- graceful cancel；
- force terminate；
- worker crash；
- heartbeat；
- Runtime shutdown cleanup；
- 一个 Job 崩溃不影响其他 Job。

测试 worker 必须放在可 import 的顶层测试模块，不能用局部函数/lambda。

### Phase 7：Designer 多工作流和非阻塞运行

新增 WorkflowStore/Controller/Tabs/RuntimeWorker。

测试：

- 多工作流保存恢复；
- 标签切换不丢节点位置和参数；
- 子流程引用更新；
- 删除被引用工作流时拒绝；
- Loop 配置保存；
- UI 运行期间保持响应；
- 事件映射到正确 workflowRunId/nodeId；
- artifact.created 更新预览。

### Phase 8：收口、CI、文档

- `.github/workflows/ci.yml`：Ubuntu + Windows，Python 3.10；
- Linux 设置 `QT_QPA_PLATFORM=offscreen`；
- 运行 ruff、mypy、pytest、proto drift check；
- 更新 README、engineer-guide、project-json-spec、runtime-event-flow；
- `__version__` 更新到 `0.2.0`；
- 删除失效文档描述，不保留与实际代码相反的说明。

---

## 18. 重点测试清单

### 18.1 单元

```text
project_v1_migrates_to_v2
project_v2_rejects_extra_fields
project_save_is_atomic
workflow_compiler_rejects_duplicate_ids
workflow_compiler_rejects_unknown_operator
workflow_compiler_rejects_recursive_subflow
workflow_compiler_rejects_cycle_inside_workflow
subflow_maps_inputs_and_outputs
repeat_executes_exact_count
foreach_preserves_order
while_uses_condition_and_state
loop_reaches_limit
loop_times_out
cancellation_token_aborts_between_iterations
event_store_sequences_are_monotonic
job_state_terminal_transition_is_idempotent
```

### 18.2 Runtime 集成

```text
start_job_returns_before_completion
stream_events_follows_live_job
stream_events_replays_after_sequence
gracious_stop_aborts_loop
force_stop_terminates_blocked_worker
worker_crash_marks_job_failed
two_jobs_run_in_distinct_processes
one_worker_failure_does_not_affect_other_job
runtime_restart_marks_orphaned_jobs_failed
artifact_event_contains_structured_reference
```

### 18.3 真 gRPC

```text
load_validate_list_workflows_start_status_stream_stop
embedded_and_external_runtime_contract_match
rpc_deadline_and_connection_error_are_mapped
```

### 18.4 Designer

```text
workflow_tabs_round_trip
active_workflow_switch_restores_scene
subflow_ports_follow_target_interface
loop_editor_validates_required_fields
runtime_worker_does_not_block_ui_thread
runtime_event_updates_correct_workflow_instance
```

### 18.5 端到端验收项目

```text
main
  -> Repeat(3, body=inspect_once)
  -> Subflow(handle_result)

inspect_once
  -> Image Loader
  -> Canny
  -> Image Saver

handle_result
  -> Empty/If/Switch 兼容链路
```

验收：

- inspect_once 正好执行 3 次；
- 三次 workflowRunId 不同，iterationPath 分别为 `[0] [1] [2]`；
- 每轮产生 artifact.created；
- Job 运行时 StartJob 已返回；
- 第二个 Job 可并行运行且 PID 不同；
- graceful stop 可中止长循环；
- force stop 可终止阻塞测试进程；
- Runtime 主进程始终可查询另一个 Job；
- Designer 不冻结；
- v1 项目仍能迁移并执行。

---

## 19. 提交建议

在同一实现分支上按可回滚提交推进：

```text
1. docs: add runtime workflow architecture implementation plan
2. fix(runtime): harden grpc client and protobuf contracts
3. feat(project): add project json v2 and v1 migration
4. refactor(project): unify repository and package builder on v2
5. feat(workflow): add compiler and multi-workflow models
6. feat(workflow): add runner and subflow execution
7. feat(workflow): add structured repeat foreach and while loops
8. feat(runtime): add async job manager and live event store
9. feat(runtime): persist jobs and events in sqlite
10. feat(runtime): add spawn-based job supervisor
11. feat(designer): add multi-workflow editing and runtime worker
12. test(e2e): cover workflow loops and multiprocess isolation
13. ci: add windows and ubuntu validation
14. docs: document runtime architecture v0.2
```

不要用一个巨大提交完成全部代码；每个提交均应通过当阶段相关测试。

---

## 20. Definition of Done

只有同时满足以下条件才能声明 `IMPLEMENTATION_COMPLETE`：

1. 当前分支基于计划基线，无意外合并；
2. `project.json v2` 是唯一项目源；
3. v1 项目自动迁移且测试覆盖；
4. 一个项目可包含至少三张工作流；
5. Subflow 可多层调用且禁止递归；
6. Repeat、ForEach、While 均按文档契约运行；
7. 普通工作流仍禁止任意图环；
8. 所有循环有轮次、超时、取消保护；
9. StartJob 立即返回；
10. Job 在独立 spawn 子进程运行；
11. 两个 Job 可以受限并发；
12. graceful/force stop 均有真实测试；
13. worker crash 不影响 Runtime 主进程和其他 Job；
14. 事件实时订阅、重放、sequence、timestamp 正确；
15. Job/Event 写入 SQLite，重启清理孤儿状态；
16. 大图不通过普通 IPC Queue 频繁复制；
17. Designer 多工作流保存恢复可用且运行不阻塞；
18. 当前所有既有测试继续通过；
19. 新增 Windows/Ubuntu CI 全绿；
20. README 和架构文档与代码一致；
21. 工作树 clean，HEAD 已推送，远端分支与本地一致；
22. 不存在 TODO、pass 占位、跳过关键测试或伪造通过结果。

---

## 21. Luna/Codex 执行约束

1. 先完整阅读本计划、README、engineer-guide、project-json-spec、runtime-designer-sequences 和 runtime-event-flow；
2. 再阅读本计划列出的全部现有代码和测试，不得仅按文档猜实现；
3. 只在 `agent/runtime-workflow-architecture-v1` 工作；
4. 不合并 `main`，不直接推送 `main`；
5. 先建立回归测试再修改高风险契约；
6. 逐 Phase 实现，每个 Phase 完成后运行相关测试和全量测试；
7. 遇到计划与代码冲突时，以代码事实为准，但必须在提交说明和最终报告中记录偏差；
8. 不通过删测试、降低断言、扩大 ignore 或 catch-all 吞错来制造绿色；
9. 对 multiprocessing 测试使用顶层可 import worker；
10. 对时间相关测试使用 Event/Queue 同步，不使用不稳定的固定长 sleep；
11. 最终创建或更新 Draft PR 指向 `main`，不得合并；
12. 最终报告必须包含：HEAD、提交列表、主要文件、迁移说明、精确测试命令和结果、CI/PR 状态、仍需真实设备验收的事项。
