# dot 最小交接：不重复整套 Debian 验收

请仅审阅既有分支 `agent/runtime-workflow-architecture-v1` 的本轮 Windows 分层审计提交，先记录实际 SHA，并阅读同目录 REPORT.md、EVIDENCE-INDEX.json。此文档是交接材料，实际工作授权由用户决定。

本轮没有修改 src、capture预算、期限或原ABBA判定。新增有界只读审计器、28项纯数据测试、2项受控GUI时序测试、Windows专项覆盖和既有inspection测试的失败上下文；不是宣称capture根因已修复。**不需要再重复五路全生命周期、96场景、D01–D08整套Debian。**

如用户安排最小复核，仅做：

1. 校验新证据包及包内hash，离线运行审计器；四组全1–96保留，原FAIL/INVALID/NOT_ASSESSED不变。
2. 核对源及两独立消费者receive/decode完整；两窗口共享consumer0的hub，不把另一消费者忽略。
3. 只把trial0/68判为有直接live SUPERSEDED证据。trial1/60、94无paint和trial3/38无commit仍NOT_PROVEN；受控测试不能倒推历史因果。
4. 保留旧Windows精确提交CI失败与PR合并提交CI失败的不同身份。PR Linux图像BUDGET_EXCEEDED是真实源不可用，不被GUI latest免责；本地一次通过不能关闭。
5. 不自动改代码／合并／发布／部署。Windows新精确SHA CI由Codex负责，不要求dot替代。

关键词：**只读分层审计 / 全96含预热 / 双消费者 / 原FAIL保留 / GUI原因未证实 / capture与CI OPEN / 无src修改**。

旧Debian直接关闭后ownership只读采样仍属可选非阻塞补证，不把未采字段写成泄漏，也不把Windows自动化快照冒称旧Debian人工证据。
