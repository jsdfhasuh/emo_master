# Panel Contract v1：Runtime 客户端与 Web 页面交接合同

合同版本 `1.0`，Python SDK `emo_panel_protocol 1.1.0`。工程当前版本为
`2.9` / Presentation `1.2`，发布快照为 `published-1`，ZIP 页面包为
`emo-web-panel-1`（扩展名 `.emoweb`）。这些版本分别管理不同边界，不互相替代。

2026-10-31 的[固定交接入口](../handoffs/runtime-web-panel-v1/README.md)提供仓库内可直接下载的 ZIP、实现源码 SHA 和文件摘要。

本合同供远程 Designer 与 Web 网关共同使用 Runtime RPC。浏览器使用网关的固定
HTTP / WebSocket API；网关是普通 Runtime 客户端，不依赖运行中的 Designer。
执行、设备、正式工程和历史来源均归 Windows Runtime。浏览器登录、角色、网页
渲染、上游订阅共享、包激活及回退由 Dot 实现。

## 生成与追溯

在仓库 Python 3.10 开发环境执行：

```powershell
python scripts/build_panel_contract.py
```

默认输出 `build/panel-contract-v1/`，其中包括：

- `contract-manifest.json`：实际源码 SHA、是否含未提交修改、SDK 版本及每个文件的 SHA-256/长度。
- `proto/runtime.proto`：唯一 RPC 协议源；不另建 Web 专用 Runtime 服务。
- `python/emo_panel_protocol-*.whl`：只依赖 grpcio、protobuf、Pydantic 的协议 SDK。
- `schemas/`：从 SDK 模型和 protobuf 描述符生成的 JSON Schema Draft 2020-12。
- `typescript/panel-contract.ts`：由同一组 schemas 生成的结构类型；范围、正则及语义约束仍以 schema/SDK 为准。
- `fixtures/`：合成的实时结果、无采集、终态、换 Job、历史分页/统计/图片、控制反馈及错误示例。
- `sample-panels/demo.emoweb`：完整页面包；`sample-panels/source/` 是生成示例的当前格式工程与静态图片。

`--no-wheel` 仅用于开发，manifest 明确标为 `development-without-wheel`；不能作为完整 SDK 交付。
`--skip-proto` 仅在生成代码已同步时使用。生成器不执行工程、不连接 Runtime、不改现场网络。
manifest 的 SHA 表示仓库基线；`sourceDirty=true` 时新增能力来自工作区，不能把基线 SHA 当成全部实现。

示例工程经过模型校验、保存后重读、工作流编译和正常运行采集定义准备。运行状态组件不绑定为采集输出；
状态由权威运行读模型提供，样例结果仅包含可正常采集的 count/verdict 变量。示例源路径均为合成的相对路径；
历史数据库及图片引用仅供 fixtures 驱动的预览，示例不包含业务数据库。
`sampleValidation.designerOpen` 明确记录实际 Designer 打开是否执行，不把编译结果当成 UI 验收。

## RPC、连接和身份

第一版面向受控局域网，使用明确的 `host:port` 和普通 gRPC；不要求 mTLS。
本机例如 `127.0.0.1:50051`，远程例如 `192.168.10.20:50051`；地址仅为示例。
网关安装 wheel 后可直接连接，无需安装 `emo_master`、Qt、OpenCV 或设备 SDK：

```python
import grpc
from emo_panel_protocol.generated import runtime_pb2 as pb
from emo_panel_protocol.generated import runtime_pb2_grpc as rpc

channel = grpc.insecure_channel('192.168.10.20:50051', options=[
    ('grpc.max_receive_message_length', 9 * 1024 * 1024),
])
runtime = rpc.RuntimeServiceStub(channel)
display = rpc.DisplayServiceStub(channel)
info = runtime.GetRuntimeInfo(pb.RuntimeInfoRequest(), timeout=5)
# 将 info.runtime_instance_id 与所选 project_id 绑定到后续请求；检查 capabilities。
```

控制、Display、历史、日志通道使用同一明确端点，不在连接失败时回退到本机 Runtime。
重连后先取得当前 Runtime 实例与工程身份；旧实例的 Job、游标、lease、opaque image ref 不沿用。

| 需求 | RPC / 责任 |
| --- | --- |
| 实例、能力、正式工程及生产模式 | `GetRuntimeInfo` |
| 工作流目录、输入默认值及基本可启动性 | `ListWorkflows`；真正启动仍由 `StartJob` 校验 |
| 当前活跃及终态运行 | `ListRuns`、`GetRunState`；读取 Runtime 的权威运行状态 |
| 启动及不确定结果核实 | `StartJob`、`GetStartRequest` |
| 停止所选运行 | `StopJob`，携带预期 Runtime 实例、工程、运行组与准确 Job |
| 实时结果及资源 | Display `GetJob`、`Snapshot`、`Subscribe`、`ReadAsset`、`AcquireLease`、`ReleaseLease` |
| 历史 | `QueryHistoryPage`、`QueryHistoryAggregate`、`ReadHistoryImage` |
| 远程工程与资源 | 现有在线工程、CAS 写入、传输及 `ReadProjectResource`，用于 Designer；网页不开放任意工程写入 |

精确字段和 RPC 流式标志见 `runtime.proto` 与 `schemas/runtime-rpc.schema.json` 的
`x-rpcMethods`。该 schema 使用标准 protobuf JSON 命名：lowerCamelCase、64 位整数为
十进制字符串、bytes 为 Base64。Python gRPC 仍使用 protobuf 的 snake_case 字段。
`*_json` 是 JSON 文本字段，先按外层协议解码再解析，不重复转义成对象。
schema 主要描述结构；oneof 互斥、身份和当前状态的业务约束仍由 Runtime 验证。

## 运行、控制和网页通知

`runtimeInstanceId + projectId + jobId` 确定所查看运行，`runGroupId` 把根运行与 Dispatch
消费者关联。Job 的终态和资源释放是两件事；`ownsResources`、
`displayResourcesReleased` 不能从 `COMPLETED` 字符串推断。

同一次明确启动意图始终复用 `start_request_id`。超时后查询这个 ID，不生成新 ID 自动补执行。
停止准确作用于用户所选 Job；停止整组时选择根 Job。不能把历史 J1 的“停止”静默切换为最新 J2。
同一工作流可以存在多个合法运行，不新增禁止并发的全局锁。消费者不是独立生产入口。
continuous 循环由 Runtime 执行，Web 不循环调用 StartJob。

`schemas/browser-message.schema.json` 是 Dot 网关需要实现的通知格式，由
`emo_panel_protocol.messages.BrowserMessage` 定义；不是声明仓库已经有 WebSocket 服务器。
通知类型如下：

| event | 含义 |
| --- | --- |
| `result` | 已封闭结果与十进制字符串 cursor；按完整 identity 合并 |
| `capture_unavailable` | Job 未启用页面采集；仍可查看任务状态，不自动重启 |
| `job_state` | 从 `RunState` 映射的当前运行/资源状态与 allowedActions |
| `job_changed` | 明确选择切换；清除旧 Job 的显示状态，再读取新快照 |
| `control_result` | 启动/停止的 ACCEPTED、REJECTED、UNKNOWN；接受不等于退出完成 |
| `error` | code 与可显示 message，保留正常重选/刷新出口 |

浏览器控制通过网关固定业务接口完成，通知格式不开放任意 RPC 转发。
网关按 Runtime 实例 + Job 共享上游订阅，浏览器数量不直接放大 Runtime 流数量；
慢客户端有界丢旧帧，状态和累计产量仍读 Runtime，不能按收到多少帧计算产量。

## 结果、capture、revision 与数值

结果 DTO 为 `ClosedResult`：一个结果列出全部 expectedSourceIds，每个来源只出现一次，
AVAILABLE 含恰好一个 frozen valueJson 或 ImageRef，UNAVAILABLE 含原因而不夹带旧值。
`resultScopeId`、`invocationId`、`resultKey`、`resultOrdinal` 与 execution/capture revision
共同防止旧结果串入新任务或新作用域。网关使用 SDK `decodeResult` 和 `CaptureCoverage`。

页面目标过滤仅控制展示。Runtime capture 覆盖桌面和 Web 所需来源的并集，且仍按工作流/
Dispatch scope 分开。旧运行未采集的新来源显示 `SOURCE_NOT_CAPTURED`，等待用户明确启动新运行。
纯布局变化不重启生产，也不伪造采集覆盖；兼容性应检查来源、scope 和 capture definition。

revision 沿用现有 Python 算法，不能改成 JavaScript `JSON.stringify` 后假称等价：

```python
json.dumps(value, sort_keys=True, ensure_ascii=True,
           separators=(',', ':'), allow_nan=False).encode('utf-8')
```

对这些 UTF-8 字节取 SHA-256 十六进制小写。键递归按 Python 字符串顺序排序，非 ASCII
字符及非 BMP 字符转义，数组顺序保持；Python 浮点表示保留 `1.0`、`-0.0` 和指数格式，
整数任意精度，NaN/Infinity 拒绝。`fixtures/canonical-vectors.json` 给出实际输入、规范字符串和摘要，
包含中文、emoji、负零、指数浮点及超出 JS 安全范围的整数。

浏览器直接使用 Runtime/包提供的权威 revision。确需跨语言重算时，必须通过全部向量，
使用保留整数与浮点字面语义的解析器；标准 JSON.parse/JSON.stringify 会丢失部分信息。
protobuf 64 位字段保留十进制字符串；ClosedResult 的数值与 valueJson 也不得静默舍入超大整数。

实时图片使用 result_store resourceId 和有限 lease，不能把它当历史文件地址；固定和选中行
历史图片使用独立历史 RPC，不随实时 Job 终止而失效。

## 历史查询与图片

来源为正式在线工程中 `location=runtime` 的 componentId。上下文包含 Runtime 实例、
projectId、historyDefinitionRevision。页面包与草稿不能直接授予读盘权限；网页不提交文件路径或 SQL。
选中行图片继承同页 sqlite_table 的位置，固定图片声明自己的 Runtime 路径及允许根目录。

分页从 `page=0` 开始，`page_size` 1–100（0 表示默认 20），筛选为声明显示字段的文字包含匹配，
排序只能使用声明列。页和聚合各自查询当前数据库，不承诺跨请求的长期一致快照。
`HistoryPage` 精确描述 `HistoryReply.result_json` 的成功页；`HistoryAggregate` 描述统计。
`stableRowIdentity=false` 时不能跨刷新按旧行号恢复选择，应等待重新选择；稳定行 ID 也不代表行内容永久不变。

`imageRefs[图片componentId]` 返回 AVAILABLE + opaque imageRef、EMPTY 或 INVALID_VALUE。
图片读取请求和这些引用不接受或暴露文件路径；仅用于图片关联、未选为显示列的字段不随查询结果外发。
作者明确选入表格显示列的字段保留原值，包括同时用作图片关联的路径字段；不希望展示路径时，从显示列中移除该字段即可，图片关联仍然有效。
Runtime 引用当前有效期为 300 秒、容量有界；过期返回 `E_HISTORY_IMAGE_EXPIRED`，刷新记录后重新取引用。
读取图片返回 AVAILABLE、EMPTY、MISSING、UNSUPPORTED 或 TOO_LARGE；非 AVAILABLE 不展示旧图片。
图片字节支持 PNG/JPEG/BMP，最多 8 MiB、32M 像素，按组件声明的根目录解析。
fixtures 的 opaque refs 是演示值，不能发送给真实 Runtime。

## 页面、布局与组件支持

工程 `targets` 为 desktop、web 或两者；发布器仅输出 web 页面并保证默认页可达。
2.8/1.1 已有的上下/并排/换行布局继续使用，1.2 新增目标和数据位置，不另建断点网格编辑模型。
发布快照保留有序 children、responsive 尺寸/权重/对齐、grid 兼容布局和通用外观属性。

全部组件的精确字段见 PublishedComponent/PublishedProps schema。通用属性包括 title、text、emptyText、
unit、decimals、fontFamily/fontSize/fontWeight、textColor、cardStyle。每种组件还须满足下表语义：

| 组件 | 绑定 / 来源 | 额外属性与行为 |
| --- | --- | --- |
| image | image 绑定、静态 imageResourceId 或 history 三选一 | 保持比例；资源失效显示原因；fixed/selected-row 历史图片 |
| text | value 或静态 text | 文本展示，禁止当 HTML 执行 |
| number | value | unit、decimals；不可用不转换成零 |
| indicator | value | indicatorStates 按规范 JSON boolean/string 键匹配 |
| runtime_status | 原生 Runtime 连接/任务状态 | 正常运行时不绑定为 capture 输出；状态与结果产出分别显示 |
| table | rows | columns 字段路径和页大小；不猜测未知结构 |
| navigation_button | 无结果绑定 | iconResourceId；clicked 的 navigate/freeze/resume_live |
| container | children | 有序布局；children 仅容器可用 |
| page_title | 当前页面 | 页名与普通外观 |
| page_navigation | 终端可达页面 | tabs/buttons；不显示另一终端专用页 |
| clock | 显示端时钟 | 独立于 Runtime 结果周期 |
| sqlite_table | history kind=table | 声明列、分页、筛选、排序、选中行图片 |
| sqlite_value | history kind=aggregate | count/sum/avg/min/max；空统计保留空状态 |

navigate 必须指向可达页面；displayed_result 上下文和 freeze 需要明确 resultScopeId。
冻结/恢复只影响当前查看窗口，不暂停生产，不改变其他客户端。网页负责实际实现这些能力，
导入时核对 rendererRequirements，未知必需能力拒绝激活，不静默丢掉组件。

## 发布包、更新和预览

`.emoweb` 是完整不可变快照，包含 manifest.json、presentation.json 和按摘要命名的静态 assets。
发布器当前打包 PNG/JPEG/BMP；单资源最多 8 MiB、4M 像素，合计 64 MiB。
PublishedPresentation 排除 sqlite/imagePath 的本机路径，历史只保留组件句柄和展示能力。
导出前复用页面发布校验检查 Web 页面所用绑定、SQLite 配置、图片允许根目录和引用；
桌面独占的未完成组件不会阻塞独立 Web 页面导出。检查使用当前客户端算子目录，不执行算子或设备。
packageRevision 来自不含该字段的 manifest + presentation 的规范摘要；每个 asset 另有摘要和字节数。

两个发布动作分别执行：Designer 显式写入 Runtime 正式数据/执行配置；Dot 上传并激活网页包。
激活不调用 StartJob、不写在线工程。布局/文案/静态图片可独立更新；历史声明或新增采集来源变化
必须提示对应正式配置/新运行要求。激活失败保留旧包；回退只恢复网页包，不重启生产。

Dot 提供已构建前端和独立安装入口，用户不需安装 Node：

```text
emo-web-panel preview --package PATH --context CONTEXT_JSON --host 127.0.0.1 --port 0 --json --no-control
```

启动器只绑定 loopback 临时端口，stdout 第一行是一条 JSON：

```json
{"event":"ready","protocolVersion":"1","url":"http://127.0.0.1:54321/?token=short-lived-token"}
```

之后诊断输出到 stderr。Designer 负责临时快照、打开系统浏览器、关闭预览进程和清理临时状态。
Designer 提供“示例数据”和“当前 Runtime，只读”两个明确入口。`--context` 是单次预览的临时
JSON 文件，模型为 SDK `PreviewContext`，对应 `schemas/preview-context.schema.json` 和生成的 TS 类型。
SDK 1.1.0 增加此交接，页面包格式和 ready 消息保持原版本；Dot CLI 必须支持该参数后才能接入新版启动器。

共同字段是 `previewContextVersion="1"`、`mode`、`readOnly=true`、`projectId`、`packageRevision`
及 `historyDefinitionRevision`。CLI 先验证 schema，并核对上下文的工程、包摘要和历史声明与页面包一致。
`mode=fixtures` 不包含 Runtime 连接，仅使用同合同合成数据；不能把 fixture 图片令牌发给真实 Runtime。

`mode=runtime` 另含 `runtime.target`（host:port）、`timeoutSeconds`、`maxReceiveMessageBytes`、
`runtimeInstanceId` 和十进制字符串 `documentRevision`。连接来自 Designer 的 Runtime 菜单主连接，
不是桌面观察窗口另选的高级连接；本机嵌入 Runtime 会按需开放回环 RPC 端点，不创建 Job 或写配置。
Designer 在后台通过这个固定地址读取 GetRuntimeInfo，核对正式工程和历史声明后才启动 CLI。
预览启动后主连接切换不会将已打开的预览静默重定向。

Dot 用独立 RPC 通道消费此配置，启动和断线重连时调用 `PreviewContext.validateRuntimeInfo` 或等价检查，
核对实例、工程和正式历史声明。实例或历史声明变更后提示重新打开预览，不猜测新身份或静默改用 fixtures。
`documentRevision` 记录交接快照，不要求纯布局发布后继续等于原值；实时结果仍按所选 Job 的 Coverage 判断。
网页从同一实例/工程的已有运行目录中让用户明确选择 Job，调用已有只读状态、结果订阅和历史 RPC；
未启用 capture 时显示无采集，不自动启动。没有 Job 时历史查询仍可用。
两个模式均禁用 Start、Stop、工程写入、正式包导入/激活和回退；关闭预览只清理它自己的订阅、进程和临时文件。

`fixtures/preview-fixtures.json`、`fixtures/preview-runtime.json` 给出两种输入示例，后者地址为文档保留地址。
这条预览 CLI 是 Dot 的交付接口；本地 launcher 验证与真正浏览器渲染分别记录。

## 验证记录边界

合同生成和源码测试可验证 schema、fixture、SDK 独立安装及发布快照；本地模拟 Runtime 可以验证
协议和状态语义。真实浏览器、原生 Qt、冻结包、另一台电脑的连接与真实设备分别记录。
本合同不把未执行的层级标为 PASS，不授权部署、安装、提交、推送或操作现场设备。
