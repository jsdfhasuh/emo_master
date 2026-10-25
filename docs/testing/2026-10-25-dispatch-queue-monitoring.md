# 双流程背压、消费者页面与队列监控验证

日期：2026-10-25。

本轮已实现 Dispatch 可取消容量等待、持久消费者逐调用页面发布，以及全局变量窗口中的只读运行队列页。
源码和模拟业务链路完成联合验证；最终扩展回归为 **152 passed、1 failed**，唯一失败已在未修改的基线源码中复现。
这不是全仓库测试、冻结包或真实摄像头验收。

## 基线与改动边界

- 分支：`agent/runtime-workflow-architecture-v1`。
- 开始和结束核对的 HEAD：`9265547dbe61bc57542d3cf0ea143fcf455600fb`。
- 验证对象为该 HEAD 上的未提交工作区，不把本轮功能归属于上述提交。
- 原有错误提示、日志界面、`StartJobReply.diagnostics_json`、上传资源映射及其测试修改保留，不回退、不重复实现。
- 本轮复用现有 Runtime 帧所有权、Dispatch 调度、页面采集服务、后台请求 worker 和 QWidget 界面；未改业务变量类型或存储方式。
- 未连接真实相机 / PLC，未播放声音，未修改用户 RTSP 工程、凭据、模型、现场 Runtime 或数据库；未提交、推送、打包、部署或重启用户程序。

## 实现结果

### 队列满等待

Dispatch 新增 `queueFullPolicy=error|wait` 与 `waitTimeoutMs`。默认仍为 `error`；等待模式的超时 0 表示等待容量或取消。
同一扇出的全部目标都有容量时才接纳，明确的 `WAITING` 不生成拒绝回执、不递增拒绝次数、不推进接纳高水位。
生产者保持同一次采图、编码、cycle 和逻辑投递身份；传输信封序号和 requestId 的变化不产生新业务调用。
超时采用单调时钟，停止或消费者退出可退出等待；部分接纳和通信结果未知时不重放。

Windows 停止时可能先收到管道关闭，再运行取消探针。仅当框架请求已观察到取消且遇到 OSError / EOFError 时，
转换为取消并保留 `outcome=UNKNOWN`、动作和传输诊断；设备动作仍保留未知结果错误，已收到的明确错误回执也不被取消覆盖。

### 消费者页面

按消费者入口冻结页面配置，在创建持久消费进程前独立 attach；attach 在 supervisor 进程接纳锁之外完成。
消费者逐次调用沿现有 collector、结果存储、图片资产和订阅链路发布，不等整个 Job 结束，也不重复检测或留档。
DisplayJob 关联运行组、生产 Job、消费槽位；原有结果的 workflowRunId 身份保持不变，Dispatch 身份另作诊断关联。

从生产 Job 查看结果时，单个有页面的消费者自动选中；多槽位通过现有选择入口区分。
窗口标明消费工作流、槽位和 Job，查看不改变当前生产 Job 或停止操作的归属。
页面作用域须绑定消费入口，原生产者页面不会被自动改写。

### 运行队列页

“运行 → 全局变量”打开“业务变量 / 运行队列”双标签页。专用只读 `GetDispatchQueueState` 返回项目范围内的活动组、槽位摘要和按需明细。
累计接纳、完成、失败、取消、拒绝在调度器状态转移处维护，回执退休不抹掉累计数。
队列明细包含实际生产等待起点、FIFO 调用位置、入队 / 开始时间及有限图像元数据，不返回图像字节、共享内存句柄或设备配置。

可见页按 1000 ms 轮询，最多一个请求在途；切换、关闭和晚到响应按现有 worker 生命周期收尾。
查询失败保留旧快照并标记过期，组释放显示不可用而不是假零，旧 Runtime 显示不支持。
查询不 retain/release 帧、不写全局变量数据库；没有吞吐、CPU 或摄像头节拍的实测性能结论。

## 验证环境与最终结果

- Windows；`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，Python 3.10.21，PySide2 5.15.2.1。
- 所有 Python 命令使用 `-X utf8`。
- 一般 Qt 回归使用 `QT_QPA_PLATFORM=offscreen`；像素与原入口操作使用 `windows`，缩放分别为 1 和 1.5。
- 集成测试启动真实 spawn 子进程和 `127.0.0.1` 临时端口 gRPC，使用模拟相机及临时工程 / 数据库。
- 慢消费由测试屏障控制取项时机，实际算子、IPC、调度器、页面采集和资源退休仍运行。

原始 XML 和截图保存在仓库忽略目录 `build/`，未将它们当作已提交交付物。

| 检查 | 最终结果 | 本机证据 |
|---|---|---|
| 释放路径、队列状态及 StopAll 聚焦复查 | 22 passed | `build/dispatch-retirement-final-20261025.xml` |
| Runtime、spawn / RPC、编辑器、变量页、队列页及普通页面扩展回归 | 152 passed，1 failed；失败见下节 | `build/dispatch-full-focused-final-20261025.xml` |
| 原生 Qt 100%，单消费者与两槽位 | 2 passed | `build/dispatch-native-delivery-100-20261025.xml` |
| 原生 Qt 150%，单消费者与两槽位 | 2 passed | `build/dispatch-native-delivery-150-20261025.xml` |
| 隔离导出的未修改基线单测 | 同一断言 1 failed | `build/dispatch-existing-diagnostics-baseline-20261025.xml` |
| 17 个相关源码文件及 6 个示例 / 测试文件 Ruff | 通过 | 下方命令与文件清单 |
| 17 个相关源码文件 Mypy，follow-imports=silent | 通过 | 下方命令与文件清单 |
| scheduler 与 queue panel 额外 check-untyped-defs | 通过 | 下方命令 |
| protobuf 生成一致性、git diff --check | 通过 | 下方命令；Git 仅有 CRLF 转换提示 |

### 正常路径及直接反例

新增测试覆盖：单 / 多目标容量等待及恢复、同一投递去重、等待期间不重复采图 / 编码、超时、停止、消费者退出，
以及两个同时运行的组、项目隔离、统计恒等式、回执清理、元数据白名单和不改变帧所有权。
消费者仍存活时即取得数量和图片结果；保存输出可读，结束后帧和 cycle 释放；页面准备失败在任何消费者进程启动前收尾。

Qt 回归覆盖可见页轮询、慢请求、项目 / 组 / 连接切换、旧响应丢弃、断连过期 / 重连恢复、旧接口不支持、关闭及父对象销毁。
原变量编辑、写入、生命周期和包导入导出测试一并执行。新增等待参数经过编辑器保存重开、流程包往返和编译检查。

原生 Qt 测试经 `MainWindow.loadProjectDirectory()` 实际打开临时 2.7 工程，先同步并保存规范化草稿，再显式加载到隔离 Runtime，
没有绕过正常工程发布确认。使用原始启动 / 查看 / 停止入口，观察生产等待、消费页面、保存和队列累计数；
关闭监控与页面后业务继续运行；等待期间停止可结束，单槽位重跑得到新消费 Job 的结果。

截图目录为 `build/dispatch-queue-ui-20261025/`：`queue-1.png`、`queue-narrow-1.png`、`consumer-1.png`，以及对应 `1.5` 文件。
最终两个缩放的窄窗口截图和消费者结果截图已人工读取像素检查，未见文字、表格或操作按钮相互遮挡。
窄窗口截图中的长流程名和错误码是只读显示 DTO 的布局用例，不代表当时业务运行真的超时。
这是隐藏的原生 QWidget 自动化，不是用户当前运行 EXE 的人工操作。

计划矩阵用于组织范围，不表示每一条内部组合都独立运行过。图像准备 / 编码生命周期与普通结果断连重连复用了既有回归；
没有单独逐一执行每种消费者页面断连与慢显示组合，也没有执行真实 YOLO 缓存、检测精度、留档冷却或摄像头长期运行验收。

## 首次失败与处置

1. 初始队列 UI 用例 `7 passed、1 failed`：假客户端未返回测试期望的生产 Job。修正夹具，不改变产品选择逻辑。
   原始证据：`build/dispatch-panel-first-20261025.xml`。
2. Designer 原入口测试最初停在显式发布确认。2.7 草稿打开不等于发布，Designer 还会规范化端口与默认字段；
   测试改为保存规范化的临时草稿、显式加载隔离 Runtime，再调用原始启动入口，未移除生产保护。
3. offscreen 在正常停止与模拟 capture 相遇后弹 QMessageBox 时出现 Windows 访问冲突。
   当时原始业务诊断为设备动作 `E_DEVICE_OUTCOME_UNKNOWN`，保留设备动作未知结果语义，未将其伪装为成功或自动重试。
   初始中断运行未产生完整 XML；对应临时日志位于 `pytest-3378/testDesignerContinuousConsumer0/logs/runtime-20261025-042600-73308-0001.jsonl`。
4. 首次原生 Qt `1 passed、1 failed` 暴露等待停止时框架管道 BrokenPipe 与取消探针的顺序问题。
   增加上述限定范围的取消处理；明确错误回执及真实设备动作不受影响。
   原始证据：`build/dispatch-native-100-20261025.xml`，最终两种缩放均通过。
5. 首次扩展回归 `146 passed、6 failed` 中有 5 项为 `RunGroup(document=None)` 的既有释放夹具被新增显示策略初始化阻断。
   将显示策略解析延后至首次快照，释放路径不新增工程文档依赖，未弱化或删除原测试。
   原始证据：`build/dispatch-full-focused-20261025.xml`，对应 22 项聚焦复查已通过。

### 保留的基线断言失败

`tests/runtime/test_startup_cancellation.py::testUncancelledSpawnedDeviceActionFailureStaysFailed` 的第 250 行
要求终态 payload 与仅包含原始 diagnostics 的字典完全相等，但当前终态还包含 `diagnostics.deviceOutcome` 与 `previewSnapshots`。
此前关于 `FAILED`、`E_DEVICE_CAPTURE_FAILED`、`UNKNOWN` 和原始节点诊断的断言均通过。

使用 `git archive` 从固定 HEAD 导出 `src`、`tests`、`pyproject.toml` 到
`build/dispatch-queue-baseline-source-20261025/`，在该目录运行同一测试，得到完全相同的字段差异。
因此记录为已有测试预期与诊断 payload 不一致，不是本次 Dispatch 功能回归；本轮不修改其断言或相关业务诊断代码。
这项失败仍然存在，不能把最终扩展回归称作全绿。

## 可复现命令

在仓库根目录使用以下环境；测试清单也可从最终 XML 中逐项核对。

```powershell
$python = 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe'
$env:QT_QPA_PLATFORM = 'offscreen'
& $python -X utf8 -m pytest `
  tests/runtime/test_dispatch_queue_state.py tests/runtime/test_dispatch_queue_integration.py `
  tests/runtime/test_frame_dispatch.py tests/runtime/test_review_dispatch_lifecycle.py `
  tests/runtime/test_stop_all_dispatch.py tests/runtime/test_dispatch_start_retirement.py `
  tests/runtime/test_startup_cancellation.py tests/runtime/test_cancellable_device_wait.py `
  tests/runtime/test_device_ipc_retirement.py tests/designer/test_dispatch_editor.py `
  tests/designer/test_dispatch_queue_panel.py tests/designer/test_global_variables_ui.py `
  tests/designer/test_global_variables_lifecycle.py tests/designer/test_global_variable_review_fixes.py `
  tests/designer/test_targeted_dispatch_package.py tests/runtime/presentation/test_normal_capture.py `
  tests/runtime/presentation/test_normal_capture_resources.py tests/runtime/presentation/test_capture_lane_lifetime.py `
  tests/ui/page_designer/test_normal_run_viewing.py tests/ui/page_designer/test_two_job_observation.py `
  -q --timeout=180 --junitxml=build/dispatch-full-focused-final-20261025.xml

$env:QT_QPA_PLATFORM = 'windows'
$env:EMO_DISPATCH_UI_ARTIFACTS = "$PWD/build/dispatch-queue-ui-20261025"
foreach ($scale in @('1', '1.5')) {
  $env:QT_SCALE_FACTOR = $scale
  & $python -X utf8 -m pytest tests/ui/page_designer/test_dispatch_queue_viewing.py -q --timeout=180
}
```

静态检查的源码清单为下列 17 个文件，路径均相对于仓库根目录：

```powershell
$sources = @(
  'src/emo_master/core/workflow/dispatch_contracts.py',
  'src/emo_master/apps/designer/ui/dispatch_editor.py',
  'src/emo_master/apps/designer/ui/dispatch_queue_panel.py',
  'src/emo_master/apps/designer/ui/global_variables_dialog.py',
  'src/emo_master/apps/designer/ui/main_window.py',
  'src/emo_master/apps/designer/services/runtime_client.py',
  'src/emo_master/apps/designer/page_designer/preview.py',
  'src/emo_master/apps/designer/page_designer/preview_window.py',
  'src/emo_master/apps/runtime/devices/ipc.py',
  'src/emo_master/apps/runtime/frame_dispatch/runner.py',
  'src/emo_master/apps/runtime/frame_dispatch/scheduler.py',
  'src/emo_master/apps/runtime/grpc_server/service.py',
  'src/emo_master/apps/runtime/jobs/manager.py',
  'src/emo_master/apps/runtime/jobs/supervisor.py',
  'src/emo_master/apps/runtime/presentation/collector.py',
  'src/emo_master/apps/runtime/presentation/diagnostics.py',
  'src/emo_master/apps/runtime/presentation/rpc.py'
)
$extra = @(
  'examples/dispatch_queue_demo.py',
  'tests/runtime/test_dispatch_queue_state.py',
  'tests/runtime/test_dispatch_queue_integration.py',
  'tests/designer/test_dispatch_queue_panel.py',
  'tests/designer/test_dispatch_editor.py',
  'tests/ui/page_designer/test_dispatch_queue_viewing.py'
)
& $python -X utf8 -m ruff check @sources @extra
& $python -X utf8 -m mypy --follow-imports=silent @sources
& $python -X utf8 -m mypy --follow-imports=silent --check-untyped-defs `
  src/emo_master/apps/runtime/frame_dispatch/scheduler.py src/emo_master/apps/designer/ui/dispatch_queue_panel.py
& $python -X utf8 scripts/gen_proto.py --check
git diff --check
```

## 模拟工程与使用边界

生成器：`examples/dispatch_queue_demo.py`。本机交付样例：`build/dispatch-queue-demo-20261025/dispatch-queue.emoproj`。

由 `WorkflowStore().toPayload()` 生成最新 2.7 结构，经 `ProjectDocument` 校验、保存重读及测试中的实际 Runtime 编译 / Designer 打开。
默认是生产者无限 While、模拟相机、双轴翻转、容量为 1 的 wait Dispatch、消费页面与 `latest.png` 覆盖保存。
生产主流程的采图循环体是一个子工作流；只有生产 Job 和持久消费 Job 两类运行任务。
示例数量是固定模拟值 1，不代表人脸检测。样例文件未在用户 Runtime 中启动，重复生成拒绝覆盖已有文件。

使用说明见 [全局变量与运行队列](../global-variables.md)，实施状态见 [实施计划](../plans/2026-10-25-global-variables-queue-monitoring-plan.md)。
正在运行的 Designer / Runtime 不会热加载源码或新 RPC；正式使用需在允许的时间更新并重新启动对应程序。
转换用户真实 RTSP 工程、绑定实际检测页面及现场持续运行仍需单独明确执行，本轮没有代为操作。
