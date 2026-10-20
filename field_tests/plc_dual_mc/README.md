# 双 PLC MC 二进制测试工程

日期：2026-10-16（第 2 版修正）。协议：MC/SLMP 3E Binary over TCP。

## 打开哪个工程

- 真机工程：本目录的 `dual_plc_mc.emoproj`。在新版 Designer 的“打开项目”中直接选择该文件，不是导入单个工作流。
- 全类型模拟工程：先运行 `serve_simulation.py`，再打开它打印的 `simulation/session/dual_plc_mc_simulation.emoproj` 文件。模拟服务使用本机随机空闲端口，不连接任何真实 PLC。
- `simulation/dual_plc_mc_simulation.emoproj` 是可复现的模拟模板；它的端口是占位值。实际联调使用启动脚本生成的 session 工程。
- 两个工程的默认入口都是 `read_both`，关闭自动启动，单次运行，最多一个 Job。不含相机、PLC 启停命令、无限循环或跨 PLC 数据复制。
- `内部/` 开头的工作流是有必填输入的步骤，供父流程调用，不应单独运行或设为启动入口。

## 已确认的现场范围

| PLC | 地址 | 默认路由 | 已授权测试范围 |
| --- | --- | --- | --- |
| .50 | 192.168.101.50:4998 | network=0, PC=255, I/O=1023, station=0 | D800 只读；D796 单字读写 |
| .51 | 192.168.101.51:4998 | 同上 | 同上 |

按用户“D796 可以写入”理解为两台均允许该地址。D796 写入期间可能被 PLC 程序观察到，测试前仍须确认它是空闲测试寄存器。不要改成生产命令地址。

之前已分别只读验证两台 D800，均读到原始字 `0x0006`（当时值为 6，不是固定断言）。本工程创建时未执行真机写入，未把模拟结果记为真机验收。

真机工程所有读操作仅访问 D796 或 D800，一个 16 位字；所有写操作仅访问 D796，一个字。没有 D797、D799、D801 或 M 区访问。没有把 D800 读出的集合接到写入端。

## 真机操作顺序

1. 确保电脑与 PLC 通信正常，关闭其他软件对这两个 MC 端口的占用。此前 .50 在被占用时会接受 TCP 连接后直接断开。
2. 使用本次更新后的 Designer 和 Runtime，打开 `dual_plc_mc.emoproj`。先运行默认的 `00 两台 D800 只读（默认入口）`，查看 `plc50_numberValue`、`plc51_numberValue` 及各自的 `values` 集合。
3. 分别运行 `只读/.50 D796 原值记录` 和 `只读/.51 D796 原值记录`，在开始写入前记录原始无符号整数。不要假设原值是 0 或 6。
4. 一次只调试一个 PLC，选择下面的一个 `写入/` 工作流作为单次测试目标。若运行按钮仍指向默认入口，先在启动入口设置中只选择该测试流程；测试结束恢复 `read_both`。不要选择“连续运行”。
5. 检查输出 `selectedValueMatches=true`、`firstWordRestored=true`，并确认 `original.values` 与 `restored.values` 相同，两个 receipt 的地址均为 D796、`wordCount=1`。
6. 再单独读取 D796，独立确认原值恢复；完成 .50 后再测试 .51。

| 每台的流程后缀 | 临时值 | 覆盖内容 |
| --- | --- | --- |
| `cycle_uint16` | 1 | `uint16`、`params.values`、写入回执、写后回读 |
| `cycle_int16` | -1（原始字 65535） | `int16` 负数解释、原始无符号字恢复 |
| `cycle_scalar` | 2 | 数值常量 → 严格转整数 → 写算子 `value` 输入 |

每个写入流程都通过必填子流程输入明确排序：

`读原始字 → 写测试值 → 按测试类型回读 → data 输入恢复原始字 → uint16 回读`

`data` 输入的地址、类型、数量来自集合本身，会覆盖写算子表单中的对应字段。恢复只使用本次流程从 D796 读取的原始集合，不手填默认恢复值。单值 `value`、列表 `values`、集合 `data` 和 `params.values` 四种来源互斥，不能同时提供。

## 失败和恢复边界

- 这是正常完成路径上的恢复，不是事务、异常 finally 或安全联锁。
- 断网、被占用、取消、关闭程序、回读失败或恢复失败时，流程可能在恢复前停止；写入返回失败也不等于 PLC 没有收到写入。
- 不要重新运行整个写入流程“试试看”，否则可能把测试值当成新的原始值。先单独读取 D796，再与第 3 步记下的原值核对。
- 确需人工恢复时，使用对应 PLC 的调试页，把设备设为 D、地址 796、类型 uint16、数量 1，显式解锁写入并填写先前记录的原始字；确认目标后写一次，再读取核对。没有可靠原值则停止，不猜测。
- 所有真机读写 `retryCount=0`。写入超时不自动重试，避免重复副作用。
- 编辑器的写入解锁和确认保护调试操作，不等于工作流有写入锁。运行含写入算子的流程本身就是执行写入，不能依赖编辑器的复选框阻止它。
- 单独调试 `内部/` 的写节点不会自动执行父流程后面的恢复；仅需检查界面时不要点击写入。

## 全类型模拟测试

在仓库根目录的 PowerShell 中启动：

```powershell
& 'C:\Users\dd109\.conda\envs\emo_master\python.exe' field_tests/plc_dual_mc/serve_simulation.py
```

使用同一台电脑上的 Runtime；`127.0.0.1` 指 Runtime 所在机器，不是远程 Designer。脚本会打印两个模拟端口及实际工程路径。保持脚本运行，在 Designer 中打开打印的 `.emoproj` 文件；Ctrl+C 关闭模拟服务。服务复用仓库现有的独立 MC 测试端实现，只监听 loopback。

模拟初值：D796=60000、D797=17、D798=18、D799=19、D800=6，M100/M103 为 true。每台除三个单字流程外，还有：

| 流程 | 输入 | 检查点 |
| --- | --- | --- |
| `cycle_uint32` | 4294967295、65541 | 双字低字在前、批量、outputIndex=1 |
| `cycle_int32` | -2147483648、-1 | 有符号 32 位、边界值 |
| `cycle_float32` | 1.5、-2.25 | 浮点编码、双字恢复 |
| `cycle_list` | 11、22 | 两个严格整数 → 数值对 → `values` 输入 |
| `cycle_m_word` | 9、65535 | M 字访问，两字覆盖 32 位 |
| `cycle_m_signed` | -1、123 | M 字有符号解释 |
| `m_bit` | 读取 M100 | `bit` 类型、`booleanValue` 输出 |

`firstWordRestored` 只比较恢复后的第一个原始字；多字用例还必须逐项比较 `original.values` 和 `restored.values`。自动回归会比较全部字及模拟内存，并在反转节点和边顺序后重跑，验证没有依赖布局或列表顺序执行。

当前普通写算子不支持 `dataType=bit`；M 位写入属于 PLC 专用调试页能力，不把它伪装成可运行的普通写算子。M 位多点操作、写入锁、确认、租约、请求去重、取消和资源互斥由下面的既有模拟回归覆盖。实际 UI 可以在模拟工程中的 PLC 算子调试页验证，不要把端点改回真实 PLC 来做 M 位或双字测试。

## 覆盖清单与验证

| 内容 | 验证位置 |
| --- | --- |
| 两台真实端点、D800 读取、D796 单字写读恢复 | 真机工程；实际写入仍待用户现场运行 |
| read 的 `values/numberValue/booleanValue`，count/outputIndex | 真机单字 + 模拟批量/M 位 |
| write 的四种输入来源及 receipt | 真机 params/value/data 恢复 + 模拟 values |
| 全部合法数据类型、D/M、字数限制、整数范围、来源互斥 | 模拟工程 + `test_communication_operators.py` |
| 路由字段、响应拆包、畸形包、超时、断连和重试 | `test_slmp_debug_transport.py`、`test_plc_debug.py` |
| 写入锁、逐次确认、租约、取消、端点占用、未知结果 | `test_plc_debug.py`、`test_plc_operator_debug.py`、Designer PLC 测试 |
| 窗口控件、精确数值输入、鼠标滚轮误改保护 | `test_plc_editor.py`、`test_plc_wheel_safety.py` |

在仓库根目录运行模拟回归，不连接真实 PLC：

```powershell
$env:HUARAY_CAMERA_SMOKE='0'
$env:QT_QPA_PLATFORM='offscreen'
& 'C:\Users\dd109\.conda\envs\emo_master\python.exe' -m pytest -q tests/runtime/test_dual_plc_field_project.py tests/plugins/test_communication_operators.py tests/plugins/test_slmp_debug_transport.py tests/runtime/test_plc_debug.py tests/runtime/test_plc_operator_debug.py tests/runtime/test_builtin_communication_operator_workflows.py tests/designer/test_plc_editor.py tests/designer/test_plc_wheel_safety.py
```

`build_project.py` 可重新生成两个模板工程，但会覆盖手工编辑过的模板；不会连接设备。生成后由回归检查与提交版本的一致性。`simulation/session/` 是被忽略的临时工程，不用于保存正式测试修改。

本工程覆盖正常路径和列出的边界场景，不代表所有 PLC 型号、设备故障或现场工况已验收。真机 32 位、浮点、批量及 M 区操作尚未授权，不能用模拟通过替代现场验收。

### 初版验证结果与缺口

- 上述 8 个测试文件共 **671 passed**（128.11 秒），包含新工程的 7 项回归；不是全仓库测试结果。
- 工程模型校验与全工作流编译通过；真机模板和模拟模板的所有公开流程均在 loopback 上执行通过，且反转节点/边顺序后再次通过。
- 真实运行新工程的默认 `read_both` 入口，两台均输出 D800=6，完整集合和标量输出正常；没有执行真实写入流程。
- 模拟启动脚本以 1 秒生命周期完成启动、生成临时工程和关闭检查，当前未留下后台模拟服务。
- 新增 Python 文件 Ruff 检查通过。结构化记录见 `validation.json`。

初版仅验证 Runtime 编译/执行，没有覆盖 Designer 打开。它沿用了兼容的 `project.json` 文件名，并遗漏节点的端口元数据，导致 Designer 加载时裁掉部分连线。第 2 版改用当前 `.emoproj` 扩展名（内部仍是 schema 2.4 JSON），为算子、子流程和边界节点保存完整端口信息，并增加 Designer 加载/保存/重新打开及画布边检查。不要继续使用旧的已打开草稿覆盖修正版文件；直接打开新的 `.emoproj`。

### 第 2 版验证结果

- 工程及原生 Designer 回归 **11 passed**（46.03 秒）：遍历真机、模拟模板全部工作流，检查实际画布连线、节点不重叠、保存并重新打开；公开流程经过 Designer Store 往返后在 loopback 执行通过。
- 项目文件名与存储相关回归 **30 passed**（4.75 秒）；本次未重跑上述历史 671 项测试全集。
- 检查了默认双 PLC 读取、D796 写读恢复流程的实际窗口截图。默认入口有 4 条输出连线；无外部输入，因此左侧 Workflow Input 无连线是正常的。
- Ruff 通过，模拟服务启动、生成新版 session 文件并关闭通过，没有留下后台服务。本次修正验证未连接真实 PLC，也未执行真实写入；现场调试页和 D796 写入验收仍待进行。

原生 Designer 回归命令（Windows）：

```powershell
$env:HUARAY_CAMERA_SMOKE='0'
$env:QT_QPA_PLATFORM='windows'
& 'C:\Users\dd109\.conda\envs\emo_master\python.exe' -m pytest -q tests/designer/test_dual_plc_project_open.py tests/runtime/test_dual_plc_field_project.py
```
