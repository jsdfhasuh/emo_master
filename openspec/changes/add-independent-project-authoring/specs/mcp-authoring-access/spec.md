# HTTP 与 MCP 工程编排入口

## Purpose

为网页和选定外部 AI 客户端提供一致的工程编排入口，使能力发现、编辑、检查与交付可以通过明确输入和结构化结果完成；该入口只适配同一工程服务，不构成远程桌面控制，也不自动获得生产执行权限。

## ADDED Requirements

### Requirement: One authoring service with optional MCP

系统 SHALL 使 HTTP 和 MCP 调用同一业务服务并获得一致编辑语义。关闭或未安装可选 MCP 时，基础 HTTP 草稿服务仍可运行。任何入口均不得为完成草稿操作依赖 Designer 进程。

#### Scenario: Use the same draft through two interfaces

- **WHEN** 客户端经 MCP 修改草稿，随后经 HTTP 查询
- **THEN** 查询到同一修订和工程内容，而不是两套独立业务状态；关闭 MCP 不破坏已有草稿读取

### Requirement: Actionable tool contracts

系统 SHALL 暴露可发现的能力查询、编辑、保存、校验、预检和导出工具，使用有明确命令结构的输入和机器可读结果。算子目录通过查询工具返回，不为每个算子自动创建执行工具；业务检查失败与工具调用失败须可区分。

#### Scenario: Discover then configure an operator

- **WHEN** AI 先发现目录工具，读取某算子详情及编辑命令结构，再提交配置
- **THEN** 能在不查源码的前提下构造合法请求，结果包含修订与定位诊断；FAILED/INCOMPLETE 预检报告不是伪造的成功执行记录

### Requirement: Single workspace access boundary

系统 SHALL 对 HTTP、MCP 和文件上传下载使用同一工作区凭据与适用的请求来源检查，凭据不得写入工程、日志或下载 URL。首期只承诺选定受控客户端的认证接入，不要求多用户角色及 OAuth 客户端矩阵。

#### Scenario: Deny unauthenticated file access

- **WHEN** 客户端未携带有效工作区凭据请求编辑或下载导出物
- **THEN** 请求被拒绝，不能因下载路径不同而绕过同一工作区访问边界

### Requirement: Connection-independent drafts

系统 SHALL 使草稿生命周期独立于 MCP 连接和会话。单个进行中请求的取消遵循实际协商协议及所选 SDK，不能硬编码所有版本相同的断线语义；请求取消不得删除整个草稿、擅自撤销已提交操作或自动重放写操作。未完成工作按实际边界退出，重连后可以重新读取草稿状态。

#### Scenario: Reconnect after an uncertain edit response

- **WHEN** 修改响应返回前连接断开，客户端随后重新连接
- **THEN** 客户端可以查询当前工程和修订确认结果，服务不重复执行原修改，也不销毁草稿

### Requirement: Explicit client file transfer and handoff

系统 SHALL 提供可发现的文件传输约定：上传的 method/path、字节 body、用途/替换标识及期望修订、结果中的 resourceId 与新修订；导出的固定下载路径、文件名、类型、大小、摘要、所属草稿修订及所需认证方式。不得返回真实凭据或假设能读取客户端本地路径。完成条件包含一个选定实际客户端及已说明的宿主/HTTP 辅助方式，真正上传字节、使用新修订绑定并携带凭据下载，再由 Designer 打开。仅 SDK 测试或返回 URL 不等于完成文件往返。

#### Scenario: Deliver an AI-created image project

- **WHEN** 选定客户端先上传 PNG 字节，再绑定资源、编辑工程、预检并请求导出
- **THEN** 客户端使用上传返回的新修订完成绑定，再实际携带凭据下载固定 ZIP，核对大小/摘要并在 Designer 继续编辑；HTTP 辅助步骤明确记录，不把普通 URL 点击或配套工具读取说成纯 MCP 自动传输

### Requirement: Version-bound protocol behavior

系统 SHALL 在阶段 A 固定并记录一个实测可用的 Python、官方 SDK、协商协议与实际外部客户端组合，按该版本处理传输和请求取消。首期不要求多版本矩阵，不得未经确认升级现有 Python 基线；实际客户端文件辅助方式也必须明确。

#### Scenario: Cancel a request with the selected protocol

- **WHEN** 选定客户端按实际协商协议发出取消信号，或发生该协议定义为取消的请求流断开
- **THEN** 服务按所选 SDK 结束该请求的未完成工作并正确释放资源，保留草稿及已提交内容；重连可读当前修订，完成记录包含版本组合而非笼统称兼容所有客户端

### Requirement: Machine-readable diagnostics for correction

系统 SHALL 在编辑及检查结果中保留已有诊断的 code、message、projectId、workflowId、nodeId、fieldPath，在已知时追加 resourceId，未知位置不得伪造。创建结果返回实际对象 ID，修订冲突返回当前修订。正常完成的 FAILED/INCOMPLETE 检查报告与非法编辑/服务处理错误须区分；不得只返回整段异常字符串或内部堆栈。

#### Scenario: Act on a located error

- **WHEN** 一次配置请求被拒绝或检查发现具体节点/资源缺项
- **THEN** AI 获得已知位置、稳定错误代码和对应修订，能够读取并修改正确对象；HTTP 和 MCP 保留相同业务诊断，不依赖解析自然语言消息猜节点
