# 第二轮 Web 后端：跨 Runtime 发布协调

2026-11-04。源码 HEAD 为 `d92ffd37960e8b8f527136151ab9ccb80054e78f`，分支 `codex/logging-consolidation-20261103`，加已有未提交实现及本轮局部修改。开始前重读 AGENTS.md、当前实施计划及发布/失败恢复章节，保留旧工作区和第一轮证据。没有提交、推送、合并、部署、修改打包脚本或产物，也没有访问现场 Runtime 地址、相机或 PLC。本轮执行范围是轻量 Web 后端及相关测试。

## 根因及修前证明

第一轮为保护“发布等待旧客户端确认时管理窗口改址”的场景，让编辑器发布复用应用全局 `target_lock`。该锁同时被所有目标的 Start、Stop、视图和管理操作取得，因而 A 的一次正常发布 RPC 等待会卡住无关 B。EditorStore 内部的全局 `publish_lock` 是第二层串行：只改路由层仍会阻塞 B 发布。

产品修改前，原样保存用户提供的 Temp 反例为 `cross-target-lock.py`、`cross-target-lock-supplied.json`，执行副本得到 `cross-target-lock.json` / `cross-target-stop-before.log`。正常完成和取消发布两分支均记录：A 发布等待时，B Stop 未完成，B 仍 running；释放 A 后 B 才 stopped。

再建立 `test_editor_target_isolation.py` 的直接 HTTP 回归，修改产品前执行跨目标三例，`backend-isolation-before.log` 为 **3 failed**：A submit 被明确暂停时，B Stop、B Start、B 发布均未完成。`backend-before/` 保存结构化实际状态；它们不是人为制造多故障组合，而是正常独立目标并行请求的直接反例。所有目标都是明确的 MockRuntime，不发真实网络请求。

## 局部修复与锁顺序

- `app.py`：目标协调改为 `target_lock(runtimeId)`。同目标发布/查询/继续/回退/结束、改址、删除、控制和创建视图继续协调；不同 Runtime 不共享长 RPC 等待。删除后保留该 ID 的锁对象，避免已排队请求与同 ID 后续记录使用不同锁。
- `editor_routes.py`：所有已有发布协调入口使用同一目标的锁，仍从取得目标到本次确认/启用结束保持关系一致。请求身份、工程/配置修订校验、未知结果不重放和发布状态逻辑没有修改。
- `editor.py`：发布、reconcile、abandon 使用按 `runtimeId` 的内部发布锁。同一 Runtime 即使切换工程也共用锁；取得锁后仍重新读取权威版本记录。`complete` 仍复用 reconcile，内部 `_complete` / `_reconcile` 不重入公开锁。
- 锁顺序为 **目标锁 → 该目标发布锁**。不同层是不同锁对象，内部没有反向取得目标锁。
- `grant_target` 先取得该目标锁，再取得短 `user_lock`；`delete_user` 只持短 `user_lock` 执行本地用户/ACL修改。共享用户锁不等待目标发布，不包 Runtime RPC。入口身份/角色、CSRF、ACL及用户是否存在的检查保留。
- 旧包对多个明确目标启用时，按排序后的唯一 Runtime ID 取得各目标锁，全部目标校验后仍一次调用 SQLite `activate_many`。没有扩大为全局发布锁，也没有改成部分目标先成功。
- 原全局 `client_lock` 保留，只用于缓存中的客户端构造及本地目录读取。当前 `GrpcRuntimeClient.start()` 仅 SDK导入、构造 channel/stub，不等待连接成功或执行网络 RPC；MockRuntime.start 为即时返回。A 的发布网络等待不持此锁。若以后 start 增加实际握手，应重新评估，不能把当前结论视为任意未来实现保证。
- `Operations.lock` 是原有请求意图关联锁，本轮未修改。它在显式 Start/Stop 执行路径中，A 发布从不取得它，故不会造成此次跨目标发布反例。本轮不顺带扩为“任何控制 RPC 等待都必须跨目标并发”的新任务；请求 ID 的原子预留和未知操作不重放保持原语义。
- 同步包存储锁及 SQLite事务仍只包本地短操作，不覆盖 A 的待确认 RPC。

async 上下文在返回、异常或取消时正常释放两个目标范围锁，不新增无限重试、后台补执行、全局状态账本或强制停止任务。

## 用户五项检查与实际结果

| 要求 | 实际检查 |
| --- | --- |
| 1 A 发布等待时 B Stop 独立完成 | `test_other_runtime_stop_completes_while_publication_waits`；A仍 pending 时 B HTTP完成且状态 stopped。原用户脚本修后正常/取消两分支也均完成。 |
| 2 B Start 不受 A 影响 | `test_other_runtime_start_completes_while_publication_waits`；先正常停止 B，再于 A pending 时明确启动，B运行且 A仍 pending。 |
| 3 同目标改址及删除保护 | `test_same_target_address_change_and_delete_wait_for_publication` 两子例，以及原 `test_publish_and_target_address_edit_share_existing_target_coordination`；变更在 A等待期间未生效。发布完成后管理请求正常执行，删除后没有遗留激活映射。 |
| 4 取消/失败/查询/继续无残留占锁 | `test_cancelled_or_failed_publication_can_query_continue_end_and_publish_again` 两子例；HTTP取消或一次提交失败后可查询、complete、abandon，未知请求提交次数保持1；只有新的明确发布才增加到2并成功，随后管理操作可正常执行。 |
| 5 重复complete与后续发布正确 | 原 `test_duplicate_complete_followed_by_new_publish_keeps_latest_page_and_capture_together` 正确断言保留并通过：迟到旧完成不覆盖B的新页面/采集配置。 |

额外直接验证第二层锁隔离：`test_other_runtime_publication_completes_while_publication_waits` 中 B发布先完成，A仍 pending。`test_queued_grant_on_publishing_target_does_not_hold_shared_user_guard` 中 A授权排队，B授权及Stop先完成，A授权在发布结束后正常完成。发布所在 Runtime 的隐式控制次数始终0。

## 执行记录

- `cross-target-stop-before.log` / `cross-target-lock.json`：原用户反例修前输出。
- `backend-isolation-before.log`：产品修改前 3 failed；`backend-before/*.json` 保存三例实际结果。
- `backend-isolation-after.log`：首批针对性与既有目标管理/重复完成检查 **17 passed、4 subtests passed**，17.86秒。随后增加短用户锁排队检查，已在最终全套执行。
- `cross-target-stop-after.log` / `cross-target-lock-after.json`：原用户脚本修后正常及取消分支均为 `otherStopCompletedWhilePublishPending=true`、pending期间B已 stopped；修前文件没有覆盖。
- `backend-after/*.json`：Start/Stop/B发布独立、同目标改址/删除、取消/失败恢复、用户锁排队的实际状态。
- `backend-full.log`：**175 passed、218 subtests passed，53.27秒，无跳过**。命令为 `python -m pytest web_panel/tests -q`，设置 `PYTHONPATH=src;web_panel/backend;web_panel/tests`、`EMO_WEB_PRODUCT_RUNTIME=1`、`EMO_WEB_PRODUCT_PYTHON=D:\Conda\envs\emo_master\python.exe`。真实软件 Runtime 独立进程的证据保存在 `product-runtime/`，包括独立Web外部count=2及显式Qt失效校验；没有把Mock并发反例说成真实跨机网络测试。
- `backend-lightweight-source.log`：轻量解释器直接导入当前后端源码，没有安装或加载 emo_master、PySide2、cv2、onnxruntime。
- `backend-pip-check.log`：依赖检查通过。`backend-diff-check.log`：相关源码和测试差异空白检查通过。
- `backend-ruff.log` 保留Ruff全文件检查的失败：app.py的未使用urlsplit和logout局部who均为固定HEAD已有问题。`backend-ruff-existing-findings.json` 比较HEAD与当前文件，两项相同、无新增；不将整文件lint记为通过。`backend-ruff-added-paths.log` 显示 editor.py、editor_routes.py、新测试的F检查通过。本轮不清理无关代码。
- `backend-source-state.json` 固定本轮源码摘要；主任务重建wheel后应核对最终后端字节。

独立只读终审核对了目标锁/发布锁的两层隔离、锁内重读、用户锁取序和多目标启用，没有发现本轮新增阻断。该结论属于静态终审，与上述实际执行结果分别记录。

## 冻结与未执行层级

本轮后端产品源码和测试已冻结。主执行者负责后续 Web wheel、归档和总验收记录；当前文件不宣称旧wheel已经包含第二轮代码。

本子任务没有执行浏览器操作、人工原生 Qt、跨电脑 LAN、真实设备、部署或冻结 EXE验收；也没有重建或安装wheel。相关本轮前端与最终交付检查由主执行者另记，不把源码自动测试扩称为这些验收。
