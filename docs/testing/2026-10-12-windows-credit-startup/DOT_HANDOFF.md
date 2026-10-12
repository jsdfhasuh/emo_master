# dot 复验关键词

拉取 agent/runtime-workflow-architecture-v1，记录实际完整 SHA、dirty 状态和新进程来源。
先阅读本目录 REPORT.md；Windows Actions 由 Codex 处理，不要求 dot 替代 hosted 诊断。

重点：捕获额度 / 96 个源图 / 两消费者逐号读取与像素一致 / NG 同调用图像 / 5 路 While /
SQLite 保存重载 / D01–D08 / 暂停继续 / ROI 四输出与 maskedImage / 额度只归还一次 /
异常、超时、取消后资源回收。

源图 AVAILABLE、客户端收到/解码、live 应用、Qt 提交/paint 分开统计；不要用任一层
代替另一层。v3 增加消费者和窗口就绪后的首输入握手，仅用于诊断，不代表冷启动通过。
保留第一轮失败、缺失 ordinal、完整身份、实际期限及原始证据；不重跑挑选结果。
服务器导出/资产 RPC 500ms，客户端接收/解码 550ms。真实桌面、脚本化 Qt、故障注入
分别记录。不要连接生产设备，不合并或部署。
