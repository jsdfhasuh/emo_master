# R3 收尾源码地图

**审查基线：** `1f35861a22f4e5bd553fe210db989e28c3c4a0d1`

  --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  路径                                                       已确认内容                                                                 本补充动作
  ---------------------------------------------------------- -------------------------------------------------------------------------- ------------------------------------------------
  `src/emo_master/apps/operator_runtime/log_sources.py`      `RuntimeLogSources` 来源顺序 Job/System/JSONL；构造时当前索引              默认索引 0；离开 Job
                                                             2；`setToolVisible` 仅更新标志；`_ensureReader` 对隐藏/非 Job 来源直接     来源/隐藏时请求停止、drain、保存
                                                             return；`_poll` drain 当前 reader                                          cursor，结束后再恢复

  `src/emo_master/apps/operator_runtime/log_view.py`         `resetFilter()` 当前切索引 2；`setDevice()` 也切索引 2                     reset 回 Job；setDevice 保持 JSONL

  `src/emo_master/clients/runtime/global_logs.py`            `RuntimeGlobalLogs` 已有 cursor、stop、context、stream                     复用现有取消/完成机制；连接事件改中立名称
                                                             cancel、finished、close；`_notice` 事件类型是
                                                             `designer.runtime_logs.connection`

  `src/emo_master/apps/operator_runtime/theme.py`            复用 `LIGHT_THEME`；只在 `Microsoft YaHei UI` 存在时设置字体；现有 QSS     增加已安装字体回退和必要控件样式
                                                             未完整覆盖 checkbox/radio/scrollbar

  `src/emo_master/apps/operator_runtime/startup_page.py`     StartupPage 已有双列 QBoxLayout 和 920px                                   仅修真实截图中可见的字体/控件/间距，不重建布局
                                                             断点；QGroupBox、日志区域、路径区已存在

  `src/emo_master/ui/runtime_logs/runtime_job_logs.py`       共享 Job 工作台；支持 `allowAdvancedHistory`，但 Operator 传               本补充不打开高级历史；保留此范围差异
                                                             False；`historyRequested` 需宿主接线

  `src/emo_master/apps/operator_runtime/log_client.py`       Operator 已有薄日志客户端：Runtime                                         复用，不扩展完整 RuntimeClient
                                                             info、ListRuns、ListJobHistory、QueryRuntimeLogs、设备诊断、算子注册信息

  `tests/ui/operator_view/test_runtime_job_log_sources.py`   已有 Operator 三来源、系统事件分类、Job 导出和关闭时取消系统查询的测试     扩展默认来源和 reader 暂停/恢复断言

  `tests/ui/operator_view/native_runtime_r3_probe.py`        R3 Windows 原生 Qt + 软件 Runtime 代表流程                                 只调整/复用必要步骤，新增来源默认和 reader
                                                                                                                                        可见性检查

  `src/emo_master/apps/designer/ui/log_dialog.py`            Designer 宿主接线 `historyRequested` 并有独立历史结果视图                  不修改 Designer 该 UI；高级历史留在本 Change
                                                                                                                                        之外
  --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

## R3 原生截图参考

-   [起始/设置页
    1060×800](https://github.com/jsdfhasuh/emo_master/blob/1f35861a22f4e5bd553fe210db989e28c3c4a0d1/openspec/changes/archive/2026-11-27-runtime-platform-overall-v1/verification/native-qt/01-startup-settings.png)
-   [Job 工作台
    1060×800](https://github.com/jsdfhasuh/emo_master/blob/1f35861a22f4e5bd553fe210db989e28c3c4a0d1/openspec/changes/archive/2026-11-27-runtime-platform-overall-v1/verification/native-qt/04-job-log-workbench.png)
-   [设备管理
    920×620](https://github.com/jsdfhasuh/emo_master/blob/1f35861a22f4e5bd553fe210db989e28c3c4a0d1/openspec/changes/archive/2026-11-27-runtime-platform-overall-v1/verification/native-qt/05-device-controls.png)
