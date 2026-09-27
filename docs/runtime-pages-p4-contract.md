# P4 Designer 页面编辑最小闭环

本轮是正式模块的最小子集，不是 P4 全能力或现场版本。Python 3.10 / PySide2 不变。
保留 P0/P2/P3 原性能 FAIL、长期资源稳态缺口及 Qt 组合访问冲突；允许编辑开发不等于验收通过。

## 入口与操作

仓库根目录，使用项目 Python 环境：

```powershell
python scripts/p4_demo.py
```

打开的是现有 MainWindow，使用临时 Runtime 数据目录及一个内置 ImageLoader → Blob → Count
旧格式流程，页面初始为空。目录随演示退出清理；需要保留成果请使用主菜单另存项目目录。
也可 `python scripts/p4_demo.py --project D:/path/to/project` 打开持久化本地工程。
常规源码入口用 `$env:EMO_PAGE_DESIGNER="1"; python scripts/dev.py run-designer`；不开开关时入口与设备行为保持原状。

1. 在主工具栏点击“页面设计”，确认采用 2.2。原 project.json 在保存时备份为 .bak。
2. “新建页面”，从组件库拖到中央网格。网格底部预留一个空白拖入行；控件按网格放置，重叠拒绝。
3. 选择控件，在右侧调整行/列/跨度、页面或容器列数及属性，点“应用属性 / 布局”。
4. 从流程数据树拖端口到控件并确认，或在右侧选择输出并“绑定所选输出”。树按工作流调用位置、节点、端口组织。
   示例数值绑定 Count.count，图像绑定 Blob.overlay（流程参数 drawOverlay 必须已开启）。
5. 新建或复制“检测详情”页，为导航按钮选择稳定页 ID；副本重绑不影响原页。
6. “切换为模拟预览”只用于布局，没有伪造业务结果。预览态才允许导航；编辑态点击用于选择。
7. 先保存项目，“登记本地输入图片”选择具体 ImageLoader 及本地图片。输入复制到声明资源目录。
8. 点击“明确开始本地草稿调试”。等待真实结果，可切页和切工作区；新绑定在下一次明确调试生效。
9. “停止自有调试 / 断开观察”只收尾自己创建的调试或自己的只读会话。另存、关闭重开后配置保留。

## 状态与命令

正式模块 `apps/designer/page_designer`：coordinator（项目边界）、editing（无 Qt 命令/目录）、
workspace/tools（原生 UI）、resources（明确资源登记）、preview（异步会话所有权）。
core/client 保持无 Qt；正式模块不 import prototypes，演示/验证脚本复用 examples 辅助输入。

`ProjectEditSession(..., enablePresentation=False, workflows=existingStore)` 允许工作区先保持 2.1；
`enablePresentation()` 才显式迁移。页面事务与流程完整命令/画布拖动完成检查点共用最近100项历史。
选中、导航、实时值不进入 history/dirty。ProjectController 接入唯一加载/保存/另存边界；失败不清 dirty。
另存仅复制已声明输入，校验大小/摘要/根目录，目标冲突拒绝，不复制数据库、输出或整个目录树。
2.2 草稿打开不调用旧 Runtime.LoadProject 去编译未物化资源；明确调试使用 P2 Prepare/Start。
页面开关内读取受信任 manifest 补全省略的端口元数据；加载保留草稿边，不因未解析端口静默删连接。

`PageCommands` 的 add/move/update/delete/copy/bind 都是单一 session 事务；失败恢复整个项目。
来源含明确 callPath，重复调用分别列出，不猜“最后一次”；当前 P2/P3 限单一调用作用域。
绑定使用不可变来源，副本单独引用新来源；结构/类型检查不把 boolean 当数值或把集合第一项当标量。
源节点删除产生带路径错误，修复或明确清除绑定后才可调试；可选 overlay 提示不修改算法参数。

## 渲染与生命周期

`RuntimePages.reload(presentation)` 只替换页面配置/控件，保留 hub/session；先清图片/表格引用、隐藏旧控件，
再 deleteLater，防止延迟销毁期间遮挡新页面。配置更新释放冻结上下文；仍按既定图像和窗口预算计账。
编辑模式屏蔽组件运行动作；预览复用同一 P3 渲染器，无第二套预览引擎。
一个 PreviewController 最多一个后台操作、一个 DisplaySession、一个内嵌观察窗口。
GUI 定时拉取有界完成队列；启动/准备、读图/解码、关闭收尾不在 GUI 执行。

本地调试仅允许已验证内置版本 ImageLoader 1.1.0、Blob 1.2.0、Count 1.1.0、ImageSaver 1.0.0；
ImageSaver 仍需已有明确隔离输出声明，编辑器不猜输出路径。未知插件/设备算子拒绝。
只在已有内嵌 Runtime 上创建 PresentationService 和 loopback aio 服务，没有第二个设备 Runtime。
输入、DB、输出由 P2 在临时根物化；当前草稿在点击启动时取得独立文档。
Prepare/Start 使用正式 typed gRPC；展示使用正式 DisplaySession/DisplayHub，沿用导出/读图500ms及全部既定额度。
纯布局更新不改采集计划；来源摘要不匹配明确显示需新任务，不热更新旧 Job。

只读连接要求明确 loopback 地址和 Job ID，不 Prepare/Start/Stop/ReleaseJob。
退出先异步关闭自身订阅；仅本地调试拥有者停止自己创建的 Job，外部 Runtime 生命周期不属于页面。
项目切换若正在启动/观察，会先异步收尾并提示完成后重新选择项目；不会让旧完成回调接入新项目。
关闭期间新增修改仍重新检查 dirty。退出失败保留资源所有者与错误，不伪报已关闭。

## 编辑支持表

| 能力 | 本轮范围 |
|---|---|
| 页面 | 新建/复制/重命名/排序/首页/删除与明确引用修复 |
| 图像、数值、文字 | 网格/跨度、标题/文字/空值文字/单位/小数，正式来源绑定 |
| 容器 | 嵌套网格、跨容器移动；重叠、树循环回滚 |
| 判定 | 显式 JSON 布尔/字符串键→文字及固定颜色；不在页面计算合格逻辑 |
| 表格 | 集合/正式字段投影，列路径与分页大小；没有默认第一项选择 |
| 导航 | 创建稳定 ID 实时导航；已存在详情/冻结动作保留，预览由 P3 执行 |
| 状态 | 客户端连接状态；业务 global_counter/runtime_status 绑定不支持 |
| 预览 | 一个内嵌共享观察者；浮动编辑预览本轮未提供（P3独立双窗能力保留） |
| 执行 | 已验证本地图像子集、单调用作用域、跨页复用一个图像源 |

不包含独立几何叠加、混合作用域、设备调试许可、完整动作编辑器、发布打包或 P5。
两页功能验证与短时布局更新测量不替代原1080p/5Hz、5页50控件、长期稳态或工控机验收。
