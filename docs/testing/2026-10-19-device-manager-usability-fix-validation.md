# 设备管理可用性修复与验证记录

日期：2026-10-19 UTC。基线：`8a65f4ce0fc9c1e95104250351730ba8a632b427`。
本记录随修复提交；不是全产品或真实硬件验收通过声明。

## 修复范围

- 参数表单的数字和闭合下拉框不再因滚轮静默改值；保留键盘、显式选择与表单滚动。
- 最后一个资源 CHUNK 返回后、COMMIT 前重新检查取消；已提交结果和未知结果仍按实际状态处理，不自动重放。
- 设备管理正文可滚动、底部操作区固定；中文动作标签、最小 32px 逻辑按钮高度及常驻草稿说明改善可发现性。
- 重复设备在关闭创建表单前明确确认复用；拒绝时保留输入，复用不覆盖既有配置。名称、字段和策略采用就地中文提示并显示错误所在页。
- 活动上传可直接请求取消并留在比较框；原传输查询、取消、重试和显式写入 Runtime 的边界保持。
- 成功后台状态刷新清理自身过期错误，但不覆盖后来的用户校验反馈或未应用输入。
- Job 取消信号改为单向共享标志，避免被终止进程遗留的 Event 条件锁卡住设备 IPC 退场；所有权、未知副作用、退场期限和不重放保护不变。

没有大重构、生产设备访问或部署。初始六文件修复及所有首次失败均保留在验收归档。

## 固定候选身份

16 个产品/测试文件的最终 SHA-256 清单：
[`2026-10-19-device-manager-usability-fix-validation.sha256`](2026-10-19-device-manager-usability-fix-validation.sha256)。
清单自身 SHA-256：`fd732a02893a0abc3da1b48d7823d37f8788c0bce8ecef3140e0a3207ad9b0e2`。
从仓库根目录可运行 `sha256sum -c docs/testing/2026-10-19-device-manager-usability-fix-validation.sha256`。
清单只标识已测试的产品/测试文件，不包含本记录及清单本身。

## 验证结果与版本边界

环境：Linux 云端真实桌面 `DISPLAY=:0`，1364×1024；Python 3.10.22、PySide2 5.15.2.1、Qt 5.15.2。
Designer 和嵌入式 Runtime 使用同一候选源码，隔离工程和数据目录；TCP 未启用。

| 证据层次 | 实际结果 | 边界 |
| --- | --- | --- |
| 全仓首次检查 | proto 漂移、Ruff `src tests`、mypy 496 文件通过；pytest **6244 passed / 23 failed / 11 errors / 23 skipped / 42 subtests passed**，1456.60s | 对应最后两项 UI 小修之前的候选；不是最终候选全量通过 |
| 最终两项 UI 小修 | 过期状态消息清理、不可解析数值错误页定位；相关 **39 passed**（执行者 9.98s、独立复审 9.72s）；Ruff / mypy 再通过 | 定向验证；与此前 37 项重叠，不累计或并入 6244 |
| IPC 确定性验证 | 真实 spawn、生产 shutdown 终止持锁 worker，旧 Event 对照复现双层退场错误；新信号同一用例通过；独立新增 7 项及相邻 60 项通过 | 原历史 CI 未保留阻塞线程栈，精确历史因果仍为强推断；已证实机制及相同失败链 |
| 上传取消 Qt 验证 | 活动取消、最后 CHUNK、已 COMMIT、丢失 COMMIT 回复及查询/取消互斥；新增 7 项独立复审通过 | 受控传输注入，不冒充人工点击 |
| 真实 Qt 人工复查 | 新增/取消/修改/校验/重复恢复、共享设备及节点覆写、引用跳转、草稿应用/放弃、过滤清选、在线预检/取消/显式本地写入、资源导入/上传取消/重试且不发布、设备导出/禁用副本导入/删除保护、保存重开和退出通过 | 模拟设备及合成资源；人工上传取消实际发生在 CHECK 阶段 |
| 实际缩放 | 100% 和 200%；200% 下保存、创建表单取消、保留草稿关闭按钮实际可点击 | 仅本次屏幕，不能推广到所有 DPI/操作系统 |

全量失败对照：与之前六文件候选相比，没有新增失败 node ID 或异常签名，唯一消失的是设备 IPC workers still exist。
剩余 15 个失败及 4 个 teardown errors 已在同环境固定基线复现；8 个失败及 7 个 setup errors 被 Windows-only IMV 的 Linux 平台门禁阻断。
TCP `EADDRINUSE` 仍记为失败，不把它笼统归为无害环境噪声。完整 CI 仍为红灯。

最终定向命令（仓库根目录，既有 Python 3.10 环境）：

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=src python -m pytest -q \
  -W error::pytest.PytestUnhandledThreadExceptionWarning \
  tests/designer/test_device_workspace_usability.py \
  tests/designer/test_device_workspace_sizing.py \
  tests/designer/test_device_workspace.py \
  tests/designer/test_device_definition_feedback.py \
  tests/designer/test_device_identity_reuse.py
python -m ruff check src tests
python -m mypy --config-file mypy.ini src
git diff --check
```

## 未测与保留限制

- **NOT_RUN**：真实 IMV 相机、PLC、SDK、实物读参/试采/生产监视及真实连接/断开控制。模拟类型禁用的能力未被修改来“测通”。
- 分类/搜索清选通过，不等于完整旧“隐藏相机仍断开”的硬件动作通过。
- 共享取消标志及其约 20ms 轮询在本 Linux 环境验证；不宣称通用硬实时保证或所有平台验证完成。
- 已接受的 F01 人工篡改多故障组合继续暂缓，不设为普通用户验收前提。
- 独立复审无阻断项。最后进程于 17:04:40 UTC 正常退出，未留下本轮 Designer / Runtime / multiprocessing 进程或共享内存。

## 归档索引（大文件不入库）

完整截图、UI 工程、合成资源、首次失败、修复后日志、独立审查与清理记录另行归档。本提交不包含大 ZIP、凭据或运行数据。

| 归档对象 | SHA-256 |
| --- | --- |
| 全量候选文件清单（最后两项小修前） | `559ddab416556579069a0ee0af22d275b7eeb394a31065cb8df03ec8829d657b` |
| 全量首次日志 | `77caca66e902580e1e82d86c700bf03002df4256037b72f288d6f6569b2c12c4` |
| 最终 39 项执行者日志 | `6a843607558f864177b9378f0102544a4c88cbad9fe491e2dba73cd667616f94` |
| 最终 39 项独立日志 | `d77b30dd5669c96bbc57630e59a2d6c911a91f766c623fec277d35c16e9fcefc` |
| 7 页最终报告 PDF | `882b106f90d4bbc55b4a995b4c9929f4f6a5a31505a07e7d9445f8ec8cbc1dcd` |
| 最终证据 ZIP | `deeb6101508386e9ff0dc2b957bf554f9fc74ddff2960fce3068b0856b25e30d` |
