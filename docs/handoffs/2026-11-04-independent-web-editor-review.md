# 独立 Web 页面编辑器：源码交付与整体操作复查

日期：2026-11-04。用户已授权提交、推送首期实现，后续由 Dot 复查整体操作。本交接不代表合并主分支、发布安装程序或现场部署。

## 待审源码

- 分支：`codex/independent-web-editor`。
- 实现提交：`f4cf67cecce125873692173175064a7a037116a8`；其后的交接提交只增加记录和证据。
- 整合基线：`8b987ce393f83141738091ed35985695beba6f7f`，来自 `codex/logging-consolidation-20261103`。包含远端最新日志修复，与本地 Web 改动没有直接文件重叠；已执行下述交集回归。
- 实现最初依据：`d92ffd37960e8b8f527136151ab9ccb80054e78f` 加原工作区的未提交实现。原工作区和历史失败证据保留。
- [完整计划](../plans/2026-11-03-independent-web-editor.md)、[启动与编辑步骤](../../web_panel/README.md)、[安装和恢复说明](../../web_panel/docs/DEPLOYMENT.md)。

本分支依赖日志整合 PR #9 的基线。审查 Web 增量时对比 `8b987ce3` 与本分支；日志 PR 合入 main 后再处理主分支整合，不重复实现或丢弃其修复。

## 当前完成程度

首期两页编辑、完整页面源保存与重开、绑定、静态资源、布局、撤销重做、示例及实时预览、发布、运行中发布、回退和正常恢复已实现。Web 使用 DisplayService；Runtime 只持久化与界面无关的输出请求，任务使用启动时冻结的采集配置。Designer 继续负责 Qt 页面，Web 专用入口已移除。

前三轮确认的功能缺陷已修复，审查记录见[总记录](../testing/2026-11-03-independent-web-editor/README.md)及[第三轮记录](../testing/2026-11-03-independent-web-editor/review-round3/README.md)。任务工程/执行/采集版本目前显示在编辑器实时预览；正式运行页未提供同样的版本信息区，此项作为非阻断显示范围保留。

## 本次推送前实际检查

| 检查 | 结果与边界 |
| --- | --- |
| Runtime / SDK / Qt 采集，13 文件 | 190 passed，0 skipped；包括外部请求、额度、图片资源寿命、重启及协议 |
| Qt 启动 / 预览 / 日志交集，10 文件 | 59 passed，0 skipped，offscreen；含先前远端失败的 dispatch_queue_viewing 两组参数 |
| Web 后端完整检查 | 183 tests，OK，无跳过；启用独立源码软件 Runtime 子进程 |
| 前端 Node | 105 passed，0 failed，0 skipped |
| TypeScript / Vite | 通过，构建前端并同步 Web 包静态资源 |
| Ruff / mypy / proto | src/tests Ruff 通过，mypy 614 源文件通过，协议生成无漂移 |
| SDK 提交后字节 | 暂存内容分别按 core.autocrlf=false/true 重新检出，各 3 项合同精确字节检查通过 |
| Web wheel 独立安装 | 新环境 python -I 从 site-packages 导入；编辑源保存重读、资源、发布无隐式启动通过；无 Qt/emo_master/推理包，PATH 无 Node 时可启动 |

上述组有交叠，不相加。完整命令、退出码和首轮失败保存在[本次整合证据](../testing/2026-11-03-independent-web-editor/push-integration-20261104/README.md)。没有重跑整仓约 9000 项，也未将本机定向通过当作远端完整 CI 通过。原 `cbf362a8` 的 Windows CI 图片显示等待失败已保留；本次两组参数本机通过不能单独证明其根因已解决。远端后续结果须核对实际 SHA。

准备提交时另有三项局部调整：Web CI 两处 SDK 断言与步骤名更新为 1.5.0；SDK 和历史证据声明精确字节属性；超长路径测试根据 checkout 实际长度补齐夹具，继续保留大于 260 字符、资源字节和重开断言。未放宽产品逻辑或测试条件。

SDK 1.5.0 保留其真实生成来源 `d92ffd… / sourceDirty=true` 及原 wheel 摘要，不伪装为新提交生成。历史记录中的旧 Runtime/Web wheel 和 ZIP 属于当时基线；本次源码复查应从本分支重新构建。构建产物和运行数据不随 Git 提交，配套 SDK wheel 按既有合同分发方式保留。

## Dot 整体操作复查顺序

先读取 AGENTS.md、完整计划和本交接，记录本次实际 HEAD、解释器、构建来源。只用临时模拟工程、独立设置/数据库和回环软件 Runtime，不恢复现场工程或连接真实相机/PLC。正式操作检查须使用正常界面入口；自动化、模拟、人工桌面和安装后运行分别记录。

1. 独立启动 Web，Designer 不运行时选择 Runtime 工程；确认目录和工程身份正确。
2. 从空白页制作检测页和详情页，绑定结果图、数量、OK/NG、表格，上传静态图；保存并关闭重开。覆盖切页选择、复制绑定控件后删除再粘贴、根/嵌套网格调整和撤销重做。
3. 打开示例/实时预览及切页，确认不会隐式开始检测；明确选择已有任务，缺少来源时给出真实提示。
4. 正式发布后明确启动，核对同次图片、数量、判定、详情和页面切换。模拟示例应为两个目标、数量 2、OK、表格面积 1600/2400。
5. 在任务运行时修改布局和新增来源并发布，确认原任务不停止/重启，新增数据在下次明确启动生效；普通重连保持旧页面版本，明确刷新才采用新版。
6. **补完整 W08**：用 Designer 正常修改流程，删除/替换被绑定节点；Web 保留页面源、定位失效控件，其余有效来源正常；重新绑定、发布、明确启动新任务后恢复。
7. 走一次发布断连后的查询/继续、页面回退、关闭并恢复 Web/浏览器、Runtime 正常重启后本机启动。保留原请求和任务身份，不盲目重放未知操作。
8. **补原生 Qt 人工走查**：Qt 页面编辑、预览、运行、Designer GUI autoStart；确认 Web 入口收敛未损害 Qt 正常操作。
9. **补安装 Runtime wheel 后完整检测**，与已安装 Web 配套验证。当前完整历史浏览器闭环使用源码 Runtime，本次 Web wheel 安装检查没有替代此项。

源码模拟启动可在配置好 Runtime 依赖的独立终端执行：

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
python scripts/independent_web_editor_demo.py --directory <新的临时Runtime目录> --port 50061
```

另一个独立 Web 环境按 web_panel/README.md 构建安装并创建测试管理员，连接 `127.0.0.1:50061`，浏览器打开 `http://127.0.0.1:8780/editor`。Linux 使用相应 venv 的 `bin/python` 和 `bin/emo-web-panel`。模拟工程由当前 WorkflowStore 创建 2.9/页面 1.2 格式并校验、保存重读、编译；这不替代实际 Designer 打开。

复查发现问题时记录正常触发步骤、预期/实际结果、源码与截图/日志、最小修法；功能缺陷、非阻断显示细节和未执行验收分开列明。不要重复修已通过问题，不为假设性故障组合扩大范围。异机 LAN、真实设备、冻结 EXE/安装程序和现场部署按实际条件另列，未做就保持未执行。

公开证据保留关键成功和失败记录。临时 Runtime 数据、缓存、重复构建、完整起始工作区补丁及用户原始附件保留本地，排除清单见 [public-evidence-manifest.json](../testing/2026-11-03-independent-web-editor/public-evidence-manifest.json)。历史 README 对这些本地文件的引用不表示它们随本次 Git 交付。
