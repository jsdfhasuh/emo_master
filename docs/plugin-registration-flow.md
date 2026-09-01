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
  + 可选 ui/editor.ui + editor.py
      -> PluginRegistry.scan(...) / scanRoots(...)
      -> validateManifestFields(...)
      -> isVersionCompatible(...)
      -> loadOperatorClass(entry)
      -> validateConsistency(...)
      -> Runtime activeOperators / rejectedOperators
      -> RuntimeService.ListOperators()
      -> 可选 GetOperatorEditorAsset()（注册时冻结的 UI 字节 + SHA-256）
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
- 可选 `editor`

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
4. `PluginRegistry.scanRoots(pluginRoots)`（单目录入口 `scan()` 会委托给它）

关键文件：

- `src/emo_master/apps/runtime/grpc_server/service.py`
- `src/emo_master/core/plugin/registry.py`

### `PluginRegistry.scanRoots(...)` 做了什么

在 `src/emo_master/core/plugin/registry.py` 中，`scanRoots(pluginRoots)` 负责完整注册流程：

1. 按配置顺序扫描所有根目录下的 `**/manifest.json`，并去重重复根和重复文件
2. 调 `_readManifest(manifestPath)` 读取 JSON，并保留解析/读取失败原因
3. 调 `validateManifestFields(...)` 校验字段结构
4. 在导入代码前汇总 `operatorId`；同目录或跨目录的重复声明全部拒绝
5. 调 `isVersionCompatible(...)` 校验当前 coreVersion 是否兼容
6. 调 `loadOperatorClass(entry)` 动态导入类，并确认必需方法可调用
7. 调 `validateConsistency(...)` 校验 manifest 与 operator meta 是否一致
8. 成功则进入 `activeOperators`
9. 失败则进入 `rejectedOperators`，错误消息包含来源 manifest 路径

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
- `inputPorts` 是否是端口名到类型字符串或 PortSpec 描述对象的映射
- `outputPorts` 是否是端口名到类型字符串或 PortSpec 描述对象的映射
- `paramSchema` 是否是对象

PortSpec 的 `type/required/nullable/schemaVersion` 规则以及几何、Blob、Detection、
颜色统计 payload，统一见 `docs/operator-io-contracts.md`。

如果这里失败，这个算子甚至还没进入“尝试导入 Python 类”的阶段。

### 5.2 `isVersionCompatible(coreVersion, minCoreVersion, maxCoreVersion)`

它检查当前 coreVersion 是否在插件支持范围内。

版本采用一到三段纯数字比较，拒绝前导零和预发布后缀；最大版本还支持
`1.x`、`1.2.x` 这样的通配范围。格式错误只会拒绝对应插件，不会中断整个扫描。

### 5.3 `loadOperatorClass(entry)`

它做三件事：

1. 解析 `entry`，格式必须是 `module:Class`
2. 动态 import 模块
3. 检查目标确实是类，并且以下方法存在且可调用：
   - `validateParams`
   - `executeNode`

### 5.4 `validateConsistency(manifest, operatorClass)`

这一步检查 manifest 与类的 `meta` 是否一致，当前包括：

- `operatorId`
- `displayName`
- `version`
- `inputPorts`
- `outputPorts`
- `paramSchema`

为兼容早期第三方算子，上述字段只有在 `meta` 中声明时才比较；`meta` 本身仍然
是必需的。

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
- `editor_spec_json`
- `editor_issues_json`

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

## 9. 如何注册一个算子（可直接照做）

先明确当前机制：**没有中央 `registerOperator()` 方法，也不需要修改一份算子列表。**
Runtime 默认递归扫描 `src/emo_master/plugins/**/manifest.json`。一个 manifest 通过字段、
core 版本、entry 导入和 meta 一致性校验后，其 `operatorId` 就会进入
`activeOperators`，这一步就是注册。

完整流程只有六步：创建算子目录；编写 manifest；实现带 `meta` 的 operator class；为
语义端口声明准确的类型与 `schemaVersion`；补算子单测和注册期望集合；扫描确认进入
`activeOperators`。不需要修改 Runtime/Designer 的中央列表，也不要在 import 时执行
网络连接、模型加载或设备初始化。

### 9.1 创建目录

内置算子放到默认扫描根下。例如新增一个乘法算子：

```text
src/emo_master/plugins/builtins/example_multiply/
  ├── __init__.py
  ├── manifest.json
  └── operator.py
```

`__init__.py` 可以为空，但必须保证 `entry` 指向的 Python 模块可 import。

### 9.2 编写完整 manifest

`manifest.json`：

```json
{
  "operatorId": "vision.example.multiply",
  "displayName": "Multiply",
  "version": "1.0.0",
  "entry": "emo_master.plugins.builtins.example_multiply.operator:ExampleMultiplyOperator",
  "category": "示例",
  "iconKey": "number",
  "summary": "将输入数值乘以参数 factor。",
  "inputPorts": {
    "value": {"type": "number", "required": true, "nullable": false}
  },
  "outputPorts": {
    "result": {"type": "number", "required": true, "nullable": false}
  },
  "paramSchema": {
    "type": "object",
    "properties": {
      "factor": {"type": "number", "default": 1.0}
    }
  },
  "minCoreVersion": "0.3.0",
  "maxCoreVersion": "1.x"
}
```

必需字段是：

- `operatorId`：所有扫描根范围内全局唯一，建议使用稳定的反向域式命名。
- `displayName/version/entry`：entry 必须是 `module:Class`，不能写文件路径。
- `inputPorts/outputPorts`：端口可用字符串简写，也可使用包含
  `type/required/nullable/schemaVersion` 的 PortSpec。
- `paramSchema`：必须是对象；Designer 用它生成参数面板，算子仍须自行严格校验。
- `minCoreVersion/maxCoreVersion`：版本为一到三段数字；最大版本支持 `1.x` 或
  `1.2.x`。

`category/iconKey/summary` 可省略，但建议填写。语义端口和 schemaVersion 的规则见
`docs/operator-io-contracts.md`。

复杂算子可以额外声明专用编辑器；不声明时继续使用通用 Schema 表单：

```json
"editor": {
  "schemaVersion": "1.0",
  "kind": "customUi",
  "openMode": "window",
  "uiResource": "ui/editor.ui",
  "controllerEntry": "emo_master.plugins.builtins.example.editor:ExampleEditorController",
  "fallback": "schemaForm",
  "previewMode": "none"
}
```

`uiResource` 必须是插件目录内不含 `.`、`..` 或空段的相对 `.ui` 路径，大小不超过
2 MiB。首版只允许 Qt 内置控件；自定义画布和图表由 Controller 插入 `.ui` 占位
`QWidget`。`controllerEntry` 必须与运行算子 `entry` 位于同一插件包命名空间。
注册阶段只校验并冻结 `.ui` 字节，不导入 Controller，所以专用页面损坏不会让 Runtime
算子退出 `activeOperators`，而是通过 `editor_issues_json` 告知 Designer 回退通用表单。

`previewMode` 的含义：

- `none`：不允许 Runtime 预览。
- `pure`：允许以正式算子类做无副作用单节点预览，单次上限 5 秒。
- `live`：用于相机等有状态采集页面，页面关闭或正式 Job 接管时释放会话。

坐标或视觉集合端口应使用明确的强类型，不能为了省事全部声明成 `json`。只传播仿射
1.1 payload 的生产者可以声明精确 `1.1`；能接收或传播 Homography 的端口应声明
`schemaVersion: "1.x"`；Line、Circle 及第二批经典视觉集合由 DTO 固定写出 `1.2`。

### 9.3 实现 operator class

`operator.py`：

```python
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, TypeGuard, cast


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "factor": {"type": "number", "default": 1.0},
    },
}


class ExampleMultiplyOperator:
    meta = OperatorMeta(
        operatorId="vision.example.multiply",
        displayName="Multiply",
        version="1.0.0",
        inputPorts={
            "value": {"type": "number", "required": True, "nullable": False},
        },
        outputPorts={
            "result": {"type": "number", "required": True, "nullable": False},
        },
        paramSchema=PARAM_SCHEMA,
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        factor = params.get("factor", 1.0)
        if not _isFiniteNumber(factor):
            return {
                "code": "E_PARAM_INVALID",
                "message": "factor must be a finite number",
            }
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        if "value" not in inputs:
            return {
                "status": "error",
                "error": {"code": "E_INPUT_MISSING", "message": "value is required"},
            }
        value = inputs["value"]
        if not _isFiniteNumber(value):
            return {
                "status": "error",
                "error": {"code": "E_INPUT_TYPE", "message": "value must be a number"},
            }
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        factor = cast(int | float, params.get("factor", 1.0))
        result = float(value) * float(factor)
        return {
            "status": "ok",
            "outputs": {"result": result},
            "metrics": {},
            "diagnostics": {},
        }


def _isFiniteNumber(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )
```

operator class 必须满足以下规则：

- 有 `meta`；其中声明的 `operatorId/displayName/version/inputPorts/outputPorts/paramSchema`
  必须与 manifest 一致。
- `validateParams(params)` 和 `executeNode(inputs, params, runtimeContext)` 必须存在且可调用。
- 成功返回 `status=ok` 和按端口名组织的 `outputs`；失败返回标准 `error.code/message`。
- 不得返回 manifest 未声明的输出；所有 required 输出都必须返回且类型正确。
- 几何、Blob、Detection 等语义 DTO 必须先调用 `toPayload()`，不能直接跨端口传实例。

有状态设备算子可以额外实现可选生命周期：

```python
def initOperator(self, initContext: dict[str, object]) -> None:
    # 只做轻量对象初始化；设备连接仍可延迟到首次 executeNode。
    ...

def disposeOperator(self) -> None:
    # 必须幂等；失败时抛带明确 code 的异常。
    ...
```

Runtime 只缓存声明了 `initOperator` 或 `disposeOperator` 的算子，缓存键为
`(workflowId, nodeId)`；普通算子仍在每次执行时重新实例化。有状态实例仅在单个 Job 内复用，
Loop 和重复 Subflow 调用共享同一节点实例，最外层工作流结束时按创建逆序释放。成功路径的
清理失败会使 Job 以 `E_RESOURCE_CLEANUP_FAILED` 失败；已有执行错误或取消时保留原错误，
并附加清理诊断。长时间阻塞的设备调用应周期性调用
`runtimeContext["raiseIfCancellationRequested"]()`，不要只读取一次性的
`isCancellationRequested` 快照。

Runner 还会向 `runtimeContext["logger"]` 和 `initContext["logger"]` 注入结构化日志对象。
算子不要直接依赖 Runtime 内部实现，也不要在 manifest 增加日志字段；统一通过公共 helper：

```python
from emo_master.core.contracts import getOperatorLogger

def executeNode(self, inputs, params, runtimeContext):
    logger = getOperatorLogger(runtimeContext)
    logger.info("request started", payload={"count": len(inputs)})
    ...
```

可用方法为 `debug/info/warning/error/log/isEnabledFor`。记录会形成带完整 Job、Workflow、Node、
NodeRun 和 Loop iteration 上下文的 `node.log`，不需要也不允许用输出端口传递。旧 Runtime、
纯单测或缺少注入的预览环境会得到 `NullOperatorLogger`，因此无需自行判断键是否存在。

日志只应记录阶段、数量、耗时、设备选择器和有限的错误摘要。不要写原始图像、完整 PLC 值、
完整 TCP 报文、密码、token 或私钥；Runtime 虽会限流、截断和脱敏，但这不是算子主动控制
数据面的替代方案。日志持久化和轮转规则见 `docs/runtime-event-flow.md`。

上面的数值算子只是为了让注册样例足够短。图像算子的注册步骤完全相同：基础预处理可
参考 `roi`、`threshold`；集合适配可参考 `collection_filter`；Homography 传播可参考
`perspective`；经典分析可参考 `contour`、`template_match`；文件副作用可参考
`result_writer`；坐标文件和几何测量可参考 `coordinate_reader/coordinate_calculator`；
有界设备通讯可参考 `plc_slmp_read/plc_slmp_write/tcp_client/`
`tcp_receive_once`。PLC 标量选择与 TCP 文本解码作为可选输出直接声明在对应 I/O 算子的
manifest 和 `OperatorMeta` 中，完整强类型 payload 端口继续保留。
需要作业内连接复用、延迟加载厂商 SDK 和可取消分段等待的设备节点可参考
`huaray_camera` 与 `_huaray_imv.py`。
每个目录都应让 manifest 与 `OperatorMeta` 的 ID、版本、端口和参数
schema 完全一致。通讯类的连接、bind、模型或设备探测只能发生在 `executeNode()` 中，
绝不能在模块 import、manifest 扫描或 `meta` 构造阶段触发。

### 9.4 为复杂算子实现 `.ui + Controller`

推荐目录：

```text
example/
  ├── manifest.json
  ├── operator.py
  ├── editor.py
  └── ui/editor.ui
```

Controller 固定实现七个方法：

```python
class ExampleEditorController:
    def bind(self, rootWidget, context) -> None: ...
    def loadParams(self, params: dict[str, object]) -> None: ...
    def collectParams(self) -> dict[str, object]: ...
    def validate(self) -> object: ...
    def onOpen(self) -> None: ...
    def onClose(self) -> None: ...
    def dispose(self) -> None: ...
```

控件通过稳定 `objectName` 查找。Controller 只能通过受限 `EditorContext` 应用参数、记录
日志、列出/上传/下载预览图源、运行 pure preview 或管理 live preview；不要导入或保存
`MainWindow`。`dispose()` 必须幂等并关闭线程、Timer、future 和预览 session。

Designer 的 `OperatorEditorManager` 以 `(projectId, workflowId, nodeId)` 为键，每节点最多
一个非模态窗口。builtin Controller 自动信任；外部 Controller 以
`operatorId + version + controllerEntry + uiHash` 作为信任身份，任一项变化都需要重新
确认。远端或旧 Runtime 不支持 UI RPC、资源校验失败、Controller 不可导入时，Designer
自动回退 Schema 表单。

pure preview 的本地图片只上传到 Runtime 临时区，不写入项目；作业快照只在整个 Job
成功后提升为项目最近结果，失败/取消不会覆盖旧快照。Loop 中同一节点端口由最后一次
成功迭代覆盖，并保留其 `iterationPath`。

### 9.5 本地检查是否注册成功

无需先启动 UI，可以直接扫描默认插件根：

```python
from pathlib import Path

from emo_master.core.plugin.registry import PluginRegistry


operatorId = "vision.example.multiply"
scan = PluginRegistry(coreVersion="0.6.0").scan(
    Path("src/emo_master/plugins")
)

if operatorId in scan.rejectedOperators:
    for issue in scan.rejectedOperators[operatorId]:
        print(issue.code, issue.message)
    raise SystemExit(1)

assert operatorId in scan.activeOperators
print("registered:", operatorId)
```

`scan()` 返回本次扫描结果，不会写数据库或修改中央状态。Runtime 初始化时会执行同类
扫描，并持有该结果。新增或修改算子后应重启 Runtime；Designer 重新获取 catalog 后
才会显示新定义。

### 9.6 外部算子根目录

外部算子也使用相同的 manifest 机制。创建 RuntimeService 时可传多个根：

```python
service = RuntimeService(
    dbPath=databasePath,
    pluginRootPaths=(
        "src/emo_master/plugins",
        "D:/vision_plugins",
    ),
)
```

注意：

- 显式传入 `pluginRootPaths` 会替代默认根；仍需 builtins 时必须把
  `src/emo_master/plugins` 一并传入。
- 扫描根只负责发现 manifest，不会自动修改 `sys.path`；外部 entry 对应的 Python
  包必须已安装，或其父目录已经在 Python import path 中。
- 同一个 `operatorId` 在任意两个 manifest 中重复时，这些声明会全部进入
  `rejectedOperators`，不会随机选择一个。

### 9.7 添加注册测试

至少补三类测试：

1. 算子单测：直接调用 `validateParams()` 和 `executeNode()`，覆盖成功、参数错误、输入
   错误和输出契约。
2. Runtime 注册测试：把新 `operatorId` 加入
   `tests/runtime/test_operator_registration.py` 的 expected 集合，确保它处于 active 且
   `rejectedOperators` 为空。
3. 契约/工作流测试：语义 DTO 覆盖 round-trip、未知字段、非有限值和坐标空间不一致；
   有图像/集合链路时再覆盖实际端口路由。使用 Homography 的算子还必须验证变换方向、
   连续组合、仿射互操作、奇异矩阵和 `w=0` 映射失败。

网络算子必须使用本机随机端口的 fake peer，不依赖现场设备；至少覆盖拆包、提前 EOF、
超时、畸形响应、远端拒绝、重试边界和资源关闭。通用发送不应默认重试非幂等消息。

推荐验证命令：

```bash
pytest -q tests/core/plugin/test_registry.py tests/runtime/test_operator_registration.py
pytest -q tests/core/contracts tests/plugins tests/runtime/test_builtin_vision_operator_workflows.py
ruff check src/emo_master/plugins/builtins/example_multiply
mypy src/emo_master/plugins/builtins/example_multiply
```

注册成功后再启动 Runtime/Designer 做一次实际连线和运行验证。看到节点只说明 metadata
已经注册；工作流运行成功才说明 `executeNode()`、端口路由和输出契约也正确。

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

### 11.2 manifest 已支持可选复杂编辑器

现在已经支持：

- 分类
- 图标
- 摘要
- 参数 schema
- `.ui + Controller` 独立窗口
- pure/live 两类 Runtime 预览

后续仍可继续补充：

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
