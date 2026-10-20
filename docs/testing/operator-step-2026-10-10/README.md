# 单算子调试 A2 实施与验证记录

日期：2026-10-10。对应 [实施计划 A2](../../plans/2026-10-09-operator-step-and-workflow-debugger-v1.md) 和 [调试契约当前实现范围](../../operator-step-contract-v1.md)。A2 受限后端批次 COMPLETE；最终专项、相关回归与静态检查已结束。首轮全量失败与修正后验证分别保留，不能宣称最终源码全仓库测试全绿，也不能宣称整个单步调试功能已完成。

## 交付范围

新增 `apps/runtime/operator_debug/` 的契约校验、Worker、会话管理及 RPC 适配。通过 11 个独立 RPC 完成能力查询、开会话、输入准备、异步执行、结果/事件查询、取消、重置、续租与关闭。`RuntimeClient.operatorDebugCall` 支持嵌入式和 gRPC 入口，旧 Runtime 明确返回 E_DEBUG_UNSUPPORTED，不自动保存工程、跑整条流程或切换本地执行。

当前只开放可信内置来源的 Number 与 Compare Number。其他算子继续出现在能力清单中并显示不支持原因；图像资产、变量存储、模型/设备和写入适配属于后续批次。Designer 按钮、输入面板及结果界面尚未实现。

进程使用 spawn、独立临时工作目录和 JSON 字节管道；正式 Job 数据库、loadedDocument、工程文件和页面运行结果不变。沿用已有取消令牌、独立心跳和公共执行器，没有复用生产 JobRepository。进程、通信线程、句柄和临时目录未完全退休时不归还资源名额。

## 证据

分支 `agent/runtime-workflow-architecture-v1`，HEAD `61a42bf3ab181c3b2339f0425edfb40a60d0ec7b`，包含用户既有未提交工作。解释器仍为 `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`。

沿用 A0 的采集脚本，固定源码和静态检查的原始证据位于前一日期目录，使用独立 `a2-*` 标签，不覆盖 A0/A1：

| 验证 | 当前结果 | 原始证据 |
| --- | --- | --- |
| 修改前源码清单 | 已保存，含 dirty 状态和源码哈希 | [a2-before](../operator-step-2026-10-09/a2-before/source-before.json) |
| 首轮会话故障测试 | 18 PASS、2 FAIL，已定位并修正 | [a2-initial](a2-initial/pytest.xml) |
| 会话第二轮 | 20 PASS | [a2-sessions](a2-sessions/pytest.xml) |
| 首轮嵌入式/回环 RPC | 19 PASS | [a2-rpc](a2-rpc/pytest.xml) |
| 扩展故障边界 | 46 PASS | [a2-expanded](a2-expanded/pytest.xml) |
| 全量前专项 | 52 PASS，另一次 Aio 断连租约回收单例 PASS | [a2-final-focused](a2-final-focused/pytest.xml) |
| 首轮全仓库 tests | FAIL：4195 PASS、1 FAIL、8 SKIP、28 subtests PASS；1119.32 秒，源码无漂移 | [全量日志](../operator-step-2026-10-09/a2-full/pytest.log)、[源码核对](../operator-step-2026-10-09/a2-full/result.json) |
| 收尾修正后专项 | 58 PASS，51.55 秒，含原失败用例 | [a2-postreview-focused](a2-postreview-focused/pytest.xml) |
| 收尾修正后相关回归 | 980 PASS，390.06 秒 | [a2-postreview-regression](a2-postreview-regression/pytest.xml) |
| 重置稳定性复测 | 10 轮 PASS；每轮含去重、代次拒绝、状态重置与干净关闭，使用生产 3 秒宽限 | [复测日志](a2-postreview-reset-cycles.log) |
| Proto/Ruff | PASS | [最终静态记录](../operator-step-2026-10-09/a2-postreview-static/result.json) |
| mypy | BASELINE_FAIL：检查 378 个源码文件，仍为原有 5 个文件的 10 条错误；`: error:` 行与 A1 static-final 完全一致 | [最终 mypy 日志](../operator-step-2026-10-09/a2-postreview-static/mypy.log) |
| 最终源码一致性 | PASS：专项前与静态检查后的 src/proto/tests/scripts 哈希完全相同 | [验证汇总](verification.json) |

首轮的两个问题分别是：取消测试在算子进入之前取消，正确返回 CANCELLED 而不是预期的强杀 UNKNOWN，现改为观察 invoked 日志后再取消；Windows 管道在正常关闭时可能返回断管错误，不能覆盖已经确认的租约/关闭原因。原始失败 JUnit 保留。

首轮全量失败是 `testSpawnLifecycleDedupResetAndGenerationFence`：测试把正常退休宽限压至 150 毫秒，重置在 Windows 退出/IPC 排空期间被标记为强制退出。独立诊断复现了 `closedAck=true`、`forced=true`、`generation=1`。正常生命周期测试现使用生产的 3 秒宽限；实现也修正为只有实际对仍存活的进程请求 terminate 才设置 forced，已经退出但仍在退休 IPC 的进程不伪报强杀。新增确定性回归覆盖后一分支。

诊断原始输出 [reset-timing-probe-command.log](reset-timing-probe-command.log) 保留了复现，取得状态后人工中止，不作为通过证据。首次通过 stdin 启动的探针不支持 Windows spawn，已中止，记录在 [reset-timing-probe.log](reset-timing-probe.log)；该启动方式错误不是产品回归。

收尾还修正两项边界：事件按单条序列化增量累计分页，避免一批大日志先超限或首条大日志使游标停滞；正式 StartJob 在原有请求去重之后、SQLite 目标冻结/页面采集准备之前拒绝调试资源占用，保留后段原子检查。新增 ASCII/转义 Unicode 大日志以及两种准备路径的拒绝与重试用例。

全量的 8 项跳过为：6 项 Windows 符号链接权限不可用、1 项真实 Huaray 相机未启用、1 项仅用于非 Windows 的拒绝路径。跳过不计作验收通过。尚未在收尾修正后再次运行整个 UI 全量，最终结果按专项、相关回归和静态检查分别记录。

最终相关回归覆盖资源退休、正式 Job 生命周期、草稿/旧相机预览、PLC、纯预览取消、算子编辑器预览、StartJob 请求/快照策略、并行流程、连续生产、整个 Runtime presentation 测试目录和 SQLite writer 测试目录。最终两批 pytest 合计 1038 PASS；10 轮稳定性复测单独计数，不混入 pytest 数量。

源码审计相对 `a2-before/source-before.json` 只改变 8 个已有文件，并新增 5 个 `operator_debug` 模块文件与 3 个测试文件，无源码删除。A2 未再次修改公共执行器或正式 Runner；其他既有 dirty 改动全部保留。静态检查沿用原仓库配置，没有调整依赖或放宽检查。最终命令与源码快照链接见 [verification.json](verification.json)。

专项覆盖实际 spawn、成功/业务异常/大结果拒绝/进程崩溃、协作取消与强制退出、超时、独立续租与无人续租回收、并发重复请求、重置代次、结果淘汰不重放、去重配额满后退出、启动失败、清理失败仍占额、五次打开/关闭的进程/线程/管道/目录退休、严格 JSON、可信注册来源、旧 Runtime、草稿上下文以及正式/相机/PLC/页面入口互斥。

## 未验收边界

- A3 完整类型化资产和隔离状态、A4 Designer 交互、A5 长测与支持矩阵：NOT_STARTED。
- 实际相机、PLC、数据库写入、模型资源、冻结包、发布和部署：NOT_RUN。
- 父 Runtime 被强杀后的 Worker 自退与遗留目录恢复：NOT_RUN。已有父进程存活检查，但本批没有直接故障注入，也没有启动时清扫孤儿目录；Worker 崩溃与客户端断连用例不能代替这项验证。
- 1000 次执行、50 次会话、原生 UI 缩放/文字像素矩阵属于 A5；本批五次进程回收不能代替长测。
- 测试是源码验证，现有 Designer/Runtime 未重启；已运行的进程不会自动获得新增协议。
- 不提交、不推送，不改外部打包仓库，保留其他 dirty 改动。
