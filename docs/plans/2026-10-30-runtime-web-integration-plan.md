# emo_master Runtime 远程客户端与 Designer 实施计划

日期：2026-10-30。状态：**已进入实施。本地 Runtime、Designer、协议 SDK、历史服务和 Web 页面发布器已落地于工作区；Dot 的真实 Web renderer、网关和跨端联调尚未交付。**

实施基线为 `0fa2115e337e7499688e4d0efd50539e34104a6a`，当前实现含未提交修改，不能把基线 SHA 当作全部功能代码。接入方法见 [Runtime 远程访问指南](../guides/runtime-remote-access.md)，共同开发材料见 [Panel Contract v1](../development/panel-contract-v1.md)，功能与验证边界见 [实施记录](../testing/2026-10-30-runtime-web-integration/README.md)。下载目录中的原始计划仍保留其编写时状态。

| 阶段 | 当前实现状态 |
| --- | --- |
| L1 | 已提供独立 `emo_panel_protocol` SDK、统一 proto、schemas、TypeScript、合成 fixtures、canonical 向量和当前格式样例包 |
| L2a | 已提供显式局域网监听、连接配置贯通、运行读模型、精确停止与桌面已有运行查看 |
| L2b | 已提供远程首次导入候选、声明资源上传、校验及显式提交，以及按声明下载工程资源 |
| L3 | 复用 2.8/1.1 响应式布局；本阶段新增字段正式使用工程 2.9 / Presentation 1.2 |
| L4 | 已提供与 Job 独立的历史分页、筛选、排序、聚合和图片 RPC，并接入 Qt 历史组件 |
| L5 | 已提供目标/来源属性、路径隔离的完整 `.emoweb` 发布器；预览可明确选择 fixtures 或当前 Runtime，并传递只读连接上下文；真实浏览器预览待 Dot CLI/renderer |
| L6 | 本地协议与 Qt 协同已做定向验证；另一台实体电脑、Dot 网关/浏览器、冻结包和真实设备未执行 |

2026-10-31 已将本地实现固定到提交 `d4ab96409e43d7ab6ae566820da357508d01658c`。
Dot 使用仓库内的[交接入口及合同 ZIP](../handoffs/runtime-web-panel-v1/README.md)开始开发；该包从干净提交生成并记录摘要。

本文由本地 emo_master 开发承担，配套文档为 [Web 面板 Dot 开发计划](./2026-10-30-web-panel-dot-development-plan.md)。
本地部分先完善其他电脑 Designer 对 Runtime 的正常访问；Web 网关作为另一种普通 RPC 客户端使用同一接口。
两份计划共同完成远程工程操作、实时查看、历史查询、受控启停和页面发布更新；执行、设备与正式工程配置始终归 Windows Runtime。
本文涉及 Designer、工程模型、协议 SDK 和发布器，均由本地开发负责，避免在两地开发之间留下无人实现的部分。
这里的“本地开发”指代码在本地 emo_master 仓库实现，不表示 Runtime 只能接受同一台电脑的客户端。

## 1. 目标、基线与职责

用户在本机或其他电脑的 Qt Designer 中编排流程、配置设备并设计页面，Windows Runtime 继续承担实际生产执行。
Runtime 向这些 Designer 及 Dot 开发的 Web 网关提供同一套 RuntimeService / DisplayService RPC。
Web 网关把浏览器 HTTP / WebSocket 转为所需 RPC；它用自己的连接访问，不伪造 Designer 身份，也不依赖运行中的 Designer。
正常使用结果是：Designer 关闭后网页仍可使用；网页关闭后生产任务仍继续；桌面与网页能看到彼此启动的运行。

```text
本机 Designer ───────────────┐
其他电脑 Designer ── gRPC ──┼── Windows Runtime ── 相机 / 算法 / PLC / 业务数据
浏览器 ─ HTTP/WS ─ Web 网关 ─┘
```

本计划核对的源码 HEAD 为 `1d4a200f262bc2f15b14a555bf78f286930ba760`。
工作区另有未提交的 SQLite 页面组件、路径图片、页面编辑器等改动，不能将本计划描述为该 SHA 上已全部存在的能力。
实施前重新检查分支、SHA、适用 AGENTS.md 和工作区差异，保留现有修改及历史证据，避免重复实现已完成的功能。
本文最初核对的工程格式为 `schemaVersion: "2.7"`、Presentation `1.0`。随后用户授权 Designer 响应式页面改造，由[共享布局合同](../development/responsive-layout-contract.md)推进工程 `2.8`／Presentation `1.1`；实际支持状态以最新源码模型和工程规范为准，这两个版本不表示本文的远程接入和 Web 功能已经实现。
本文不授权合并、正式发布、安装、网络变更或操作真实相机 / PLC；开发使用临时工程、本地图像和模拟设备。

| 交付内容 | 本地开发责任 | Dot 开发责任 |
| --- | --- | --- |
| Windows Runtime 接入 | 同一服务 owner 的局域网端点、实例身份与通用 RPC | 作为普通 RPC 客户端连接、展示错误与在线状态 |
| 其他电脑 Designer | 连接配置、在线工程读写、资源传输、结果与历史查看 | 复用相同 RPC 的操作员子集，不承接 Designer 编辑功能 |
| 运行状态与启停 | 统一运行读模型、Qt 同步、现有启停合同补齐 | 工作流 / 运行组列表、选择、操作反馈 |
| 实时结果 | 现有采集合同、capture 覆盖与共享客户端纯逻辑 | 上游订阅、浏览器分发、资源缓存与渲染 |
| 历史数据与图片 | 正式来源声明、查询 / 聚合 / 图片 RPC | 查询表格、筛选分页、统计、选中行图片 |
| 页面设计与工程格式 | 新 schema、编辑保存、目标与布局、发布器 | 按发布包和合同渲染，不修改工程主格式 |
| 用户登录与角色 | 按现有业务合同校验目标、版本及操作条件 | 浏览器会话、查看 / 操作权限、固定业务 API |
| 共同开发材料 | Panel Contract v1、SDK、schemas、类型、fixtures、样例包 | 消费同一版本材料并反馈实际缺口 |

不默认承诺网页流程编排、配方编辑、复杂 ROI 编辑、任意脚本执行、任意文件读取或任意设备写入。
既有算子、设备驱动、任务调度与 PySide2 / QWidget 技术栈继续复用，不以新增网页端为由迁移整个产品。

## 2. 共同合同与开发交接

共同合同暂定名为 **Panel Contract v1**，它是两边开发交接材料的名称，不表示另建一套 Web 专用 Runtime RPC。
`proto/runtime.proto` 继续作为唯一 Runtime 协议源；Designer 与 Web 网关复用其中相同方法、身份和错误语义。
新增运行查询、历史读取等能力也供远程 Designer 使用，Web 只暴露操作员需要的功能子集。
合同同时定义数据身份、页面语义、发布包、运行控制和历史查询，不以某个语言的内部类作为跨团队边界。
本地维护合同源，双方通过版本及能力声明协作；新增可选能力允许增量扩展，改变既有语义须更新合同版本。

建议本地交付独立 Python SDK **`emo_panel_protocol`**，具体构建目录按仓库组织确定。
SDK 只含 protobuf 生成代码、必要 DTO、`decodeResult` 等纯数据转换、canonical revision 算法及 Coverage 判断。
SDK 不引用 RuntimeService、Supervisor、设备管理器、Qt、OpenCV、推理库或整个 Designer RuntimeClient。
可复用的纯逻辑从现有模块局部抽取或共享，避免维护两份结果身份 / revision 判定实现。
尤其拆开 Coverage 到 snapshots 的纯函数依赖，不把现有文件直接搬入 SDK 后又传递导入完整工程 / 插件库。
Runtime 和 Designer 的工程适配留在仓库内，SDK 接收规范化 DTO / payload；独立安装只需声明的协议与数据依赖。
Web 仅安装该 SDK 和自己的 Web 依赖，不安装 `emo_master`，不复制设备 SDK、模型或业务数据库。

| 本地交接产物 | 必含内容 | Dot 使用方式 |
| --- | --- | --- |
| `Panel Contract v1` 文档 | 身份字段、方法、状态、错误、能力与兼容规则 | 实现固定 HTTP / WS 适配及界面语义 |
| SDK 构建产物 | 生成协议、DTO、canonical / Coverage 工具 | Python 网关使用，版本锁定 |
| JSON schemas | 发布包、页面、消息、历史查询 DTO | 导入校验、前后端共同约束 |
| TypeScript 类型 | 与 schemas / 协议来源一致的类型及枚举 | 浏览器渲染器与 API 客户端使用 |
| 脱敏 fixtures | 实时结果、无采集、终态、换 Job、历史分页 / 图片、控制结果 | Dot 可独立开发正常功能与反馈状态 |
| 页面包样例 | `emo-web-panel-1` 完整快照、静态资源、历史句柄 | 上传、预览、激活和回退开发 |
| 客户端连接示例 | 同机 loopback、受控局域网 IP / 端口及通道选项 | Designer / Web 共用连接定义，不自动改机器网络 |

统一交接目录建议为 `panel-contract-v1/`：

```text
panel-contract-v1/
  contract-manifest.json
  proto/runtime.proto
  python/emo_panel_protocol-*.whl
  schemas/
  typescript/
  fixtures/
  sample-panels/
```

`contract-manifest.json` 记录合同 / SDK 版本、实际源码 SHA 和交付文件摘要，Dot 据此锁定同一组输入。
交接目录不包含生产凭据、机器配置或用户数据库，示例图片和数据使用可分发的脱敏材料。
跨语言 canonical 算法必须明示字段选择、排序、UTF-8 和数字处理，提供同输入同 revision 的示例向量。
不让浏览器自行猜测工程 revision、capture 范围或历史来源身份，也不从本地绝对路径推导服务地址。

## 3. 通用远程客户端接入

### 3.1 Runtime 的局域网端点

第一版面向受控局域网，沿用普通 gRPC 和现有 insecure_channel，通过明确的 IP / 端口连接。
Runtime 提供“允许局域网客户端访问”、监听地址和固定端口配置；其他电脑 Designer 和 Web 网关使用同一端点。
未启用局域网访问时保留已有仅本机入口；固定端口占用时明确报错，不静默换端口，不自动改防火墙或网卡。
连接配置属于应用部署设置，不写入业务工程或页面包；局域网端点不作为公网接口。
TLS 仅作为以后有实际部署需要时的扩展，本轮不要求实现 mTLS、证书签发、分发、更新或撤销体系。

生产宿主仍只有一个 RuntimeService、PresentationService、Supervisor 和设备 owner。
本机查看与远程客户端共用执行和资源归属；若保留两个监听绑定，也共用服务对象和订阅配额。
Runtime 的生产模式随能力信息返回，避免将普通开发实例当成连续生产宿主。
`GetRuntimeInfo` 为建议新增的通用只读合同，返回实例、协议能力、生产模式、当前工程和在线 revision；已有接口足够时复用其事实来源。
断开 Designer 或 Web 客户端只释放其连接和查看资源，不关闭 Runtime、不停止 Job、不卸载工程。

### 3.2 Designer 的连接必须贯通全部客户端

已有 `services/runtime_connection.py` 能连接 host:port，不需要另写一套 Designer；补充可保存的连接配置和正常切换流程。
建立小型无 Qt 的 ConnectionProfile / channel factory，第一版只处理目标地址、超时、消息大小等现有 gRPC 选项。
RuntimeClient、DisplaySession、DispatchDisplaySession、预览日志和历史 adapter 都取得同一连接配置，不能主窗口连上而子窗口仍回到 loopback。
优先保持各查看会话拥有自己的 channel 并在关闭时释放；借用 channel 时明确不代替连接 owner 关闭，避免关一个窗口断掉全部功能。
设备和通信客户端继续复用当前 RuntimeClient 注入的 stub / call，不新增设备 owner 或另一套设备网络连接。
当前 Runtime 连接切换时同步更新连接配置、实例身份和各观察会话；老目标未完成的回复不能落入新目标。

### 3.3 远程 Designer 的正常工程路径

使用既有在线快照、readBack、差异比较和配置写入机制完成：连接 Runtime → 读取当前在线工程到草稿 → 编辑 / 本地保存 → 比较 → 上传新资源 → 显式写入。
本地保存只保存 Designer 草稿；写入仍带 Runtime 实例和在线 revision，保留当前查询、取消及受影响任务处理语义。
补按工程声明资源 ID / 摘要下载资源的有限合同，例如 ReadProjectResource，使另一台 Designer 可以查看静态图并取得所需工程资源。
资源以字节和声明传输，不让 Runtime 打开 Designer 电脑上的绝对路径；生产输出和业务库不作为普通工程资源批量下载。

完整远程初始化列为本地开发项：复用现有配置 writer 和资源传输模块，支持无在线工程时提交候选声明、上传声明资源、校验后显式提交到 Runtime 受管工程目录。
沿用请求 ID、状态查询和取消，导入成功不自动启动工作流；不新增任意磁盘路径、目录管理或脚本执行接口。
这需要补首次导入的资源归属，不能只删除 initializeProjectConfiguration 的远端检查；旧服务不支持时仍提供现有执行端导入入口。
`local_project_path` 的本机目录重定位语义继续保留本机限制；带本地 resource_root 的旧 Prepare / 图片测试不自动变成远程工程导入。
正常远程结果查看从当前 Runtime 连接选择已有运行，使用相同 Display / History RPC，不依赖手填 loopback 的旧预览入口。

服务端主要改动在 `apps/operator_runtime`、`apps/runtime/grpc_server`、`apps/runtime/project_config`；Designer 主要改动在连接、工程配置、页面预览与共享客户端模块。

## 4. 统一运行状态与启停语义

### 4.1 运行读模型

基于既有 JobRepository、Supervisor、Dispatch group 和 Presentation 状态，提供 `ListRuns` / `GetRunState` 等只读合同。
方法名为建议名称，最终以统一 proto 为准；不为此新增独立生产状态数据库或第二套任务 owner。
返回 Runtime 实例、projectId、workflowId、jobId、运行组关系、执行状态、资源占用与展示可用性。
同时给出来源名称、启动时间、当前可执行动作及拒绝原因，让两端按同一事实显示运行状态。
当前活跃运行和历史运行明确区分，合法的同工作流多次 / 并发运行分别保留身份。

工作流目录复用已有 `ListWorkflows`，保留 ID、名称、入口标志及输入 / 输出描述；没有任何 Job 时也能选择工作流。
将生产模式、允许用于启动的输入默认值、可启动性及原因纳入明确的只读合同，现有返回不足时兼容扩展字段。
Web 不从最近一次 Job 反推工作流目录、生产输入或运行许可；真正启动时仍由 Runtime 按当前状态受理。

修正现有桌面缓存场景：本地 J1 已结束后 Web 启动 J2，Qt 不能因 session 仍记着 J1 而显示 READY。
Qt 状态刷新、启动判断和停止目标解析都读取 Runtime 权威状态；Web 使用同一个读模型。
`ProductionRuntime.sessions` 保留启动请求协调与查看选择，不再作为所有活跃运行的唯一事实来源。
查看历史 J1 与控制活跃 J2 是不同选择；不能在用户点击停止后静默改为另一个“最新 Job”。

### 4.2 启动与停止

继续使用 `StartJob` 按工程 / 工作流创建新 Job，传入生产输入、capture 选项和在线配置版本。
沿用 `start_request_id`、`expected_runtime_instance_id`、`expected_document_revision` 及 `GetStartRequest`。
同一次用户启动意图的重复点击、超时查询和断线恢复复用同一请求 ID，不用新 ID 自动补执行。
不同用户明确发起的独立启动是不同意图，允许性仍由现有配额、设备归属和业务规则决定。
不增加禁止同工作流并发的全局锁；若界面提供批量启动，逐项返回已接受、被拒绝和待确认的结果。

扩展 `StopJobRequest`，新增可选的预期 Runtime 实例、工程和运行组身份字段。
旧客户端省略新增字段时维持原合同；新 Web / Qt 路径传完整身份，Runtime 验证后才停止指定 Job。
停止请求已被接受与全部工作进程 / 设备资源已退出分别展示，不能用终态字符串替代资源退出事实。
停止超时后查询同一运行的真实状态，不自动改目标，不把停止不确定转成自动重启。

### 4.3 Dispatch 与连续生产

复用现有 `run_group_id`、`source_job_id`、`target_slot_id` 和 `GetDispatchQueueState`。
将根运行及消费者放在同一运行组下，消费者可以选看结果，但不伪装成独立生产入口。
停止整个生产运行明确指向根 Job，复用 Supervisor 已有的组停止逻辑，不在 Web 逐个猜测子 Job。
连续生产仍在现有 productionMode 与工程 production 设置下执行，不由 Web 循环重复调用 StartJob。
对 continuous 工作流，网页启动 / 停止明确作用于生产根运行，循环由现有 Runtime 执行；内部单轮状态按可用字段展示。
本方向不额外承诺独立的周期调度启用 / 停用开关，通信周期配置仍走既有 Designer 显式写入路径。
新增网页控制不改变周期配置归属、设备串行访问、Dispatch 背压及取消后不补执行的行为。

## 5. 页面与工程格式

工程 **2.8**／Presentation **1.1** 的基础响应式布局由 [Designer 画布改造](./2026-10-30-designer-canvas-alignment-responsive-plan.md)实施，字段和语义见[共享布局合同](../development/responsive-layout-contract.md)。本次核对后，页面目标、来源位置等新增严格模型字段已使用工程 **2.9**／Presentation **1.2**，不占用已交付的响应式版本。
工程规范、AGENTS.md、新建路径、迁移与兼容提示已同步。本次普通打开/保存保留旧工程版本，明确应用新增语义时升级；旧版客户端不保证可读取 2.9/1.2。
新建仍沿用 `WorkflowStore().toPayload()`、ProjectDocument 校验和 saveProject，不只替换 schemaVersion 字符串。
同时核对 `WorkflowStore.reset()` 的完整默认结构；`migration.SCHEMA_VERSION` 是历史迁移基线，不能直接当最新版本常量修改。
保留页面、资源、production 与设备结构，`devices.schemaVersion="1.0"` 不因新增 Web 而删除或降级。

每页新增 `targets` 数组，允许 `desktop`、`web` 或两者，默认 `["desktop"]`。
旧工程迁移后保持桌面行为；明确升级保存，不批量重写已有现场工程及旧版兼容夹具。
严格模型新增字段后，旧客户端可能拒绝新工程，文档和界面须明确兼容边界，不承诺旧版完全可读。
回退依赖迁移前工程备份和旧发布包，不设计静默丢字段降级保存。

Qt 桌面页面与 Web 共用基础响应式布局：页面 `layout`／容器 `grid` 描述上下、并排或自动换行，组件 `responsive` 描述尺寸、比例和对齐；子组件数组顺序决定展示顺序。Qt 也随窗口宽度重排，不能仅为网页保留响应式行为。
首次交付页面 schemas、TypeScript 类型、fixtures 或样例包前，冻结与共享布局合同及实际模型一致的字段、默认值和换行语义，并在同一次合同交付中同步；本地与 Dot 不分别猜测新的字段。
若后续需要终端布局／样式覆盖，应另行明确字段和版本，继续引用原 componentId，不复制绑定、动作、查询来源和执行逻辑。CSS 实现由 Dot 负责，基础排列语义仍按同一合同。
componentId 继续保持整个 Presentation 全局唯一，作为引用和历史来源句柄，不额外建立来源 registry。

`SqliteSource` 新增 `location: "display_local" | "runtime"`，旧声明默认 `display_local`。
`display_local` 保持现有桌面读取语义；网页使用的动态历史来源必须显式选择 `runtime` 并写入在线工程。
数据库路径、表、列、排序、聚合及图片关联声明仍归正式工程，迁移不擅自把本地路径改成 Runtime 路径。
Designer 提供明确“由 Runtime 读取”的配置 / 关联入口，说明数据文件由哪台 Runtime 访问，不自动转换已有来源。
Qt 运行中的 `runtime` 来源也通过相同 history adapter / RPC 读取，网页与桌面共用数据归属和查询语义。
`display_local` 继续在桌面适配器读取；完整 Web 支持不意味着浏览器可以直接读取用户电脑本地磁盘。
网页发布发现仅本地可读来源时，Designer 提示具体组件和修改入口，不能发布后悄悄空白。

`ImagePathSource` 的固定路径模式同样增加 `display_local/runtime` 位置；选中行模式继承来源表格的位置。
不允许选中行图片另指向不同位置而改变数据库字段的含义；旧声明迁移后仍按原本地规则读取。
固定 Runtime 图片由正式工程中的 image 组件声明路径和允许根目录，按组件 ID 读取，不要求先建立历史表格。
需要把本地文件随网页交付时，由用户明确转为工程静态资源，不能把导出本地文件悄悄变成远端文件读取授权。

## 6. 历史数据与图片服务

### 6.1 正式来源与身份

Runtime 只接受当前正式在线工程中 `location=runtime` 的声明；草稿或上传页面包不能直接注册读取来源。
来源句柄使用全局唯一 componentId，查询上下文带 Runtime 实例、projectId 和 `historyDefinitionRevision`。
下文 `sourceHandle` 就是来源组件的 componentId，不再维护第二套 ID 映射；wire 字段统一采用 `component_id`。
`historyDefinitionRevision` 从规范化历史声明派生，包括查询列 / 排序 / 聚合 / 图片关联及相关路径声明。
它不取界面时间戳、Job ID 或布局版本；布局调整不应无故使同一历史来源失效。
声明版本变化时拒绝陈旧查询并返回明确原因，Web 刷新对应发布配置或提示需要重新发布。

历史查询独立于实时 Job 生命周期：没有运行任务、任务已停止或当前查看另一个 Job 时仍能查询。
复用 `core/presentation/sqlite_reader.py` 的只读访问、标识符校验、分页与聚合逻辑。
路径解析使用 Runtime 正式工程目录及声明规则，不让 Web 上传数据库或图片绝对路径作为读取参数。
确需共用的路径图片解析从现有 UI 模块抽为纯核心函数，Runtime 不导入 QWidget 组件。

### 6.2 建议新增 RPC

| 方法 | 请求 | 返回及用途 |
| --- | --- | --- |
| `QueryHistoryPage` | 上下文、sourceHandle、页码 / 页大小、声明允许的筛选 / 排序 | 类型化列、行、分页信息、选中行图片 opaque refs |
| `QueryHistoryAggregate` | 上下文、sourceHandle、声明允许的筛选 | 按声明计算的 count / sum / avg / min / max 与状态 |
| `ReadHistoryImage` | 上下文、互斥的 opaque imageRef 或已声明固定图片 componentId | 编码图片、MIME、尺寸及可用性；不接收任意路径 |

查询只接受声明中的表 / 列和明确支持的条件，不接受任意 SQL、表达式或数据库连接字符串。
固定图请求按 componentId 找到正式在线工程中的 runtime 图片声明，核对来源摘要后读取；选中行图片仅使用查询返回的引用。
分页、排序和筛选沿用桌面可理解的语义；有稳定主键时用其确定同值行顺序，声明无稳定键时说明行为。
数据仍在写入时，分页和统计按各次只读查询得到的当前数据库内容展示，不承诺跨多次请求的长期一致快照。
表格刷新后选中行由返回的行身份重新核对，不使用浏览器行号去猜测另一页图片。

图片引用由 Runtime 根据已声明来源和查询到的记录生成；图片读取接口和 opaque ref 不接受或暴露文件路径。
仅用于图片关联、未选为表格显示列的字段不随查询结果外发。若作者明确把同一字段选为显示列，
表格保留该声明列的原值，不静默遮改；不希望显示路径时，应在表格显示列中移除该字段，图片关联仍然有效。
引用包含或关联来源版本、行 / 图片字段、有效期等必要信息；Runtime 读取时验证并在允许目录内解析。
引用过期后重新查询生成新引用，不沿用实时 Job 的 lease，也不因实时任务结束销毁历史查看能力。
图片不存在、字段为空、格式不支持分别返回可显示状态，正常翻页和再次选行仍可继续。
实现采用有限请求和有限引用寿命，复用既有资源处理，不建设通用文件服务器或持续扫描所有图片目录。

## 7. 实时采集与跨端结果

沿用 DisplayService 的 Job 身份、快照、游标、订阅、资源 ID、有限 lease 与结果作用域语义。
页面目标过滤只影响终端展示；Runtime capture 必须覆盖桌面页和网页专用页共同需要的结果来源。
调整 `presentationForWorkflow(..., capture=True)` 及 captureDefinition 调用链，避免桌面看不到的页导致来源未采集。
派生 capture 定义仍按工作流 / Dispatch scope 归属，不把所有工程来源塞入每个 Job。
共享页由各 Job 采集自己所属的来源，组合规则和 Coverage 由合同及 SDK 明确定义。

没有启用 capture 的 Job 仍可读执行状态；界面明确说明页面来源未采集，查看动作不自动重启生产任务。
新启动请求按发布页面需要启用 capture，旧运行仍使用启动时的采集定义，不能假装热补已缺失来源。
网页包匹配判断使用 projectId、来源 / scope、capture 定义与合同要求，不只比较一个 UI 包版本字符串。
纯布局更新可作用于兼容的当前运行；需要新增采集来源的更新显示原因并等待明确的新运行或配置操作。

Runtime 每 Job 的 Display Subscribe 配额由本机查看、远程 Designer 和 Web 网关共同使用，不按浏览器数量放大上游流。
现有每 Job 两条订阅不足以同时承载这三类查看者；改为有界可配置值，初始建议四条，保证上述正常组合可使用。
本地交付原始编码资源与元数据语义，Dot 实现按 Runtime 实例+Job 共享订阅和有界浏览器分发。
当前数值 / 图片走 Display，运行状态走权威读模型，事件走 StreamJobEvents，累计数值取 Runtime 计数器。
不让浏览器通过收到多少帧计算产量，避免丢帧、刷新和断线导致静默错数。

## 8. Designer 编辑与发布闭环

在现有页面编辑器补充页面目标选择、Web responsive 属性、历史来源位置和图片关联配置。
新字段纳入 PresentationStore 的编辑、撤销 / 重做、保存重读、复制页面 / 组件和删除引用校验。
复制时重建全局唯一 componentId，并重写内部 selected_row、导航及相关引用；不得遗留指向原组件的句柄。
桌面导航只展示 desktop 页面，Web 导航只展示 web 页面；各端默认页应落在其可见页面集合中。
跨端不可达导航在编辑 / 发布时指出具体目标并提供修改入口，不让点击后无反应。

桌面预览继续 Qt；网页预览使用 Dot 的真正 Web renderer，传入完整预览快照与受控数据连接。
Dot 交付独立安装的 `emo-web-panel preview` 入口和已构建前端，本地 Designer 负责导出临时快照、启动与清理预览进程。
预览入口仅绑定 loopback 临时端口，返回含短期访问凭据的本机 URL，由 Designer 打开系统浏览器；两端约定机器可读的启动结果。
预览用独立临时状态，不导入 / 激活生产页面包、不提供生产启停，不要求用户安装 Node，也不向生产 Runtime 启动路径增加 Web 依赖。
草稿预览中的示例历史数据使用脱敏 fixtures；访问正式 Runtime 历史来源仍要求匹配在线声明。

本地启动器交接已补齐 `--context` 临时 JSON：固定所选主连接地址/参数、实例、工程、包摘要及正式历史声明。
SDK 1.1.0 的 `PreviewContext`、schema、TS 与两种 fixture 同步生成；后台连接协商不触发启停或工程写入。
Dot 读取该上下文并按同合同验证后连接；真实 renderer 的消费和浏览器展示仍属于 Dot 交付。
预览不发布工程、不启动任务、不覆盖在线配置；Designer 关闭后已发布网页不依赖其预览进程。
组件支持表由合同维护，包含实际 props、动作、数据来源能力，而不是仅判断组件名称。
已有历史表格、统计、选中行图片及实时组件在完整交付范围内，不以“复杂组件后续再说”替代实现。

## 9. 页面包与两条发布路径

页面包扩展名统一建议为 **`.emoweb`**，使用 ZIP 容器，包内 format 为 **`emo-web-panel-1`**。
构建时生成完整不可变快照，不依赖 Designer 临时文件或运行中的对象。
manifest 至少包含 `projectId`、`packageRevision`、`panelContractVersion`、`rendererRequirements`。
包内包含目标页面 presentation、共享绑定 / scope 定义、responsive 配置、静态 assets 与资源清单。
历史部分仅包含 sourceHandle、允许展示的字段 / 能力和 historyDefinitionRevision，不下发数据库 / 图片绝对路径。
这里的完整快照是按 `schemas/published-presentation.schema.json` 导出的渲染描述，保留全部必要页面语义，并非原样序列化含本机路径的 ProjectDocument。
该 schema 和导出转换由本地维护、随合同交付；它只描述派生产物，没有第二个编辑入口，Web 也不反向保存为工程。
静态图片、图标按资源 ID 打包；桌面固定路径图片需转换为声明的静态资源或明确 Runtime 图片来源。
包中不带模型、设备 SDK、凭据、生产数据库或业务输出目录副本。

发布分成两个明确动作：

1. **显式写入 Runtime 数据 / 执行配置。** 继续使用现有在线工程写入、CAS、资源传输及周期配置归属。
2. **上传并激活 Web 页面包。** Dot 校验格式、renderer 能力和目标身份后切换页面，不调用 StartJob 或工程写入。

只调整布局 / 文案 / 静态资源时可以只更新网页包；修改历史来源或采集绑定时说明还需哪条配置发布动作。
激活前旧包继续可用；激活失败保持旧包，回退恢复选定旧包，不重启生产任务。
网页更新后的新查询与订阅核对对应声明版本，不能靠旧包缓存掩盖来源不匹配。
发布器复用现有 package_builder / delivery 的资源收集能力，但区分开发测试包与正式 Web 页面包。

## 10. 本地实施阶段与具体改动位置

阶段按可交接功能组织；双方可以在合同和样例具备后并行开发，不要求先完成整套 UI 才开始联调。

| 阶段 | 主要文件 / 建议新增模块 | 具体产出与正常使用结果 |
| --- | --- | --- |
| L1 合同与 SDK | `proto/runtime.proto`、生成协议目录、建议 `packages/emo_panel_protocol/`、合同 schemas | 固定身份 / DTO / 错误，Dot 能用 fixtures 开发运行列表、页面和历史交互 |
| L2a 通用远程接入与运行读模型 | `apps/operator_runtime/controller.py`、`main.py`、`apps/runtime/grpc_server/aio_entry.py`、`service.py`、Designer 连接模块及 `clients/runtime` | 其他电脑 Designer 完整连接；显示 / 日志通道贯通，三类客户端共用运行事实 |
| L2b 远程工程操作 | `apps/runtime/project_config`、资源传输模块、`apps/designer/ui/project_configuration.py`、RuntimeClient | 读取在线工程、按资源 ID 下载、显式上传写入、首次远程导入；不依赖客户端本机路径 |
| L3 工程与采集模型 | `core/project/models.py`、`migration.py`、`core/presentation/models.py`、`validation.py`、`workflow_view.py`、`core/project/snapshots.py` | 基于已落地的响应式版本明确本阶段新增字段和迁移版本；桌面和网页页面使用同一绑定，网页专用来源参与采集 |
| L4 通用历史服务 | `core/presentation/sqlite_reader.py`、纯路径解析模块、建议 `apps/runtime/presentation/history.py`、RPC 适配、`ui/presentation/sqlite_widgets.py` / `path_image.py` | 远程 Designer / Web 使用同一历史 RPC；无 Job 也能翻页、筛选、聚合和读取选中行图片 |
| L5 Designer 与发布器 | `apps/designer/state/presentation_store.py`、`page_designer/property_panel.py`、`editing.py`、`workspace.py`、`delivery.py`、`core/project/package_builder.py` | 目标 / 布局 / 历史配置可编辑保存撤销，产出完整 Web 页面包并真实浏览器预览 |
| L6 协同与交付 | `apps/operator_runtime/main.py`、相关原生界面、合同与示例、安装 / 配置说明 | 完成本机 / 远程 Designer 与网页共同操作、发布更新和局域网连接说明 |

L1 的 SDK / fixtures 先交 Dot；L3 的 schema 变化及时同步生成类型；L4 提供历史接口后接替其模拟数据适配。
Dot 的 W2 同时交回 renderer 与预览启动器；本地 L5 使用它完成真实浏览器预览，L2a / L2b / L4 和 Dot W3 / W4 可并行推进。
L2a 首先把远程 Designer 接入现有 RPC，Web 据此使用同一协议；后续新增能力同样对两个客户端开放，不形成 Web 专用服务分支。
每次调整共用合同，更新 SDK 版本、schemas、TS 类型和受影响 fixtures，避免口头约定两端各猜一套。
仓库现有未提交页面改动先核对归属和接口，再在其基础上增量实现；不覆盖已有 SQLite / 路径图片功能。
新增模块只是局部组织建议，不能借此大范围重构 Runtime、页面编辑器、依赖和打包系统。

## 11. 功能确认与交付记录

确认围绕实际使用闭环进行：另一台电脑 Designer 连接、工程读取 / 显式写入、首次导入、三方查看、网页启停、桌面接管新运行、历史浏览与页面发布。
复用已有夹具补受影响正常路径和直接反例，避免把每种线程交错和故障排列组合变成开发前置条件。
重点保留 J1 结束后 Web J2 启动、两个合法运行选定停止、网页专用页采集、历史与 Job 独立的直接验证。
新工程样例使用届时正式最新格式，完成模型校验、保存重读、工作流编译；真实 Designer 打开单独记录。
源码检查、模拟 Runtime、浏览器、原生 Qt、冻结包和真实设备分别记录，不互相冒充。
结果使用 PASS、FAIL、BLOCKED、NOT_RUN，并保留已观察失败；未做现场设备操作就明确写 NOT_RUN。

本地最终交付代码与协议版本、SDK、schemas / TS 类型、脱敏 fixtures、页面包样例、迁移说明和接入配置示例。
同时交付与 Dot 对齐的接口清单、桌面协同结果、发布 / 回退说明及尚未完成事项。
本轮交付受控局域网的普通 gRPC 连接示例；TLS 不作为本轮交付条件，本文不自动更改防火墙、服务启动项或现场工程。
功能开发完成后可以推进其适用范围内的用户使用确认，不要求先清空所有非阻断风险或另建性能专项。
合并、正式发布、机器部署和现场设备结果各自记录，不以一句“全部完成”替代。
