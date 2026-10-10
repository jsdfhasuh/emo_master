# 工作区与变更导航

## 目录约定

| 目录 | 内容 | 提交策略 |
| --- | --- | --- |
| `src/emo_master/apps/designer` | Qt 界面、Controller、主题和内部布局辅助 | 跟踪 |
| `src/emo_master/apps/runtime` | 作业执行、事件与预览服务 | 跟踪 |
| `src/emo_master/plugins/builtins` | 内置算子、Controller 与 `.ui` 资源 | 跟踪 |
| `tests` / `scripts` | 回归测试及开发、验证脚本 | 跟踪 |
| `docs/testing` | 已核验的验收记录和精选运行证据归档 | 跟踪 |
| `field_tests/global_counter` | 现有仓库中的全局计数器示例 | 保持原有跟踪 |
| `field_tests/camera_ui` | 本机相机项目，含现场参数 | 原地保留，忽略 |
| `manual_test_workspace` | 隔离运行产物、截图缓存、调试归档 | 忽略 |
| `.pytest_cache` / `.mypy_cache` / `.ruff_cache` | 可重建工具缓存 | 忽略 |

不得为清理工作区覆盖现场 `project.json`、删除原始验收数据或提交运行时数据库。新增现场目录应先区分可共享示例与机器专用配置，不能因为未跟踪就直接上传。

## 2026-09-10 整理内容

### 相机与实时预览

- IMV 的所有设备选择方式在创建句柄之前完成设备枚举，枚举失败不创建句柄，后续可重试。
- 实时预览失败保留有界错误摘要，释放资源后订阅者仍能取得原始错误。
- 预览错误写入 Runtime 事件与 JSONL，并通过 gRPC `FAILED_PRECONDITION` 和相机编辑器显示原始失败原因。
- 相机参数采用中文分组、枚举显示与条件启用；底层字段和值保持兼容，禁用字段不丢失配置。
- 新增模拟相机、预览服务、远程 gRPC 错误及参数表单回归测试。未执行真实硬件验收。

### Designer 全窗口现代化

- 统一浅色工作台、字体、离线图标、工具栏动作和窗口尺寸约束。
- 修复折叠栏反弹、标签裁字、节点端口越界、首次定位与手动缩放保持。
- 改造参数、日志、计数器、流程包和三种内置算子工作区。
- 新增 20 项真实 Qt 几何与像素测试，以及独立 Windows Qt 截图检查脚本。
- 全仓库测试默认 Runtime 目录改为逐测试临时目录，避免与正在运行的 Designer 争用真实数据库和作业目录锁。
- 详细范围、测试计数、DPI 结果和边界见 [Designer 验证记录](designer-ui-validation.md)。

### 文档与证据

- [当前 UI 规范](qt-widgets-qss-guidelines.md) 已取代旧橙色主题约定。
- [前后对比归档](testing/designer-ui-2026-09-10/README.md) 包含 93 张截图，文件复制经过 SHA-256 一致性检查。
- 原始项目与调试产物继续留在本机，不上传相机现场项目、缓存或数据库。

本次整理不发布版本、不创建 tag、不构建安装器；代码版本保持 `0.6.1`，README 仅同步原有实际版本。

## 提交前检查

```powershell
python scripts/ci_check.py
python -m ruff check scripts/designer_visual_check.py
git diff --check
git status --short
```

测试使用模拟 Runtime 和隔离 Qt 设置；不应启用 `HUARAY_CAMERA_SMOKE`。常规开发与 CI 不依赖本机相机项目。
