# 算子图标（图片资源）注册实现计划 v1

> 文档修订：`r4`（已实施机制、三个示例和回归测试）\
> 状态：`IMPLEMENTED_LOCAL_VALIDATED`（实现和本地包体验收通过，正式发布/现场验收待完成）\
> 日期：2026-09-10\
> 仓库：`jsdfhasuh/emo_master`\
> 目标分支：`agent/runtime-workflow-architecture-v1`\
> 本地远程跟踪名：`origin/agent/runtime-workflow-architecture-v1`\
> 代码核对基线：`090757fb772d922bfead87f4e043164639892c7f`\
> 本次修订基于计划提交：`7081e2506c708be291102b2cd05341b7cb5fd0b4`\
> 本次交付：补齐计划并实施注册机制、三个示例和测试；按后续授权提交/推送，不触发正式发布、不修改外部构建仓库。\
> 实施批准：2026-09-10，用户明确指示“开始补齐 然后实现吧”。

> 提交/推送批准：2026-09-10，用户明确指示“整理工作区 提交 推送上去吧”。

## 0. 本次修订与审查问题对应

| 审查项 | 本次明确的约定 | 对应章节 |
| --- | --- | --- |
| 冻结资源与刷新/删图验收混淆 | Designer 刷新只获取目录；磁盘换图需重启 Runtime；断网区分已有缓存与未加载图片 | 4.2、5.5、7.3、11.2 |
| 两秒超时、取消和目录刷新未接到现有客户端 | 单次调用独立超时、可跟踪请求、异步目录刷新、刷新合并；区分远程与嵌入式 Runtime | 2、6.4、7.1—7.3 |
| 迟到回调可能写入仍存活但已换绑的控件 | 增加消费端绑定代次及项目/工作流/节点身份；资源缓存身份与显示对象身份分开 | 7.2、11.1—11.2 |
| PNG 附加压缩数据缺少约束 | 数据块白名单、CRC/顺序检查、拒绝压缩附加数据，并限制 IDAT 解压输出 | 5.3.1、11.1 |
| SVG 属性与解析方式不明确 | 固定命名空间、图元/属性/值规则和受限路径语法；选定安全 XML 解析入口 | 5.3.2、10、11.1 |
| 兜底显示可能被误判为图标功能通过 | 正常显示、故障降级分别验收；正式包必须断言真实资源已渲染 | 9、11.3 |
| 首次异步目录影响业务入口 | 明确 loading/ready/failed；首次目录未就绪时延后专用编辑器打开和导入依赖检查，不以空目录判断缺失算子 | 7.1 |
| 自绘卡片不消费普通 setIcon | provider 通过专用 setter 更新卡片自绘字段；保留现有 Lucide 分类兜底 | 4.2、7.4 |
| 缺 QtSvg 导致公共图标入口失败 | 公共 icon() 与 provider 均有无 SVG 的绘制兜底；测试完整窗口启动 | 7.4、11.1 |

下述测试矩阵保留为验收要求，不代表每项环境都已验证。实际结果、截图及尚未完成的
环境验收见 [实施记录](../testing/operator-icons-2026-09-10/README.md)。

## 1. 要解决的问题与本轮边界

目标不是继续增加一个写死在 Designer 中的图标对照表，而是让**图标资源成为算子插件自身声明、注册、分发和显示的一部分**。

预期效果：开发者在插件目录中放入图片，并在 `manifest.json` 中声明相对路径；Runtime 扫描时校验并注册，Designer 通过接口获取资源，在算子选择卡片、画布节点和节点详情中一致显示。Designer 不需要为每个新增算子修改宿主代码。

本轮实现范围为：可选图标声明、资源安全校验、注册快照、gRPC 资源获取、客户端异步加载与缓存、必要的目录刷新异步化、主要 UI 入口接入、兼容与打包验收，以及少量示例资源。

本轮明确不包含：全量算子的独立图标设计、应用 EXE/窗口品牌图标、用户在界面上传图标、在线图标商店、任意 URL 下载、动态图标、完整 SVG/PNG 标准支持、全新主题系统、算子算法或端口变更、插件热重载，以及第三方 Python 插件安全沙箱。全量配图列为独立后续阶段，不能与“注册机制已经可用”混为一谈。

这里的 v1 是“图标资源机制”的版本，不是要把整个算子协议或 `project.json` 再升级一个不兼容版本。受限格式未接受的图片不一定损坏，也可能只是超出本版支持范围，诊断必须说明原因。

## 2. 已核对的代码基础

以下结论以文首代码基线为准，已包含 `e32a7a7` 的 Designer 改造。相关文件可通过附录 A 定位。

| 位置 | 当前实现 | 本轮需要补齐 |
| --- | --- | --- |
| `core/plugin/models.py` | `PluginManifest` 有 `iconKey`；`PluginDescriptor` 有 `resourceRoot` 和编辑器 UI 字节、SHA-256、问题列表 | 可选图标声明、不可变图标资源与独立问题列表 |
| `core/plugin/validator.py` | `iconKey` 是可选字符串，默认 `default`；没有图标文件资源协议 | 图标声明的独立解析与非致命诊断 |
| `core/plugin/registry.py` | 已有编辑器资源校验和注册时冻结字节的实现 | 独立的图片校验和冻结流程，不直接照搬 `.ui` XML 校验 |
| `proto/runtime.proto` | `OperatorInfo` 已使用字段号 1—13，其中 `icon_key = 8`；已有 `GetOperatorEditorAsset` | 追加图标元数据和资源 RPC，保留旧字段号 |
| Designer `services/runtime_client.py` | 已传递 `iconKey`；`_call()` 使用共享 `deadlineMs`；`close()` 跟踪事件流，未管理图标 unary 请求 | 单次超时覆盖、只读展示请求句柄及取消；不改变业务请求默认行为 |
| Designer `main.py` | 配置 `EMO_RUNTIME_TARGET` 时使用 gRPC；否则直接调用进程内 `RuntimeService` | 图标加载同时覆盖远程和嵌入式模式 |
| Designer `operator_catalog_controller.py` / `ui/main_window.py` | `refreshOperators()` 同步获取目录后刷新界面 | 把目录获取与界面应用拆开，GUI 不等待网络 |
| Designer `ui/icon_map.py` | 已有 Lucide icon()/operatorIcon()，字符映射作兼容兜底 | 保留分类图标并补齐无 QtSvg 的公共兜底 |
| Designer `ui/operator_bubble.py` | paintEvent 绘制私有 _icon，标题为纯文本 | 专用 setter 更新自绘图标，不能仅调用 setIcon |
| Designer `ui/flow_scene.py` | 节点标题没有图标资源引用 | 视图图标引用及局部刷新，不动业务数据 |
| Designer `ui/main_window.py` | 节点详情复用同一组控件，选择变化时重新设置内容 | 检查绑定代次，不能仅判断控件仍存活 |
| Designer `state/flow_graph_model.py` | `FlowNode` 持有业务节点和参数状态 | 不写入图片字节或客户端缓存路径 |
| Designer `state/system_node_catalog.py` | 边界、子流程和循环由系统节点目录定义 | 系统节点保留本地兜底，不伪装成插件请求资源 |
| `.github/workflows/release-windows.yml` | 调用外部 `python_build_scripts` 复用打包工作流 | 核实真实资源收集规则和 Qt SVG 依赖 |
| `apps/package_selftest.py` | 已检查算子、编辑器 UI、样式、迁移和推理等 | 增加图标完整性和真实 GUI 渲染自检 |

当前缺口是“图片资源的注册与消费”，不是 `iconKey` 字段完全缺失。复用既有能力，不重写插件系统，不把 Runtime 与 Designer 重新耦合。

算子数量在实施时按实际扫描结果记录；内置算子、示例算子与系统节点分别统计，不将目录数量或包体自检阈值当成准确数量。

## 3. 已批准的设计决定

| 编号 | 实施决定 | 理由 |
| --- | --- | --- |
| D1 | 新增可选 `iconResource`，保留 `iconKey` | 老插件不改也能注册和显示 |
| D2 | 第一版支持本文定义的静态 SVG、静态 PNG 子集 | 不承诺任意图片均可直接导入 |
| D3 | 图标错误只降级显示，不拒绝原本有效的算子 | 图片不是算法执行的必要依赖 |
| D4 | Runtime 注册时冻结字节，换图须重启后重新注册 | 保持字节与摘要一致，不暗中引入热重载 |
| D5 | 目录刷新和取图异步；已有有效缓存优先复用 | 不因图片或目录网络请求阻塞编辑 |
| D6 | 图标属于插件，不属于项目业务数据 | 不迁移 `project.json`，不把资源塞进作业快照 |
| D7 | 先完成完整机制和三个示例，再审阅全量配图 | 先验证能力，再确定整套视觉风格 |
| D8 | 资源身份和控件绑定身份分别校验 | 防止旧请求写到新选中的节点 |
| D9 | 内容校验使用同一份纯 Python 策略；正式 GUI 验收不允许仅靠兜底通过 | 减少前后端策略分歧和假通过 |

以上决定已获批准并实施。容量、超时、关闭预算和结构限额是实现约束，不是所有部署环境的
性能承诺；固定的 `defusedxml==0.7.1` 和 SVG/PNG 正反例已进入依赖及自动化测试。

## 4. 插件声明与兼容策略

### 4.1 目录与 manifest

```text
plugins/builtins/canny_edge/
├── __init__.py
├── operator.py
├── manifest.json
└── assets/
    └── icon.svg
```

现有 manifest 仅新增如下字段；这是字段增量，不是可替代完整 manifest 的文件：

```json
{
  "iconKey": "edge",
  "iconResource": "assets/icon.svg"
}
```

`iconResource` 以该算子的 `manifest.json` 所在目录为根，不是进程工作目录、项目目录或 `entry` 模块目录。PNG 示例为 `assets/icon.png`。

未提供或值为空字符串时表示没有自定义图片，不报错。非空值必须是规范相对路径字符串；不静默修剪或修复路径。显式 `null`、数字、数组等错误类型产生图标诊断，但不使其他字段合格的插件被拒绝。已有 `iconKey` 校验语义保持不变。

不要求给 `operatorClass.meta` 增加图标字段，也不将图标加入算法一致性比较。正式发布修改资源时，应维护插件版本及其原有版本一致性要求；缓存仍用 SHA-256 区分内容，不能只依赖版本号。

### 4.2 显示优先级与断网行为

```text
当前目录身份对应的图片已校验且可渲染（包括有效内存缓存）
    → 显示自定义图片
否则
    → 使用 operatorIcon(iconKey) 的现有 Lucide 分类兜底
QtSvg 不可用
    → 不依赖 SVG 的字符绘制兜底
iconKey 不认识
    → 使用 default 兜底
```

兜底适用于未声明、无效、尚未加载、无可用缓存时的网络失败、旧 Runtime 不支持或客户端解码失败。GUI 可把字符绘制为统一尺寸图标；无 Qt 的测试替身继续用字符表示，不能出现空白按钮或遮挡标题。

短暂断网不主动清空同一作用域内已验证的缓存，已显示图标可以保留；尚未取得资源的控件使用兜底。收到新目录的 `invalid`/`none`、版本或摘要变化时，不再展示旧资源。切换或重新建立 Runtime 会话按第 7.3 节隔离，不将另一会话同名算子的图标当成有效结果。

保留图标不表示 Runtime 在线，连接状态仍由现有状态区展示。断网后的作业启动失败按既有通信语义处理，不能承诺断网时仍能执行工作流。

第一版不强制建设分类 SVG 库；以后替换兜底图形也不能破坏兼容规则。

## 5. Core 与注册器设计

### 5.1 数据模型

在 `core/plugin/models.py` 增加纯数据类型：

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

字节大小由 `len(content)` 派生。新增字段均有默认值，保留已有构造调用，不移动已有位置参数字段。

Core 只处理声明、路径、字节与诊断，不导入 PySide2、QIcon、QPixmap 或 Designer，不要求无界面 Runtime 创建 QApplication。

### 5.2 校验与注册流程

新增 `core/plugin/icon_resources.py`，分别提供声明解析、受限文件读取、纯 bytes 内容校验。注册器和 Designer worker 复用内容校验函数；客户端不调用插件磁盘读取函数。

```text
读取 manifest 原始数据
    → 原有必需字段、版本、entry 和 meta 校验
    → 独立解析 iconResource，保留 iconIssues
    → 在插件资源根内定位文件并受限读取
    → 校验 MIME、结构和限额
    → 对同一份已校验 bytes 计算 SHA-256
    → PluginDescriptor(iconAsset, iconIssues)
```

图标问题不得加入决定 `rejectedOperators` 的致命问题列表。错误类型可使规范化后的 `manifest.iconResource` 为空，但原始诊断必须保留，不能静默丢弃。

状态派生规则固定为：有图标问题则 `invalid`；否则有冻结资源则 `ready`；否则为 `none`。`ready` 只表示通过核心校验，不等于所有客户端均已成功渲染。

图标解析与加载使用同一次 manifest 读取结果。必要时给内部记录补充原始数据，不为图标再次读取 manifest，也不借机重写既有编辑器机制。

### 5.3 共同安全边界与限额

| 项目 | 第一版规则 |
| --- | --- |
| 路径 | 插件内部 POSIX 风格相对路径；拒绝绝对路径、盘符/盘符相对路径、UNC、反斜杠、URL、冒号、控制字符、空路径分量和 `.`/`..` 分量 |
| 解析边界 | 解析真实路径后确认归属；符号链接或目录重定向不能越出插件根；只读取普通文件 |
| 文件大小 | 单文件 1—262144 字节；实际最多读取上限加一字节，不只依赖 stat |
| 扫描总量 | 默认最多冻结 16 MiB；按稳定扫描顺序处理，超额图片独立降级并记录诊断 |
| MIME | 仅 `image/svg+xml`、`image/png`；内容和后缀必须一致；不接受 SVGZ 或容器内压缩 SVG |
| 客户端复核 | 先验证长度、摘要、MIME，再执行与注册器相同的内容策略，通过后才交给 Qt 解码 |
| 输出边界 | 不按图片任意声明分配渲染缓冲，输出尺寸由 UI 的受限逻辑尺寸和 DPI 策略确定 |

路径检查和冻结快照不是针对本机恶意写入者的文件系统沙箱。部署目录仍须可信，避免扫描时并发修改。SHA-256 用于一致性和缓存，不代表来源认证；本轮不新增网络认证/TLS，也不声称白名单消除了所有原生解码器风险。

#### 5.3.1 PNG：容器、附加数据和解压边界

首版采用明确子集，而不是“扩展名正确就交给 Qt”。宽高均为 1—512，总像素不超过 262144；允许 PNG 标准颜色类型 0、2、3、4、6 对应的合法位深组合；压缩方法、滤波方法必须为 0，隔行方式只接受 0。Adam7 隔行图片首版不支持，提示重新导出为非隔行 PNG。

允许的数据块仅为 `IHDR`、`PLTE`、`IDAT`、`IEND`、`tRNS`、`sRGB`、`gAMA`、`cHRM`、`pHYs`，总块数不超过 256。必须逐块检查长度边界、类型、CRC、数量和顺序，不能只检查文件头。[B3]

| 数据块 | 必须检查的条件 |
| --- | --- |
| `IHDR` | 唯一、第一块、长度 13；尺寸、颜色类型、位深及方法字段合法 |
| `PLTE` | 在 IDAT 前且最多一个；类型 3 必需，类型 0/4 禁止；长度为 3 的倍数，调色板条目 1—256，类型 3 不超过该位深可表示的条目数 |
| `tRNS` | 在 IDAT 前，若有 PLTE 则在其后；最多一个；只允许颜色类型 0/2/3，并校验各类型规定的长度及样本范围，索引透明度不超过调色板条目数 |
| `sRGB/gAMA/cHRM/pHYs` | 固定长度分别为 1/4/32/9；各最多一个，按 PNG 规定的顺序和值约束检查；不容许附带任意长度负载 |
| `IDAT` | 至少一个且连续；合并后的内容是单个合法 zlib 流，压缩数据非空；按下述解压预算验证 |
| `IEND` | 唯一、最后一块、长度 0；拒绝其后的多余字节 |

拒绝 APNG 的 `acTL/fcTL/fdAT`，也拒绝 `zTXt`、`iTXt`、`iCCP`、`tEXt`、`eXIf` 及所有未列入白名单的数据块；不先解压这些附加数据再决定是否接受。合法但超出子集的数据块返回 `E_ICON_FORMAT_UNSUPPORTED`，损坏的容器返回内容错误。

限制像素和压缩文件大小仍不足以约束全部解压输出：PNG 的部分附加数据本身可包含独立压缩流。[B3] 首版拒绝它们；IDAT 则需要在 Core 中执行受限 zlib 验证，不把风险完全留给 Qt。

非隔行图像的预期解压字节数为：

```text
rowBytes = ceil(width × channels × bitDepth / 8)
expectedBytes = height × (1 + rowBytes)
```

其中 channels 按颜色类型取 1/3/1/2/4，每行多出的一个字节是滤波类型。采用增量解压并始终设置正的 `max_length`，累计输出最多为 `expectedBytes + 1`，另设 3 MiB 绝对上限；不能使用无上限的 `zlib.decompress()`，也不能误把 `max_length=0` 当成零预算。[B4]

输出超额立即拒绝；不足、未到流结尾、尾随压缩数据/额外流或行滤波值不在 0—4 内同样拒绝。Core 不实现完整像素重建；Designer 在通过这些检查后使用 Qt 完整解码，再确认最终尺寸一致。

“小尺寸、小文件、压缩附加数据”和“合法尺寸但 IDAT 超额解压”夹具已进入
`tests/core/plugin/test_icon_resources.py`；不将未随仓库提供的样例当作通过证据。

#### 5.3.2 SVG：解析器、命名空间和属性规则

选定 `defusedxml.ElementTree` 作为图标专用 XML 解析入口，显式传入 `forbid_dtd=True`、`forbid_entities=True`、`forbid_external=True`。不能依赖默认参数，尤其 DTD 禁止项默认不是开启状态；不得使用全局 monkey patch 或在依赖缺失时悄悄退回普通解析器。[B5][B6]

已在 Python 3.10 验证并固定 `defusedxml==0.7.1`，同步维护 `pyproject.toml` 和
`requirements.txt`。缺少安全解析器时 SVG 独立降级，诊断 `E_ICON_VALIDATOR_UNAVAILABLE`；
PNG 和有效算子仍可使用。正式包缺少此依赖则不能通过 SVG 验收。

解析前执行文件大小检查和严格 UTF-8 解码，可接受 UTF-8 BOM；若有 XML 声明，编码必须为 UTF-8。采用支持 `start/end/start-ns/pi` 事件的受限解析入口，在解析中检查深度和节点数；拒绝非 XML 声明的处理指令，包括样式表处理指令。

只接受 SVG 命名空间 `http://www.w3.org/2000/svg`，允许默认命名空间或绑定该 URI 的前缀。检查完整展开后的 QName 和命名空间声明，禁止仅剥掉命名空间后比较标签名；无命名空间、外国命名空间元素/属性、`xlink`/`xml:base` 等均不接受。

根必须且只能为一个 `svg`，不允许嵌套 `svg`。允许的其余图元为 `g`、`path`、`rect`、`circle`、`ellipse`、`line`、`polyline`、`polygon`、`title`、`desc`。图元总数最多 512，根深度计为 1、最大深度 16。未知元素或属性一律拒绝。

下表是首版允许属性的完整边界，未列出的属性不自动开放：

| 图元 | 允许属性 |
| --- | --- |
| 根 `svg` | `viewBox`、`width`、`height`、`preserveAspectRatio`，以及下面定义的公共绘制属性；命名空间声明单独检查 |
| `g` | 公共绘制属性、`transform` |
| `path` | `d`、公共绘制属性、`transform` |
| `rect` | `x/y/width/height/rx/ry`、公共绘制属性、`transform` |
| `circle` | `cx/cy/r`、公共绘制属性、`transform` |
| `ellipse` | `cx/cy/rx/ry`、公共绘制属性、`transform` |
| `line` | `x1/y1/x2/y2`、公共绘制属性、`transform` |
| `polyline/polygon` | `points`、公共绘制属性、`transform` |
| `title/desc` | 无属性、无子元素，只允许纯文本，各最多 2048 字符 |

公共绘制属性仅包括 `fill`、`stroke`、`stroke-width`、`stroke-linecap`、`stroke-linejoin`、`stroke-miterlimit`、`fill-rule`、`opacity`、`fill-opacity`、`stroke-opacity`。

| 值类型 | 首版规则 |
| --- | --- |
| 数值 | 使用有限十进制/科学计数法数值，不接受 NaN/Infinity、百分数、表达式；普通几何数值绝对值不超过 1000000 |
| `viewBox` | 必填，恰好四个数值；宽高在 0.000001—1000000 内，原点也须有限且在普通数值界限内 |
| 根宽高 | 可省略；存在时为 1—512 内的数值，可带 `px`，其他单位不支持；不用于无限制分配输出缓冲 |
| 几何长度 | 半径、宽高等长度不得为负；必需属性、缺省值和矩形圆角联动规则按 SVG 基础图元语义编写测试 |
| 颜色 | 仅 `none`、`#RGB`、`#RRGGBB`；缺省采用 SVG 默认填色/描边语义；不接受命名颜色、`rgb()`、`currentColor`、`inherit`、`url()` 或变量 |
| 透明度 | 数值 0—1；不接受百分比 |
| 描边/填充枚举 | linecap 为 butt/round/square；linejoin 为 miter/round/bevel；fill-rule 为 nonzero/evenodd；stroke-width 为 0—1024，miterlimit 为 1—100 |
| `preserveAspectRatio` | 可省略；显式值只接受 `xMidYMid meet` 或 `none` |
| 变换 | 仅 translate(1/2 参数)、scale(1/2)、rotate(1/3)、matrix(6)；数值有限，每个属性最多 16 个变换，局部及累计矩阵系数绝对值不超过 1000000；不支持 skew |
| 路径 | 仅 SVG 标准 M/L/H/V/C/S/Q/T/A/Z 及小写相对形式；必须以 moveto 开始，严格检查参数组、命令衔接及弧线半径/0或1标志；不依赖 Qt 容错接受非法字符串 |
| `points` | 仅成对有限数值；polyline 至少 2 点、polygon 至少 3 点 |

单个 `d/points` 属性最多 32768 字符，全文件两类属性合计最多 65536 字符；展开重复参数组后路径段和折线段总数最多 2048。校验相对路径累计坐标，不能只检查单个输入数值；扫描需线性推进并受总预算约束，不用灾难性回溯正则。

除 title/desc 外只允许排版用空白文本。禁止 DTD、自定义实体、脚本、事件属性、foreignObject、动画、image、外部引用、data URL、style 元素/属性、use、滤镜、蒙版、渐变、字体和白名单外特性。XML 合法字符引用可用于纯文本，但不能绕过解析后的属性值检查。

SVG 规则是本项目受限图标格式，不是完整 SVG 实现。P0 将上述规则整理成正反例表；P1 实现语法与内容校验，P4 在 PySide2 5.15 真实环境确认渲染。导出工具产生 style/元数据或不支持特性时，应提示重新导出为基础路径，不在运行时偷偷清洗、改写图片或绕过检查。

### 5.4 降级与诊断

图标诊断使用独立 `ruleId = "ICON_RESOURCE"`，沿用 `ValidationIssue`。

| 场景 | 诊断码 | 行为 |
| --- | --- | --- |
| 声明类型或值非法 | `E_ICON_SPEC_INVALID` | 图标降级，保留有效算子 |
| 路径越界或不允许 | `E_ICON_PATH_INVALID` | 不读取目标文件 |
| 文件不存在、不可读或非普通文件 | `E_ICON_ASSET_NOT_FOUND` | 图标降级 |
| 格式/子集不支持、内容与后缀不符 | `E_ICON_FORMAT_UNSUPPORTED` | 说明不支持项，图标降级 |
| 容器、结构、语法非法或含禁用危险内容 | `E_ICON_CONTENT_INVALID` | 图标降级 |
| 文件、尺寸、结构、解压或总量超限 | `E_ICON_LIMIT_EXCEEDED` | 停止处理该图片并降级 |
| 安全校验依赖不可用 | `E_ICON_VALIDATOR_UNAVAILABLE` | 不进入不安全的替代解析路径 |
| 客户端解码/渲染失败 | `E_ICON_DECODE_FAILED` | 客户端兜底，不改变 Runtime 算子状态 |

图标问题在展示层为 WARNING，不标记业务节点 FAILED，不计入视觉 NG。老插件未声明图片不产生警告。按 Runtime 会话、算子、诊断码和资源摘要去重；无摘要时用声明身份代替，不在重绘时刷日志。显式刷新后仍应能查看当前诊断，去重只影响重复日志。

### 5.5 快照、磁盘修改与刷新语义

**第一版不做图标热更新。Designer 的“刷新算子”不是重新扫描插件磁盘。**

| 操作 | 规定行为 |
| --- | --- |
| 修改、删除或替换磁盘图片，Runtime 未重启 | 当前冻结 bytes、SHA 和状态不变，继续提供原资源 |
| 点击 Designer“刷新算子” | 异步读取 Runtime 当前目录，更新客户端绑定和重试预算，不重新读取 Runtime 磁盘 |
| 修改 manifest 或图片后重启远程 Runtime | 重新扫描注册；Designer 重新获取目录后使用新资源或兜底 |
| 修改图片后使用嵌入式 Runtime | 保存项目并正常关闭、重新启动 Designer，以重建内嵌 Runtime；本轮不新增就地重建运行实例按钮 |
| 当前目录已返回 invalid/none | 撤销该算子的旧图片绑定，不继续用之前缓存伪装为正常 |

“换图能失效”的前提是 Runtime 已重新注册且客户端取得新目录。只动磁盘、不重启时仍显示原图是正确行为，验收不能据此判定失败。重启可能影响作业，应在空闲测试环境进行，不为图标在生产运行中自动重启 Runtime。

## 6. Runtime/gRPC 契约

### 6.1 列表只返回元数据

保留 `OperatorInfo` 的 1—13 字段，追加：

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

// OperatorInfo 的追加字段，不是完整 message：
// OperatorIconInfo icon = 14;
// repeated OperatorIconIssue icon_issues = 15;
```

旧 Runtime 没有新字段时按 `none` 处理。`ready` 必须具备支持的 MIME、64 个十六进制字符的 SHA-256 和 1—262144 字节的大小；未知状态或不一致元数据仅使图标降级。`none/invalid` 不发布可用资源摘要和大小。

`ListOperators` 不含图片 bytes、Base64 或 Runtime 绝对路径。详细路径仅保留在 Runtime 本机日志，对 Designer 的图标诊断只返回相对资源名和必要原因。

实施前重新确认字段号，14/15 如已占用则使用后续空闲编号，不覆盖或重编号。目录来自 Runtime 当前注册快照，读取目录不隐式重新扫描。

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

三个请求字段均来自当前有效目录，只允许算子 ID、精确版本和预期摘要查找，不接受路径。空值或非法值返回 `E_ICON_REQUEST_INVALID`；不存在的算子、无资源、版本不匹配、摘要不匹配分别返回 `E_ICON_OPERATOR_NOT_FOUND`、`E_ICON_ASSET_UNAVAILABLE`、`E_ICON_VERSION_MISMATCH`、`E_ICON_DIGEST_MISMATCH`。

Runtime 只返回注册快照，不在接口中重读文件、不动态 import 图标代码、不访问相机/PLC，不要求先加载业务项目。成功响应 MIME、版本、长度、SHA 必须与目录一致；客户端重新哈希后才可解码。失败响应不携带图片内容。

使用有大小上限的 unary RPC，不为小图片引入上传或流式接口。取图不持有项目运行锁进行耗时 I/O；进程内调用同样只能做只读快照查询。

### 6.3 新旧端兼容与生成代码

| 组合 | 预期 |
| --- | --- |
| 新 Designer + 新 Runtime + 有效图片 | 异步显示图片 |
| 新 Designer + 新 Runtime + 老插件 | 使用 iconKey 兜底 |
| 老 Designer + 新 Runtime | 忽略新增字段，保留原显示 |
| 新 Designer + 老 Runtime | 无元数据不请求；UNIMPLEMENTED 按会话记忆并兜底 |
| 新 Designer + 旧进程内服务/测试替身 | 缺少新方法且无资源能力时兜底，不把普通内部异常一概当成旧服务 |
| 目录与资源版本/摘要不一致 | 丢弃响应，只允许第 7.3 节规定的一次自动目录恢复刷新 |

protobuf 新增字段的二进制兼容不等于应用兼容，必须实际测试，不能只凭 `getattr()` 默认值宣布通过。[B1]

修改 `proto/runtime.proto` 后用 `scripts/gen_proto.py` 生成代码，不手改 `generated/runtime_pb2*.py`，保留顶层 shim。维持 Python 3.10、PySide2 5.15 和仓库锁定的 gRPC 路线，不因本功能迁移 PySide6 或升级全部依赖。

### 6.4 单次超时、请求取消与嵌入式模式

客户端提供带独立关键字参数的取图方法，例如 `getOperatorIconAsset(..., timeoutMs=2000, cancellationToken=None)`。目录获取支持单次超时覆盖；已有调用省略参数时保留原 `deadlineMs` 行为。

**禁止临时修改共享 `runtimeClient.deadlineMs` 来模拟取图超时，也禁止为了取消图标请求关闭业务共用 channel。**

远程模式使用 unary callable 的 `.future(request, timeout=..., wait_for_ready=False)` 或经等价验证的可取消调用，保存调用句柄。gRPC Future/Call 的取消针对该次请求；单纯停止等待不等于请求已取消。[B2] 超时、取消、断连要转成稳定客户端诊断，不捕获后继续使用失败响应。

目录请求和图标请求归入独立的只读展示请求集合，并区分 owner。登记、取消和关闭需线程安全；owner 关闭后禁止再登记新调用，解决“关闭与刚创建 future”竞态。图标 worker 可取消自己的调用，但不能取消作业事件订阅、编辑器或计数器调用。客户端整体关闭时先停止展示任务，再执行原有关闭流程。

嵌入式模式在专用 worker 中调用进程内服务，不在 GUI 线程直接执行。两秒是客户端接受结果的逻辑期限，不是强制中断 Python 方法的能力。使用可检查取消/截止时间的本地调用上下文；方法只读取已冻结资源，目录遍历中检查取消，返回后再次检查结果是否过期。

不得对本地运行线程使用强制终止。超时后即使方法迟到返回也不能写回 UI。不得将本地函数等待超时伪装为“底层函数已停止”，必须分别测试远程取消与本地丢弃结果。

## 7. Designer 异步加载、缓存与渲染

### 7.1 职责拆分和目录刷新

| 模块 | 职责 |
| --- | --- |
| `services/operator_icon_cache.py` | bytes、摘要与元数据检查、LRU；不持有 GUI 对象 |
| `services/operator_icon_worker.py` | 有界取图、单次超时、取消、结果 DTO；不争用长连接事件队列 |
| `services/operator_catalog_worker.py`（或复用独立的短任务设施） | 异步获取目录、合并刷新、丢弃旧刷新结果 |
| `ui/operator_icon_provider.py` | GUI 渲染、兜底、QIcon/QPixmap 缓存、绑定和更新通知 |

`runtime_client.py` 增加 DTO 和取图方法，`OperatorDefinition` 新字段有默认值。目录 payload 保持 JSON 可序列化，不含 bytes 或 QIcon。

把现有同步刷新拆为“获取目录 DTO”和“应用目录”。启动、按钮刷新及图标触发的恢复刷新均走异步获取；GUI 只应用结果、刷新卡片和绑定。可保留同步客户端 API 供无 GUI 调用和测试使用，但不能从 GUI 路径调用它等待网络。

目录刷新每个 RuntimeScope 最多一个在途请求；重复点击或多个算子同时报摘要错时合并。目录默认超时 5 秒，使用独立超时参数；不能排在 4 个取图槽位或无限事件流后面。手动刷新时保留已有目录并显示刷新中，首次加载无目录则显示加载提示，失败时不把可用旧目录清空为“没有算子”。

每次目录请求携带 refreshRequestId 和 RuntimeScope，GUI 仅应用当前有效请求的结果。目录应用要通知全部图标消费者，不只重建算子气泡。只允许局部更新展示；保持选中节点、搜索条件、视角和运行态，不修改业务图和项目 revision。

目录控制器显式维护 loading/ready/failed 和是否曾获得有效目录。首次目录未就绪时，专用编辑器打开与工作流导入依赖检查暂不执行，提示目录加载状态并允许完成后再次操作；不能把空定义传给编辑器管理器后永久缓存通用窗口，也不能把未知目录当作缺失算子。已有有效目录的后台刷新不阻止这些操作；刷新失败保留旧目录。高延迟首次启动测试覆盖编辑器与工作流导入两个业务入口。

### 7.2 取图、消费端绑定与关闭生命周期

已有有效缓存立即复用，否则先显示兜底。按可见卡片、当前画布和详情需求排队；同一资源请求合并，多个同类节点共享结果。默认最多 4 个在途取图请求、128 个待取图身份；超额优先保留当前可见需求，低优先级需求留待下一次显示时申请，不阻塞 GUI。

网络调用默认 2 秒，从实际开始调用计时；本地同值为结果接受期限。排队等待不占网络 deadline，但取消/离开界面的无效需求应及时移出队列。

**资源身份与显示对象身份分开管理。**每个消费端生成订阅 token 并维护单调递增的 `bindingGeneration`。项目切换、工作流切换、当前节点选择变化、控件复用、目录重新绑定或关闭时撤销旧订阅/递增代次，即使 QObject 仍存活也不能使用旧绑定。

画布/详情绑定至少包括：

```text
(consumerToken, bindingGeneration,
 projectInstanceToken, workflowId, nodeId,
 runtimeScope, catalogGeneration, operatorId, version, sha256)
```

项目实例 token 每次打开项目重新生成；不能只用可能重复的 projectId/nodeId。算子卡片不需要项目字段，但仍要 consumerToken、bindingGeneration 和完整资源身份；系统节点不请求插件图片。

更新前必须同时确认：目标仍有效、订阅未取消、绑定代次一致、项目/工作流/节点仍是当前绑定、资源身份仍匹配。任何不一致只丢弃该次 UI 更新；合格 bytes 可留在对应作用域缓存。

例如选中 Canny 后马上选中 YOLO，Canny 请求迟到返回只能补充缓存，不能改写当前 YOLO 详情图标。退订一个节点不能取消其他节点共享的请求；没有消费者且没有预取需求时才取消底层请求。

为避免后台回调触碰已销毁 Qt 对象，worker 只向受控纯数据结果队列提交 DTO，由 GUI 线程消费。请求回调不捕获控件强引用。图标 Qt 对象只归 provider 和 GUI 消费端所有。

关闭图标子系统的顺序固定为：停止接收需求 → 撤销订阅并使绑定失效 → 清空未执行队列 → 取消所属远程调用/标记本地上下文取消 → 在 GUI 事件循环仍响应的情况下等待收尾 → 释放 provider。收尾使用一次总预算 3 秒，不对每个任务逐一等待 3 秒。

本轮展示短任务可使用固定数量的专用 Python 守护 worker 和有界队列；它们不得持有 Qt 对象、写业务数据或执行插件算法。到预算仍未返回的本地只读任务仅可独立收尾，结果通道已失效，不阻止进程退出，也不访问已关闭 UI。不得用 `QThread.terminate()`、销毁运行中的 QThread，或把 `ThreadPoolExecutor.shutdown(wait=False)` 误认为足以保证解释器退出。[B7]

图标关闭不改变 Job 停止/回收策略。正式测试需记录 worker 是否正常退出；超过预算应产生独立关闭诊断，不能把超时解释为成功取消本地函数。

### 7.3 缓存身份、恢复与重试预算

目录资源绑定身份为：

```text
(runtimeScope, catalogGeneration, operatorId, version, sha256)
```

RuntimeScope 包含连接来源和客户端连接会话 token。新建/更换 RuntimeClient、显式重连、重建内嵌服务时更换 token。远程适配器观察到失联后连接恢复时，先使旧请求失效并重新获取目录，再建立新会话绑定；不能仅因为地址相同就沿用旧绑定。透明短断线的识别边界需写进测试，不据此承诺未通知客户端的服务端变化会自动热更新。

本轮只有内存缓存。同一作用域内 bytes 按 `(mimeType, sha256)` 去重，渲染缓存还区分逻辑尺寸、设备像素比和显示状态。目录代次用于绑定而非强迫重复下载：新目录仍确认同版本同摘要时可复用同作用域缓存。

字节缓存上限 16 MiB，渲染缓存上限 32 MiB 且最多 256 项，LRU 淘汰。上限是 provider 管理的缓存预算，不等于整个 Qt 进程内存上限；显示控件可能持有资源引用，验收还要观察重复开关面板后的总内存趋势。

| 事件 | 缓存/重试行为 |
| --- | --- |
| 磁盘换图但 Runtime 未重启 | 快照不变，不声称应立即失效 |
| 新目录确认同资源 | 重新绑定，可复用同作用域有效缓存 |
| 新目录状态/版本/摘要变化 | 清除旧绑定，只有新 ready 资源才进入取图 |
| 短暂断网 | 已验证且仍对应最后有效目录的缓存可继续显示；未加载图片兜底 |
| 切换或重新建立 Runtime 会话 | 旧请求和旧绑定失效；取得新目录后按新作用域加载 |
| 内容或元数据校验失败 | 当前目录代次负缓存，不因每次重绘重试 |
| 临时网络失败 | 负缓存 5 秒，到期且再次有显示需求时最多自动补试一次；仍失败等待显式刷新/新会话 |
| UNIMPLEMENTED | 按会话记忆不支持，停止本会话图标请求；新会话重新判断 |

资源版本/摘要不匹配引发的自动目录恢复刷新，以 RuntimeScope 为单位最多一次；多个错误合并。自动刷新本身、目录代次增长、每次图标失败不得重置这个预算，避免“刷新—取图—再次刷新”循环。只有用户显式刷新或建立新会话才重置；仍不一致时保留诊断和兜底。

显式刷新清除本次请求失败预算并重取 Runtime 目录，但仍不扫描磁盘；刷新失败保留旧目录和可用缓存。新目录确认 invalid/none 时则必须撤销旧图，不能用“保留缓存”覆盖明确的新状态。

### 7.4 渲染与 UI 接入

SVG 使用 `PySide2.QtSvg.QSvgRenderer` 从已验证字节加载，检查有效性后渲染到受限缓冲；PNG 用 Qt 图片解码。接口必须在本项目 PySide2 5.15 环境验证，不能照搬 Qt 6 专属 API。[B8]

所有 Qt 渲染、QIcon/QPixmap 创建、使用和 UI 更新固定在 GUI 线程；worker 负责 RPC、内容校验和纯数据缓存准备。按小批次渲染，避免首屏集中占用事件循环。QtSvg 不可用或渲染失败时运行界面可以兜底，但正式包的成功路径验收必须失败。

公共 icon_map.icon() 也必须捕获 QtSvg 导入/渲染失败，回退到不依赖 SVG 的 QPainter 字符图标，避免工具栏或卡片构造先于 provider 失败。故障注入必须覆盖完整 MainWindow 构造，不只覆盖 provider。自绘卡片通过 setOperatorIcon 更新 _icon 并 update；画布保持固定图标槽，标题省略宽度扣除图标槽但不移动端口。

| 入口 | 接入要求 |
| --- | --- |
| 算子选择卡片 | 通过 setOperatorIcon 更新自绘图标，标题和 ID 保持纯文本；搜索、拖拽不变 |
| 画布节点 | 标题前固定图标位，独立图形项或受控绘制；不改变端口锚点、不重建整图、不覆盖运行态着色 |
| 节点详情/编辑器 | 使用同一 provider；详情复用和编辑器重新绑定必须检查 bindingGeneration |
| 左侧当前节点列表 | 纳入首轮，复用同一 provider；不能另起资源查找机制 |

卡片建议 24×24，画布/详情 20×20 逻辑像素。验证 100%、150%、200% 缩放以及当前填色、选中、运行中、失败状态的辨认性，不新增完整主题系统。

每个渲染目标物理宽高不超过 512，缓存键包含实际渲染 DPI；更高缩放可使用受限结果拉伸，不进行不受限分配。画布缩放不在每次 paint 中重新下载或无限生成缓存。

新增图标子项不得吞掉拖动、选择、双击、端口连线事件；保留文字，不能只凭图标表达算子身份。

### 7.5 项目与系统节点不变

图标引用只进入 `FlowNodeViewModel` 或由 provider 按 operatorId 查询目录，不改变 `FlowNode` 序列化。不向 `project.json`、工作流包、参数、作业快照和拖拽 MIME 写入图片 bytes、绝对路径、客户端缓存位置或消费端绑定 token。

打开旧项目后根据当前目录恢复图标。下载、刷新、失败和缓存失效不得增加 revision、触发未保存提示或改变执行结果。缺失算子仍保留业务校验错误，不能用图标兜底伪装可执行。

工作流边界、子流程和 Repeat/ForEach/While 系统节点按本地类型兜底；没有插件 manifest，不调用 GetOperatorIconAsset，不计入插件图标覆盖率。

## 8. 示例配图与后续全量配图

机制阶段只安排 `canny_edge`、`huaray_camera`、`yolo_inference` 三个内置示例，优先使用静态 SVG。PNG 支持通过测试插件和自动化夹具验证，不为演示刻意混用风格。

示例采用透明背景、24×24 viewBox、基础几何线条，无文字/嵌入字体，满足第 5.3 节子集。颜色和造型待用户审阅，不把应用品牌图标用作全部算子图标。首轮示例的资源内容摘要及非透明像素特征应可用于真实渲染断言。

三个示例的原生 SVG 已随实现新增，来源、版本和摘要见
[资源记录](../operator-icon-assets.md)。全量配图仍需独立规划：导出实际注册清单，
按用途分组，记录资源和许可来源，允许基础图形复用但须易区分。
外部图标需记录适用许可，不引入来源不明资源。

完整配图覆盖率以约定的需配图清单为分母；模板/示例算子、旧兼容插件、系统节点分别说明。

## 9. 打包与部署验收

图标随插件目录分发，保持与 manifest 的相对关系。不能仅源码环境能显示，离线工控机发布包缺资源。

Windows 发布调用外部 `jsdfhasuh/python_build_scripts` 的复用工作流。P0 必须定位 emo-master 实际打包目标和收集规则，不预设本仓库有一个可直接修改的 PyInstaller spec。

本仓库补图标资源完整性、冻结摘要、独立 GUI 渲染自检。无 GUI 自检不创建 QApplication；GUI 自检在 Windows/offscreen 等明确支持环境单独运行。检查 `defusedxml`、`PySide2.QtSvg` 及必要动态库进入实际包体。显式 QSvgRenderer 字节渲染的验收，不以文件路径 SVG 插件是否存在作为唯一条件。

**正常路径测试与故障降级测试分开：**正常路径必须记录 `renderSource=custom`、资源 SHA、实际渲染尺寸及样例像素断言；仅出现非空 QIcon、不抛异常或 `renderSource=fallback` 均不能算通过。样例使用非透明、特征明确的图形，在固定测试环境做像素特征/容差断言；不要求不同 Qt 版本产生逐字节一致的抗锯齿结果。

缺安全解析依赖、QtSvg 或资源时，运行界面可以继续兜底，但发布验收结果必须标为失败，不能用同一兜底代码掩盖打包缺陷。通用 Core CI 可以明确不运行 GUI 套件；Windows 发布门禁不能把必需 GUI 测试 skip 掉后记为通过。

原有编辑器 UI、QSS、SQL 迁移、ONNX 自检全部回归；如分发 wheel/sdist，还需验证安装后的资源而非仅源码树。

外部构建仓库改动单列依赖与提交；本次未授权修改外部仓库。正式包未验证前不将发布交付标记完成，本功能不改变安装器/压缩包策略。

## 10. 分阶段实现与修改文件

实施状态按实际进展记录；未获得测试结果的阶段不得标记完成。

| 阶段 | 当前结果 |
| --- | --- |
| P0 | 完成：字段 14/15、49 个 active builtins、格式限额、依赖和外部构建配置已核对 |
| P1 | 完成：注册快照、受限格式、独立非致命诊断及无 GUI Core 测试 |
| P2 | 完成：真实 gRPC/嵌入式调用、超时取消、兼容及真实连接恢复测试 |
| P3 | 完成：异步目录、有限 worker/LRU、重试与会话/绑定隔离、关闭预算测试 |
| P4 | 完成本地验收：卡片/画布/侧栏/详情/编辑器接入，100/150/200% 实际 Qt 截图 |
| P5 | 本地冻结 ZIP 隔离验证通过：49 个算子、3 个 UI、九组真实图片及全部旧自检；正式安装器和跨机器待验收 |
| P6 | 完成：开发指南、来源摘要、DPI 截图、源码/包体 JSON 结果及未验收环境均已记录 |

最新 `scripts/ci_check.py`：916 passed、1 skipped（真实相机 smoke），proto drift、ruff、
mypy 通过。后续已修复搜索浮层将原生窗口点击误判为外部点击的问题，并补七项回归；
此前通过发布前验证的本地 ZIP 未重建，不含这项后续修复。
未执行的正式安装器和现场验收不计为完成。

| 阶段 | 主要改动 | 通过条件 |
| --- | --- | --- |
| P0 契约与基线 | 复核提交、proto 字段、算子清单和打包基线；冻结 SVG/PNG 正反例、XML 依赖版本、目录刷新/关闭策略和 UI 覆盖 | 记录原有失败；不能带着未决解析规则进入 P1，也不能掩盖既有问题 |
| P1 声明与资源注册 | 修改 core/plugin 的 models、validator、registry；新增 icon_resources；维护安全解析依赖及单测 | 受限 PNG/SVG、非致命诊断、冻结快照、无 Qt 和老插件全部通过 |
| P2 RPC 与客户端 | 更新 proto、Runtime service、RuntimeClient/Protocol 和 DTO，生成代码；单次超时、句柄及取消适配 | 真实 gRPC 与嵌入式调用分别通过；不改共享 deadline、不误关业务 channel |
| P3 目录、取图与缓存 | 新增目录 worker、图标 cache/worker/provider；拆分目录获取/应用 | 手动/自动目录刷新异步；请求合并、重试上限、作用域隔离和关闭测试通过 |
| P4 UI 接入 | 修改卡片、画布、主窗口、详情/编辑器，并覆盖旧项目及工作流恢复路径 | bindingGeneration 防串图；正常图片真实显示；交互、运行态、保存语义不回退 |
| P5 示例与包体回归 | 三个示例 manifest/assets，package_selftest 和 GUI 自检；按实际构建规则补资源 | 独立 Windows 包真实渲染；缺依赖不能假通过，故障降级单独验收 |
| P6 文档与验收记录 | 更新 plugin-registration-flow，补协议/测试说明及实施记录 | 第三方图标插件不改 Designer 即可接入；未验收环境明确标记 |

建议按 P1、P2、P3/P4、P5/P6 分小提交。P4 同时核对 `controllers/workflow_controller.py` 中创建/恢复视图的路径，以及 `operator_editors/manager.py`、`workspace_window.py`、`ui/node_param_dialog.py` 的窗口复用和关闭；不要只改“新拖入节点”路径。

不顺带实施算法重构、项目格式迁移、GUI 框架升级或完整插件热重载。

## 11. 测试矩阵与完成标准

### 11.1 自动化测试

| 层级 | 必测场景 |
| --- | --- |
| 声明兼容 | 缺失/空字段、合法资源、错误类型、未知 iconKey；既有 manifest/meta 回归 |
| 路径/读取 | 两类操作系统绝对路径、盘符相对路径、UNC、反斜杠、URL、穿越、符号链接、目录代文件、权限、读取变化 |
| PNG 容器 | 假签名、CRC、块长度/顺序、重复 IHDR/IEND、尾随字节、PLTE/tRNS 错配、非法位深、APNG、隔行、未知块 |
| PNG 解压 | 压缩附加块即使文件小/像素少也拒绝；IDAT 超额、不足、截断、多流、非法滤波；不发生无限制解压 |
| SVG | QName/命名空间欺骗、无命名空间、DTD/实体/PI、脚本/事件/style/use/外链、未知属性、非法颜色/变换/path/points、非有限和超界值、结构预算 |
| 解析依赖 | 安全解析器不可用时 SVG 降级而有效算子仍注册；不能退回不安全解析器；正式 SVG 包验收失败 |
| 注册/快照 | 图标错误保留有效算子；重复 ID 仍拒绝；编辑器/图标诊断独立；磁盘换图未重启快照不变，重建 Runtime 后才更新 |
| 远程 RPC | 无项目取图、非法请求、ID/版本/摘要错误、重新哈希、新旧协议、UNIMPLEMENTED、真实 deadline 和单请求取消 |
| 嵌入式 | 不经网络仍用 worker；本地调用逻辑超时、取消上下文、迟到结果丢弃，不声称强制中断函数 |
| 目录刷新 | 高延迟下 GUI 仍响应；并发刷新合并；旧请求不覆盖新目录；摘要错误只触发一次自动恢复；刷新失败不清空旧目录 |
| 缓存 | 共享请求/LRU、同 ID 同版本不同 SHA、换 Runtime/重连、断网前后缓存差异、有限补试；代次变化但资源不变可复用 |
| 绑定竞态 | Canny→YOLO 快速选择、工作流切换、项目重开且 nodeId 相同、卡片重建、删除节点、关闭窗口时迟到回调 |
| GUI | 真实 SVG/PNG 与 fallback 分开断言；DPI、标题、鼠标、运行态、局部更新；退订一个节点不影响共享消费者 |
| 关闭 | 取图与目录请求在途时关闭；并发调用登记竞态；单次总预算、无无限 join、无 QThread 强杀、不关闭业务共享 channel |
| 持久化/业务 | 旧项目、导入导出、工作流切换和 Job 不引入图标字段；图标错误不改 NG/失败统计 |
| 打包 | 真资源、摘要、依赖、renderSource 和样例像素检查；离线独立目录；旧自检回归；必需 GUI 套件不能被 skip 掩盖 |

本轮主要新增测试文件：

```text
tests/core/plugin/test_icon_resources.py
tests/core/plugin/test_registry_icons.py
tests/runtime/test_operator_icon_assets.py
tests/designer/test_operator_catalog_worker.py
tests/designer/test_operator_icon_cache.py
tests/designer/test_operator_icon_worker.py
tests/designer/test_operator_icon_provider.py
tests/designer/test_operator_icon_integration.py
```

同时更新既有 registry、RuntimeClient、算子卡片、画布、项目保存、工作流导出、窗口生命周期及打包测试。核心测试无 Qt 可运行；GUI 在实际 PySide2 路线执行；不得用全部 mock 替代真实 gRPC、真实 Qt 和包体验收。

### 11.2 人工验收步骤

使用空闲测试环境和项目副本。先运行一个不依赖真实设备的“本地图片读取—视觉处理—结果输出”基准工作流，记录执行结果；三个图标示例可分别放到画布检查显示，不要求为图标验收实际触发相机或 PLC。

| 步骤 | 操作及预期 |
| --- | --- |
| A 正常显示 | 正常资源启动 Runtime，检查卡片、画布、详情/编辑器，确认显示真实图片而非字符兜底 |
| B 冻结验证 | Runtime 不重启时删除/替换磁盘图片，再点“刷新算子”；冻结资源应仍有效，SHA 不变，不判作缓存故障 |
| C 重注册验证 | 停止测试作业后重启 Runtime；嵌入式模式正常重启 Designer；重新取目录后，缺失/非法资源应为 invalid 并兜底 |
| D 换图恢复 | 修复或替换为另一张合规图片，重启 Runtime 并刷新目录；应取得新 SHA，多个节点同步使用新图片 |
| E 断网验证 | 同时准备已缓存和未加载图标再断网：前者可保留，后者兜底；确认连接状态提示与图标独立，不把通信失败算成图标执行失败 |
| F 恢复连接 | 重新建立会话并获取目录，旧回调不写回；新版 Runtime 或更换目标的资源不能与旧会话串用 |
| G 换绑竞态 | 模拟取图延迟，快速切换 Canny/YOLO、工作流和项目；迟到图片只能进入合适缓存，不能覆盖当前绑定 |
| H 关闭与共享 | 同类算子多个节点共享资源，删除其中一个不影响其他节点；目录/取图在途时关闭窗口，不无界等待或访问已销毁对象 |
| I 数据不变 | 对比图标下载/刷新前后项目业务内容和 revision；不因图标出现未保存状态；在图标故障但 Runtime 正常时复跑基准工作流，结果保持一致 |
| J 部署 | 两台机器不共享插件目录仍能显示；独立 Windows 发布目录不依赖源码树；正式样例须真实渲染成功 |

负缓存和重试验证应观察实际请求次数，而非仅凭界面“看起来没卡”。关闭预算为测试条件，不作为未测环境的性能承诺。

### 11.3 完成定义

只有同时满足以下条件，才标记“图标注册机制完成”：

1. 新插件通过 manifest 和自带资源接入，不改宿主算子 ID 表。
2. 受限 SVG/PNG 成功路径与失败降级都通过，旧插件可用。
3. Runtime 无新增 Qt GUI 依赖，接口不接受或泄露任意文件路径。
4. 冻结 bytes、目录元数据、版本和 SHA 一致，磁盘修改/重启/目录刷新语义一致。
5. 手动/自动目录刷新与取图均不阻塞 GUI，单次超时独立，取消不影响共享业务连接。
6. Runtime 作用域、目录代次和控件绑定代次全部生效，无快速换绑串图和失效对象写回。
7. 不改变项目格式、输入输出、NG/失败统计、Job 停止/回收及既有执行语义。
8. 正式 GUI/包体测试确认真实自定义资源，而不是仅 fallback 或非空 QIcon；缺依赖不能假通过。
9. 真实网络、嵌入式、既有回归和 Windows 发布包均有结果记录；未验证项明确标为待验收。

全量算子拥有独立图标不属于上述条件，在后续配图计划单独验收。

## 12. 风险、回退与审阅入口

主要风险是解析/解码复杂度、取图或目录请求卡住界面、旧请求写错对象、缓存作用域混淆和打包资源遗漏。分别以受限格式、独立调用预算、有界 worker、绑定代次、会话隔离和真实图片渲染门禁控制。

回退优先恢复字符兜底，保留兼容的新 protobuf 字段；字段号不要随意复用。撤销示例 iconResource 不需迁移项目，但须重启 Runtime 才改变冻结快照。正常 revert 功能提交，不强制覆盖目标分支历史，不修改用户工作流。

建议审阅第 0 节修订对照、第 3 节 D1—D9，重点核对 5.3 受限格式、5.5 刷新语义、6.4 单次超时与两种 Runtime、7.2 换绑/关闭及 11.2—11.3 验收门禁。

当前审批记录：`APPROVED`（2026-09-10）。批准范围为本轮机制和三个示例，不包括全量配图、对外发布或外部构建仓库修改。

## 附录 A：源码依据

以下路径相对本计划目录。第 2 节的改造前事实以文首代码基线为准；本轮实现和
验收结果以当前工作树及实施记录为准，不把旧架构文档的历史问题当成现状。

- [插件模型](../../src/emo_master/core/plugin/models.py)、[字段校验](../../src/emo_master/core/plugin/validator.py)、[注册器](../../src/emo_master/core/plugin/registry.py)。
- [RPC 协议](../../proto/runtime.proto)、[Runtime 服务](../../src/emo_master/apps/runtime/grpc_server/service.py)、[协议生成脚本](../../scripts/gen_proto.py)。
- [Designer 启动及两种 Runtime 模式](../../src/emo_master/apps/designer/main.py)、[客户端](../../src/emo_master/apps/designer/services/runtime_client.py)、[目录控制器](../../src/emo_master/apps/designer/controllers/operator_catalog_controller.py)。
- [现有字符图标](../../src/emo_master/apps/designer/ui/icon_map.py)、[算子卡片](../../src/emo_master/apps/designer/ui/operator_bubble.py)、[主窗口与详情复用](../../src/emo_master/apps/designer/ui/main_window.py)、[画布](../../src/emo_master/apps/designer/ui/flow_scene.py)。
- [业务节点模型](../../src/emo_master/apps/designer/state/flow_graph_model.py)、[工作流控制器](../../src/emo_master/apps/designer/controllers/workflow_controller.py)、[系统节点目录](../../src/emo_master/apps/designer/state/system_node_catalog.py)。
- [Canny manifest](../../src/emo_master/plugins/builtins/canny_edge/manifest.json)、[现有注册说明](../plugin-registration-flow.md)。
- [依赖声明](../../pyproject.toml)、[运行依赖](../../requirements.txt)、[Windows 发布工作流](../../.github/workflows/release-windows.yml)、[包体自检](../../src/emo_master/apps/package_selftest.py)。

## 附录 B：外部接口与格式依据

下列资料用于核对接口或格式。本文白名单、数字限额、超时及分期均为本项目设计选择，不是外部标准要求。

- [B1 Protocol Buffers proto3：更新消息类型](https://protobuf.dev/programming-guides/proto3/#updating)：追加字段与字段号兼容原则；应用行为仍须测试。
- [B2 gRPC Python API](https://grpc.github.io/grpc/python/grpc.html)：UnaryUnaryMultiCallable.future 的单次 timeout、Future/Call 及 Channel.close 行为。在线文档版本可能高于仓库锁定版本；P0 必须在实际 gRPC 版本验证，不以本功能为由直接升级。
- [B3 W3C PNG 第三版规范](https://www.w3.org/TR/png-3/)：容器结构、块顺序、合法颜色类型/位深、独立压缩附加数据。本文只支持其受限静态子集。
- [B4 Python 3.10 zlib](https://docs.python.org/3.10/library/zlib.html)：增量解压的 max_length、eof、unconsumed_tail、unused_data 等边界。
- [B5 Python 3.10 XML 安全说明](https://docs.python.org/3.10/library/xml.html)：不可信 XML 的解析风险及安全处理背景。
- [B6 defusedxml 上游说明](https://github.com/tiran/defusedxml)：forbid_dtd、forbid_entities、forbid_external 参数及显式使用安全解析入口。
- [B7 Python 3.10 concurrent.futures](https://docs.python.org/3.10/library/concurrent.futures.html)：Executor.shutdown 的 wait=False 不代表未完成任务不会影响进程退出。
- [B8 Qt 官方 QSvgRenderer 说明](https://doc.qt.io/qt-6.5/qsvgrenderer.html)：字节加载、有效性检查和渲染模式的参考；这是 Qt 6.5 页面，不作为本项目升级 Qt 的依据，实施必须在 PySide2 5.15 验证相应 API。
