# 相机节点草稿预览修复验证（2026-10-08）

## 范围和候选

- 起点：`6aa57f3e21fd207a65047f532feb92a3b82de1ae`。
- 隔离分支：`codex/camera-draft-preview`。只包含相机预览修复，不包含原工作区尚未提交的 PLC、参数中文标题、工作流接口和批量图片节点等工作。
- 原工作区已应用同样的修复；未切换、重置、暂存或提交其其他修改，未终止用户正在运行的 Designer。
- `scoped-source-manifest.json` 记录独立候选的 15 个源码、协议、测试和说明文件的 SHA-256。生成的 protobuf 与该分支的协议源一同检查。
- 相机算子类型继续使用现有注册目录；新增节点实例通过独立草稿预览 RPC 校验和创建临时会话，不覆盖 Runtime 正式加载工程，不创建 Job，不自动保存草稿或临时参数。

## 验证结果

日志的 `.txt` 版本仅统一换行并去除行尾空白，便于仓库检查和阅读；每份原始日志同时以 `.txt.raw.gz` 无损保存。`raw-log-hashes.json` 记录解压后原始字节的 SHA-256，`evidence-sha256.json` 记录所有证据文件的 SHA-256。原始失败与成功输出均保留，没有删掉失败记录或更改测试结果。

| 验证项 | 状态 | 证据 |
| --- | --- | --- |
| 修改前既有相机/编辑器相关测试 | PASS，65 项 | `baseline-tests.txt` |
| 修改前新回归测试 | EXPECTED_FAILURE，2 项；旧 Runtime 缺少草稿 RPC 和快照回调 | `regression-before-fix.txt` |
| 修复后新回归测试，原工作区 | PASS，33 项 | `focused-final.txt` |
| 相机单独分支的新旧相关测试 | PASS，98 项 | `scoped-candidate-tests.txt` |
| 干净相机分支整库 CI | PASS；3052 passed，8 skipped，28 subtests passed；proto/Ruff/mypy 均通过 | `scoped-candidate-ci.txt` |
| 原混合工作区整库 CI | FAIL；5 failed，3797 passed，8 skipped，28 subtests passed；proto/Ruff/mypy 通过 | `full-ci.txt` |
| 两项原工作区失败的独立重测 | FAIL，2 项 | `current-worktree-two-failures.txt` |
| 使用修改前源码快照重放这两项失败 | BASELINE_FAILURE，2 项，断言与错误相同 | `pre-fix-unrelated-failures.txt`、`check_pre_fix_failures.py` |
| 使用修改前源码快照重放全部五项失败 | BASELINE_FAILURE，5 项，断言与错误相同 | `pre-fix-five-failures.txt`、`check_pre_fix_failures.py` |
| 实机 IMV SDK / 相机网络 / 物理画面采集 | NOT_RUN | 测试使用模拟相机，不连接真实相机 |
| 打包 / 发布 / 外部打包器修改 | NOT_RUN | 本次仅源码修复与 Git 分支交付 |

新回归覆盖 2.1/2.2/2.3 草稿、未加载工程、Runtime 旧工程没有新节点、未完成的其他算子、节点删除或移动、算子与节点身份不匹配、非 operator 节点、重复节点、非法 JSON、请求大小限额、非法临时参数、任务运行或资源尚未释放、取消后关闭设备、加载工程与普通 Job 启动前关闭预览，以及旧 Runtime 明确返回不支持。

真实内置相机编辑器和“单帧”按钮通过 Qt 离屏事件循环验证，使用模拟相机采集 JPEG；工程文件、项目 metadata、节点已应用参数和 Runtime 正式加载工程保持不变。同步 gRPC 和 AIO gRPC 使用真实回环连接。普通 Job 收尾测试使用隔离计数器工作流和真实 Windows spawn，不执行用户流程。

## 原工作区五项既有失败

`tests/designer/test_minimal_project_metadata.py` 中：

- `testEditorUsesLateCatalogWithoutMutatingSavedGraph`：测试期望 `node.paramSchema == {}`，实际已填充 schema。
- `testNormalizationPreservesSavedContractsAndOwnsFallbackSchema`：`folderPath` 的固定节点索引访问产生 `KeyError`。

另外，`tests/runtime/test_image_batch_while.py` 的两个 `testRealWhileProcessesEveryImageAndResetsForNewRun` 参数化用例，以及 `testDirectoryResolvedRelativeToProjectAndDraftUnchanged` 失败。现有批量图片示例中已加入 `vision.io.huaray_camera` 节点：运行用例在 SDK 定位时返回 `E_CAMERA_SDK_UNAVAILABLE`，固定节点索引读取 `folderPath` 也已不成立。

用修改前备份的 10 个 Python 模块通过只读 import loader 重放后，五项仍以同样的断言和错误失败。loader 保持原 `__file__`，没有覆盖现场文件或中断用户进程。这些是原混合工作区已有问题，不计作相机修复通过，也不为本次任务改动其测试断言、用户示例或实现。

本次新增测试全部使用模拟相机；原混合整库中的已有示例测试尝试定位 IMV SDK，但在设备打开之前失败，不能算作实机采集验收。只读检查确认当前测试进程的默认 `HuarayTech/MV Viewer` 候选目录不存在；没有全盘搜索其他 SDK 安装位置、没有修改系统环境，也没有安装软件。真实连接仍可能需要安装 MV Viewer 或配置 `HUARAY_MV_VIEWER_ROOT`。

## 使用和验收边界

先保存用户当前工作，正常关闭并重启 Designer；源码改动不会自动装入已有 Python/Qt 进程。之后拖入相机节点，双击并点击“连接并预览”或“单帧”，即可使用未保存节点预览。远程 Runtime 也须更新后重启。实际 IP、SDK、设备访问权限与帧转换仍需实机连接验收。
