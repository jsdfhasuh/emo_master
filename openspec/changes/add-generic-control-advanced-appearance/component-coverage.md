# 控件覆盖范围与部位对照

方案版本：0.7
代码核查基线：`6cbff0dcbea671663cf9ab773c6ce5c81690da35`
实施版使用下表的部位，并按当前控件与显示模式生成选择器提示。逐项生效证据见 [验收记录](acceptance.md)。

## 1. 完成范围

本变更覆盖现有 13 种可见页面组件，以及页头开始、停止两个宿主按钮。`spacer` 保留布局功能，不纳入高级外观。

按钮、状态行、数值卡可作为先贯通编辑、预览、保存与渲染的代表对象；只完成这几类不能将本变更标记完成。每个纳入对象都应有适用的常用外观配置、本端原生代码编辑入口、可命中的样式部位、预览和运行端应用。

“覆盖”不要求所有组件显示相同表单或拥有相同状态。只列出当前组件真实存在的部位；表格、图片、文本和导航按各自实现接入。CSS 与 QSS 分别保存、分别应用，不承诺任意语法互转或逐像素相同。

## 2. 逐项覆盖表

下表列出控件部位。`root` 表示当前组件外框。标题、辅助文字和按钮等由当前控件及配置决定。

| 编号 | 对象／类型 | 公开部位 | 两端代表效果 | 实施注意事项 |
| --- | --- | --- | --- | --- |
| C01 | 图片 `image` | `root`、`title`、`viewport`、`helper` | 常用配置修改外框或背景；本端代码修改外框细节；图像换帧后样式保留 | 包括采用该类型的静态图片。Qt 图像视口背景有自绘逻辑，需明确接入；图像内容、检测标注仍按原显示逻辑处理。 |
| C02 | 文字 `text` | `root`、`title`、`value`、`helper` | 常用配置修改字号／颜色；本端代码修改主文本字重等 | Qt 换行文字可能使用 `QTextEdit`，不能为所有文字只生成 `QLabel` 选择器；字体变更后核对换行与高度。 |
| C03 | 数值 `number` | `root`、`title`、`value`、`helper` | 常用配置修改主值外观；本端代码命中主值字重等；刷新数值后保留 | 保留精度、单位和绑定。数字与单位原本共用文本时，可继续共用 `value`，不虚构独立单位控件。 |
| C04 | 判定指示 `indicator` | `root`、`title`、`value`、`marker`、`helper` | 常用配置修改状态外观；本端代码加粗边框，另确认 marker 可独立命中且不改变主值文字；状态切换保持正确语义 | 图第 08 页已公开 `marker`，本轮须将拼入文字的圆点接入真实部位，保留位置、显隐与状态联动。 |
| C05 | 运行状态 `runtime_status` | `root`、`title`、`value`、`helper` | 常用配置修改文字外观；本端代码命中状态文字；连接／任务状态刷新后保留 | 显示的数据及实际可用性仍来自原状态来源。 |
| C06 | 集合表格 `table` | `root`、`title`、`table`、`header`、`viewport`、`helper`、`action` | 常用配置修改外框／文字；本端代码至少命中表头或表格项；刷新、翻页正常 | Qt 为 `QTableView`；Web 为 HTML 表格。普通 Web 集合表当前没有选行交互，不为样式新增选行功能。 |
| C07 | 导航按钮 `navigation_button` | `root`、可选 `title`、`action` | 常用配置修改按钮与图标外观；本端代码修改适用状态；原动作正常 | 图标可能是 Qt `QIcon`，不承诺它是可独立匹配的子 QWidget；图标尺寸／排列仍通过通用字段接入。 |
| C08 | 容器 `container` | `root`、可选 `title`、自身存在的 `action` | 常用配置修改背景／边框；本端代码修改容器自身外框；子组件保持各自样式 | 样式作用于容器自身，不把嵌套子组件的同名部位当成容器自己的部位。已有折叠动作继续正常。 |
| C09 | 页面标题 `page_title` | `root`、`title` | 常用配置修改标题字体；本端代码命中标题字重等 | 沿用原标题来源与换行行为，不因样式改动改变显示内容。 |
| C10 | 页面导航 `page_navigation` | `root`、可选 `title`、`tab`／`action` | 常用配置修改导航外观；本端代码修改当前页选中态；切页后正确更新 | Qt 有 `QTabBar` 与可选中 `QPushButton` 两种实现，均需覆盖；部位与选中态提示按当前模式提供。 |
| C11 | 时钟 `clock` | `root`、可选 `title`、`value`、实际存在的 `helper` | 常用配置修改文字外观；本端代码命中实际时钟文字；定时更新后保留 | 不改变原时区、格式或时间来源。 |
| C12 | 数据库表格 `sqlite_table` | `root`、`title`、`table`、`header`、`viewport`、`helper`、自身的 `action` | 常用配置修改外框／文字；本端代码命中表头或已有选中行；列表模式样式亦接入 | 检查表格与列表两种已有显示模式。Qt 列表委托直接绘制颜色，需接入样式；筛选、排序、分页与记录选择继续正常。 |
| C13 | 数据库数值 `sqlite_value` | `root`、实际存在的 `title`、`value`、`helper`／`action` | 常用配置修改数值外观；本端代码命中实际数值文字；读取刷新后保留 | 使用两端已经支持的数据访问路径，不要求 Web 直接读取 Designer 主机的本地路径。 |
| H01 | 页头开始按钮 `startControl` | `action` | 常用配置修改按钮／图标；本端代码修改正常、悬停、按下、禁用样式 | 从页头设置进入；不改为普通可拖放组件。实际启用条件与开始命令沿用宿主。 |
| H02 | 页头停止按钮 `stopControl` | `action` | 独立保存停止按钮样式，并验证对应四态和实际运行显示 | 不与开始按钮共用同一段可变配置。实际停止目标、启用条件与命令沿用宿主。 |
| X01 | 空白占位 `spacer` | 不提供样式部位 | 原尺寸、固定／弹性占位与布局行为正常 | 不展示高级外观或代码编辑入口，不作为视觉覆盖达标项。 |

## 3. 部位与原生选择器

Qt 使用 `emoPart` 标识实际 QWidget 部位，Web 使用 `data-emo-part` 标识实际 DOM 部位。编辑器根据当前对象显示可用选择器。以下写法可直接用于当前对象。

| 部位／场景 | Qt 示例形态 | Web 示例形态 | 说明 |
| --- | --- | --- | --- |
| 外框 | `QFrame[emoPart="root"]` | `[data-emo-part="root"]` | 以组件真实外框类型生成 Qt 示例。 |
| 标题／主值／辅助文字 | `*[emoPart="value"]` 或匹配实际类的选择器 | `[data-emo-part="value"]` | Qt 可能为 `QLabel`、`QTextEdit` 或其他实际文本部位；不得统一假定类型。 |
| 按钮 | `QPushButton[emoPart="action"]:enabled:hover` | `[data-emo-part="action"]:enabled:hover` | 按下态分别使用 Qt `:pressed` 与 Web `:active`。 |
| 表头 | `QHeaderView[emoPart="header"]::section` | `[data-emo-part="header"] th` | `QHeaderView` 的 section 是原生子控件，不是逐列创建的 QLabel。 |
| 表格单元格 | `QTableView[emoPart="table"]::item` | `[data-emo-part="cell"]` | 表头有独立的 header 部位。 |
| SQLite 列表选中项 | `QTableView[emoPart="table"]::item:selected` | `[data-emo-part="item"][aria-pressed="true"]` | Qt 选中文字使用表格部位的 `selection-color`。 |
| 页签模式 | `QTabBar[emoPart="tab"]::tab:selected` | `[data-emo-part="tab"][aria-current="page"]` | Qt 的 `tab` 部位标识 QTabBar，各页签由原生子控件规则控制。 |
| 按钮导航模式 | `QPushButton[emoPart="action"]:checked` | `[data-emo-part="action"][aria-current="page"]` | 切页时沿用原选中状态更新。 |

逐类型的 Qt 实际部位来自同一示例工程的 Runtime 控件树。Web 选择器由局部预览的拥有者及部位标识生成。[Qt 部位清单](../../../docs/testing/2026-11-20-advanced-appearance/qt-runtime-evidence.json) · [可复用代码](examples/README.md)

### 组件自身与嵌套组件的边界

一个容器下可能存在其他组件，其他组件也有 `root`、`title`、`value` 等同名部位。作用范围必须识别部位所属组件及页面实例；不能只给用户 CSS 加一个祖先前缀就宣称已经隔离。Qt 也不能将容器的自由规则直接作为覆盖全部后代的父级样式。

应让当前组件的规则仅命中其自身登记的部位；子组件继续应用自己的基础配置与代码。组件复制、页面预览和运行页面并存时，同样保持范围正确。这里需要简单明确的部位归属与渲染接入，不建立独立样式服务。

字体、文字色等公开的常用继承属性也应保留子组件自身基础值。用一个没有自定义代码的嵌套文字组件验证即可。容器尺寸／内距引起的正常布局变化不算外溢，不扩为任意 CSS／QSS 的全属性隔离承诺。

## 4. 自绘与非 QWidget 部位

- **图片：** Qt `ImageView.paintEvent()` 当前直接绘制背景与图像。若常用字段开放图像视口背景，应让实际绘制消费该设置。原生 QSS 首先提供真实外框和可样式化部位；不声称任意 QSS 能直接修改图片像素或绘制标注。
- **SQLite 列表：** `_RecordListRow.paint()` 当前直接绘制选中背景、分隔线、标题、副标题及状态徽标。对应常用字段和受支持的项样式必须进入实际委托绘制，不能只在父 QWidget 设置样式后把列表模式算作完成。
- **表格单元格：** 保持现有模型／视图与委托结构，使用表格、表头和 item 的真实样式机制，不为每个单元格创建 QLabel，也不引入一套虚拟 QWidget 选择器。
- **图标：** Qt 图标不一定是独立子控件。通用图标字段负责大小、位置和间距；只对真正存在的可样式化部位提供原生代码提示。

## 5. 示例与完成证据

实施后的示例按上表逐项对应。每个纳入对象在 Qt 与 Web 各有一个常用字段和一个本端代码的可见变化，复杂控件的代码示例应命中其适用内部部位，不能所有类型都只证明外框变色。

此外，按 [examples/target-effects.md](examples/target-effects.md) 在代表对象上覆盖本次新增字段和图示组合效果。两层证据可复用；逐类型覆盖不要求逐类型重复保存、取消、撤销、复制和发布。

可把多个对象放在同一工程、同一页面或多页场景中验收。实施配置、代码和真实截图采用 v0.7 目标参数，v0.6 图稿作为布局／交互参考。数据库与历史图片选择两端已有支持的数据路径；可使用明确标注的受控样例数据，不通过新增数据服务来完成外观测试。

具体结果记录见 [acceptance.md](acceptance.md)。所有实施进度只在 [tasks.md](tasks.md) 维护。

## 6. 核查依据

- [组件类型与页面模型](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_panel_protocol/models.py)
- [Qt 组件构造与图片自绘](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/renderer.py)
- [Qt 文本类型与状态刷新](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/appearance.py)
- [Qt 集合表格](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/table.py)
- [SQLite 表格与列表自绘](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/sqlite_widgets.py)
- [Qt 页面导航两种模式](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/src/emo_master/ui/presentation/page_navigation.py)
- [Web 组件与集合表格](https://github.com/jsdfhasuh/emo_master/blob/6cbff0dcbea671663cf9ab773c6ce5c81690da35/web_panel/frontend/src/renderer/PanelRenderer.tsx)
