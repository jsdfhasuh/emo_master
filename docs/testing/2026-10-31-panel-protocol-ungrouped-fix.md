# Panel SDK 普通 Job 空运行组修复

日期：2026-10-31。任务分支：`codex/panel-protocol-ungrouped-fix`。

## 基线与范围

开始时工作区干净，当前分支为 `codex/logging-and-designer-improvements`，HEAD 是原 SDK 交付
`b9f448c18ed297652cddc3e040ca1db847b0fbfa`。拉取后远端为用户已审查的 Web 提交
`828633688aafff301e16333bb0a94d07b0194ed0`。中间五个提交仅涉及 `web_panel/` 与 Web CI，
未修改共享 SDK、Runtime 或合同生成器；本任务从 82863368 新建独立分支，保留这些成果。

原 Runtime 实现为 `d4ab96409e43d7ab6ae566820da357508d01658c`。
本次修复 SDK 的身份表达及交付材料，沿用 Panel Contract 1.0、工程 2.9 / Presentation 1.2。
未改 Web 源码、Runtime 调度、分组或停止实现。原 SDK 声明为 1.1.0，相关远端分支未占用 1.1.1。

## 实现与字段核对

- 新增专用 `RunGroupId`：明确空字符串，或现有非空 `Id`；非空最大长度仍为 160。
  JSON schema 显式排除空白，避免部分正则引擎的 `$` 在末尾换行前匹配。
- `BrowserJobState` / `BrowserJobChanged` 的运行组字段继续必填，不接受 null；
  `BrowserControlResult` 保留 null/缺省，同时接受普通 Job 的 `""`。
- `runtimeInstanceId`、`projectId`、`jobId` 等其他 Id 沿用原约束。
- `JobRecord` / `JobSnapshot`、`RunState.run_group_id`、`DisplayJob.run_group_id` 已能表达空组；
  `StopJobRequest.expected_run_group_id` 是有 presence 的 optional 字符串，明确空组已有精确比较。
  `DispatchQueueRequest` 和双路 Dispatch Coverage 表达真实分组，不需要放宽。
- SDK 与 wheel 版本统一为 1.1.1。生成器保留旧分组样例，新增五个 `ungrouped-*` 样例，
  其中停止接受为 control_result/ACCEPTED，退出后的状态为 job_state/ABORTED。
- 生成器增加 `--archive`，按生成 manifest 校验并归档完整合同。`jsonschema` 只加入开发测试依赖。

## 定向验证

修复前运行空组及非空组的直接回归：**3 failed / 6 passed，2.99s**。
三项失败分别来自 job_state、job_changed、control_result 的空运行组长度校验；非空身份通过。
原始输出保留在本地 `build/panel-protocol-ungrouped-fix/before-model-fix.log`。

修复后执行：

```powershell
python -m pytest tests/core/presentation/test_panel_contract.py tests/runtime/test_run_catalog.py tests/designer/test_remote_runtime_lifecycle.py::testExplicitDesignerStopUsesAcceptedRunIdentity -q
```

结果 **66 passed，13.12s**。覆盖空组、分组与长度边界、空白/换行/非法类型拒绝、必填与可空区别、
其他身份非空、正式生成 schema、fixtures/索引和 ZIP 一致性。原普通 Job 的身份从 Runtime 读模型
经 BrowserJobState 原样进入 Stop，optional 空组字段明确存在，停止后为 ABORTED，另一运行保持 RUNNING。
既有实际本机 gRPC + 原生 Qt Designer 精确停止测试也通过。使用临时工程和模拟设备。
日志：`build/panel-protocol-ungrouped-fix/targeted-regression.log`。

相关源码/测试/脚本 Ruff 通过，SDK mypy **11 source files** 通过，`scripts/gen_proto.py --check` 通过。
正式产物的固定源码 SHA、独立 wheel 安装、文件摘要与 TypeScript 验证随交付完成后记录于交接入口。

没有执行全项目 pytest、Dot 浏览器集成、另一台实体电脑、冻结程序、真实相机/PLC 或现场部署。
Web 依赖更新、临时消息分支移除和 Web 操作状态处理由 Dot 接入本交付后完成。

## 固定交付结果

源码实现提交：`95c6371542f73a725c0ca2fd585152c728338dbf`。
生成产物交付提交：`b9a741f31a7304a3b2902530a9b6acd98755285d`。
交接文档随后记录这两个实际 SHA；完整 [SDK 1.1.1 交接入口](../handoffs/runtime-web-panel-v1/sdk-1.1.1/README.md)
包含版本、ZIP/wheel 摘要、manifest 和 Web 消费步骤。

在源码提交后，干净工作区使用 `scripts/build_panel_contract.py --output build/panel-contract-v1-sdk-1.1.1
--archive build/panel-contract-v1-sdk-1.1.1.zip` 完整生成。manifest 记录实际源码 SHA、`sourceDirty=false`、
SDK 1.1.1 / Panel Contract 1.0 / 工程 2.9 / Presentation 1.2，39 个交付文件及其摘要。
ZIP 为 90,424 字节，wheel 为 38,038 字节；旧 1.1.0 包字节与摘要保持不变。

正式产物检查通过：manifest 每个文件的长度和摘要、ZIP 全内容/testzip、wheel 每个 Python 源文件与 owning source、
wheel 元数据版本、全部普通 Job fixtures 的正式 JSON schema 校验。
与旧交付比较，原分组 fixtures 及 browser-message 以外的所有 schema 均逐字节相同。

新 wheel 已在干净临时 venv 中安装，仅使用其声明依赖，以 `-I` 并阻断 emo_master、PySide2、cv2、numpy、
onnxruntime、PIL 导入运行 DTO 校验；三类空组身份、可空控制结果、ABORTED 与精确 Stop 的空组 presence 均通过。
该独立环境实际使用 Pydantic 2.14.2 / grpcio 1.78.0 / protobuf 6.33.6。
日志和结构化结果在本地 `build/panel-protocol-ungrouped-fix/delivery-verification.log`、`delivery-verification.json`。
生成类型经 TypeScript 5.8.3 `tsc --noEmit --target ES2020` 检查通过。
