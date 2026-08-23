# EmoMaster Qt Widgets + QSS 落地规范

> 这份文档把学习包里的 Qt Widgets + QSS 经验收敛成 `emo_master` 当前可直接执行的规则。目标不是复刻旧项目，而是给当前桌面工作台建立稳定、统一、可持续演进的样式基线。

## 1. 目标

- 保持 `Qt Widgets` 工作台结构，而不是转成页面式 Web 风 UI。
- 保持 `QSS` 为主主题入口，不把视觉控制权外包给第三方主题库。
- 优先统一主窗口、导航、参数面板、运行反馈，再做局部业务特化。
- 样式优先表达层级和状态，不优先追求装饰感。

## 2. 当前项目的 UI 事实

- Designer 是一个左中右三栏的桌面工作台。
- 样式入口当前集中在 `src/emo_master/apps/designer/ui/styles/app.qss`。
- 主窗口结构由 `src/emo_master/apps/designer/ui/main_window.py` 装配。
- 启动页由 `src/emo_master/apps/designer/ui/project_entry_dialog.py` 构建。
- 画布与节点视觉主要在 `flow_scene.py`，但大部分 Widgets 风格仍应由 `app.qss` 统一收口。

## 3. 通用设计原则

### 3.1 工具感优先

- 主界面应该像工程工具，而不是营销页。
- 控件边界要清晰，hover、selected、disabled 要能一眼分辨。
- 强调色只用于交互焦点、状态变化、主动作按钮。

### 3.2 三层背景

- 应用背景层：主窗口外层，使用冷灰或浅灰。
- 容器背景层：侧栏、右栏、卡片、面板，接近白色。
- 控件背景层：输入框、列表项、文本编辑器，纯白。

不要在同一屏堆出第四层、第五层背景套娃。

### 3.3 用边框表达层级

- 默认使用 `1px` 边框。
- 常规圆角使用 `6px` 到 `10px`。
- hover 优先改边框和局部背景，不依赖阴影。
- 选中态优先改边框和强调色，不用大面积填色。

### 3.4 同类控件必须完全一致

- 主按钮一个规则。
- 次按钮一个规则。
- 输入框一个规则。
- 卡片容器一个规则。
- 菜单与菜单项一个规则。

不要在单个页面临时发明新风格。

## 4. 推荐主题变量

### 4.1 颜色

```text
appBg = #eef0f3
panelBg = #f7f6f6
surfaceBg = #ffffff

textPrimary = #111111
textSecondary = #514841
textMuted = #aea79f

borderDefault = #b4b4b4
borderSoft = #cfcfcf
borderStrong = #969696

accentPrimary = #f68656
accentHover = #ff963c
accentPressed = #c84614
accentSelection = #ec7440

menuBg = #41403b
menuText = #dfdbd2
menuHoverText = #ffffff
```

### 4.2 圆角与间距

```text
radiusXs = 4px
radiusSm = 6px
radiusMd = 8px
radiusLg = 10px

spaceXs = 4px
spaceSm = 8px
spaceMd = 12px
spaceLg = 16px
```

## 5. Widgets 选型建议

适合继续作为主线使用：

- `QMainWindow`
- `QDialog`
- `QToolBar`
- `QMenuBar` / `QMenu`
- `QListWidget`
- `QTextEdit`
- `QGraphicsView`
- `QSplitter`
- `QTabWidget`
- `QToolBox`
- `QScrollArea`

如果后续需要统计和运行趋势图，可优先考虑 `QtChart`，不要急着引入新的第三方图表框架。

## 6. 第三方 Qt 包取舍

### 6.1 应优先吸收的

- `PyQt5` / `PySide2` 的 `Qt Widgets` 工作台思路
- 项目自定义 `QSS`
- `QtChart`

### 6.2 只作为参考来源的

- `qt_material`
- `QDarkStyle`

这两个库可以借鉴控件覆盖面、色板和资源组织，但不应直接接管当前项目主题。

## 7. Designer 专用约束

### 7.1 主界面结构

Designer 保持以下布局认知：

- 左栏：分类、节点导航、辅助入口
- 中间：流程画布与交互工作区
- 右栏：运行摘要、当前节点、结果预览
- 顶部：菜单栏和工具栏
- 底部：状态反馈

### 7.2 左栏和右栏

- 左右栏应使用统一卡片容器语言。
- 卡片背景用 `panelBg`，不是纯色大块强调底。
- 标题层级靠字号、字重和留白表达，不靠杂色。

### 7.3 启动页

- 启动页可以更轻，但仍应属于同一套工作台风格。
- Hero 区允许轻微渐变，但操作卡片和最近项目卡片必须回到统一容器规则。

### 7.4 菜单栏和工具栏

- 菜单栏属于辅助结构层，使用深色更容易和工作区区分。
- 工具栏保持浅色，承担主操作区角色。
- 菜单栏不能比画布区更抢戏，强调只出现在 hover / selected。

### 7.5 控制流与节点分类

- 分类按钮默认低饱和，不用高彩铺底。
- 当前选中分类再使用强调色或深色激活态。
- 气泡面板分类卡片允许少量语义色，但应统一圆角、边框和间距。

## 8. 当前落地建议

短期内保持单文件 `app.qss` 也可以，但编辑时要遵守下面的逻辑顺序：

1. 全局基线：`QMainWindow`、`QWidget`、文本颜色
2. 结构容器：卡片、侧栏、右栏、启动页区块
3. 导航结构：`QMenuBar`、`QMenu`、分类按钮、列表项
4. 控件：按钮、输入框、文本框、滚动与选择态
5. 业务对象名：启动页、最近项目、算子气泡等

后续如果样式继续增长，再拆成：

- `app.qss`
- `containers.qss`
- `controls.qss`
- `navigation.qss`
- `dialogs.qss`
- `business.qss`

## 9. 不该做的事

- 不要把所有按钮都做成高饱和主按钮。
- 不要在不同页面使用不同的输入框边框语言。
- 不要在 Widgets 界面大量堆阴影和漂浮卡片。
- 不要把对象名样式写成第二套不可维护的主题系统。
- 不要为了接近旧项目，硬拷贝全部旧 QSS 细节。

## 10. 本轮实际执行范围

这轮落地只做两件事：

- 建立这份规范文档，固定设计语言。
- 在 `Designer app.qss` 中把菜单栏、按钮、输入控件、列表容器、左右栏卡片、启动页卡片收敛到工业风基线。

这样能先看到明显结果，又不需要重构 `main_window.py` 或拆分大量样式文件。
