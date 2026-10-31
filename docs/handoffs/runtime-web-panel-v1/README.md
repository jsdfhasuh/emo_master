# Runtime / Web Panel：Dot 开发交接

交接日期：2026-10-31。Runtime、Designer、共享 SDK 和本地发布/预览接入已经实现，Dot 从本交接开始开发 Web 网关和浏览器页面。

| 项目 | 固定值 |
| --- | --- |
| 仓库分支 | `codex/logging-and-designer-improvements` |
| 实现源码提交 | `d4ab96409e43d7ab6ae566820da357508d01658c` |
| Panel Contract | `1.0` |
| Python SDK | `emo_panel_protocol 1.1.0` |
| 工程 / Presentation | `2.9` / `1.2` |
| 交接包 | [panel-contract-v1.zip](./panel-contract-v1.zip)，87,604 字节 |
| ZIP SHA-256 | `2ac8435d0aee3323413be980262eeae97a0aa0282f859ca3e77e6753915b2c5c` |
| wheel SHA-256 | `bb0cff1f007b8c6b850c8e43d792200ee6adebbaa4af23c4a966362b74e697f9` |

ZIP 从上述干净实现提交生成，`contract-manifest.json` 记录 `sourceDirty=false`，包含 34 个交付文件的摘要。
包内包括 SDK wheel、proto、JSON schemas、TypeScript 类型、合成 fixtures、canonical 向量及当前格式样例页面包。
源码和文档按该提交核对；后续更新共用合同须同步版本与生成材料。

## 开发入口

1. 阅读 [Dot Web 开发计划](../../plans/2026-10-30-web-panel-dot-development-plan.md)，按 W1–W6 实现。
2. 阅读 [Panel Contract v1](../../development/panel-contract-v1.md)，以其身份、状态、组件与发布语义为准。
3. 解压 ZIP，安装 `python/emo_panel_protocol-1.1.0-py3-none-any.whl`，复用生成协议与 DTO；前端消费 `typescript/panel-contract.ts` 和同包 schemas/fixtures。
4. 先完成 SDK/fixtures 适配和 renderer，再连接 [Runtime 远程访问入口](../../guides/runtime-remote-access.md)。本地已有测试层级和限制见 [实施记录](../../testing/2026-10-30-runtime-web-integration/README.md)。

## 共同约定

Web 网关作为普通 RPC 客户端连接 Runtime。生产执行、设备和正式在线工程配置由 Runtime 管理；浏览器通过网关固定业务接口操作。
受控局域网沿用明文 gRPC。本轮不新增 TLS/证书管理。

Dot 负责网关、浏览器 renderer、会话/权限、共享订阅、页面包导入激活/回退，以及独立安装的预览 CLI。
页面布局复用现有 Presentation 响应式合同；不另建一套页面设计器。

预览 CLI 必须支持 SDK 1.1.0 的明确数据源交接：

```text
emo-web-panel preview --package PATH --context CONTEXT_JSON --host 127.0.0.1 --port 0 --json --no-control
```

`PreviewContext.mode` 为 `fixtures` 或 `runtime`。在线预览核对包、实例、工程与正式历史声明，并让用户选择已有运行；
两个预览模式均只读。正常 Web 生产控制另按合同实现 Start 请求身份、原 Job 精确 Stop 和未知结果核实，不自动补执行。

目前本地源码、本机 gRPC 和原生 Qt 的定向验证已完成；Dot renderer、真实浏览器、另一台实体电脑及冻结安装包的联合验收尚未执行。
