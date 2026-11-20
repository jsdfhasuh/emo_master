# 高级外观样式示例

以下规则已写入高级外观示例工程及其 Web 包。回放参数见 [目标效果](target-effects.md)，界面布局参考 v0.6 图稿。[示例工程与页面包](../../../../examples/advanced_appearance/README.md)

## 文件与对应效果

| 文件 | 当前对象与图稿 | 配套常用字段 |
| --- | --- | --- |
| [direction.qss](direction.qss)、[direction.css](direction.css) | 方向判定；第 08／13 页，左边框 4 px，主值加粗 | 符合状态背景 `#F0F9F5`，文字 `#11734C`，边框 `#72B39C`，基础边框 1 px，圆角 8 px |
| [start_button.qss](start_button.qss)、[start_button.css](start_button.css) | 页头开始按钮；第 06／11 页，正常／悬停／按下／禁用外观 | 图标使用已有开始图标；图标 12 px，图文间距 6 px，位置由常用表单控制 |
| [navigation_button.qss](navigation_button.qss)、[navigation_button.css](navigation_button.css) | “查看历史记录”导航按钮的悬停颜色与左边框 | 保留原导航目标及文字，图标 12 px 在右侧，间距 6 px |
| [stop_button.qss](stop_button.qss)、[stop_button.css](stop_button.css) | 停止按钮独立四态；原运行图展示禁用态 | v0.7 补齐可用态参数，不改变宿主可用条件 |
| [number.qss](number.qss)、[number.css](number.css) | 角度数值；实际主值加粗 | 底色、边框与主值颜色通过常用表单，数据与单位沿原绑定 |

QSS 放入 Designer／Qt 当前对象代码区，CSS 放入 Web 当前对象代码区，分别保存和应用。系统负责为规则绑定当前组件／宿主按钮的内部所有者；用户不需要在代码中写运行时随机标识。

## 从同一示例提取其他规则

- 文字、运行状态、SQLite 数值和时钟可使用 `*[emoPart="value"]` 或 `[data-emo-part="value"]` 设置实际内容。独立的指示圆点使用 `marker`，页面标题使用 `title`。
- 集合表格及 SQLite 表格的 Qt 表头用 `QHeaderView[emoPart="header"]::section`，Web 用 `[data-emo-part="header"] th`。Web 单元格使用 `cell`。
- SQLite 列表在 Qt 用 `QTableView[emoPart="table"]::item:selected` 修改选中项背景，在表格控件上设置 `selection-color` 可修改选中文字。Web 选中行使用 `[data-emo-part="item"][aria-pressed="true"]`。
- Qt 页面导航使用 `QTabBar[emoPart="tab"]::tab:selected` 或 `QPushButton[emoPart="action"]:checked`。Web 在当前页的 `tab` 或 `action` 上可用 `[aria-current="page"]`。
- 图片视口使用 `viewport`，容器自身外框使用 `root`。每个对象的选择器可从高级外观窗口查询。

示例生成脚本列出了每个对象的通用颜色、边框及对应两端规则。“符合”“复核样本”和“需复核”配色跟随原判定值。关闭本端规则可观察当前通用外观和状态外观。[示例生成脚本](../../../../examples/advanced_appearance/build_example.py)
