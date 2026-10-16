# 双工位正常路径迁移：本轮实现与验证记录

- 日期：2026-10-09。
- 计划：沿用 `docs/plans/2026-10-09-two-station-hardware-trigger-migration-v1.md` v2。
- 基线：`agent/runtime-workflow-architecture-v1`，HEAD `61a42bf3ab181c3b2339f0425edfb40a60d0ec7b`，加未提交改动。没有提交、推送、应用版本发布或生产部署。
- 解释器：`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`，实测 Python 3.10.21。
- 使用路径：`C:/Users/jsdfhasuh/my_scripts/emo_master`；实测与 Git 返回的 `D:/jsdfhasuh/documents/my_project/emo_master` 为同一目录，不是另一份待部署 checkout。
- Qt 测试使用 `QT_QPA_PLATFORM=offscreen`，先导入 `emo_master`；真实 Qt 控件/事件循环测试不等于打包版人工或现场 UI 验收。

## 1 实现清单

| 领域 | 本轮实现 | 兼容及限制 |
| --- | --- | --- |
| 全局变量 | 既有 Write 增加 increment/reset；原子操作；必填非空 after；本帧输出 n 与后续 reset 独立 | 默认 set 不改变；结构字段静态绑定；常量、预览保护保留；没有专用拍次节点/旁路计数库 |
| 编辑器/编译器 | int64 精确控件；非法草稿提示；接口变更拒绝静默删线；活动/非活动工作流重载；模式端口编译校验 | 打开旧节点不自动改参数；没有工程版本升级 |
| 相机 | 显式 hardware 等待；SDK ≤100ms 取消检查；会话内 contiguous 帧序检测；生产配置变化停止，不重新猜拍次 | 默认 bounded/sequence off 保留；预览强制有界；真实 SDK 缓存容量/设备释放未验收 |
| 视觉/几何 | 最小外接圆、单对象 bbox、显式取点、点坐标回源；坐标 add 严格配对/显式广播 | 不拿 overlay 测量；不拿 Hough/质心代替外接圆；没有用质心冒充 Blob 内心；不提供设备单位转换 |
| 固定坐标 | 默认 perInvocation 兼容；session 准入冻结所有可达 Reader 的字节/解析配置/哈希；Job/根入口隔离 | 固定文件错误发生在设备节点和 job.started 之前；停止重准入后更新；页面声明式坐标资源准备待补 |
| 网关 | 图内单坐标对格式化及请求关联 ACK 校验；PLC 整数规则、机器人两位小数；严格同接收块尾随字节检测 | PLC ACK 只证实历史可核对的 X/符号；机器人 ACK 非动作完成；单次连接/不确定发送不自动重发 |
| 示例与资源 | 独立能力夹具，四条结构路径；实际中间算子＋外部边界替身；既有运行包收集 TXT/模型、搬迁编译与准入 | 不把原控制骨架升格；假模型文件只证明资源收集，不证明 ONNX 或旧算法等价 |

新增注册算子为：

1. `vision.analysis.minimum_enclosing_circle`
2. `vision.geometry.detection_bbox`
3. `vision.geometry.extract_points`
4. `vision.geometry.reframe_points`
5. `vision.flow.error`
6. `communication.gateway.coordinate_format`
7. `communication.gateway.ack_validate`

注册数从 53 增至 60。测试保留明确的新算子 ID、manifest/meta 一致性、中文参数标题以及零拒绝项断言，不通过取消校验规避注册问题。

## 2 结果表

| 检查 | 结果 | 证据范围 |
| --- | --- | --- |
| 重点回归，10 个文件 | **PASS：76 passed in 5.90s** | 变量模式、Qt 编辑、真实 Runner 控制、SDK/协议边界模拟、固定准入、实际视觉能力链、运行包搬迁 |
| 类型标注收尾后回归，14 个文件 | **PASS：92 passed in 8.39s** | 上述重点测试加原注册、ONNX、heartbeat 回归；与前行重叠，不累计独立测试数 |
| 原失败项重跑，5 个文件 | **PASS：20 passed in 4.16s** | 注册、真实 ONNX 原回归、拍次控制、worker heartbeat 队列释放；不与 76 项相加计算独立覆盖 |
| 最初广泛回归 | **FAIL：16 failed / 2227 passed / 2 skipped** | 初始运行期间尚有 schema 调整；原始失败不覆盖、不标为 baseline PASS |
| Designer 历史本轮快照 | **PASS：893 passed in 110.66s** | 发生在最后小范围变更前，不能代替最终快照回归 |
| 稳定实现的广泛回归，5 个目录 | **PASS：3389 passed / 2 skipped in 798.12s** | `tests/plugins tests/runtime tests/core tests/designer tests/ui`；随后仅两处类型标注收尾，并对影响链重跑 92 项；没有把较早快照称为收尾后全仓库重跑 |
| 其余目录初跑 | **FAIL：1 failed / 184 passed in 90.49s** | `tests/e2e tests/p0 tests/proto tests/sqlite_writer`；旧版本能力门槛测试未包含新增七个算子 |
| 其余目录修正后重跑 | **PASS：185 passed in 89.76s** | `tests/e2e tests/p0 tests/proto tests/sqlite_writer`；保留旧 Core 兼容门槛，不降低新算子的 minCoreVersion |
| Ruff | **PASS** | 所有本轮修改/新增 Python 文件；未改为宽松规则 |
| Mypy，6 个重点模块 | **PASS：no issues found in 6 source files** | `--follow-imports=silent`：全局变量契约、Writer、坐标算子、几何桥接、网关报文/ACK、快照服务；不是全仓库类型验证 |
| git diff --check | **PASS** | LF/CRLF 警告不等于空白错误；没有新增空白错误 |
| 实际 Runtime 双连续 Job / 生产分工位 UI | **NOT_RUN** | 目前未扩多入口生产编排和页面过滤 |
| 原工程真实模型/业务公式/Blob 内心等价 | **NOT_RUN** | U01～U05 未确认；能力夹具不替代对照 |
| 冻结现场包、真实相机/PLC/机器人及节拍 | **NOT_RUN** | 没有现场授权操作或设备验收 |
| 业务 NG | 本阶段不实施 | 按用户范围暂缓，技术错误仍停止正常输出 |

重点复现命令：

~~~powershell
Set-Location 'C:\Users\jsdfhasuh\my_scripts\emo_master'
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:QT_QPA_PLATFORM = 'offscreen'
& 'C:\Users\jsdfhasuh\.conda\envs\emo_master\python.exe' -m pytest -q `
  tests/plugins/test_variable_write_modes.py `
  tests/plugins/test_camera_hardware_wait.py `
  tests/plugins/test_geometry_bridges.py `
  tests/plugins/test_gateway_coordinates.py `
  tests/runtime/test_two_shot_variable_control.py `
  tests/runtime/test_coordinate_snapshots.py `
  tests/runtime/test_coordinate_snapshot_worker_admission.py `
  tests/runtime/test_hardware_trigger_normal_chain.py `
  tests/designer/test_variable_write_modes_ui.py `
  tests/core/test_hardware_normal_fixture_package.py
~~~

## 3 失败修复记录

- 初始广泛回归的相机注册出现 `E_META_PARAM_SCHEMA_MISMATCH`；该运行与相机 schema 调整重叠。最终以同步后的 schema/manifest 重新扫描及回归验证，不能直接将初始失败抹去或认定是旧基线问题。
- 注册及标题测试硬编码旧数量 53；增加七个算子后更新为 60，并显式检查新增 ID，保留 manifest 数与有效注册数相等、零拒绝及中文标题校验。
- Worker 在 `job.started` 前调用新增的 `prepareResources`；heartbeat 专项测试的旧 Runner 替身缺少该方法。只为该替身补真实接口，不在生产代码用 `hasattr` 跳过强制资源准入。另加 worker 级缺文件/解析/编码故障测试，证明准入失败不产生 job.started、workflow.started 或 node.started。
- 静态检查首次指出 Writer schema 的字典类型推断和坐标快照服务的 object/get 类型问题；收尾修复与复验结果在最终表中记录。
- 其余目录发现旧 Core 0.4 的兼容测试只允许三个拒绝项，没有包含新增七个算子。更新精确拒绝集合，保留 `E_CORE_VERSION_INCOMPATIBLE`、旧基础算子可注册和当前 Core 零拒绝断言；该文件 21 项重跑通过，没有降低 minCoreVersion。

两个跳过项另用 `-rs` 确认：真实相机 smoke 未设置 `HUARAY_CAMERA_SMOKE=1`（没有启动现场设备）；Windows 环境不允许创建符号链接。跳过不算 PASS，不取消原保护或启用真实相机测试去填满通过数。

广泛回归之后只补了两处无业务行为变化的类型契约：Writer schema 字典注解、Reader 的快照 Provider Protocol/cast。收尾后的 92 项对这两条影响链重新执行并验证；其它广泛结果保留自身源码快照层级。

全部测试目录按上述两组执行；3389 与 185 来自不相交目录，但不是同一个最终快照单进程运行。重点/收尾、原失败项重跑及跳过原因核对都与这些目录重叠，不能把它们再次累加。原始失败日志、最终源码/日志摘要另存于同目录的 `2026-10-09-two-station-normal-path-evidence.json`。

完整原始日志位于忽略的 build 目录：

- `C:/Users/jsdfhasuh/my_scripts/emo_master/build/two-station-focused-final.txt`
- `C:/Users/jsdfhasuh/my_scripts/emo_master/build/two-station-post-typing-final.txt`
- `C:/Users/jsdfhasuh/my_scripts/emo_master/build/two-station-failed-rerun.txt`
- `C:/Users/jsdfhasuh/my_scripts/emo_master/build/two-station-source-regression.txt`（初始失败）
- `C:/Users/jsdfhasuh/my_scripts/emo_master/build/two-station-designer-regression.txt`（较早快照）
- `C:/Users/jsdfhasuh/my_scripts/emo_master/build/two-station-full-regression-final.txt`
- `C:/Users/jsdfhasuh/my_scripts/emo_master/build/two-station-other-regression-final.txt`（其余目录初跑失败）
- `C:/Users/jsdfhasuh/my_scripts/emo_master/build/two-station-other-regression-rerun.txt`
- `C:/Users/jsdfhasuh/my_scripts/emo_master/build/two-station-skip-reasons.txt`
- `C:/Users/jsdfhasuh/my_scripts/emo_master/build/two-station-mypy-final.txt`
- `C:/Users/jsdfhasuh/my_scripts/emo_master/build/two-station-ruff-final.txt`

## 4 剩余门槛

1. 原工程四条路径的测量区域、模型/类别、业务公式、单位/舍入、两次转换及 Blob 内心/矩形定义仍需逐项填实，不能使用夹具公式冒充。
2. 多目标/多孔规则未确认前只验证单目标；没有猜网关数组或逐孔动作协议。
3. 双工位生产入口、每工位 Job 状态/变量选择、拍次停机编辑保护、独立启停、启动同步/设备占用、页面过滤与展示资源释放仍须实现并验证。
4. 通用运行包的资源可搬迁与“包含合法现场模型的冻结生产包”是不同层次；后者没有验收。
5. 真实帧号、缓存容量/FIFO/溢出、PLC 曝光锁存或握手、物理周期起点由 U06/U07 现场契约明确。源码边界模拟不证明它们成立。

本轮交付结论是正常链路的通用能力和能力夹具已经落地；不是“完整工程迁移完成”。
