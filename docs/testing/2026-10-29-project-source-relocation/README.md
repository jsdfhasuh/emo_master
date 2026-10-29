# Designer 本机工程来源迁移修复验证

日期：2026-10-29。源码基线：`25c82042ce3cfb83e16ede8ea2cb2bcbb2110ce4`，加本轮未提交修改。
环境：Windows，Python 3.10 / PySide2；解释器为 `C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`。

## 问题与修复

工程搬到新目录后，Designer 的 SQLite 检查使用新草稿目录，而 Runtime 的持久化 `sourcePath` 仍指向旧位置。
例如新位置 `E:/deployment/new-project/project.emoproj`、旧位置 `E:/deployment/old-project/project.emoproj`。
此处为等价示例路径，原始现场目录名未纳入公开记录。
启动时相对数据库路径被解析到旧目录，因此出现“数据库不存在”，尽管新目录下数据库实际存在。

修复复用现有比较、校验、显式写入和事务提交：

- 新增 embedded-only `local_project_path` 写入字段及 `projectSourceWrite=1` 能力声明；gRPC 不接受本地目录切换。
- Designer 同时显示本地与在线来源；内容相同但来源不同仍可校验/写入，运行入口不能略过这项差异。
- 目录单独写入时保持在线 payload，不夹带未选中草稿；目录与选中配置在同一事务内提交和恢复。
- 来源变化使执行版本递增；Windows 大小写、分隔符和 `..` 等同路径拼写不产生额外执行版本。
- 当前工程的运行任务、根/子检测周期与设备维护占用均受保护；原 stop/drain 策略仍可正常退休受影响任务。
  非 Job 操作先正常结束，提交前再次检查；仅目录变化不强迫重连未变化的设备。
- 包含 SQLite 写入节点时，通过既有管理额度和取消通道检查新目标；缺库/缺表报告实际路径、流程、节点，
  不自动新建或搬运业务库。StartJob 拒绝信息也带在线工程来源诊断。
- 项目 schema 仍为 2.7，模型、保存图片和数据库参数保留相对路径。

操作说明见 [目录迁移指南](../../guides/device-dispatch-online-configuration.md#迁移本机工程目录)。

## 已执行验证

各集合有重叠，不相加为去重后的测试数。

| 范围 | 结果与证据 |
| --- | --- |
| Runtime 配置、首次写入、生产加载、停止/排空、资源、Designer 配置、SQLite 回归 | **271 passed，173.70s**；[regression.txt](regression.txt) |
| 最终目录迁移及占用测试 | **17 passed，23.35s**；[relocation-final.txt](relocation-final.txt) |
| Windows 原生 Qt 100%，配置迁移/布局/导航/客户端 | **35 passed，10.34s**；[native-100.txt](native-100.txt) |
| Windows 原生 Qt 150%，目录迁移完整操作 | **1 passed，5.26s**；[native-150.txt](native-150.txt) |
| 协议生成一致性 | `scripts/gen_proto.py --check` 通过 |
| 受影响源码、测试与验证脚本 | Ruff 通过，修改范围 `git diff --check` 通过 |
| 配置 RPC/store/impact/transition、客户端与配置 UI 的定点 mypy | 6 个文件无错误；默认未检查无类型函数体，[mypy.txt](mypy.txt) |

新增测试覆盖：目录单独切换及同时改输出参数、取消、缺库保留旧配置、运行新图片并写入新库、重启恢复、
重复请求回执、旧能力/远程拒绝及普通远程写入、无设备连续任务 stop/drain、SQLite I/O 取消、
实际模拟设备 ExecuteDeviceAction 占用/释放、根子周期和提交前检查。

新增测试文件：

- [test_project_source_relocation.py](../../../tests/runtime/test_project_source_relocation.py)
- [test_source_relocation_owners.py](../../../tests/runtime/test_source_relocation_owners.py)
- [test_project_source_relocation_ui.py](../../../tests/designer/test_project_source_relocation_ui.py)

原生截图已目视检查，路径可换行、选择框和底部按钮无重叠，写入后显示新的在线来源：
[100% 比较](native-100/compare-directory.png)、[100% 提交后](native-100/directory-committed.png)、
[150% 比较](native-150/compare-directory.png)、[150% 提交后](native-150/directory-committed.png)。
150% 验证之后仅修改了零 JSON 变更确认文案与无 SQLite 节点时跳过管理额度的条件；
最终 100% 和 17 项目录迁移回归分别覆盖这两处修改。

## 外部工程临时副本复验

先前还使用一个外部工程、已有模型与本地完整帧，在临时旧/新目录中复验来源迁移：旧位置启动拒绝并给出正确路径，
显式切换后相对模型、图片输出和 SQLite 写入目标均跟随新目录，重启 Runtime 后仍恢复新来源。
原工程、模型和数据库哈希未变化，RTSP 入口被拦截且未触发。

这属于先前已经完成的本地副本证据，不是本次整理重新执行的测试，也不是冻结 EXE、内网部署、真实设备或模型准确率验收。
原始结果包含外部工程/模型/帧标识与业务统计，连同专用复验脚本、未脱敏 README 保留在交付机本地，
不随公开提交上传。本目录中的通用测试输出与临时合成工程 Qt 截图仍随功能提交，可用于常规复核。

## 交付边界

本轮未提交、推送、打包或发布；内网现有 EXE 尚不包含修复，需更新包含这些修改的 Designer/Runtime 软件包。
未改变用户现场配置、连接真实相机/PLC，未重写外部交付工程。
工作区中并发的 SQLite 编辑器布局及算子元数据修改保持原样，不归入本轮修复。
