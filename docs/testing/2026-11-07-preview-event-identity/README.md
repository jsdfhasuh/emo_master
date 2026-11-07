# 预览诊断事件归属补修与 Dot 最小复验交接

执行者：**ChatGPT Web**。日期：2026-11-07（北京时间）。
基线：`9fa84670d4dd12402ea1b5e505d68c2c4aba6efe`。

## 为什么继续补这几处

5328aac8 可见验收中的 Dispatch 停止后事件 422 已由前一提交修正。本次用户要求最终复核、必要补修和 Dot 提示词。在直接事件写入点审查中，另发现两个 `preview.snapshot.failed` 异常入口省略工程及工作流身份，和一个导出退场诊断省略工作流身份。

这些是代码审查及辅助反例确认的问题，**不是声称在该 Dot 证据包中又观察到了这些异常**。预览失败告警本应可被读取，缺工程身份会使后续 Web 严格事件身份校验拒绝同一 Job 的事件页。因此作局部补修，不把故障注入扩大为新的人工验收门槛。

## 改动

- `RuntimeService._inspectionFailed()`：从原 JobRecord 写入 projectId/workflowId，保持原 WARN、E_INSPECTION_TAKEOVER 和消息。
- `_onJobTerminal()` 的 preview promote 异常分支：同样使用原 Job 身份，保持 E_PREVIEW_SNAPSHOT_FAILED，不改变终态或异常捕获。
- `PresentationService._exportRetired()`：在已有 projectId 基础上补 workflowId，保留 invocation、payload、退休/隔离及资源释放语义。此处原 projectId 已正确，不是此前 422 的来源。
- 不从当前 loadedDocument 推断历史 Job 归属。正常生命周期先创建 JobRecord，之后才建立接管回调，记录不随资源退出删除。没有记录的旧内部诊断调用仍保留原空身份行为，避免覆盖原错误或伪造当前工程身份。
- Web guard、EventStore、游标、超时、设备串行访问、取消不补执行和未知副作用不重放保持原样。历史数据库不回填。

## 直接反例与本地验证

新增参数化回调测试分别触发 inspection 接管和 completed preview promotion 的单一已捕获错误；使用真实 RuntimeService、JobManager/Repository、EventStore、SQLite。当前加载工程刻意与原 Job 不同，验证不会借用当前工程归属。既有两个导出退场反例加强内存、SQLite、JSONL 的 workflow 断言，保留原资源证据。

- 修复前：4 个参数样本均失败于缺失身份字段；没有通过改夹具或删断言绕过。
- 修复后：上述反例连同直接相邻 Runtime/gRPC、inspection、终态资源和 Dispatch 停止重启测试，共 **41 通过、0 跳过**。
- Web 契约、HTTP、明确启用 `EMO_WEB_PRODUCT_RUNTIME=1` 的实际软件 Runtime 文件：**35 通过、0 跳过，另 6 个子测试通过**。其中新回调测试是辅助反例，不能称作人工或真实硬件验收。
- Ruff 通过；mypy 614 个源文件通过，现有 untyped-body 提示保留；diff whitespace 通过。
- Web 保留 1 条现有 Starlette TestClient/httpx 弃用提示；不为消警告扩改依赖。

补修后审查 Runtime 内 25 处直接 `eventStore.append` 调用：剩余两处未显式同时列出 projectId/workflowId 的写法分别是 Runtime 全局 `__runtime__` 事件，以及通过 `_workerEventFields` 展开身份字段的正常 worker 事件。后者已有原 Job 的身份 fallback。此审查是指定直接调用点检查，不宣称静态扫描覆盖所有动态路径。

## 前一提交远端 CI 已完成

关联 head 为 9fa84670，Actions 实际 checkout 是 PR 合并候选 `c9f83fb8b12a09da5bfa4ba459d996d39331e44e`（9fa 合入 main eea6997）。本段只属于前一提交，不算本补修的远端结果。

- Linux 全仓：9239 通过、31 跳过、42 个子测试通过。
- Windows 全仓：9265 通过、5 跳过、42 个子测试通过。
- 两边 Runtime 恢复专项各 57 通过；具名跳过集合与 5328 相同。
- 两平台 Web 后端各 228 项中 7 项跳过；前端各 194 通过。所有列出的浏览器步骤成功。
- Windows owning Runtime 独立文件 5 项通过（3 个实际软件 Runtime 集成和 2 个辅助测试），浏览器两 Job/同 Runtime 重启/自动准入和实际图像检查通过。
- 隔离 Web 的 4 条 owning Runtime opt-in 跳过中，独立 Job 补跑其中 3 条；不能声称全部跳过均已补跑。

证据：[完整 CI](https://github.com/jsdfhasuh/emo_master/actions/runs/42364381601)；[Web CI](https://github.com/jsdfhasuh/emo_master/actions/runs/42364381518)。本补修推送后的 SHA 与新 CI 状态记录于 PR 的 ChatGPT Web 评论，不借用本段结果。

## Dot 最小可见复验

固定最终评论中的完整 SHA，确认 Runtime/Web 实际进程来自该版本。沿用合格工程与已授权的本机免密入口，使用真实软件 Runtime、模拟相机及新运行组。

正常出图后选中 root，展开“权威计数与任务事件”；正常停止后继续查看这个已停止 root。确认事件游标真正读到了两条 Dispatch 退场事件，再保留至少三次成功轮询；后续空页游标保持不变是正常情况。不能只看还未读到尾页的前三页 HTTP 200 就判断通过。

在同一 Runtime 中再启动新组，核对消费者图像和数值归属、持续更新，正常停止收尾。通过 UI 导出一次本轮 root 日志，对照两条退场事件身份即可；不用再导出整库历史或重做 5/5 启停和 15 分钟全流程。

首次异常保留现场、请求状态/游标/脱敏响应及 Runtime 日志，不用刷新后成功覆盖失败。原有 5328 全流程通过范围保留并标记原 SHA。历史 Error 9、Windows 首次空图、长停顿及未测平台/硬件单列，不因此宣布唯一根因已闭环，也不无条件扩成本轮门槛。

本轮没有合并、发布 Release、部署现场或进行新补丁可见人工验收。
