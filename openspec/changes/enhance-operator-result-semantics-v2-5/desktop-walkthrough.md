# PR20 独立桌面走查

使用 PR20 当前源码和已经安装项目依赖的 Python 3.10。在仓库根目录执行以下 PowerShell 命令，将示例里的 Python 路径换成本机路径。

```powershell
$emoPython = 'D:\Conda\envs\emo_master\python.exe'
$env:PYTHONPATH = (Resolve-Path .\src).Path
& $emoPython .\openspec\changes\enhance-operator-result-semantics-v2-5\build_demo.py
& $emoPython .\scripts\dev.py run-designer --local
```

在启动设置选择「本机运行」。保留演示专用的数据目录，从打开工程入口加载生成的 `manual_test_workspace/pr20-demo/run-details.emoproj`。工程只有两个纯数值算子，包含变量覆盖和布尔输出。生成时按当前注册描述符填写端口、参数和依赖，采用 2.10 / 1.3 工程格式，并完成编译与保存重读。

## 正式 Job

1. 点击「开始运行」。首次运行时按现有配置对话框校验、写入 Runtime，然后点击「运行在线版本」。
2. 打开「运行日志 → 运行任务」，双击最新任务，在执行结构里选中「比较阈值」的完成记录。确认状态为已完成，节点总耗时来源为节点起止事件；若保存了算子内部耗时，核对它与节点总耗时各自的标签与单位。
3. 输入左侧为 `0`，右侧调用参数 `rightValue` 为变量覆盖后的 `5`。生效参数里的实际右值也为 `5`，来源为指标。节点配置中的 `999` 仅是未覆盖时的备用值。比较结论 `0 > 5` 为 `false`。
4. 查看易读详情中字段的中文名称和提示，展开「指标」「记录身份」，分别复制 `false` 和当前记录。原始 JSON 应能查到真实算子 ID、版本、参数摘要和字段原 key。用当前历史查询入口导出该任务记录，再核对相同证据。

## 单算子调试

1. 双击「比较阈值」节点打开参数编辑器，点击「算子调试」。在输入页分别为 `left`、`right` 选择「手工输入（值）」，填入 `0`、`-1`，点击「单步执行」。
2. 在「结果 → 执行详情」查看本次调用参数、实际右值 `-1`、输出 `true`，以及独立的调试执行耗时。展开「准备阶段参数」，核对它与有明确来源的实际处理值分别显示。查看原始 JSON 中本次调试的会话、代次和执行 ID，并试复制当前记录。
3. 保留调试窗口的第一条结果，修改输入并再次执行。从「日志 → 查看此条日志的执行结果」回看旧轮次，确认仍显示旧轮次的实际值和原始 JSON。

## 独立 Runtime

如需重复验证 gRPC 路径，在另一 PowerShell 窗口从同一源码启动隔离 Runtime。

```powershell
$emoPython = 'D:\Conda\envs\emo_master\python.exe'
$env:EMO_RUNTIME_DATA_DIR = Join-Path (Get-Location).Path 'manual_test_workspace\pr20-remote'
& $emoPython .\scripts\dev.py run-runtime
```

在 Designer 启动设置选择「连接外部 Runtime」，地址填写 `127.0.0.1:50051`，重复正式 Job 和单算子调试步骤。记录启动源码 SHA、连接方式、两条执行记录的 ID、实际结果和任何未完成的检查。
