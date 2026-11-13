# 验收：七组业务观察目标

本文件把 specs 中的行为场景合并到实际交付路径，不要求“一条 Scenario 一套测试”，也不新增独立 CI 产品线。优先复用现有测试；公共逻辑改动验证对应桌面操作，不为每个 HTTP 接口重复人工桌面验收。

## 贯通示例

使用真实目录中的 `vision.io.image_loader` 和 `vision.preprocess.threshold`，端口、参数与版本以目标实际返回为准：

查询目标/命令/文件约定 → 搜索 Threshold → 读取完整详情 → 创建流程 → 上传 PNG → 使用上传的新修订绑定 Image Loader → 连接 Threshold → 页面绑定 mask → 编辑及撤销重做 → 本地校验与保存 → 目标预检 → 读取一次缺项/错误诊断并按位置修复 → 用新修订再预检 → 导出 → 携带凭据实际下载并核对 → Designer 正式打开、修改、保存 → 隔离软件 Runtime 明确运行一次。

必须先从详情得知模式和配置规则，不能测试代码预先硬编码所有参数再绕过能力发现。资源、页面、预检和正式交付使用同一工程样例。诊断修复可从“导入一份暂缺 PNG 的合法草稿 → 预检 → 上传补齐 → 更新绑定 → 再预检”开始；无需故意损坏工程或为未涉及的异常新建矩阵。示例未实际执行前，不填写成功结果。

## 七组验收

| 组 | 主要观察 | 最小验证方式 | 对应能力 |
| --- | --- | --- | --- |
| G1 独立工程与保存 | 无 Designer 创建/保存/重读；离线 validate 不依赖目标，不完整但结构合法的草稿可保存 | 一个无窗口流程；明确 scope=local、未编译/未运行及缺项，正常重启读回 | project-draft-authoring |
| G2 能力发现与共用规则 | 真实目录值到达 MCP，未知/空列表不混淆，命令与事务一致；D 公开入口接到高级能力 | Threshold 与一个复合 ROI 代表核对四字段映射及嵌套内容；版本用实际注册/目标值，保留功能断言。复用页面/隔离测试；D 用既有 fixture 验证高级公开命令及保存重读 | operator-capability-discovery、project-draft-authoring |
| G3 草稿状态 | 两份草稿独立、旧修订不覆盖新值、旧报告不冒充当前版本 | 两个草稿和两个基于旧修订的请求即可，不做并发压力矩阵 | project-draft-authoring、candidate-project-precheck |
| G4 资源与导出 | 真实上传/修订传递、替换保留旧读取、固定导出实际下载 | 同一 PNG；上传返回新修订用于绑定；一个未完成反例；下载核对 sizeBytes/sha256 并重读 | project-resource-delivery |
| G5 真实预检 | scope=target；真实诊断可修复；B 不替换 A，预检不是执行 | 同一候选缺项/错误 → 按 code/fieldPath/对象修复 → 新修订再预检；核对真实编译结果及 A 身份 | candidate-project-precheck、mcp-authoring-access |
| G6 真实边界的正常取消 | 请求取消按阶段 A 选定协议；不提前释放在用资源，不删除草稿/撤销已提交操作 | 选定组合的一次取消/请求流断开及重连读取；实际异步边界复用已有用例，不扩协议或故障组合矩阵 | candidate-project-precheck、project-draft-authoring、mcp-authoring-access |
| G7 实际客户端交付 | 选定外部客户端真实贯通发现、编辑、文件往返、诊断修复和交付 | 记录 Python/SDK/协商协议/客户端和 HTTP 辅助方式；实际鉴权下载及一次无凭据拒绝；Designer 继续编辑、软件运行；关闭 MCP 基础服务可用 | mcp-authoring-access、project-resource-delivery |

## 算子支持信息的必要断言

在 G2/G7 内完成，不新增“支持度测试系统”：

- Threshold 的真实模式枚举、blockSize 的 xOdd/xEnabledWhen、threshold 输出 nullable 不被简化映射丢弃。
- 在具备新 ROI 声明的目标或明确标记的对应源码 fixture 中，递归核对 regions/items/properties、purpose、enabled、points 与 xVisibleWhen/xValidateWhen；经新公开配置入口做一次保留/排除区域编辑、保存重读及撤销重做，不使用 Qt 窗口。它是 G2 内代表用例，不再跑一整套端到端流程。
- 同一目录映射核对源已具备的轮廓点数筛选与最小外接圆 scope 字段；旧目标据实返回旧声明，不补造新版本。现有算法测试已覆盖的分支不重复穷举。
- 贯通 4a602120 的现有字段：真实 Runtime 序列化 manifest.nextCycleParameterPaths/minCoreVersion/maxCoreVersion 和自身实例身份，实际客户端解析后 HTTP/MCP 值保持一致；仅生成文件含字段不能判为通过。
- 在同一目录适配测试中核对未提供/空字符串、JSON `[]`、真实非空路径列表三种普通情况；未知不折叠为空列表，Core 版本与实例身份缺失不补造。fixture 标明来源，不要求部署多个旧版本 Runtime 或重建兼容矩阵。
- 源码注册测试保留 manifest 与目录版本一致的断言，资源请求用 roiInfo.version/descriptor.manifest.version；远端测试只使用目标返回版本，不将本地版本强加给目标。动态版本不替代 ROI 嵌套参数、条件字段、资源摘要、预览和注册快照的原有断言。
- 服务只列出已实现命令，无法执行的特殊编辑有明确说明；Runtime 列出算子不等同于该编辑已接入。
- 算子详情查询不执行模型或连接设备。缺文件时在预检报告缺项，而不是篡改算子目录为“不支持”。

## 阶段 D 与文件往返的补充说明

G2 的高级操作使用已有 fixture 和同一公开命令测试入口，可参数化；至少含一个 Subflow/Loop/Dispatch-Join 代表操作、一组设备定义/绑定与变量配置、一项页面/生产配置代表修改，并经正式保存重读核对字段及引用。不是重测所有底层算法，也不能拿底层单测通过代替新入口接通。没有设备连接或现场动作要求。

G4/G7 必须实际获取下载字节；只展示 URL 或只用 SDK 脚本不满足实际客户端文件往返。HTTP 辅助是允许的交付方式，记录为“客户端 + HTTP 辅助”，按 file-transfer.md 更新到真实实现的命令并使用同一凭据。上传后的绑定、修复后的再检查都使用最新返回修订。

## 新基线下的测试复用边界

参考基线除 ROI 组合区域、轮廓点数筛选、合并求圆、页面快照及工作区初始化测试外，还已有 f663c007 的 ROI 编辑器版本修复。优先保留并使用 `tests/runtime/test_operator_editor_preview.py`，不重做该修复。优先使用 `tests/plugins/test_roi_regions.py`、`test_contour_point_filter.py`、`test_merged_contour_circle.py`、`tests/core/presentation/test_edit_session.py`、`tests/designer/test_workflow_store.py` 及直接受影响的桌面测试。测试文件存在不等于本轮已运行，也不能代替新 MCP/HTTP 入口验收。

仅页面读取应保留新 `PresentationStore.snapshot()` 的局部校验和独立对象行为；复用原有“不调用整个工程序列化”和“撤销/重载后无陈旧缓存”的测试即可。不要引入全项目毫秒级性能预算、新压力框架或用户现场图像配方验收。用户提交中的历史性能、原生窗口和现场图像证据，按其原始范围记录，不标为本计划的新执行结果。

上轮读取的 `docs/testing/2026-11-13-workspace-integration/README.md` 记录了生成一致性检查、10 项描述符兼容断言及 91 项相关测试通过；这是既有报告，不是本包本轮复跑，也不证明四字段已由 Runtime 返回或 MCP 已验收。后续接入后运行直接受影响测试；proto 再变化时继续使用 `scripts/gen_proto.py --check`，不另建协议 CI 产品线。

## 验证边界与完成记录

A/B 阶段可以交付可用中间结果，但要把 C/D 未完成项保留为未完成。测试环境没有相机/PLC 并非本轮失败：本轮不要求未涉及的硬件验收。SDK 协议测试、Qt 自动化、人工桌面操作、真实软件运行分别记录，不能互相替代名称。

一份简短完成记录即可：执行 SHA/分支、关键改动、A–D 完成范围、实际测试命令及结果、Python/SDK/协商协议/真实客户端版本、文件辅助方式、示例工程位置与打开步骤、未验证项。无需每个反例一份报告。所有假 Runtime/模拟输入必须标明，不能冒充真实目标或现场设备。

只有本变更的能力完成并经必要验证后才勾完任务及归档；规范格式校验不算功能验证。修改 proto 跑已有生成一致性检查，不为文档组织额外建立持续集成任务。
