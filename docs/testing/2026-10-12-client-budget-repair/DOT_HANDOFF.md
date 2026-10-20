# 本轮固定提交复验

以 Codex 交付消息给出的完整 SHA 为唯一产品基线，先核对 `git rev-parse HEAD`，保留原工作区和所有首次失败；不要直接以滚动分支、PR merge 或旧进程替代。

先读同目录 REPORT.md 与 VALIDATION.json。本轮授权的变化是 P0 客户端 receive/decode/verify 总预算 **550 ms**；原 36b4617 在 500 ms 下的失败保持有效。asset RPC/read、export、seal 均仍为 500 ms，所有性能门槛、SQLite 预算与捕获额度不变。

## 核心验收

1. 运行新增 `tests/p0/test_client_deadline.py` 和原双网络消费者测试。核对 550 ms 边界、超过边界拒绝、坏图拒绝、同 resultKey/ordinal/assetId、失败使旧结果失效。两路每一路都必须完整覆盖，不能合并计数。
2. 若任何结果 UNAVAILABLE，保存 read_phases、read_error_type、原始完整行及 server/export 现有身份；缺阶段为 unknown。不得把客户端总预算失败、asset RPC 失败、服务端等待失败合并成同一个根因。不要为补 taskId 伪造关联。
3. 检查 r3 包装器不再因 `_runNode` 新参数失配导致 0 executed。完整 ABBA 的 96 是总输入，含 8 预热、88 计量；与 P0 的 96 计量+8 预热口径不同。
4. 四个 arm 均须保存 Job 终态、错误码、失败事件和通道结果。即使 Job COMPLETED，少一张图仍是 INVALID。Codex 本机第 3 arm ordinal 75 为 BUDGET_EXCEEDED，这条首次失败不得抹去。
5. 核对精确 SHA push 的 CI 与 credit diagnostic。公开 artifact 仅包含 allowlisted 脱敏 JSON；worker 错误原文只在本地有界 trial 内保留，不能为采证上传任意原始日志。采证或 watchdog PASS 不代表 workload PASS。
6. 严格 P0 保持 `--require-performance --count 96 --warmup 8 --rounds 3`、原 5 组、原图像/PNG/5Hz 负载。性能测试与 pytest 等负载错开；记录 CPU/其他负载边界，不挑窗口、不重跑取绿。新 550 ms 不改变 execution p95 5% 门槛或 live age 500 ms 门槛。
7. 回归 4/5 路 While、SQLite 并发与保存重载、NG 同调用身份和释放。PR merge 的 busy 与产品 SHA 分开；不能凭一次通过关闭历史根因。
8. D01–D08、ROI 四路图像（含 maskedImage）、O20 原生操作按实测记录；自动化、真实鼠标 Qt、故障注入和设备分别标记。未执行就是 NOT_RUN。

建议 Windows 统一 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`；诊断/pytest 临时目录用短的新路径，禁止覆盖旧证据。不要修改测试、预算、阈值或安全检查，不连接生产设备；只复验，不提交、合并、发布或部署。

关键词：固定SHA、客户端总预算550ms、服务端500ms不变、read_phases、boundValues透传、终态错误留存、脱敏单文件artifact、ordinal75捕获拒绝、ABBA96含8预热、P0严格退出、首次失败保留、历史根因OPEN。
