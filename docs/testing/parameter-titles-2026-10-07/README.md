# 算子参数中文名称验证记录

日期：2026-10-07。源码基线：`6aa57f3e21fd207a65047f532feb92a3b82de1ae`，验证对象为该基线上的当前工作区。工作开始时已有 PLC 调试相关未提交改动，本次保留并在相关编辑器上追加参数显示适配。没有提交、推送、外部打包、发布或真实设备验收。

## 实现结果

- 50 个内置算子全部注册成功，295 个顶层参数和 2 个数组元素属性均具有中文 `title`。
- 通用表单及相机、ROI、直方图、PLC、SQLite 专用编辑器显示中文名称；标签和参数输入控件提供原始键名提示。
- 旧工程仅在编辑器显示用的 schema 深拷贝上合并标题；参数键、值、默认值、约束和工程格式保持兼容。
- 相机分组、既有中文选项和字段联动保留。PLC 高级区参与可用高度分配，展开可见，640×430 小窗口仍满足现有布局测试。
- [元数据审计](metadata-audit.json)：50 份清单的 `paramSchema` 去掉 `title` 后与基线相等；37 个 Python 算子文件的 AST 去掉标题注解后与基线相等。记录包含这些文件的 SHA-256。

## 自动化结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| protobuf 漂移检查 | PASS | `ci-tests.txt` |
| Ruff（src、tests、截图脚本） | PASS | 已对最终源码执行 |
| mypy | PASS，336 个源码文件 | 已对最终源码执行；保留既有 untyped-body 提示 |
| 全仓库首次正式 tests 运行 | 3624 passed、1 failed、8 skipped、28 subtests passed | [完整输出](ci-tests.txt) |
| 最终 Designer 整组 | 710 passed | [输出](designer-utf8-final.txt) |
| 最终 PLC 编辑器及目录锁测试 | 113 passed | [输出](plc-layout-final.txt) |
| 表单、旧工程、相机、注册 RPC、SQLite 等定向回归 | 57 passed | [输出](focused-preload-fixed.txt) |
| 新增测试在 150% 缩放下复测 | 11 passed | [输出](focused-scale150.txt) |
| 最终元数据、注册 RPC、SQLite 与编码告警用例复测 | 23 passed | [输出](contracts-rpc-sqlite-final.txt) |

上表不同测试集有重叠，不应相加作为独立用例总数。全仓库运行发生在最后的 PLC 高级区布局调整之前；该调整由最终 PLC 与 Designer 整组测试覆盖。没有把首次全量运行标记为 PASS，也没有将跳过项计为通过。

全仓库唯一失败为 `test_second_designer_explains_busy_directory_and_can_retry_after_release`：子进程继承 `PYTHONIOENCODING=utf-8`，父进程 `subprocess.run(text=True)` 仍按 GBK 解码，导致读取线程 `UnicodeDecodeError` 和 `stderr=None`。同时启用 `PYTHONUTF8=1` 后，该测试单独及在最终 Designer 整组中通过；没有修改目录锁代码或削弱断言。第一次直接调用 CI 时，默认 pytest 还扫描了本地 `manual_test_workspace` 的历史源码副本而发生收集冲突；正式全量运行通过 `PYTEST_ADDOPTS=tests` 明确使用仓库测试目录，未修改 CI 脚本。

新增测试的初期导入顺序曾引发 ONNX Runtime DLL 诊断。已按仓库惯例先导入 `emo_master` 再导入 Qt；保留原始 [初期诊断输出](focused-final.txt) 和上述修复后输出。开发中的固定高度尝试在六个 PLC 小窗口用例失败，原始输出保留于 [plc-final.txt](plc-final.txt)；最终实现采用布局剩余空间分配，全部 PLC 测试通过。

## 原生 Qt 检查

最终证据位于 [100% 报告](accepted-100/report.json) 和 [150% 报告](accepted-150/report.json)。使用 Windows 原生 Qt、实际 `OperatorWorkspaceWindow`、无硬件调用的编辑器上下文。七类表单分别在 640 和 1100 逻辑像素宽度、两档缩放下检查，共 28 个布局用例，标签高度检查全部通过。

- [ROI 窄窗口](accepted-100/roi-640.png)
- [相机窄窗口](accepted-100/huaray_camera-640.png)
- [直方图，150%](accepted-150/histogram-640.png)
- [嵌套长标签，150%](accepted-150/nested-640.png)
- [SQLite 窄窗口，150%](accepted-150/sqlite_writer-640.png)
- [PLC 高级区，150%](accepted-150/plc-advanced.png)
- [英文参数键悬停提示](accepted-150/tooltip.png)

开发阶段截图保存在本地 `manual_test_workspace/parameter-titles-20261007/development`，不作为最终界面验收结果。这里的 PASS 只表示本机源码和模拟界面验证，真实相机、PLC、外部打包与现场验收均为 NOT_RUN。

## 复现

使用现有 Python 3.10 `emo_master` 环境，不安装或升级依赖。PowerShell 中统一设置测试进程编码并明确测试目录：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTEST_ADDOPTS = 'tests'
& "$env:USERPROFILE/.conda/envs/emo_master/python.exe" scripts/ci_check.py
```

原生截图通过 `scripts/validate_parameter_titles.py --output <新目录>` 生成，分别设置 `QT_SCALE_FACTOR=1` 和 `1.5`。该脚本不启动检测任务，不连接相机或 PLC，也不写业务数据库。
