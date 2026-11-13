# 独立完整工程草稿

## Purpose

为网页及外部 AI 提供不依赖桌面进程的完整工程工作副本，使工程可以逐步编辑、保存、恢复并交回桌面继续使用；本能力约束工程内容、事务与状态语义，不要求两端共享内存或相同的界面调用流程。

## ADDED Requirements

### Requirement: Independent project sessions

系统 SHALL 在不启动、连接或控制 Designer 的情况下创建和编辑独立草稿；不同 draftId 之间以及草稿与桌面当前工程之间不得共享可变编辑状态或撤销历史。

#### Scenario: Edit without a desktop process

- **WHEN** Designer 未运行，客户端创建两个草稿并只修改其中一个
- **THEN** 修改仅影响指定草稿，另一个草稿不变，服务无需桌面进程参与

### Requirement: Complete official project format

系统 SHALL 使用正式工程结构与保存入口，保留流程、页面、资源、设备、变量及运行和生产配置。导入保留 projectId，生成独立 draftId；空白合法工程可以保存，不为通过业务编译伪造节点。

#### Scenario: Save and reopen a complete project

- **WHEN** 客户端保存一个包含页面、设备、变量和资源声明的合法草稿并重新读取
- **THEN** 正式字段完整保留，内容可由正式加载入口读取，读取操作不改变保存修订或时间，返回的嵌套对象与会话隔离

#### Scenario: Read detached page state without full project rebuild

- **WHEN** 客户端只读取页面状态，并在另一次读取前执行页面修改、撤销或重做
- **THEN** 页面读取复用只校验页面部分的快照路径，不重复序列化全部流程；返回内容不与 Store 共享可变对象，也不残留旧缓存；完整事务和保存校验保持不变

### Requirement: Atomic semantic editing

系统 SHALL 将一项语义编辑视为一次事务：成功修改形成一次撤销记录，失败恢复原内容且不更新草稿修订，无实际变化不增加历史。撤销和重做不得擅自回退最后成功保存的文件元数据。

#### Scenario: Apply and undo an edit

- **WHEN** 客户端成功修改节点配置，随后执行一次撤销和一次重做
- **THEN** 工程内容按该项操作恢复和重现，没有重复撤销记录

#### Scenario: Reject an invalid edit

- **WHEN** 节点修改违反适用的参数、变量绑定或连线规则
- **THEN** 返回可定位错误，原工程和草稿修订保持不变，不留下部分修改

### Requirement: Complete semantic operation coverage

系统 SHALL 提供流程和普通节点编辑、连接与位置、复杂控制流程、设备与变量、页面与控件、资源绑定以及正式运行/生产配置的编辑能力，沿用既有规则而非另行解释。参数完整替换，嵌套对象与数组保持真实类型和结构；省略变量绑定保留原值，显式空列表清空绑定。

#### Scenario: Configure an operator with variable bindings

- **WHEN** 客户端提交节点完整参数和显式变量绑定列表
- **THEN** 两者原子应用；动态端口、资源占用与引用检查遵循既有规则；未提供绑定列表则保留原绑定

#### Scenario: Configure advanced project content

- **WHEN** 客户端创建或修改 Subflow、Loop、Dispatch/Join、设备、变量或页面配置
- **THEN** 对应正式字段及引用关系正确更新，控制流程合同和端口诊断遵循现有编辑语义，而不是直接写入未经业务检查的任意 JSON

#### Scenario: Configure compound regions without a graphical editor

- **WHEN** 客户端依据支持组合区域的目标声明，经通用配置命令提交含保留与排除区域的 ROI 参数，并执行保存重读和撤销重做
- **THEN** 区域用途、启用标记、几何字段和点集保持正确，一次配置只形成一次事务；过程不依赖 ROI 的 Qt 编辑器，声明中的 UI 预览能力不被冒充为 MCP 执行能力

#### Scenario: Copy a configured resource-bound node

- **WHEN** 客户端通过公开 `node.copy` 复制已有图片/模型资源绑定或现场参数绑定的普通节点
- **THEN** 新节点与指向它的资源计划绑定在同一事务生成，绑定目标使用返回的新 nodeId，资源字节继续引用原 resourceId，原绑定不变；一次撤销/重做同时恢复节点和绑定，不把空路径的新节点当成完整复制成功

### Requirement: Revision and saved-state boundaries

系统 SHALL 对每草稿写操作检查 expectedDraftRevision 并串行修改。连接断开不删除草稿；服务重启恢复最后成功保存内容并生成新会话修订，不承诺恢复未保存修改及全部撤销历史。真实修改工作结束前不得释放其互斥边界。

#### Scenario: Reject an outdated edit

- **WHEN** 第一项修改已更新修订，第二项修改仍使用旧修订
- **THEN** 第二项被拒绝并返回当前修订，不覆盖第一项修改

#### Scenario: Recover saved content

- **WHEN** 服务正常重启，客户端重新读取先前保存的草稿
- **THEN** 返回最后保存内容和新的会话修订，旧客户端不能用重启前修订直接覆盖；保存/导入后的声明恢复沿用能力发现规范中的正常采用路径，不以创建并删除临时节点替代

### Requirement: Draft-local page bindings

系统 SHALL 从当前草稿和相应声明生成页面可绑定节点输出；不得使用目标 Runtime 当前在线工程的节点目录代替草稿，也不得要求额外维护一份对外输出别名。

#### Scenario: Bind a newly created output

- **WHEN** 客户端在尚未发布的草稿新增 Threshold 节点并查询页面输出选项
- **THEN** 能选择该草稿节点的 mask 输出并完成页面绑定，无需 Runtime 先加载该工程

### Requirement: Local validation without online runtime

系统 SHALL 提供不强制连接在线 Runtime 的 validate_project_draft，在指定草稿修订上使用正式模型和本地可取得的对应声明，检查结构、引用、页面/设备/变量绑定及已知资源。缺必要声明或输入须报告 INCOMPLETE，确定错误优先为 FAILED；本地通过仅表示本地范围通过。结果明确 scope=local、compileStatus=not_run、executionStatus=not_run，不能被描述为目标编译或执行成功。结构合法而尚未配置完整的草稿仍可保存。

#### Scenario: Validate an incomplete draft while offline

- **WHEN** 目标 Runtime 离线，客户端对结构合法但缺模型资源或必要声明的草稿调用本地校验并保存
- **THEN** 服务完成可用的本地检查，返回相应修订、可定位缺项和 INCOMPLETE，不要求先恢复在线连接；正式保存仍可完成，未伪造目标检查通过

#### Scenario: Distinguish local success from target readiness

- **WHEN** 当前草稿在已有声明下完成全部本地校验且没有错误或缺项
- **THEN** 结果标记 scope=local 与本地 PASSED，compileStatus/executionStatus 仍为 not_run，不生成目标 Runtime 已通过或已运行的结论
