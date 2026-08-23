# Runtime 事件流与 Designer 实时回写说明

> 这份文档专门解释 Runtime 事件是如何产生、如何通过 gRPC 回到 Designer、以及 Designer 如何把这些事件实时映射到日志、右侧详情和画布节点样式上的。适合未来工程师在调试运行态、扩展 `If` / 分支反馈、或增强实时可视化时参考。

## 1. 这份文档解决什么问题

如果你在看运行相关代码，最容易迷失的地方通常是：

- 一个节点的 `COMPLETED / SKIPPED / FAILED` 是在哪一层生成的？
- Runtime 为什么能把节点级信息带回 Designer？
- `payload_json` 是谁写进去的，又是谁解析出来的？
- 为什么右侧“当前节点”能实时变化？
- 为什么画布节点会实时变色？

这份文档就是围绕这条主线写的。

---

## 2. 一句话先建立心智模型

当前事件流主线是：

```text
executeGraph() 生成节点执行结果
  -> RuntimeService 把结果拆成事件
  -> RuntimeEventBus 存储事件
  -> StreamJobEvents 输出事件
  -> RuntimeClient 解析 payload_json
  -> MainWindow 逐条消费事件
  -> 右侧详情 / 画布节点颜色 / 日志 实时更新
```

你可以把它理解为：

**Runtime 负责“生成结构化事件”，Designer 负责“把事件翻译成 UI 状态”。**

---

## 3. 事件模型长什么样

核心文件：`src/emo_master/apps/runtime/events/event_bus.py`

当前事件数据结构：

```python
RuntimeEvent(
    jobId: str,
    eventType: str,
    message: str,
    level: str = "INFO",
    nodeId: str = "",
    payloadJson: str = "{}",
)
```

### 字段作用

- `jobId`
  - 哪个作业的事件
- `eventType`
  - 事件类型，例如：
    - `job.started`
    - `job.completed`
    - `node.completed`
    - `node.skipped`
- `message`
  - 给日志和状态面板看的文本
- `level`
  - `INFO / WARN / ERROR`
- `nodeId`
  - 节点级事件关联哪个节点
- `payloadJson`
  - 结构化附加信息，目前主要存：
    - `status`
    - `branch`

### 为什么要有 `payloadJson`

因为仅靠 `message` 不够稳定。像 `If` 分支命中这类信息，不能只靠字符串拼接去解析，所以 Runtime 需要把结构化状态显式带出去。

---

## 4. Runtime 是怎么生成这些事件的

核心文件：

- `src/emo_master/apps/runtime/execution/dag_executor.py`
- `src/emo_master/apps/runtime/grpc_server/service.py`

### 4.1 第一步：执行器生成结构化结果

`executeGraph(...)` 的返回值里，当前最重要的几个字段是：

- `artifacts`
- `nodeStatus`
- `branchHits`

在 `dag_executor.py` 里：

- `nodeStatus[nodeId]`
  - 记录每个节点最后是：
    - `COMPLETED`
    - `SKIPPED`
    - `FAILED`
- `branchHits[nodeId]`
  - 对 `vision.flow.if` 节点记录：
    - `true`
    - `false`

### 4.2 第二步：RuntimeService 把执行结果拆成事件

在 `RuntimeService._runLoadedGraph(...)` 中：

1. 先调用 `executeGraph(...)`
2. 拿到 `nodeStatus` 和 `branchHits`
3. 对每个节点生成节点级事件：
   - `node.completed`
   - `node.skipped`
4. 用 `payloadJson` 存：

```json
{
  "status": "SKIPPED",
  "branch": "true"
}
```

### 4.3 第三步：统一写入事件总线

`_appendJobEvent(...)` 最后调用：

- `RuntimeEventBus.publish(...)`

这个总线当前是一个内存事件桶：

- 每个 `jobId` 对应一个事件列表

它现在简单但够用，Designer 通过 `StreamJobEvents` 拉回整个事件序列。

---

## 5. gRPC 怎么把事件送回 Designer

核心文件：`src/emo_master/apps/runtime/grpc_server/service.py`

### `StreamJobEvents(...)`

它会把 `RuntimeEventBus` 里的 `RuntimeEvent` 转成 protobuf `JobEvent`：

- `job_id`
- `node_id`
- `event_type`
- `level`
- `message`
- `payload_json`

这里有一个重要点：

**Runtime 内部的 `payloadJson` 到了 gRPC 协议层仍然是字符串，不会自动变成 Python dict。**

所以 Designer 端必须主动解析它。

---

## 6. Designer 是怎么解析事件的

核心文件：`src/emo_master/apps/designer/services/runtime_client.py`

### `RuntimeClient.streamJobEvents(jobId)`

它的职责不是只把事件列表原样返回，而是做一层轻量规范化：

1. 调 Runtime 的 `StreamJobEvents`
2. 遍历返回事件
3. 读取 `payload_json`
4. 尝试 JSON 解析
5. 把结果挂到事件对象的 `payload` 字段上

所以到了 `MainWindow` 这一层时，事件已经是：

- 文本字段：`event_type / message / node_id`
- 结构化字段：`payload`

这一步很关键，因为它是 Designer 和 Runtime 协议之间的“翻译层”。

---

## 7. MainWindow 是怎么实时更新 UI 的

核心文件：`src/emo_master/apps/designer/ui/main_window.py`

### 7.1 `startJob()` 的事件消费方式

当前 `MainWindow.startJob()` 的流程大致是：

1. 启动作业
2. 取一次 `GetJobStatus`
3. 拉取 `streamJobEvents(jobId)`
4. **逐条**消费事件

这里的关键不是“事件最终都拿到了”，而是：

**它是逐条处理，而不是只在最后汇总。**

### 7.2 每条事件做了哪些事

在 `startJob()` 的事件循环里，会做三件事：

#### A. 写日志

```python
self.appendRuntimeLog(...)
```

#### B. 更新作业级状态

```python
self.runtimePanelState.applyEvent(...)
```

这个主要更新：
- jobStatus
- lastMessage
- latestImagePath

#### C. 更新节点级状态

```python
self.applyRuntimeEventToNode(...)
```

这是现在“实时”能力的核心。

---

## 8. `applyRuntimeEventToNode(...)` 到底做了什么

核心文件：`src/emo_master/apps/designer/ui/main_window.py`

输入：

```python
{
  "nodeId": "if1",
  "payload": {
    "status": "SKIPPED",
    "branch": "true"
  }
}
```

它会：

1. 取出 `nodeId`
2. 取出 `payload.status`
3. 取出 `payload.branch`
4. 调 `setCurrentNodeRuntimeState(nodeId, status, branch)`

### 为什么单独抽这个函数

因为这样：

- 测试可以直接构造运行事件来验证 UI 行为
- 后续如果换成 websocket / 真流式订阅，也只要继续复用这层方法

---

## 9. `setCurrentNodeRuntimeState(...)` 怎么把数据变成 UI

核心文件：`src/emo_master/apps/designer/ui/main_window.py`

这一步会做两件事：

### 9.1 更新右侧“当前节点详情”数据源

把状态写入：

- `_nodeRuntimeState[nodeId] = {status, branch}`

右侧当前节点详情通过 `getCurrentNodeSummary()` 读取这里的数据。

### 9.2 同步画布节点颜色

调用：

- `flowScene.setNodeRuntimeState(nodeId, status)`

这就是为什么画布节点也会实时变色。

---

## 10. `FlowScene` 怎么实现节点运行态变色

核心文件：`src/emo_master/apps/designer/ui/flow_scene.py`

### `_NodeItem.setRuntimeState(state)`

这是实际切换节点视觉状态的地方。

当前映射关系是：

- `RUNNING` -> 浅蓝 / 蓝边
- `COMPLETED` -> 浅绿 / 绿边
- `SKIPPED` -> 浅灰 / 灰边
- `FAILED` -> 浅红 / 红边

### `getNodeVisualStyle(nodeId)`

这是测试与调试用的查询接口，用来验证：

- 当前 variant（比如 `if`）
- 当前 runtimeState
- 当前 fill/border 颜色

---

## 11. 这条事件流当前已经实现了什么

截至当前版本，事件流已经支持：

- 作业级状态：
  - `job.loaded`
  - `job.started`
  - `job.completed`
  - `job.failed`
- 节点级状态：
  - `node.completed`
  - `node.skipped`
- `If` 节点分支命中：
  - `true`
  - `false`
- Designer 侧实时更新：
  - 日志
  - 当前节点详情
  - 画布节点颜色

---

## 12. 当前限制与不足

这部分是未来扩展时最需要注意的。

### 12.1 目前没有 `node.started`

现在节点状态更偏“结果态”，还缺：

- 节点开始执行时的即时事件

这会影响对 `RUNNING` 的细粒度可视化。

### 12.2 连线级分支高亮还没有做

现在 `If` 的分支命中能体现在：

- 右侧详情
- 节点本身状态

但还没有体现在：

- true / false 哪条边亮了

### 12.3 事件总线还是内存实现

这意味着：

- 事件只在当前 Runtime 进程内存里
- 没有持久化
- 没有真正的流式订阅模型

### 12.4 `startJob()` 仍然是同步消费整批事件

虽然 UI 是逐条应用事件的，但当前实现仍然是：

- 先拿完整 `streamJobEvents(...)` 返回列表
- 再逐条应用

未来如果要做真正的长作业实时流式更新，这里还需要进一步演进。

---

## 13. 下一步演进建议（按优先级）

### P1：补 `node.started`

这是最应该优先补的事件。

有了它之后：

- 当前执行节点可以实时标蓝
- 右侧详情能真正显示“运行中”而不是只在结果态跳变

### P2：做连线级分支高亮

如果 `If` 分支要真正好用，这一步非常关键：

- 命中边高亮
- 未命中边淡化

### P3：增加最近事件时间线面板

右侧当前节点详情已经有了，但还可以补：

- 最近 10 条节点/作业事件摘要

这样调试体验会明显提升。

### P4：从“整批拉取事件”演进到“真流式订阅”

如果未来作业执行时间变长，现在这套“先拿完整列表再处理”的方式会逐渐不够。

### P5：让 Runtime 事件更结构化

当前 `payload_json` 已经够用，但未来可以考虑：

- 更明确的 schema
- 不同事件类型有不同 payload 结构

---

## 14. 给未来工程师的建议

如果你要改“运行时 UI 是怎么实时变化的”，建议按这个顺序看：

1. `src/emo_master/apps/runtime/execution/dag_executor.py`
2. `src/emo_master/apps/runtime/grpc_server/service.py`
3. `src/emo_master/apps/runtime/events/event_bus.py`
4. `src/emo_master/apps/designer/services/runtime_client.py`
5. `src/emo_master/apps/designer/ui/main_window.py`
6. `src/emo_master/apps/designer/ui/flow_scene.py`

不要只盯着 UI，看不到状态是哪里没更新，很多时候根因并不在 UI，而在：

- 执行器没产出状态
- Service 没把状态变成事件
- RuntimeClient 没把 JSON 解析出来

也就是说，问题经常卡在“中间层”，不是起点也不是终点。
