# 配置与运行契约

**V1.3 · ce8f84ca 已实现基础上的收尾契约；无存量工程前提继续有效。** 本文件锁定最小可交付语义。JSON 是目标页面 1.3 的局部示意，不是完整可运行工程。字段进入现有正式模型、编辑器和发布链路；局部类名与函数拆分由实现者按仓库风格处理。

## 1. 当前格式与统一默认

**用户已明确没有需要保留的现成工程。本轮只实现当前正式路径，不承担旧工程、旧页面包或旧SDK客户端的兼容。**

| 项目 | 决定 |
| --- | --- |
| 工程 / 页面 | 所有正式新建、编辑、保存、发布和交付统一使用 2.10 / 1.3 |
| 旧格式 | 不要求读取、迁移或保持；不新增迁移器、双格式保存或升级撤销事务 |
| 显示路径 | 所有页面使用同一套主题、控件默认和布局测量，无 legacy 外观分支 |
| 属性继承 | 未自定义属性继承当前主题/组件默认；恢复默认清除该项自定义，不关联格式版本变化 |
| 序列化 | 当前模型统一读写；只要求当前工程和当前发布包往返完整，不为旧包摘要维持特殊省略规则 |
| 当前发布 | 沿用 published-1、emo-web-panel-1、panelContractVersion=1.0 的现有外层结构，sourcePresentationVersion 为 1.3 |
| 当前 SDK | 已交付基线为1.6.0；本轮 historySource 改变 owning model，按仓库规则使用下一未占用正式版本并同步消费者。文档V1.3本身不触发版本升级；不得覆盖同名旧wheel |

新建仍走正式 WorkflowStore/保存入口，并保留设备、变量、资源、生产、Dispatch 和结果采集的现有功能。这是当前工程功能完整性，不是旧文件兼容要求。受本轮影响的旧fixture/生成样例可以更新到当前格式；不用保留专门证明旧行为的测试。已有不支持版本提示可以继续使用，无需新增兼容向导或恢复框架。

## 2. 页面外观、外壳与编辑入口

### 2.1 presentation.appearance

| 字段 | 范围 | 当前默认与语义 |
| --- | --- | --- |
| theme | light | 一套浅色语义 token |
| contentMaxWidth | 0–4096 | 0 为可用宽度；正数使内容居中且不超过该宽度 |
| pagePadding | 0–64 | 默认 24 逻辑像素，不替代容器内边距 |

token 至少包含页面背景、卡片、边框、主/次文字、主色及 green/red/amber/blue/neutral 的前景和浅底色。由一份不依赖 Qt 的正式定义生成 Web 数据，Qt 读取相同定义。沿用已有响应式布局，不新增跨语言布局引擎。

### 2.2 presentation.shell

| 字段 | 当前默认与语义 |
| --- | --- |
| title | 空时使用工程显示名 |
| subtitle | 空时不占位 |
| logoResourceId | 空时隐藏；非空沿用受管图片资源与发布投影 |
| diagnosticsInitiallyExpanded | false；仅诊断区的初始展开状态 |
| showConnection | true |
| showJobState | true |
| showControls | true；仅显示宿主原本允许的操作 |

开始/停止直接复用宿主控制入口、目标选择、可用性和已有确认行为。showControls 只决定显示。连接/操作失败以简短事实提示呈现；Job ID、资源释放、原始日志等放进可展开诊断区。

Web 主栏集中显示工程身份、连接/任务状态、当前观察对象与控制对象摘要、原开始/停止及目标选择入口。Runtime/页面包选择、工作流输入等次级配置放进可展开的“运行设置”，不再以两条完整工具栏常驻主体上方。当前目标可辨别，改变目标继续原显式选择路径；切页、冻结和设置折叠不改目标。诊断是独立入口，保留简短错误摘要。不增加 shell.mode、页面启停 Action 或新控制服务。

导航沿用现有优先级：当前页含显式 page_navigation 时，宿主自动隐藏自己的重复导航；没有时显示宿主导航。允许项目有意在页顶、页底各放一个导航。不增加 navigationPlacement、配置冲突错误或生产启动拦截。

### 2.3 正式编辑

appearance/shell 默认适用于所有页面，缺省按同一当前模型补全，不表示回到另一套旧渲染。它们进入现有“页面设置 / 全局外观”区域；组件属性进入原选中组件属性面板。可选属性组区分“继承默认”和“自定义”，并可恢复默认。编辑标题等其他字段时，不应把主题继承意图改成固定的自定义值。保存、撤销、Web 编辑和正式 authoring 命令使用同一模型，不新增草稿状态系统。

## 3. 既有控件的显示属性

### 3.1 表面 props.surface

| 字段 | 取值与默认 |
| --- | --- |
| kind | none / card |
| background | transparent / surface / subtle；none 默认 transparent，card 默认 surface |
| border | none / subtle；none 默认 none，card 默认 subtle |
| radius | 0–32；none 默认 0，card 默认 12 |
| padding | top/right/bottom/left 各 0–64；none 默认全 0，card 默认全 16 |

无装饰容器承担排列，外层卡片承担边界。当前组件默认：container 为 card，叶子为 none；布局组明确选择 none。surface 是唯一表面配置，不维护 cardStyle 的旧数据回退链。标题、右侧按钮、图例和页脚由普通容器组合，不新增卡片槽位协议或业务专用卡片类型。

**表面与紧凑测量一起实现。** 新示例中的普通文本、辅助文字、图例、链接和无装饰组按内容测量，可将既有 responsive.minWidth/minHeight 明确设为 0；不得继续被通用 160×64 下限撑大。需要可点击高度的按钮、页签和状态行明确配置合理尺寸。已有 sizeHint/minimumSize/heightForWidth 和 Designer candidateGeometry 使用实际字体、内容和配置内边距，不再叠加不适用的固定标题/卡片加成。当前工程明确配置的最小尺寸仍有效。

Designer 选中框、绑定提示等编辑辅助通过既有画布覆盖层或提示显示，不插入业务内容布局。预览的可用内容宽高须与运行时一致。

### 3.2 文字与字段

- props.typography：title/value/helper 三个可选角色；每个角色可设 size（8–96）、weight（normal/bold）、color（default/neutral/primary/green/red/amber）。缺省角色继承当前主题默认；不维护旧 fontSize/cardStyle 优先级链。
- props.valueLayout：vertical / horizontal；horizontal 时标题与值同一行，helper 置下方。
- props.helperText：空时不占位；保留已有 unit/decimals/emptyText。
- text 按纯文本保留换行，并允许长行自动换行。历史文本沿用读取器已有 4096 字符截断及省略提示；不为普通 text 新建长度策略，不增加任意文件读取。
- 日期/时间可直接使用来源声明的显示列；示例需要分别显示时间和日期时，使用既有 SQLite VIEW 的声明列。首版不新增格式 DSL、HTML 或时区系统。
- 动态 text 从占位符改为时间、长字段或多行日志，以及再切回短文本时，须让自身及父布局获得当前测量，保留配置的横排对齐。尺寸取实际字体、可用宽度和换行；不能靠固定高度、隐藏换行或内部无滚动裁切掩盖问题。

### 3.3 状态

- props.indicatorView：text / badge / row；默认 text。
- props.showMarker：可选布尔；badge/row 默认有圆点。
- indicatorStates 继续采用规范 JSON 字面量键，增加整数。整数 1、布尔 true、字符串 "1" 精确区分。
- IndicatorStyle 保留 text/color，color 增加 blue；可选 background=none/soft。
- 空值、数据不可用和未映射值统一用已有 emptyText 作中性显示；内部保留原因用于诊断。无需三套 stateFallbacks 配置。

row 显示左侧名称与右侧状态。匹配状态的 background=soft 覆盖包含标题、圆点和值的整行；状态色按原语义token，padding/边框/圆角仍用既有 surface，不增加第二套外观字段。badge 的浅底只覆盖紧凑状态值区域，text 保持纯文字状态表现。无映射、NULL或不可用时清除上一状态前景/浅底，使用已有 neutral emptyText。history list 的 trailing 复用紧凑状态映射，不因此给整条历史记录染成业务状态色。

Designer 和 Web 正式新建、目录点击、拖放及属性绑定都必须接受支持的 integer/boolean/string；整型状态的渲染支持必须贯通创作与保存发布。整数1、布尔true、字符串“1”继续精确区分。

```json
{
  "type": "indicator",
  "props": {
    "title": "检测判定",
    "surface": {"kind": "none"},
    "indicatorView": "row",
    "emptyText": "—",
    "indicatorStates": {
      "1": {"text": "通过", "color": "green", "background": "soft"},
      "2": {"text": "需复核", "color": "amber", "background": "soft"}
    }
  },
  "bindings": {"value": "existing-output-source-id"}
}
```

示例码值只解释通用映射，不定义 PLC 协议。NULL、未映射或任务 COMPLETED 均不能被自动解释成业务 NG、OK 或设备回执。

### 3.4 图像 props.imageView

| 字段 | 取值与语义 |
| --- | --- |
| sizing | fixed / aspect；fixed 沿用既有高度，aspect 按可用宽度和比例求高度 |
| aspectRatio | 正数或空；空时用有效图像宽/高，尚无图时用 4/3 |
| background | subtle / dark / transparent |
| horizontalAlign | start / center / end；默认 center |
| verticalAlign | start / center / end；默认 center |

imageView 默认 sizing=aspect，aspectRatio 为空时按上表解析。统一等比 contain。显式高度和 min/preferred 约束按原布局规则工作；不以 stretch/cover 裁掉检测证据。Qt 图像比例变化须触发布局几何更新，不只 repaint；Web 去除与新配置冲突的固定 max-height。示例主图/局部图使用明确固定比例，切图时容器稳定。加载中或缺图使用占位，不能显示上一条图。

### 3.5 按钮、导航与折叠

保留 navigation_button 类型，编辑器可称“动作按钮”。props.buttonStyle=primary/secondary/link，默认 secondary；均有正常点击范围与键盘焦点。page_navigation 在 Qt/Web 按已有 navigation.mode=tabs/buttons 呈现，使用相同 pageOrder/currentPage。

container.props.collapse 可设 {initiallyCollapsed: boolean}；存在即显示展开入口，折叠时收回子内容高度。折叠状态只存当前查看会话，不使工程 dirty，不写 Runtime。

## 4. 数据归属

| 数据 | 来源与更新 |
| --- | --- |
| 连接、job_state、控制可用性 | 现有宿主，始终实时 |
| 一次检测图片、判定、对应请求和实际回执 | 已有 DataSource → resultScope，沿用 workflow_terminal 成组提交和显示冻结 |
| 历史所选图片、标量与查询位置 | 同页 sqlite_table 的查看会话，由读取/选中/翻条/筛选驱动 |

生产字段继续直绑 node_output/workflow_output，不增加 exposed variables、别名层或表达式语言。props.statusField=connection/job_state 仅用于无 bindings 的 runtime_status，从宿主取值，不进入 normal_capture。

“当前请求”与“本次检测对应请求”按真实来源区分；后者应与结果同 scope。没有实时寄存器来源时，不为页头文字增加轮询器。

## 5. 历史回看：显示、定位、同条恢复

### 5.1 一个来源、两种外观

sqlite_table.props.historyView 可设 {mode: table|list, titleField?, subtitleField?, trailingField?}。缺省 mode=table，list 字段均来自声明 columns。list/table 共用查询、排序、选择和记录状态。列表选中强调与业务状态色分开。

筛选沿用“一个声明列 + 关键词 contains”。界面默认选有效列；没有声明可筛选列时不显示输入入口。非空关键词不得配空 filterColumn，删除当前 Web 未实际支持的“所有声明列”选项；清空筛选使用原查询路径。更改筛选/排序后从首页建立新选择，不把旧条件下的页码用于新集合。

### 5.2 稳定键与按键查询

props.sqlite.recordKey 是可选单字段名，属于私有来源声明；VIEW 可指定真实唯一非空标量列。普通表继续按现有 PK（含已有复合键）或 rowid 规则。键可以不作为可见列，但必须属于声明来源；不扫描全库预先证明唯一性。实际查到空键/重复键时说明无法稳定定位。

保留 rowId 的原有稳定身份编码。有稳定键的历史来源统一返回 **keyValues**：按服务端确定的键列顺序保留真实标量值；rowId 是摘要，不能反解为数据库键。不按旧客户端或工程版本切换响应结构。未声明稳定键的VIEW可以仅展示表格；使用选中详情或逐条浏览时必须提供真实稳定键。

既有 QueryHistoryPage 增加两个可选请求字段，沿用同一项目/来源上下文：

| 字段 | 语义 |
| --- | --- |
| anchor_key_json | keyValues 数组的 JSON；为空时沿用原 page/page_size 分页 |
| anchor_step | -1 为排序中的前一条，0 为同条，1 为后一条；缺省 0 |

客户端只传键值，不传键列名、SQL 或图片路径。服务端从来源声明决定键列，用参数化查询定位。带 anchor 时，page 参数不决定目标；响应仍为既有分页结构，包含目标所在的实际有界页，并增加可选 selectedRowId。行返回新的 imageRefs。

定位目标、计算前置行数、取得目标所在页在**一次短只读查询事务**内完成，使用现有排序及稳定键次排序和原查询预算。不可把整库读入 Python/浏览器；不保持跨请求快照、不建游标服务或全局排名服务。到达前后边界时保留原记录，按返回页和选中位置更新按钮。实际记录不存在/不匹配当前筛选时清空选择并明确提示；不因“旧页没有它”判断记录消失。

Qt 本地读取器、Runtime、Qt Runtime 客户端、Web 后端与前端贯通同一行为。仅为上述两个参数修改 protobuf、按原脚本重新生成；**不新增 RPC**。当前客户端和服务端共同使用更新后的DTO；普通分页与锚点是同一接口的两种查询方式，selectedRowId依查询是否指定目标而出现，不是新旧协议兼容分支。锚点响应直接按selectedRowId更新选择。

### 5.3 同页标量引用

text/number/indicator 可选 props.recordValue：

```json
{"componentId": "history-records", "kind": "field", "field": "result_code"}
```

或引用可选查询元信息：

```json
{"componentId": "history-records", "kind": "meta", "meta": "position"}
```

meta 仅允许 position/count。源组件必须是同页 sqlite_table，字段须为声明 columns。number 接受数字，indicator 按原始类型映射；SQLite 数字 0/1 不擅自转 bool。recordValue 与同控件生产 bindings 互斥，不进入结果捕获目录。

图片继续使用原 imagePath.selected_row 与 Runtime opaque imageRefs。标量来自 HistoryRow.values，不经过路径解析。无选择时清空关联字段；NULL 使用中性 emptyText。

### 5.4 会话、导航与图片恢复

- 同一项目/页面/组件保存查询、排序、选中键和已读数据。切生产页再回历史，按原键 anchor_step=0 查询同条并取得新 imageRefs；生产推送不抢选中。
- previous/next 按选中真实键请求 -1/+1，不用旧 OFFSET 推测邻条。普通持续新增不会让“下一条”重复当前记录或倒退。
- first 保持筛选和排序，刷新首页并选首条；当前已首条或上次为空时仍可刷新。table/list 初次非空读取统一选首条，空集合无选择。
- 图像引用到期时允许按当前键重查一次并重新取图；仍失败就显示该条图片不可用。复用既有到期错误，不延长全局 TTL 或增加循环重试。
- 选 B 时立即清除 A 的图片/旧详情，提交 B 标量和 B 加载占位。沿用原 generation/请求身份丢弃 A 的晚到回调；B 缺图时保留 B 字段并显示缺图。
- 换项目、换不相容来源声明或结束会话时释放状态；不建新的全局状态中心。

### 5.5 可选 position/count

position 为本次返回 page×pageSize + 页内选中索引 + 1；时间倒序示例最新为 1/N。count 仅在页面实际引用时走既有 QueryHistoryAggregate/readValue，必须传相同 filterColumn/filterText；Qt readValue 适配需补齐这两个已有参数。

二者只表示最近查询结果，不随后台生产实时重排。简单查看已读同页记录不必重复 count；新查询结果按 generation 丢弃旧回调。无 count 就不显示总数；count 与列表来自独立请求，不承诺同一数据库瞬间。界面可选显示“第 i 条”或“第 i/N 条”，两者都不是记录身份。


### 5.6 Web 历史来源引用与复制

本节锁定已复现复制缺陷的修复设计。原生工程拥有 SQLite/ImagePath 声明；独立 Web 页面引用 Runtime 已经声明的来源。两者是当前职责的区别，不是新旧格式分支。生产 DataSource 与结果作用域不变。

#### 唯一可编辑引用组

在现有正式 Props 中增加可选 historySource，使用严格 ID 类型：

~~~json
{
  "historySource": {
    "componentId": "runtime-main-image",
    "sourceComponentId": "table-ui-a"
  }
}
~~~

- historySource.componentId：直接引用现有 historySources 目录中的 Runtime 正式来源ID，复制时不变。
- historySource.sourceComponentId：仅选中行图片使用，指向同一页面负责选择记录的 sqlite_table 控件实例ID；表格、统计值和固定图片不设置。
- 只允许已有 image/sqlite_table/sqlite_value 类型按实际支持路径使用。Web 当前创作面板仍按其已有组件范围开放，不因本字段新增统计控件功能。
- 不保存数据库路径、表名、SQL、图片文件路径或整份来源元数据。来源 kind、可选列、默认排序继续来自现有目录。
- 与普通生产 bindings、静态图片、imagePath 或实际非默认 sqlite 来源配置互斥。Props.sqlite 的模型默认空值不是第二个来源，也不要求伪造 location=runtime 的空壳。
- Web 新建历史控件保留正常生成的UI ID，并显式写入 historySource。保存、重开、正式操作与当前fixture都采用同一字段；不为旧Web草稿增加隐式ID回退或迁移。
- 原生 .emoproj 仍使用实际 SQLite/ImagePath 声明；原生 Designer 复制继续复制其声明，不强制转换为引用原Runtime来源。

纯模型验证字段形状、互斥和页内关联，不联网或读取目录。原生工程仍经 core/presentation/validation.py 验证实际数据库/表/列。Web 的来源kind、列成员及图片归属在现有 prepare/build_package 的目录上下文和发布包语义校验中确认，不拿默认空 sqlite.columns 否决合法外部引用。

#### 三种身份及发布投影

| 位置 | 语义 |
| --- | --- |
| Component.componentId | 页面的控件实例；复制后新建，页面选择会话使用它 |
| Props.historySource.componentId → PublishedComponent.history.componentId | Runtime已声明的来源地址；复制后保持 |
| Props.historySource.sourceComponentId → PublishedComponent.history.sourceComponentId | 选中行图片所跟随的同页表格UI实例 |

发布沿用已有 Published history 字段，不增加另一组发布ID；kind/columns/orderBy等从原目录派生后，移除创作属性 historySource 和原有私有路径。原生导出的实例与来源ID可以自然相同；Web复制件可以不同，两者都走当前发布模型。

selected-row image 必须关联同页表格，且“图片Runtime声明的父表来源ID”与“该表格引用的Runtime来源ID”一致。recordValue/historyView字段继续由该表的已声明列验证。图片 Runtime 声明与父表来源的一致性，由 prepare/build_package 的现有目录上下文核对；离线页面包校验删除“UI ID必须等于来源ID”的条件，保留类型、同页表格引用、声明列及包完整性校验。

#### 复制和重新编辑

复用 copyComponents 的现有一次ID映射：

1. 被复制组件的外层ID换新；historySource.componentId不变。
2. historySource.sourceComponentId、recordValue.componentId、历史动作componentId等页内引用指向复制集合内部时，改成对应的新ID；不在复制集合且仍在同页时保持原目标。
3. 复制单个表格创建独立选择会话，不抢走原有图片/字段。复制整组/整页时，详情与按钮跟随新的表格实例。
4. 在现有 Web 图片属性面板提供同页表格实例选择，展示可区分的控件标题；多个合法表格实例时由用户选择，不静默绑定任意实例。
5. 再次打开已发布页的编辑上下文时，从现有 active 包恢复目录；将图片 history.sourceComponentId 的表格UI ID解析成该表格 history.componentId 的Runtime来源ID，按Runtime来源ID去重。不得把复制后的显示实例注册成新Runtime来源。

不新增复制事务框架、别名注册表、数据库目录或新确认流程。单图/整组复制属于上述规则，不增加所有剪贴板与跨工程组合的验收矩阵。

#### 查询与图片的实际消费

HTTP仍接收页面UI实例ID并先找到当前包中允许访问的组件，再将其已声明的 history.componentId 交给原 QueryHistoryPage/QueryHistoryAggregate 或固定图片接口。禁止把任意客户端ID直接当Runtime地址。

前端选中会话、recordValue、历史按钮与图片联动使用本页表格UI ID。Runtime返回的 imageRefs 继续以Runtime图片来源ID为键，因此显示图片用图片的 history.componentId 查找引用。opaque imageRef读取、上下文、超时和一次到期恢复继续原实现。

本节不新增RPC、不修改anchor参数、不重写Runtime来源声明，也不让UI复制改变Runtime的historyDefinitionRevision。必须一起贯通 source-map 中的创建、复制、发布、包校验、网关、HistoryView和目录再读取，不能只修 copyPage 或放宽相等校验。

## 6. 有限动作

| type | 目标与行为 |
| --- | --- |
| navigate | 原 pageId/context/resultScope 规则 |
| freeze | 明确 resultScopeId，冻结当前已显示结果 |
| resume_live | 原范围，只恢复显示 |
| history_previous | 同页 sqlite_table componentId，当前排序前一条 |
| history_next | 同上，后一条 |
| history_first | 同上，刷新当前排序首页并选首条 |

历史动作不携带生产 resultScope/显示结果上下文，不读写全局变量，不触发采图或设备动作。加载中不重复发相同翻条；已知真实边界或无选择时禁用 previous/next。示例使用“上一条/下一条”，旁边标明时间倒序；first 可显示“回到最新记录”。没有业务上必须的场景，不再增加动作种类。

## 7. 通用标注与生产归档

继续扩展 vision.render.annotate 的已有 image/frame、detections、lines 输入和 overlay/frame 输出，目标小版本 1.2.0（执行时核对是否占用）。

| 参数 | 目标语义 |
| --- | --- |
| classStyles | 类别名精确映射到可选 displayLabel/color；未匹配回退原标签和 detectionColor |
| labelBackground | 标签底色；当前默认 false |
| showConfidence | 置信度开关；当前默认 true |
| colorMode | preserve / bgr；当前默认 preserve，示例明确 bgr |
| lineColor | 可选 BGR 三整数数组；缺省回退原色 |
| lineStyle | solid / dashed；当前默认 solid |
| endpointMarker | none / dot；当前默认 none |

采用单一当前Annotate实现，不为旧参数保留另一条像素绘图路径；缺省参数就是当前正式默认。颜色沿用 [B,G,R] 0–255。displayLabel 只改显示，bgr 只改绘图副本，检测对象、算法输入、frame 坐标不改变。中文用已有 Pillow 和随执行包提供的可再分发字体/许可证；无 QApplication、无现场系统字体也能绘制。Web 接收绘制好的位图，字体不复制进每个 .emoproj/.emoweb。当前执行包资源与一个 headless 绘图覆盖本轮验收；外部 Windows EXE 构建不成为新前置里程碑。

**示例必须连通生产与历史。** 使用既有 ImageSaver 保存本次图像，既有 SQLiteWriter 保存稳定 record key、图像字段、结果、时间及声明详情；写成功的同一条记录能在历史 first 后读取。页面继续显示原工作流输出，归档是示例工作流配置，不建立自动采集所有结果的新平台服务。保存后取到的图像和字段与生产页同 key 对应；模拟状态以简短标识说明。

## 8. 发布与校验

按实际使用写入 rendererRequirements：

- presentation.composable-v1：新的组合与显示属性。
- history.record-selection-v1：recordValue、historyView、recordKey 或历史逐条动作。
- shell.operational-v1：shell/statusField。

发布保持原路径保护、上下文校验和未知能力处理；新字段参与正式 Schema 与投影。recordKey/私有来源变化参与 historyDefinitionRevision，字号颜色等纯显示变化不改变它。没有目标的动作、引用不存在字段等实际不可执行配置继续定位错误；纯视觉重复导航不升级为阻断错误。

当前生成目录沿用 web_panel/contracts/panel-contract-v1/，通过既有生成器更新 schema/TS/wheel/manifest，Web 真正安装当前SDK。当前消费者和CI统一到这份生成物，不保留旧SDK的加载、摘要或固定字节验收；历史交接文档无需清理。源文件落点见 [source-map.md](source-map.md)，实施顺序见 [tasks.md](tasks.md)。


V1.3 的 historySource 必须进入唯一 owning model 并由原生成器生成当前Schema/TypeScript/wheel/manifest；Web实际安装同版SDK。工程2.10/页面1.3沿用当前格式，不为计划文档另建版本分支。未改proto时不修改生成RPC文件。本文第5.6节是当前Web来源引用的唯一规则，不引入临时复制侧表。
