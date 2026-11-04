# SDK 1.4.0 日志查询开发交付

基线 `940994b3420b47ff0add8766ccce5a4f2686a02a`，本轮源码修改未提交，`sourceDirty=true`。新增 `QueryRuntimeLogs` 和事件应用、模块、实例字段。当前消费合同位于 `web_panel/contracts/panel-contract-v1`；SDK wheel、完整合同及 ZIP 位于本机 `build/logging-consolidation-panel-contract-sdk-1.4.0` 与同名 ZIP。历史 1.3.0 交付证据保留。

生成入口：`python scripts/build_panel_contract.py --skip-proto --output build/logging-consolidation-panel-contract-sdk-1.4.0 --archive build/logging-consolidation-panel-contract-sdk-1.4.0.zip`。验收与限制见 `docs/testing/2026-11-03-logging-consolidation/README.md`。此记录不代表提交、发布或部署。
