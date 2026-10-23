# 多视频流程：旧调试快照有界异步持久化

日期：2026-10-23。实施起点 SHA：`21b9ad6aa4c24b8dafa01a5e6d78a69cc9396e84`。
工作目录：`D:/jsdfhasuh/documents/my_project/emo_master`；验证基于该工作区的未提交源码。
保留已有文件列表、动态视频路径、设备状态与网络相机等其它改动，没有提交、推送或打包。
收尾时 HEAD 为 `25120e0124358a48c587ff80cb03bb06bcf8f5ad`；已核对新增提交仅涉及
并行 UI 测试及其记录，没有修改本轮 Runtime/快照文件，不重复实现或回退并行工作。

## 范围

用户要求改善 9 视频的循环处理耗时，并确认采用异步方式。本轮只拆分旧调试快照的
持久化，不并行执行视频/While/ForEach，不改 YOLO 配置，不改正式 Image Saver 回执、
页面采集或实时检查会话的发布与身份校验。原 `.emoproj`、视频、模型、现有裁剪输出
不改写；临时工程及输出均位于独立验证目录。

- `preview/async_writer.py`：每 Job 单后台线程；同工作流/节点/有效端口集合合并待写旧
  调用；图像、JSON、上下文与端口定义先冻结；部分输出保留顺序。
- 容量默认 64 个待写调用、64 MiB 数据预算（包括正在写的一份），满时可取消地等待。
  空缓冲允许一份超预算大图；对象/短暂复制/codec 内存不等于这个数据预算。
- `preview/store.py`：复用原有原子写入、索引与清理；拆出 `captureLegacy`；无有效输出
  不再重写空索引。直接使用同步 Writer 的调用者仍同步。
- `jobs/worker_main.py`：成功先排空最后快照再发布终态；失败/停止丢弃待写诊断并等待
  活跃写入退出；收尾期间停止仍为 ABORTED。排空期间心跳继续。可选诊断不覆盖业务异常。
- 终态事件 `payload.previewSnapshots` 提供队列统计。它是原始事件字段，现有 JSONL
  completed 投影不会自动保留这些字段，验证不能只读取该投影。

## 首轮失败与修复（保留，不算通过）

首轮局部测试 49 项通过，但没有覆盖真实编译节点的只读端口映射。整套 Runtime 试跑
`d7c61064-6fcd-4a49-b8fa-b164499fa1ee` 暴露出对 `MappingProxyType` 直接 deepcopy 的错误：
业务运行结束，但快照提交/写入为零，并有 `cannot pickle 'mappingproxy' object` 警告。
**该轮约 149.6 秒的耗时无效，不能作为性能改进结果。**

修复为先把编译端口表转成独立 dict 再 deepcopy；新增真实编译 Video/ForEach/While
回归及实际 spawn Job 的最终快照提升、终态统计、图像/调用身份检查。不是仅替换测试夹具
或忽略警告。修复后要求快照提交/写入非零、警告为零、最终资产可读。

验证脚本另有一个读回错误：把 `EventStore.readMerged()` 当成无限历史。该接口仍受
SQLite/内存滚动保留窗口限制，无法从最终窗口取回最初的 job.accepted。已改成仅在
隔离验证进程使用原始事件 sink，保留本轮完整事件；未为此修改生产事件存储/日志系统。

首轮记录：
`D:/jsdfhasuh/Downloads/Compressed/emo_master_file_list_riders_20261023/validation/async-preview-20261023-160247/`
其中 `REJECTED.md`、`run-report-initial-readback-failure.json`、原脚本、console、保留事件和
Runtime 日志均保留，后续复测使用新目录，不覆盖失败证据。

## 修复后验证

Python：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`（3.10），Qt 使用 offscreen。

修复后局部测试 **50 passed**；加上实际 spawn 最终快照回归及扩展链路后，最终
**205 passed in 70.04s**，退出码 0。不是全仓库测试。

覆盖以下 19 个测试文件（`tests/runtime/`，最后一个 Image Saver 算子文件在 `tests/plugins/`）：

```text
test_async_preview_writer.py             test_worker_preview_finalization.py
test_preview_snapshot_store.py           test_preview_snapshot_origins.py
test_inspection_preview.py               test_worker_heartbeat_finalization.py
test_worker_capture_diagnostics.py        test_run_inspection_store.py
test_run_inspection_rpc.py                test_yolo_inspection_outputs.py
test_legacy_snapshot_policy.py            test_video_files_foreach.py
test_image_batch_while.py                 test_image_saver_filename.py
test_runtime_job_lifecycle_integration.py test_worker_event_batch_barriers.py
test_worker_event_batch_persistence.py    test_job_supervisor_contracts.py
../plugins/test_image_saver_operator.py
```

最终 Ruff 检查本轮 3 个源码文件和 2 个新增测试文件；Mypy 检查 3 个源码文件。
测试原始输出在有效复测目录的 `regression-tests.txt`。新测试曾有一条 lambda 赋值的
E731 lint 提示，已改成普通函数；不涉及产品行为或测试断言放宽。

关注正常路径：异步返回/冻结、合并顺序、部分输出、同名节点隔离、容量与大图、取消、
写入失败警告、成功/失败/取消终态与心跳、NONE 策略、实时图片身份、正式保存不变。

## 实际 9 视频复测

修复后独立目录：
`D:/jsdfhasuh/Downloads/Compressed/emo_master_file_list_riders_20261023/validation/async-preview-20261023-161342/`

通过源码 RuntimeService 启动真实子进程；保持 ALL 快照、实时检查会话和页面采集开启。
测试副本仍为 2.7，由原工程完整模型复制，仅改 Image Saver 的输出目录；执行模型校验、
保存重读和 Runtime LoadProject/编译。保留完整原始事件、Runtime 日志、报告和独立裁剪图。

有效 Job：`60444524-ec48-4fc0-9adc-33a64978303c`，Worker PID `43336`，
状态 **COMPLETED**，实际 Worker 已退出并释放任务资源。

| 检查 | 结果 |
| --- | --- |
| job.accepted→job.completed | **412.395 秒（约 6 分 52 秒）** |
| 调用 StartJob 到观察到终态的墙钟时间 | **412.906 秒（约 6 分 53 秒）** |
| 视频 / 取帧 / node.completed | **9 / 159 / 9623**，取帧路径、原始帧号、hasNext 与原验证报告一致 |
| 正式裁剪 | **32 张**，与原生验证基线逐像素、PNG 文件 SHA-256 均一致 |
| 全部事件 WARN / ERROR | **0 / 0** |
| 快照提交 / 合并 / 实际写入 | **5997 / 4964 / 1033** |
| 后台写入失败 / 收尾丢弃 | **0 / 0** |
| 待写队列峰值 / 数据预算计账峰值 | **34 / 22,206,895 字节（约 21.18 MiB）** |
| 成功提升的旧快照端口条目 | **47** |
| 实时检查存储交接 | 会话仍有效；`readers=0`、`pendingFiles=0` |
| 用户原工程、模型、9 段视频 | 运行前后 11 个文件 SHA-256 全部一致 |

工程原始 SHA-256：`c380474ac8f464e06e4c08d8543f7a0f41646a2bfd4428159b754f0a7c87d3e0`。
验证报告：有效复测目录 `run-report.json`；完整原始事件：`events.jsonl`；独立裁剪：`riders/`。
报告中的 historicalSeconds=1580.008 来自此前状态观察；本次重新核对的同口径事件时间
为下面的 1579.859 秒，以下耗时比例使用事件口径，不混用这两种时间。

历史对照 Job `d36ce58c-645c-49f8-8de3-56de04ff51ab` 的 JSONL 本轮重新读取：
job.accepted→job.completed 为 **1579.859 秒（约 26 分 20 秒）**，159 次取帧、32 次保存、
9623 次 node.completed、无 WARN/ERROR。它不是同机负载受控的同步/异步 A/B，不能把
历史比值说成各机器保证的加速比。

同口径历史对照下耗时减少约 **73.9%**。保持所有 159 帧和 32 张正式结果，减少的是
只供调试、原本也会被后续覆盖的快照写入，不是减少业务处理或降低识别质量。

小规模 Writer 对照独立于视频试跑：128 次标量/几何快照、8 个反复执行节点，使用实际
文件写入。同步提交/结束 14.453 秒、128 次索引写；异步提交 0.016 秒、含排空结束
0.828 秒、9 次索引写，合并 119 次，最终 16 个端口值相同。详见首轮目录下
`writer-comparison.json`。这个对照使用普通端口元数据，只证明局部排队/合并效果，不是
真实编译节点或整条视频链路的验收替代。

## 交付边界

- 这是源码、隔离临时工程、真实 spawn Runtime 和相关自动化测试的证据。
- 没有对用户当前 Designer 窗口做人工走查、没有冻结 EXE、部署、重启正在使用的
  Designer/Runtime，也未访问相机/PLC。既有测试使用模拟设备/合成输入。
- 当前已运行进程不热加载源码；通过当前源码启动的新 Worker 才能使用本实现。
  打包版需另行构建，本轮没有将源码修改冒充已更新安装版。
