# emo_master 独立 Web 页面编辑器与运行面板

`web_panel/` 是独立的 Python 3.10+/FastAPI + React 应用。它通过受控 LAN 的普通 gRPC 连接 Runtime，使用 **Panel Contract 1.0 / SDK 1.5.0**，无需运行 Windows Designer。Web 后端只依赖轻量 SDK，不导入 `emo_master`、Qt、设备驱动或推理包。

本期从新建 Web 页面开始。Web 管理完整 Presentation 1.2 编辑源、资源、草稿和发布版本；Windows Designer 继续管理 Qt 页面。Runtime 按工程保存与 UI 无关的输出采集请求，正常任务统一使用它，结果和图片复用 DisplayService。实现起始基线为 `d92ffd37960e8b8f527136151ab9ccb80054e78f`；当前源码交付及整体操作复查入口见[交接说明](../docs/handoffs/2026-11-04-independent-web-editor-review.md)。源码交付不代表已部署。

## 开发机安装与启动

从仓库根目录执行；Windows PowerShell 示例：

```powershell
py -3.10 -m venv web_panel/.venv
& web_panel/.venv/Scripts/python.exe -m pip install web_panel/contracts/panel-contract-v1/python/emo_panel_protocol-1.5.0-py3-none-any.whl
& web_panel/.venv/Scripts/python.exe -m pip install -e './web_panel[test]' build
npm --prefix web_panel/frontend ci
npm --prefix web_panel/frontend run build
& web_panel/.venv/Scripts/python.exe web_panel/scripts/package_frontend.py
& web_panel/.venv/Scripts/emo-web-panel.exe --data-dir D:/EmoWeb/panel-data create-user admin
& web_panel/.venv/Scripts/emo-web-panel.exe --data-dir D:/EmoWeb/panel-data serve --mode grpc --runtime-address 127.0.0.1:50051
```

将示例地址换成实际 Runtime 地址。默认运行面板是 `http://127.0.0.1:8780/`，独立编辑器是 `http://127.0.0.1:8780/editor`。新安装没有默认用户或密码，`create-user` 默认交互输入密码；无人值守初始化可用 `create-user admin --password-file <本机私有密码文件路径>`，格式、权限和清理要求见[密码文件初始化](docs/DEPLOYMENT.md#密码文件初始化)。编辑需要 admin 权限；操作员和查看者按现有 Runtime 目标授权访问。目标记录持久化后，管理员保存的连接地址优先于启动命令中的初始地址。

若只在可信本机使用，可跳过 `create-user`，给 `serve` 明确追加 `--local-no-auth`。首次打开即可使用本机管理员身份，无需创建账号或密码；默认仍是密码模式。**该管理员可编辑、发布并启停全部已登记的 Runtime，包括远端目标**。本机其他用户与进程也可使用此入口，不能通过反向代理或隧道向外开放。退出后需明确点击“进入本机模式”；真正关闭免密入口须停止服务或移除选项后重启。完整限制见[本机免密管理员模式](docs/DEPLOYMENT.md#本机免密管理员模式)。

本机仓库的 C: 路径经 junction 指向 D:；Node/Vite 开发构建请从实际 `D:\jsdfhasuh\documents\my_project\emo_master` 执行，避免跨盘 realpath 产生输出路径错误。已构建wheel的运行不需要Node。

`--mode mock` 是明确标注的合成演示，适合查看编辑操作，不作为 gRPC 连接失败时的回退。普通局域网 gRPC 不需要 TLS/mTLS。Web 的非回环监听使用明确的 `--host` 和 `--allowed-origin`，见[独立交付和备份恢复](docs/DEPLOYMENT.md)。

## 两页编辑、发布和运行

1. Designer 将流程加载或发布到 Runtime 后，独立打开 `/editor` 并登录。选择 Runtime；工具栏显示当前工程、实例和文档修订。流程结果区来自该 Runtime 已生效工程和算子声明，不包含 Designer 尚未发布的修改。
2. 使用初始空白“检测页”，添加结果图片、数值、判定指示、运行状态。新建“详情页”，添加结果表格或数值详情与导航按钮。页面目标固定为 Web，使用 `presentation.schemaVersion="1.2"`。页面标签支持重命名、复制、排序、首页和删除；画布、属性区和撤销重做编辑完整源。
3. 在属性区选择“工作流 → 节点 → 输出”，或从流程结果区拖放绑定。图片绑定 image，数值绑定 integer/number，判定绑定 boolean/string，表格绑定 collection。实际端口由目录决定；调用位置和结果作用域保留在来源定义中。节点重命名不改变来源 ID。
4. 静态图片通过上传进入 Web 资源存储。点击“保存草稿”后重开，页面、布局、来源和资源继续可编辑。保存不发布，也不改 Runtime 采集配置。示例预览明确标注“示例数据”，打开、保存和切页均不启动检测。
5. 点击“发布”。Web 保存候选源和 `.emoweb`，根据全部页面绑定生成通用输出请求，校验当前工程和采集能力，经 Runtime 确认保存后启用页面。运行中的任务保持原采集范围，发布不停止或重启它。
6. 回到运行面板，通过已有控制区明确选择工作流并“开始”。选择准确任务查看结果；图片、数量、OK/NG 使用同次封闭结果。详情页正常切换。Start 被接受不等于任务已完成，Stop 被接受不等于任务已终止，状态以 Runtime 为准。
7. 在编辑器选择已有任务进行实时预览。当前任务未采集的新来源显示“先发布，再明确启动新任务”。草稿不创建调试任务。布局修改发布后明确刷新或切换页面版本；新增来源在下一次明确启动时生效。

页面版本、请求配置修订和任务 `capturePlanRevision` 是不同身份。覆盖依据来源定义和结果作用域判断，不要求这三个修订相等。浏览器视图固定所选版本，收到页面更新通知后由用户明确刷新；多客户端不会改变任务采集范围。

## 未保存草稿与并发编辑

同一工程的 Runtime 重启不会丢弃未保存编辑，实时预览任务需要重新选择。
会话过期或退出后，在同一标签页以同一帐号登录可恢复该目标/工程的未保存草稿；
恢复不会自动保存或发布。关闭标签页前仍应保存到服务器。

其他窗口修改同一草稿时，编辑器保留本地内容并显示版本冲突。可比较两份源后，
选择采用服务器草稿（可撤销），或明确确认用本地内容覆盖所显示的服务器版本。
服务器若再次变化会重新提示冲突，不会无条件覆盖。
修复逻辑、实际测试和浏览器环境限制见[草稿保护修复记录](../docs/handoffs/2026-11-04-editor-draft-repair.md)。

## 发布状态与一次断连恢复

| 状态 | 已发生的动作 | 正常出口 |
| --- | --- | --- |
| `PREPARED` | 候选源和包已保存，校验连接中断时尚未提交写入 | 明确继续，重新校验后提交 |
| `REQUEST_UNKNOWN` | 原请求已发送，未取得可确认结果 | 查询原请求；不自动重放写入 |
| `REQUEST_SAVED` | 原请求回执或当前实际配置已确认，页面尚未启用 | 明确继续，核对实际配置后启用 |
| `PUBLISHED` | 采集配置与页面启用均已确认 | 选择已有任务，或明确启动新任务 |
| `REJECTED` | 当前工程、类型、来源或额度不满足 | 保留草稿和旧正式页，修复后新建明确发布 |
| `ABANDONED` | 用户结束此页面发布协调 | 保留草稿；原 Runtime 请求未被撤销或重放 |

查询只读取原请求回执和实际配置。回执缺失而实际配置匹配候选时，可确认配置已满足并继续启用；实际配置不同则继续显示未知。也可明确“结束此发布”，记录观察到的实际修订，再发起新的发布。这不声称原写入从未执行。Runtime 已保存但 Web 尚未启用时，新任务可能已经使用新请求，界面会如实显示。

回退使用保存的旧完整页面源，针对 Runtime 当前工程重新校验并提交后续任务需要的请求，再启用对应包。旧来源已删除时列出原因，保留当前正式页。流程变化后的失效来源保留定义和诊断；有效来源继续按任务快照展示，失效来源不能显示旧图片或旧 OK/NG。

## 构建、分发和验证

```powershell
& web_panel/.venv/Scripts/python.exe -m pytest web_panel/tests -q
npm --prefix web_panel/frontend test
npm --prefix web_panel/frontend run build
& web_panel/.venv/Scripts/python.exe web_panel/scripts/package_frontend.py
& web_panel/.venv/Scripts/python.exe -m build --wheel web_panel --outdir web_panel/dist
```

将 Web wheel 和供应的 SDK 1.5.0 wheel 一起交付。Web wheel 含已构建前端，目标电脑运行不需要 Node。保留独立 Python 环境，使用一个 ASGI worker 共享结果订阅和有界图片缓存。SDK 可从 `contracts/panel-contract-v1/` 或仓库 `docs/handoffs/runtime-web-panel-v1/sdk-1.5.0/` 获取。

检查证据见仓库 `docs/testing/2026-11-03-independent-web-editor/`。源码自动测试、实际软件 Runtime、真实浏览器、原生 Qt、wheel 安装验证和现场设备是不同层级；未执行的层级不能记为通过。现场验收不由合成演示替代。

旧 `emo-web-panel preview --package ... --context ... --host 127.0.0.1 --port 0 --json --no-control` CLI 仍可用于隔离的只读包检查。独立 Web 编辑闭环不依赖该 CLI，也不要求 Designer 导出包或启动预览进程。历史交接文件保留原始证据，不作为新 Web 页面的创建流程。

Runtime 历史日志继续使用既有 `/api/runtimes/{rid}/logs` 和 `/logs/export`，精确 ID/游标保留十进制字符串。网关诊断保存在 data-dir 的 `logs/`，与操作审计分开；不写凭据、token 或请求体。
