# emo_master Web 操作员面板：Dot 独立开发计划

日期：2026-10-30

状态：待实施；本文定义完整 Web 功能方向与代码分工，不表示本文的 Web 功能或新增接口已经实现。

本地交接更新（2026-10-31）：Runtime/Designer 增量已提交为 `d4ab96409e43d7ab6ae566820da357508d01658c`，工程格式为 **2.9** / Presentation **1.2**，基础布局复用 2.8/1.1。Dot 从仓库内的[固定交接入口](../handoffs/runtime-web-panel-v1/README.md)取得 SDK 1.1.0、合同 ZIP 和摘要；精确 RPC、schemas、TypeScript、fixtures 和样例包以同一次 manifest 为准。[Panel Contract v1](../development/panel-contract-v1.md)给出业务语义，接入与实施状态分别见 [远程访问指南](../guides/runtime-remote-access.md) 和 [本地实施记录](../testing/2026-10-30-runtime-web-integration/README.md)。本文的 Web 网关、浏览器 renderer、账户、包激活/回退及真正的 preview CLI 仍由 Dot 实现。

配套计划：[Runtime 远程客户端与 Designer 实施计划](./2026-10-30-runtime-web-integration-plan.md)。

## 1. 目标与交付形态

交付一个独立 Web 应用，让手机和其他电脑通过浏览器查看生产状态、实时结果、历史记录与图片，并执行授权的启动和停止。
页面由 Windows Qt Designer 设计和发布，Runtime 继续拥有工程配置、任务、设备、算法、生产调度和历史数据。
Web 网关与其他电脑 Designer 一样，作为普通 RPC 客户端连接同一个 RuntimeService / DisplayService 端点。
它装载页面包，为浏览器提供登录、页面渲染、结果订阅、资源转发及固定业务操作，不依赖某个已打开的 Designer。
“像 Designer 一样访问”表示复用同一 RPC、数据身份和业务语义，不伪造 Designer 名称或身份，也不把全部编辑功能开放到网页。
完整交付包括实时面板、历史查阅、生产运行入口、多人独立查看、页面包更新、账户权限和独立安装运行。

建议在独立仓库 `emo_master_web_panel` 开发；本轮只编写计划，不创建仓库、部署、修改运行电脑或发布软件。
Dot 负责这个独立仓库的后端、前端和开发用模拟 Runtime；本地开发者负责配套计划中的 Runtime、Designer 和协议源。
Dot 环境不被假定能访问用户 Windows Runtime，环境内模拟开发与真实 gRPC 客户端适配同步推进。

## 2. 与本地实现的责任划分

| 内容 | 本地 Runtime／Designer | Dot Web 应用 |
| --- | --- | --- |
| 通用远程接入 | 完善其他电脑 Designer 的局域网连接及各业务通道 | 以同一 RPC 合同作为另一个客户端接入 |
| 工程格式、页面目标、移动端布局编辑 | 定义、保存、迁移并生成页面包 | 按合同读取和渲染 |
| 页面包 | 从正式模型生成完整快照和静态资源 | 导入、校验、启用、切回旧版本 |
| 工作流、Job、Dispatch 组、周期生产 | 保持执行归属，提供业务查询和控制接口 | 提供操作界面并调用接口 |
| SQLite 历史数据和历史图片 | 依据正式在线工程解析配置并读取 | 提交受限查询、展示记录和资源 |
| 共享协议 | 维护 proto、DTO、schema、SDK 和脱敏样例 | 固定 SDK 版本消费，不自行分叉语义 |
| 登录、Web 会话、角色、操作审计 | 提供 Runtime 身份和受保护连接 | 实现浏览器访问和业务权限 |

Web 不装载执行器、设备 SDK、模型推理环境，不调用 Qt 控件；不维护第二个任务调度器。
本计划不增加网页工作流编辑、配方系统、任意 Python、任意 SQL、任意文件路径或直接 PLC 写入。
需要修改工程、数据绑定、历史数据库路径时，用户回到 Designer 正常配置并显式写入 Runtime。

## 3. 共同接口合同与开发输入

共同约定暂称 **Panel Contract v1**，是双方交接材料的集合；Runtime 协议源仍是同一份 `proto/runtime.proto`。
它不建立 Web 专用 Runtime 服务。已有 RPC 直接复用，新增运行和历史能力由 Designer 与 Web 共用；未实现的部分明确标为拟定。
合同版本与 Runtime 软件版本、工程格式版本、页面包版本分别表达，不能混为一个版本号。
本地提供独立包 `emo_panel_protocol`，只包含生成的协议代码、必要 DTO、Coverage 和 canonical／摘要逻辑。
SDK 同时提供 JSON Schema、TypeScript 类型、错误码说明和脱敏 fixtures；不依赖 Qt、设备驱动或推理库。
Dot 不通过 `pip install emo_master` 取得协议，也不从整个源仓库复制一套长期独立维护的协议实现。
需要新字段或改变语义时，在配套计划的合同源补充；模拟器、SDK 和真实 Runtime 使用同一版本。
交接材料使用统一目录，`contract-manifest.json` 记录合同版本、生成源码 SHA 和文件摘要：

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

| Runtime 能力 | 接口安排 | Web 用途 |
| --- | --- | --- |
| 实例、能力、正式在线工程身份 | 拟新增 `GetRuntimeInfo` | 连接信息、能力提示、目标校验 |
| 工作流目录及启动输入 | 复用 `ListWorkflows`，按需兼容补充生产默认输入和允许性 | 无 Job 时也能选择工作流；不从历史运行猜输入 |
| 运行列表与聚合状态 | 拟新增 `ListRuns`、`GetRunState` | 工作流／运行入口、Dispatch 组与周期状态 |
| 启动及不确定结果查询 | 沿用 `StartJob`、`GetStartRequest` 和现有版本校验 | 固定启动目标并接收新运行身份 |
| 停止 | 扩展 `StopJob` 的预期实例、工程、组等目标字段 | 操作明确选中的运行，保留原有停止语义 |
| 展示结果和资源 | 沿用 Display 元数据、快照、订阅、资源与 lease | 实时结果、图像、冻结详情 |
| 历史记录和统计 | 拟新增 `QueryHistoryPage`、`QueryHistoryAggregate` | 分页、筛选、排序、统计 |
| 历史和固定来源图片 | 拟新增 `ReadHistoryImage`，互斥接受查询得到的 imageRef 或正式固定图片 componentId | 从合法记录／来源取得图片内容，不提交磁盘路径 |
| 计数与任务事件 | 复用现有计数和事件接口 | 权威累计值、日志和告警记录 |

新 RPC 的最终字段、分页和错误合同由本地维护，Dot 可以提出消费需求，不能为模拟器另造不兼容版本。
工程 `schemaVersion: "2.8"` 与 `presentation.schemaVersion: "1.1"` 的基础响应式布局由 [Designer 画布改造](./2026-10-30-designer-canvas-alignment-responsive-plan.md)实施，具体字段与换行语义见[共享布局合同](../development/responsive-layout-contract.md)。实际支持状态以源码模型与正式工程规范为准；这两个版本不表示 Web 页面目标、远程历史来源、页面发布器或 RPC 已实现。
后续增加严格模型字段时，由本地重新确定版本和兼容边界，不继续把 2.8／1.1 当未使用的预留版本。发布页面 schemas、TypeScript 类型、fixtures 和样例包前冻结同一布局合同；早期 Web 使用明确标注为合同草案的页面包 fixtures。

## 4. 技术栈与代码组织

后端使用 Python 3.10、FastAPI、`grpc.aio`；前端使用 React、TypeScript、Vite 和 CSS。根据共享响应式语义选择 Flex／Grid 实现，不因渲染技术选择改变页面布局合同。
生产模式由后端同源提供前端构建产物和 `/api`、WebSocket；Vite 只作为开发服务器。
首个正式部署采用单 Web worker，共享上游订阅和有界资源缓存，不引入消息中间件或跨进程协调服务。
建议代码结构如下，目录名称可按仓库规范调整，职责保持清楚：

```text
backend/emo_web_panel/
  app.py                 # lifespan、路由和静态前端入口
  config.py              # Runtime 别名、连接、数据目录和部署配置
  runtime_client.py      # grpc.aio 通道与业务请求适配
  run_service.py         # 运行目标、状态、启停和请求结果协调
  display_bridge.py      # 共享结果订阅与资源获取
  view_sessions.py       # 每浏览器查看状态、冻结和生命周期
  history_service.py     # 合法来源查询、记录和图片转发
  packages.py            # 包导入、安装、启用、版本切回
  auth.py                # 用户、会话、角色和业务权限
  audit.py               # 操作记录与请求关联
  routes/                # HTTP、WebSocket 和资源端点
frontend/src/
  app/                   # 登录、运行入口、管理页、路由
  contracts/             # 由合同生成或发布的 TypeScript 类型
  renderer/              # 组件注册、共享响应式布局、样式、导航和动作
  state/                 # 目标、实时结果、候选结果、查看会话
  data/                  # HTTP、WS、图片加载和历史查询
  components/            # 业务页面和通用交互控件
dev/mock_runtime/        # 同合同的环境内模拟 gRPC 服务
dev/fixtures/            # 脱敏页面包、结果、历史及运行样例
```

`grpc.aio.Channel` 在 FastAPI lifespan 内创建和关闭，订阅任务和资源请求归属同一事件循环。
SQLite Web 状态库的磁盘操作使用受控线程适配；不在事件循环直接执行耗时文件读取或图片转换。
Runtime 客户端只暴露本产品需要的业务调用，不提供按 RPC 名称随意转发的通用代理路由。
使用与远程 Designer 一致的目标地址和通道参数语义；后端 async 适配不复制 Qt 界面及 Designer 内部控制器。

## 5. Runtime 连接与运行入口

管理员配置 Runtime 别名及连接资料，普通用户选择授权目标，不在浏览器输入任意服务器地址。
首页以 Runtime、工程、工作流和生产运行作为入口，展示可用页面、当前运行与最近结束运行。
工作流目录和输入描述取自 `ListWorkflows` 及明确的生产默认输入合同，无 Job 时仍可浏览、选择和启动。
`ListRuns/GetRunState` 提供运行事实；浏览器不根据 Job 列表猜测哪个 Job 才是“当前生产”。
Dispatch 展示采用明确运行组及成员关系，分别绑定生产者、消费者的结果作用域，不把同一工程的任意两个 Job 拼组。
连续生产显示 Runtime 返回的运行模式及可用单轮状态；对 continuous 工作流，启停作用于选定的生产根运行。
具体可控制动作随 Runtime 合同能力显示；本方向不新增独立周期调度开关，通信周期配置仍由 Designer 显式写入。
用户可查看旧运行结果，但控制栏始终展示单独确认的当前控制目标；查看旧记录不会改变控制对象。
Runtime 重启或目标失效后更新连接状态与运行列表，重新取得明确身份，不能沿用旧实例的资源或命令目标。

## 6. 页面包导入、启用与更新

页面包扩展名拟定为 `.emoweb`，内容为 ZIP，格式标识为 `emo-web-panel-1`，由 Designer 生成。
Web 不成为第二套页面编辑器。
manifest 至少包含 `format/projectId/packageRevision/panelContractVersion/rendererRequirements`。
包包含完整 presentation 快照、所需来源及绑定、共享响应式布局、页面导航配置和静态 assets 清单；后续明确支持的终端覆盖随同一版本合同交付。
快照遵循本地交付的 `schemas/published-presentation.schema.json`，是完整的派生渲染描述；不要求它原样通过包含本机路径属性的工程主模型校验。
Dot 按该 schema 读取，页面和绑定仍只有 Designer 一个编辑来源，不把派生格式反向保存成工程。
历史来源只携带 `projectId + componentId + historyDefinitionRevision` 及必要列元数据。
发布器仅将已显式配置并写入 Runtime 的历史／路径来源导出为这些受限引用，完整保留展示结构。
`display_local` 来源不会在导出时自动转换为远端授权；本地文件如需随包交付，由 Designer 明确转为静态资源。
固定 Runtime 图片按已声明 image 组件 ID 读取，选中行图片继承来源表格位置并使用查询返回的 opaque ref。
Web 不直接调用本地文件读取 Props，也不把草稿中的路径当作 Runtime 已授权来源。
历史数据库路径、图片根目录和可执行查询不作为浏览器可提交的参数；Runtime 根据正式在线工程解析。
页面包不能授权 Runtime 访问新目录或数据库，也不能借导入包修改生产工程配置。

管理员导入包后可查看工程、版本、页面列表、所需合同能力和与已配置 Runtime 的关联。
安装包保存到版本目录；启用仅改变 Web 当前使用的包引用，不改变运行中的 Job 或 Runtime 工程。
可切回已安装旧版本，旧版仍按其数据定义校验，不把“切回页面”伪装为“回滚生产配置”。
默认页面和导航只指向本包实际发布的 Web 页面，组件引用、静态资源引用随包完整解析。
Web 只接受按 ID 引用的包内资源，解析到安装目录中的相对文件，HTTP 不暴露主机绝对路径。
包更新通知现有浏览器，由用户切换或在明确的页面重载点切换；一次渲染只使用一个包版本。
当前页面被删除时返回运行首页或新默认页面，显示已切换说明，不保留失效组件和旧资源引用。
仅改布局、文案、样式时可继续读取兼容的运行结果；改变采集来源后使用 Coverage 显示具体缺失来源。
历史定义版本失配时，相关历史组件提示重新发布或同步正式配置，不能阻断无关实时组件。
绑定定义更新必须经 Designer／Runtime 正常配置流程生效，Web 不偷偷替用户写入或重启任务。

W2 同时交付 `emo-web-panel preview` 本机预览入口，使用同一 renderer 和已构建前端，不维护第二套预览页面代码。

本地启动器已新增 SDK 1.1.0 `PreviewContext` 交接：CLI 接收 `--package PATH --context CONTEXT_JSON`
及 `--host 127.0.0.1 --port 0 --json --no-control`。按 `mode=fixtures/runtime` 分别读取示例数据或固定 Runtime；
核对包摘要、实例、工程和历史声明后，只提供已有运行选择、结果与历史查看。完整字段、两种 fixture、断线后的
身份检查和 ready 输出见 [Panel Contract v1](../development/panel-contract-v1.md)。不自行推断 Designer 连接，
不把只读预览接入正式启停/激活接口。
入口接收 Designer 导出的临时完整快照和示例／只读数据配置，仅绑定 loopback 临时端口，以机器可读结果返回预览 URL 和短期访问凭据。
Designer 负责进程启动、打开浏览器和退出清理；Dot 负责临时快照装载、预览服务和前端渲染，不要求本机安装 Node。
预览状态与正式包激活、用户数据库分离，退出后清理临时数据；预览不提供生产启停、不写入 Runtime 配置。
草稿历史来源使用样例数据；连接正式历史来源必须匹配在线声明。完整启动参数和输出 JSON 随 Panel Contract v1 交付。

## 7. 完整页面组件与动作

完整 renderer 覆盖当前已有的 13 种组件，按组件和来源组合实现，不只做“图片＋文字”的展示样例。

| 组件 | 应实现的行为 |
| --- | --- |
| `image` | Runtime 结果、包内静态图、合法固定历史来源和选中行图；保持比例、空态、失败态 |
| `text`、`number` | 静态／绑定内容、单位、小数位、空值、字体和样式 |
| `indicator` | 按配置映射布尔或字符串判定，不能把任务完成等同产品 OK |
| `runtime_status` | 连接及运行状态，明确与业务判定分开 |
| `table` | 单次结果集合、列字段映射和分页，跟随该次结果身份 |
| `navigation_button` | 文字、图标、动作和目标页；遵守已发布页面范围 |
| `container` | 嵌套布局、标题和样式，保留子组件身份 |
| `page_title`、`page_navigation` | 当前页面标题、页面选择和导航状态 |
| `clock` | 格式化当前时间，时区规则明确，不冒充检测记录时间 |
| `sqlite_table` | 远端历史分页、筛选、排序、刷新和行选择 |
| `sqlite_value` | 按已配置定义读取 count/sum/avg/min/max 等统计 |

三种现有动作 `navigate/freeze/resume_live` 保留语义；冻结查看不暂停检测，也不冻结任务状态提示。
按共享响应式合同渲染页面及嵌套容器：上下、并排和自动换行使用组件数组的稳定顺序，采用相同的最小／首选宽度、填充比例、对齐和间距规则。Qt 桌面也消费这组基础规则；Web 不按旧行列坐标自行猜测手机排列。窗口变化只调整布局，不重建数据会话或清空表格选择。
触屏支持正常点击、表格滚动和图片查看；操作栏窄屏换行，不遮挡结果或导航。
组件样式使用明确的属性映射，禁止直接把配置当任意 HTML、脚本或 CSS 执行。
时钟默认使用浏览器时区并显示配置规则；记录时间使用数据中的时间戳，不在前端重新生成检测时间。

## 8. HTTP 与 WebSocket 业务入口

以下是 Web 自身建议路由，最终命名可统一调整；Runtime RPC 合同不随 HTTP 命名变化。

| 路由 | 职责 |
| --- | --- |
| `/api/session`、`/api/logout`、`/api/me` | 登录、退出和当前权限 |
| `/api/admin/users`、`/api/admin/runtimes` | 账户及允许连接目标管理 |
| `/api/packages`、`/api/packages/{id}/activate` | 包导入、列表、查看和启用 |
| `/api/panels/{id}` | 已启用页面描述及可访问页面 |
| `/api/runtimes/{id}/workflows` | 授权工作流目录、输入描述和生产默认值 |
| `/api/runtimes/{id}/runs`、`/api/runs/{id}` | 运行列表和明确目标状态 |
| `/api/runs/start`、`/api/runs/{id}/stop` | 固定业务启动与停止 |
| `/api/operations/{id}` | 查询一次已提交操作的结果 |
| `/api/views`、`/api/views/{id}/freeze`、`/resume` | 查看会话及冻结／恢复 |
| `/api/views/{id}/history/{componentId}` | 已发布历史来源的分页和统计查询 |
| `/api/assets/{id}`、`/api/history-images/{id}` | 经授权的编码资源内容 |
| `/ws/views/{id}` | 元数据、状态、失效和包更新通知 |

资源、历史、控制和 WebSocket 都经过同一会话授权；ID 是定位信息，不自动构成访问权限。
HTTP 控制返回 `operationId` 和实际受理状态，浏览器用其展示等待、成功、失败或结果待确认。
WS 承载消息和图片引用，图像内容走 HTTP；不把大幅 base64 图片塞进所有状态消息。

## 9. 消息合同与同轮结果展示

WS envelope 包含 `protocolVersion/type/sessionEpoch/targetId/pagePackageRevision`。
结果保留 `runtimeInstanceId/jobId/resultScopeId/resultKey/resultOrdinal/executionRevision/capturePlanRevision`。
每来源保留 `sourceId/state/valueJson/image/reasonCode/reason`，结果保留 `status/executionTerminal`。
图片引用包含 `resourceId/ownerResultKey/mimeType/byteSize/sha256/url`，不能只有一个会复用的“最新图片 URL”。
另传连接和运行状态、`reset_required`、最新开始序号、过期作用域序号、资源不可用和包更新事件。
所有 proto 64 位游标、序号和可能超出 JavaScript 安全整数范围的数字使用约定字符串表示。
时间字段明确是 UTC 时间还是某个进程的单调时钟；不同机器的单调时间不能直接比较。

前端每个 scope 保存候选结果和当前展示结果，新元数据与所属图片一起准备。
当前页面所需图片加载完成，或明确进入失败状态后，整份 scope 的图片、数值和判定一起切换。
图片失败时显示本轮失败信息，不能保留上一件图片并配上本轮 OK/NG。
各 scope 可以独立前进，不把互不相关流程的结果强行拼为一个原子整页。
换 Job、换包、切页或接到 reset 时使旧候选请求失效；延迟到达的图片不能盖回新目标。
重连先取得完整快照，再按游标续订；网页显示离线、最后更新时间和正在更新的实际状态。

## 10. 共享订阅、资源与每用户查看状态

单 worker 内按 Runtime 实例和 Job 共享一条上游订阅，多个浏览器各有有界发送队列。
Runtime 的订阅配额也供本机和远程 Designer 使用；本地计划将现有两条上限改为有界可配置值，Web 不假定自己独占所有通道。
Dispatch 组按明确成员分别订阅和归属 scope，使用现有组身份及 Coverage，不合并不相关任务。
导航、冻结、历史筛选、翻页和行选择属于各自查看会话，不修改共享实时数据。
共享缓存按实例、Job、资源 ID 和摘要识别字节，相同图片并发获取合并为一个任务。
上游图片读取保持有界并发，对齐 Runtime 的资源配额；不能每个浏览器同时重复读取同一大图。
PNG／JPEG 已编码图片直接转发；静态非目标浏览器格式优先在发布时转换，转换产物使用自己的摘要。
确需在 Web 转换的历史图片使用独立可选图片依赖和有界缓存，不引入 Qt 或推理库。

冻结固定用户实际看到的 `resultKey`，保留已取得的元数据及编码图片，并使用有限 TTL 和字节预算。
冻结跳转详情页需要尚未取得的图片时，短暂 AcquireLease，拉取指定结果资源后 ReleaseLease。
过期或缺失来源显示具体原因，不能用最新结果替代；不自动无限续租，也不沿用桌面 PinStore 的两窗口配额。
冻结时连接与任务状态仍更新；恢复实时切到该 scope 当前最新可用结果，释放自己的冻结引用。
页面切换取消无用请求，前端替换图片后回收 Blob URL，服务端在无人引用和到期后释放资源字节。
最后一个查看者离开时取消其共享上游订阅；浏览器关闭不调用 `ReleaseJob`，不停止生产任务。
Web 退出时取消并等待自身任务、释放自身 lease、关闭 channel；Runtime 继续保持运行事实。

## 11. 启动、停止与生产操作反馈

启动固定 Runtime 实例、工程、工作流、在线配置版本和一次稳定请求 ID，接收 Runtime 返回的运行身份。
同一启动意图在重复点击、刷新、超时后的查询中使用同一关联，复用 `GetStartRequest` 确认结果。
用户明确发起新的运行才创建新请求 ID，不通过永久禁用按钮阻止合法连续操作或多任务运行。
停止固定已选择的实例、工程、运行组／Job 和停止方式，目标变化后要求重新选中实际对象。
连续生产由 Runtime 已有根运行执行，Web 不通过定时重复 StartJob 模拟周期生产，也不自行增加周期配置写入接口。
对运行组的展示和停止范围以 Runtime 返回成员和支持操作为准，不由浏览器遍历碰巧可见的 Job 批量停止。
请求不确定时先查询；Runtime 已确定终态后界面应有完成、失败或重新发起的正常出口。
操作权限和允许目标由后端检查，审计记录操作人、请求标识、目标、受理结果与最终状态。

## 12. 历史、计数、事件的数据分工

历史分页发送已发布来源身份、定义版本、页码／分页标识、允许的筛选列和排序条件。
Runtime 按正式工程中的数据库和表配置读取；Web 不接收 SQL、数据库路径或图片路径作为查询入口。
记录返回显示字段及合法的图片引用，行选择只影响当前浏览器，原图和结果图可分别绑定。
刷新、筛选、排序和翻页按既有语义清除旧选择，图片空值或失败清空旧图并说明原因。
历史统计通过 `QueryHistoryAggregate` 读取，是否跟随筛选由发布定义明确，不能无声改变桌面统计语义。
累计产量和 OK/NG 统计读取 Runtime 权威计数或已保存统计，不按浏览器收到的帧数累加。
日志和告警记录使用任务事件 sequence 补读，不能以 Display 最新值流充当完整事件历史。
展示“当前状态”和“历史记录”时分别标明来源与时间，历史数据可在生产未运行时查询。

## 13. 用户、会话及 Web 自身状态

角色采用 `admin/operator/viewer`：管理员管理账户、连接与包；操作员控制授权目标；查看者只读。
管理权限是否包含生产操作在权限表中明确，不因可以导入包就默认授权所有 Runtime 操作。
HTTP、WS、图片和历史资源统一使用已登录会话；同源部署使用 HttpOnly / SameSite Cookie、Origin 和 CSRF 处理。
Cookie 的 Secure 设置与实际 HTTP / HTTPS 部署一致，局域网 HTTP 模式不配置一个无法发送的 Secure Cookie。
退出及会话过期使查看连接和后续资源读取失效，正在运行的任务不随 Web 会话结束而停止。
一个 Web 状态 SQLite 可保存用户、会话、包激活信息、审计和操作请求关联；密码保存为适当哈希。
Runtime 是运行状态和生产配置的权威，Web 数据库不能保存一份自行推进的第二任务状态机。
启动 Web 时恢复配置和包，重新查询 Runtime；未确认操作按原请求身份查询，不自动重放。
登录凭据放在独立配置／凭据位置，不加入页面包、前端代码、普通日志或脱敏 fixtures。

## 14. 独立部署与日常使用

交付独立 Python 分发／安装入口及已构建前端，声明只需哪些 Web 依赖，不要求用户安装 Node 开发工具。
第一版面向受控局域网：同机使用 loopback gRPC，跨机直接使用明确的 Runtime IP / 端口和普通 gRPC。
浏览器可访问局域网 Web HTTP 地址；已有 HTTPS 反向代理时可按实际部署接入，不要求应用自建证书管理。
TLS / mTLS 是后续有实际部署需要时的扩展，不属于本轮必做功能；局域网 RPC 端点不作为公网接口。
Web 主机、端口和启动方式由部署配置明确，程序不自动改防火墙、网络或系统服务。
日常操作包括登录、选择运行、查看／控制、查历史，以及管理员导入和启用新页面包。
停止 Web、升级 Web 或切回页面包只影响浏览器服务；生产任务和设备仍由 Runtime 管理。

## 15. 功能实施阶段

| 阶段 | 具体代码产物 | 可使用的功能 |
| --- | --- | --- |
| W1 合同与应用骨架 | SDK 消费、同合同模拟器、grpc.aio 适配、账户、同源应用 | 登录并连接明确 Runtime，显示能力与运行列表 |
| W2 页面包与基础 renderer | 包导入／启用、组件注册、共享响应式布局、静态资源、预览启动器 | 打开发布页面；向本地 L5 交付可直接接入的真实浏览器预览 |
| W3 实时结果与查看会话 | 共享订阅、资源代理、staged scope、冻结／详情、重连 | 图像判定一致，多用户独立查看并正常切换任务 |
| W4 完整历史和状态面板 | 历史表格／统计／图片联动、权威计数、事件补读 | 查历史记录和图像，展示生产状态及事件 |
| W5 生产操作与日常管理 | 启停、Dispatch 运行组入口、请求协调、权限与审计 | 对明确目标操作，沿用 Runtime 连续运行语义，并更新／切回页面包 |
| W6 独立交付 | 安装入口、前端构建、配置例子、部署及升级说明 | 在指定 Web 主机独立运行，与本地实现联调 |

这些阶段用于组织代码，最终交付范围包含 W1—W6；不能把模拟器页面称为已经接通用户的 Runtime。
本地接口尚未交付时，Dot 按相同合同完成模拟实现和真实客户端适配，并准确标记待接入部分。

## 16. 交接输入与输出

本地交给 Dot：版本化 SDK／schema／TS 类型、RPC 业务说明、错误语义、完整样例包、脱敏结果和历史 fixtures。
本地还需交付通用远程客户端连接示例、工作流目录／启动输入、运行组／连续运行状态定义、图片及事件配额、历史定义修订规则和支持能力清单。
Dot 交回：独立仓库提交、后端及前端产物、本机预览启动器、Web 配置示例、模拟器、支持组件矩阵和未满足的合同需求。
接口缺口以具体用户动作、请求和缺失返回字段说明，避免双方分别猜测目标关系或新增不同名的同义状态。
最终联调使用用户选择的机器与网络路径；Dot 环境无法直连时，由本地运行相同版本并记录实际结果。

## 17. 与功能一起完成的简短验证

验证重点是完整正常链路：导入包、登录、选择运行、图片与判定同轮、冻结详情、历史选行、启停反馈和包更新。
复用同合同 fixtures 验证模拟与真实适配输入输出，关注一次断线、慢图片、重复点击和多用户状态不串扰。
安装验证使用独立 Web 依赖，确认页面运行不需要 Qt、设备 SDK、模型或整个 emo_master 安装。
报告注明实际运行环境和未完成项，模拟 Runtime、真实软件 Runtime、浏览器及现场设备结果分别记录。
本计划不要求新增故障排列矩阵、消息平台、后台补偿框架或另一个任务调度器。
