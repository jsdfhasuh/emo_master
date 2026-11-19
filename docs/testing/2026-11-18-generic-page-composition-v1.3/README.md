# 通用页面组合 OpenSpec V1.3 验证记录

计划来自 ChatGPT Web 提供的 `emo_master_pages_openspec_v1_3_closeout_patch_20261118.zip`。Codex 将其中的统一补丁应用到原 change `enhance-generic-page-composition` 后实施。验证基于 `ce8f84caf4d0fd779e579b23ff82fa4e80de978d` 和本轮工作区改动。

## 工程和操作

工程 2.10、页面 1.3。把[样例目录](../../../examples/generic_page_composition/)复制到可写目录，用 Designer 打开 `inspection.emoproj`。在软件 Runtime 加载这份工程。Web 使用 SDK 1.7.0，连接同一 Runtime 后，从「运行设置 → 页面包管理」导入并启用 `inspection.emoweb`。

生产页通过宿主启动「软件模拟检测与归档」，在「查看运行」明确选择作业后查看或冻结同次结果。「停止目标」独立选择。切到「历史回看」可筛选、翻条、回到最新并展开原始详情与日志。正式交付工程包含生产和历史两个页面。复制验收产生的页面只保存在临时 Web 草稿和发布包。

- [可编辑工程](../../../examples/generic_page_composition/inspection.emoproj)
- [同工程导出的页面包](../../../examples/generic_page_composition/inspection.emoweb)
- [SDK 1.7.0 和契约清单](../../../web_panel/contracts/panel-contract-v1/)

## 本轮验证

| 范围 | 实际结果 |
| --- | --- |
| Qt 原生窗口 | Windows 原生自动化通过。为验证持续结果中的冻结与恢复，临时副本使用 1800 ms 的连续采样。实际开始、跨新结果冻结、恢复、停止、按同键回看通过。两个视口均显示完整 UTC 时间及三行实际归档日志。生产判定浅底覆盖两侧，空值恢复组件原表面。38 项直接回归、8 项图片订阅回归通过。 |
| Web 整数编辑和生产页 | 在同一软件工程的 Runtime 目录用点击、拖放和属性下拉绑定实际整数输出。配置整数 `1`，保存、重开并发布，由下次明确启动的新作业取得采集值，实时预览显示所配文本。运行设置默认收起，展开设置、切页和冻结后观察及控制选择保持。实际结果为整行浅绿，清除查看后恢复配置表面并隐藏状态标记。[实际记录](web-a2-b.json) |
| Web 复制历史 | 实际 Chromium、独立网关和回环 gRPC 软件 Runtime。使用相同工程的六条真实归档记录，完成来源选择、复制、保存重开、发布、不同记录的独立选择和翻条、切回恢复及再次发布。主图和局部图与对应归档像素一致。该旅程复用数据库与图片，未启动新任务。[实际记录](web-a3.json) |
| 源码和契约 | Source CI 的 Ruff 全量检查和分流规则 9 项测试在本机通过。mypy 检查 636 个源文件通过。Panel Contract 源码测试 55 项通过。交付 wheel 安装到独立 Web 环境，契约清单与内嵌 fixture 复核通过。官方 OpenSpec CLI 2.1.0 strict 验证通过，`scripts/gen_proto.py --check` 通过。 |
| Web 回归 | 前端 197 项通过，TypeScript/Vite 构建通过。后端独立环境收集 230 项，223 项通过，7 项需要 owning Runtime 的检查按本地环境标记跳过。另执行 owning Runtime 测试文件，5 项通过。浏览器软件 Runtime 端到端验证真实登录、发布、两组启停及两个任务的图像归属和资源释放。资产退役、冒烟、响应式、编辑器草稿恢复、Dispatch、包与网格、正式预览以及独立 wheel 安装在本机通过。FastAPI/Chromium 的应用回归也验证了日志游标、冻结、启停、权限、移动布局和 Runtime 间的包隔离。[实际记录](r6-web-regressions.json) |
| 独立 Web 编辑器补充回归 | 使用软件 Runtime 通过双页创作、实时结果、发布回退及重连。作业中的观察端保持原页面包和作业，显式刷新才采用新包。浏览器及网关重启后，草稿、静态资源和连续作业的订阅恢复。 |

## 实际渲染截图

| 页面 | 1366×768 | 1920×1080 |
| --- | --- | --- |
| Qt 生产 | [截图](qt-production-1366x768.png) | [截图](qt-production-1920x1080.png) |
| Qt 历史 | [截图](qt-history-1366x768.png) | [截图](qt-history-1920x1080.png) |
| Web 生产 | [截图](web-production-1366x768.png) | [截图](web-production-1920x1080.png) |
| Web 历史 | [截图](web-history-1366x768.png) | [截图](web-history-1920x1080.png) |

1366×768 下可向下滚动查看历史操作与详细字段。[Qt 展开日志](qt-history-expanded-log-1366x768.png) · [Web 空值恢复](web-production-empty-row.png) · [Web 整数实时预览](web-editor-integer-live-preview.png)

## 复用的证据

基线的历史 29 项、Annotate 5 项和同一模拟流程的 5+1 次真实归档证据保留原归属。基线覆盖连续插入后按键翻条、同次归档图像与记录一致、历史引用刷新，以及 Designer 的工程编辑、保存和重开。[V1.2 实施记录](../2026-11-18-generic-page-composition/)

## 待确认

本地验证时，`main` 位于 `ce8f84caf4d0fd779e579b23ff82fa4e80de978d`，包含尚未提交的 V1.3 改动。GitHub Actions 的最终提交验收待记录。基线的两条工作流分别为 [Source CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44271356400) 和 [Web CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44271357295)。

现场相机、PLC、工位精度和外部冻结 EXE 未验证。
