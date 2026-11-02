# 七类日志接入缺口修复

日期：2026-10-29。源码基线：`25c82042ce3cfb83e16ede8ea2cb2bcbb2110ce4`，叠加当前工作区修改。用户授权接入此前审查发现的七类日志缺口。本轮沿用 Python 3.10 / PySide2、既有日志窗口、Runtime EventStore / SQLite / JSONL；保留工作区内工程路径迁移、SQLite 编辑器等其他工作，没有提交、推送、构建或部署。

原审查与失败证据保留在 [日志接入缺口审查](../2026-10-29-log-integration-audit/README.md)。本记录表示以下指定缺口已修复并完成定点验证，不是全仓库零遗漏证明，也没有确定最初内网截图的具体根因。

## 七项行为

| 缺口 | 当前行为与等级 |
| --- | --- |
| 1. 较旧结果晚到的导出失败漏记 | 保留晚到失败的 ERROR 和结果身份，按来源有界去重及汇总；较旧成功不能恢复较新失败。原有通道忙的 INFO/WARN 分级保留。 |
| 2. Designer 本机显示失败未进入普通日志 | 图片读取、校验/解码、冻结图片读取、展示连接、客户端缓存及 Qt 图片预算，通过页面预览控制器进入现有日志窗口和 Designer 本机 JSONL。校验/解码失败 ERROR，通信超时/临时断连/预算 WARN，实际恢复 INFO。 |
| 3. 外部任务观察器过滤业务失败 | 对非普通 Run 管理的 producer/consumer，转发 presentation.*、WARN/ERROR 和 job.completed/job.failed/job.aborted；普通 Run 保留自己的订阅，普通逐帧 node.completed INFO 不新增转发。 |
| 4. 原生设备失败丢失等级、错误码与主日志入口 | 设备失败保留顶层 level/code/工程与节点归属。关键 WARN/ERROR 经独立的 __runtime__ 事件序列进入 EventStore、SQLite、Runtime JSONL 及 Designer 全局订阅；同设备/代次/操作的恢复为 INFO。关键事件不再同时直接写入第二份文件记录。 |
| 5. 普通任务日志断流/恢复静默 | 首次断流 WARN，连续重试合并，收到有效后续事件时 INFO 恢复。带任务、工作流、游标和错误信息，Qt queued signal 在 GUI 线程接入现有日志。 |
| 6. 预览快照失败未写 JSONL | preview.snapshot.failed WARN 加入现有运行日志白名单，保留原 EventStore / SQLite 路径。 |
| 7. 部分 Designer 操作只弹窗或错误记 INFO | Runtime 连接/读取失败、配置读取/操作失败、页面测试包导出失败、设备类型读取失败明确 ERROR；写入结果未知 WARN，保留 requestId，不自动重放；确认结果 INFO。取消保持正常行为，未保存等前置条件单独使用 WARN。 |

持续显示/设备故障首条立即报告，重复项按约 30 秒合并，保留次数与持续时间；停止时补齐未报告的汇总。显示诊断采用稳定成功后恢复，收尾只报告已经观察到的成功。主动停止、切换任务或释放结果触发的取消不新增异常；仍在观察时意外收到 RPC CANCELLED 则记 WARN。

## 文件与窗口

- 窗口入口仍为“运行 → 打开日志”。保留此前完成的“全部 / 流程设计 / 页面设计”横栏、可展开 JSON 树、完整 JSON 复制、筛选和节点定位。
- 新增“本机日志目录”按钮，打开当前 Designer 的诊断文件夹。默认位置为 `%USERPROFILE%/.emo_master/runtime/logs/designer`；配置了本机启动数据目录时为该目录的 `logs/designer`，`EMO_DESIGNER_LOG_DIR` 可单独指定目录。窗口按钮的提示显示实际路径。
- Designer 本机文件保存操作与客户端诊断的完整结构化上下文，文件名为 `designer-*.jsonl`，包括 source、level、code、project/job/workflow/node、payload 和 Designer 会话身份。复用现有异步 writer，单文件 8 MiB 轮换、总量 128 MiB、保留 14 天。
- Runtime 事件由 Runtime 主机上的既有 SQLite / JSONL 保存；Designer 显示这些事件，不把同一批服务端事件再复制进本机文件。远程连接时，“本机日志目录”不会打开远端 Runtime 的目录。
- “清空视图”只清空当前界面缓存；关闭窗口会收尾文件写入，不删除已写文件。日志窗口仍是当前会话的有限历史，不自动重新载入过去的本机 JSONL。
- 写入失败通过不再落盘的 `DESIGNER_LOGFILE_FAILED` ERROR 进入窗口，避免递归写同一失败文件；关闭 writer 返回的超时诊断也会被消费。Qt 直接删除窗口时由资源清理回调收尾。
- 全局订阅不占用 RuntimeClient 的运行流/显示调用计数，不妨碍正常切换 Runtime。同一订阅停止后恢复沿用游标；切换服务时重新读取新 Runtime。正式 Runtime 的 SQLite 序列在普通重启后继续增长。

## 验证

解释器：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`。Qt 使用 `QT_QPA_PLATFORM=windows`。使用临时数据目录、模拟设备/结果及本机 loopback gRPC/TCP，没有访问真实相机/PLC。

| 批次 | 结果 | 证据 |
| --- | --- | --- |
| Runtime 晚到失败、设备日志、JSONL 等定点回归 | 56 passed | [runtime-focused.xml](runtime-focused.xml) |
| A1 工程准备 → A4 设备配置正常路径及设备日志 | 9 passed | [runtime-device-chain-final.xml](runtime-device-chain-final.xml) |
| Designer 连接、配置、控制器、操作日志集成 | 61 passed | [designer-operations-integration.xml](../2026-10-29-log-gap-repairs/designer-operations-integration.xml) |
| Designer 断流、结果确认和操作日志补充 | 33 passed | [designer-operations-focused.xml](../2026-10-29-log-gap-repairs/designer-operations-focused.xml) |
| 主窗口、日志分类、流程来源回归 | 34 passed | [window-regression.xml](window-regression.xml) |
| 最终本机/全局日志集成、JSONL、日志刷新 | 41 passed in 11.79s | [local-global-final.xml](local-global-final.xml) |
| 最终客户端诊断、SessionViews、页面日志、Qt 图片预算 | 41 passed in 68.68s | [display-final.xml](display-final.xml) |
| 复跑原审查的五项辅助探针 | 五项结果符合预期 | [probe-results-after.json](probe-results-after.json) |

批次存在重叠，不累加为独立用例总数。最终本机/全局集成实际验证了：设备 ERROR 与恢复 INFO 经 EventStore → loopback gRPC → MainWindow → LogDialog；Runtime 文件中各一条；重建同一订阅不重复旧事件；本地结构化错误在清空视图、关闭窗口后仍保留；全局订阅不阻止 Runtime 切换。

最后客户端批次在收紧意外 CANCELLED 与主动取消的区分后执行，覆盖读取/解码异常、预算、外部任务转发和正常收尾。[原生 Qt 截图](designer-log-window.png) 由该批测试的模拟日志生成，已检查分类横栏、JSON 树、完整复制和本机日志目录按钮的布局；不把自动化截图记作现场人工验收。

修复后探针 [probe-after.py](probe-after.py) 沿用原业务场景，仅调整输出文件名，并为假 worker 补齐新增诊断信号/字典读取：旧结果现在有 EXPORT_FAILED ERROR；外部任务的三个失败都转发；设备顶层变为 ERROR/E_DEVICE_CONNECT；任务断流/恢复各有事件；快照失败实际进入 JSONL。原失败探针及输出未覆盖。

本轮涉及的源码与测试 Ruff 通过；限定修改的 `git diff --check` 通过，仅有仓库既有 LF/CRLF 转换提示。异步线程、Qt 清理、Runtime 切换及文件失败回路也进行了源码复核。

## 保留的失败与未验证范围

- Runtime 首轮定点批次为 55 passed / 1 failed：旧 JSONL 测试原本断言 preview.snapshot.failed 被过滤。修复要求其入盘，因此负例换成仍应被过滤的 process.heartbeat，并加入快照失败实际入盘的正例；不是删除过滤断言。
- 单独运行依赖 A1 准备的 A4，得到 7 passed / 1 failed，错误为 E_CONFIG_NOT_LOADED，在新增日志路径之前失败；见 [runtime-integration.xml](runtime-integration.xml)。随后 A1/A4 中间批次又因 MainWindow 先调用、LogDialog 尚未加入 setLogDirectory 而出现 2 failed，见 [runtime-device-chain.xml](runtime-device-chain.xml)。接口完成后按 A1 → A4 正常准备顺序执行的 9 项通过。
- 并行编辑期间 Designer 操作集成首轮为 32 failed / 29 passed，均在 MainWindow 初始化调用缺少的 setLogDirectory 时失败。原轮未请求 XML，已保留命令、失败点及结果的 [initial.txt](../2026-10-29-log-gap-repairs/designer-operations-initial.txt)；接口完成后同批 61 项通过。
- 页面/SessionViews 中间批次为 21 passed / 6 setup errors，同样发生在缺少 setLogDirectory 的初始化阶段；完成接口后受影响的两个页面文件 14 项通过。后续最终客户端批次 41 项通过，中间失败不据此抹除。
- 更早的页面扩展回归仍保留 8 passed / 2 failed：testNormalRunEnablesSharedPageCapture[ALL/NONE] 在工程配置同步确认阶段违反已有夹具断言，尚未进入页面预览。该既有失败的原因记录在 [前轮页面展示日志验证](../2026-10-29-presentation-operational-logs/README.md)，本轮没有修复或宣称其通过。
- 未执行全量源码测试、冻结 EXE 验证、内网部署或真实设备验收。现场使用这些变化需要配套更新 Designer 和 Runtime；本轮源码测试不代表旧安装包已有该功能。
