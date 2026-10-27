# 2026-10-25 Runtime 工作区整理与交付验证

## 范围与源码

本轮按“整理工作区、提交、推送代码”的要求交付现有公开修改，并修正整体验证中发现的类型检查和测试夹具问题。不扩展为新的功能开发、现场工程迁移、打包或部署。

- 分支：`agent/runtime-workflow-architecture-v1`。
- 起始提交：`9265547dbe61bc57542d3cf0ea143fcf455600fb`。
- 代码验证对象：以下三个提交的合并结果，共 130 个文件。测试不是逐提交独立执行。

| 提交 | 内容 |
| --- | --- |
| `d387fcf2893443a38eaeae159445056227293899` | 托管 RTSP 驱动、设备声明、显式迁移、Designer 设备入口及测试文档 |
| `64c334fc45654b8ea11d66c75f7bb2c9576edfdf` | Dispatch 队列监控与背压、生产者/消费者共享结果页、容量和资源诊断、预览传输、Designer 日志及回归测试 |
| `b9f3a7bfa640c6794e108f2deceae9a9a8baf17a` | Designer/独立 Runtime 启动反馈、Runtime 设置与日志、开发启动器及测试文档 |

本地忽略的现场工程、模型、凭据、私有测试和构建产物不纳入提交，也未清理或回退。提交前按明确文件清单暂存并检查；敏感模式检查只命中合成脱敏测试 URL，未发现实际凭据或私有设备地址。

## 环境与最终结果

环境：Windows，Python 3.10.21，PySide2 5.15.2.1；`PYTHONPATH=src;.`。常规回归使用 `QT_QPA_PLATFORM=offscreen`；原生 Qt 回归使用 Windows 平台，分别设置 100% 和 150% 缩放。

| 检查 | 结果 | 证据范围 |
| --- | --- | --- |
| Ruff | 通过 | `src tests scripts/dev.py examples/dispatch_queue_demo.py` |
| mypy | 通过，548 个源码文件 | `mypy --config-file mypy.ini src` |
| Protobuf 生成一致性 | 通过 | `scripts/gen_proto.py --check` |
| 最终变更集回归 | 504 passed，4 skipped，298.32 秒 | 42 个公开测试文件，`final-tests.*` |
| 相邻回归 | 288 passed，351.77 秒 | 27 个公开测试文件，`related-tests.*` |
| 实时页夹具修正后单文件复验 | 15 passed | `live-refresh-final.*` |
| 原生 Windows Qt，100% 缩放 | 59 passed，42.11 秒 | `native-100.*` |
| 原生 Windows Qt，150% 缩放 | 59 passed，50.83 秒 | `native-150.*` |
| 算子目录检查 | 78 个算子、55 个入口模块、9 个托管算子、7 个设备类型 | 无拒绝注册项、无资源问题；`operator-catalog.json` |
| 暂存检查 | 通过 | 每组清单一致，`git diff --cached --check` 无错误 |

不同回归集合存在重叠，不相加为去重后的测试总数。最终 offscreen 的 4 项跳过均为 `test_run_failure_feedback.py` 中需要 Windows QMessageBox 的测试；它们在两轮原生 Qt 回归中均实际执行通过。

新 Dispatch 示例沿用 `WorkflowStore().toPayload()` 创建 schema 2.7 工程，并走 `ProjectDocument`、保存与重读路径；对应测试覆盖编译和 Designer 打开入口。本轮不批量改写历史工程。

## 首轮失败与处理

原始失败日志保留在本地 `build/workspace-submit-20261025/`，后续通过结果不覆盖首轮证据。该目录受忽略规则管理，不随公开代码上传。

1. Ruff 首轮 1 项未使用导入：移除 `test_managed_rtsp_ui.py` 中的 `json`；复验通过，见 `ruff-initial.txt`、`ruff-delivery.txt`。
2. mypy 首轮在 3 个本轮修改文件中报告 9 项错误：日志读取器显式区分空游标分支，日志 UI 补充集合类型并沿用已有 Qt Signal 注解方式，Designer Runtime 事件复制前收窄为字典。未改写正常业务流程；见 `mypy-initial.txt`、`mypy-final.txt`。
3. 首轮兼容抽测为 1 failed、8 passed、4 errors。公开测试原先统计所有 `QDialog`，新增日志对话框后误报；改为断言恰好一个 `DeviceWorkspace`，保留对象复用和工作区不变断言。4 个 errors 来自原本已被本地忽略的 `test_migration_operator_pages.py`，其私有工程设备槽声明不匹配。本轮未修改这些工程或测试，也不将它们记为通过；见 `integration-first.*`。
4. 启动取消反例首轮 1 failed：终态载荷的旧断言未包含已有 `deviceOutcome` 与 `previewSnapshots`。补全确切预期，保留 FAILED、原始错误和 UNKNOWN outcome 断言，未弱化为子集比较；完整文件复验通过，见 `cancellation-first.*`、`related-tests.*`、`final-tests.*`。
5. 首轮 41 文件变更集回归为 492 passed、1 failed、4 skipped。跨行画面布局测试在 offscreen 默认屏幕下被限制为 800x600，且测量等待占位内容而非实时数值。夹具固定屏幕可用区域，先送入实时数据，并新增实际窗口尺寸等于请求尺寸的断言；原有画面占比、数值行占比和无横向滚动断言保留。本轮未因此修改产品渲染代码。反例探针禁用行拉伸后仍失败，确认回归保护有效；见 `changed-tests.*`、`span-layout-*`、`live-refresh-final.*`。

## 复验命令

在仓库根目录、已安装开发依赖的 Python 3.10 环境中运行。变更集测试清单由上述固定提交范围生成，与本轮 42 文件清单一致：

```powershell
$env:PYTHONPATH = 'src;.'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QT_SCALE_FACTOR = '1'
python -m ruff check src tests scripts/dev.py examples/dispatch_queue_demo.py
python -m mypy --config-file mypy.ini src
python scripts/gen_proto.py --check
$changedTests = @(git diff --name-only 9265547dbe61bc57542d3cf0ea143fcf455600fb..b9f3a7bfa640c6794e108f2deceae9a9a8baf17a -- tests |
    Where-Object { $_ -match '(^|/)test_[^/]+\.py$' })
python -m pytest -q -rs --tb=short --timeout=120 --timeout-method=thread @changedTests
```

相邻回归的明确清单：

```powershell
$relatedTests = @(
    'tests/runtime/test_startup_cancellation.py',
    'tests/runtime/test_frame_dispatch.py',
    'tests/runtime/test_device_manager.py',
    'tests/runtime/test_device_lifecycle.py',
    'tests/runtime/test_device_connection_recovery.py',
    'tests/runtime/test_device_runtime.py',
    'tests/runtime/test_device_ipc_retirement.py',
    'tests/runtime/test_parallel_workflows.py',
    'tests/runtime/test_production_boundaries.py',
    'tests/runtime/test_production_continuous.py',
    'tests/runtime/test_production_project_configuration.py',
    'tests/runtime/test_jsonl_logging.py',
    'tests/runtime/presentation/test_normal_capture.py',
    'tests/runtime/presentation/test_normal_capture_status_validation.py',
    'tests/designer/test_runtime_controller.py',
    'tests/designer/test_online_run.py',
    'tests/designer/test_designer_bootstrap.py',
    'tests/designer/test_startup_directory_lock.py',
    'tests/designer/test_startup_project_entry.py',
    'tests/designer/test_startup_load_feedback.py',
    'tests/designer/test_device_catalog.py',
    'tests/designer/test_device_identity_reuse.py',
    'tests/core/plugin/test_device_registry.py',
    'tests/core/plugin/test_operator_development_examples.py',
    'tests/plugins/test_rtsp_camera_operator.py',
    'tests/ui/presentation/test_session_views.py',
    'tests/ui/presentation/test_capture_coverage.py'
)
python -m pytest -q -rs --tb=short --timeout=120 --timeout-method=thread @relatedTests
```

原生 Qt 回归在可用 Windows 桌面会话中运行：

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$nativeTests = @(
    'tests/designer/test_run_failure_feedback.py',
    'tests/designer/test_managed_rtsp_ui.py',
    'tests/ui/presentation/test_live_refresh.py',
    'tests/ui/page_designer/test_dispatch_live_page.py',
    'tests/ui/page_designer/test_dispatch_queue_viewing.py'
)
$env:QT_SCALE_FACTOR = '1'
python -m pytest -q -rs --tb=short --timeout=120 --timeout-method=thread @nativeTests
$env:QT_SCALE_FACTOR = '1.5'
python -m pytest -q -rs --tb=short --timeout=120 --timeout-method=thread @nativeTests
```

## 验证边界

- 本轮执行的是定向源码测试和原生 Qt 自动化，不是全仓库 pytest，也不是人工完整业务走查。
- 本轮未访问真实相机或 PLC，未改设备地址、现场工程或凭据。既有专题报告中的历史设备证据保留原样，不视作本轮重新验收。
- 本轮未构建或部署 EXE，未替换正在运行的应用。源码修改需应用重启才能生效，已打包程序需重新构建。
- 此记录反映本地验证；推送后的 CI 状态应按相应固定提交的实际运行结果确认，不能从本地通过推断远端 CI 已通过。
- 私有工程测试的 4 个 setup errors 本轮仍未处理；不属于公开交付测试的通过范围，也不将其静默改为跳过或成功。
