# P1 页面模型与项目编辑 API

本契约是正式代码的模型/状态层，不是 Qt 页面、Runtime 展示通道或发布能力。
Python 3.10 / PySide2 方向不变；以下模型和状态模块均可在不导入 Qt 的进程中测试。

## 版本与保存

- `migrateProjectPayload(payload)`：旧项目严格校验并迁移至 2.1；已有 2.2 原样规范化。
- `migrateProjectPayload(payload, enablePresentation=True)`：显式 1.x → 2.1 → 2.2。
  新增空 `presentation`、`resources`；不生成页面、任务或设备。
- `ProjectDocument`：2.0/2.1 拒绝页面/资源字段（包括显式 null），2.2 必须包含两者。
  未知字段不在迁移时丢弃，输入不变，Loop v1/v2 保持原语义。
- `ProjectEditSession(payload)` / `.load(directory)`：显式进入页面编辑。
  `.workflows` 是同一个 WorkflowStore；流程修改包在 `.transaction()` 内，页面命令共用此事务。
  `.save(directory)` 使用现有原子写入与备份；只有成功后更新 revision/dirty。
  `.undo()` / `.redo()` 默认保留最近 100 个完整事务；保存后撤销不会倒退磁盘修订。
- 普通 Designer 默认项目仍创建 2.1。现有 WorkflowStore 保存 2.2 时保留页面/资源。
  MainWindow 尚未接入新编辑协调器；正式工作区集成在 P4。
- 工作流局部包仍只含工作流及依赖；导入不改变目标页面。旧 `.vxpkg` 发布器对 2.2 明确拒绝，
  P5 完整资源/页面发布校验尚未实现，不能借旧发布入口宣称页面包可现场使用。

## 页面与来源

`core/presentation/models.py` 固定 presentation 1.0：页面顺序/首页、网格布局、组件树、
已知组件类型和版本、属性、绑定与导航动作。全项目 componentId 唯一，最多 128 页、1024 组件、
8 层，网格最多 24 列；非法值、重叠布局、未知字段和循环树拒绝。这是编辑配置边界，
不替换 P0 的 Runtime 资源预算。

`buildOutputCatalog(project, manifests)` 仅接受受信任 PluginManifest 元数据，列出项目内的算子节点实例
和工作流输出，不实例化算子、不运行工作流。简写端口与插件类型必须一致；插件可选/nullable/schemaVersion
信息保留。Blob overlay 提示 drawOverlay 默认关闭，不写回参数。规范字段依据真实 geometry2d 契约。

`DataSource` 地址含 kind/resultScopeId/workflowId/nodeId/port/callPath/fieldPath/expectedType/ruleVersion。
callPath 是从入口出发的有序 `{nodeId, relation}`，relation 为 subflow / loop_body / loop_condition。
重复子流程必须选具体调用节点；循环体来源必须处于对应 iteration scope，禁止把多次迭代当一个单值。
采集计划仅采集当前引用的来源，按完整规范来源值去重；组件重绑替换引用，不原地改共享来源。
API 返回脱离草稿的副本，调用者修改副本不会修改 session。

Blob `items.*.area`、`items.*.centroid.x/y`、Detection `items.*.classId/label/confidence` 是集合投影，
结果仍为 collection，供表格使用；不隐式选择第一项。Point2d x/y 为 number。
通用 json/any/object 不推断字段。integer 可赋给 number，boolean 不能作数值。
平台 status 仅 job_state / connection_state；global_counter 必须由调用方提供 Runtime 已声明名称集合，均只读。

`validateBindings(project, manifests, publish=False, counterNames=...)` 返回带路径/代码的诊断：
未绑定是合法草稿，发布校验拒绝未绑定显示控件；非空悬空来源、类型冲突、调用歧义、导航问题在两种模式均报错。
没有冻结/图像/网络执行；坐标来源证明的运行时验证留给 P2/P3，不承诺未知链路可叠加。

## 页面命令与本机视图状态

`session.presentation` 提供 createPage / renamePage / reorderPages / setDefaultPage / putComponent /
copyPage / referencesTo / deletePage / rebind。每个命令是一个事务，失败回滚。
复制生成新 pageId 和所有新 componentId，自指向导航重映射；原来源引用保持，副本重绑不会连带原页。
删除被引用页面必须明确 repairTo 或取消；详情作用域不兼容的修复拒绝。

`navigate(Action)` 不写 dirty/undo。详情用 `context=displayed_result` 和明确 resultScopeId，
来源页及目标页必须支持该 scope，取 `recordDisplayed` 登记的实际已显示 resultKey；缺失直接拒绝，
不取全局最新值。活动页、displayedResults/frozenResults 是本机状态，不存 project.json。
这些是 UI 适配 API，不是订阅管理器或无限历史缓存。

## 快照、资源及结果 DTO

`freezeProjectSnapshot` 生成 frozen ProjectSnapshot，持有不可变 JSON 字符串；debug 用 draftRevision，
release 要求明确 releaseRevision。它做现有低成本工作流结构校验、绑定、参数和资源校验；
不是执行许可。P2 仍须运行完整工作流编译器/算子语义校验，物化稳定资源快照、检查权限与设备互斥。

- executionRevision：算法图、规范参数、实际插件版本、输入资源摘要；排除页面外观、节点名称和机器展开路径。
- capturePlanRevision：引用的来源、作用域（含显式页面作用域）和规则版本。
- siteBindingRevision：本次显式站点值；秘密只允许 `secret://name@version` 引用。
- 输入根目录必须显式存在；资源清单校验规范相对路径、大小、SHA-256、越界与链接，不回退开发路径。
- 参数顺序：插件默认 → 草稿参数 → 资源 → 站点 → 最终受支持 schema 类型/必填/范围校验。
  不支持的参数校验关键字明确拒绝，不静默略过。算子自身语义校验仍在 P2 编译阶段。
- 参数目标重叠/祖先冲突拒绝；资源与站点用途需应用受信任适配表授权，项目不能自定 Python 解析器。
  内建适配：image_loader.imagePath、image_saver.outputPath、huaray_camera.ipAddress/cameraKey、tcp.client.host。
  未声明用途的自定义文件参数明确不支持自动准备，不扫字符串猜路径。
- output_directory/output_file 只接受相对位置，落在 siteDataRoot 的项目/模式命名空间；
  debug 每个 snapshotId 有独立 SQLite 和输出位置，release 使用稳定项目 release 位置。
  此模块不创建数据库/文件、不改变旧 Job 的状态目录、不创建第二个 Runtime。
- 此时仅校验资源并记录路径；稳定文件复制、读写权限和启动前再次校验属于 P2，不能把校验瞬间
  的文件视为已经物化的不可变资产。

`results.py` 定义 ResultIdentity / FrameProvenance / ImageRef / AssetLease / ClosedSource /
ClosedResult / ResultNotification。检测 resultOrdinal 与 resultKey、messageCursor 分离。
封闭结果逐项覆盖 expectedSourceIds；导出失败为 UNAVAILABLE，执行失败不能 COMPLETE；
可用图片必须已由该 resultKey 接管。资源只按 ID 传递；租约最长 30 秒。
这是正式 wire-neutral DTO 校验，不是 P0 收集器/导出器的整体搬迁，也不是 P2 执行链路。

## 验证

`python scripts/p1_validate.py --suite focused --output <新目录>` 运行纯模型/state 用例；
regression 运行 core/runtime/e2e 及相关 Designer 回归；ci 尝试完整 scripts/ci_check.py。
可加 `--baseline-ref <SHA> --suite ci` 从只读 git archive 副本核查基线。
每次保存 HEAD、dirty、受测源码摘要、原始日志、前后代码一致性和退出码；不覆盖已有输出。
外层 watchdog 默认 300 秒，Windows 使用 kill-on-close Job Object 收尾；不放宽 P0 单任务期限。
