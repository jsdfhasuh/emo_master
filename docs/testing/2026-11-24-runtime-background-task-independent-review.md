# Runtime 后台保存独立审查

2026-11-24。PR #19 的后台保存主链路已实现，独立验收仍有缺口。优先修复配置排空丢图、跨 Job 的文件回执失效，以及 Dispatch 投递结果归属错误。这三项都有可重复的业务反例。

## 审查范围

| 项目 | 核对结果 |
| --- | --- |
| 实施依据 | 关联聊天附件及 OpenSpec 确认计划 `028400a573a38690f2518d97c97aff44d565b3a4` |
| 被审源码 | 产品提交 `b4df667267f15de3dba3690eed978b2681f9394a`。当前 PR HEAD 为 `50f240f9d47fcfa280ba1520153c8ecf46f55976`，后一个提交只更新交付记录及验收清单 |
| 工作区 | 复现和回归时工作区干净。审查结束只添加本报告 |
| 环境 | Windows、Python 3.10、临时工程、合成图片、回环 Runtime。各条目分别标出真实子进程、Qt 自动化或受控反例 |

## 影响业务结果的问题

### 1. 配置选择“排空后替换”仍会丢失已经受理的图片｜P1

在生产模式启动每 5 秒执行一轮的连续 Job，后台保存 6000×6000 的 RGB PNG，原图 108 MB，在服务限额内。读到真实 `node.completed` 中的 `accepted=true` 后，立即在线给流程加一个数值节点并选择 `drainAffected`。两次 Windows 子进程实测中，配置均返回 `COMMITTED`、旧 Job 均为 `ABORTED`，回执中的目标文件均未生成。较小图片能在 Stop 前落盘。

工作流返回即释放周期。配置切换只检查周期和 Dispatch 队列，随后发出普通 Stop，后台服务按取消语义放弃已接收的写入。修正应让配置排空在原有预算内等待旧 Job 已接收的任务和文件登记，再退休该 Job。主动 Stop 继续按原有 5000 毫秒规则处理。定位在 `apps/runtime/project_config/transition.py:73` 和 `apps/runtime/jobs/worker_main.py:51`。

### 2. 后台保存回执跨 Dispatch 后，两边的 Job 都会失败｜P1

使用全内置算子的双 Job 工程，将主流程的 `ImageSaver.result` 作为 JSON 传给子 Job，子流程的“条件选值”原样输出该值，主流程用 Join 等结果。真实 Windows 子进程中，PNG 已经写出，子 Job 的选值节点仍报 `E_BACKGROUND_TASK_EXPIRED`，主 Job 的 Join 随之报 `E_DISPATCH_FAILED`。同一工程改为同步保存，两边均完成。

接收方仅凭 JSON 字段认出待保存文件，随后到自己的后台服务里查另一个 Job 的任务编号。应在跨进程交付文件回执时由来源 Job 确认写入、完成登记，再交付可跨进程使用的文件结果。Dispatch 输入和消费者返回结果都有这个边界。定位在 `apps/runtime/workflow/background_outputs.py:49` 和 `apps/runtime/frame_dispatch/runner.py:18`。

### 3. 前一次投递的写入失败会记到下一次投递｜P1

在同一个消费者连续接收 A、B 两次投递。用真实 WorkflowRunner、后台服务和 ImageSaver 执行，用 Event 固定编码失败时机，模拟 Dispatch 投递通道。A 的工作流先返回，在取 B 时让 A 的编码失败。实际回执为 A **COMPLETED**、B **FAILED**，B 的错误文本来自 A。异常中的源 workflowRunId 确认属于 A，B 的保存节点尚未执行。

消费者先对 A 发出 `dispatchComplete`；B 启动时轮询到 A 的异常，异常处理却使用 B 的投递身份发布失败。应在确认当次投递的业务终态前处理属于该次投递的任务，确保 Join 和运行队列收到正确的结果。定位在 `apps/runtime/jobs/worker_main.py:287` 和 `:292`。

### 4. Designer 保存节点看不到后台保存成功的结果图｜P2

对同一 ForEach 工程分别做同步和后台保存，实际启动两次 Runtime 子进程，并通过回环 gRPC 和原生 Windows Qt 节点面板检查。两次都能从 Runtime 读取 `__saved_result__`。同步时产物事件为第 19 条、节点完成为第 20 条，面板有“保存结果图”。后台模式的节点完成为第 19 条、产物为第 29 条，面板找不到该选项。

Designer 收到迟到的 `artifact.created` 时只更新 Job 的最近产物。它仅在处理节点事件时把产物写入该次节点记录。应在收到产物事件时按 Job、工作流执行和节点执行的完整身份补入仍然保留的那条历史记录，并更新记录大小。定位在 `apps/designer/state/run_result_history.py:191` 和 `apps/designer/ui/node_result_panel.py:196`。

### 5. While 的迭代间隔会推迟后台失败，甚至改变主错误｜P2

用已编译的无限 While、真实 Runner 和 ImageSaver，使编码在迭代间隔内失败。设置 `minIntervalMs=1100` 时，约 1.11 秒后才报保存失败。再设 `timeoutMs=450`，约 0.47 秒后先报 `E_LOOP_TIMEOUT`，保存失败直到收尾才可见。顶层连续 Job 的等待已经会轮询，内层 While 仍整段等待。应保持原节拍和取消响应，并在间隔中检查后台任务。定位在 `apps/runtime/workflow/loop_runner.py:290`。

### 6. 普通 Job 失败可能在输出目录留下临时图片｜P2

Windows 独立子进程运行真实 Job worker 入口、后台服务和 ImageSaver，使用测试夹具控制工作流，在临时文件关闭后、最终发布前模拟下游节点报错。Job 发出 `job.failed` 并退出，目录留下 84 字节的 `.tmp`，正式 PNG 未生成。该反例使用受控延迟，没有强杀进程。

失败分支封闭最终发布后立即返回，daemon 线程可能尚未执行删除临时文件的 `finally`。修正时应给正在退出的任务有限清理时间，保留工作流原始错误。定位在 `apps/runtime/jobs/worker_main.py:53` 和 `plugins/builtins/image_saver/operator.py:194`。

上文短路径以 `src/emo_master/` 为根。

## 已验证的计划项和回归缺口

| 计划项 | 独立核对 |
| --- | --- |
| 注册、能力注入、调试隔离 | 旧 manifest、非法声明、按可信声明注入及纯预览、克隆试跑隔离的定点测试通过。ImageSaver 单算子调试覆盖真实子进程；整工作流的后台能力注入使用服务端测试算子，实际 ImageSaver 沿用仅单算子调试的准入规则 |
| 后台服务、正常 Job、主动 Stop | FIFO、同 Job 共享及独立 Job 隔离、数量和字节限额、记录过期、取消后的发布门禁、正常 drain 和顶层空闲轮询用例通过 |
| ImageSaver、产物、SQLite | 同 Job 正常路径验证了源图复制、保存与后续节点重叠、完整 PNG、产物副本及校验和、产物先于 Job 终态。SQLite 文件绑定等到写完才执行，注入保存失败时 stop/continue 均未写入无效引用 |
| Designer 表单回归 | 文件选择器的“选定新路径”和“取消”两项测试失败。原有路径、覆盖和命名参数均符合断言，实际结果多了预期的新字段 `saveMode=sync`。应保留旧字段断言并明确验证同步默认值。定位在 `tests/designer/test_image_saver_form.py:26` |

本轮 Windows 联合回归为 **89 通过、2 失败、0 跳过**。其中还有单独执行的重叠子集，未重复累计。OpenSpec strict 校验通过。复现计数和故障注入反例独立于 pytest 的 91 项计数。

```powershell
python -m pytest -q tests/designer/test_image_saver_form.py tests/runtime/test_background_tasks.py tests/runtime/test_background_task_job.py tests/runtime/test_background_task_debug.py tests/plugins/test_image_saver_operator.py tests/sqlite_writer/test_background_file_references.py tests/core/plugin/test_registry.py --tb=short --timeout=120
openspec validate add-runtime-background-task-service --strict
```

托管 CI 核对时，Linux Runtime 已结束，3422 通过、4 失败、10 跳过。4 个失败的用例在实施所合入的 main 提交 `82a1aa6d8499ee203ea8e452c2f016db37ea7b3f` 上也失败。Linux 和 Windows 的 other 分组均有设备包相关失败，基线上也有相同用例失败。当时 Windows Runtime 分组尚在运行。实施记录中的 Windows 414 项和 Linux 132 项是上一轮的验证数据。本轮未另行跑 Linux 全量、冻结包、人工 Designer 或现场设备验收。[本 PR 的源码 CI](https://github.com/jsdfhasuh/emo_master/actions/runs/45305345623) · [基线 CI](https://github.com/jsdfhasuh/emo_master/actions/runs/45164410608)

## 两项非阻断观察

- 独占输出目录时，UTC 时间和 UUID 已使各保存任务使用不同路径。若其他写方拿到提交回执，并在正式发布前主动创建同一目标，`os.replace` 会覆盖它。受控测试复现了这一窗口。共享写入同一目标的环境需要原子拒绝覆盖。定位在 `plugins/builtins/image_saver/operator.py:191`。
- 超出 128 MiB 的任务会正确报 `E_BACKGROUND_TASK_TOO_LARGE`。不过 ImageSaver 在服务判定大小前已执行 `image.copy()`。6000×8000 RGB 反例中，拒绝前额外分配约 144 MB。可在复制前复用服务的单任务大小校验。本次没有观察到进程内存耗尽。定位在 `plugins/builtins/image_saver/operator.py:145`。
