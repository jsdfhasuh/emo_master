# 计划复核记录

源码基准：`main @ b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd`。本文件记录静态源码审查，不代表源码实现或测试已经完成。

## 已确认的必要设计

1. manifest 声明能力，PluginManifest 保存，OperatorExecutor 按声明注入；不改 OperatorMeta、基类或 executeNode 签名。
2. 每个 Job 进程拥有一条后台 worker；不同 Job 不跨进程共享线程。
3. submit 与真正保存成功分开；ImageSaver 默认同步，后台模式返回 taskId 和 SUBMITTED。
4. ImageSaver 后台模式使用 unique 命名、任务专属临时文件和原子发布，防止同名覆盖与半成品文件。
5. Runner 必须在文件成功后登记 artifact；SQLite FILE_REFERENCE 必须延迟到 SQLiteWriter 执行前等待并采集。
6. 后台完成记录由 Runtime 执行线程处理；Job terminal 前先收尾后台任务和产物，再关闭预览 writer。
7. 连续运行和 Dispatch IDLE 期间也要轮询未观察失败，避免后台失败长期无人发现。

## 过度设计审查

| 原计划内容 | 审查结论 | 调整 |
|---|---|---|
| 每 Job 一条 FIFO worker | 必要 | 保留 |
| manifest 能力声明 | 必要 | 保留 |
| 任务数量 + 字节双重限额 | 图片任务需要 | 保留，但只做内部固定上限，不增加用户配置 |
| 任务终态记录上限 | 长时间连续 Job 需要 | 保留最近有限记录；pending 依赖固定保留 |
| TaskControl 最终发布门禁 | 解决 close 超时后的迟到文件发布 | 保留最小 API，只提供取消检查和短小原子发布 |
| 独立 30 秒正常 drain 上限 | 需要防止正常收尾永久卡住 | 保留为服务内部常量，不增加 JobProcessSpec 或工程配置 |
| 复杂失败历史/计数系统 | 不需要 | 简化为首个未观察失败；Runner 及时 poll 并终止当前执行 |
| 任务递归 submit/wait 的运行时防御 | 不值得增加单独框架 | 仅作为任务函数开发约束，不增加递归检测机制或专项测试 |
| 单独覆盖每一种取消时序的测试 | 重复价值低 | 合并为服务生命周期测试 |
| 逐项测试每个 manifest 非法组合 | 重复价值低 | 用参数化测试一次覆盖 |
| 独立性能基准、压力/soak、全算子遍历 | 超出首版目标 | 删除，不作为验收门槛 |

## 重要修正

- 后台保存首版必须使用 `namingMode=unique`；用户已确认接受。同步模式继续支持 fixed/unique。
- TaskControl 的接口定义只保留一处：任务函数签名为 `callable(taskControl, *args)`；`publish(finalize)` 只包住临时文件到最终路径的原子提交，不能执行耗时 I/O。
- 30 秒 drain 超时是服务内部常量，不新增 JobProcessSpec 字段；用户 Stop 继续沿用现有 5000ms Supervisor 规则。
- 不建立独立的失败历史/计数服务；首个未观察失败被 Runner 发现后立即终止当前 Job。
- 任务记录只在仍被 Runner pending artifact/SQLite binding 引用时固定保留，依赖释放后进入普通有限保留。
- Runner 对 SQLite pending binding 的处理顺序为：wait → 处理任务完成记录/登记 artifact → captureBoundValue → SQLiteWriter。

## 精简后的测试范围

只保留 5 组直接相关验证：注册与注入、后台服务核心行为、Job/Runner 生命周期、ImageSaver/artifact/SQLite 文件链路、调试/预览隔离。采用 Event 和临时目录做确定性测试，不新增全算子遍历、长时间压力/soak、现场设备或独立性能基准。

## 仍需实施验证

- OpenSpec strict validation。
- 受影响的 manifest、后台服务、Runner、ImageSaver、SQLite binding 和调试测试。
- Windows/Linux Job worker 集成测试。
- 真实 Designer/现场图片保存体验（如需宣称生产验收）。

本次仅修改计划文档；未修改 Runtime 源码，未运行测试。
