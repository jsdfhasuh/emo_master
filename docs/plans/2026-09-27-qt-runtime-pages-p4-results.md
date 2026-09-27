# P4 Designer 最小闭环执行记录

起始本地 HEAD：`dce0c66edaa2ea43e5992acf283162054661a6b7`，工作区干净，
工作分支 `agent/runtime-workflow-architecture-v1`。已核对完整第三批对象及其历史；
独立远端查询仍为 `2e45f3b70a5b317e39e855c38de3dd22f7eefe1d`。
正常补推遇到连接重置（curl 65 / unable to rewind rpc post data）；失败后再次 ls-remote 确认未同步。
未改 TLS、postBuffer、历史、main 或用户内容。原本地有完整代码，按授权继续开发。

## P4-A

显式开发开关 `EMO_PAGE_DESIGNER=1` 在现有 Designer 主工具栏增加流程设计/页面设计。
页面编辑需确认升级 2.2；未进入时仍保存 2.1。共用现有 WorkflowStore 和一个 ProjectEditSession，
页面事务与流程命令、拖动完成检查点进入同一有界历史；运行事件不进入历史。
保存/另存/加载/关闭由协调器接入原 ProjectController，失败不清 dirty；另存仅复制声明资源。
主窗口仅组装/分发；页面管理和协调器位于正式 `apps/designer/page_designer`。
渲染器新增受控 reload，保留 hub/session，旧图片/集合引用先释放；编辑状态禁用运行动作。

`python scripts/p4_validate.py --suite ui --output docs/evidence/p4/a-first --timeout 300`
实际通过页面/渲染/P1 专项、全 src/tests Ruff、src mypy；原始输出及受测 HEAD、dirty、
源码摘要、环境和退出码在 evidence.json。此批不宣称拖放与实时调试已实现，后续批次追加。

A 提交 `d727505`，96 passed / 0 skipped。批次推送再次连接重置，独立查询远端仍为 `2e45f3b`。
仓库忽略 *.log 与新计划文件；本报告和原始日志在 B 批显式纳入版本控制，未删改原输出。

## P4-B

正式静态输出目录从当前项目及 Runtime 返回的可信 manifest 元数据生成，未运行可绑定。
重复调用路径分别列出；现有单作用域限制明确拒绝。组件/输出使用实际 Qt MIME 拖放，
属性面板与拖放调用同一 schema 命令；网格重叠/容器循环拒绝，副本重绑隔离。
属性包括网格位置/跨度、文字/单位/小数、表格列/分页、判定映射及稳定 ID 导航。
编辑态只选择，预览态才导航；数据/页面选择不创建 Job 或订阅。

`b-first`：98 passed / 1 failed，Ruff 2 errors。拖放用例在布局尚未完成时使用根网格坐标，
暴露 drop 命中不稳定；修复为布局激活、命中已有控件时采用实际模型单元格，
测试直接向该控件发送 Qt DropEvent 验证重叠拒绝。保留原失败。
`b-fixed`：99 passed / 0 skipped，Ruff/mypy PASS。命令为同一 p4_validate --suite ui，
测试含实际拖组件/拖端口、类型拒绝、两个 count 实例、副本隔离、缺节点定位和容器移动回滚。
