# 2026-10-25 工作区整理与提交验证

## 范围与固定源码

- 用户要求：整理当前工作区并推送现有分支，不合并、不强推、不打包或部署。
- 分支：`agent/runtime-workflow-architecture-v1`。
- 整理前 HEAD：`577450fc19c60ac86989475d76838e952bcc3e86`。
- 初始盘点：65 个已跟踪修改、82 个未跟踪文件，共 147 个文件；包含 49 个 PatchCore 历史证据文件，合计约 3 MB。
- 本轮按明确文件清单整理，未执行 `git add .`、`git clean`、重置或清理现场目录。`build/`、`manual_test_workspace/`、`field_tests/camera_ui/` 继续按已有规则留在本地。

前三个源码提交依次为：

| 提交 | 内容 |
| --- | --- |
| `e63a42cc195dafe4228f20b41edf2e0a499128fb` | PatchCore 1.1 推理与显示、YOLO 可选裁剪、多选一输入必需性、执行错误及 TCP/归档副作用诊断，保留历史测试证据 |
| `f4fc44b6006bfa66f9279f9606a4075ea169e9d2` | 独立 Runtime 显式加载/重新加载、目录切换、停机工程包更新和只读检查 |
| `3ebc90954d6514b4e5681eaf7b57812a63d23461` | Designer 运行/页面浮层、页面标签导航、工作区切换和测试设置隔离 |

配套开发指南、算子目录、可执行文档示例测试和本记录随最后的文档提交入库。历史报告继续描述其当时的未提交状态，不将后续验证倒填为早先已通过。

测试启动前仅补正两处类型表达，不改变业务行为：编译器对已知端口描述符使用 `Mapping` 类型转换；Runner 用 `setattr` 保留异常的动态 `diagnostics` 属性。初始 mypy 失败输出一并保留。

## 本轮实际验证

环境：Windows、Python 3.10.21、PySide2 5.15.2.1、NumPy 1.26.4、ONNX Runtime 1.23.2、ONNX 1.16.0；使用现有 `emo_master` Conda 环境，未安装或升级依赖。

证据目录：[2026-10-25-workspace-delivery](../evidence/2026-10-25-workspace-delivery/)。

| 检查层次 | 结果 | 证据与边界 |
| --- | --- | --- |
| 全库 Ruff | PASS | `python -m ruff check src tests` |
| 全量源码 mypy（禁用增量缓存） | PASS，536 个源码文件 | `python -m mypy --config-file mypy.ini --no-incremental src`；按仓库现有配置，不声称检查了未注解函数的全部函数体 |
| protobuf 一致性 | PASS | `python scripts/gen_proto.py --check`，未修改生成物 |
| 受影响源码/UI 回归（offscreen） | **1010 passed、1 skipped**，555.89 秒 | [XML](../evidence/2026-10-25-workspace-delivery/focused-regression.xml)、[输出](../evidence/2026-10-25-workspace-delivery/focused-regression.txt)、[路径清单](../evidence/2026-10-25-workspace-delivery/focused-test-paths.txt)；1011 个唯一用例，无重复执行，不是全仓 pytest |
| 文档示例更新后重跑 | **3 passed** | [XML](../evidence/2026-10-25-workspace-delivery/documentation-examples.xml)；这三个用例也在上述源码组内，不相加 |
| Windows 原生 Qt，100% | **95 passed、1 failed** | [完整 XML](../evidence/2026-10-25-workspace-delivery/native-100-complete.xml)；失败详见下一节，不能记为通过 |
| Windows 原生 Qt，150% | **96 passed**，173.38 秒 | [完整 XML](../evidence/2026-10-25-workspace-delivery/native-150-complete.xml)、[结果汇总](../evidence/2026-10-25-workspace-delivery/test-results-summary.json) |
| 插件实际注册 | PASS，77 算子、6 设备类型 | 无拒绝项或图标资源问题；仅导入/注册，未执行真实设备 |
| 文件检查 | PASS | 81 个变更 Python 文件可解析，2 个 JSON、13 个历史 XML 可解析；基础密钥模式扫描无命中，不等同于完整安全审计 |
| 证据完整性 | PASS | 49 个历史文件的暂存字节与初始 SHA-256 一致；`.gitattributes` 禁止这些原始证据换行转换，不改写或删去中间失败 |

唯一跳过项为 `tests.core.plugin.test_icon_resources::testSymlinkCannotEscapeRoot`，原因是 `symlinks unavailable in this environment`。跳过不计为通过。

源码组覆盖全部 37 个变更/新增测试模块，以及直接相关的插件合同、Runtime 配置替换/资源生命周期、算子生命周期、工作流导航及操作员窗口回归。完整路径和参数在证据中保留。原生组重复覆盖其中部分用例，不能将不同组通过数相加为唯一覆盖数。

## 本轮未修复：100% 原生 Qt 短窗口工具区

- 用例：`tests/designer/test_floating_toolbox.py::testShortCanvasCanScrollToEveryCategoryAndCard`。
- 触发：Windows 原生 Qt、`QT_SCALE_FACTOR=1`，窗口调整到 `640×319` 并展开工具区。
- 后果：工具头和标签区域占满过短画布，内容 viewport 可收缩为 0；滚动到分类后仍不可见。工具区整体尚在画布内，并不代表分类内容可操作。
- 证据层次：完整原生组出现一次失败，单用例重跑仍失败；额外等待布局处理及再次提升浮层均不能恢复可见性。已查看 [复现截图](../evidence/2026-10-25-workspace-delivery/native-toolbox-after-settle.png)，不是仅凭测试名称提出风险。
- 本轮判断：此次交付是现有工作区的源码整理推送，不是冻结 EXE/现场验收；在上述原生组其余 95 项和源码组通过的前提下，记录为**本次整理推送的非阻断小窗可用性缺陷**。这不等于该问题已修复，或所有原生 Qt 检查通过。
- 后续最小处理方向：在现有浮层内给短画布提供更紧凑或可滚动的工具头/标签区，保留可操作的内容高度；不靠永久禁用操作、限制正常流程或增加启动门槛掩盖。该方向本轮未实施或验证。

[首轮提前停止报告](../evidence/2026-10-25-workspace-delivery/native-100.xml)为 26 passed、1 failed；[单用例复现](../evidence/2026-10-25-workspace-delivery/native-toolbox-repro.xml)为 1 failed。二者保留，不与完整组叠加，不计为通过。原断言没有弱化、删除或跳过。

## 复现与交付边界

在仓库根目录使用项目 Python 3.10 环境：

```powershell
python -m ruff check src tests
python -m mypy --config-file mypy.ini --no-incremental src
python scripts/gen_proto.py --check

$paths = Get-Content docs/evidence/2026-10-25-workspace-delivery/focused-test-paths.txt
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest @paths -q -x --tb=short --timeout=120 --timeout-method=thread

$paths = Get-Content docs/evidence/2026-10-25-workspace-delivery/native-test-paths.txt
$env:QT_QPA_PLATFORM = 'windows'
$env:QT_SCALE_FACTOR = '1'  # 另一次设为 '1.5'
python -m pytest @paths -q --tb=short --timeout=120 --timeout-method=thread
```

基础检查、测试清单、固定源码与文件哈希详见归档 JSON/文本；没有把未执行或失败项记为通过。

未执行全仓 pytest、冻结 EXE 自测、打包/Release、真实 DINO 业务模型准确率、真实相机/PLC/机器人操作或部署。测试使用模拟器、回环服务、临时工程和合成 ONNX/图像。源码推送不会更新已有 EXE，也不修改现场工程或凭据。
