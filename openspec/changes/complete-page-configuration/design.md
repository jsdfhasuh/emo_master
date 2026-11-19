# 设计

## 1. 配置与动作分离

现有宿主继续决定实际启动/停止对象、权限、可用状态和确认流程。`startControl` / `stopControl` 只改变显示文字与样式，不保存执行表达式、不创建另一套 action。

`label` 省略时用宿主当前默认。作者清空文字即移除覆盖。多流程控制也用对应配置，具体对象仍由原目标选择和提示说明。

`ControlAppearance` 采用现有主题语义：primary/secondary/link、文字角色、圆角、四边内距、最小宽高、动作图标。Qt 生成局部 QSS，Web 生成 CSS。正常、悬停、禁用状态继续由真实控件状态派生；样式不改 enabled。作者预览不执行任务。

## 2. 页头

`logoWidth` / `logoHeight` 是等比包含的逻辑像素范围，默认 40×40，宽 1–512、高 1–256。logo 从受管原图解码后缩放，避免先截成 32×32 小图；原按钮图标行为独立保留。

`headerSurface` 复用 Surface；`headerTypography.title` 用于标题，`helper` 用于副标题。页面内容仍用既有 appearance 和 responsive 布局。没有新的自由定位布局。

## 3. 状态行

`titleFollowsState=false` 保持静态标题字体色；开启后使用本次 indicatorStates 命中的语义颜色。`markerPosition` 取 `value` 或 `title`，与现有 `showMarker` 组合。标题不存在时圆点回到状态值前。

标题、圆点、值和整行底色来自同一次状态映射。空值、未映射、来源不可用时恢复中性标题并移除活动圆点，避免保留上一次结果颜色。没有新增头到/尾到/NG 专用控件。

## 4. 历史列表

`HistoryView.showFilter/showSort/showPagination` 默认均为 true，独立控制 UI。

隐藏排序控件仍按 source 中的 orderBy/descending 查询；Qt 表格保留列标题，但关闭列头点击排序和排序箭头。隐藏分页栏时外部 `history_previous/history_next/history_first` 继续工作，上一/下一条可以跨数据页，回到最新仍重新查询首页。隐藏分页栏也隐藏该栏中的刷新按钮。

切换 table/list 不删除显隐配置或已选显示字段。查询身份、选中记录、图片和详情联动继续复用原机制。

## 5. 两点构线

新算子输入 `startPoints`、`endPoints`（各为已选的 `list<point2d>`）和本次 `frame`，输出既有 `lineCollection` 1.2 和 `lineCount`。两端各一个点时构线；任一列表为空时输出本次 frame 的空线集合。多点不隐式取第一项，跨坐标空间沿用已有显式转换。

上游仍负责目标筛选和中心点提取。算子不识别业务类别；Annotate 保持只渲染。示例的连线改为本次所选两个模拟目标中心，不再使用单独静态边缘图拟合线。原 detection_bbox 对空检测的既有错误语义不在此更改，业务缺目标分支仍须明确处理。

## 6. 交付与验证范围

字段从源模型生成官方 JSON Schema、TypeScript、独立 SDK wheel，并更新消费者依赖。主工程版本不做无关升级，也不加入旧 SDK 兼容分支。

验收关注作者修改/重开、Qt/Web 状态渲染、隐藏工具栏后的跨页回看、两点到实际虚线图、工程保存/编译/发布，以及可见页头布局。复用相关测试；不增加硬件故障、极端并发或所有样式排列组合的门槛。
