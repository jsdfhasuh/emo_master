# 计划复核记录

复核基准：OpenSpec 提交 4cd21655a413c370af8d9f3379a5d24fe49d1f6c；源码基准 main b75df0adb7a90a4e0b540ff1c8c09e9339cfcdbd。

## 复核结论

原计划的主方向正确：manifest 声明能力、PluginManifest 保存声明、OperatorExecutor 按声明注入 Job-scoped 后台任务客户端，不改算子基类或 executeNode 签名。

但原计划还有两处关键链路没有闭合，已在本次修订中补充：

1. **SQLite FILE_REFERENCE 提前校验。** Runner 在生产节点之后立即调用 captureBoundValue；该函数马上调用 SQLite toStorage，要求引用已经 saved=true 且文件存在。因此只在 SQLiteWriter 算子内部 wait 不可能生效。修订为 Runner 暂存 pending binding，在 SQLiteWriter 节点执行前 wait，成功后再调用原 captureBoundValue；失败生成 BoundValue 错误，不执行无效引用写入。
2. **ArtifactStore 和预览 writer 的线程/关闭顺序。** 原 Runner 会立即 registerPath、复制并计算 SHA-256；worker_main 的 terminal helper 当前先关闭 AsyncPreviewSnapshotWriter。修订为服务只产出完成记录，Runner 在自身执行线程 drain 并登记 artifact；Job terminal 顺序先关闭/排空后台服务、处理完成记录和产物，再关闭预览 writer，最后发布 terminal。

另补充了连续 Job 的任务状态记录上限、调试 class-only registry 的能力传递，以及现有 5000ms graceful-stop 强制终止边界，避免把线程收尾描述成无限等待。

## 仍需实施验证

- 生产 Job 的多算子共享、异步重叠和 Job 隔离。
- SQLite FILE_REFERENCE 延迟绑定的成功/失败路径。
- artifact.created、ArtifactStore 复制/校验、preview capture 与 job terminal 的严格顺序。
- 调试模式的能力传递与 drain。
- 任务记录达到上限时的显式过期行为。
- graceful stop 超时后不伪造保存成功。

本复核仅修订计划文档；没有修改 Runtime 源码，没有运行 OpenSpec CLI、pytest、GUI 或真实设备验收。
