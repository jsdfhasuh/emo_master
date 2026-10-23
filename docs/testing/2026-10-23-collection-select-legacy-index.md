# 集合选择旧节点的动态索引端口补齐

## 范围和基线

- 用户授权修复上轮审核确认的 P2：当前算子目录已经声明可选 `index`，但旧节点保存的端口表没有它，导致无法正常接线。
- 实际 HEAD：`e8c24fcfd7ebf964f0f506a7d73a42405069dff9`；本轮在已有未提交工作之上局部修改，未回退其他改动。
- Python 3.10 / PySide2；使用 `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`。
- 不自动转换全局变量，不改 Runtime 算法、数据库事务、模型、识别参数或用户工程。

## 修复方式

1. `MainWindow._controlFlowPresentation` 仅在当前 Runtime 算子目录声明了 `index: integer` 时，为缺失该端口的旧集合选择节点构造临时显示契约；原节点和参数保持不变。
2. 原模式展示继续控制可见性：索引模式可接线，其他模式保留已有连线并提示不适用。新增端口放在原集合输入之后，不改变默认接线顺序。
3. 连接入口把已解析的目标端口类型传给现有 `FlowGraphModel.connectNodes`。模型先验证端点和类型，仅成功时补入新端口，与连线一起进入已有编辑事务、撤销/重做和正常保存流程。
4. 参数应用、打开和切换工作流不会单独升级保存契约；普通类型不匹配接线不会留下端口或撤销记录。既有端口类型不会被这个补齐接口覆盖。
5. 没有算子目录或目录未声明整数 `index` 时，不为旧节点凭空提供该能力；已经明确保存的端口和连线不会因目录暂未到达而删除。

没有新增迁移框架、第二套状态缓存或后台任务，也没有改变工程格式或算子版本。

## 验证

证据目录：`docs/evidence/2026-10-23-collection-select-legacy-index/`。

- 先新增正常路径回归，再修改产品代码：初次 **3 failed, 2 passed**，失败均为旧节点缺少可见/可连接的 `index`，保留于 `before-fix.txt`。
- 最终相关 **26 个测试文件，324 passed in 30.04s，退出码 0**，见 `regression-final.txt`。不是全仓库 CI。
- 新增 6 项覆盖：目录能力约束、打开不变脏、原集合端口顺序、失败接线不修改、成功接线、撤销/重做、流程切换、参数及绑定保留、保存重开、Runtime 取动态索引、断开重连、模式切换及目录晚到。
- 新夹具从 `WorkflowStore().toPayload()` 创建完整 2.7，经过 `ProjectDocument`、保存重读和编译；仅旧节点端口表模拟扩展前状态，不伪造旧工程格式。
- 原生 Qt 在 100% 和 150% 下分别 **6 passed**；加载验证进程专用字体并通过文字栅格探针，实际检查两张 `legacy-index-*.png`，端口及提示可读。
- Ruff 检查 2 个修改源码文件和新测试通过。
- Mypy 没有全绿：`main_window.py:1470` 有既存 `PageCoordinator | None` 的 `showDevices` 类型错误。使用 `--shadow-file` 指向本轮修改前的两份源码快照，复现了同一处错误；前后都只有这一处。保留 `mypy-before.txt` / `mypy-after.txt`，没有把失败记为通过或顺带修改无关功能。

## 用户原工程验证

实际通过 `MainWindow.loadProjectSelection` 打开原多视频工程，访问全部 8 个流程：

- `one_target.select`、`one_vehicle.select` 都能显示并连接新 `index`，仅查看时保存契约仍无该字段，工程不变脏。
- 在临时编辑中添加索引源并接线，原参数和变量绑定保持不变；另存临时副本、重新打开及编译成功。
- 该真实工程步骤没有运行视频；动态输入的执行结果由前述独立合成工程回归确认。
- 所有窗口使用隔离 QSettings 和 RuntimeClientStub，没有连接在线 Runtime 或真实设备。

两份用户文件在验证前后 SHA-256 不变：

| 文件 | SHA-256 |
| --- | --- |
| `motorcycle-file-list-riders.emoproj` | `c380474ac8f464e06e4c08d8543f7a0f41646a2bfd4428159b754f0a7c87d3e0` |
| `motorcycle-local-loop-state.emoproj` | `73bcf716ec9df79a22a03281a1f630863930cf35f6bf1b9bc75020e3628c35f5` |

本轮是源码、临时工程和原生 Qt 自动化验证，不是冻结安装包或当前桌面人工验收；没有重新测试 9 视频性能，没有提交、推送、打包、部署或重启运行中的应用。已有窗口不会热加载源码修改。
