# PR20 独立桌面走查

**状态：P3.5 未完成。先关闭 R1–R3 的修复与定向回归，再按本文件进行独立桌面验收。** 生成器提供数值比较和动态类别样式两个演示。

P3.5 需要在原生 Qt 窗口中实际操作。离屏组件测试、工程生成/保存/重读、编译、加载入口或启动冒烟成功，均不能单独记为桌面验收通过。执行角色为 ChatGPT Web 或人工；桌面验收阶段不得启动、恢复或委托 Codex、Work、CLI、SDK、IDE 或付费模型 API。记录实际源码 SHA、工作区修改、平台与连接方式。


使用 PR20 当前源码和已经安装项目依赖的 Python 3.10。在仓库根目录执行以下 PowerShell 命令，将示例里的 Python 路径换成本机路径。

```powershell
$emoPython = 'D:\Conda\envs\emo_master\python.exe'
$env:PYTHONPATH = (Resolve-Path .\src).Path
& $emoPython .\openspec\changes\enhance-operator-result-semantics-v2-5\build_demo.py
& $emoPython .\scripts\dev.py run-designer --local
```

在启动设置选择「本机运行」。保留演示专用的数据目录，从打开工程入口加载生成的 `manual_test_workspace/pr20-demo/run-details.emoproj`。默认入口有两个纯数值算子，包含变量覆盖和布尔输出。生成时按当前注册描述符填写两条工作流的端口、参数和依赖，采用 2.10 / 1.3 工程格式，并完成编译与保存重读。

## 正式 Job

1. 点击「开始运行」。首次运行时按现有配置对话框校验、写入 Runtime，然后点击「运行在线版本」。
2. 打开「运行日志 → 运行任务」，双击最新任务，在执行结构里选中「比较阈值」的完成记录。确认状态为已完成，节点总耗时来源为节点起止事件；若保存了算子内部耗时，核对它与节点总耗时各自的标签与单位。
3. 输入左侧为 `0`，右侧调用参数 `rightValue` 为变量覆盖后的 `5`。生效参数里的实际右值也为 `5`，来源为指标。节点配置中的 `999` 仅是未覆盖时的备用值。比较结论 `0 > 5` 为 `false`。
4. 查看易读详情中字段的中文名称和提示，展开「指标」「记录身份」，分别复制 `false` 和当前记录。原始 JSON 应能查到真实算子 ID、版本、参数摘要和字段原 key。用当前历史查询入口导出该任务记录，再核对相同证据。

## 单算子调试

1. 双击「比较阈值」节点打开参数编辑器，点击「算子调试」。在输入页分别为 `left`、`right` 选择「手工输入（值）」，填入 `0`、`-1`，点击「单步执行」。
2. 在「结果 → 执行详情」查看本次调用参数、实际右值 `-1`、输出 `true`，以及独立的调试执行耗时。展开「准备阶段参数」，核对它与有明确来源的实际处理值分别显示。查看原始 JSON 中本次调试的会话、代次和执行 ID，并试复制当前记录。
3. 核对原始记录的可信身份位于 `sourceIdentity`；易读详情应已显示该算子的中文说明、实际算子 ID/版本及可确认的生效信息，不依赖手工补顶层身份。
4. 保留调试窗口的第一条结果，修改输入并再次执行。从「日志 → 查看此条日志的执行结果」回看旧轮次，确认仍显示旧轮次的实际值和原始 JSON。

## 动态类别样式

在同一演示工程中执行以下步骤。生成器已在工程旁的 `run-details-annotate-inputs` 文件夹写入小测试图和一条模拟检测。

1. 切换到「动态类别样式演示」工作流，双击「标注模拟检测」节点并打开「算子调试」。节点已配置 `classStyles={"person":{"displayLabel":"人员","color":[0,128,255]}}`。
2. 在输入页用文件选择按钮给 `image` 传入 `image.png`，给 `detections` 传入 `detections.json`，上传完成后点击「单步执行」。结果应有一个标注框，`drawnCount` 为 `1`。
3. 在「结果 → 执行详情」展开「提交参数」或「准备阶段参数」中的 `classStyles → person → displayLabel / color → 数组元素`。核对动态 key `person`、字段和数组元素的中文说明，以及 BGR 三个整数的原顺序。在易读详情复制选中的 `person` 对象，与原始 JSON 的 key、值和类型核对。
4. 选中 `drawnCount` 并点击「导出完整结果…」。核对导出的 `.emodebug.zip` 中 `value.json` 为数字 `1`，`metadata.json` 的 `identity.rawParams.classStyles.person` 保留原样式，身份对应本次运行。未知字段的保留由共享组件回归覆盖。

## 独立 Runtime

如需重复验证 gRPC 路径，在另一 PowerShell 窗口从同一源码启动隔离 Runtime。

```powershell
$emoPython = 'D:\Conda\envs\emo_master\python.exe'
$env:EMO_RUNTIME_DATA_DIR = Join-Path (Get-Location).Path 'manual_test_workspace\pr20-remote'
& $emoPython .\scripts\dev.py run-runtime
```

在 Designer 启动设置选择「连接外部 Runtime」，地址填写 `127.0.0.1:50051`，重复正式 Job 和单算子调试步骤。记录启动源码 SHA、连接方式、两条执行记录的 ID、实际结果和任何未完成的检查。

本次协议与目录传输未改变时，沿用既有本地/gRPC 自动化证据，不要求把全部人工步骤按连接方式复制一遍。实际走查选择一种可用连接；若观察到另一连接独有问题，再针对性验证。R2 的认证 URL 使用源码定向回归及合成凭据验证，不要求桌面连接真实设备。

## 记录结果

将实际日期、角色、源码 SHA、工作区状态、平台/连接方式、正式 Job 与调试执行标识、复杂字段入口及操作结果写入 delivery.md。逐项写实际通过、失败、未执行，不用“已启动”代替验收。只有既定操作确实完成后才能勾选 P3.5。
