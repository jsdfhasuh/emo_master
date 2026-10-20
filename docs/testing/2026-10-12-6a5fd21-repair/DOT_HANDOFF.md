# dot 固定提交复验提示

请使用 Codex 最终交付消息给出的完整 SHA，不以浮动分支或 PR merge SHA 替代。先记录 `git rev-parse HEAD`、工作区状态、解释器和新进程身份；保留旧工作区与首次失败。

Windows 验证进程应统一使用 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，避免子进程 UTF-8 输出被父进程默认 GBK 解码；本轮原全量的该 warning 保留，不借环境修正改写首次结果。

本次基线为 `6a5fd216180ebdec3159a7e9808147d953e32f69`。本轮改动是**首结果可观测性与验收结果判定**，不是已经证明修复 Windows 首结果超时或 P0 性能。先读同目录 REPORT.md 和 evidence.zip。

## 1. Windows 首结果 / FIRST_CAPTURE_TIMEOUT

- 先核对交付 SHA 的 push CI，不能用 PR merge 成功替代。
- 原用例 `tests/runtime/presentation/test_normal_capture.py::testExplicitStopReleaseAndRestartNormalJob` 保持 StartJob 返回后 10 秒 deadline 和 20ms 轮询；用例内部启用 `EMO_PRESENTATION_TRACE=1`。失败输出增加 `FIRST_CAPTURE_TIMEOUT` JSON，原断言与 `jobFailureDetails` 保留。
- 手工诊断时，仅对新测试进程设置该环境变量；默认关闭。性能测量不启用它。
- producer 记录 scope.begin、source.observed、scope.end、open/image/seal sending/sent/send_failed；parent 记录 reader、锁等待、export/adopt、seal、finish 等待原因、close/校验失败。
- 必须按 runtimeInstanceId/jobId/resultScopeId/invocationId (= workflowRunId)/resultKey/resultOrdinal/sourceId 关联；PID/TID、顺序号和单调时钟一起解释。
- 每侧只保留最早 64 条，单条 2048 bytes；生产侧独立共享区不依赖原事件/展示队列，父侧不等待诊断锁。只追踪各 scope 的首 ordinal，满额保留最初记录并标记 truncated。不能把缺失、截断、观察锁不可用解释为“该阶段没有发生”。
- `assertionObservedNs` 与随后 snapshot 的观察时刻必须分开。state 是有界样本（最多 16 个 open、32 个 latest 的扫描范围），不是全局原子视图；不得把稍后 AVAILABLE 强行解释为断言当时已经闭合。
- 原失败发生在 StopJob 前；单次 SQLite commit 栈不证明持锁根因。没有证据则保持 OPEN，不增加 deadline、不删断言、不循环刷绿。

## 2. P0 严格判定 / --require-performance

在没有其他测试套件并发的受控主机执行原完整配置：

```text
python scripts/p0_validate.py --scenarios benchmark --count 96 --rounds 3 --warmup 8 --require-performance --output <全新证据路径>.json
```

- 负载、五组、轮换顺序、5Hz、预热、5% execution p95 门槛、500ms export/read/seal 预算均未修改。
- performance 为 FAIL 或 NOT_RUN 时，严格模式必须 exit 1，仍保留所有原始窗口和标准错误；execution=PASS 只指 harness 完成。
- 默认报告模式仍允许性能 FAIL 时 exit 0（用于完整收集数据），但会明确打印各维度；correctness 非 PASS 即使在报告模式也 exit 1。
- `p0_exit=NOT_EVALUATED` 仍然保留，不能把局部软件脚本当 Qt/硬件/发布总验收。
- 此轮没有采用 PNG 参数优化：离线候选更慢，原型 exporter 与产品 exporter 均未改动。不要报告“P0 已加速”。
- 本机本轮基线中 original 与 new-no-presentation 实际调用同一 disabled 路径却大幅波动，不能用本机前后数字证明修复。保留全部 FAIL，隔离环境后再判断；不要只挑通过窗口。

## 3. 回归与边界

回归新阶段诊断故障注入、seal Full 恢复、迟到回调、结果校验失败、额度恰好归还一次，及 D01–D08、ROI 四路导出、O20 新库两次 COMMITTED 与保存重开、4/5 路 While 图像、双消费者完整覆盖、NG 同 invocation 图像及资源释放。

真实 Qt 操作、offscreen 自动化、故障注入、性能基准分别记账。历史本机 NG→INCOMPLETE、网络 ordinal20、Windows While busy 和首展示根因不能凭一次通过关闭。Windows 实体桌面、安装卸载、DPI、外设未做则继续 NOT_RUN。不得连接生产设备。

本轮只验收和记录；不要擅自修改代码、推送、合并、发布或部署。关键词：`固定SHA`、`FIRST_CAPTURE_TIMEOUT`、`EMO_PRESENTATION_TRACE=1`、`seal与export分账`、`同调用身份`、`--require-performance`、`96/8/3/5`、`首次失败保留`、`历史根因OPEN`。
