# 最小交接：capture失败归还与Windows长路径回收

检查 `agent/runtime-workflow-architecture-v1` 本次交付的固定SHA（由Codex交付回复提供），先读同目录REPORT和EVIDENCE-INDEX；保留本地改动。Windows验证由Codex负责，不需要重跑旧整套Debian。

关键词：**capture raw retirement / encoder_reaped / not_dispatched / 恰好一次归还 / 后继reservation不被释放 / 500ms不变 / 全96含预热双消费者 / Windows长路径回收 / 首次失败保留 / 历史因果仍OPEN**。

本轮真正产品修复是：私有快照前失败后，旧编码器确认退出便归还raw，不再等callback/restart；未派发超时也提前退还。无法确认退出或release结果未知时仍隔离，不重试；busy/slab assignment不提前结束。另统一Runtime两个清理点的Win32 extended path。

如做Debian补充，只需固定SHA下的定向复验：NG持续运行时同调用图像/计数身份完整，失败/取消/关闭后原始lane与实际owner退休配对。真实Qt操作、自动化和故障注入分开记录；首个失败立即保存上下文，不重复择绿。capture/closure诊断含lastProducerAcquisition、pendingRaw、active ownerPhase和laneReleaseProof，快照非原子，不据此倒推历史失败。

不得盲增配额/期限、删除预热或缺号、借旧图补新结果；不能将新通过写成所有历史capture/Job/GUI原因已关闭。无需合并、发布、部署或连接生产设备。
