# SQLite 写入正式契约

`vision.io.sqlite_writer` 是显式业务写入节点，使用 Python 3.10 标准库 sqlite3。
它不使用 Runtime 内部状态表，不依赖 Designer 快照或全局最新结果缓存。
默认设备执行入口和已有无数据库工作流保持原行为；旧执行器缺少写入能力时明确报错。

## 配置与数据依赖

配置保存于现有节点 `params`，项目格式没有升级：

```json
{
  "configVersion": 1,
  "databasePath": "business.sqlite3",
  "table": "records",
  "failurePolicy": "stop",
  "mappings": [
    {"column": "count", "storageType": "INTEGER", "missing": "error",
     "source": {"kind": "node_output", "nodeId": "count-stable-id", "port": "count"}},
    {"column": "job", "storageType": "TEXT", "missing": "error",
     "source": {"kind": "context", "key": "jobId"}}
  ]
}
```

来源 `constant` 携带完整 `value`；上下文支持 jobId、projectId、workflowId、
workflowRunId、nodeId、nodeRunId、iterationPath、timestampUtc、writeId。
来源节点必须在同一流程定义；调用节点的正式输出可以绑定，其内部结果不能跨作用域取值。
绑定不改变画布端口：只有可选 `enabled:boolean` 和必需 `receipt:json`。
没有连接 enabled 时启用，False 返回 SKIPPED。

编译后 `parameterBindings` 与连线共同排序/查环，按稳定节点 ID 和端口检查类型。
Runner 每个流程调用创建独立投递表，只转换已声明输出，记录来源 workflowRunId/nodeRunId。
结构化结果通过正式 toPayload 和完整 JSON 转换，拒绝 ndarray/任意对象、非有限值和截断。
复制参数深拷贝；流程导入通过 nodeIdMap 重绑。名称变化不影响 ID，来源删除保留失效引用供修复。

INTEGER 使用 SQLite 有符号 64 位；REAL 必须有限且整数转实数无损；BOOLEAN 按 0/1，
TEXT 不自动把数值转文字，UTC_TIME 需要带时区 ISO 8601 并归一 UTC，JSON 存 TEXT。
FILE_REFERENCE 绑定只支持 Image Saver 的正式持久保存结果，保存其完整 path，
不复制或归档图片，不接受预览/任务临时图片，不承诺文件永久存在。

`missing:error/null/default` 分别报错、显式 NULL、从 INSERT 省略列。
已产出的 None 是真实空值，不能因配置 default 而替换为数据库默认值；0、False、空串保留。

## 目标、管理与初始化

文件位于 Runtime 主机本地盘。相对路径由原工程目录解析并在执行快照中冻结，
不以 Job cwd 或 prepared 目录解析。拒绝用户 SQLite URI、内存库、UNC/映射网络盘、
内部库及其文件别名、预览缓存、任务临时目录。

正式 typed RPC `InspectSqliteTarget` / `InitializeSqliteTarget` 接受路径、工程目录、
表名及结构化 `SqliteColumnPlan`，返回 Runtime 主机、最终路径、表/列元数据和建表预览。
没有任意 SQL 字段。客户端旧 Runtime UNIMPLEMENTED 明确转为 E_SQLITE_UNSUPPORTED。
检查只读；文件不存在不创建。明确 confirmed 初始化才建库/表，不隐式创建父目录。
新表固定 `id INTEGER PRIMARY KEY AUTOINCREMENT`、`write_id TEXT NOT NULL UNIQUE`，
自定义列支持上述存储类型和常量默认值。已有目标表重新核对，差异报错，不自动迁移。
初始化重入目前仅接受本接口生成的同一 CREATE TABLE 文本；等价但格式不同的手写 DDL
会被明确拒绝。选择已有普通表进行只读检查和写入不要求该文本一致。
数据库外部 DDL 不受项目撤销控制。

SQLite 管理专用后台执行器最多 2 个已接纳操作，不增加待执行队列；aio 独立 sqlite 类别额度 2。
实际工作结束才释放额度，RPC 取消/超时不提前释放；关闭仍有实际工作时明确未完成，允许重试收尾。
普通控制/长期订阅沿用已有分类额度。无写库节点的普通 StartJob 不占 SQLite 管理额度。

## 写入与结果

运行前检查所有写库节点（包含其引用流程），每次 INSERT 使用独立连接和
BEGIN IMMEDIATE 单条事务，在同一事务内再次读取真实 schema，按列名参数化 INSERT。
仅 INTEGER/REAL/TEXT 亲和性支持，生成列不可显式写，未映射必填且无默认/生成列报错。
允许新增无关可省略列和改变列顺序；映射列删除或新增必填列拒绝。
启用 foreign_keys，保留 CHECK/UNIQUE、已有触发器和外键语义。普通运行没有 DDL、UPSERT、
异步写入队列、自动重试、断线补发或重复 INSERT。

receipt 包含 writeId、status、rowsAffected、可取得的自动整数主键、执行身份、elapsedMs、error。
COMMITTED 只在 INSERT 实际影响恰好一行且 commit 返回成功后报告；
触发器 RAISE(IGNORE) 或 ON CONFLICT IGNORE 导致零行时返回 E_SQLITE_NO_INSERT，
回滚该事务（包括触发器副作用），按 stop/continue 策略返回 FAILED，不自动重试。
SKIPPED 为明确禁用；FAILED 为已知失败；
UNKNOWN 为无法确认提交，rowsAffected 为 null，不能等同回滚。
回执丢失时数据库中可能已经存在记录；唯一 write_id 可用于人工核对，新调用不会自动复用/重试。
强杀时未结束的写入由 Runtime 在 Job 终态事件之前补 UNKNOWN 节点诊断。
continue 正常返回 FAILED/UNKNOWN 回执且写 ERROR 日志，与“节点执行完成”分开；取消传播到任务终态。

`write_id` 是保留的本次写入身份；不映射时后端补入。显式映射只允许上下文
`writeId`、TEXT、缺失报错；事务内再次拒绝与 receipt.writeId 不一致的值。
原生节点结果面板按 Job/workflowRun/nodeRun 身份保留有界真实回执，并在原来的
每节点 8 KiB/64 节点额度内显示提交状态、主键及错误，不用 JSON 字段数量代替回执。
新增执行失败或下一次调用不会沿用旧 COMMITTED。
已返回真实回执之后发生取消或结果收尾失败，node.failed 仍携带同一 Job/workflowRun/nodeRun
的已确认回执；节点/任务取消状态与数据库提交状态分别呈现，不将已提交记录说成已回滚。

## 原生配置与项目编辑

从现有算子库拖入“SQLite 数据库写入”，双击打开专用窗口。目标/字段映射/规则
共用正式 params 和 ProjectEditSession。来源目录使用 compiler 的正式端口规则，
涵盖同流程内的边界、算子、子流程及循环调用输出；不运行、不推断草稿最新值。
增减/排序、缺失处理和类型错误定位到映射行。一次应用是一次项目撤销；取消不修改
项目。保存、切换项目和关闭检查未应用配置；普通算子复制保留外部来源，工作流复制
和导入通过现有 nodeIdMap 重绑，副本参数深拷贝。

选中写库节点时目标摘要、配置错误、来源高亮和临时依赖线仅用于编辑视图，
不进入 edges 或项目格式。清空画布先阻断同步 selectionChanged，清空 Qt 包装对象和
缓存后再通知空选择，停止待路由刷新。

管理 RPC 在共享 GUI 后台执行器（额度 2）处理，GUI 定时领取已结束结果；取消和关闭
只发取消信号，不提前归还实际工作额度。来源、建表预览和初始化确认使用信号驱动的
原生 QDialog，代际/对象有效性检查拒绝迟到回调；没有嵌套弹窗等待悬挂在已关闭编辑器
上。初始化确认列出主机、最终路径和完整 SQL，默认取消。数据库外部操作仍不可撤销。
内容区可滚动，应用/关闭按钮在内容区之外，长路径可换行并选择复制。

## 草稿调试与清理

正式普通运行使用选择的实际数据库；专门 prepare(debug) 使用
`<projectKey>/debug/<snapshotId>/business-sqlite/`，复制支持的 schema，不复制正式数据。
外键、触发器、独立索引、生成列需要明确指定独立 `debugDatabasePath` 专用测试库；
其测试 schema/种子数据复制到拥有的 debug 命名空间执行，源测试库只读。
拒绝正式库及其文件别名充当测试模板。没有测试库时明确失败，绝不回退正式库。
准备失败、准备记录退出和 Runtime 关闭按现有命名空间所有权清理新增隔离资源。

每节点最多 64 映射、序列化单条记录最多 1 MiB，超限失败不截断。
最终记录额度包含后端补入的 write_id；INTEGER PRIMARY KEY DESC 不视为自动 rowid 主键。
管理元数据限 256 表/256 列。锁等待 2 秒，单操作 5 秒 monotonic 期限，
SQLite progress/interrupt 配合取消检查；监控线程先退出、连接关闭后才完成返回。
不可中断的底层 I/O 尚未退出时不声称回收。强杀测试的外层 watchdog 为 25 秒，
独立于 5 秒单任务期限，最终 terminate/kill/join 回收所有测试子进程。

业务库、常见 SQLite 扩展及配置所选文件（含无扩展名）和 WAL/SHM/journal 从旧项目包排除。
P5-A 测试交付算子 allowlist 没有扩大到 SQLite，页面项目含此算子时明确提示不支持，
不静默丢弃配置。没有数据库迁移、BLOB、长期查询页面、跨机/EXE 或现场发布验收。

基线和本轮结果见 [验证报告](testing/sqlite-writer-2026-10-05.md)。
