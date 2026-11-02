# Panel SDK 1.1.1：普通 Job 空运行组合同

交付日期：2026-10-31。任务分支 `codex/panel-protocol-ungrouped-fix` 从已审查 Web 提交
`828633688aafff301e16333bb0a94d07b0194ed0` 建立。Web 原有代码保留在该基线上，SDK 修复独立交付。

| 项目 | 固定值 |
| --- | --- |
| 源码实现提交 | `95c6371542f73a725c0ca2fd585152c728338dbf` |
| 产物交付提交 | `b9a741f31a7304a3b2902530a9b6acd98755285d` |
| SDK | `emo_panel_protocol 1.1.1` |
| Panel Contract | `1.0` |
| 工程 / Presentation | `2.9` / `1.2` |
| ZIP | [panel-contract-v1.zip](./panel-contract-v1.zip)，90,424 字节 |
| wheel | [emo_panel_protocol-1.1.1-py3-none-any.whl](./emo_panel_protocol-1.1.1-py3-none-any.whl)，38,038 字节 |
| manifest | [contract-manifest.json](./contract-manifest.json) |
| ZIP SHA-256 | `8f0efaf9b918d44684c5c8c8b3f0d67fee740179ea33d17c2a064c801d9d3b7a` |
| wheel SHA-256 | `aed06e1badb4f3fedcca121413391dbf88168cc02dc37fc54f65cbf089c92858` |

产物由源码实现提交的干净工作区生成。manifest 的 `sourceSha` 为上述实现 SHA、`sourceDirty=false`，
列出 39 个交付文件的长度和 SHA-256；ZIP 共 40 个文件（包含 manifest）。同目录 wheel、manifest
均逐字节复制自生成输出。交接说明在产物提交之后记录其实际 SHA，因此文档提交和产物交付提交分别可追溯。

## 本次合同变化

`RunGroupId` 表示 `""` 或现有非空 Id。空字符串是普通 Job 的真实未分组身份；非空组仍须无空白且不超过 160 字符。
工程、Job、Runtime 实例等其他 Id 的约束没有放宽。

| 消息字段 | 1.1.1 语义 |
| --- | --- |
| `BrowserJobState.runGroupId` | 必填字符串；普通 Job 为 `""`，分组 Job 为真实组 ID；拒绝缺省/null |
| `BrowserJobChanged.runGroupId` | 同上，切换到普通 Job 也明确发送 `""` |
| `BrowserControlResult.runGroupId` | 已知普通 Job 时发送 `""`；继续允许 null/缺省表示没有返回组身份 |

BrowserMessage JSON schema 与 TypeScript 从同一模型生成，范围和正则校验仍由 schema/SDK 执行。
新增五个普通 Job fixtures：`ungrouped-running`、`ungrouped-job-changed`、`ungrouped-start-accepted`、
`ungrouped-stop-accepted`、`ungrouped-aborted`。索引同步生成；原分组样例逐字节保留。
停止接受的 control_result 为 `ACCEPTED`，之后的停止终态示例为 Runtime 实际使用的 `ABORTED`。

Runtime 读模型、Dispatch 分组和精确 Stop 实现沿用现状。
`expected_run_group_id=""` 是明确核对普通 Job 身份，必须保留 optional 字段的 presence；不要省略或替换成 jobId/null。

## Dot 接入步骤

1. 从本任务分支取得并核对本目录 ZIP 与 wheel 摘要。将 ZIP 完整解压到 Web 的
   `web_panel/contracts/panel-contract-v1/`，用同一个 manifest 更新 schemas、TypeScript、fixtures 和样例。
   该目录的 `python/` 只保留此次 1.1.1 wheel，以便现有 CI 的通配符安装选择同一版本。
2. 将 `web_panel/pyproject.toml` 的 `emo-panel-protocol==1.1.0` 更新为 `==1.1.1`，安装本次 wheel；
   同步 Web README 和 `web_panel/tests/test_contract_manifest.py` 中固定的源码/版本/摘要。
3. 将同包 fixtures 同步到 `web_panel/backend/emo_web_panel/contract_data/fixtures/`，消费新的生成类型与 schema。
4. 普通 Job 的 job_state、job_changed、control_result 使用正式 `BrowserMessage` 校验和发送；
   移除 `contractStatus=UNGROUPED_JOB_SCHEMA_GAP` 临时消息分支。不要用运行组字符串真假判断消息是否可以进入正式合同。
5. 保留原 `RuntimeClient.control` 的空组精确 Stop 字段。Web 操作状态与停止终态处理按 `ABORTED` 和权威资源状态接入，
   这部分代码由 Dot 实现；停止 ACCEPTED 不意味着任务已退出。
6. 使用新普通 Job fixtures 和保留的分组 fixtures 回归消息、切换与控制链路，再完成 Web 浏览器集成验证。

可从仓库根目录安装独立 SDK：

```powershell
python -m pip install docs/handoffs/runtime-web-panel-v1/sdk-1.1.1/emo_panel_protocol-1.1.1-py3-none-any.whl
```

## 验证与边界

- 源码合同、生成 schema/fixtures、Runtime 读模型/精确 Stop，以及既有本机 gRPC + 原生 Qt 精确停止：**66 passed，13.12s**。
- 干净 venv 仅安装 wheel 和声明依赖，使用 `-I` 并阻断 Qt、emo_master、设备/推理库导入：**PASS**。
  在隔离环境验证三类空组消息、null/缺省控制结果、ABORTED 以及 Stop optional 空组 presence。
- 生成 TypeScript 5.8.3 编译、SDK mypy（11 文件）、定向 Ruff、proto 一致性：**PASS**。
- manifest 摘要/长度、完整 ZIP、wheel 源码与版本元数据：**PASS**；其他 schema 和原分组 fixtures 与 1.1.0 逐字节相同。
- 样例模型、保存重读、工作流编译与采集定义准备：**PASS**；样例实际 Designer 打开：**NOT_RUN**。

未执行全项目 pytest、Web 依赖升级后的浏览器集成、另一台实体电脑、冻结安装包、真实相机/PLC 或部署。
修复前的 **3 failed / 6 passed** 记录及完整验证范围见[本轮实施记录](../../../testing/2026-10-31-panel-protocol-ungrouped-fix.md)。
SDK 1.1.0 的原始包、摘要与交付提交仍保留在[上级历史交接](../README.md)。
