# 调试器范例工程

三个工程均可直接用 Designer 打开，不需要模型、相机、PLC、网络地址或生产数据库。只使用公开准入的内置算子，不依赖测试替身或定制插件。

本目录用于目标 Windows 机器上的调试器交互验收，不是相机/PLC 单步调试工程。现阶段通用调试器仍拒绝设备、外部写入和模型资源算子。真实设备正常执行另见 [dot 联合验收清单](../../docs/testing/2026-10-10-dot-joint-acceptance.md)。

## 启动

1. 拉取本次交付分支 `agent/runtime-workflow-architecture-v1`，确认 Designer 和 Runtime 来自同一提交。已启动的旧进程需要结束后重开。
2. 使用源码入口 `start_designer.cmd`；该入口是隔离本机开发数据的内嵌 Runtime 模式，不自动开始检测。另一台机器的 Python 路径可通过 `EMO_MASTER_PYTHON` 指定，不需要照抄开发者的绝对路径。
3. 选择下表中的具体 `.emoproj` 文件打开，不选择含有三个工程的整个目录。不需要重新生成范例。
4. 使用“编辑 > 流程调试”，不要把 F5 的正式运行与调试启动混为一谈。每次结束后先等待调试窗口关闭，再打开下一个工程。

## 范例与预期

| 工程 | 根流程输入 | 固定预期 |
| --- | --- | --- |
| [01-nested-calls.emoproj](01-nested-calls.emoproj) | 无 | 两次调用同一个 child；最后 value=7 |
| [02-foreach.emoproj](02-foreach.emoproj) | items 选择 [items.json](items.json)，内容 `[1,5,9]` | result=`[false,true,true]`，index=`[0,1,2]` |
| [03-image.emoproj](03-image.emoproj) | image 选择 [sample.png](sample.png) | mask 为 320x240 uint8 单通道二值图，像素值仅 0/255；threshold=127 |

### 01：进入、跳过、跳出与试运行

1. 启动后停在 `main/seed` 之前。进入一次会执行 seed，停在 `main/first`。
2. 再进入一次，停在 `child/number`。调用栈应同时包含 first 和 number；输入边界不额外消耗单步。
3. 点击“暂停节点独立试运行”，只把临时 value 改成 99。试运行输出应为 99，原流程仍暂停在同一位置。
4. 跳出，回到 `main/first` 的 `call.return`，其真实输出仍为 7。继续完成，最后输出仍为 7。
5. 结束并重新打开调试。在第一次暂停时，为 `child/number` 勾选断点，条件填 `params["value"] == 7`，命中起点设 2，点击“更新断点”。继续后应只在第二次 child 调用暂停；动态 workflowRunId/nodeRunId 与第一次调用不同。
6. 保持暂停至少 65 秒，窗口应继续显示 PAUSED，随后可恢复。不要通过操作系统挂起进程模拟暂停。

本工程也可用于孤立算子测试：关闭流程调试，切到 child，右键 number 选择“算子调试”；不应用地把 value 改成 99，单步输出 99，但工程中的正式值仍为 7。

单独检查“跳过”：在 main/first 前点击单步跳过，应直接停在 first 的 call.return，不在 child/number 前暂停，输出仍为 7。普通单点断点则不填条件、命中起点保持 1，两次 child/number 调用都应在执行前暂停，调用身份不同。

### 02：循环与条件断点

1. 在根流程输入 items 行选择 `items.json`，启动后停在 foreach。
2. 在断点页勾选 `body/compare`，条件为 `inputs["left"] >= 5`，命中起点 1，点击更新后继续。
3. 应依次停在 left=5 和 left=9，调用位置的轮次分别包含 1 和 2。left=1 的第一次迭代不应因条件断点暂停。
4. 继续至结束，核对表中的结果。暂停等待不应消耗该循环的 10 秒活动执行预算。

### 03：完整图像与参数版本

1. 在根流程输入 image 行选择 `sample.png`，启动后停在 process。
2. 进入子流程后，可在 blur 前检查原图；执行 blur 后在 threshold 前检查真实滤波图和同源 frame。
3. 对 threshold 做独立试运行，将临时阈值改为 200；试运行结果应变化。恢复主流程后仍使用启动快照中的 127。
4. 完成后，在“调用数据”选择最终 mask，核对尺寸和二值内容。不要把旧图、缩略图或参数摘要当作本次完整输出。

图像范例只有文件上传动作，不使用 Runtime 文件读取算子；外部 Runtime 不需要访问 Designer 上的原始文件路径。

另做独立单点测试：关闭流程调试，切到 process，右键 threshold 打开算子调试，手动上传 sample.png。它必须直接对原图二值化，不补跑 blur。流程中的 threshold 则必须收到本次 blur 的滤波图；可在断点页选中 process/threshold，使用“运行到所选节点”定位并比较。分别记录这两类输入和结果，不把独立调试等同于从流程开头运行到该节点。

## 共同检查

- 调试不会自动保存工程、应用参数、创建生产 Job 或把调试输出传播到画布正式结果。
- 调试持有资源期间，F5 正式运行和其他设备预览应被拒绝，不应偷偷结束调试。
- 关闭窗口、停止、断开连接后的租约回收应最终释放 Worker 和资源；收到取消请求不等于已经清理完成。
- 100%、125%、150%、200% 缩放下检查按钮、中文文字、调用栈和图像，无重叠或不可操作的控件。

## 开发复现

仓库根目录使用已安装依赖的 Python：

```powershell
$env:PYTHONPATH = 'src'
python examples/workflow_debugger/build_examples.py
$env:QT_QPA_PLATFORM = 'offscreen'
python -m pytest -q tests/runtime/test_workflow_debug_examples.py
python -m pytest -q tests/designer/test_workflow_debug_example_ui.py tests/designer/test_debug_example_interactions.py
```

构建脚本生成确定性工程和合成图，并先用实际插件注册表/编译器验证。它不连接设备、不运行生产工程，也不修改用户原有项目。
