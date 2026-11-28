# Operator Runtime R4 — OpenSpec 修订版交接

**状态：R4 三阶段功能已实现。** 验收状态见 `tasks.md` 和[实施与验收记录](../../../docs/testing/2026-11-28-operator-runtime-r4/README.md)。

本包替换原 `improve-operator-runtime-ui` 初版计划，沿用同一变更 ID、三个实施阶段及两份能力规格。无需建立第二个 R4 change。

## 使用方式

1. 在目标代码版本中检查 `AGENTS.md`、当前 OpenSpec 和最新源码，按项目已安装的 OpenSpec CLI 执行 `openspec validate improve-operator-runtime-ui --strict`（如 CLI 支持该参数）；处理真实格式/重复 requirement 问题。
2. 使用同目录的 `CODEX_PROMPT.md` 作为执行提示，依 `tasks.md` 推进。不要仅提交文档或勾选任务就宣称实现完成。

## 文件结构

```text
improve-operator-runtime-ui/
  .openspec.yaml
  README.md
  CODEX_PROMPT.md
  proposal.md
  design.md
  tasks.md
  specs/
    operator-runtime-shell/spec.md
    operator-runtime-diagnostics/spec.md
```

## 本次修订的关键取舍

| 原方案关注点 | 修订后的决定 |
| --- | --- |
| 单实例启停 | 仅全局按钮单实例，保留已有流程级按钮、全部停止及原状态约束 |
| 工具页停止始终可用 | 有可停止对象时保持可见，按原忙碌/关闭/任务状态决定是否可用；明确为软件停止 |
| 动态停靠 | 集中管理父布局、显示和样式，避免轮询复写、父页销毁和重复连接；工具页标准外观，业务页恢复作者配置 |
| 单行顶栏 | 常规单行，窄窗口/高 DPI 优先停止及返回，允许次要入口溢出或分行 |
| 共享主题 | 最小抽取，限制 QSS 作用域，不整体重构 Designer |
| 日志过滤与导出 | 当前已加载窗口搜索；四条件统一用于三种导出范围；起始页清筛选、普通工具返回保留；完整文件导出不受 UI 上限限制 |
| 日志表格 | Qt 模型/视图与增量更新，保留读位置、选择和原导出；仅针对现有卡顿做轻量前后对照 |
| 设备健康 | 以实际契约区分正常、非运行、维护、异常与未知，不猜字段，不把 TCP 零客户端当故障；同步更新既有测试调用方 |
| 监听设置 | 草稿、下次保存、当前有效值分开；只有真实来源才能标成 CLI 覆盖 |
| 现有运行控制 | 单活动 Job 直接停止；保留现有多流程启停、`_stopChoice()` 和 `viewRun()`，不新增或替换运行选择窗口；完整 Job ID 和归属校验沿用现有实现，仅验证顶栏迁移无回归 |
| CI/验收 | 阶段定向测试，最后统一正式 Source CI 和原生 Qt；不加无关 Worker 或重复全仓检查，不把模拟/截图当硬件或冻结 EXE 验收 |

## 源码审查与本机核对补充（2026-11-28，基线 `78a54727`）

- 本机仓库中 `operational-page-shell` 位于未归档变更 `openspec/changes/enhance-generic-page-composition/specs/operational-page-shell/spec.md`，已核对与本 R4 变更兼容。
- 本机实查 `DeviceManagementDialog` 在 `src/` 中当前仅由 `src/emo_master/apps/operator_runtime/main.py` 实例化（`openRuntimeLogs()` 含非 Operator 宿主回退提示）；测试中除 `tests/ui/operator_view/test_basic_runtime_feedback.py` 外，`tests/runtime/test_tcp_device_integration.py`（第 618 行）也直接访问了 `view.selector`，Phase 2.6 已同步纳入回归与迁移范围。
- 未经用户授权不推送、不合并、不发布，也不操作真实设备。获得授权推送时，评论使用实际执行角色，说明本次改动、验证与未验证项。
