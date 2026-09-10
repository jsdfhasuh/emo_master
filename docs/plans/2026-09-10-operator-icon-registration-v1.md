# 算子图标（图片资源）注册实现计划 v1

> 状态：`REVIEW_REQUIRED`（待用户审阅）  
> 日期：2026-09-10  
> 仓库：`jsdfhasuh/emo_master`  
> 目标分支：`agent/runtime-workflow-architecture-v1`  
> 本地远程跟踪名：`origin/agent/runtime-workflow-architecture-v1`  
> 代码核对基线：`0d5ae591267a265834b3db87a05864b45ee1cec8`  
> 本次交付：仅本计划文档；不实现功能、不新增实际图标、不调整发布配置。  
> 实施门禁：用户审阅通过后，才进入下面列出的实现阶段。

## 1. 要解决的问题与本轮边界

目标不是继续增加一个写死在 Designer 中的图标对照表，而是让**图标资源成为算子插件自身声明、注册、分发和显示的一部分**。

预期效果：开发者在插件目录中放入图片，并在 `manifest.json` 中声明相对路径；Runtime 扫描时校验并注册，Designer 通过接口获取资源，在算子选择卡片、画布节点和节点详情中一致显示。Designer 不需要为每个新增算子修改宿主代码。

本轮实现范围为：可选图标声明、资源安全校验、注册快照、gRPC 资源获取、客户端异步加载与缓存、主要 UI 入口接入、兼容与打包验收，以及少量示例资源。

本轮明确不包含：全量算子的独立图标设计、应用 EXE/窗口品牌图标、用户在界面上传图标、在线图标商店、任意 URL 下载、动态图标、完整 SVG 标准支持、全新主题系统、算子算法或端口变更、插件热重载，以及第三方 Python 插件安全沙箱。全量配图列为独立后续阶段，不能与“注册机制已经可用”混为一谈。

这里的 v1 是“图标资源机制”的版本，不是要把整个算子协议或 `project.json` 再升级一个不兼容版本。

## 2. 已核对的代码基础

以下结论以文首提交为基线，相关文件可通过文末源码索引定位。

| 位置 | 当前实现 | 本轮需要补齐 |
| --- | --- | --- |
| `core/plugin/models.py` | `PluginManifest` 有 `iconKey`；`PluginDescriptor` 有 `resourceRoot` 和编辑器 UI 字节、SHA-256、问题列表 | 可选图标声明、不可变图标资源与独立问题列表 |
| `core/plugin/validator.py` | `iconKey` 是可选字符串，默认 `default`；没有图标文件资源协议 | 图标声明的独立解析与非致命诊断 |
| `core/plugin/registry.py` | 已有编辑器资源校验和注册时冻结字节的实现 | 对图片建立独立校验和冻结流程，不直接照搬 `.ui` XML 校验 |
| `proto/runtime.proto` | `OperatorInfo` 已使用字段号 1—13，其中 `icon_key = 8`；已有 `GetOperatorEditorAsset` | 追加图标元数据和专用资源 RPC，保留旧字段号 |
| Designer `runtime_client.py` / `operator_catalog_controller.py` | 已传递 `iconKey` | 继续传递图标状态、摘要和诊断；提供异步取图入口 |
| Designer `ui/icon_map.py` | `getOperatorGlyph()` 只有六个字符映射 | 保留兼容兜底；增加统一图片解析与显示服务 |
| Designer `ui/operator_bubble.py` | 把字符图标拼进按钮文本 | 有图片时使用真正的图标，不再与标题字符串混合 |
| Designer `ui/flow_scene.py` | `FlowNodeViewModel` 和节点标题没有图标资源引用 | 增加仅供视图使用的图标引用及局部刷新 |
| Designer `state/flow_graph_model.py` | `FlowNode` 持有业务节点和参数等状态 | 不把图片字节、客户端缓存路径写进业务节点 |
| Designer `state/system_node_catalog.py` | 工作流输入/输出、子流程和循环由系统节点目录定义 | 系统节点保留本地兜底，不伪装成插件去请求资源 |
| `.github/workflows/release-windows.yml` | 调用 `python_build_scripts` 仓库的复用打包工作流 | 核实资源收集和 Qt SVG 运行依赖，不能假设本仓库有可直接修改的打包 spec |
| `apps/package_selftest.py` | 已检查算子、编辑器 UI、样式、迁移和推理等 | 增加图标文件完整性与独立的 GUI 渲染自检 |

当前缺口是“图片资源的注册与消费”，不是 `iconKey` 字段完全缺失。既有能力应当复用，而不是重写插件注册器或把 Runtime 与 Designer 重新耦合。

算子数量在实施阶段通过实际扫描结果记录；内置算子、示例算子与系统节点分别统计，不将目录数量或包体自检阈值直接当成准确算子数量。

## 3. 建议本轮确认的设计决定

| 编号 | 建议决定 | 理由 |
| --- | --- | --- |
| D1 | 新增可选 `iconResource`，保留 `iconKey` | 老插件不改也能继续注册和显示 |
| D2 | 第一版支持静态 SVG、静态 PNG | SVG 用于新图标，PNG 兼容已有图片；格式范围可控 |
| D3 | 图标错误只降级显示，不拒绝原本有效的算子 | 图片不是算法执行的必要依赖 |
| D4 | Runtime 注册时读取一次并冻结字节；Designer 通过 RPC 取图 | 不依赖共享磁盘，避免文件后续变化导致返回字节与摘要不一致 |
| D5 | UI 先显示 `iconKey` 兜底，异步加载成功后替换 | 不让网络和磁盘等待阻塞算子面板 |
| D6 | 图标属于插件，不属于项目业务数据 | 不迁移 `project.json`，不把资源塞进拖拽和作业快照 |
| D7 | 先完成完整机制和三个示例，再另行审阅全量配图 | 先验证能力，再确定整套视觉风格 |

以上为待审阅建议，并非已经实现的行为。后文容量和超时数值也是本计划提出的初始默认值，不是性能实测结论。

## 4. 插件声明与兼容策略

### 4.1 目录与 manifest

建议结构：

```text
plugins/builtins/canny_edge/
├── __init__.py
├── operator.py
├── manifest.json
└── assets/
    └── icon.svg
```

现有 manifest 仅新增如下字段；这是字段增量示例，不是可替代完整 manifest 的文件：

```json
{
  "iconKey": "edge",
  "iconResource": "assets/icon.svg"
}
```

`iconResource` 以该算子的 `manifest.json` 所在目录为根，而不是进程工作目录、项目目录或 `entry` 模块目录。PNG 示例为 `assets/icon.png`。

约定：未提供或值为空字符串时表示没有自定义图片，无需报错；非空值必须是规范的相对路径字符串。显式 `null`、数字、数组等错误类型产生图标诊断，但不使其他字段合格的插件被拒绝。已有 `iconKey` 的校验语义保持不变。

不要求给 `operatorClass.meta` 增加图标字段，也不将图标加入算法一致性比较。正式发布修改资源时，应同步维护插件版本及其原有版本一致性要求；缓存仍以 SHA-256 区分内容，不能只依赖版本号。

### 4.2 显示优先级

```text
自定义图片已下载、校验且可渲染
    → 显示自定义图片
否则
    → 显示 getOperatorGlyph(iconKey) 的现有兜底
iconKey 不认识
    → 显示 default 兜底
```

兜底适用于：未声明、无效资源、尚未加载、网络失败、旧 Runtime 不支持以及客户端解码失败。GUI 可将现有字符绘制为统一尺寸的图标；无 Qt 的测试替身继续使用字符表示。没有图片时不能出现空白按钮或遮挡标题。

第一版不强制再建设一套分类 SVG 图标库；以后可以替换兜底图形，但不能改掉上述兼容规则。

## 5. Core 与注册器设计

### 5.1 数据模型

建议在 `core/plugin/models.py` 增加一个纯数据类型：

```python
@dataclass(frozen=True)
class PluginIconAsset:
    content: bytes
    mimeType: str
    sha256: str
```

`PluginManifest` 在现有字段后追加 `iconResource: str = ""`；`PluginDescriptor` 在现有字段后追加：

```python
iconAsset: PluginIconAsset | None = None
iconIssues: tuple[ValidationIssue, ...] = ()
```

字节大小从 `len(content)` 派生，不再维护可能失配的第二份数值。新增字段均提供默认值，保留原有构造调用，尤其不能移动已有位置参数字段。

Core 只处理声明、路径、字节与诊断，不导入 PySide2、QIcon、QPixmap 或 Designer。增加图标能力不能要求无界面的 Runtime 创建 QApplication。

### 5.2 校验与注册流程

拟新增 `core/plugin/icon_resources.py`，提供“声明解析”和“资源加载校验”两类函数。`validator.py` 与 `registry.py` 调用它，而不是在主窗口中判断文件是否合法。

```text
读取 manifest 原始数据
    → 现有必需字段、版本、entry 和 meta 校验
    → 独立解析 iconResource，保留 iconIssues
    → 按插件资源根定位文件，受限读取
    → 校验图片类型、结构和限额
    → 对同一份已校验 bytes 计算 SHA-256
    → PluginDescriptor(iconAsset, iconIssues)
```

图标问题不得追加到最终决定 `rejectedOperators` 的致命问题列表。错误类型经标准化后可以把 `manifest.iconResource` 置为空，但原始错误必须保存在 `iconIssues`，不能静默丢弃。

注册结果分为：`none`（没有声明）、`ready`（有通过核心检查的冻结资源）、`invalid`（声明或资源有问题）。`ready` 不等于已经通过每台客户端的实际渲染检查；Designer 仍要处理解码失败。

实现时应在同一次 manifest 解析结果上完成图标解析与加载，不为图标再读取一遍 manifest。必要时给注册器内部记录补充原始数据，但不借此重写整个注册流程。

### 5.3 资源安全与限额

| 项目 | 第一版建议规则 |
| --- | --- |
| 路径 | 只接受插件内部的 POSIX 风格相对路径；拒绝绝对路径、盘符/盘符相对路径、UNC、反斜杠、URL、冒号、控制字符及 `.` / `..` 路径分量 |
| 解析边界 | 使用解析后的真实路径检查归属；指向插件根之外的符号链接或目录重定向不得通过；只读取普通文件 |
| 文件大小 | 单文件不超过 256 KiB；实际读取最多上限加一字节，不能只相信事先 stat |
| 扫描总量 | 一次扫描冻结图标字节总量默认不超过 16 MiB；超出的图片独立降级并产生诊断 |
| MIME | 仅 `image/svg+xml`、`image/png`；结合实际内容检查，不能只看扩展名 |
| PNG | 验证签名、IHDR 和基本容器完整性；宽高均为 1—512，像素总数不超过 262144；拒绝 APNG 与明显截断/非法数据 |
| SVG | UTF-8、单一 SVG 根、有效且有限数值的 viewBox、静态基础图元白名单；节点数不超过 512，嵌套不超过 16 层，单个 d/points 属性不超过 32768 字符 |
| SVG 禁止项 | DTD、实体声明/展开、脚本、事件属性、foreignObject、动画、外部图片、外部引用、data URL、CSS style、use 引用，以及第一版白名单之外的滤镜/蒙版/渐变等复杂特性 |

SVG 首版图元白名单建议为 `svg`、`g`、`path`、`rect`、`circle`、`ellipse`、`line`、`polyline`、`polygon`、`title`、`desc`。属性也必须采用按图元约束的白名单，只接受必要几何、描边、填色、透明度和变换值；拒绝 URL 型属性值，不能只用正则搜索几个危险词。

XML 解析必须明确禁用 DTD、实体及外部资源，并有恶意样例测试。PNG 的完整解码和渲染结果检查在 Designer 再执行一次，渲染前再次确认尺寸；不得把未经限制的原始资源直接交给图片解码器。

路径检查与冻结快照可减少错误引用和读取不一致，但不是针对拥有本机写权限攻击者的完整文件系统沙箱。插件目录部署仍须可信且避免扫描中被并发修改；SHA-256 用于内容一致性和缓存，不代表来源认证。本轮也不新增网络认证/TLS。

### 5.4 降级与诊断

建议图标相关诊断使用独立 `ruleId = "ICON_RESOURCE"`，沿用 `ValidationIssue` 数据结构。

| 场景 | 建议诊断码 | 行为 |
| --- | --- | --- |
| 声明类型或值非法 | `E_ICON_SPEC_INVALID` | 图标降级，保留算子 |
| 路径越界或不允许 | `E_ICON_PATH_INVALID` | 不读取目标文件，图标降级 |
| 文件不存在、不可读或非普通文件 | `E_ICON_ASSET_NOT_FOUND` | 图标降级 |
| 格式不支持、内容/后缀不符 | `E_ICON_FORMAT_UNSUPPORTED` | 图标降级 |
| 图片结构非法或含禁用内容 | `E_ICON_CONTENT_INVALID` | 图标降级 |
| 文件、尺寸、结构或总量超限 | `E_ICON_LIMIT_EXCEEDED` | 图标降级 |
| 客户端渲染失败 | `E_ICON_DECODE_FAILED` | 客户端兜底，不改变 Runtime 算子状态 |

上述图标问题在展示层为 WARNING 级，不标记业务节点 FAILED，不计入视觉检测 NG。没有声明图片的老插件不产生警告。重复诊断按 Runtime 会话、算子和资源摘要去重，不在每次重绘时重复刷日志。

## 6. Runtime/gRPC 契约

### 6.1 列表只返回元数据

保留 `OperatorInfo` 的现有 1—13 字段及语义，建议追加：

```protobuf
message OperatorIconInfo {
  string status = 1;       // none | ready | invalid
  string mime_type = 2;
  string sha256 = 3;
  uint32 byte_size = 4;
}

message OperatorIconIssue {
  string rule_id = 1;
  string code = 2;
  string message = 3;
}

// 以下是 OperatorInfo 的追加字段，不是完整 message 定义：
// OperatorIconInfo icon = 14;
// repeated OperatorIconIssue icon_issues = 15;
```

旧 Runtime 没有这些字段时，客户端按 `none` 处理。`ready` 必须同时具备支持的 MIME、64 位十六进制 SHA-256 和有效字节大小；元数据不一致时仅图标降级。

`ListOperators` 不携带图片 bytes、Base64 或 Runtime 的绝对文件路径。核心问题信息可以在本机日志保留详细路径，但返回 Designer 的图标诊断应只包含插件相对资源名和必要原因。

字段号基于文首基线；开始实施前重新确认目标分支，若 14/15 已被其他功能占用，使用后续未占用编号，不覆盖或重编号已有字段。

### 6.2 专用资源接口

```protobuf
message GetOperatorIconAssetRequest {
  string operator_id = 1;
  string version = 2;
  string expected_sha256 = 3;
}

message GetOperatorIconAssetReply {
  bool ok = 1;
  bytes content = 2;
  string mime_type = 3;
  string sha256 = 4;
  string code = 5;
  string message = 6;
  string version = 7;
}

// 在现有 service 中追加：
// rpc GetOperatorIconAsset(GetOperatorIconAssetRequest)
//     returns (GetOperatorIconAssetReply);
```

请求只允许通过算子 ID、精确版本和预期摘要查找，不接受任何文件路径参数。三个字段均由客户端从当前有效目录元数据构造；空值、非法摘要或版本不匹配返回明确业务错误。

Runtime 只返回注册快照中的资源，不在 RPC 中重读文件，不动态 import 图标代码，不访问相机/PLC，也不要求先加载业务项目。不存在的算子、不可用资源、版本不匹配和摘要不匹配分别返回可判断的错误，例如 `E_ICON_OPERATOR_NOT_FOUND`、`E_ICON_ASSET_UNAVAILABLE`、`E_ICON_VERSION_MISMATCH`、`E_ICON_DIGEST_MISMATCH`。

成功响应的 MIME、版本、长度和 SHA-256 必须与列表元数据一致；客户端重新计算 bytes 的 SHA-256 后才进入解码。失败响应不携带可用图片内容。

服务仍采用有大小上限的单次 unary RPC，不为小图片引入上传或流式传输。图标请求不能持有项目运行锁执行耗时 I/O。

### 6.3 新旧端兼容与生成代码

| 组合 | 预期 |
| --- | --- |
| 新 Designer + 新 Runtime + 有效图片 | 异步加载真实图标 |
| 新 Designer + 新 Runtime + 老插件 | 使用原 `iconKey` 兜底 |
| 老 Designer + 新 Runtime | 忽略新增 protobuf 字段，继续显示原图标 |
| 新 Designer + 老 Runtime | 无图标元数据则不请求；遇 `UNIMPLEMENTED` 时记住该会话不支持，使用兜底 |
| 资源与目录摘要不一致 | 丢弃图片，至多触发一次目录刷新；禁止无限重试 |

protobuf 二进制协议新增字段可兼容旧消息，但并不自动证明应用代码兼容，以上组合必须有实际测试。不能仅凭 `getattr()` 默认值就宣布兼容完成。

只修改 `proto/runtime.proto` 后用仓库 `scripts/gen_proto.py` 生成代码，不手改 `generated/runtime_pb2*.py`；保留当前顶层兼容 shim。维持现有 Python 3.10、PySide2 5.15 技术路线，不因本功能迁移 PySide6。

## 7. Designer 异步加载、缓存与渲染

### 7.1 职责拆分

拟新增以下小模块，名称可在实施时按仓库命名风格微调：

| 模块 | 职责 |
| --- | --- |
| `services/operator_icon_cache.py` | 字节缓存、内容摘要与元数据检查、LRU 限额；不持有 Qt GUI 对象 |
| `services/operator_icon_worker.py` | 专用有界取图任务，处理 RPC 超时、取消和失败反馈；不与长连接事件消费争用同一串行队列 |
| `ui/operator_icon_provider.py` | GUI 层统一渲染与兜底、QIcon/QPixmap 缓存、资源更新通知 |

`runtime_client.py` 增加取图 DTO 和方法；`OperatorDefinition` 的新字段提供默认值。目录控制器输出可 JSON 序列化的图标元数据，不把 bytes 或 QIcon 放进目录 payload。

### 7.2 加载与生命周期

面板打开时立即显示兜底；按可见卡片、当前画布节点和详情窗口的需求排队取图。相同资源的并发请求合并，多个同类节点共享结果。第一版默认最大 4 个取图请求并发，每次超时 2 秒；不得按算子个数在 GUI 线程同步调用 RPC。

结果回调携带 Runtime 会话标识、目录代次和资源身份。切换 Runtime、刷新目录、移除节点、关闭面板或销毁窗口后，旧结果必须被丢弃或仅留在安全缓存中，不能写回已销毁的 Qt 对象。关闭程序时取消或有限等待取图任务，不能无限等待。

### 7.3 缓存身份与失效

目录绑定身份建议为：

```text
(runtimeScope, catalogGeneration, operatorId, version, sha256)
```

`runtimeScope` 至少包含 Runtime 连接来源和本次连接会话标识。本轮只做内存缓存，不建设持久化磁盘缓存；重新连接后重新确定目录，不使用另一 Runtime 同名算子的旧图标。

字节可按同一会话内的 `(mimeType, sha256)` 去重。渲染缓存还须区分逻辑尺寸、设备像素比和必要的显示状态，不能把低分辨率图片无条件用于高 DPI。

默认字节缓存上限 16 MiB，渲染缓存上限 32 MiB 且最多 256 项，超过限额按 LRU 淘汰。数字为初始建议，后续性能测试可调整。

资源校验失败在当前目录代次内不重复请求；临时网络失败短期负缓存 5 秒，之后只在再次有显示需求或用户刷新时有限重试。`UNIMPLEMENTED` 按会话记忆。换图、换版本、换 Runtime 或显式刷新都应能正确失效。

### 7.4 渲染与 UI 接入

SVG 计划使用 `PySide2.QtSvg.QSvgRenderer` 从字节加载并渲染到受限尺寸的图像；PNG 使用 Qt 图片解码路径。进入 Qt 解码前再次校验内容策略和尺寸，不能把服务端校验当成客户端唯一防线。

第一版所有 Qt 渲染以及 QIcon/QPixmap 的创建、使用与 UI 更新固定在 GUI 线程；worker 只负责 RPC、字节校验和缓存准备。内容通过后按小批次渲染，避免首次加载大量图标集中占用事件循环。QtSvg 不可用或渲染失败时仍可使用兜底。

具体入口：

- 算子选择卡片：有图片时调用按钮图标接口并保留纯文本标题、算子 ID；无图片时使用兼容兜底；搜索和拖拽行为不变。
- 画布节点：在标题前保留固定图标位置，采用独立图形项或受控绘制；留出标题空间，不改变端口锚点；异步更新不能重建整张图或覆盖运行态着色。
- 节点详情/编辑器窗口：通过同一个 provider 显示相同资源，不各自另建取图逻辑。

建议卡片 24×24、画布/详情 20×20 逻辑像素。必须验证 100%、150%、200% 缩放，以及当前画布填色、选中、运行中和失败状态下的可辨认性；这不代表本轮新增完整深色主题。

新增图标子项不得吞掉拖动、选择、双击和端口连线事件。文字应保留，不能仅用图标表达算子身份。

### 7.5 项目与系统节点不变

图标引用只追加在 `FlowNodeViewModel` 或由 provider 根据 `operatorId` 查询目录，不改变 `FlowNode` 的业务序列化。不向 `project.json`、工作流导出包、参数、作业快照及拖拽 MIME 写入图片字节、绝对路径或缓存位置。

打开已有项目后根据当前算子目录恢复显示。图标下载、刷新或失效不得增加项目 revision、触发未保存提示或改变执行结果。算子确实缺失时仍维持现有的业务校验错误，不能用图标兜底把缺失算子伪装成可执行。

工作流边界、子流程、Repeat/ForEach/While 等系统节点按本地系统节点类型使用兜底；它们没有插件 manifest，不发起 `GetOperatorIconAsset`，也不计入插件图标覆盖率。

## 8. 示例配图与后续全量配图

机制验收阶段只安排三个内置算子示例：`canny_edge`、`huaray_camera`、`yolo_inference`。三个生产示例优先使用静态 SVG；PNG 支持由测试插件及自动化夹具验证，不要求为展示刻意混用风格。

示例规范建议：透明背景、24×24 viewBox、基础几何线条、无文字和嵌入字体，优先保持小尺寸辨识度。不得依赖被资源白名单禁用的 SVG 特性。颜色和最终造型待审阅，不沿用应用品牌图标作为所有算子的图标。

示例资源属于后续实现阶段，本次计划提交不生成它们。

全量配图在机制通过后单独规划：从实际注册目录导出清单，按算子用途分组，为各算子指定资源及许可来源，允许复用基础图形但要求容易区分。外部图标必须记录来源与适用许可，不引入未说明授权的第三方资源。

后续完整配图的覆盖率应以本轮约定的“需配图算子清单”为分母；模板/示例算子、旧兼容插件和系统节点的处理单独标明。

## 9. 打包与部署验收

图标随插件目录分发，与 `manifest.json` 的相对目录关系保持不变。不能只在源码目录能显示，而在离线工控机的发布包里丢失。

本仓库的 Windows 发布调用外部 `jsdfhasuh/python_build_scripts` 的复用工作流，因此实施前必须定位实际 emo-master 打包目标和资源收集规则，按实际打包器调整，不预设只改一个 PyInstaller spec 就能解决。

本仓库需要补充：声明图标资源的完整性检查、图片摘要验证、打包后样例图标读取和单独的 GUI 渲染 smoke test。无 GUI 的资源自检不能强制创建 QApplication；GUI 自检在 Windows/offscreen 等明确支持的测试环境单独执行。

对于 Qt SVG，检查 `PySide2.QtSvg` 及其必要动态库确实被包含。采用显式 QSvgRenderer 字节渲染时，不把 QIcon 文件路径 SVG 插件的存在当成唯一验证条件；最终以真实 SVG 渲染是否成功为准。

原有编辑器 UI、QSS、SQL 迁移、ONNX 自检均需回归。若同时提供 wheel/sdist，也必须检查安装后的资源，而不只检查源码树。

外部构建仓库需要的改动应单列跨仓库依赖和提交记录；本次只推送本仓库计划，未授权修改外部仓库。在正式包未验证前，不将“发布交付”标记完成。本功能不改变安装器/压缩包的现有交付策略。

## 10. 分阶段实现与修改文件

所有阶段当前均为 `NOT_STARTED`；下面是审阅通过后的实施顺序，不是已经完成的清单。

| 阶段 | 主要改动 | 通过条件 |
| --- | --- | --- |
| P0 契约与基线 | 复核分支提交、已有 proto 字段号、实际算子列表、运行/打包基线；确认本计划决定 | 清楚记录原有失败，不能用本功能掩盖已有问题 |
| P1 声明与资源注册 | 修改 `core/plugin/models.py`、`validator.py`、`registry.py`；新增 `icon_resources.py` 和单测 | 正常图片可冻结；所有图标错误均独立降级；老插件和无 Qt Runtime 不受影响 |
| P2 RPC 与客户端 DTO | 修改 `proto/runtime.proto`、Runtime `grpc_server/service.py`、Designer `services/runtime_client.py`；运行 `scripts/gen_proto.py` | 真实 gRPC 可获取一致 bytes；新旧端兼容、版本/摘要检查通过 |
| P3 取图、缓存与 provider | 新增 Designer 图标 cache/worker/provider；更新目录控制器 | 不阻塞 UI；请求合并、限额、失效、关闭和旧回调测试通过 |
| P4 主要 UI 接入 | 修改 `ui/operator_bubble.py`、`ui/flow_scene.py`、`ui/main_window.py`，按需接入节点详情 presenter/编辑器窗口 | 卡片、节点、详情一致显示；旧项目保存结果不变，交互和运行态不回退 |
| P5 示例与包体回归 | 三个示例的 manifest/assets；修改 `apps/package_selftest.py`；按审查结果补资源打包规则和测试 | 源码与 Windows 发布包均可显示，损坏示例可降级且不影响执行 |
| P6 文档与验收记录 | 更新 `docs/plugin-registration-flow.md`；新增图标资源协议/测试说明，记录测试和限制 | 能按说明新增第三方图标插件，不修改 Designer 核心代码即可显示 |

建议按 P1、P2、P3/P4、P5/P6 划分小提交，便于审阅与回退。不得把图标工作顺带扩展成算子算法重构、项目格式迁移或 GUI 框架升级。

## 11. 测试矩阵与完成标准

### 11.1 自动化测试

| 层级 | 必测场景 |
| --- | --- |
| 声明兼容 | 缺失/空 iconResource、合法 SVG/PNG、错误类型、未知 iconKey；既有 manifest/meta 校验回归 |
| 路径与读取 | POSIX/Windows 绝对路径、盘符相对路径、UNC、反斜杠、URL、目录穿越、符号链接越界、目录代替文件、权限错误、读取中资源变化 |
| 内容与容量 | 假后缀、非法 PNG 签名/尺寸/截断/APNG、SVG 脚本/DTD/实体/外链/style/use/复杂图元、过深/过大/超限文档 |
| 注册行为 | 图标出错仍保留有效算子；重复 operatorId 仍按原规则拒绝；UI 与 icon 诊断互不污染；冻结后文件改变不影响当前资源 bytes 和 SHA |
| RPC | 无需业务项目即可取图、ID/版本/摘要不匹配、失败响应、客户端重新哈希、旧服务 `UNIMPLEMENTED`、超时/断连、真实网络传输 |
| 缓存 | 相同图标请求合并、LRU 回收、同 ID/版本不同摘要、不同 Runtime 不串图、重连和目录刷新、负缓存与有限重试 |
| GUI | SVG/PNG 渲染、兜底、不同 DPI、标题不重叠、图标不抢鼠标事件、节点状态保持、关闭时尚有请求、删除节点后迟到回调 |
| 持久化与工作流 | 旧项目打开保存、工作流导入导出、切换标签页、运行作业，均不新增图标业务字段或被图标错误阻断 |
| 打包 | 声明资源未被遗漏、Qt SVG 可用、离线新目录运行、原有 package self-test 回归 |

核心资源校验测试应能在没有 Qt 的环境运行；GUI 测试在已有 PySide2 路线上执行。不使用纯 mock 代替全部 gRPC 和发布包验收。

建议增补测试文件：

```text
tests/core/plugin/test_icon_resources.py
tests/core/plugin/test_registry_icons.py
tests/runtime/test_operator_icon_assets.py
tests/designer/test_operator_icon_cache.py
tests/designer/test_operator_icon_worker.py
tests/designer/test_operator_icon_provider.py
tests/designer/test_operator_icon_integration.py
```

同时更新既有 `test_registry.py`、`test_runtime_client.py`、`test_operator_bubble.py`、画布交互、项目保存/工作流导出以及打包相关测试，而非只增加孤立新测试。

### 11.2 人工验收

用一个带本地图片读取、视觉处理和结果输出的简单工作流，检查三个示例算子的选择卡片、画布和详情图标；不需要为了验证图标真正触发相机或 PLC。

然后分别移除图片、换成非法图片、断开 Runtime，确认显示兜底、诊断可见，并且图片本身不导致原本能执行的工作流失效。两台机器验证 Designer 不挂载 Runtime 的插件目录也能显示图标。

同一算子放置多个节点，重复打开面板和切换工作流，确认不会每次触发重复下载。图标加载完成不能使项目出现未保存状态。Windows 发布包需在不依赖源码树的独立目录验证。

### 11.3 完成定义

只有同时满足以下条件，才把“图标注册机制”标记为完成：

1. 新插件通过 manifest 与自带资源即可接入，无需修改宿主算子 ID 对照表。
2. SVG 和 PNG 均有成功路径与失败降级测试；旧插件仍可使用。
3. Runtime 不因图标引入 Qt GUI 依赖；资源 RPC 不泄露或接受任意文件路径。
4. 列表、资源 bytes、版本和 SHA 一致；换图、换 Runtime 与缓存失效正确。
5. 卡片、画布、详情使用统一来源；异步任务不阻塞编辑或写回失效对象。
6. 不改变项目格式、算法输入输出、业务失败/NG 统计和现有执行语义。
7. 真实 gRPC、既有回归和 Windows 包体检查有记录；尚未验证的环境明确标为待验收。

全量算子拥有独立图标不属于上述完成条件，应在后续配图计划中独立验收。

## 12. 风险、回退与审阅入口

主要风险是 SVG/PNG 解码复杂度、远程取图影响 UI、资源与缓存身份混淆，以及外部打包规则遗漏。对应控制分别为白名单与限额、专用异步任务、版本/摘要/Runtime 会话隔离，以及真实发布包渲染验收。

回退优先恢复 Designer 的字符兜底，保留兼容的 protobuf 新字段；未使用字段不要随意复用编号。撤销示例 `iconResource` 不需要迁移项目。由于图标与执行数据分离，回退不应修改用户工作流。若需整体回退功能提交，使用正常 revert 流程，不强制覆盖目标分支历史。

建议用户先审阅第 3 节的 D1—D7，再看第 5.3 节的格式/限额、第 7 节的界面与缓存行为，以及第 8 节“先示例、后全量配图”的范围划分。

当前审批记录：`PENDING`。没有用户批准记录时，不将本计划改成 `IMPLEMENTATION_READY`，也不据此启动全量代码修改。

## 附录 A：源码依据

以下路径相对本计划所在目录；当前事实以文首基线提交为准。之后代码变动时，应重新核对，不将旧架构计划中的历史描述当成当前事实。

- [插件模型](../../src/emo_master/core/plugin/models.py)、[字段校验](../../src/emo_master/core/plugin/validator.py)、[注册器](../../src/emo_master/core/plugin/registry.py)。
- [RPC 协议](../../proto/runtime.proto)、[Runtime 服务](../../src/emo_master/apps/runtime/grpc_server/service.py)、[协议生成脚本](../../scripts/gen_proto.py)。
- [客户端](../../src/emo_master/apps/designer/services/runtime_client.py)、[目录控制器](../../src/emo_master/apps/designer/controllers/operator_catalog_controller.py)、[现有字符图标](../../src/emo_master/apps/designer/ui/icon_map.py)。
- [算子卡片](../../src/emo_master/apps/designer/ui/operator_bubble.py)、[画布](../../src/emo_master/apps/designer/ui/flow_scene.py)、[业务节点模型](../../src/emo_master/apps/designer/state/flow_graph_model.py)、[系统节点目录](../../src/emo_master/apps/designer/state/system_node_catalog.py)。
- [Canny manifest](../../src/emo_master/plugins/builtins/canny_edge/manifest.json)、[现有注册说明](../plugin-registration-flow.md)。
- [依赖声明](../../pyproject.toml)、[Windows 发布工作流](../../.github/workflows/release-windows.yml)、[包体自检](../../src/emo_master/apps/package_selftest.py)。

## 附录 B：外部协议与渲染参考

- [Protocol Buffers proto3：更新消息类型](https://protobuf.dev/programming-guides/proto3/#updating)：二进制协议中可追加新字段，不应修改已有字段号；应用层仍须单独验证新旧端行为。
- [Qt 官方 QSvgRenderer 说明](https://doc.qt.io/qt-6.5/qsvgrenderer.html)：说明从 XML 字节加载、有效性检查及渲染的基本模式。该页面是 Qt 6.5 参考，不作为项目使用 Qt 6 的依据；实施必须在当前 PySide2 5.15 环境核对并测试相应接口，不能引入 Qt 6 专有 API。

以上资料用于协议和渲染设计参考；文件大小、白名单、缓存、超时与实施分期均为本项目本次提出的设计选择。
