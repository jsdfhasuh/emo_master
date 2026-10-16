# 双工位迁移 M5：生产编排、配置与分层验证记录

- 日期：2026-10-09。
- 同一份[迁移计划](C:/Users/jsdfhasuh/my_scripts/emo_master/docs/plans/2026-10-09-two-station-hardware-trigger-migration-v1.md) v2，更新 M5/M6 状态，不另建并行计划。
- 基线：`agent/runtime-workflow-architecture-v1`，HEAD `61a42bf3ab181c3b2339f0425edfb40a60d0ec7b`，加未提交工作区。保留前一批实现和无关改动，没有提交、推送、版本发布、打包或部署。
- 运行目录：`C:/Users/jsdfhasuh/my_scripts/emo_master`，与 Git/Path.resolve 返回的 `D:/jsdfhasuh/documents/my_project/emo_master` 为同一目录，不是两份 checkout。
- 解释器：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，本轮复核 Python **3.10.21**。环境为 `PYTHONPATH=src`、`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`、`QT_QPA_PLATFORM=offscreen`。
- 新证据文件：[M5 SHA-256 清单与日志记录](C:/Users/jsdfhasuh/my_scripts/emo_master/docs/testing/2026-10-09-two-station-production-m5-evidence.json)。前一批 normal-path 记录和 evidence 文件保持原样；不能给旧源码快照套用新代码的通过结论。

## 1 本轮实现

| 领域 | 实现 | 兼容与边界 |
| --- | --- | --- |
| 工程契约 | 现有 2.4 增加可选 `production.stations`，最多两个稳定 ID；名称、独立入口、启用、页面、输入、保护变量 | 非空声明必须明确迁移至 2.4；没有新 2.5；空声明不序列化；打开/取消不升级、不变脏 |
| 静态归属 | 校验入口/名称唯一、每页恰好归属一站、保护变量为 mutable job 且不重复归属；禁止工作流引用另一站保护拍次 | 不新增设备配置真源；相机节点用同一静态命名空间的不同 IP 或 cameraKey；拒绝重复、动态身份、index/userId 和混用别名；不做实际设备发现 |
| 正常展示 | 合法工位根 normal capture；按站过滤页面、数据源、作用域及预算；修订身份包含工位声明 | 保留旧单根指纹；拒绝跨站 scope、拍次来源和导航；保留最多两个 retained display Job 的配额 |
| 输入冻结 | 声明式生产在工程加载时复制 manifest 声明的模型/图像/TXT 输入、记录摘要；每次 Start 复核内部副本 | 两站启动/单站重启复用同一加载版本；输出路径/设备地址不搬入副本；16 GiB 上限；拒绝未声明清单的 input directory；所有工位停机释放后才重载/更新 |
| 后端准入 | 仅允许启用的合法站根；重复占用、未释放终态、动态改变站输入、内部快照篡改、生产 prepared/debug 绕过均拒绝 | `StartJobRequest.production_sync_confirmed = 9`，每次声明式硬件 Start 必须显式确认；它是调用者声明，不是物理同步或相机 armed 证明 |
| 生产 owner | 每站独立 requestId、jobId、startUncertain；独立/全部启停；load/install/close 串行；认领直接 gRPC 准入的 Job | 不保存拍次；不确定 Start 只 lookup 原请求，不重新 Start；部分失败不重启成功站；关闭由 Runtime 最终确认全部 owned worker 释放 |
| 原生生产 UI | 每站独立 tab、状态、Job 和启停；全部启停；独立 DisplaySession/Hub/页面 | 重启一站不替换另一站的 Job、订阅或页面；硬件启动显式首拍同步确认，autoStart 不绕过；部分错误按真实结果显示 |
| 原生工程配置 | Designer **运行 → 生产工位配置…**，上下文表单设置工位、页面、保护变量及标量入口输入；精确整数帧间隔 | 显式保存表单才应用声明，工程文件仍由原保存机制落盘；非法配置不部分写入；保护站入口/变量不能静默删除 |
| 全局变量管理 | Job 下拉显示工位名/入口，首次打开自动选当前 Job；生产运行/退役期间受保护拍次禁止人工 set/reset | 不锁无关变量；不改用户已选择的有效 Job；普通 debug Job 可 set/reset 自己的隔离值，另一个 Job 的值保持不变 |

### 仍然使用既有全局变量控制器

每站拍次为 integer、初值 0、job 生命周期。图像到达后 PLC 子工作流完成，再由原子 increment 输出本帧不可变 `n` 给 Switch；第二拍分支在算法前 reset。归零改变全局值，不改变当前帧 `n=2`。

本轮没有专用“两拍阶段”节点、隐藏拍次变量、新计数服务或特殊执行器。`StationSession` 只有任务/请求管理状态，不保存拍次。

主要代码入口：

- [工程工位与归属契约](C:/Users/jsdfhasuh/my_scripts/emo_master/src/emo_master/core/project/production.py)
- [生产加载级输入快照](C:/Users/jsdfhasuh/my_scripts/emo_master/src/emo_master/apps/runtime/context/production_snapshot.py)
- [Runtime 准入与变量 RPC](C:/Users/jsdfhasuh/my_scripts/emo_master/src/emo_master/apps/runtime/grpc_server/service.py)
- [生产 owner](C:/Users/jsdfhasuh/my_scripts/emo_master/src/emo_master/apps/operator_runtime/controller.py)
- [生产原生窗口](C:/Users/jsdfhasuh/my_scripts/emo_master/src/emo_master/apps/operator_runtime/main.py)
- [Designer 工位表单](C:/Users/jsdfhasuh/my_scripts/emo_master/src/emo_master/apps/designer/ui/production_dialog.py)

## 2 验证结果与源码快照边界

| 检查 | 结果 | 证据层级 |
| --- | --- | --- |
| 本轮广泛回归 | **PASS：2953 passed / 1 skipped，949.50s** | `tests/core tests/runtime tests/designer tests/ui tests/proto`；发生在最后几项变量 UI/保护、测试事件等待/模拟帧消费收尾之前，不能称为最新快照全目录重跑 |
| 最终受影响链与旧兼容回归 | **PASS：195 passed / 1 skipped，94.50s** | 14 个文件加 `tests/proto`，完整命令见 §5；最后修正后运行；与广泛回归重叠，不相加计算独立通过数 |
| 调试 Job 隔离失败项单独重跑 | **PASS：1 passed，6.36s** | 使用两个实际 Job 记录；set/reset 均不改变另一 Job 的非零值；随后包含在最终组，不额外累加 |
| Ruff | **PASS：65 个改动/新增 Python 文件** | 排除原已排除的 generated proto，不降低规则；最后测试夹具修正后复验 |
| Mypy | **PASS：4 个重点源文件** | `--follow-imports=silent`，工位契约、输入快照、生产 controller、Designer state；不是全仓库类型验证 |
| proto drift | **PASS：exit 0** | `scripts/gen_proto.py --check`，在临时目录生成并比较；不再写回源码 |
| git diff --check | **PASS** | LF/CRLF 提示保留，不把提示当空白错误 |
| 原生 Qt 视觉证据 | **PASS：5 张 offscreen 截图** | Microsoft YaHei；1060/1280×800 两个工位页面和 850×640 工位配置表单；等待实际展示及 RUNNING 状态；原生控件/事件循环与长中文显示人工逐图检查 |
| 真实模型、原公式与几何等价 | **NOT_RUN** | 不能从推理后端替身或夹具测试公式推导旧算法等价 |
| 物理同步、现场相机/PLC/机器人及节拍 | **NOT_RUN** | 只使用本地模拟对端和 SDK/推理边界；无现场设备操作 |
| 冻结发布包、安装/升级、部署 | **NOT_RUN** | 通用资源搬迁结构测试不是冻结现场包验收；无打包、发布或部署 |
| 业务 NG | 本阶段不实施 | 继续按用户范围暂缓；技术错误仍停止正常输出，不盲重试 |

唯一跳过项用最终 `-rs` 确认：`tests/core/plugin/test_icon_resources.py::testSymlinkCannotEscapeRoot`，`symlinks unavailable in this environment`。不取消安全校验，不把 SKIPPED 算作 PASS。本轮广泛组没有重新执行整个 `tests/plugins`、`tests/e2e`、`tests/p0` 或 `tests/sqlite_writer`，不得将前一批这些目录的数量混入本轮结果。

## 3 实际 spawned 双工位验证

[硬件边界测试](C:/Users/jsdfhasuh/my_scripts/emo_master/tests/runtime/test_spawned_hardware_stations.py)实际启动两个独立 worker 子进程，临时复制 builtin registry 只替换相机 SDK/推理入口，不重复注册同一个算子 ID：

1. FileTriggeredSdk 以按序到达的文件模拟硬件帧，IMV 短时间片等待/取消逻辑仍走真实相机算子；消费后的帧删除，防止新模拟会话重放。
2. ONNX 后端替身只返回检测集合。YOLO 算子、掩码/Crop、二值化、轮廓、测量、取点/坐标、全局变量及分派都实际执行，不注入最终坐标或直接替测试图发送设备报文。
3. PLC 对端为真实本地 socket 服务，处理 MC 3E 请求；网关为本地 socket 对端，ACK 分包返回。真实 SLMP/TCP/格式化/ACK 算子执行，未访问现场网络。
4. 工位 1 持续无触发时，没有完成根调用、没有 PLC 读取或拍次推进。工位 2 接收四帧，展示中的本帧 `n` 为 **1、2、1、2**，帧结束全局值为 **1、0、1、0**，PLC 样本为 **201、202、203、204**。
5. 工位 2 的 PLC/机器人正常输入报文各两条，所有展示来源均为该站；工位 1 之后接收两帧，各发送一条。所有图内资源、采样及协议正常依赖保持原有控制机制。
6. 工位 2 保持 RUNNING 时反复停止/重启无触发工位 1；每次实际 worker 退役，模拟 SDK destroy 标记正确，展示配额保持两个，不重启工位 2。最终 StopAll/close 确认两个 worker 和模拟 SDK 释放。

另外的[生产契约测试](C:/Users/jsdfhasuh/my_scripts/emo_master/tests/runtime/test_production_stations.py)验证源文件/配置改写不混入单站重启、内部副本篡改拒绝准入、未释放终态拒绝重复根启动、丢失 Start 响应后精确原 request lookup、部分失败保留成功站、direct gRPC Job owner 认领，以及一个 Stop RPC 失败后仍尝试其他站并由 close 最终清理全部 owned worker。

这些结论分别支持计划 V07/V13/V14/V15/V16/V22/V26/V27 的**源码、原生 UI 和外部边界模拟部分**，不支持现场设备、物理同步、真实缓存容量或 four-path 旧算法等价的完成声明。

## 4 本轮失败记录及修复

原日志保留，不覆盖、不标为旧基线 PASS：

| 记录 | 原结果 | 处理 |
| --- | --- | --- |
| `final-focused.log` | **FAIL：2 failed / 83 passed，91.43s** | 变量窗口未默认选择当前 Job；补首次选择，同时保留已有用户选择。旧连续测试在独立 capture/diagnostic 队列上过早检查 identity；改为 bounded wait 精确相同 invocationId 的事件持久化，不删 identity 断言、不加固定 sleep、不改 Runtime 顺序 |
| `final-focused-02.log` | **NOT_RUN：退出码 4，未收集任何测试** | 命令误用不存在的 `tests/runtime/test_global_variables_rpc.py`；修正为真实文件，后续先检查路径；不能称为测试通过或产品缺陷 |
| `final-focused-03.log` | **FAIL：1 failed / 190 passed / 1 skipped，94.65s** | 新 debug Job 隔离测试用了生产资源绑定夹具，普通 debug 编译仍要求显式 imagePath；只为此测试填实际图像路径，不放宽加载校验。加强为两个 Job 的 set/reset 和另一个非零值不变 |
| `debug-isolation-rerun-01.log` | **PASS：1 passed，6.36s** | 精确重跑修正后的隔离测试；后续整体组再次执行 |
| `final-focused-04.log` | **PASS：195 passed / 1 skipped，94.50s** | 包含修正项及受影响的新旧路径 |
| `native-ui.log` | **FAIL：ModuleNotFoundError: tests** | 独立验证脚本只设置了 src 路径，缺 repo 导入路径；在脚本 main 内显式加入 repo，随后重跑。不是现场或模型故障 |
| `native-ui-current.log` | **PASS：5 images；exit 0** | 当前代码的原生 Qt 证据重新生成，旧 UI 日志/图片仍保留 |

Ruff、Mypy、proto 和空白检查的最终结果及原日志摘要见 evidence。测试用的资源/数据库/相机标记均位于临时目录或忽略的 build 路径；没有修改用户现场配置、全局 Qt 偏好或真实设备。

## 5 安全复现命令

最终重点组，所有路径已核验存在：

~~~powershell
Set-Location 'C:\Users\jsdfhasuh\my_scripts\emo_master'
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:QT_QPA_PLATFORM = 'offscreen'
& 'C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe' -m pytest -q -rs `
  tests/core/test_production_station_contract.py `
  tests/runtime/test_production_stations.py `
  tests/runtime/test_spawned_hardware_stations.py `
  tests/designer/test_production_settings_dialog.py `
  tests/ui/operator_view/test_production_station_window.py `
  tests/runtime/test_production_continuous.py `
  tests/runtime/test_production_boundaries.py `
  tests/ui/operator_view/test_production_window.py `
  tests/designer/test_global_variables_ui.py `
  tests/designer/test_global_variable_review_fixes.py `
  tests/core/test_hardware_normal_fixture_package.py `
  tests/runtime/test_hardware_trigger_normal_chain.py `
  tests/runtime/test_global_variables.py `
  tests/core/plugin/test_icon_resources.py `
  tests/proto
~~~

广泛组为相同环境的 `python -m pytest -q tests/core tests/runtime tests/designer tests/ui tests/proto`，保留它自身较早源码快照层级。原生截图命令：

~~~powershell
& 'C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe' `
  scripts/validate_production_stations_ui.py --output build/m5_validation/native-ui-current
~~~

该脚本只创建合成图工程，不启动真实相机。复现时换一个新的输出目录，不覆盖已有证据。

原始日志目录：[build/m5_validation](C:/Users/jsdfhasuh/my_scripts/emo_master/build/m5_validation)。关键截图：

- [第一工位 1060×800](C:/Users/jsdfhasuh/my_scripts/emo_master/build/m5_validation/native-ui-current/operator-1060-s1.png)
- [第二工位 1060×800](C:/Users/jsdfhasuh/my_scripts/emo_master/build/m5_validation/native-ui-current/operator-1060-s2.png)
- [第一工位 1280×800](C:/Users/jsdfhasuh/my_scripts/emo_master/build/m5_validation/native-ui-current/operator-1280-s1.png)
- [第二工位 1280×800](C:/Users/jsdfhasuh/my_scripts/emo_master/build/m5_validation/native-ui-current/operator-1280-s2.png)
- [原生工位配置表单](C:/Users/jsdfhasuh/my_scripts/emo_master/build/m5_validation/native-ui-current/station-configuration.png)

这些生产页面的 `2` 是合成图中的 Blob 数测试源，不是现场两拍计数；硬件两拍计数见 §3 的 spawned 测试。不能从界面 fixture 文字推导硬件触发成立。

## 6 使用方式

1. Designer 打开工程，通过 **运行 → 生产工位配置…** 添加/配置两个工位，各选自己的入口、页面、拍次保护变量和入口输入；设备、PLC、模型、TXT 与网关仍在原算子表单配置。
2. 表单 **保存** 应用工程声明，再按原工程保存机制落盘。打开/取消不会升级旧工程；禁止删除仍被工位声明引用的入口/保护变量。
3. 生产端有 **全部开始/全部停止**，每个 tab 也有 **开始工位/停止工位**。硬件工位每次启动必须确认外部新周期第一拍同步；软件不会主动复位 PLC、清缓存或宣称设备已经 armed。
4. 普通重启一个工位不重载工程/模型/TXT，也不改变另一个工位的 Job 或显示。修改输入资源或工程后，先全部停止并释放，再显式重新加载/更新。
5. [硬件正常链能力夹具](C:/Users/jsdfhasuh/my_scripts/emo_master/examples/hardware_trigger_normal_fixture/project.json)默认不自动启动，相机/网络为模拟配置且不附真实模型。可用于看图和改参数，**不能直接作为现场运行工程**。

运行中的旧应用不会自动载入源码修正；查看本轮原生 UI/行为需要重新启动对应 Designer/生产 Runtime。没有生成新的冻结包。

## 7 剩余门槛与下一批

- **四条旧算法仍 PARTIAL**：PLC 采样业务含义、YOLO 模型/类别/区域、坐标公式、轴向/单位/两次转换、工位 1 Blob 内心、工位 2 矩形/被裁切左路，以及多孔配对/发送数量。不能用能力夹具的圆心、旋转矩形中心、相加测试公式冒充旧定义。
- **M6 准备管线仍待补**：声明式页面准备的 `coordinate_file` purpose/可信适配器/哈希整合，以及显式多根选择。目前多根 prepare 明确拒绝，生产只走 normal station capture；拒绝是已知边界，不是完整准备能力验收。
- **现场与包均 NOT_RUN**：真实模型样图对照、合法现场模型的冻结包、真实 SDK FIFO/容量/溢出、物理帧号/周期起点、相机 armed/触发握手、PLC/机器人接收及节拍，需要分别验收并另行授权设备/发布操作。
- **业务 NG 继续暂缓**；无效输入、断连、非法计数、发送不确定仍停止对应工位，不回滚复用旧图、不盲重试动作。

本轮交付是 **M5 生产编排与展示主体已实现，源码/原生 UI/实际 spawned 外部边界模拟通过；M5 四条原算法等价和 M6/M7 尚有明确剩余工作**。不是完整现场迁移完成。
