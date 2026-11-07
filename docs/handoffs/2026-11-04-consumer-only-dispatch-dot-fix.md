# consumer-only Dispatch 组身份定点修复

- 执行者：dot。由 dot 编写并提交，不代表其他贡献者代写。
- 基线：`7919f9a5b5335f55f6eeb0fb1e77109653e4f32c`；2026-11-04 核对远端 main 后实施。
- 范围：root 没有捕获、仅 consumer 捕获的正常 Dispatch 组及 Web 实时预览。

## 修改与逻辑

1. `DisplayRpc.ListJobs` 没有 display dispatch 配置时，读取已登记 `JobRecord` 的真实 `runGroupId/sourceJobId/targetSlotId`。组身份不随可选捕获附件缺失或释放而丢失。保留普通 Job 的空组身份，不伪造捕获开启、修订、来源或结果。
2. 编辑器收到 `capture_unavailable` 时，仅移除对应 Job 的结果并取消其 scope 的待准备结果。未捕获 root 的通知不清空消费者，某消费者失去捕获也不清空其兄弟。单任务仍清空自身结果。
3. 不改变启动、停止、设备、采集配额或发布权限，不自动重启任务。

## 实际验证

- 新 Runtime 元数据回归先在旧代码上复现：4 项失败，均为真实组身份变成空字符串。
- Python 3.10.22：元数据、真实 spawn + loopback Dispatch、普通捕获和 Run catalog 共 36 项通过。
- Web Dispatch 合同 7 项通过，包括 root 未捕获时消费者 coverage 的保留。
- 前端 Node 109 项通过，新增 4 项执行实际编辑器消息分支，覆盖普通任务、root/consumer 隔离和准备中的结果。
- TypeScript 检查和 Vite 构建通过；变更 Python 文件 Ruff、目标 Runtime 文件 mypy、protobuf 漂移检查及 `git diff --check` 通过。

## 验证边界

新增 `editor_dispatch_browser.py` 用于构建后的真实浏览器回归。当前云容器 Chromium 无法创建 IPC socket (`Operation not permitted`)，一次提升沙箱权限重试仍失败，因此浏览器脚本未完成，不能记为通过。未执行全仓库 `ci_check.py`、Windows 原生 Qt、人工 UI 或真实设备验收；上述软件模拟不代替这些验收。
