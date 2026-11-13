# 工程资源与正式交付

## Purpose

为独立草稿提供真实资源字节的登记、替换及正式工程交付，使客户端生成的工程能够携带声明资源并继续由桌面编辑；明确缺项、资源完整性和快照归属，避免把未完成上传或缺失资源当成完整交付。

## ADDED Requirements

### Requirement: Complete uploads before registration

系统 SHALL 在资源字节接收完成并通过大小、摘要及草稿修订检查后，原子登记资源和必要引用。未完成或失败的上传不得形成有效资源；客户端文件名不得直接决定服务器文件路径。

#### Scenario: Register a PNG resource

- **WHEN** 客户端在当前修订下完整上传 PNG 并登记资源
- **THEN** 返回有效 resourceId、新 draftRevision、实际大小与 SHA-256；后续绑定使用该新修订，不能沿用上传前修订

#### Scenario: Incomplete upload

- **WHEN** 资源上传中途终止或完整性检查失败
- **THEN** 不登记有效资源和工程引用，保留原草稿内容，清理本次无效临时内容

### Requirement: Stable resource versions

系统 SHALL 在替换资源时写入新物理路径，可以保留逻辑 resourceId；仍被撤销历史或在途快照引用的旧文件保持可读。导入后补齐缺失资源使用同一资源操作，不引入独立的无修订恢复语义。

#### Scenario: Replace while an older snapshot is in use

- **WHEN** 新编辑替换资源，而已有导出或预检使用替换前快照
- **THEN** 新工程引用新文件，旧快照仍读取旧字节，撤销能恢复旧声明

### Requirement: Formal project import

系统 SHALL 支持正式工程文件和普通工程目录 ZIP 导入为新草稿。主工程必须可确定，解包不得逃逸目标目录或导入符号链接；实际提供的声明资源需校验，未提供与损坏必须分别处理。

#### Scenario: Import a project with missing resources

- **WHEN** 正式工程内容合法但部分声明资源未提供
- **THEN** 创建保留完整字段的新草稿并列出缺项；已经提供但摘要错误的资源不得被忽略并冒充缺项

### Requirement: Snapshot-bound editable exports

系统 SHALL 从指定修订导出单独工程文件或包含声明资源的完整 ZIP，并通过正式加载入口重读校验。导出不改变原草稿保存点和历史；单文件可说明缺项，完整 ZIP 必须资源齐全；固定下载结果不随后续编辑变化，响应包含所属草稿修订、文件名、类型、大小/摘要及带认证的下载方式，不在 URL 中嵌入凭据。

#### Scenario: Deliver a complete project to Designer

- **WHEN** 客户端导出资源齐全的图像工程 ZIP，解压后用 Designer 正式入口打开
- **THEN** 工程能继续编辑和保存，未声明的运行日志和历史输出不会被递归打包，导出响应说明对应草稿修订，并区分报告是否附带与工程检查结论

#### Scenario: Refuse an incomplete full package

- **WHEN** 客户端请求完整 ZIP，但尚有必要声明资源未提供
- **THEN** 拒绝将其作为完整交付并列出缺项；仍允许单独工程文件导出并明确其限制

### Requirement: Truthful exported report association

系统 SHALL 将导出动作是否检查、是否附带报告与工程是否已检查区分表达。没有附带报告时使用明确的未附带语义，不据此宣称该版本未检查。附带目标报告时须匹配 draftId、draftRevision、targetId、runtimeInstanceId，并保留报告原来的检查/编译/执行范围。没有报告不阻断合法导出，不要求强制再次预检或建立报告存储服务。

#### Scenario: Export a revision that was already checked

- **WHEN** 某修订刚完成目标预检，客户端导出同一修订
- **THEN** 导出要么附带准确匹配的原报告，要么明确本次没有附带报告/导出未运行检查；不得固定返回“该工程版本未预检”与已有结果相矛盾

#### Scenario: Do not reuse a stale passed report

- **WHEN** 预检后工程修订变化，或报告目标身份不匹配，再请求导出
- **THEN** 旧 PASSED 不得被关联为当前导出已通过的证据，固定文件与返回修订仍对应新快照
