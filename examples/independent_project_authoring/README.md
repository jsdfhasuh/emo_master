# 独立工程草稿服务示例

`project.emoproj` 和 `assets/` 是本轮外部 MCP Inspector + HTTP 辅助实际导出、鉴权下载并核对过的同一工程，文件未经手工拼装。工程使用 2.9、页面 1.2、设备 1.0 格式，包含 Image Loader → Threshold、PNG 资源和页面 mask 绑定。算子版本来自本轮隔离 Runtime 的真实目录。

在 Designer 中使用“文件 → 打开工程”，选择此目录的 `project.emoproj`，或将整个目录复制到新的可写目录再打开。资源路径相对于工程目录，复制时保留 `assets/`。原生 Qt 自动化已经另外走过正式打开、修改名称、保存、重开和编译入口；人工桌面操作尚未执行。

工程已通过本轮真实候选预检，并在隔离软件 Runtime 中显式运行一次，结果为 `COMPLETED`。导出的工程自身不携带目标预检的永久成功声明；改变参数、资源或目标后，需要使用当前草稿修订重新检查。

使用新服务导入时，以 `application/octet-stream` 将 `project.emoproj` 上传至 `/api/project-drafts/imports?filename=project.emoproj`。单文件不包含 PNG 字节，随后按资源 ID 上传补齐；若要一次交付全部资源，应将此目录中的工程文件和 `assets/` 打包为 ZIP，再通过同一导入入口上传。所有请求使用工作区 Bearer 凭据。

启动、连接及上传下载的完整步骤见 [服务指南](../../docs/guides/independent-project-authoring.md)，实际客户端和验证边界见 [交付记录](../../docs/testing/2026-11-13-independent-project-authoring/README.md)。
