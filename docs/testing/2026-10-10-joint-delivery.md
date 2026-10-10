# 全部工作区联合交付

日期：2026-10-10。分支：`agent/runtime-workflow-architecture-v1`。整理起点：`61a42bf3ab181c3b2339f0425edfb40a60d0ec7b` 加用户明确授权一起提交的全部工作区改动。

状态：全部交付源码的隔离全量回归和静态门禁通过，范例及单点交互专项通过。本记录随全部工作区改动联合提交；精确提交号和推送确认以交付消息及远端分支为准。

## 范围

- 算子共享执行器、单算子调试、流程调试 B0/B1/B2、Designer 入口、受监督 Worker、类型化资产、隔离变量、命令去重/暂停确认，以及 B3 评估。
- 先前并行开发的普通多工作流 Job 控制、事件/结果归属、运行目标与并发设置、长期 While、变量原子操作、相机硬件等待与帧序、固定坐标快照、几何桥接、网关报文/ACK 校验，以及相应测试和双工位夹具。
- 新增三个设备无关 `.emoproj`、PNG/JSON 输入、可重复生成脚本、真实 Worker 和 Designer 打开测试、dot 联合验收步骤。
- 为全部提交消除原有 5 文件 10 条 mypy 错误：补充异构字典容器/导出槽标注、给已知 schema/diagnostics 字典补 cast、避免源码/编译节点和 schema 字段的局部变量混用。无 `type: ignore`、门禁放宽或依赖变更，不改变执行语义。

不提交缓存、实时数据库、密钥、本机设备配置、`manual_test_workspace` 或构建临时物。保留历史失败证据，不删除失败记录来制造通过结果。

## 验证

| 检查 | 结果与证据 |
| --- | --- |
| 三个范例实际 Worker 执行及 Designer 打开 | 6 PASS；输入/输出、原始工程字节和生产 Job 隔离均有断言 |
| 范例原生单点交互 | 7 PASS；`operator-step-2026-10-10/joint-single-point-final.xml`；独立数值/阈值算子、进入/跳出/跳过、普通/第二次命中断点、循环条件、运行到指定节点、数值/图像试运行隔离；一次操作只产生一个新暂停点 |
| 调试联动回归 | 62 PASS；`operator-step-2026-10-10/joint-debug-regression.xml`；含新增 7 项和原生入口、RPC、控制器、范例执行 |
| 隔离检出最终调试联动复验 | 62 PASS，58.83 秒；`operator-step-2026-10-10/joint-debug-candidate-final.xml`；包含最终加强的暂停序号和试运行 mask 逐像素断言 |
| 完整交付源码 mypy | 392 文件 PASS；历史 10 条错误已修复，不再沿用基线失败状态 |
| 全部 src/tests 及新范例生成器 Ruff | PASS |
| 隔离检出完整回归 | 4288 PASS、10 SKIP、28 subtests PASS，1139.18 秒；`operator-step-2026-10-09/joint-delivery-full/`；运行中源码无变化 |
| 隔离检出 Proto/Ruff/mypy | 全部 PASS；`operator-step-2026-10-09/joint-delivery-static/`；运行中源码无变化 |

全量测试运行期间未修改隔离检出的 `src/proto/tests/scripts`，记录中 `sourceChangedDuringRun=[]`。新增 7 项单点测试在全量启动后编写，全量结束后才复制到隔离检出；这之后产品源码没有变化。提交前按 Git 规范化内容比对 958 个 `src/proto/tests/scripts/examples` 文件，待提交树与隔离检出一致。各轮测试有重叠，不把 4288、62 和 7 相加冒充一次全量结果。

10 项 SKIP 分别是 7 项平台/符号链接条件、1 项真实相机测试、2 项显式启用的长测。长测的既有单独运行记录仍见 B0-B3 审查，默认跳过不计为通过；真实相机仍为 NOT_RUN。

范例构建首次将 manifest 的描述型端口直接写入只接收类型字符串的节点缓存，被实际 ProjectDocument 验证拒绝；已改用既有 normalizePortType。新增图片测试首次未解包 decode 的 `(value, bytes)` 返回值，以及两个新测试文件同名导致 pytest 收集冲突，均已修正测试代码并通过 6 项验证。没有绕过工程校验或削弱图片内容断言。

新增单点交互测试首次有 7 项失败，见 `operator-step-2026-10-10/joint-single-point-initial.xml`。原因属于新测试夹具：误将试运行快照当作带 phase 的流程快照、访问尚未创建的流程窗口属性、忽略测试准备阶段普通工作流导航已填充派生缓存而产生的 dirty 状态。修正为按 snapshotKind 识别试运行、安全关闭未打开的窗口，以及比较调试前后 dirty/草稿/撤销栈是否一致；未清除 dirty、未保存工程、未修改产品代码或放宽输入/输出断言。独立阈值输出与原图二值化逐像素一致，流程暂停点输入与真实 GaussianBlur 输出逐像素一致，并明确断言两种输入/输出有差异。

## 验收边界

源码本地验证不等于远端 Actions、真实相机取帧、PLC/机器人联动、业务算法等价性或安装包验收。当前没有操作真实设备、发送现场写入、重启用户旧进程、打包、发布或部署。用户表示由 dot 执行联合实机测试；未代替用户发送跨任务消息或启动实机操作。

使用 [范例 README](../../examples/workflow_debugger/README.md) 和 [dot 验收清单](2026-10-10-dot-joint-acceptance.md)。通用调试器仍只允许已审查算子；双工位实际设备链走正常运行或既有专用设备入口，不承诺在长设备等待中进行通用断点调试。
