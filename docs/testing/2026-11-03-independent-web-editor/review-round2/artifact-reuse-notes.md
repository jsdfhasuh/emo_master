# 第二轮产物复用核对与只读审查边界

本记录针对第二轮“同目标发布协调与跨目标控制隔离、固定终端高度预览”收尾。
本子任务没有修改 Runtime、SDK、Web 实现或共享验收 README，没有重建产物、提交、推送或部署。

核对时分支为 `codex/logging-consolidation-20261103`，HEAD 为
`d92ffd37960e8b8f527136151ab9ccb80054e78f`，工作区包含此前实现及本轮进行中的未提交修改。
该 SHA 只是基线，不代表未提交实现的完整内容。

## Runtime 与 SDK 可以复用

本轮开始时，以下文件的 SHA-256 和长度与上一轮
`review-fixes/delivery-artifacts.json` 完全一致。

| 产物 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `build/independent-web-editor/runtime/emo_master-0.6.6-py3-none-any.whl` | 1,662,929 | `aeb26acc932485cb6942b7cc0bffa10bb24118d5007913ca6da73febebd6ecd1` |
| `web_panel/contracts/panel-contract-v1/python/emo_panel_protocol-1.5.0-py3-none-any.whl` | 40,265 | `97db049bbbc86152f899af809c4e670b582e345b061f6a3e439d6722f5d33a6d` |

实际执行的检查：

- 两份 wheel 的 ZIP 完整性检查通过。
- Runtime wheel 的 **620 个 Python 模块**与当前 `src` 的 Python 文件清单及每个文件的字节完全一致，没有已删除的 Designer `web_preview.py`。
- SDK wheel 的 **15 个 Python 模块**与当前 `src/emo_panel_protocol` 的清单及每个文件的字节完全一致。
- SDK wheel 摘要与当前合同 manifest 一致。
- 以上核对没有依靠移动分支名、旧成功日志或版本号推断源码一致。

逐项结果及可重复计算的源码清单摘要见
[`artifact-runtime-sdk-current.json`](artifact-runtime-sdk-current.json)。

只读完整交付输入检查也实际执行并通过：

```powershell
& 'D:\Conda\envs\emo_master\python.exe' scripts/package_independent_web_editor_delivery.py --check-only
```

日志见 [`artifact-check-only-before.log`](artifact-check-only-before.log)。该次检查同时确认了当时的
Runtime、SDK、Web wheel 与源文件、前端 `dist` 与后端 `static` 的一致性；当时的 Web wheel
为 188,352 字节，SHA-256 为
`d098b3c99700276d2587c9013579575007a7f4d19dc024d723b964709a3719eb`。

**这是第二轮改动完成前的快照。** Web 后端或前端修改后，该 Web wheel 和旧交付归档不能据此记为最终产物。
根任务须在本轮源码稳定、前端构建并同步 `static`、Web wheel 重建后再次执行完整检查，更新最终交付及其摘要。
Runtime 与 SDK 无需仅因 Web 层修改而重建；最终检查继续匹配当前源码时可复用本表中的两份 wheel。

本轮没有重跑上一轮的 188 项 Runtime/SDK/Qt 采集回归；没有 Runtime/SDK 源码差异是复用既有结果的依据。
本轮新执行的是完整档案检查和源码字节比对，不是安装后 Runtime 检测、原生 Qt UI、冻结 EXE、异机 LAN 或真实设备验收。

## 两项源码的独立审查建议

已阅读 `editor_routes.py`、`editor.py`、`app.py`、`services.py`、Runtime 客户端创建入口、
`Editor.tsx` 和 `PanelRenderer.tsx`，并分别向负责后端和前端的 agent 发送建议。
以下是本轮开始时的实现观察与改法建议，不是对尚在实施改动的最终通过声明。

### 同目标协调与跨目标隔离

当前全局 `app.target_lock` 覆盖发布的网络等待，因此 A 的发布会拖住 B 的启动、停止或连接管理。
`EditorStore.publish_lock` 也是全局；只拆分前者，A 的发布仍会拖住 B 的发布。
建议两层都按 Runtime 目录中的稳定 `rid` 隔离，同一 `rid` 的发布、查询确认、继续、回退、结束跟踪、
连接修改/删除及控制继续串行；同一 `rid` 的不同工程也不能绕过该协调。

需保留旧的重复完成请求在锁内重新读取权威发布记录的修复，避免旧完成请求重新启用旧版本。
同目标连接修改不能在发布等待确认时更换客户端；不能通过放开该约束实现跨目标并发。
现有视图创建、授权和多目标包启用也使用这条协调路径，调整时仍须保留其必要约束。
多 `rid` 启用可使用固定排序获取相应目标锁，并保持全部校验后的一次 SQLite 启用事务。

建议的直接验证是：A 延迟发布且尚未恢复时，B 的启动、停止及发布可以完成；A 自己的连接修改仍等待。
保留同目标重复完成、后续新版本发布和未知写入不重放的现有检查。

另观察到 `Operations.execute` 的全局锁覆盖 `runtime.control` 等待。它不影响“A 发布等待导致 B 控制阻塞”
这一指定反例，因为发布没有占用该锁；若范围另含“A 控制等待与 B 控制隔离”，才需对它作直接评估。
此点已通知后端 agent，由当前明确范围决定，不扩为通用并发框架。

### 固定高度与正式运行行为

当前 `PanelRenderer` 通过 `window.innerHeight - root.top - 16` 计算根布局可用高度，
弹性纵向空白组件消费此高度。Editor 固定尺寸预览仅设置外层 `minHeight`，因此固定终端预览仍随宿主窗口高度变化。

建议增加由编辑预览显式传入的可选终端高度参数，并设置明确的固定终端容器高度及滚动边界。
未传入参数时保持现有正式运行页面的窗口测量行为；切换终端尺寸时重新计算布局。
现有 `canvasTop` 向上扩展应只增加一次扩展空间，不能同时从内部弹性高度扣减或重复增加。

建议真实浏览器分别验证：相同固定终端在不同宿主窗口高度下布局一致；多个固定尺寸切换及时生效；
内容超出时可滚动；跟随窗口和未传可选参数的正式运行页仍随宿主窗口变化。

本记录没有将上述建议当作已完成的修复或已执行的浏览器验收；最终实现和交互证据由相应 agent 与根任务汇总。

## 实现稳定后的只读复审补记

后端稳定版本的 `app.py`、`editor_routes.py` 和 `editor.py` 已重新审读。
目标协调锁和发布记录锁都按 `rid` 获取；发布确认、继续、回退、结束跟踪仍走同目标协调，
发布记录仍在锁内重新读取。授权路径采用“先目标锁、后短用户目录锁”的顺序，
删除用户只获取短用户目录锁，均没有持有全局用户锁等待某个目标的 RPC 或发布。
多目标旧包启用按排序后的目标 ID 获取锁，最后继续使用一次 `activate_many` 事务。
此范围内未发现新的阻断问题。

已读取负责 agent 实际执行的 `backend-full.log`：**175 passed、218 subtests passed / 53.27s**。
本子任务没有重复执行这套后端测试。
另读取 `backend-after/cross-target-stop.json`、`cross-target-publish.json` 和 `queued-grant-user-guard.json`，
确认记录分别显示：A 发布仍挂起时 B 停止成功、B 发布完成，以及 A 的授权请求排队时 B 的授权和停止均完成。
A 发布只提交一次采集请求，没有隐式检测控制。
后端源码冻结摘要见 `backend-source-state.json`。

前端稳定版本已重新审读：可选 `viewportHeight` 表示整个固定终端逻辑高度，
根布局扣除原有 16px 顶部间距；只有编辑预览显式传入，未传时仍使用原来的窗口测量。
固定预览容器明确设置高度与 `overflow:auto`，组件保留自己的正常尺寸。
`canvasTop` 的内部布局使用逻辑可用高度，外层只增加一次向上扩展空间。
此范围内未发现新的阻断问题。

已读取负责 agent 生成的真实无头 Edge 几何记录
`after-fixed-height-attempt1-height-browser.json`：示例和实时预览在宿主高度 900/1200 下均为
终端 768、内部布局 752、顶部间距 16、底部偏移 768，`scrollHeight == clientHeight == 768`。
超出内容场景保留 900/400 像素组件尺寸，滚动高度 1364，滚动 596 后可见底部。
跟随窗口场景分别保留 645/945、596/896 的原有布局高度。
另读取 `frontend-node.log` 的 **98 passed、0 failed、0 skipped**；其中新增的静态渲染检查覆盖
`canvasTop` 扩展一次。这里没有将该 Node 检查记为单独执行过的 `canvasTop` 浏览器验收。

本补记属于独立源码复审和对执行证据的核对。真实浏览器记录使用构建 React、模拟 HTTP/实时视图 WS，
不能据此认定真实 Runtime、设备、原生 Qt 或最终安装包全部通过。最终 Web wheel、已安装运行和归档的核对仍由根任务汇总。
