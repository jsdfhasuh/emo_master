# 独立完整工程草稿与 MCP 编排

此服务独立管理完整工程草稿。HTTP、MCP、文件上传和下载使用同一业务服务及一套工作区 Bearer 凭据，不依赖 Designer 进程，也不提供生产发布、启停或算子执行工具。

## 安装与启动

使用本仓库的 Python 3.10 环境。安装当前源码或本轮构建的 wheel，使实际导入的 `emo_master` 与 `emo_panel_protocol.generated` 来自同一目标版本；只更新旧 SDK wheel 以外的源码不会更新旧部署。

```powershell
python -m pip install -e '.[mcp]'
# 在进程环境中安全设置 EMO_WORKSPACE_TOKEN，勿将实际值写进工程或命令示例。
python -m emo_master.authoring --data-dir D:/emo-authoring-data `
  --target local=127.0.0.1:50051 --port 8765
```

默认监听 `127.0.0.1`；MCP Streamable HTTP 地址为 `http://127.0.0.1:8765/mcp`。目标 Runtime 是明确配置的另一个进程，目录查询和目标预检使用它的真实声明。无目标时仍可创建、读取、保存草稿并执行本地校验；新增普通算子节点需指定提供其声明的目标。

`--no-mcp` 关闭 MCP，并且不导入可选 SDK，HTTP 草稿服务仍可使用。`--timeout` 为目标调用预算（默认 30 秒，现有连接契约上限 60 秒）；`--max-transfer-bytes` 设置文件传输上限，默认 256 MiB。Runtime 候选协议的包大小上限为 128 MiB、单块上限为 256 KiB、解压总量上限为 256 MiB。

远程部署在受控 TLS/访问边界后进行，并显式配置 `--allowed-origin`、`--allowed-host`。凭据只发送到已配置的服务，不放入 URL、工程或日志。服务不会为普通浏览器链接取消下载认证，也不承诺 OAuth 或所有客户端的认证适配。

本轮实际验证组合：Python 3.10.21、官方 Python MCP SDK 1.30.0、MCP Inspector 2.13.0、协商协议 `2025-11-25`；文件方式是“Inspector + HTTP 辅助”。该 SDK 作为可选依赖固定为 `mcp==1.30.0`。实际验证是一个版本组合，不能推及全部协议或客户端。

## 发现能力并编辑

先调用 `get_authoring_capabilities`，或 `GET /api/authoring-capabilities`。响应包含明确目标、实际命令的 JSON Schema、工具输入结构、正式资源用途和文件传输方式。`apply_project_command` 的 `command` 包含具体 `type` 及必填字段；设备、页面、绑定和运行配置使用正式模型生成的嵌套 schema。

依次使用 `list_operators`、`get_operator` 和设备类型查询读取实际目标的声明；节点参数、端口和版本从该次结果取得。完整端口、参数数组/对象、条件扩展保留原结构。`nextCycleParameterPaths=null` 表示回复未提供，`[]` 表示明确空列表；缺 Core 版本或真实实例身份不补造默认值。`unavailableFields` 明确列出未知项，目标查询失败返回错误。

创建使用 `create_project_draft` 或 `POST /api/project-drafts`，body 为 `{"name":"Image project"}`。结果含 `draftId`、`draftRevision`、完整 `project` 和实际 `createdIds`。新工程使用正式默认模型，当前为工程 2.9、页面 1.2、设备 1.0。

示例编辑请求：

```json
{
  "draftId": "返回的草稿ID",
  "expectedDraftRevision": "最近一次返回的修订",
  "command": {
    "type": "node.create",
    "workflowId": "main",
    "targetId": "local",
    "operatorId": "vision.preprocess.threshold",
    "nodeId": "threshold"
  }
}
```

HTTP 对应 `POST /api/project-drafts/{draftId}/commands`，body 省略 URL 中已有的 `draftId`。参数配置使用 `node.configure`，`params` 完整替换，嵌套 ROI 区域保持对象和数组；变量绑定未传保留，显式 `[]` 清空。

每草稿独立会话、锁和撤销历史；成功的实际编辑生成新修订，一项语义操作只有一次事务。失败回滚，旧修订返回当前修订供客户端重新读取，不自动重放写入。undo、redo、save 都需要当前修订。保存后正常重启恢复最后保存的内容和新会话修订，不恢复未保存修改或内存撤销历史。

正常重启或导入工程后，声明不会因一次目录查询自动归属于草稿。先读取草稿，再调用 `adopt_project_declarations`，输入 `draftId`、当前 `expectedDraftRevision` 和明确 `targetId`；HTTP 对应 `POST /api/project-drafts/{draftId}/declarations`，body 传修订和目标。这条操作按工程已保存的算子依赖版本、设备类型及驱动版本重新采用目标真实声明，恢复本地校验、输出选项和声明相关编辑，不需要创建再删除临时节点。

声明采用不修改工程内容、参数、绑定或依赖版本，不改变保存时间、dirty 或内容撤销历史。检查上下文实际变化时返回新修订，后续调用必须使用该修订；重复采用同一声明不会仅因观察时间不同而反复生成修订。目标离线、版本不匹配或依赖缺少版本锁时返回明确诊断，保留原上下文和工程；仍可读取、保存，缺少可用声明时本地校验如实返回 INCOMPLETE。不会自动借用另一草稿或最近查询目标的声明。

`node.copy` 同时复制指向源节点的资源／现场参数绑定，在同一事务中把目标改为实际返回的新 `nodeId`。资源继续复用原 `resourceId` 和已有字节；源节点绑定不变，undo/redo 同时撤销／恢复副本及其绑定。

页面输出使用 `get_output_choices`，来源为当前草稿。只读页面使用 `get_project_presentation` / `GET .../presentation` 的局部快照；完整事务、保存、导出和预检继续使用完整校验及快照隔离。

## 上传、绑定、导出及下载

以下 PowerShell 使用同一凭据，不打印凭据值。先安全设置 `EMO_WEB_URL`、`EMO_WORKSPACE_TOKEN`；`$draftId` 和文件路径由操作者明确选择。

```powershell
$base = $env:EMO_WEB_URL.TrimEnd('/')
$headers = @{ Authorization = 'Bearer ' + $env:EMO_WORKSPACE_TOKEN }
$draft = Invoke-RestMethod "$base/api/project-drafts/$draftId" -Headers $headers
$file = Get-Item -LiteralPath 'D:/inputs/input.png'
$hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$uploadHeaders = @{} + $headers
$uploadHeaders['X-Expected-Draft-Revision'] = $draft.draftRevision
$uploadHeaders['X-Content-SHA256'] = $hash
$name = [Uri]::EscapeDataString($file.Name)
$uploaded = Invoke-RestMethod -Method Post `
  -Uri "$base/api/project-drafts/$draftId/resources?name=$name&purpose=input_image" `
  -Headers $uploadHeaders -ContentType 'application/octet-stream' -InFile $file.FullName
if ($uploaded.sha256 -ne $hash -or $uploaded.sizeBytes -ne $file.Length) { throw '上传核对失败' }
```

上传接收真实字节，并检查 `Content-Length`、提供的摘要及期望修订。用途从能力响应取得，当前正式枚举为 `input_image/model/page_image/icon`。替换时添加 `resourceId` query，写新物理路径并保留撤销或在途快照仍使用的旧字节。绑定必须携带 `$uploaded.draftRevision`：

```json
{
  "type": "resource.bind",
  "resourceId": "上传返回的资源ID",
  "target": {"workflowId":"main","nodeId":"loader","parameterPath":["imagePath"]}
}
```

随后各步继续传递最新修订。`POST /api/project-drafts/imports?filename=project.zip` 接收正式工程文件或 ZIP 的原始字节，创建新草稿并保留工程身份。缺资源可补齐，已提供但大小或摘要错误的字节会被拒绝；不能用本地路径代替文件传输。

导出支持 `project-file` 和 `project-zip`。完整 ZIP 要求声明资源齐全，只打包工程及声明资源，不递归打包运行日志或历史输出。导出不改变保存点，下载结果固定于导出时的修订。

```powershell
$draft = Invoke-RestMethod "$base/api/project-drafts/$draftId" -Headers $headers
$body = @{ expectedDraftRevision = $draft.draftRevision; format = 'project-zip' } | ConvertTo-Json
$export = Invoke-RestMethod -Method Post "$base/api/project-drafts/$draftId/exports" `
  -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $body
$destination = Join-Path (Get-Location) ('emo-project-' + [Guid]::NewGuid().ToString('N') + '.zip')
Invoke-WebRequest -UseBasicParsing -Uri ($base + $export.download.path) -Headers $headers -OutFile $destination
if ((Get-Item -LiteralPath $destination).Length -ne $export.sizeBytes -or `
    (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $export.sha256) {
  throw '下载核对失败'
}
```

`download.path` 为同源固定路径 `/api/project-exports/{exportId}/content`，必须带相同凭据。只展示 URL 不算下载。导出中的 `checkReport` 为 `{"status":"not_attached","draftRevision":"本次导出修订","performedByExport":false}`：它说明本导出没有附带检查报告，导出动作没有执行检查；不否认该修订先前已取得的 PASSED，也不证明该修订通过。服务不额外返回无作用域的 `precheck=not_run`。客户端保留已取得的报告，并核对其 `draftId/draftRevision/targetId/runtimeInstanceId`，不能把旧修订或另一目标的结果用于新导出。

## 校验、预检及取消

`validate_project_draft` 为 `scope=local`，无需在线目标，检查已知正式模型、引用、绑定和资源；缺必要声明或输入返回 `INCOMPLETE`，确定错误优先 `FAILED`。`compileStatus` 和 `executionStatus` 均为 `not_run`，合法但未配完整的草稿仍可保存。

`precheck_project_draft` 需要 `draftId/expectedDraftRevision/targetId`，冻结工程及资源，通过单 ZIP 流提交到目标。目标使用真实声明和编译器，只检查独立候选；不替换在线工程、不写在线配置、不创建 Job、不连接设备。报告含实际实例、被检修订、三态状态、编译状态和 `executionStatus=not_run`。`PASSED` 预检不等于执行成功。

诊断保留 `code/message/projectId/workflowId/nodeId/fieldPath`，已知时保留 `resourceId`。按位置修复，再使用新修订预检。正常 `FAILED/INCOMPLETE` 是工具成功返回的检查结果；非法编辑、修订冲突、目标不可达和传输错误是工具错误。

本轮固定协议按 SDK 的显式取消通知处理。普通断线不被统一视作撤销；连接结束不删除草稿，已提交编辑不自动 undo。正在修改的线程拥有草稿锁直到工作结束，候选编译退出前保留所用临时目录与执行额度；结束后清理。结果不确定时重连读取，禁止自动重放写请求。

## 重现本轮验收

`scripts/authoring_acceptance.py --inspector <Inspector的clients/launcher/build/index.js> --output <新的空证据目录>` 启动隔离本机服务，通过外部 Inspector 完成发现、编辑、缺项诊断修复、上传绑定、页面 mask、撤销重做、预检、导出及 HTTP 下载。之后它在隔离软件 Runtime 显式加载并运行下载工程一次；这是验收脚本的单独操作，不是 MCP 运行工具。脚本保存 `report.json`、`download.zip` 和解压后的完整工程，不访问现场设备。

V7 首轮交付见 [原交付记录](../testing/2026-11-13-independent-project-authoring/README.md)；V7.1 六项修复、本轮重新执行的结果、同一下载物的 Designer 验证及尚未关闭的检查见 [修复记录](../testing/2026-11-13-independent-project-authoring-v7_1/README.md)。两轮证据分别保存，不把原有测试数量计成本轮结果。
