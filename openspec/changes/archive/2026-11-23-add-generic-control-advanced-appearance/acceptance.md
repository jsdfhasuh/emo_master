# 验收要求与实施记录

方案版本：0.7
计划代码核查基线：`6cbff0dcbea671663cf9ab773c6ce5c81690da35`
收尾核查日期：2026-11-23
收尾核查基线：`4f3ea2da9ddb2157d85cff195e7fb0b2f5fa3bb6`
实施与验证的逐项记录见 [tasks.md](tasks.md)。

本文件记录验收要求和结果，不是第二份任务清单。实施进度只维护在 [tasks.md](tasks.md)；完整组件范围及部位定义见 [component-coverage.md](component-coverage.md)。

**当前结论：13＋2 完整范围的实现及约定验证已完成，功能与 CI 具备收尾条件。** [当前完整 Source CI](https://github.com/jsdfhasuh/emo_master/actions/runs/45017102613) 和 [适用产品版本的 Web CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44990324003) 均成功。下方首次功能验收记录、截图及初期失败保留原始基线；最后的“2026-11-23 收尾核查”说明后续修复、当前证据及未验证边界。

## 1. 使用方式

准备一个包含全部纳入类型的示例工程，按阅读需要放在同页或分成少量页面；页头包含开始、停止按钮。可以复用受控样例数据；工程须经实施 HEAD 的正式创建／加载与保存路径生成，不要求为每个控件新建工程。

每个纳入对象在 Qt 与 Web 各验证一个常用字段变化和一个本端代码变化。记录表按 15 个对象、两端结果填写，表示覆盖要求，不要求 30 套独立测试脚本或 30 张独立截图。一张清晰截图可对应多个控件，结合配置与结果记录定位具体变化。

这只是逐类型基本覆盖。新增颜色、边框细节、图标排列和按钮状态还须按第 4 节的代表对象组合验证；不能只使用原有字号字段或所有对象外框变色来结束。

Qt 应覆盖 Designer 的编辑／预览及对应 Qt Runtime 页面；Web 应覆盖编辑／预览及发布后的运行页面。可在同一次场景验收中完成，不重复执行全部属性组合。

保存、开关、确认／取消／撤销、复制和隔离等共享机制按代表对象验证，不逐类型重跑。普通组件与宿主走不同作者入口时，各确认一次入口正常；其余共用逻辑复用证据。规范场景、任务和下表可以引用同一操作，不要求分别建自动化测试或同时提交人工录像。

截图、日志及记录注明源码提交、运行环境、页面与样例数据来源。历史截图和设计原型只能作为参考，不能充当本次功能完成证据。Qt 离屏渲染或软件 Runtime 的证据应准确标注，不称为现场人工验收。

## 2. 全部对象的基本覆盖记录

Qt 与 Web 的结果格建议填写“待实施／通过／失败／受阻”，并附对应证据位置。未覆盖项须保留实际状态；不能用“通用机制已完成”替代缺少的对象结果。

| 编号 | 对象 | 常用字段代表变化 | 本端代码代表变化 | Qt 结果／证据 | Web 结果／证据 |
| --- | --- | --- | --- | --- | --- |
| C01 | `image` | 视口背景 `#253950` | 外框底边 3 px | 通过，正常换帧并保留标注。[Qt 生产] | 通过，图片地址更新后计算样式仍匹配。[Web 生产] |
| C02 | `text` | 主值 14 px、`#214D85` | 实际文字加粗 | 通过，说明文字清晰，嵌套文字正常换行。[Qt 生产] | 通过，说明文字为粗体，嵌套文字保持自身样式。[Web 生产] |
| C03 | `number` | 实际值 52 px、`#214D85` | 实际主值加粗 | 通过，`10.0° → 15.0°` 后样式保留。[Qt 记录] | 通过，`5.0° → 10.0°` 后样式保留。[Web 记录] |
| C04 | `indicator` | 业务状态的颜色、背景和 1 px 基础边框 | 左边框 4 px，独立 `marker` | 通过，实际值及圆点可分别设置，偏离后文字不沿用合格色。[原生 Qt 测试] | 通过，单独改变圆点尺寸，状态刷新保留尺寸，主值独立着色。[Edge 回归] |
| C05 | `runtime_status` | 状态文字 16 px、`#214D85` | 实际状态文字加粗 | 通过，运行中到已中止时样式保留。[Qt 记录] | 通过，真实运行状态文字应用 `value` 粗体。[Web 生产] |
| C06 | `table` | 浅蓝表面与外框 | 实际“类别”表头 | 通过，运行结果表内容及第二页，表头样式保留。[Qt 对照] | 通过，正常翻页，表头保持 `#D9E8FA`。[Web 对照] |
| C07 | `navigation_button` | 右侧图标 12 px、间距 6 px | 按钮左边框和悬停背景 | 通过，点击原按钮进入历史回看。[Qt 生产] | 通过，悬停变色，点击进入历史回看。[Web 记录] |
| C08 | `container` | 背景及边框 | 自身外框；内部独立 | 通过，实际容器和子文字隔离，复制后所有者独立。[原生 Qt 测试] | 通过，嵌套及相邻元素保留自身基础样式。[Edge 回归] |
| C09 | `page_title` | 标题 24 px、`#214D85` | 实际标题加粗 | 通过，“控件对照”标题实际显示。[Qt 对照] | 通过，实际标题 24 px 粗体。[Web 对照] |
| C10 | `page_navigation` | 浅蓝底色及外框 | 当前页选中态 | 通过，页签和按钮模式均按实际选中状态切换。[原生 Qt 测试] | 通过，页签和按钮模式中选中样式随切页切换。[Edge 回归] |
| C11 | `clock` | 时间文字 19 px、`#214D85` | 实际时钟文字加粗 | 通过，时间更新后保留样式。[Qt 对照] | 通过，等待一次真实计时更新后仍为粗体。[Web 记录] |
| C12 | `sqlite_table` | 浅蓝表面和边框 | 表头及列表选中项 | 通过，真实记录及列表选中背景、文字颜色。[Qt 历史] · [Qt 对照] | 通过，真实归档表头及选中行切换保留样式。[Web 历史] · [Web 对照] |
| C13 | `sqlite_value` | 汇总值 26 px、`#214D85` | 实际数值加粗 | 通过，`6 条 → 9 条` 后字号、颜色保持。[Qt 记录] | 通过，真实 SQLite 计数与数据库记录数一致，显示单位和粗体。[Web 对照] |
| H01 | 页头 `startControl` | 右侧 12 px 图标、6 px 间距 | 正常／悬停／按下／禁用配色 | 通过，原生四态像素及实际开始。[Designer 记录] · [Qt 就绪] | 通过，计算样式及实际开始。[Edge 回归] · [Web 就绪] |
| H02 | 页头 `stopControl` | 独立颜色与图标配置 | 正常／悬停／按下／禁用配色 | 通过，原生四态像素，实际停止后终态 ABORTED。[Designer 记录] · [Qt 记录] | 通过，四态预览，实际对所选运行发停止请求并进入 ABORTED。[Edge 回归] · [Web 记录] |

`spacer` 保持固定／弹性布局设置。Designer 原生入口验证及 Web 组件判断均将空白块留在布局入口。[Designer 记录] · [Web 编辑外观入口](../../../../web_panel/frontend/src/editor/PresentationFields.tsx)

图片与数据库类使用现有支持的数据来源。Web 数据库组件必须展示实际样例记录或数值，不能仅对“请先选择查看会话”等占位提示截图并认定该类型通过。Web 不需要为本项能力直接读取 Designer 所在电脑的本地路径。

## 3. 少量深入场景

以下场景集中覆盖共享机制与明确的渲染差异，可复用上面的示例工程和截图。除记录的风险外，不展开颜色、字号、状态、数据来源与平台的全排列。

| 编号 | 场景与操作 | 预期结果 | 当前记录 |
| --- | --- | --- | --- |
| A01 | 在示例工程中同时保存各组件的通用配置、Qt QSS 和 Web CSS；重开工程，沿用原流程生成页面发布数据并重新载入 | 字段和代码原文保留；两端只应用自己的语言；修改一端代码不会清除另一端。未配置原生代码的组件沿用基础外观 | 通过，工程保存与发布包重读、Web 草稿与正式发布都检查原文。[Designer 记录] · [Web 记录] |
| A02 | 代表组件启用、停用、重启用，再分别尝试清空后取消与清空后确认；与 A01 合并保存重开 | 停用保留代码；清空只置空当前语言 code，开关、通用配置和另一端不变；取消不提交清空；确认后的空代码使用当前通用／状态外观 | 通过，核对空代码、开关、另一侧及结构化样式即时回退。[Designer 原生测试] · [Edge 回归] |
| A03 | 实际启动 Designer／Web，从原入口进入代表组件弹窗，试改、预览、取消／关闭／Esc、确定及撤销；宿主独立入口补一次确认 | 草稿只影响局部预览；取消／关闭／Esc 不提交；确定一次提交，原画布更新且可撤销。打开前 `commitPending()` 提交的其他字段不回退；不能仅直接调用渲染函数代替这次界面操作 | 通过，原生 Designer 与真实 Edge 操作，页头按钮入口补验。[Designer 记录] · [Edge 回归] · [Web 记录] |
| A04 | 同页放置嵌套容器、两个相邻同类组件，并复制一个；修改容器边框与文字色／字体，内部放无自定义代码的文字组件 | 直接匹配限定自身部位；子组件保留自身基础文字外观；相邻与其他实例不误改，复制后代码可命中。正常容器布局变化不算外溢，无需恶意选择器矩阵 | 通过，检查当前对象与副本不同颜色、字体，子组件与邻项保持各自外观。[Designer 原生测试] · [原生 Qt 测试] · [Edge 回归] |
| A05 | 在同一运行场景中刷新判定状态、数值、运行状态、时钟、图片和表格结果 | 自定义样式持续生效；原数据、精度、单位、状态映射和布局仍正确。判定示例不把 NG 状态无条件染成合格颜色 | 通过，实测不同刷新路径，包括 SQLite 聚合实际刷新和图片换帧。[Qt 记录] · [Web 记录] |
| A06 | 验证 Qt 图片视口背景，以及 SQLite 列表的普通／选中外观；同时对照 Web 对应显示 | 常用配置确实进入 Qt 自绘过程；列表委托没有覆盖已支持的样式设置；没有虚构单元格 QLabel 选择器。所示部位和代码提示与实际实现一致 | 通过，图像视口及列表文字／选中背景可见。[Qt 生产] · [Qt 历史] · [Web 历史] |
| A07 | 对页面导航分别使用现有页签模式和按钮模式，切换当前页 | Qt 的 QTabBar 与可选中 QPushButton 均有正确的选择器／选中态；Web 对应模式正常。普通集合表不因样式新增选行交互 | 通过，两端导览使用各自的实际选中属性。[原生 Qt 测试] · [Edge 回归] |
| A08 | 两个页头按钮预览四态；每端各做一轮正常开始／停止，可复用同一既有软件 Runtime 和受控工程 | 预览不派发命令；真实按钮可用性、命令及停止目标正常，运行样式正确。不增加相机、PLC、机器人接入或运行模式全组合 | 通过，两端启停终态 ABORTED，均为隔离的受控工作目录。[Qt 记录] · [Web 记录] |

如果项目的撤销／关闭操作形式在某端不同，应沿用该端正式编辑流程并记录实际结果，不为外观功能另建一套工程事务系统。操作形式可以不同，但不得削弱草稿隔离、确定一次提交、可撤销及取消／关闭／Esc 不提交的结果要求。

## 4. 样式示例与诊断检查

### 4.1 新增能力和目标效果

按 [examples/target-effects.md](examples/target-effects.md) 使用同一份配置回放以下效果，合并到上述覆盖和 A01／A03／A08，不新建一套项目或重复流程。

| 能力 | 代表对象与判定 | 当前记录 |
| --- | --- | --- |
| 自定义背景／文字／边框颜色 | 方向与角度使用目标配色和单位 | 通过。[Qt 生产] · [Web 生产] |
| 边框宽度与线型 | 方向左侧 4 px，代码关闭按基础 1 px，结构化线型显示 | 通过，Qt 读取真实虚线像素，Edge 核对虚线／点线和撤销后的实线。[原生 Qt 测试] · [Edge 回归] |
| 图标位置、尺寸与间距 | 右置 12 px，间距 6 px，按钮 14 px 文字、圆角 8 px | 通过，实际按钮绘制与 DOM 样式符合配置。[Qt 就绪] · [Web 就绪] |
| 按钮状态及基础继承 | 两按钮四态、部分悬停字段及回退 | 通过，原生 Qt 像素、Edge 预览和真实按钮状态。[Designer 记录] · [Edge 回归] |
| 内部部位 | 主值、圆点和实际表头／选中项 | 通过，对应 C02–C06、C11–C13 的实际部位。 |
| 实际操作到运行 | 原入口、局部预览、确认、原画布、保存重开及运行 | 通过，由下方实装截图和两端运行记录复核。 |

按钮四态先在一个对象上通过表单配置检查，再应用示例代码复用同一预览；不要求全部控件的表单与代码每种组合分别测试。使用实际启动的作者端，可采用现有自动化或手工操作之一，不同时强制两套证据。

### 4.2 有限诊断

代码示例采用实施后的真实选择器，由编辑器当前对象的部位提示产生。样例至少包含：文本／数值部位、判定外框或圆点、表头、页面导航选中态、图片外框、容器自身，以及宿主按钮状态。

Qt 与 Web 示例各自使用支持的语法。例如 Qt 的 `QHeaderView::section`、`QTabBar::tab:selected` 不应被翻译成不存在的 QWidget；Web 的 `:active` 不应直接复制为 Qt 按下态。代码规则、示例参数和截图必须相互对应。

两端各选一个明确结构错误和一个已声明不支持的规则形式，检查提示、修正后恢复，以及启用时不能确认／停用后可保留代码。错误预览显示当前通用外观并标明“当前代码未应用”，不维护最后有效预览缓存。基础检查通过不等于全部选择器命中；不自研完整语义验证器、不统计任意规则命中数、不枚举非法输入。

| 检查项 | 当前记录 |
| --- | --- |
| 编辑器只展示当前对象真实存在的部位，代码可直接命中 | 通过，覆盖实际部位见 [逐类型对照](component-coverage.md)。 |
| 包内代码与实际截图使用相同配置，Qt／Web 语法分别正确 | 通过，示例由同一组 QSS／CSS 文件生成，Web 通过原编辑入口录入同一 CSS 规则。 |
| 基础错误提示可理解，修改后可恢复预览 | 通过，两端验证了结构未闭合，并确认修复后预览恢复。 |
| 两端各一个明确不支持项有提示，未被宣称已经生效 | 通过，Qt 验证 `qproperty-*`，Web 验证 `@import`。 |
| 已识别错误启用时不提交；停用后可保存文本，预览标识与实际状态一致 | 通过。[Designer 原生测试] · [Edge 回归] |

### 4.3 不扩大的验证范围

只新增本功能涉及的模型往返、作用域、预览提交和渲染路径检查；复用既有测试工具。原项目必须通过的门禁照常执行，不把它们全部改造成高级外观专属测试。示例工作流只有新建／修改时才走已有编译校验，不新增调度、设备、通信或 SDK 兼容专项；不增加平台／浏览器／分辨率组合、压力、模糊测试或生产硬件验收。收尾提供必要实装截图，无需重绘全部 14 页原型。

## 5. 首次功能验收与交付记录

本节保留首次功能验收时的实际源码、环境与结果；后续修复和当前 CI 状态见文末收尾核查。

| 项目 | 首次记录 |
| --- | --- |
| 实际被测源码提交 | Windows 当前分支 `main`，HEAD `1e47522ccb8451f1665852caa5ae6ff333b340bb` 加本轮工作区改动。原有四份 11 月 13 日验证记录和 `.tmp/` 已保留。 |
| Qt 测试环境与运行方式 | Windows / Python 3.10.21 / PySide2 5.15.2.1 / Qt 5.15.2。用原生 Qt 窗口、QTest 和实际 QWidget 截图。 |
| Web 测试环境与浏览器 | Windows / Edge 157.0.4341.42 / Playwright。一次真实网关、HTTP／WebSocket 和软件 Runtime 验收，一组独立的浏览器交互回归。 |
| 示例工程与数据来源 | [inspection.emoproj](../../../../examples/advanced_appearance/inspection.emoproj)，正式创建、保存重读及工作流编译通过。复用合成图与软件检测流程。 |
| 对应页面发布包 | [inspection.emoweb](../../../../examples/advanced_appearance/inspection.emoweb)，正式发布投影与包重读通过。 |
| 两端样式示例与配置 | [QSS／CSS 示例](examples/README.md)，纳入对应示例工程。 |
| 覆盖表和深入场景的证据位置 | 本页第 2–4 节、实际截图和出具过程记录。 |
| 首次失败与未验证项 | 首次 Web 全量后端检查出现 IPv6 TestClient 错误，当时尚未执行远端 CI；该错误不再是当前未关闭的 CI 问题，后续 Windows／Linux 后端全量检查均通过，见文末收尾核查。冻结 EXE 和真实生产设备仍未验证。首次本机一个 Git 忽略的旧 2.9 迁移用例未纳入 Qt 页面回归。 |

完成判定需要覆盖表中的 15 个对象在两端均有有效结果，明确的特殊渲染路径和深入场景已核对，且失败／受阻项如实记录并解决；不能只凭设计图、代码字段存在或代表性的三个控件通过就结束。

## 首次基线核对与协议验证

`6cbff0dcbea671663cf9ab773c6ce5c81690da35` 与实际 HEAD 之间的差异涉及相机适配及历史记录，
页面产品源码一致。远端 `main` 核对到 `9fc675031a0ca7be03b4fb2ea90b9e93a4a8f423`，
该提交相对计划基线只调整四份 UI／Runtime 测试。本轮在原分支、原 worktree 实施。

共享模型与工程协议测试 327 项通过。Web 后端草稿、官方包及交付契约 33 项通过，
其中新增用例通过独立的 SDK 1.9.0 wheel，实际调用草稿保存、重开、CSS 清空和正式发布，
核对原 QSS （含尾随换行）、当前开关和按钮状态均保留。

生成脚本更新了工程展示及发布 Schema、生成 TypeScript、SDK wheel 与摘要。验证包保留现有
契约示例和静态夹具的字节内容。OpenSpec CLI 2.0.0 strict 通过。

## 首次实装截图

| 场景 | 证据 |
| --- | --- |
| Designer 原入口、局部预览、保存重开 | [外观入口] · [方向 QSS] · [关闭 QSS] · [数值卡] · [开始按钮] · [停止按钮] · [画布重开] |
| Qt Runtime | [Qt 就绪] · [Qt 生产] · [Qt 历史] · [Qt 对照] |
| Web 编辑器 | [CSS 局部预览] · [编辑画布] |
| 发布后的 Web Runtime | [Web 就绪] · [Web 生产] · [Web 历史] · [Web 对照] |

Designer 使用受控显示快照回放样例值与实际图像。Web 画布与局部预览使用既有示例数据模式。
真实 Qt 与 Web Runtime 各在临时副本中以连续采样运行，确认选中数据、界面状态和实际停止。
示例交付库保持空白。首次 Windows 验收工程采用 CRLF 换行，SHA-256 为 `3edd4d36bbf0e27919e42bfab52b68a3de055a488f2eb8f9b105da83011635eb`，
Web 包为 `6025544b12f7643c573a3a7da3c379f61a5f280ed32b11820a051ffca8fc5c8b`。
2026-11-23 核对仓库中的同一工程采用 LF 换行，SHA-256 为 `931eb009590315cd01c8f0fa08f33d12870fa813e6a813a7bef305603e2492a7`；仅将 LF 转为 CRLF 后即与首次工程摘要一致。Web 包摘要仍一致，不改写原始验收 JSON 中记录的摘要。

## 首次运行命令与范围

- Qt 页面回归 222 项、Operator UI 103 项通过。页面回归排除 Git 忽略的本机旧迁移用例
  `tests/ui/presentation/test_migration_operator_pages.py`。该文件中的旧 2.9 夹具被当前严格模型拒绝。
- Designer 原入口与页头 21 项原生测试通过。补充运行验证了复制隔离、结构化虚线以及不支持规则诊断。[Designer 记录]
- 最终组合回归 352 项通过，包含上述 327 项共享模型与工程协议测试、Designer 原入口及 Qt 高级外观定向测试。
- Web `npm run build`（含 TypeScript）通过，`npm test` 204 项通过。浏览器高级外观回归使用真实 Edge 和模拟草稿 API，
  真实发布与启停使用本机软件 Runtime。旧版浏览器冒烟及六条编辑器回归也通过。[Edge 回归] · [Web 记录]
- 使用交付的 SDK wheel 与仓库声明的 `httpx2` 运行 Web 后端全量 `unittest discover`，共 231 项、7 项跳过、1 项错误。
  `test_ipv6_and_default_port_authorities` 在 Starlette 1.6.0 TestClient 解析 `[::1]:8780` 时抛出 `ValueError`。
  使用空 Starlette 应用单独复现了同一错误。外观草稿、正式发布及生成契约的 33 项定向检查通过。
  此处是首次环境下的失败记录；当前后端全量 CI 已通过，见后续收尾核查，不将该历史错误继续登记为待修复项。
- 项目生成与 Web 端到端回放脚本位于 [示例目录](../../../../examples/advanced_appearance/README.md)。

## 推送前合并回归

本轮实现提交 `6037f4b6` 合入远端 `main` 的 `8e4d6cd373e7d0d02ad6193d9085966211d3db05`。
保留远端按工程运行模式启动的 RPC 和 Web 调用。合并后的外观模型、最新 RPC、生成契约与独立安装的
wheel 使用 SDK 1.10.0。上方记录和截图保留首次功能验收时的源码与环境。

- 共享页面协议、Qt 高级外观、Designer 启动模式和软件 Runtime 连续运行定向回归 351 项通过。
- Web 后端使用安装后的 SDK 1.10.0 验证契约、草稿、发布、Runtime RPC 和启动采集，48 项通过，1 项可选集成测试跳过。
- 前端逻辑回归 205 项通过，TypeScript／Vite 构建和 OpenSpec strict 通过。
- 真实 Edge 使用同一示例重新完成草稿编辑、保存重开、发布、连续采样和停止，停止后任务为 `ABORTED`。[合并后回放](../../../../docs/testing/2026-11-20-advanced-appearance/web-merge-verification.json)

## 6. 2026-11-23 收尾核查

本次由 ChatGPT Web 对固定 `main` 提交 `4f3ea2da9ddb2157d85cff195e7fb0b2f5fa3bb6` 的源码、计划、既有运行证据和远端 CI 进行收尾核查。开始收尾时审查工作区干净；本次整理的是文档和规格归档，不将既有截图或软件 Runtime 回放改记为本轮重新执行。

### 两轮 CI 修复已关闭

| 提交 | 实际改动 | 后续结果 |
| --- | --- | --- |
| [`48d4008b`](https://github.com/jsdfhasuh/emo_master/commit/48d4008b07ced37859bdd9fb6294cd7258dc86bf) | 修正外观配置函数的 Ruff E731、启动模式读取的类型声明；Web 编辑器保留 renderer 传入的类名，并移除覆盖 spacer 正常高度的样式。 | [Web CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44744581204) 通过，两个平台编辑器网格与 spacer 浏览器检查成功；当时 Source CI 仍有后述 11 个失败，未将该轮记作全部通过。 |
| [`9d59fd16`](https://github.com/jsdfhasuh/emo_master/commit/9d59fd16a1d0d3ec0c60725c839ff38183d6916b) | 响应式拖放预览的临时容器及标题／折叠按钮接入同一 NativeAppearance；保留原几何比较并补一个自定义 QSS 场景；PLC 错误输入测试适配当前数值列表控件，保留错误草稿和不触达设备的断言。 | [Source CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44759100925) 与 [Web CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44759100928) 均成功。原先 8 个拖放预览及 3 个 PLC 输入失败，在 Windows／Linux 均通过。 |

### 当前完整 Source CI

[`4f3ea2da` 的 Source CI](https://github.com/jsdfhasuh/emo_master/actions/runs/45017102613) 实际执行 Windows／Linux 的 runtime、other 四个分片，以及两个完整覆盖汇总门禁，全部成功。该次不是仅检查文档或跳过源码分片的成功状态。

| 平台 | runtime 分片 | other 分片 | 平台合计 |
| --- | --- | --- | --- |
| Linux | 3399 通过，10 跳过 | 6283 通过，30 跳过 | **9682 通过，40 原有跳过，0 失败** |
| Windows | 3407 通过，2 跳过 | 6309 通过，4 跳过 | **9716 通过，6 原有跳过，0 失败** |

两平台汇总均确认 9722 个收集用例由两个分片完整覆盖；跳过仍按跳过记录。other 分片另有 66 个 subtests 通过，未重复加入上表用例数。Ruff 通过，mypy 检查的 648 个源码文件通过。

日志明确包含高级外观草稿／撤销、原生部位归属、判定刷新、宿主按钮状态、页面导航选中态，以及默认／自定义 QSS 的拖放几何检查；这些测试在不同分片中的执行结果均已核对。

### 当前适用的 Web CI

[`9a9cc511` 的 Web CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44990324003) 的 Linux、Windows 和 Windows Owning Runtime 集成三个 job 均成功。

- Linux 前端逻辑 205 项通过；Windows 前端平台回归 36 项通过，均无失败或跳过。
- Windows／Linux 后端各运行 233 项：225 项通过、8 项原有 opt-in 跳过。QSS／CSS 草稿与发布往返用例明确通过；首次 IPv6 TestClient 失败不再是当前未关闭的 CI 问题。
- 按工作流平台分工要求执行的编辑器网格、spacer、完整 Web 应用、官方预览 CLI、独立分发构建和安装检查成功；Windows Owning Runtime 的软件集成及双 Job 图像／自动重启浏览器验收成功。
- Windows 按既定平台分工不重复 Linux 的部分浏览器与前端逻辑检查；这些条件跳过不记作已执行。

已核对 [产品提交 `9a9cc511` 到当前 `4f3ea2da` 的差异](https://github.com/jsdfhasuh/emo_master/compare/9a9cc511d2c2eec588b5602405cd364e40ae313a...4f3ea2da9ddb2157d85cff195e7fb0b2f5fa3bb6)：后续仅为日志工作台 OpenSpec 交付记录、规范和归档，没有产品、测试、依赖或 CI 工作流变更。Web 工作流的路径条件不包含这些 OpenSpec 文档，因此沿用该产品版本已通过的 Web CI，不重复触发相同检查。

### 完成范围与证据边界

第 2–4 节的 13＋2 覆盖、既有原生 Qt／Edge 作者端记录、保存发布往返、软件 Runtime 启停证据，与当前源码和 CI 核查相符，功能及必要验证完成。本 change 已按下节记录归档。

2026-11-23 核查未重新运行独立的高级外观浏览器脚本、Windows 人工界面走查或软件 Runtime 全流程；最新通用浏览器 CI 也不替代该独立脚本的首次执行记录。冻结 EXE、真实相机／PLC／机器人和生产工程仍未验证，按原定范围保留边界，不追加为本次外观收尾门槛。历史原始日志、截图及验收 JSON 保留，不将它们改写成当前 HEAD 的新运行证据。

## 7. 2026-11-23 规格归档与引用检查

使用 OpenSpec CLI 2.0.0 正常执行归档，未跳过规格同步或校验。正式规格为 [page-advanced-appearance](../../../specs/page-advanced-appearance/spec.md)，本目录为 `openspec/changes/archive/2026-11-23-add-generic-control-advanced-appearance/`。

| 本轮实际执行 | 结果 |
| --- | --- |
| `openspec validate add-generic-control-advanced-appearance --strict` | 归档前校验通过。 |
| `openspec archive add-generic-control-advanced-appearance --yes` | 正常归档成功，新增正式规格的 15 条需求。超过 10 条 delta 的拆分建议为非阻断提示，本次保留已完成的单项功能范围。 |
| `openspec validate page-advanced-appearance --type spec --strict` | 正式规格校验通过。 |
| `openspec validate --archived --strict --no-interactive` | 本归档校验通过，36 项实施任务均已完成。 |
| 正式规格与原 delta 逐字比较 | 15 条需求、28 个场景正文完整一致。 |
| 归档文档与受影响外部文档的相对链接检查 | 139 处通过，无断链；修复归档增加的一层目录深度及外部入口。 |
| 示例生成器样式路径检查 | 隔离调用实际 `STYLES`、`native`、`fileStyle` 定义，5 组 QSS／CSS 文件均可读取；未运行完整工程生成或重新生成交付样例。 |
| 示例与证据保留 | `.emoproj`、`.emoweb` 与收尾基线字节一致；原始截图、运行日志与验收 JSON 未改写。 |
| `git diff --check` | 通过。 |

本次仅整理验收与交接、同步正式规格、归档资料及修复随归档变化的引用。产品渲染和交互逻辑、测试断言、CI 工作流保持收尾基线内容；不新增测试矩阵。归档提交的远端执行结果及独立 ChatGPT Web 评论以对应提交页面的实际回执为准，不与上述已通过的产品基线 CI 混用。

[Designer 记录]: ../../../../docs/testing/2026-11-20-advanced-appearance/designer-native.txt
[Designer 原生测试]: ../../../../tests/ui/page_designer/test_usable_properties.py
[原生 Qt 测试]: ../../../../tests/ui/presentation/test_advanced_appearance.py
[Qt 记录]: ../../../../docs/testing/2026-11-20-advanced-appearance/qt-runtime-evidence.json
[Web 记录]: ../../../../docs/testing/2026-11-20-advanced-appearance/web-verification.json
[Edge 回归]: ../../../../web_panel/frontend/tests/advanced_appearance_browser.py
[外观入口]: ../../../../docs/testing/2026-11-20-advanced-appearance/designer-appearance-entry.png
[方向 QSS]: ../../../../docs/testing/2026-11-20-advanced-appearance/designer-direction-qss.png
[关闭 QSS]: ../../../../docs/testing/2026-11-20-advanced-appearance/designer-direction-common.png
[数值卡]: ../../../../docs/testing/2026-11-20-advanced-appearance/designer-number-qss.png
[开始按钮]: ../../../../docs/testing/2026-11-20-advanced-appearance/designer-start-qss.png
[停止按钮]: ../../../../docs/testing/2026-11-20-advanced-appearance/designer-stop-qss.png
[画布重开]: ../../../../docs/testing/2026-11-20-advanced-appearance/designer-canvas-reopened.png
[Qt 就绪]: ../../../../docs/testing/2026-11-20-advanced-appearance/qt-runtime-ready.png
[Qt 生产]: ../../../../docs/testing/2026-11-20-advanced-appearance/qt-runtime-production.png
[Qt 历史]: ../../../../docs/testing/2026-11-20-advanced-appearance/qt-runtime-history.png
[Qt 对照]: ../../../../docs/testing/2026-11-20-advanced-appearance/qt-runtime-coverage.png
[CSS 局部预览]: ../../../../docs/testing/2026-11-20-advanced-appearance/web-editor-appearance.png
[编辑画布]: ../../../../docs/testing/2026-11-20-advanced-appearance/web-editor-canvas.png
[Web 就绪]: ../../../../docs/testing/2026-11-20-advanced-appearance/web-runtime-ready.png
[Web 生产]: ../../../../docs/testing/2026-11-20-advanced-appearance/web-runtime-production.png
[Web 历史]: ../../../../docs/testing/2026-11-20-advanced-appearance/web-runtime-history.png
[Web 对照]: ../../../../docs/testing/2026-11-20-advanced-appearance/web-runtime-coverage.png
