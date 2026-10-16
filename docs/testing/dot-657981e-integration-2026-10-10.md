# dot 657981e 算子易用性与本地维修整合

## 身份与范围

- 共同基线：`74abb41612d2689ba44f34f599b9404a6f1ccbe1`。
- dot 已推送提交：`657981e1c5159f82efdb0052c80053f80cbcc895`，直接继承上述基线，共 54 个变更文件。
- 整合前本地维修：28 个已跟踪变更、7 个新增文件。18 个路径与 dot 重叠；其中一个是同名新增测试，不能覆盖。
- 当前分支以 fast-forward 接纳 dot 提交，本地维修及语义整合保留为未提交工作区变更。没有合并 PR、推送、发布或部署。
- 整合前原始文件 ZIP、逐文件 SHA256、binary patch 和两边/共同基线版本均已保留；另保留 stash `a3133b6e20992f86c40734ff1df56835d5e7673e`。验证通过前后均不自动删除备份。

## 语义整合决策

| 重叠区域 | 整合结果与不变式 |
| --- | --- |
| 草稿纯预览协议 | 统一使用 dot 已推送的 `OpenDraftPurePreviewSession` / `CloseDraftPurePreviewSession` 以及上传、读取、执行请求中的 `draft_session_id`。本地尚未发布的 `PreviewDraftContext` 曾与同一字段号发生类型冲突，不能同时复用字段号；不保留第二套矛盾协议。生成代码与 dot 协议一致。 |
| 草稿与 Runtime 身份 | 服务端新发会话隔离同 ID 工程副本；当前 Qt 线程冻结草稿、绑定和未 Apply 参数；旧 Runtime 明确提示更新/重启；不 LoadProject，不创建正式 Job，不借用生产可变变量或旧工程图片。叠加本地原 Runtime 固定与重连拒绝，清理不需要访问已删除节点或已关闭工程。 |
| 异步结果与收尾 | 按当前 generation 筛选结果，晚到旧结果不能吞掉新结果；跟踪所有在途 Future，而非只等最后一个。窗口关闭在后台等待所有生产者退休后向原 Runtime 清理，空闲关闭立即完成；保留 dot 的失效草稿检查、会话租约、配额及失败删除追踪。 |
| 参数完整性 | 保留 dot 中文标签、字段定位、动态适用性、旧 schema、可选字段、颜色通道边界、算子模式及输入错误改进；本地非法 JSON、非有限数、精度、Apply/保存基线不变回归一并保留。显式提供的隐藏参数仍校验，未提供隐藏项不凭空加入。 |
| YOLO 可选类别 | dot 当前 schema 使用 `xBlankMeansEmpty`；表单也兼容本地先前的显式 `xEmptyValue`。只有 schema 明确允许的非必填空输入可以映射为空值，坏 JSON 仍拒绝。 |
| 调试动作反馈 | 统一使用 dot 按动作记录的 `DebugActionFeedback`，不在同一窗口显示两套错误标签。成功 poll 不清除动作错误；成功载入可以显示明确成功状态，不再要求成功标签隐藏。保留本地多轮自然 poll 与旧输入不变断言。 |
| Prepare / Execute / 历史结果 | 保留 dot 执行阶段、ACK 丢失后原 request ID 单次核对与禁止重发；保留本地准备阶段异常标识、明确“准备失败”、提交时清空旧图像以及历史参数/导出身份。Prepare 失败不能伪造 executionId 或显示执行中。 |
| SQLite 与原 CI 用例 | 本地确定性事务连接 close、初始化失败清理、显式 idle owner、最终重载前真实资源退休以及诊断脚本连接生命周期修复均保留。dot 草稿变量读取也改用确定性关闭的连接管理器；不放宽原超时、所有权准入或 WAL/FULL/逐事件提交。 |
| 日志与其他算子 | dot 日志筛选、状态摘要、阈值/模糊/范围/坐标/掩码/数值等易用性改动全部纳入，没有选择性退回旧文件。真实相机/PLC/TCP 外部操作不在本轮执行。 |

## 回归用例与证据

- dot 的 `tests/runtime/test_draft_pure_preview.py` 原样保留；本地同名测试改名为 `tests/runtime/test_draft_context_preview.py`，将请求构造适配至统一会话协议，保留 fresh Runtime、不同窗口/工程、完整图像、真实 sync/aio gRPC、旧 Runtime 拒绝和原 Runtime 清理断言。
- 本地 UI 回归使用统一的动作反馈/会话接口，没有删掉失败保留、恢复、完整像素/结构化结果、无正式 Job 或生命周期断言。
- 额外覆盖“新结果已完成但更早的 worker 尚未退出时关闭”及“任务准备后 Runtime 重连，旧任务拒绝、仍能清理原 Runtime”。
- 最后审查还发现异步清理失败会经 dot 的日志钩子触碰 Qt 窗口；清理路径改用线程安全的标准日志，并新增失败注入回归。先前启动的 A 候选全量检查已主动中止并保留日志，不能算作最终通过；最终验证重新冻结 B 候选。
- 首次工作区专项出现一次流程窗口在原 12 秒等待内未 READY（249 PASS / 1 FAIL）；保留该失败，并添加仅诊断性状态/日志输出，不延长等待或自动重试操作。后续冻结副本验证单独记录，不以首次失败推断为随机抖动或认定根因。
- 新整合版本的冻结清单、静态检查、全量 JUnit、退出码、原生 UI 和重复检查证据位于：
  `C:/Users/jsdfhasuh/.codex/visualizations/2026/10/10/01a12413-8aec-7b13-b360-0f606c408e77/integration-657981e/`。
- 以该目录实际日志与 `validation-summary.json` 为最终结果依据；整合前 `repair-74abb` 的通过数字不是本次整合验证结果。

## 交付边界

本地 Windows / Debian 12 容器软件回归、Windows 测试专属原生 Qt 窗口、精确提交远端 CI、Debian 13 Xfce/GTK 实机复验、设备写入、安装包和生产部署必须分别判断。未提交的整合工作区不能声称已由 dot 拉取或由 GitHub Actions 验证。

dot 下一轮按 `dot-74abb-second-repair-checklist.md` 使用实际交付的新 SHA，Designer 与 Runtime 均更新并启动新进程，保留原工作区和证据。
