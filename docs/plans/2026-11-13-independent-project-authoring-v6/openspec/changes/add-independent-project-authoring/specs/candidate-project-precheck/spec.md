# 独立候选工程预检

## Purpose

使用目标 Runtime 的真实声明与编译能力检查某一草稿快照，帮助 AI 定位配置错误和待补输入；检查不得发布候选、替换在线工程或驱动现场设备，报告必须准确区分预检、未执行的检查与实际运行。

## ADDED Requirements

### Requirement: Non-executing candidate isolation

系统 SHALL 检查指定草稿修订的独立候选及其上传资源，不提交在线配置、不创建候选运行任务、不连接设备。输入不得暗中读取 Runtime 主机未授权的其他文件，候选临时路径不得写回草稿。

#### Scenario: Check B while A is running

- **WHEN** 在线工程 A 正常运行，客户端提交候选草稿 B 进行检查
- **THEN** 检查只作用于 B 的独立副本，A 的工程和任务身份不被替换；B 没有执行任务或设备连接副作用

### Requirement: Genuine checks and truthful reports

系统 SHALL 基于真实工程、资源和目标声明执行可完成的检查，条件具备时进行真实编译。结果明确 scope=target，绑定草稿修订和目标实例；确定错误为 FAILED，必要输入不足为 INCOMPLETE，所需检查通过才为 PASSED，并说明 compileStatus 与 executionStatus。诊断保留已有 code/message/projectId/workflowId/nodeId/fieldPath 以及已知 resourceId，不能只压成错误文本。

#### Scenario: Compile a valid image candidate

- **WHEN** 图像候选资源齐全、配置合法且目标具备所需算子
- **THEN** 返回真实编译结果、被检修订和目标实例；即使 PASSED，executionStatus 仍为 not_run

#### Scenario: Report missing inputs without fabricating success

- **WHEN** 候选缺必要资源或使用未提供的外部输入路径
- **THEN** 完成可独立执行的检查并列出缺项，不创建假输入、不删节点后冒充完整编译；若没有确定错误则为 INCOMPLETE

#### Scenario: Separate transport errors

- **WHEN** 候选包上传不完整、字节损坏或目标不可达
- **THEN** 返回传输或服务错误，而不是正常的通过报告或把损坏字节解释为未提供资源

#### Scenario: Correct and recheck the same project

- **WHEN** 客户端收到同一图像工程的缺资源或明确配置问题诊断，读取当前草稿并按返回位置修复，随后使用更新后的修订再次预检
- **THEN** 新报告绑定新的 draftRevision 和真实目标实例，输入齐全且配置合法时执行真实编译；旧报告不会被冒充成新版本通过，修复使用正常编辑/上传入口而非特殊恢复服务

### Requirement: Work-owned cancellation and cleanup

系统 SHALL 在按所选协议收到请求取消或发生超时后不进入后续阶段，并在真实后台工作退出后释放其临时资源及执行额度。结果属于被检查快照；后续编辑不能沿用旧报告作为当前版本已通过的证明。

#### Scenario: Cancel an active precheck

- **WHEN** 候选检查收到明确取消，而后台检查仍需退出
- **THEN** 不启动后续检查或执行，不提前删除仍在使用的目录，最终退出并释放资源，不永久占用服务
