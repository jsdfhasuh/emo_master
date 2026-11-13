# 最小文件往返契约与客户端辅助步骤

**v5 拟议契约，待实施。** 本文件补充 design.md D6/D8，不代表端点已经运行。保持一套工作区 Bearer 凭据，不建设独立文件服务。示例采用 Windows PowerShell 5.1+ 的 HTTP 辅助方式；实际交付应记录为“选定 MCP 客户端 + HTTP 辅助”，而不是纯 MCP 自动读取电脑文件。

## 1. 能力响应与上传

`get_authoring_capabilities` 的 `fileTransfer.resourceUpload` 描述已实现的 method、pathTemplate、bodyEncoding、queryFields、purposeValues、revisionHeader、可选 checksumHeader 及返回字段；`fileTransfer.export` 描述支持的格式与下载认证。用途枚举来自正式资源模型，禁止另造一套 enum。尚未实现时明确 unavailable，不把计划字段当成已可调用能力。

| 项目 | 本次契约 |
| --- | --- |
| 资源上传 | `POST /api/project-drafts/{draftId}/resources` |
| query | `name`：展示文件名；`purpose`：正式资源用途枚举；`resourceId`：可选，指定替换该草稿的现有资源 |
| body | 原始文件 bytes；`Content-Type: application/octet-stream`，不是 JSON 中的本地路径或 base64 |
| 认证 | `Authorization: Bearer <工作区凭据>`，HTTP/MCP/上传/下载同一凭据，不进入 URL |
| 修订 | `X-Expected-Draft-Revision`，服务在接收完成后、修改工程前比较 |
| 长度/摘要 | 首期使用已知长度的普通文件上传，客户端发送 `Content-Length`，服务核对实际字节数；服务总是计算 SHA-256。可选 `X-Content-SHA256` 存在时必须核对，未提供时不能声称与客户端摘要比对过 |
| 成功结果 | `draftId`、`draftRevision`、`resourceId`、`name`、`purpose`、`sizeBytes`、`sha256` |
| 失败 | 不登记有效资源；按现有错误/修订冲突格式返回，不自动重放上传 |

文件名只用于展示，不作为任意服务器路径。替换写新物理位置；本条操作只是登记/替换，新增节点绑定仍调用实际已声明的语义命令。

**修订链：**GET 草稿取得 r1 → 上传携带 r1 → 上传成功返回 r2 → 绑定携带 r2 → 绑定成功返回 r3 → 校验/预检/导出使用 r3。中间若又有编辑，使用该次返回的最新修订；冲突先读取状态，不直接重试写入。

## 2. 导出与受保护下载

导出使用 `POST /api/project-drafts/{draftId}/exports`，JSON body 含 `expectedDraftRevision` 与 `format`。本版建议形式名为 `project-file`（单独正式文件）和 `project-zip`（资源齐全的工程目录 ZIP）；实际工具及 HTTP schema 必须与能力响应一致。候选预检 ZIP 是内部检查产物，不必新增面向用户的第三个导出工具。

以下只是响应结构示意；花括号中的描述由服务实际结果替换：

```json
{
  "exportId": "{服务生成的导出标识}",
  "draftId": "{源草稿标识}",
  "draftRevision": "{冻结的草稿修订}",
  "filename": "image-project.zip",
  "mediaType": "application/zip",
  "sizeBytes": 1234,
  "sha256": "{服务计算的64位十六进制摘要}",
  "download": {
    "method": "GET",
    "path": "/api/project-exports/{exportId}/content",
    "authentication": "workspace-bearer"
  }
}
```

`download.path` 是相对于已配置服务根地址的同源路径，指向该 exportId 的固定文件，重复下载不重新读取当前草稿。下载客户端添加 Authorization 头；服务不在 URL、结果或工程中返回 token。导出响应按 design.md 说明预检未执行/已有结果的所属修订，不能借用其他版本的通过报告。

只把路径显示给用户不算交付完成；普通浏览器点击通常不会自动附加自定义 Authorization 头。首期采用下面的携带凭据下载，或记录选定客户端的等价能力，不为方便点击而取消鉴权。匿名下载应被拒绝。

## 3. 一条可复用的 PowerShell 上传路径

以下步骤针对**实现完成后的**接口。先在本机安全设置 `EMO_WEB_URL` 与 `EMO_WORKSPACE_TOKEN` 环境变量；不要把真实 token 写入聊天、日志或计划。示例不打印 header。文件路径和 draftId 由操作者选择，代码不猜最近草稿。

```powershell
$ErrorActionPreference = 'Stop'
if (-not $env:EMO_WEB_URL -or -not $env:EMO_WORKSPACE_TOKEN) {
    throw '先配置 EMO_WEB_URL 和 EMO_WORKSPACE_TOKEN。'
}
$base = $env:EMO_WEB_URL.TrimEnd('/')
$headers = @{ Authorization = ('Bearer ' + $env:EMO_WORKSPACE_TOKEN) }
$draftId = Read-Host '输入已创建草稿的 draftId'
$draftKey = [Uri]::EscapeDataString($draftId)
$draft = Invoke-RestMethod -Uri "$base/api/project-drafts/$draftKey" -Headers $headers
$caps = Invoke-RestMethod -Uri "$base/api/authoring-capabilities" -Headers $headers
$purposeValues = @($caps.fileTransfer.resourceUpload.purposeValues)
if ($purposeValues.Count -eq 0) { throw '服务尚未提供资源上传用途声明。' }
$purpose = Read-Host ('选择真实资源用途：' + ($purposeValues -join ', '))
if ($purpose -notin $purposeValues) { throw 'purpose 不在服务声明的枚举中。' }
$file = Get-Item -LiteralPath (Read-Host '输入本地 PNG 的完整路径')
if ($file.PSIsContainer) { throw '应选择普通文件。' }
$hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$uploadHeaders = @{} + $headers
$uploadHeaders['X-Expected-Draft-Revision'] = [string]$draft.draftRevision
$uploadHeaders['X-Content-SHA256'] = $hash
$nameQuery = [Uri]::EscapeDataString($file.Name)
$purposeQuery = [Uri]::EscapeDataString($purpose)
$uri = "$base/api/project-drafts/$draftKey/resources?name=$nameQuery&purpose=$purposeQuery"
$uploaded = Invoke-RestMethod -Method Post -Uri $uri -Headers $uploadHeaders `
    -ContentType 'application/octet-stream' -InFile $file.FullName
if ([long]$uploaded.sizeBytes -ne $file.Length -or $uploaded.sha256 -ne $hash) {
    throw '上传返回的大小或摘要与本地文件不一致；先读取状态，不自动重传。'
}
$uploaded | Select-Object draftId, resourceId, draftRevision, sizeBytes, sha256
```

`-InFile` 由 HTTP 客户端发送文件及其长度。需要替换时，query 再加入经 URL 编码的现有 resourceId。随后由 MCP 按 `get_authoring_capabilities` 返回的真实绑定命令 schema 提交 `resourceId` 和 `uploaded.draftRevision`；绑定目标使用正式 ParameterTarget/DeviceParameterTarget，不在示例中假造尚未固定的命令字段。绑定成功后继续使用返回的新修订。

## 4. 一条携带凭据的导出下载路径

在完成绑定、修复和必要检查后执行；沿用上面的 `$base`、`$headers`、`$draftKey`。若发生并发修订冲突，停止并重新读取，不自动重试修改。

```powershell
$draft = Invoke-RestMethod -Uri "$base/api/project-drafts/$draftKey" -Headers $headers
$body = @{
    expectedDraftRevision = [string]$draft.draftRevision
    format = 'project-zip'
} | ConvertTo-Json
$exported = Invoke-RestMethod -Method Post -Uri "$base/api/project-drafts/$draftKey/exports" `
    -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $body
$expectedPath = '/api/project-exports/' + [Uri]::EscapeDataString([string]$exported.exportId) + '/content'
if ($exported.download.path -ne $expectedPath) { throw '下载路径与服务契约不一致。' }
$destination = Join-Path (Get-Location) ('emo-project-' + [Guid]::NewGuid().ToString('N') + '.zip')
Invoke-WebRequest -UseBasicParsing -Uri ($base + $expectedPath) -Headers $headers -OutFile $destination
$received = Get-Item -LiteralPath $destination
$receivedHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
if ($received.Length -ne [long]$exported.sizeBytes -or $receivedHash -ne $exported.sha256) {
    throw '下载文件大小或摘要不符；不要把该文件作为已完成交付。'
}
Write-Output ('已核对下载文件：' + $destination)
```

这里没有另建 MCP 文件系统工具、服务器读取客户端路径、免密下载或自动运行功能。实现者须在所选客户端环境实际执行这些步骤或等价方式，再记录成功；本次规范交付只检查示例文本，不声称已经调用真实服务。
