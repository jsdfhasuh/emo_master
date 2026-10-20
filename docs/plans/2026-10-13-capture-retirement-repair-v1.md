# 捕获失败后的原始图像额度归还：局部维修 v1

- 日期：2026-10-13；用户授权：在 Windows 分层审计交付后明确要求“你开始维修吧”。
- 集成起点：`417f8782117ac10a0c3d714f78e4d3021c658bfd`。
- 分支：`agent/runtime-workflow-architecture-v1`；正常提交推送，不合并、发布或部署。
- 优先目标：减少真实源图捕获的无谓占用；不把 GUI latest 契约当作 source incomplete 的免责理由。

## 已确认的缺陷与历史因果边界

旧 PR 合并提交 `b003905d2945a05d621cb8c2bb964be798316d56` 的 run `38356368865`、Linux job `115160103834`，生产 NG 测试 ordinal3 有真实 `UNAVAILABLE/BUDGET_EXCEEDED`、`source image lane is busy`。失败瞬间没有捕获持有者/归还关联，不能倒推此次旧失败必然出自以下路径。

本次源码检查确认：导出在私有快照前失败时，即使 `_reap` 已证明旧编码进程退出，原始额度仍等到 callback 和 replacement `_spawn` 后才归还；未派发的排队超时也等 callback 后归还。这段等待不再保护任何原始图像读者，但会拒绝后继 capture。受控回调/重启门控及真实 spawn 故障测试固定这一缺陷，而不依靠重复正常负载求绿。

## 修改范围

1. 将原始额度归还点收紧到三个明确证据：有效私有快照、已证实编码进程退出、从未派发。回调/文件/slab assignment 所有权仍保留到原收尾。
2. 已归还任务绝不能再次释放后继任务的额度；原生 release 结果未知时不重试，整 slab 保持隔离；reap 失败也不得复用。
3. 仅增加有界关联元数据：producer 最近一次 lane acquisition、失败观察时点、parent pending/active 和真实归还证据。最后一次取得不等于失败瞬间仍持有；快照明确为非原子、非阻塞、只读。
4. 保留原生 500ms deadline、raw8MiB/slab、两 lane、队列和 worker 数、非阻塞 capture、scope 配额及原始不可用结果。超时不能冒充 AVAILABLE。
5. Windows native 专项增加失败归还与已有所有权测试，不替换原五 Session 和全96双消费者测试。
6. 扩大回归发现并复现 Windows 工作区回收长路径缺陷：private preview 写入使用 `_ioPath`，失败/残留目录清理却未使用。仅统一两处清理 IO 路径，保留原 Job 所有权准入和异常处理；两个 >300 字符测试同时检查不删除活跃 Job。

## 回归与退出条件

- 干净归档 `417f878` 加指定候选文件，隔离现有 operator_debug/flow_scene/PLC/工程迁移等未提交工作。
- 先保存修复前7项确定性失败，再验证修复后回调/重启期间恰好一个额度可用、下一 producer 不被旧收尾释放、不能重新分配仍活跃 slab。
- 故障注入覆盖未派发、无效 snapshot、snapshot timeout、reap 未证实、release 结果未知；真实 spawn/reap 与纯受控门控分别记录。
- 回归 NG 同调用图像/计数、双图双 Job、图像逐像素/owner/hash、双消费者全96含预热、五 GUI 生命周期和关闭、原 mailbox/closure/R3观测契约。
- Windows/Linux 精确推送 SHA CI 与 native artifact 分别核对，不借 PR merge 结果替代。
- 首次旧失败、修复前失败、工具/权限错误和修复后异常都保留；不重跑同一 workload 或删除断言凑通过。

## 不在本轮关闭的事项

- 历史失败缺少实际 owner 的部分，除非新证据给出因果，不宣称全部根治。
- 正当容量耗尽、500ms 内未完成导出，仍明确 INCOMPLETE。有限容量、非阻塞采集不承诺任意输入速度下永久全帧归档。
- 旧 Windows GUI60/94/38、旧 Job/启动失败、Debian关闭后ownership缺采样单独保留，不借本轮修复销项。
- 不连接生产设备，不扩为现场吞吐/长期稳定性、D01-D08、SQLite或完整Debian复验。
