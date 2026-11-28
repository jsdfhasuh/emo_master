# Codex 实施提示词：Operator Runtime R3 收尾

请在 `jsdfhasuh/emo_master` 仓库实施本目录
`runtime-operator-r3-closeout` OpenSpec Change。

## 固定起点

-   基础分支：`codex/runtime-overall-r3`
-   已知基线提交：`1f35861a22f4e5bd553fe210db989e28c3c4a0d1`
-   新分支建议：`codex/operator-r3-closeout`
-   不修改 `main`，不 amend/force-push R3，不重写 R3 归档证据。

实施前先 `git status`、核对远端分支 HEAD 和当前差异。如果 R3
分支已有后续提交，以最新已确认的分支 HEAD 为准，并在交付记录中说明。

## 必须完成

1.  日志默认来源为「运行任务」；`resetFilter()` 回到 Job
    来源；`setDevice()` 保持显式进入本机 JSONL。
2.  Job reader 只在日志工具可见且 Job
    来源选中时活动。隐藏/切换来源时请求停止，drain
    已返回事件/progress，保存 Runtime instance/cursor；reader
    结束前不启动第二个 reader；返回后从 cursor 恢复。
3.  保留导出 worker 生命周期，不因隐藏日志页误取消导出。
4.  Operator
    局部主题补字体回退、checkbox/radio、scrollbar、tabbar、focus/disabled
    等状态；来源标签紧凑不均分。只修实际可见问题，不重排共享
    RuntimeJobLogs，不影响 Designer、生产页 QSS 和共享通信控件默认样式。
5.  `designer.runtime_logs.connection` 改成中立
    `runtime.logs.connection`，同步受影响断言。

## 明确不要做

-   不启用 `allowAdvancedHistory`，不暴露没有宿主结果页接线的
    `historyRequested` 按钮。
-   不重写 RuntimeClient，不增加 RPC/proto/数据库/事件写入者/订阅中心。
-   不改 Designer 可见布局，不做全 Runtime 回归，不逐控件建立截图矩阵。
-   不做真实相机、PLC、冻结包或现场验收。

## 测试与证据

-   优先扩展现有
    `tests/ui/operator_view/test_runtime_job_log_sources.py`。
-   用有限事件验证暂停/恢复 cursor、无重复/无缺口、无并发 reader。
-   运行受影响 Operator pytest、必要的共享 reader/Designer
    用例、Ruff/mypy 和 OpenSpec strict。
-   原生 Qt
    只保存本补充相关的起始/日志/设备截图；窄窗只在实际布局受影响时补。
-   测试必须运行在最终待推送提交 SHA
    上，记录真实命令、环境、统计和未验证项。
-   如推送，按仓库约定附上 `Codex` 身份的 GitHub
    comment，说明改动、验证结果和未验证项。
-   不合并 PR、不发布产品，除非用户另行授权。
