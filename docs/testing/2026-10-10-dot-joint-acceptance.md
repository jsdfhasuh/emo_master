# dot 联合实机验收交接

日期：2026-10-10。交付分支：`agent/runtime-workflow-architecture-v1`。用户已授权将当前工作区全部改动一起提交和推送；包括单算子/流程调试、通用并发运行、硬件触发等待、变量操作、坐标/几何桥接及其测试和示例。精确提交号以交付消息和 `git rev-parse HEAD` 为准。

本文是交接清单，不表示 dot 已收到消息或实机验收已经执行。源码测试、原生界面检查、真实设备、打包发布分别记录，不互相替代。

## 1 版本与环境

1. 从远端获取本次提交，Designer 和 Runtime 使用同一版本；先记录提交 SHA、Python/Qt 版本、Windows 版本、屏幕分辨率/缩放。不能只更新 Designer，保留旧 Runtime。
2. 先备份现场工程、设备参数、固定坐标、模型和生产数据库。已有本地改动先保留，禁止 `reset --hard` 或覆盖现场工程；建议为联合验收建立独立检出与独立测试数据目录。
3. 源码 UI 可使用仓库的 `start_designer.cmd`；它使用独立开发目录，默认不自动检测。若解释器不同，设置 `EMO_MASTER_PYTHON`。不要对既有生产 Runtime 重复启动同端口的新实例。
4. 不将本次源码推送视为安装包更新。使用旧安装包不能验证本提交；本轮没有打包、发布或部署。

## 2 范例工程

| 工程 | 用途 | 能否直接调试 |
| --- | --- | --- |
| [01-nested-calls.emoproj](../../examples/workflow_debugger/01-nested-calls.emoproj) | 两次子调用、调用身份、进入/跳过/跳出、纯算子试运行 | 可以，无输入、无设备，结果 value=7 |
| [02-foreach.emoproj](../../examples/workflow_debugger/02-foreach.emoproj) | 循环轮次、条件断点、命中次数、暂停时间预算 | 可以，输入同目录 items.json；结果 `[false,true,true]` |
| [03-image.emoproj](../../examples/workflow_debugger/03-image.emoproj) | 图像上传、真实上下游输入、临时改参、完整图像输出 | 可以，输入同目录 sample.png；mask 320x240、threshold=127 |
| [双工位正常链路能力夹具](../../examples/hardware_trigger_normal_fixture/project.json) | 通用并发、两拍、硬件触发、实际中间算子和报文/ACK 结构 | **不能用通用调试器直接跑设备链**；先按该目录 README 完成配置核对，再走正常执行 |
| [离线双工位控制骨架](../../examples/two_station_normal_path/project.json) | 直接输入模拟坐标的控制关系 | 不是实机算法工程；使用其 run_simulation.py 作离线参考 |

调试器逐步操作和预期见 [范例 README](../../examples/workflow_debugger/README.md)。三个调试范例无地址、设备号或模型依赖。硬件夹具含假 cameraKey、本地占位端口和缺失 ONNX 文件，**不得直接替换成生产地址后启动**。

## 3 第一轮：调试器与正式执行互斥

以下三类必须分别验收，不能用“流程最后跑通”代替单点操作通过：**单算子独立调试、流程逐节点单步、流程断点/暂停节点试运行**。

| 编号 | 入口与操作 | 必须观察的结果 |
| --- | --- | --- |
| D01 | 01 工程切到 child，右键 number > 算子调试；不应用地修改 value=99，再执行 | 只运行 number，不要求提供 child 的 after 输入，不执行父流程；输出 99，正式参数仍为 7 |
| D02 | 03 工程切到 process，独立调试 threshold；手动上传 sample.png | 不自动运行 blur，也不自动取上游历史结果；输出是原图直接按 127 二值化的完整 mask |
| D03 | 01 流程调试：seed 单步进入、first 单步进入、number 单步跳出、second 单步跳过 | 每次只前进到一个已确认位置；依次 first、child/number、first 的 call.return、second 的 call.return；调用输出均为 7 |
| D04 | child/number 普通断点，不填条件；连续恢复两次 | 分别在两次调用执行前暂停，动态调用身份不同，不串用上次输入/输出 |
| D05 | child/number 条件 `params["value"] == 7`、命中起点 2 | 只在第二次调用执行前暂停；完成后 value=7 |
| D06 | 02 的 body/compare 条件 `inputs["left"] >= 5` | 停在 left=5、9，轮次为 1、2；结果仍为 `[false,true,true]` |
| D07 | 03 断点页选择 process/threshold，点击“运行到所选节点” | 停在 threshold 执行前，输入是本次 blur 的完整滤波图，不是原图或旧图 |
| D08 | 在 D03 的 number 暂停点试运行 value=99；在 D07 的 threshold 暂停点试运行 threshold=200 | 临时结果分别为 99/200，暂停序号和主流程位置不变；继续后主流程仍为 value=7、threshold=127 |

独立调试结束并确认资源释放后，再开启流程调试。D01/D02 不补跑上游，D03-D08 则沿真实流程执行前置依赖，这两种行为不同且都必须验证。每项附操作后的截图、暂停序号和输出，不只记录按钮可以点击。

- [ ] 三个调试范例可从 Designer 正常打开，参数表单与连线可见，未因打开文件而落盘或变成已修改。
- [ ] 01 的 child/number 试运行 value=99 后，恢复主流程真实输出仍为 7。工程参数、撤销栈及生产变量未被试运行改写。
- [ ] child/number 条件 `params["value"] == 7`、命中起点 2，只在第二次调用暂停；输入/输出数据对应本次调用身份。
- [ ] 02 的 `inputs["left"] >= 5` 断点停在 5、9，最终布尔结果和轮次正确；暂停超过循环活动预算仍可继续。
- [ ] 03 图像可上传、查看、下载；frame 与图像来源一致；阈值临时试运行不会热改主流程。
- [ ] 暂停 65 秒后仍可恢复；快速重复点击不会多走一步。停止、关闭、工程切换后资源最终释放。
- [ ] 调试会话活动时 F5、页面设备预览、正式 Job 被拒绝，不自动保存、关闭调试或启动上游。
- [ ] 正式 Job 或设备预览占用期间，调试启动被拒绝；结束并确认资源退休后才允许启动。
- [ ] 相机/PLC/TCP/文件写入/模型推理节点在通用调试中明确显示不支持；整份流程草稿中含未开放节点时会被保守拒绝，包括不可达分支。专用设备调试仍走既有入口。
- [ ] 100/125/150/200% 缩放下文字、工具栏、参数、调用栈、图像可读可操作。保存截图和当前暂停序号。

## 4 第二轮：设备只读与正常双工位执行

本轮只由具备设备访问权限、了解现场联锁和急停条件的操作者执行。先单设备、只读、无外部输出，再逐步组合；本文不授权自动向 PLC、机器人或生产目标写入。

1. 按 [硬件夹具说明](../../examples/hardware_trigger_normal_fixture/README.md) 核对两个相机的唯一身份、硬件触发来源、实际 SDK/blockId、PLC 只读点位、模型、标定/单位和输出目标。模型和业务公式未确定时不进入联动。
2. 先只验证相机硬件无触发等待。等待期间 CPU、心跳、界面和停止应正常；无图像时不得读取本帧 PLC、增加拍次或发送结果。不能以“设备可枚举”替代取帧成功。
3. 在测试工装上触发单帧，检查 image/frame/blockId；检查缺帧、重复帧或无法提供 blockId 的诊断，不伪造连续性。异常后必须人工重新同步物理首拍，不自动重放。
4. 在禁用实际外部写入或连接明确的测试接收端时检查两拍：本帧 n 应为 1/2/1/2，执行后计数为 1/0/1/0；第二拍算法在 reset 完成后才进入。计数不是 ACK 成功数。
5. 用“运行 > 运行目标与并发…”只选择 `station1-run` 和 `station2-run`。一侧无触发等待时另一侧应可处理；分别停止/重启不得连带重启另一侧。关闭程序前确认两侧 Worker 和设备释放。
6. 最后才由现场负责人批准外部发送测试。确认设备单位、轴向、比例、舍入、接收目标和 ACK 含义；ACK 超时属于可能已发送，**禁止自动重发**。PLC ACK 不证明所有坐标都写入，机器人 ACK 不等于物理动作完成。

夹具中的测试公式、像素参考点、ROI、测量算法和占位模型不构成旧业务算法等价性。特别是 Blob 内心、多孔顺序/协议以及真实坐标单位仍须独立确认；不得把默认圆心或测试相加公式用于未核对的现场路径。

## 5 记录与停止条件

每项记录 `PASS / FAIL / SKIP / UNSUPPORTED / NOT_RUN`，附版本 SHA、工程文件摘要、配置差异、Job/会话 ID、workflowId/nodeId、pauseSequence、动态调用/轮次、日志及截图。设备故障还应保留 SDK 错误码、blockId、PLC/网关回执和实际触发时间，不在报告中泄露密码或凭据。

以下任一情况立即停止相关测试并保留现场：意外外部写入、错设备占用、拍次/坐标空间错误、结果身份串线、超时后自动重发、关闭后设备仍占用却显示已释放。不要通过删除数据库、重启所有服务或清空缓存掩盖问题。

回传建议格式：

```text
Commit / Designer / Runtime:
Machine / Windows / Qt / resolution / scale:
Project / changed configuration:
Case / PASS|FAIL|SKIP|UNSUPPORTED|NOT_RUN:
Expected / actual:
Session or Job / workflow / node / pauseSequence / iteration:
Logs / screenshot / device code / receipt:
Cleanup confirmed / external effect / manual intervention:
```

## 6 源码验证

联合提交的验证记录见 [交付验证](2026-10-10-joint-delivery.md)。历史调试器验收仍保留在 `operator-step-2026-10-09/`，其中失败日志和旧 mypy 基线不删除；新结果不改写旧验证历史。
