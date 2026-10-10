# SQLite 数据库写入算子实施与验收

本任务以用户 2026-10-05 的实施计划为依据。分支为
`agent/runtime-workflow-architecture-v1`，起始 HEAD 为
`c5c61a9a88e7b3d0f098975e90977e2937ca0e72`。原有页面/UI dirty 保留，
仓库外备份和已验证 Git bundle 位于
`D:/jsdfhasuh/documents/emo-master-backups/sqlite-writer-20261005-092443-038925`。

## 范围与固定规则

正式算子 `vision.io.sqlite_writer`：固定可选输入 `enabled:boolean`（默认启用），
固定输出 `receipt:json`。每次调用最多写一条记录；循环每次调用独立写入。
字段映射存原有 `params`，配置版本 `configVersion:1`，项目格式不变。
映射来源为同一流程定义内的完整节点输出、常量或执行上下文；不支持表达式、
JSONPath、索引、跨作用域取内部输出或全局“最新结果”池。

默认写入失败停止任务；明确选择继续才返回失败回执及错误日志。取消必须传播。
普通运行写所选实际业务库；专门草稿调试使用已有 debug 命名空间的隔离库，
按支持的真实结构初始化，不复制业务数据，也不回退正式库。

SQLite 在 Runtime 主机本地磁盘，标准库 sqlite3，原工程目录解析相对路径并在
运行冻结。拒绝内存库、用户 URI、网络共享、内部状态库、缓存和任务临时位置。
普通运行不自动建库、建表或迁移。只读检查不能创建文件；初始化只接受结构化
方案，用户先查看最终路径、列和 SQL 后明确创建；项目撤销不撤销数据库 DDL。

映射支持 INTEGER、REAL、TEXT、BOOLEAN（INTEGER 0/1）、JSON（TEXT）、
UTC_TIME（TEXT）及已持久保存的图片 FILE_REFERENCE（TEXT）。集合存完整正式
JSON，一次调用一行，不展开、不截断，不保存 BLOB、预览缓存或归档图片。
每行缺失策略分为报错、显式 NULL、省略列使用数据库默认值，保留零、False、空串。

按真实元数据检查列名、亲和性、必填/default/主键/生成列，显式列名、参数化 INSERT。
拒绝虚拟表、视图、不支持的亲和性、非有限值、64 位整数溢出、有损转换。
运行前检查全部写库节点，单条事务内再次检查。保留数据库已有约束、触发器和外键。
新增无关的可省略列和列顺序变化不阻止运行；删除映射列或增加必填列报错。

每次独立连接/事务，commit 成功才 COMMITTED；SKIPPED / FAILED / UNKNOWN
分别表示明确禁用、已知失败、无法确认提交。强杀/连接丢失不自动重试或重复 INSERT。
没有队列补写、UPDATE/UPSERT、自动迁移、其它数据库、历史查询页面或现场发布。

## 三批提交

1. A：Qt 无关配置契约、正式编译依赖、Runner 同次投递、复制/导入重绑。
   映射依赖与画布边共同排序及检测环，但不增加用户端口。测试真实 Runner 的
   无边依赖、引用删除、类型、循环与重复调用隔离及旧执行顺序。
2. B：本地 SQLite 后端、typed InspectSqliteTarget / InitializeSqliteTarget RPC、
   受限管理操作、路径冻结、事务回执和 debug 隔离。使用临时项目/数据库验证
   结构变化、默认值、约束、锁竞争、取消、强杀与不确定提交。
3. C：现有双击专用编辑器体系中的目标/映射/规则三块 UI、来源提示及临时依赖线，
   主窗口只连接操作。验证明确初始化、A/B 两次真实运行、回执查询和保存重开，
   提供原生 Qt 截图、复现命令及原始结果。

## 固定预算与测试口径

| 项目 | 边界 |
| --- | --- |
| 每个写库节点 | 64 行映射 |
| 每条序列化记录 | 1 MiB，超限失败，不截断 |
| 元数据检查 | 256 表、每表 256 列 |
| SQLite 锁等待 | 2 秒 |
| 单操作期限 | 5 秒，monotonic，SQLite progress/interrupt 和取消检查 |
| Runtime 管理操作 | 最多 2 个，实际退出才归还额度 |

底层不可中断操作尚未退出时不得宣称回收；Future 超时/取消不是回收证据。
故障测试以受监督子进程和最终 terminate/kill/join 清理保障外层不挂死，
外层 watchdog 与上述单操作期限分开。

证据使用真实 HEAD、完整 dirty 清单、源码 SHA-256、解释器/依赖版本和原始输出，
分列 PASS、FAIL、SKIP、NOT_RUN。第一轮完整基线 CI 为 2737 PASS、1 FAIL、
8 SKIP；FAIL 为窄工具栏 `testNarrowToolbarKeepsRunActionAndHasOverflow`。
既有 A18、1080p/5 Hz 性能 FAIL、资源稳态缺口及 Qt 组合崩溃不改判。
业务库、WAL/SHM 和业务内容不得进入项目包；新测试全部使用临时数据。

每批用 `scripts/sqlite_writer_validate.py` 保留可重复的专项结果。完成本轮后停止，
不扩展现场发布。最终实现契约与结果分别见本轮 SQLite 契约及测试报告。

## 实施记录

A 已提交 `4dbcd1fe8b1b1d01a058b32f16d9dede8f49d89f`，专项 64 PASS；
B 已提交 `00004554759b5e339bd91f4e71100cec2059bf66`，最后复核专项 59 PASS。
C 的正式配置编辑器、来源提示、统一撤销、真实回执查看及 Qt 用户路径已实现；
本报告随第三批提交，不改动先前提交历史。
第三批最终专项 `c-verified` 为 86 PASS，受影响回归 71 PASS，兼容性专项 25 PASS。
排除原 UI dirty 的正式源码独立候选有 74 项 SQLite 专项 PASS，原生 Qt 完整用户路径
1 PASS，未依赖未提交页面编辑器 helper。可见 Windows Qt 四种
QT_SCALE_FACTOR 设置均通过真实 A/B 写入与保存重开。请求的窗口尺寸和实际尺寸分别记录，
不能把 QWidget 截图/Qt 缩放设置当成更换物理显示器、OS DPI 或现场验收。

最终完整 CI `ci-verified` 为 2811 PASS、1 FAIL、8 SKIP、28 subtests；协议生成、Ruff 和
mypy 通过，唯一 FAIL 仍为改动前的窄工具栏测试。首次完整 CI 的 21 FAIL 和两次中断
NOT_RUN 保留，并记录修正具体问题后重跑的结果；不能把本轮功能通过写成完整 CI 通过。

所有初轮失败、Qt 原生异常栈及原基线失败保留。完整 CI 的最终分类、提交/同步状态和
操作入口以 [本轮验证报告](../testing/sqlite-writer-2026-10-05.md) 为准，
本节不宣称完整 CI、既有 Qt 稳定性、性能或现场发布已经通过。
