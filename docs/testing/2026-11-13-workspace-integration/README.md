# 工作区整理与 main 集成记录

日期：2026-11-13（Asia/Shanghai）。执行者：Codex。

## 起点与整理范围

- 初始分支：`codex/logging-consolidation-20261103`，HEAD `c8b76eec0980f53cbb9f4665754568054f677790`，包含未提交的 Web/Runtime/SDK、AGENTS、测试、交付材料及计划改动。不是该 SHA 干净版本的验收。
- 本轮获取的 main：`556acb10760a9463fe832aa4d8e7fc5c0a95a67e`，包含旧 HEAD，并领先 51 个提交。PR #9、#10 及后续修复已合并。
- 逐文件核对发现绝大多数本地源码与 main 相同，或完全匹配已合并祖先 `f4cf67cecce125873692173175064a7a037116a8`。旧交付措辞、末尾空行及缺少后续修复的测试采用 main 版本，没有将旧副本覆盖回去。
- 本轮真正新增源码是 `OperatorInfo` 的 `next_cycle_parameter_paths_json`、`min_core_version`、`max_core_version`，以及 `ListOperatorsReply.runtime_instance_id`。同步运行现有 protobuf 生成器；RPC stub 未改变。
- 这些字段仅完成协议声明与源代码生成。Runtime 服务尚未填充这些字段，未宣称算子能力发现、MCP 或 v6 计划已经实现；未升级或重新发布 SDK wheel。
- 归档 v6 计划包的 19 个文件，保持 25 项实施任务全部未勾选；使用精确路径的 `.gitattributes` 保持其原始字节与摘要。
- 补充归档旧独立 Web 编辑器的 121 项历史证据：100 张截图、19 个观测/状态 JSON、1 个历史工作区 patch、1 份用户请求文本。它们保留原始日期和验证范围，不作为本轮复测结果。

## 原始工作区保留

变更前已将 775 个现存文件及删除清单备份到本机 `D:/emo-workspace-backups/20261113-0910/`；包含未提交文件及相关目录内的原始忽略日志、缓存和交付文件。

- `initial-workspace.zip`：14,302,961 字节；CRC 与逐文件 SHA256 校验通过。
- ZIP SHA256：`214486127cc65a02420839be2ced0a909c36337c5c2452943c68d75e92c95111`。
- `manifest.json`、`initial-tracked.patch`、`initial-status.txt` 保留原始清单和差异；`integration-selection.json` 记录恢复选择。
- Git stash：`764fdb0f2793515947850ca08dc12a70314e4752`，说明为 `Codex workspace backup before main integration 2026-11-13`；保留未删除。该 stash 基于旧分支，不能无条件覆盖应用到新 main。
- 临时 Runtime 工程、JSONL、preview-cache 等保留在备份中；原有忽略文件和其他 worktree 未清理。

## 本轮验证与局部修复

测试基线为 main `556acb10760a9463fe832aa4d8e7fc5c0a95a67e` 加上述协议改动。首次回归保留在 `protocol-rpc-before.xml`：89 passed、2 failed。两项失败均为 main 中既有 ROI 测试硬编码 `1.1.0`，而当前 manifest 已为 `1.2.0`。

局部修复仅调整 `tests/runtime/test_operator_editor_preview.py`：以源 manifest 校验目录返回版本，再用目录/注册 descriptor 的版本请求编辑器资源。预览尺寸、资源摘要、工程隔离及注册快照断言全部保留。

修复后的源码提交为 `f663c007cb6df594e04f3488c9b9e81e491b54e0`，包含前一协议提交。测试运行时这些文件尚未提交，同时存在文档/历史证据改动；提交后确认三个源码/测试文件与受测内容一致。本记录不将整个测试过程描述为干净 worktree。

| 检查 | 本轮结果 |
| --- | --- |
| `python scripts/gen_proto.py --check` | 通过 |
| 旧/新隔离 DescriptorPool 兼容性 | 10 项断言通过，见 `protocol-compatibility.json` |
| Runtime RPC、算子目录/注册/图标/预览、相机配置及 Panel Contract | 91 passed，0 failed、0 skipped；14.17 秒，见 `protocol-rpc-after.xml` |
| 修改测试文件 Ruff 默认检查 | 通过 |
| `git diff --check` | 通过 |
| v6 原始包摘要 | 18/18 SHA256 匹配，覆盖除摘要文件自身外的全部文件 |
| v6 文档结构 | 5 能力、27 Requirement、40 Scenario、25 个唯一且未勾选任务；6 个相对链接存在、2 个 JSON 示例可解析、代码围栏完整 |

协议检查确认：仅新增四个 string 字段，移除后整个描述符与原 main 精确相同；旧消息在新读取器得到空默认值，新消息经过旧读取器二进制往返保留新增字段；`"[]"` 与缺失元数据的空字符串可以区分。生成文件除描述符及对应偏移外没有行为变化。

复现命令（Windows，产品 Python 3.10.21，PySide2 offscreen；模拟资源和临时工程）：

```powershell
$env:PYTHONUTF8='1'
$env:QT_QPA_PLATFORM='offscreen'
$env:QT_SCALE_FACTOR='1'
$python='D:/Conda/envs/emo_master/python.exe'
& $python scripts/gen_proto.py --check
& $python -m pytest tests/runtime/test_operator_port_specs.py tests/runtime/test_runtime_grpc_service.py tests/runtime/test_operator_registration.py tests/runtime/test_operator_icon_assets.py tests/runtime/test_operator_editor_preview.py tests/runtime/test_camera_configuration_rpc.py tests/core/presentation/test_panel_contract.py -q --disable-warnings --maxfail=3 --junitxml=build/workspace-integration-20261113/protocol-rpc-after.xml
& $python -m ruff check tests/runtime/test_operator_editor_preview.py
```

## 已有 CI 与未验证范围

本轮只读核查 [main 556acb 的 CI](https://github.com/jsdfhasuh/emo_master/actions/runs/43195174649)，确认其在本轮整理前已经失败：

- Linux/Windows runtime 分片均有上述两项 ROI 版本失败；本轮已局部修复并完成相关回归。
- other 分片仍有四项 ROI UI 测试访问已移除的 `_image` 私有属性，以及 YOLO 小窗口控件裁切（Linux 460/620、Windows 460 宽度）。这些是既有 main 问题，不由本轮协议声明引入；本轮未扩大到这些 UI 整改，也未宣称全仓 CI 通过。
- 同一 SHA 的 [独立 Web CI](https://github.com/jsdfhasuh/emo_master/actions/runs/43195174473) 成功；这是既有远端证据，不计入本轮 91 项。

未运行全仓 pytest、官方 OpenSpec CLI、Mermaid 渲染、已安装 wheel/冻结包、人工 Qt UI、异机 LAN 或真实相机/PLC 验收。没有执行 v6 计划实施、发布、部署、设备配置修改或现场工程操作。新提交的远端 CI 状态以对应 GitHub PR/run 为准，本记录只声明上述本地检查及明确标注的既有远端结果。
