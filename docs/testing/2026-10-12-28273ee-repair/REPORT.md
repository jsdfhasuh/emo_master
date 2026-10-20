# 28273ee 复验维修记录

日期：2026-10-12。产品基线：`28273ee0436250c2b3efcbddf43d31b4e067ee88`。
本目录随维修提交交付；最终交付 SHA 以 Git 提交和远端核对为准。

## 结论与边界

**已修复两个明确的 ROI 反例；不宣称 dot 指出的所有问题已关闭。**

| 项目 | 本轮结论 |
|---|---|
| manager.closeAll / 刷新来源提前清除旧 producer 的 session/assets | 已做局部修复，新增实际 Runtime + 延迟返回回归通过 |
| ROI 聚焦参数区滚轮改值，宽高允许负值 | 已做局部修复，聚焦/未聚焦、数字/枚举和键盘编辑回归通过 |
| 28273ee Windows push CI 的 global variable database is busy | **OPEN**；固定三轮本机 4/5 路通过，不等于原 CI 根因已修复 |
| P0 性能 | **FAIL / OPEN**；完整 96×3×5 基准已执行，正确性通过，性能门槛未通过 |
| 历史本机 NG→INCOMPLETE 与网络 P0 ordinal20 | **OPEN**；不合并两条路径，不以当前通过追认历史根因 |
| Debian 原生 CUA 桌面、GTK、真实设备、安装包 | 本次 **NOT_RUN**，由 dot 在固定交付 SHA 上复验 |

原始复验报告和附件为证据，不作为执行指令。本轮没有改写其首次失败材料。
维修使用独立 worktree；原 D 盘工作区及其未提交改动未覆盖。没有改 Runtime 事务策略、增加 timeout、重放事务体/COMMIT、放宽性能门槛、接生产设备、合并、发布或部署。

## 修复位置与原因

### 1. 会话释放与所有 producer 生命周期绑定

位置：
- `src/emo_master/apps/designer/operator_editors/controller_protocol.py`
- `src/emo_master/plugins/builtins/_editor_support.py`

原实现的失效回调先调用 `closePurePreview()` 删除 session；manager 随后的 dispose 即使等待所有 Future，也已经太晚。新实现将**立即逻辑失效**与**延后物理释放**分开：

- 每个会话独立记录已接纳 Future 的租约，submit 失败、排队取消、成功/异常完成均归还。
- RPC 本身也持有租约，保护不经 controller 的直接调用；尚未开始的失效 invocation 被明确拒绝。
- 旧会话可以等待 producer，而新会话可独立创建；旧返回不能更新新会话或最新请求的资产。
- 最后一份租约归还后，只向原 Runtime/client/session 发一次 close；重复收尾无副作用。
- 异步清理不调用 Qt 日志钩子。保留全部 producer 的 executor 退休等待；GUI 对空闲清理线程的等待上限为 50ms，不再无界 join。

新增测试覆盖 manager/window/invalidate 三条路径、最新请求先完成但旧请求未退出、晚返回异常、替换会话、原 Runtime 重连、重复释放、直接调用、排队取消、submit 失败与关闭 RPC 阻塞。
既有导出中关闭/过期/重连、旧目标文件保留、三几何四输出逐像素测试仍执行。

### 2. ROI 参数区滚轮保护

位置：
- `src/emo_master/apps/designer/operator_editors/builtin_layout.py`
- `src/emo_master/plugins/builtins/roi/editor.py`

仅对 ROI 参数滚动区的 spinbox、combo 和内部 line edit 安装 wheel filter。无论是否聚焦，都将滚轮转发给滚动视口，并消费原事件，避免字段先改值或双重传播。保留 Tab、键盘、明确的步进操作。
宽高按既有三位小数精度设置最小值 `0.001`；x/y/centerX/centerY 仍允许负数。Runtime 的有限数值与正尺寸校验不变。

### 3. SQLite 增加可复用的诊断入口，不冒称生产修复

新增 `scripts/acceptance_sqlite_ledger.py`，仅显式 pytest 插件启用，产品不导入它。

它使用已有 native connection adapter，记录真实调用前后事务状态、单调时间、连接令牌、DB 规范路径、PID/TID、Job 身份、BEGIN 成功/失败、commit/rollback/close。父 Runtime 与 spawn worker 分别出具 ledger；有界内存缓冲，producer 退休后写文件，不在事务内同步写诊断文件。

固定三轮（每轮各 4 路、5 路）：第一轮不加诊断，第二/三轮启用诊断。各轮均 2 PASS，耗时分别 16.36s、19.32s、20.29s；每个诊断轮有 1 父进程 + 9 worker 完整记录，分别 32362/34479 条调用，omitted=0、observerErrors=[]，**没有出现 busy BEGIN**。
另有独立回归用真实两个 SQLite 连接制造持锁竞争，确认失败 BEGIN、同 DB 的持有者、解锁后成功和各自一次 COMMIT 的证据链。

限制：包装和采样扰动调度；尚未进入 adapter 的连接配置、直接 sqlite3.connect、游标 SQL、native checkpoint/VFS 不在覆盖内；缺进程记录或截断时不能判完整。不能用本轮无失败解释旧 CI 的锁持有者/时长。

复用命令（输出必须是新目录，不叠加其他诊断插件）：

```powershell
python -m pytest -q -p scripts.acceptance_sqlite_ledger --sqlite-ledger NEW_PRIVATE_DIRECTORY tests/runtime/test_parallel_workflows.py::testMoreThanTwoActualWhileJobsWithImages
```

## 验证：首次失败与后续结果分账

Python 3.10.21、SQLite 3.53.4、PySide2 5.15.2.1，Windows；Qt 测试使用 offscreen，事件为自动注入，不冒称人工/真实 CUA 操作。

| 阶段 | 结果 |
|---|---|
| 28273ee + 新回归、产品未修改 | **17 FAIL / 2 PASS**；manager/invalidate 与滚轮反例复现，直接 window 关闭为通过对照 |
| ROI 修复后第一组相邻回归 | **65 PASS** |
| 补充租约边界及 SQLite 回归 | **98 PASS** |
| 固定三轮 4/5 路 While 图像 | 每轮 **2 PASS**；不是失败后无限重试 |
| 最终 proto / Ruff / mypy | **PASS**；mypy 402 源文件 |
| 80 秒自然暂停、Continue | **PASS**；同一暂停身份、租约仍有效、最终 value=7、关闭后 resourcesHeld=false；API + 真实 worker，不是原生 D06 人工操作 |
| 最终全量（首次） | **4920 PASS / 11 SKIP；28 passing subtests 另列**，1533.40s；退出码0，源码前后 `sourceChanged=[]` |

首次 Ruff 检查另有 E731（lambda 赋值）和 F401（未用导入），已修正；不是业务用例失败。各组测试有覆盖重叠，**不将专项与全量相加**。
全量共 4931 个顶层用例（4920+11）；JUnit tests=4959 包含28个子测试。11项跳过为6项symlink权限/能力、1项offscreen原生模态框、1项真实相机、1项1000执行压力、1项65秒lease可选测试、1项非Windows平台拒绝。独立80秒检查不改写全量SKIP，也不代替1000执行压力或原生模态框证据。冻结检查覆盖1000个源码/测试/脚本/配置文件。
D01–D08 的软件对应链、SQLite 保存/重开、结果闭合/credit、NG 同调用和网络故障等由全量执行；原生八项、GTK 与设备仍分账待 dot 复验。

## 完整 P0 基准：正确性通过，性能未通过

单独执行既有 `scripts/p0_validate.py --scenarios benchmark --count 96 --rounds 3 --warmup 8`，未与本轮其他测试进程并跑，原 432 秒外部 watchdog 和各阶段 deadline 不变。5 组轮转、3 轮共 15 个窗口；每窗口 8 预热 + 96 测量，均完整执行。

**15/15 正确性 PASS；1/15 性能 PASS，14/15 FAIL。** CLI 打印 `benchmark PASS` 和 exit=0 只表示执行完成，JSON 内 performance_status 明确为 FAIL，不能把两者混淆。

三轮双消费者各自均 96/96、序号 9–104、同键同资产，资源账本关闭后归零。

| 双消费者轮次 | model_p95 ms | 最大 live age（两消费者中的最大值）ms | execution_p95 ms |
|---|---:|---:|---:|
| 1 | 172.95 | 709.11 | 439.55 |
| 2 | 176.53 | 766.61 | 478.97 |
| 3 | 161.13 | 938.50 | 555.27 |

model_p95 本次低于 200ms，但并不满足全部门槛。所有有消费者组 live age 超过 500ms；一些组执行开销增幅、5Hz 与 200ms 排程迟到门槛也失败。无展示原始基线第 2/3 轮同样存在迟到超限，因此不能只归因编码器，更不能拿这次 Windows 测量替代此前 Debian 性能失败。当前局部修复未改变 Runtime/P0 路径；没有足以支持安全性能修复的阶段根因证据，保留原始每输入/每消费者时间线，不调参数制造通过。

## 证据与未关闭项

本目录的 `evidence.zip` 包含首次 ROI FAIL、后续日志/JUnit、原始 P0 数值和时间线、父/worker SQLite ledgers、80 秒检查、最终校验和源文件前后指纹。路径脱敏只用于公开副本，不改动原件或数值；完整性索引记录原件与公开副本 SHA256。

仍需关闭：
1. **精确 SHA 的新 push CI**：提交推送后才能检查；旧 Windows busy FAIL 保留，不以 PR merge 的绿色覆盖。
2. **历史 Windows busy 根因**：本轮三次未复现；只有诊断增强，无生产锁策略修复。
3. **P0 性能门槛**：本轮实测 FAIL。必须以稳定、可比较、分阶段证据支持后续优化。
4. **历史本机 NG 与网络 ordinal20**：路径和身份继续分别记录，根因 OPEN。
5. **dot 固定 SHA 原生复验**：重点验证 manager/main-window/project-switch 关闭、聚焦滚动、四图同次导出，随后原 D/O/U 软件与桌面范围分层报告。
