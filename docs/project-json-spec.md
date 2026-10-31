# 工程文件规范：当前版本 2.9

本规范适用于 `.emoproj` 和 `project.json` 中的工程 JSON。**新建、生成和交付工程使用
`schemaVersion: "2.9"`、`presentation.schemaVersion: "1.2"`，包括不使用设备的图片、视频和模型处理工程。**
旧格式可读不代表新工程应该继续生成旧格式；创建约束见 [AGENTS.md](../AGENTS.md)。

最后核对：2026-10-30；源码 HEAD：`0fa2115e337e7499688e4d0efd50539e34104a6a`，加本轮远程客户端页面模型工作区修改。
2.8/1.1 响应式布局已在基线实现；2.9/1.2 的终端与数据位置能力来自本轮工作区修改，不能归于该 HEAD 本身。格式推进时同步更新规范，不把历史版本号作为永久常量。

## 1. 格式依据与创建入口

格式的实现依据为 [ProjectDocument](../src/emo_master/core/project/models.py)；
新建默认值来自 [WorkflowStore](../src/emo_master/apps/designer/state/workflow_store.py)
的 `reset()` / `toPayload()`；读写使用
[project_store](../src/emo_master/apps/designer/state/project_store.py)。
顶层和各声明模型严格校验类型及未知字段；算子参数、设备字段等还需按插件声明校验。

在仓库 Python 3.10 环境中运行以下代码；先安装项目或将 `src` 加入 `PYTHONPATH`：

```python
from pathlib import Path

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.models import ProjectDocument

target = Path("new-project/New Project.emoproj")
payload = WorkflowStore().toPayload("New Project")
assert payload["schemaVersion"] == "2.9"
assert payload["presentation"]["schemaVersion"] == "1.2"
# Add workflow nodes and edges before validating the finished document.
document = ProjectDocument.model_validate(payload)
saveProject(target, document.model_dump(mode="json"))
reloaded = loadProjectDocument(target)
assert reloaded == document
```

新建入口会生成工程 ID、时间戳、默认对象和工作流边界节点。生成业务工程时保留这些
结构，填入工作流、资源和依赖后再次校验，不手抄过期算子端口或参数。

注意以下历史入口与常量：

- `migration.SCHEMA_VERSION` 当前为 **2.1**，是历史迁移基线，不是最新工程版本声明。
- `createProjectSkeleton()` 已复用 `WorkflowStore().toPayload()` 和 `saveProject()`，生成完整的 **2.9** 空白工程；业务工程生成器仍应按上述流程补充并校验业务配置。
- 默认 `migrateProjectPayload()` 保留已支持版本，不会把所有旧工程升级到 2.9。
- `enableRemotePresentation=True` 将已支持的 2.7/2.8 明确升级为 2.9/1.2，保持原网格或响应式布局、设备和绑定；更早工程须先走既有设备迁移，不删除旧通信配置。
  `enableDevices=True` 当前迁移目标仍为 **2.6**，也不能当作 2.9 创建入口。

新工程优先指定 `.emoproj` 文件名。扩展名与 schema 版本独立，`project.json` 也可承载
2.9。目录式保存 API 保留 `project.json` 默认名；目录内有多个工程文件时应选择明确文件。

## 2. 完整 2.9 示例

下面是可校验、可编译的无设备空白工程，不是省略字段的结构片段。
`projectId` 和时间戳仅作示例；创建独立工程时由创建入口重新生成。

```json
{
  "schemaVersion": "2.9",
  "project": {
    "projectId": "example-project-29",
    "name": "New Project",
    "revision": 1,
    "createdAt": "2026-10-30T00:00:00Z",
    "updatedAt": "2026-10-30T00:00:00Z"
  },
  "entryWorkflowId": "main",
  "workflowOrder": ["main"],
  "workflows": {
    "main": {
      "name": "Main",
      "inputs": {},
      "outputs": {},
      "nodes": [
        {
          "nodeId": "__workflow_input__",
          "kind": "workflow_input",
          "displayName": "Workflow Input",
          "inputPorts": {},
          "outputPorts": {}
        },
        {
          "nodeId": "__workflow_output__",
          "kind": "workflow_output",
          "displayName": "Workflow Output",
          "inputPorts": {},
          "outputPorts": {}
        }
      ],
      "edges": [],
      "layout": {
        "nodePositions": {
          "__workflow_input__": {"x": 20.0, "y": 20.0},
          "__workflow_output__": {"x": 360.0, "y": 20.0}
        }
      }
    }
  },
  "runtime": {
    "maxConcurrentJobs": 2,
    "gracefulStopTimeoutMs": 5000,
    "heartbeatTimeoutMs": 5000,
    "eventRetentionPerJob": 10000
  },
  "dependencies": {"operators": []},
  "devices": {
    "schemaVersion": "1.0",
    "bindings": {},
    "definitions": {}
  },
  "presentation": {
    "schemaVersion": "1.2",
    "defaultPageId": null,
    "pageOrder": [],
    "navigation": {"mode": "tabs"},
    "resultScopes": {},
    "dataSources": {},
    "pages": {}
  },
  "resources": {
    "schemaVersion": "1.0",
    "items": {},
    "parameterBindings": [],
    "siteBindings": []
  },
  "production": {
    "autoStart": false,
    "mode": "single",
    "cycleIntervalMs": 100,
    "inputs": {}
  },
  "globalVariables": {}
}
```

## 3. 顶层字段

下表区分模型要求和新工程推荐输出；生成文件使用上面的完整默认结构。

| 字段 | 2.9 要求及含义 |
| --- | --- |
| `schemaVersion` | 必填，字符串 `"2.9"`，不是数值 `2.9`。 |
| `project` | 必填；`projectId`、`name`、`createdAt`、`updatedAt` 为字符串，`revision` 为正整数，默认 1。ID 标识工程，时间由创建/保存流程维护。 |
| `entryWorkflowId` | 必填，引用已有工作流，作为未另行配置时的默认入口。 |
| `workflowOrder` | 必填，Designer 页签顺序；每个工作流 ID 恰好一次，不能遗漏、重复或引用不存在的流程。 |
| `workflows` | 必填且至少一个工作流；键为稳定 workflowId。 |
| `runtime` | 可由模型补默认值；新工程显式保存，见下表。 |
| `dependencies` | 默认 `{"operators": []}`；业务工程按实际算子依赖填写，不把空依赖列表当通用模板。 |
| `devices` | 2.9 必须包含 `schemaVersion: "1.0"`。`definitions` 按 deviceId 保存设备定义，无设备时为空；`bindings` 是保留字段，不能代替设备定义或节点绑定。 |
| `presentation` | 必须为非 null 对象；工程 2.9 要求内部版本 `1.2`，没有页面时也保留默认结构。1.2 不能写入 2.8 或更早工程；2.8/1.1 原格式继续可读。 |
| `resources` | 必须为非 null 对象；内部版本 `1.0`，没有资源时保留空计划。 |
| `production` | 必须为非 null 对象；不自动生产时保留默认设置。 |
| `globalVariables` | 按 variableId 保存声明，默认 `{}`，不保存运行中的变量状态。 |
| `communication` | **2.9 沿用 2.7 的统一设备规则，不使用旧顶层通信配置**；新工程不写此字段。非 null 的旧配置对象会被拒绝，模型序列化也会移除该字段。 |

### Runtime 与生产设置

| `runtime` 字段 | 默认值 | 约束 |
| --- | --- | --- |
| `maxConcurrentJobs` | `2` | 正整数或 `null`；null 仅取消 Job 数量准入上限，不关闭设备串行、资源归属等保护。 |
| `gracefulStopTimeoutMs` | `5000` | 非负整数，单位毫秒。 |
| `heartbeatTimeoutMs` | `5000` | 整数且至少 100，单位毫秒。 |
| `eventRetentionPerJob` | `10000` | 正整数。 |
| `startupWorkflowIds` | 不写 | 省略时使用 `entryWorkflowId`；显式设置须为非空、无重复的已有工作流 ID 列表，数量不能超过非 null 的 `maxConcurrentJobs`。 |

`production` 默认 `autoStart=false`、`mode="single"`、`cycleIntervalMs=100`、
`inputs={}`。`mode` 仅允许 `single` / `continuous`，间隔为正整数毫秒；
普通 Designer 单次运行不等于启用生产连续策略。

### 全局变量

`globalVariables` 每项包含 `name`、`type`、`initialValue`；
`type` 为 `boolean` / `integer` / `number` / `string`，初值须匹配类型。
`kind` 为 `constant` / `variable`（默认 variable），`lifetime` 为
`persistent` / `job`（默认 job）。名称在工程内唯一；`legacyCounterName` 留给兼容用途。
节点 `globalVariableBindings` 使用 `parameterPath` 与 `variableId` 引用声明；
文件/目录参数走资源或现场绑定，不通过普通标量变量绑定替代。
详见 [变量模型](../src/emo_master/core/project/global_variables.py)。

## 4. 工作流、节点与边

每个工作流含 `name`、`inputs`、`outputs`、`nodes`、`edges`、`layout`。
`layout.nodePositions` 按 nodeId 保存 `x/y`，仅用于布局。
工作流内 nodeId 不重复；边使用 `fromNode/fromPort/toNode/toPort` 引用实际节点端口。
普通边必须保持 DAG，循环通过结构化 Loop 调用子工作流。

| `kind` | 配置与含义 |
| --- | --- |
| `operator` | `operatorId`、`params` 以及当前插件声明的输入/输出端口和参数 schema；不能仅凭旧画布快照构造算子。 |
| `workflow_input` / `workflow_output` | 工作流边界节点，与工作流接口保持一致；默认 ID 为 `__workflow_input__` / `__workflow_output__`。 |
| `subflow` | `targetWorkflowId` 引用已有流程，端口从目标接口派生。 |
| `loop` | `loop` 声明结构化循环，见下节。 |
| `dispatch` | `params.targets` 声明目标槽和队列。 |
| `join` | `params.dispatchNodeId` 引用同工作流 Dispatch，等待其结果。 |

通用节点字段还包括 `displayName`、`inputPorts`、`outputPorts`、`paramSchema`、
`globalVariableBindings`、`deviceBindings`、`deviceOverrides`、`deviceValueBindings`。
模型校验不能代替编译器对算子、边、参数类型及引用关系的验证。

### Loop v2

新 Loop 使用 `contractVersion=2`；`mode` 为 `repeat` / `foreach` / `while`，
`bodyWorkflowId` 指向循环体。有限循环配置 `maxIterations`；While v2 还支持显式
`unlimited=true`。具体限制由 [Loop 契约](../src/emo_master/core/workflow/loop_contracts.py)
及编译器验证，不把旧版配置片段当成所有模式的必填字段集合。

- Repeat 继承 body 输入/输出，非零次执行返回最后一次 body 输出。
  `repeatCount=0` 不执行 body，每个输出必须有同名且类型兼容的输入，才能透传。
- ForEach 使用 `items:list<T>` 和共享输入；`itemInputPort` 指定逐项输入，
  可通过 `indexInputPort` 接收索引；body 每个输出分别聚合为 `list<T>`。
- While 的 body 输入/输出构成同名且类型兼容的状态。`conditionMode="workflow"`
  使用 `conditionWorkflowId`，条件流程从状态取输入并输出 `continue:boolean`。
- While 的 `conditionMode="boolean"` 使用 `conditionPort` 指定布尔状态端口；
  `conditionMode="globalVariable"` 使用 `conditionVariableId` 引用布尔变量。
  这两种模式不要求条件子流程。

端口由 `deriveLoopContract` 派生；节点的 `inputPorts/outputPorts` 是画布快照，
不是编译时的接口依据。旧 `contractVersion=1` 仅供兼容。

### Dispatch / Join

`dispatch.params.targets` 包含 1 至 32 个目标槽，每项为
`targetSlotId/workflowId/inputs/queueCapacity`，单槽容量为 1 至 64。
容量只计算等待项，不包含该槽位当前处理中的一项。

Dispatch 节点另支持两个可选参数，不改变工程顶层 schema：

| `dispatch.params` 字段 | 缺省值 | 语义 |
| --- | --- | --- |
| `queueFullPolicy` | `"error"` | `error` 保留满队列拒绝和部分接纳错误；`wait` 在所有目标有容量前等待；`skip` 在任一目标队列满时跳过整次投递，不阻塞采集、不向空闲目标提前扇出，输出 `status=SKIPPED`、`accepted=false` 的 token。只有队列满可跳过，消费者不可用仍报错。适合允许抽帧的实时预览，不用于必须逐帧处理的流程；Join 收到跳过的 token 仍报告未完成。 |
| `waitTimeoutMs` | `0` | 整数 0 至 2147483647；仅在 wait 模式生效。0 等待到可用或取消，正数是本次逻辑投递的总容量等待上限。 |

等待期间保留同一次输入和逻辑投递身份，不重新采图、不增加拒绝次数、不重放已接纳
目标。超时返回 `E_DISPATCH_WAIT_TIMEOUT`，消费进程退出返回 `E_DISPATCH_UNAVAILABLE`。
取消不会在之后补投递；通信故障或未知结果不自动重试。

`join.params.timeoutMs` 为 1 至 1200000 毫秒。
运行组必须一次容纳生产者和全部持久消费者，不自动重放失败调用。
消费者页面须将 `resultScopes.entryWorkflowId` 和 `scopeWorkflowId` 绑定至消费流程，
使用其实际输出；异步 Dispatch 不作为同步 `callPath`。消费 Job 每次调用独立发布结果，
页面观察不改变生产者的停止权限。队列查看见 [全局变量与队列](global-variables.md)。
详见 [设备与在线配置指南](guides/device-dispatch-online-configuration.md)。

## 5. 统一设备与 TCP

`devices.definitions` 使用稳定 deviceId 作键，每项保存
`name/deviceTypeId/driverVersion/enabled/description/connection/defaults/policies`。
`name`、`deviceTypeId`、`driverVersion` 必填；`enabled` 默认 true，描述默认空字符串，
后三个配置对象默认为 `{}`，实际字段、能力及策略由已安装设备插件声明校验。

- `deviceBindings` 按算子槽位映射到 `{deviceId, selectors}`；selectors 是静态选择项。
- `deviceOverrides` 按槽位保存明确的工作参数常量覆写；只有缺少才表示继承，
  `0`、`false` 或与默认值相同的值仍是显式覆写。
- `deviceValueBindings` 按槽位和参数路径绑定 `source="variable"` 加 `variableId`，
  或 `source="input"` 加 `inputName`；同一路径及父子路径不能有多个生效来源。
- 节点不能覆写设备连接和策略；引用的设备须存在，设备槽和可覆盖字段须符合插件声明。

2.9 沿用 2.7 的统一设备规则：TCP 与其他设备一样进入 `devices.definitions`，由算子绑定设备。
TCP Server、单次客户端、单次接收及通道 selectors 的完整规则见
[项目 TCP 设备](guides/project-tcp-service.md)。
不要复制 2.5/2.6 的顶层 `communication`，也不要为了满足新 schema 给本地视频、图片或
YOLO 处理流程添加虚假 TCP/相机设备。旧通信工程不能靠删除原配置或只改版本号升级。

基础模型见 [设备模型](../src/emo_master/core/project/devices.py)；
继承、资源归属等基础规则见 [设备契约](guides/device-catalog-contract-v1.md)。
该历史契约中的 2.6 顶层通信描述不作为 2.9 新工程模板。

## 6. 页面、资源与目录

新工程使用 `presentation.schemaVersion="1.2"`，空工程保留示例中的导航、页面顺序、结果作用域和
数据源结构。页面 `image.props.imageResourceId` 引用 `page_image` 资源，与动态绑定互斥；
`navigation_button.props.iconResourceId` 引用 `icon` 资源。执行期私有路径不写回工程 JSON。
页面字段详见 [页面契约](runtime-pages-p1-contract.md)，其中历史阶段的发布限制不是本规范的当前版本结论。

### 显示终端与数据位置 1.2

每页 `targets` 是非空、无重复的 `desktop` / `web` 数组，默认 `["desktop"]`，两个值同时存在表示两端均显示。默认桌面值可省略。Designer 编辑器保留所有页面；运行显示端只呈现对应终端的页面，并从可见页面顺序中选择可用默认页。导航目标必须在来源页面的所有目标终端可见；错误会列明具体导航组件。采集计划仍按工作流/作用域取得全部终端所需来源，不使用过滤后的显示配置冻结采集。

`props.sqlite.location` 为 `display_local`（默认，可省略）或 `runtime`。前者读取显示页面电脑的数据库，后者读取所连接 Runtime 正式工程声明的数据库；仅改变 UI 下拉框或导出页面包不能授权服务器读取。选择 Runtime 位置时，路径以 Runtime 正式工程目录解析，不能把 Designer 本机目录发送给服务端当资源根目录。数据库文件不因发布网页被复制。

固定图片的 `props.imagePath.location` 使用相同两值，默认 `display_local`。`kind="selected_row"` 不保存独立 `location`，只能继承同页来源表格的位置。Runtime 固定图和选中行图都须声明 `baseDirectory` 作为允许读取的根目录；读取接口只接受正式组件身份或记录生成的图片引用，不接受浏览器文件路径。本地图片改为随网页交付时，应明确转换成已有工程静态资源。

这些字段的非默认语义要求工程 `2.9` / 页面 `1.2`。2.8/1.1 与2.7/1.0仍按原格式打开保存；在属性中明确应用网页目标或Runtime来源，会把支持的2.7/2.8草稿升级到2.9/1.2并记录撤销，保存与显式写入Runtime仍是不同动作。旧客户端可能拒绝新格式，不提供悄悄删除新字段的降级保存。

### 响应式布局 1.1（1.2 继续沿用）

新页面使用有顺序的响应式布局；Qt 编辑预览和运行页面都随窗口宽度重新排版，
组件 ID、数据绑定、导航动作与查询来源不因换行而变化。基础语义也供未来 Web renderer 使用，
详见[响应式布局合同](development/responsive-layout-contract.md)。这次版本升级不代表 Web 发布、远程历史来源或新增 RPC 已实现。

页面的 `layout` 和容器组件的 `grid` 使用以下对象；`grid` 是沿用的字段名：

| 字段 | 默认值／约束 | 含义 |
| --- | --- | --- |
| `type` | `"responsive"` | 响应式布局标识。 |
| `direction` | `"vertical"`；允许 `vertical/horizontal/wrap` | 上下排列、可收缩并排、按首选宽度自动换行。 |
| `spacing` | `12`；0–128 | 控件间距和行间距，单位为逻辑像素。 |
| `alignment` | `"stretch"`；允许 `start/center/end/stretch` | 交叉轴默认对齐。 |
| `justify` | `"start"`；允许 `start/center/end/space-between` | 主轴剩余空间的对齐或等距分配。 |
| `equalWidth` | `false` | 并排的一行使用等宽分配。 |

组件新增 `responsive`：

| 字段 | 默认值／约束 | 含义 |
| --- | --- | --- |
| `width` | `"fill"`；允许 `fill/content` | 填充剩余宽度或保留内容首选宽度。 |
| `minWidth` | `160`；0–4096 | 并排换行阈值；窄于此值时仍限制在可用宽度内。 |
| `preferredWidth` | `240`；0–8192 | 首选宽度；0 使用内容测量值。 |
| `weight` | `1`；0–100 | 填充控件分配剩余宽度的权重。 |
| `minHeight` | `64`；0–4096 | 显示高度下限。 |
| `preferredHeight` | `0`；0–8192 | 0 自动测量；正数提供期望下限，不裁剪自然内容。 |
| `alignSelf` | `"auto"`；允许 `auto/start/center/end/stretch` | 当前控件的交叉轴对齐；auto 沿用父组。 |

数组 `components/children` 是响应式顺序的唯一依据；组件 `layout` 中旧行列跨度不参与响应式定位。
`horizontal` 宽度不足时换行，`wrap` 根据首选宽度换行；尺寸变化只重算几何，不产生工程历史或重建数据会话。
历史 `type="grid"` 仍用于明确的旧页面读取，不能把旧网格坐标当作新响应式页面的布局规则。
Presentation `1.0` 不接受响应式布局；读取旧工程时，未声明的页面内部版本按历史 `1.0` 处理，
不会因新增默认值自动升到 `1.1`。工程 `2.8` 与页面 `1.1` 必须配套，旧网格转入新页面编辑需要明确转换并通过模型校验。

### 页面组件与来源

页面不再强制生成整行蓝色导航。单页直接显示页面内容；多页保留工具栏中的紧凑切页入口，
也可以把 `page_navigation` 组件放到页面中。`page_title`（文字为空时跟随页面名）、
`page_navigation` 和 `clock` 都是可选组件，不绑定流程结果。

本地 SQLite 组件 `sqlite_table` / `sqlite_value` 通过 `props.sqlite` 保存查询配置：

| 字段 | 类型 / 默认 | 说明 |
| --- | --- | --- |
| `databasePath` | string / `""` | 显示页面的电脑上已有 SQLite 文件的绝对路径，或相对当前工程目录的路径；不作为项目资源上传、复制或打包。 |
| `table` | string / `""` | 已有表或视图的实际名称。 |
| `columns` | string[] / `[]` | 历史表格显示字段，按配置顺序展示，最多 32 个，不重复。 |
| `orderBy` | string / `""` | 排序字段；未配置时使用首个显示字段。 |
| `descending` | boolean / `true` | 是否倒序。 |
| `aggregate` | `count/sum/avg/min/max` / `count` | 统计组件的统计方式；`count` 统计记录数。 |
| `valueColumn` | string / `""` | 除记录数外的统计字段。 |

表格复用 `props.pageSize`（1–100），统计复用 `props.decimals` / `props.unit`。
这些组件不接受流程结果绑定或执行 SQL 文本；仅在显示页面的本机以只读方式访问数据库。
设计画布和模拟显示不查询数据库，预览的“本地数据”入口可独立查阅，不需要启动工作流。
数据库配置允许作为空草稿保存，发布前必须选择数据库、表及相应字段；发布不读取本机文件。
操作步骤与部署边界见 [页面组件与 SQLite 历史查阅](guides/page-local-sqlite.md)。

图像组件通过可选的 `props.imagePath` 配置外部文件图片；与 `imageResourceId` 及流程
`bindings` 互斥，不设置时省略此字段，原有图像组件的工程结构不变。

| 字段 | 类型 / 默认 | 说明 |
| --- | --- | --- |
| `kind` | `fixed/selected_row` | 固定文件路径，或同页 SQLite 历史表格的选中记录。 |
| `path` | string / `""` | `fixed` 使用的路径；`selected_row` 必须为空。 |
| `componentId` | string/null / `null` | `selected_row` 必填，同页 `sqlite_table` 的稳定组件 ID。 |
| `field` | string / `""` | `selected_row` 必填，已有数据库的图片路径字段名（可含空格，最长 256 字符），可以不在显示列内。 |
| `baseDirectory` | string / `""` | 相对图片路径的明确基准目录，可用绝对目录或相对工程目录的路径；`.` 表示工程目录。绝对图片路径无需配置。 |

`fixed` 不得配置 `componentId` / `field`。配置允许未完成的固定路径及失去关联的草稿保存，
发布前报告空路径或缺失/跨页来源。选中记录只存活于当前窗口，既不写回 JSON，也不进入
结果作用域、流程输入、采集清单或全局变量。复制整页/容器时重映射同时复制的内部组件引用。
图像异步读取显示页面的本机文件；原图、结果图可作为两个独立图像组件绑定同一表格的不同字段。
本图片来源扩展最初沿用工程 `2.7` / 页面 `1.0`，在 `2.8/1.1` 中继续保留；需要支持该字段的源码或构建产物。

Designer 只读预览支持同页绑定一个 Dispatch 生产入口和一个已声明消费入口的结果。
每个来源仍有独立 `resultScopeId` 和完整 `callPath`，各 Job 只采集自己的来源；预览必须
核对同一 Runtime、工程、运行组及生产/消费 Job 关系，不拼接无关任务。整个组合视图仍
最多两个不同图像来源。采集画面和检测图分别更新，不表示两侧来自同一帧；单侧连接
失败只清除对应区域，停止查看不停止业务。此组合入口不等于独立 Runtime 生产页也已支持。
图像组件可用 `props.emptyText` 为正常的可选输出缺失/分支跳过设置空态文案；执行或
图像导出错误仍显示错误，原始原因仍保留在提示中。

`resources.schemaVersion="1.0"`；[资源模型](../src/emo_master/core/project/resources.py)定义：

| 字段 | 内容 |
| --- | --- |
| `items` | resourceId 到资源声明：`path`、`sha256`、`size`、`purpose`。path 为规范化相对 POSIX 路径；sha256 为 64 位小写十六进制；size 为非负字节数。purpose 为 `input_image/model/page_image/icon`。 |
| `parameterBindings` | `resourceId` 与 `target` 的绑定。节点目标为 `workflowId/nodeId/parameterPath`；设备目标为 `deviceId/section/parameterPath`，section 仅允许 `connection/defaults`。 |
| `siteBindings` | 声明现场字段 `field`、`target`、`purpose`、`required`（默认 true）。purpose 为 `output_directory/output_file/device_address/camera_serial/secret_ref`。 |

绑定字段应由算子或设备插件声明用途，不猜测任意字符串都是路径。
输入媒体、模型、输出目录和 Runtime 数据目录应分开；输出文件不是输入资源清单。
资源摘要与大小须由实际文件计算，不能填占位值交付。

推荐目录结构：

```text
<projectDir>/
  New Project.emoproj
  New Project.emoproj.bak   # 覆盖保存已有工程时生成
  assets/
  outputs/
```

`saveProject()` 校验并规范化文档，覆盖时先备份，再通过临时文件原子替换。
仅保存本地文件不等于写入 Runtime，也不代表启动 Job；在线配置须走显式写入入口。
在线权威配置存于 Runtime SQLite，文档版本、执行版本与周期标识不能混用。

## 7. 历史格式与迁移边界

本节用于识别兼容输入，不是新工程模板。

| 历史版本 | 主要结构变化 / 当前迁移入口 |
| --- | --- |
| v1 / 2.0 | 读取时迁移到历史基线 2.1；旧 `version/meta/designer` 转换为 `schemaVersion/project/workflows`，节点坐标移至 layout，并补边界节点。 |
| 2.1 | 多工作流及 Loop 契约基线；不是当前新建目标。 |
| 2.2 | 增加必需的 presentation/resources；`enablePresentation=True`。 |
| 2.3 | 增加必需的 production；`enableProduction=True`。 |
| 2.4 | 增加 globalVariables 及节点变量绑定；`enableGlobalVariables=True`。 |
| 2.5 | 增加旧顶层 communication；`enableCommunication=True`。 |
| 2.6 | 增加设备目录 1.0、节点设备绑定及设备资源目标，同时保留 communication；`enableDevices=True`。 |
| 2.7 | TCP 统一设备化，不再使用旧顶层 communication；保留页面、资源、生产、变量和设备目录。 |
| **2.9（当前）** | 页面版本 1.2，增加 desktop/web 显示终端和 Runtime 数据位置；沿用 1.1 响应式布局，旧工程显式升级，不隐式改变本地数据库归属。 |
| 2.8 | 页面版本 1.1，增加上下／并排／换行布局与组件响应式尺寸规则；其余统一设备、资源、生产及变量结构保留。 |

[迁移函数](../src/emo_master/core/project/migration.py)的默认行为不是“全部升级到最新”。
旧 2.0 Loop 迁移时标记 `contractVersion=1`，保留旧 `results:list` / `state:object`
语义；新建或重新应用配置的 Loop 使用 v2。读取迁移不意味着自动覆盖源文件。
旧 TCP 到 2.7 涉及配置归属、节点绑定和行为迁移，不能宣称现有开关已完成通用迁移。

响应式模型不承担旧页面布局保真转换；旧工程读取与显式新页面编辑分别处理，不把仅改版本字符串当成转换成功。

除明确兼容/迁移测试或用户要求的旧版本交付外，不再新建 2.1～2.8 工程。
现存用户工程、历史夹具和证据文件不因本规范更新而批量改写。

## 8. 生成与交付检查

1. 对照当前源码确认新建 schema，使用完整默认结构，不仅改版本字符串。
2. 执行 `ProjectDocument.model_validate(payload)`，核对工作流索引、必需对象和设备目录。
3. 使用 `saveProject()` 保存，再用 `loadProjectDocument()` 重读，确认版本及内容保留。
4. 使用当前已安装插件注册表进行 `WorkflowCompiler` 编译，检查端口、边、参数和依赖。
   本文空白示例无算子，可用 `WorkflowCompiler({}).compile(document)` 验证。
5. 声称“Designer 可打开”时验证实际打开/切换入口；纯模型校验或直接 WorkflowRunner
   执行不能替代该验收。有业务媒体和模型时执行代表性流程，并记录实际结果。

格式、源码运行、原生 Designer、冻结包和真实设备验收分开记录，不将未执行的层次记为通过。
本文创建代码只写本地工程，不连接设备或提交在线配置。

## 9. 相关规范

- 跨工程复用流程及依赖：使用 `.emowf.json`，见 [工作流包规范](workflow-package-spec.md)。
- 设备、TCP 和在线写入：见 [项目 TCP 设备](guides/project-tcp-service.md)
  与 [在线配置指南](guides/device-dispatch-online-configuration.md)。
- 独立操作端和完整工程运行：见 [独立 Runtime](operator-runtime.md)。

工程 schema、流程包 schema、设备驱动 API 和算子版本各有独立含义，不能互相替代。
本规范更新不自动变更这些包格式，也不承诺旧平台能够运行新工程。
