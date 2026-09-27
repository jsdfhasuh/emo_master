# P5-A 执行记录

起始分支 agent/runtime-workflow-architecture-v1，HEAD `0e45d09a3c2f0dc14ddbd432d24afcfc4c01f810`，工作区干净。
四个原待推送提交已逐一核对完整SHA和父子关系：
`dce0c66edaa2ea43e5992acf283162054661a6b7` → `d727505f35ac008f750acf383a1331a73d4a0dbc` →
`f94597406e2ed743ae731fbdc8287250915da6fc` → `0e45d09a3c2f0dc14ddbd432d24afcfc4c01f810`。

仓库外备份：`D:/jsdfhasuh/documents/git-backups/emo_master-p5-before-20260927-131653-9864.bundle`。
git bundle verify确认完整历史，list-heads确认分支末端为0e45d09；SHA256：
`b66490c53a33e5cee9fb70e51c40e167f98c17e780f313c4e0680157d09176c5`。
bundle不含未跟踪/忽略文件、环境或现场数据库；源码运行依赖沿用当前Conda环境，没有发现LFS过滤配置。

Git2.55.0.windows.5，HTTPS origin。普通push及一次命令级HTTP/1.1排查都失败：
`RPC failed; curl 55 Send failure: Connection was reset`，随后sideband断开。
两次独立ls-remote均确认远端仍为 `2e45f3b70a5b317e39e855c38de3dd22f7eefe1d`。
待推对象最大两个blob约2.78/2.76MB（P3原始日志），没有据此认定网络故障原因。
没有改TLS、全局网络参数、main或历史；停止盲目重复，按用户授权持有完整备份继续本地开发。

## 基线与批次A

基线命令：`python scripts/p4_validate.py --suite ci --output docs/evidence/p5/baseline-ci --timeout 600`。
proto/Ruff/mypy通过；pytest原生访问冲突3221225477，外层4294967295，完整CI FAIL；
崩溃后的测试NOT_RUN，不把无最终统计称为通过。原始输出及干净受测HEAD/源码摘要已保存。

新增正式导出入口、允许清单、便携参数、真实release校验和编译见P5契约。
`python scripts/p5_validate.py --suite export --output docs/evidence/p5/a-first --timeout 300`：
77 passed / 2 failed；新代码Windows只读fd调用fsync报Bad file descriptor。改为r+b同步，不弱化断言。
`a-fixed`同命令：79 passed / 0 skipped，Ruff/mypy PASS。全部原始结果保留。
两轮测试均记录HEAD+dirty、逐文件摘要、环境和code_stable；不是在未来提交SHA上运行。

A提交 `c2d6eeb` 后一次正常push仍curl55，独立远端仍2e45f3b，未同步。

## 批次B

新增DeliveryStore、跨进程目录所有权、ZIP前置准入和原子active/previous指针。
导入与启用分开；失败、坏回退目标和Runtime占用都保留当前版本。上限8个不可变版本，不自动清理运行数据。
`python scripts/p5_validate.py --suite import --output docs/evidence/p5/b-first --timeout 300`：
32 passed / 0 skipped，Ruff/mypy PASS，code_stable=true。
包括坏摘要/缺文件/不兼容版本、脚本、重复/别名/越界/链接/单文件和条目超限、原资源目录改名后验证、
中文空格目录和非项目cwd、跨进程锁、回退损坏时现用版及运行数据不变。
此时只完成包/版本机制，尚不称独立运行UI闭环通过；跨机、跨盘、EXE均NOT_RUN。

B提交 `330292ae2a4abb17be0862f63cb4aada4c025679`。批次正常push仍curl55，独立远端仍2e45f3b。

## 批次C、新问题及修复

正式ReleaseHost独占已启用版本及数据目录，使用P2 `mode=release` / 内容releaseRevision准备，
真实Capabilities握手后才产生ready.json。OperatorView独立源码入口不导入Designer，
显示“Runtime/项目/页面”三个就绪层次；明确按钮调用typed Start，其他显示使用原P2/P3共享客户端和渲染器。
单宿主一次测试启动，切页/多窗口/--job重连不创建新Job。关闭UI不停止外部Runtime。
源码Windows参数分派和默认Designer、自检兼容已覆盖；未构建EXE。

所有首轮失败保留，没有改断言/skip或增大单任务期限：

- `c-first` 11 passed / 2 errors：新增UI测试目录缺少qtApp fixture；补本目录正常Qt fixture。
  `c-fixture-fixed` 13 passed，Ruff/mypy PASS。
- `c-visible-first`、`c-start-diagnostic` 原生完整路径FAIL：独立宿主在标准输入阻塞读取期间，
  typed Start超时。线程栈定位在CPython `popen_spawn_win32.py:70 / CreateProcess`；
  诊断转储保留。去掉阻塞stdin生命周期，改为带实例握手的显式--stop控制文件请求。
  不改P2执行器、不提高Start/导出/读图期限，不把等待超时当作任务已停止。
- `c-nonblocking-host`：启动继续执行，但实际中文Runtime目录导致ImageLoader `cv2.imread`解码失败。
  修复Windows非ASCII路径用Python/NumPy文件I/O和原OpenCV编解码器；ImageSaver同样处理隔离中文输出。
  ASCII默认路径继续原调用，端口和算子版本语义不变。专项6 passed，修复独立提交
  `56cf723bbd7007fbb68cedb3b81c07930eef8e8f`，证据`c-unicode-fix`。
- `c-visible-fixed` 已通过第一次独立Qt闭环；最终两页静态说明均经Designer属性面板更新为测试项目交付，
  再保存/重开/导出，避免继承P4样例的“当前草稿”静态文字混淆release身份。

## 可复现入口和测试包

Designer：`python scripts/p4_demo.py --project YOUR_PROJECT`，工具栏“页面设计”编辑，
保存并登记输入后点“导出测试项目包”，选择项目外目录。运行阶段不需要Designer。

最终由实际Designer入口导出的包：
`docs/evidence/p5/c-accepted-visible/screens/packages/test-01d2a8bfd3badebc-0b0ca6ae.vxpkg`。
SHA256 `2c6a133557f3e12c3e0b235d9e386229697a6da3bdd1e58361eafc701f3819c5`；
内容revision `01d2a8bfd3badebc1a9854081b4b539227b598b1ba4d500dd0071ebc35738a5b`。
包内只有project.json、manifest.json、input.png（577字节），没有数据库/日志/旧包/证据/开发绝对输入路径。
两页配置在同目录`two-pages.json`，示例项目起于P4真实UI生成配置，本轮通过Designer表单编辑并导出。

在仓库根使用上述Python3.10解释器，目录D:/temp示例需由用户选择空测试目录：

```powershell
python scripts/p5_project.py import --package docs/evidence/p5/c-accepted-visible/screens/packages/test-01d2a8bfd3badebc-0b0ca6ae.vxpkg --store "D:/temp/P5 测试/store"
python scripts/p5_project.py activate --store "D:/temp/P5 测试/store" --revision 01d2a8bfd3badebc1a9854081b4b539227b598b1ba4d500dd0071ebc35738a5b
python scripts/p5_runtime.py --store "D:/temp/P5 测试/store" --data "D:/temp/P5 测试/runtime" --ready-file "D:/temp/P5 测试/runtime/ready.json"
# 第二终端：打开后点击明确启动按钮
python scripts/p5_operator_view.py --ready-file "D:/temp/P5 测试/runtime/ready.json"
# 第三终端：仅观察已显示在启动壳中的明确Job ID
python scripts/p5_operator_view.py --ready-file "D:/temp/P5 测试/runtime/ready.json" --job JOB_ID
# 关闭页面不停止宿主，需明确执行：
python scripts/p5_runtime.py --stop --ready-file "D:/temp/P5 测试/runtime/ready.json"
```

命令可将scripts路径写为绝对路径后从非项目cwd执行，真实测试正是如此。
import不activate，activate不Start；尚未开始时切两页没有Job。无固定sleep就绪判断。
本轮没有持久运行的现场Runtime；所有实际演示在临时项目/SQLite/输出目录内，并最终明确清理自有进程。

真实截图由windows平台QWidget.grab产生，已逐张复核；是Qt事件驱动验收，不声称人工鼠标或另一台电脑验收：

- [Designer保存重开](../evidence/p5/c-accepted-visible/screens/01-designer-saved.png)
- [独立页面待运行](../evidence/p5/c-accepted-visible/screens/viewer-1/01-ready-not-running.png)
- [运行总览](../evidence/p5/c-accepted-visible/screens/viewer-1/02-real-overview.png)
- [检测详情](../evidence/p5/c-accepted-visible/screens/viewer-1/03-real-detail.png)
- [退出后新观察者重连](../evidence/p5/c-accepted-visible/screens/viewer-2/02-real-overview.png)
- [完整路径证据](../evidence/p5/c-accepted-visible/screens/path.json)

独立Runtime和观看端都是实际子进程，真实spawn Job + loopback typed gRPC按ID读图解码。
原项目路径改名后不存在，导入目录含中文/空格，cwd不是项目也不是源码根；图像/资源全部来自包副本。
两观察进程（先后连接）确认同一Job `af4879fb-1c52-47b2-aa5c-6d10624fa6db`、
同一resultKey `c3b0d3aa-9fda-45df-a099-2486638b2664`、计数2和同一图像SHA256。
每个客户端两窗共享一次解码/Qt转换；关闭一窗会话存活，关闭整个观看端后宿主仍可握手、Job数量仍为1。
另有runtime专项同时两个独立DisplaySession验证共享结果及单方退出。
页面关闭不会StopJob，停止命令验证匹配实例后才要求宿主收尾。
低速小图仅证明功能；模型提交/paint时钟及原P2成本保留在view.json，不用它替代1080p/5Hz验收。

## 测试口径和已知限制

环境：Windows10.0.26200、Python3.10.21，解释器
`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`；PySide2 5.15.2.1、grpcio1.78.0、
protobuf6.33.6、numpy1.26.4、OpenCV4.10.0.84。HUARAY_CAMERA_SMOKE=0。
常规测试offscreen，截图独立windows。300/600秒仅外层进程树监督，未放宽P2的500ms导出/读图期限。

| 命令（python scripts/p5_validate.py） | 结果 |
|---|---|
| `--suite export --output docs/evidence/p5/a-fixed --timeout 300` | 79 passed；Ruff/mypy PASS |
| `--suite import --output docs/evidence/p5/b-first --timeout 300` | 32 passed；Ruff/mypy PASS |
| `--suite image-io --output docs/evidence/p5/c-unicode-fix --timeout 300` | 6 passed；Ruff/mypy PASS |
| `--suite runtime --output docs/evidence/p5/c-accepted-runtime --timeout 300` | 14 passed；Ruff/mypy PASS |
| `--suite affected --output docs/evidence/p5/c-affected --timeout 600` | 196 passed；Ruff/mypy PASS |
| `--suite visual --output docs/evidence/p5/c-accepted-visible --timeout 300` | 真实完整Qt路径1 passed |
| `--suite ci --output docs/evidence/p5/c-final-ci --timeout 600` | 本次完整CI退出0；1155 passed / 2 skipped，proto/Ruff/mypy PASS |

除完整CI外上述专项均0 skipped；集合有重叠，不能相加作为独立测试总数。
CI的2个skip另用`pytest -q tests/core/plugin/test_icon_resources.py tests/plugins/test_huaray_camera_smoke.py -rs`
监督复核，证据`c-skip-audit`（91 passed / 2 skipped）：Windows环境符号链接创建不可用，真实IMV相机未启用。
这两项未执行，不算PASS；不打开硬件开关补测。本次CI成功与本轮起始基线访问冲突并存，不能据此宣布旧崩溃已修复。
每轮保存受测完整HEAD、dirty、源码文件SHA、环境、原始日志/退出码，全部code_stable=true。
最终受影响回归及原生路径均是 `56cf723bbd7007fbb68cedb3b81c07930eef8e8f` + C批dirty源码，摘要
`095d289e2fd4c120c8823df4f0b294619d1b7be93df2fc5f0c0831e6886734fa`。
runtime14项受测摘要另为`ca3184c0b58a67b2894cdcdcc20f0d187ba502a4419b144c25ea1e25d58c0efc`，
之后只新增两项导入/导出边界测试，最终源码运行行为相同；不谎称它在未来干净提交上执行。

支持边界：本地图像内置算子、单scope/单图像源、现有P3组件；模型/静态图片资源、设备/秘密配置、
自定义插件、混合作用域/独立几何叠加、浮动编辑预览均明确不扩展。未知能力拒绝，不静默删掉。
包大小/条目/解压/Windows路径/摘要/版本/源文件复制变化均有拒绝测试。
总进程RSS/长期资源稳态、跨机/跨盘矩阵、目标工控机、冻结包/EXE、真实设备仍NOT_RUN。
原1080p/5Hz性能FAIL保持，本轮不重跑泛化性能；Qt组合崩溃基线仍实际重现，不声称修复。

最终CI与上述最终受影响回归/原生路径使用同一源码摘要，code_stable=true。

| P5-A收口项目 | 结论 |
|---|---|
| Designer实际两页状态导出、草稿修改后包不变 | PASS（已支持本地图像子集） |
| 安全暂存导入、单独启用、原子指针、失败保留旧版/数据 | PASS |
| 回退复核、Runtime持有目录时不切换 | PASS |
| 原资源不可用、中文/空格目录、非项目cwd独立运行 | PASS（本机换目录，不是跨机/跨盘验收） |
| 原生双页真实release图数、共享窗口、退出/再观察不新建Job | PASS |
| 默认Designer/旧打包入口兼容和相关回归 | PASS（上述196项和本次完整CI）；间歇Qt崩溃仍保留 |
| 本轮新增已复现问题 | 已修复并复测；全部首轮失败保留 |
| 原1080p/5Hz性能、资源长稳、Qt组合稳定性 | 原FAIL/缺证未解除，仍阻塞对应验收及现场发布 |
| 冻结EXE、另一台电脑、跨盘矩阵、真实设备、现场批准 | NOT_RUN / 未批准 |

结论：P5-A **开发态测试项目最小闭环**功能成立，整个P5及现场交付未完成。
单一测试宿主、一次明确启动、最多8个版本、受限本地图像能力等约束不隐藏。
停止本轮，不进入P6，不发布现场版本。最终本地/远端完整SHA、未同步提交和更新bundle位置
在交付答复记录，远端状态以独立ls-remote为准，不凭push中的Everything up-to-date判断。
