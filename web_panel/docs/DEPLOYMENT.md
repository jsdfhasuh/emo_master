# 独立 Web 编辑器交付、启动与备份恢复

Web 后端是普通 gRPC 客户端，目标电脑不需要安装 `emo_master`、Qt、设备驱动、推理包或 Node。目标电脑需要 Python 3.10+、Web wheel、配套 SDK 1.5.0 wheel 及其普通依赖。开发 wheel 不包含 Python 解释器；离线安装应另备对应 Python/系统架构的依赖 wheelhouse。

## 构建机

在仓库根目录执行：

本机C盘仓库路径是D盘的junction，开发构建请先切到实际 `D:\jsdfhasuh\documents\my_project\emo_master`；其他机器使用其实际仓库路径。

```powershell
& web_panel/.venv/Scripts/python.exe -m pip install build
npm --prefix web_panel/frontend ci
npm --prefix web_panel/frontend run build
& web_panel/.venv/Scripts/python.exe web_panel/scripts/package_frontend.py
& web_panel/.venv/Scripts/python.exe -m build --wheel web_panel --outdir web_panel/dist
```

一起交付 `web_panel/dist/emo_master_web_panel-0.1.0a1-py3-none-any.whl` 和 `web_panel/contracts/panel-contract-v1/python/emo_panel_protocol-1.5.0-py3-none-any.whl`。SDK manifest 记录固定源码基线、工作区修改状态、格式版本和文件摘要，见同目录 `contract-manifest.json`。保留旧 wheel 供软件回退，不把它与页面版本回退混用。

## Windows 安装和本机启动

以下由操作人员在交付目录执行，地址按实际 Runtime 修改：

```powershell
py -3.10 -m venv .venv
& .venv/Scripts/python.exe -m pip install ./emo_panel_protocol-1.5.0-py3-none-any.whl ./emo_master_web_panel-0.1.0a1-py3-none-any.whl
& .venv/Scripts/emo-web-panel.exe --data-dir D:/EmoWeb/panel-data create-user admin
& .venv/Scripts/emo-web-panel.exe --data-dir D:/EmoWeb/panel-data serve --mode grpc --runtime-address 127.0.0.1:50051
```

浏览器打开 `http://127.0.0.1:8780/editor`。密码交互输入，不写进启动参数。admin 拥有编辑和管理权限；操作员、查看者通过已有目标授权访问运行页。Runtime 正常运行即可，Designer 不必启动。Web 使用一个 ASGI worker；多 worker 不共享当前进程的订阅和视图状态。

Linux 使用 `python3 -m venv .venv`、`.venv/bin/python` 和 `.venv/bin/emo-web-panel`，其余参数相同。`--mode mock` 仅用于明确的合成演示，不作为正式 Runtime 替代。

## 受控局域网

服务与普通 gRPC 继续运行在现有受控 LAN。需要另一台电脑访问 Web 时显式配置：

```powershell
& .venv/Scripts/emo-web-panel.exe --data-dir D:/EmoWeb/panel-data serve --mode grpc --runtime-address 192.168.10.20:50051 --host 0.0.0.0 --allowed-origin http://192.168.10.30:8780
```

示例中 `192.168.10.20` 是 Runtime，`192.168.10.30` 是 Web 主机，请替换为现场地址。本交付不会自动改防火墙、网络地址、系统服务或设备设置。普通 HTTP 不启用 `--secure-cookie`；该开关仅用于已有 HTTPS 反向代理。浏览器不能直接提交任意 RPC、Runtime 文件路径或 SQL。

管理员保存的 Runtime 目标记录优先于 CLI 初始地址。改接另一台 Runtime 时，在管理区明确修改或新增目标，确认当前工程 ID、实例和修订，再进入编辑器。既有视图需要先关闭，避免活动订阅被静默改向。

## 创建两页与正式运行

完整步骤见 [README](../README.md)。操作顺序为：选择当前 Runtime 工程 → 检测页添加结果图片、数值、判定、任务状态 → 详情页添加表格或数值详情与导航 → 直接选择目录输出绑定 → 上传静态资源 → 保存草稿 → 示例预览 → 发布 → 在运行面板明确启动并选择准确任务 → 实时结果与切页。

保存草稿、打开预览、切页和发布都不执行检测。实时预览只读取所选已有任务的 DisplayService 结果，缺少来源显示“先发布，再明确启动新任务”。运行中发布不停止、重启或扩展当前任务；下次明确启动才采用新请求。Qt 页面继续走 Designer 原有入口。

## 发布恢复与回退

`PREPARED` 表示候选源和包已保存但尚未提交写入，可明确继续校验和发布。`REQUEST_UNKNOWN` 表示提交结果尚不明确，只查询原 request ID 或当前实际配置，不自动重放。`REQUEST_SAVED` 表示采集配置已确认而页面尚待启用，可继续完成原发布；该期间的新任务可能已使用新采集配置。`PUBLISHED` 才表示两端完成。

Runtime 拒绝时显示具体诊断并保留旧正式页。查询后仍无法确认时，可“结束此发布”保留草稿，再明确发起新发布；这个动作不撤销 Runtime 写入，也不声称旧请求未执行。后台没有自动补偿或自动重试写入。

页面回退从保存的旧完整编辑源重新生成请求，按目标当前工程修订校验、保存并启用。失效节点或调用路径会阻止该次回退并列出原因，不把旧图片、旧判定伪装为实时结果。正在运行的任务仍使用各自启动时的工程和采集快照。

## 备份

先正常停止 Web 进程，再完整复制 `--data-dir`，避免复制正在写入的 SQLite。任务仍由 Runtime 运行，不需要停止检测。使用新的空备份目录，例如：

```powershell
Copy-Item -LiteralPath D:/EmoWeb/panel-data -Destination D:/EmoWeb/backups/panel-data-20261103 -Recurse
```

整个目录包含 `web.sqlite`（草稿、源版本、发布记录、激活映射、目标、用户、权限和审计）、`editor-assets/`（上传静态资源）、`packages/`（不可变页面包及 `.emoweb`）和 `logs/`。保管为私有数据，不把目录提交 Git 或放到公开下载目录。运行任务和结果不由这个目录备份。

同一备份批次还应保存对应工程的当前 `.emoproj` 和受管工程资源，以及 Runtime 自身状态目录的正常备份。Runtime 的外部采集请求按工程保存在其 SQLite 状态中，Runtime 正常重启后恢复，不需要先打开 Web。Web 资料不能替代流程工程、设备配置或 Runtime 数据备份。

## 恢复或换电脑

1. 在独立环境安装匹配 Web/SDK wheel。停止 Web，把备份完整复制到一个新的数据目录；保留原目录供回退，不覆盖正在使用的目录。
2. 用恢复目录作为 `--data-dir` 启动。核对管理区的 Runtime 目标地址与权限；持久地址不会被 CLI 参数静默覆盖。新机器、新实例使用当前查询身份，旧发布回执不当作新 Runtime 的确认。
3. 在目标 Runtime 正常恢复或加载关联工程。进入 `/editor` 选择实际目标，刷新目录，确认 projectId、当前工程修订和来源诊断。恢复时沿用备份中的目标 ID，并在管理区明确修改其地址，可保留该目标下的完整草稿和资源；要注册新目标 ID 时应明确把保存的源和资源复制到新目标后重新校验，不能只改旧发布回执中的实例身份。
4. 对恢复的页面源明确执行一次发布：针对当前 Runtime 实例和工程重新校验、登记通用输出请求并启用页面。旧来源已经不存在时先修正绑定。不要只导入 `.emoweb` 而遗漏后续任务需要的请求。
5. 明确选择已有任务检查覆盖，或明确启动新任务，再验证图片、数量和判定同次对应。旧任务结果已释放时正常显示不可用，不用旧图片充当新结果。

恢复旧软件 wheel 是软件回退；编辑器中的版本回退是页面和后续采集请求的回退。二者都不自动停止或回放任务。

## 验证边界

已构建前端、独立 wheel 安装、本机软件 Runtime、浏览器、原生 Qt、另一台电脑与真实设备分别记录。当前工作证据位于仓库 `docs/testing/2026-11-03-independent-web-editor/`。部署、真实设备和现场接受程度以实际记录为准，不能从源码检查或 mock 演示推断。

保留的只读 `preview` CLI 使用隔离临时目录和一次性 token，只用于现有包检查。独立编辑器不依赖 Designer 的 Web 预览或导出入口；历史交接文档保留证据，不再作为新页面创建指引。
