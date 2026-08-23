# 算子注册与执行流程：给未来工程师的实战说明

> 这份文档专门解释一个算子从磁盘文件到 Designer 显示、再到 Runtime 执行的全过程。适合新增算子、排查“为什么没显示出来”、以及理解插件系统后续应该怎么演进。

## 1. 先回答最常见的问题

如果你是第一次接触这套系统，最常见的问题通常是：

- 我新增了一个算子，为什么 Designer 里没有看到？
- 一个算子是在哪个阶段被“注册”的？
- `manifest.json` 和 `operator.py` 到底谁决定最终行为？
- 为什么 Designer 看得到算子，但 Runtime 执行时报错？
- 如果要继续扩展 `If` / `While` 这类控制流节点，应该改哪一层？

这份文档就是回答这些问题的。

---

## 2. 一张总流程图（文字版）

```text
plugins/builtins/<name>/manifest.json
  + operator.py
      -> PluginRegistry.scan(...)
      -> validateManifestFields(...)
      -> isVersionCompatible(...)
      -> loadOperatorClass(entry)
      -> validateConsistency(...)
      -> Runtime activeOperators / rejectedOperators
      -> RuntimeService.ListOperators()
      -> RuntimeClient.listOperators()
      -> MainWindow.refreshOperators()
      -> 左侧分类 / 气泡面板显示
      -> 用户拖到画布，保存到 project.json
      -> Runtime executeGraph() 通过 operatorId 找到 operator 并执行
```

这张图里最重要的一点是：

**一个算子必须同时存在于磁盘声明层（manifest）和 Python 执行层（operator class），并且两边保持一致，才能真正进入系统。**

---

## 3. 四个角色分别负责什么

### 3.1 `manifest.json`：声明层

它负责告诉系统“这个算子是什么”。

典型字段：

- `operatorId`
- `displayName`
- `version`
- `entry`
- `category`
- `iconKey`
- `summary`
- `inputPorts`
- `outputPorts`
- `paramSchema`
- `minCoreVersion`
- `maxCoreVersion`

示例：`src/emo_master/plugins/builtins/image_loader/manifest.json`

它决定的主要是：

- Designer 里这个算子叫什么、在哪个分类、端口长什么样、参数表单如何生成
- Runtime 在扫描时如何导入它

### 3.2 `operator.py`：执行层

它负责告诉系统“这个算子真正怎么跑”。

典型内容：

- `meta`
- `validateParams(...)`
- `executeNode(...)`

示例：`src/emo_master/plugins/builtins/flow_if/operator.py`

它决定的主要是：

- 参数是否有效
- 输入如何转成输出
- 错误时返回什么结构

### 3.3 `PluginRegistry`：注册层

文件：`src/emo_master/core/plugin/registry.py`

它负责：

- 扫描磁盘中的 `manifest.json`
- 校验 manifest 是否合格
- 动态导入 operator class
- 校验 manifest 与类之间是否一致
- 最终把算子放入：
  - `activeOperators`
  - `rejectedOperators`

### 3.4 Designer / Runtime：消费层

- Runtime 负责真正扫描、注册和执行
- Designer 只通过 Runtime 暴露出来的 operator metadata 来显示算子

所以你可以把 Runtime 理解成：

**插件系统的“真注册者”**。

---

## 4. Runtime 启动时怎么扫描算子

### 调用链

1. Runtime 启动
2. `RuntimeService.__init__()`
3. `_scanBuiltins()`
4. `PluginRegistry.scan(pluginRoot)`

关键文件：

- `src/emo_master/apps/runtime/grpc_server/service.py`
- `src/emo_master/core/plugin/registry.py`

### `PluginRegistry.scan(...)` 做了什么

在 `src/emo_master/core/plugin/registry.py` 中，`scan(pluginRoot)` 负责完整注册流程：

1. 找到所有 `**/manifest.json`
2. 调 `_loadManifestData(manifestPath)` 读取 JSON
3. 调 `validateManifestFields(...)` 校验字段结构
4. 调 `isVersionCompatible(...)` 校验当前 coreVersion 是否兼容
5. 调 `loadOperatorClass(entry)` 动态导入类
6. 调 `validateConsistency(...)` 校验 manifest 与 operator meta 是否一致
7. 成功则进入 `activeOperators`
8. 失败则进入 `rejectedOperators`

### 为什么这一步重要

因为后面的一切都建立在这一步之上：

- 如果没进 `activeOperators`
  - Designer 就看不到它
  - Runtime 执行也找不到它

---

## 5. manifest 校验到底在查什么

核心文件：`src/emo_master/core/plugin/validator.py`

### 5.1 `validateManifestFields(manifestData)`

它主要检查：

- 必需字段是否都存在
- `inputPorts` 是否是 `dict[str, str]`
- `outputPorts` 是否是 `dict[str, str]`
- `paramSchema` 是否是对象

如果这里失败，这个算子甚至还没进入“尝试导入 Python 类”的阶段。

### 5.2 `isVersionCompatible(coreVersion, minCoreVersion, maxCoreVersion)`

它检查当前 coreVersion 是否在插件支持范围内。

现在实现比较简单，主要按 major version 判断。

### 5.3 `loadOperatorClass(entry)`

它做三件事：

1. 解析 `entry`，格式必须是 `module:Class`
2. 动态 import 模块
3. 检查类是否存在，并且是否至少有：
   - `validateParams`
   - `executeNode`

### 5.4 `validateConsistency(manifest, operatorClass)`

这一步检查 manifest 与类的 `meta` 是否一致，当前重点是：

- `inputPorts`
- `outputPorts`

这能防止“Designer 以为端口长这样，Runtime 实际执行却不是这样”的错配。

---

## 6. Designer 是怎么拿到算子列表的

### 调用链

```text
RuntimeService.ListOperators()
  -> RuntimeClient.listOperators()
  -> MainWindow.refreshOperators()
  -> 左侧分类 / 气泡算子面板
```

### 关键文件

- `src/emo_master/apps/runtime/grpc_server/service.py`
- `src/emo_master/apps/designer/services/runtime_client.py`
- `src/emo_master/apps/designer/ui/main_window.py`

### 具体发生了什么

#### `RuntimeService.ListOperators()`
它会把 `activeOperators` 转成协议对象，返回：

- `operator_id`
- `display_name`
- `input_ports`
- `output_ports`
- `param_schema_json`
- `category`
- `icon_key`
- `summary`

#### `RuntimeClient.listOperators()`
Designer 端把这些协议对象解析成 `OperatorDefinition`，把 JSON schema 解析回字典。

#### `MainWindow.refreshOperators()`
这个函数把 Runtime 返回的数据转换成 UI 使用的 payload，供：

- 左侧分类列表
- 顶部气泡面板
- 拖拽添加节点

### 结论

一个算子如果没显示在 Designer，排查顺序应该是：

1. Runtime 是否扫到它？
2. `ListOperators()` 有没有返回它？
3. `RuntimeClient.listOperators()` 是否解析出它？
4. `MainWindow.refreshOperators()` 是否把它放进 `operatorCatalog`？

---

## 7. 用户把算子拖到画布后发生了什么

关键文件：

- `src/emo_master/apps/designer/ui/operator_bubble.py`
- `src/emo_master/apps/designer/ui/main_window.py`
- `src/emo_master/apps/designer/state/flow_graph_model.py`
- `src/emo_master/apps/designer/ui/flow_scene.py`

### 调用链

1. `OperatorBubble.setOperators(...)` 显示算子卡片
2. 用户拖卡片到画布
3. `MainWindow.addNodeFromOperatorPayload(...)`
4. `FlowGraphModel.addNode(...)`
5. `FlowScene.addFlowNode(...)`

### 重要点

- `operatorId` 最终会被保存进 `FlowGraphModel` 和 `project.json`
- 运行时靠这个 `operatorId` 找到具体 operator class

所以 `operatorId` 是整个 Designer / Runtime 的连接键。

---

## 8. Runtime 执行时如何找到 operator 并运行

关键文件：

- `src/emo_master/apps/runtime/grpc_server/service.py`
- `src/emo_master/apps/runtime/execution/dag_executor.py`

### 调用链

1. Designer 点击开始运行
2. Runtime `LoadProject` 读入 `project.json`
3. `RuntimeService._runLoadedGraph(...)`
4. 构造 `graphPayload`
5. 从 `pluginScanResult.activeOperators` 构造 `operatorRegistry`
6. `executeGraph(graphPayload, operatorRegistry, runtimeContext)`
7. `executeGraph()` 遍历节点，根据 `operatorId` 调 `_buildOperator(...)`
8. 生成 operator 实例，执行 `executeNode(...)`

### `executeGraph()` 里和算子最相关的点

在 `src/emo_master/apps/runtime/execution/dag_executor.py`：

- `topologicalSort(...)`：决定执行顺序
- `_buildOperator(...)`：根据 `operatorId` 找到 operator class 并实例化
- `executeNode(inputs, params, runtimeContext)`：真正运行
- `_routeNodeOutputs(...)`：把输出路由到下游输入

### 为什么 `If` 是特殊但仍属于算子

`If` 不是硬编码在 UI 层的特殊节点，而是普通插件算子中的一种：

- 注册方式和普通算子一样
- 只是在执行器里会额外记录 `branchHits`

这也是一个很重要的架构信号：

**控制流能力现在还是“算子扩展”，不是独立工作流引擎。**

---

## 9. 新增一个算子的最短路径（实战）

下面这组步骤，是未来工程师最常会走的一条路。

### 第一步：创建目录

例如新增：

```text
src/emo_master/plugins/builtins/my_operator/
  ├── __init__.py
  ├── manifest.json
  └── operator.py
```

### 第二步：写 `manifest.json`

必须最少包含：

- `operatorId`
- `displayName`
- `version`
- `entry`
- `inputPorts`
- `outputPorts`
- `paramSchema`
- `minCoreVersion`
- `maxCoreVersion`

建议还写：

- `category`
- `iconKey`
- `summary`

参考：`src/emo_master/plugins/builtins/image_loader/manifest.json`

### 第三步：写 `operator.py`

最少实现：

- `meta`
- `validateParams(...)`
- `executeNode(...)`

参考：

- `src/emo_master/plugins/builtins/image_loader/operator.py`
- `src/emo_master/plugins/builtins/flow_if/operator.py`

### 第四步：启动 Runtime 看能否注册

如果注册成功：

- `ListOperators()` 会返回它
- Designer 左侧会出现它

如果注册失败：

- 它会进入 `rejectedOperators`

### 第五步：拖到画布并运行

如果能拖到画布，说明：

- Runtime 已经注册成功
- Designer 已经拿到了 metadata

如果运行时报错，再去查执行器和 `executeNode()`。

---

## 10. 最常见的失败场景与排查路径

### 场景 1：算子完全没出现在 Designer

先查：

1. `manifest.json` 是否存在、是否合法
2. Runtime `ListOperators()` 是否有它
3. `MainWindow.refreshOperators()` 是否收到它

### 场景 2：Runtime 扫描到了 rejectedOperators

先查：

- `validator.py`
- entry 是否可导入
- `inputPorts/outputPorts` 是否和 `meta` 一致

### 场景 3：Designer 能看到，但运行时报 `operator not found`

先查：

- `operatorId` 是否一致
- Runtime 当前进程是不是重新启动过
- `pluginScanResult.activeOperators` 里有没有它

### 场景 4：参数面板不对

先查：

- `manifest.json` 里的 `paramSchema`
- `param_form.py` 是否支持这种控件类型

### 场景 5：分支控制节点行为不对

先查：

- `flow_if/operator.py`
- `dag_executor.py`
- `service.py` 里对 `branchHits` 和事件流的处理

---

## 11. 现在这套算子系统的限制

这部分是为了让未来工程师不要误判系统能力。

### 11.1 当前更偏 builtins，不是真正开放插件平台

虽然架构上是插件系统，但当前主要面向：

- `src/emo_master/plugins/builtins`

外部插件目录、多来源插件仍未形成完整方案。

### 11.2 manifest 表达能力还偏基础

现在已经够支持：

- 分类
- 图标
- 摘要
- 参数 schema

但未来如果想支持更复杂 UI，可能还需要：

- richer schema metadata
- 示例值
- 文档链接
- 控制流语义标记

### 11.3 `If` 是第一步，不是终点

当前 `If` 的实现方式是：

- operator 分流
- 未命中的下游节点 `SKIPPED`

这意味着：

- 现在只是对 DAG 执行器做了有限扩展
- 如果未来做 `While`，不能简单照搬

---

## 12. 下一步该怎么改（顺路分析）

这是你特别要求我顺路分析的部分。我按工程价值排序。

### P1：增强算子元信息能力

建议内容：

- 为 manifest 增加更丰富的 UI 元信息
- 让 Designer 能显示更强的算子说明
- 为控制流节点增加明确标记

为什么优先：

- 这是 Designer 体验和插件系统扩展能力的共同基础

### P2：让 rejectedOperators 更好地暴露给 Designer

现在 Runtime 侧已经有 `ListRejectedOperators()`，但还可以继续做：

- 在 Designer 里直接显示注册失败原因
- 帮未来开发者更快定位 manifest / entry 问题

### P3：控制流算子分类管理

当前 `If` 已经存在，但未来最好把：

- 普通图像算子
- 控制流算子

在分类和视觉层面分开对待。

### P4：外部插件目录能力

如果未来想真正支持第三方算子，当前 builtins-only 的假设要逐步松开。

### P5：新增算子开发脚手架

未来可以考虑：

- 一个脚本或模板
- 自动生成 manifest + operator.py 骨架

这样新增算子会更快，也更不容易出错。

---

## 13. 最后的实战建议

如果你准备新增一个算子，推荐按这个顺序做：

1. 先复制一个最像的 built-in 算子作为模板
2. 先让 Runtime 扫描成功
3. 再看 Designer 是否显示正确
4. 最后再调参数表单和视觉样式

不要一上来就先纠结 UI 展示，因为：

**算子系统真正的第一关永远是 Runtime 能不能正确注册它。**
