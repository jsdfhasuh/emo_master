# 第三轮：额度超限的具体诊断

本项针对非阻断细节 2。基线为 `d92ffd37960e8b8f527136151ab9ccb80054e78f` 加当前未提交实现，分支 `codex/logging-consolidation-20261103`。未回退、提交、推送、部署或操作现场设备。

## 修前与修后

`quota_probe.py` 使用现有 `imageProject` / `create_demo_project` 夹具创建临时软件工程：WorkflowStore 默认结构，工程 2.9、页面 1.2、devices 1.0 且空设备；经模型校验、保存重读与工作流编译。Qt 绑定 load.image 和 blob.mask，外部请求绑定同两路时通过；加 blob.overlay 第三路时拒绝。没有 Start/Stop。

- `quota-before.json`：第三路正确拒绝，但只有 `normal capture supports at most two distinct image sources`，`diagnostics=[]`。
- `quota-after.json`：仍以相同 `INVALID_ARGUMENT` 拒绝，保存请求保持为空；诊断指出工作流 main、合并后的 3/2、五个来源 ID 的 Qt/外部归属和具体输出地址。三个图片组的消费者数量是 2、1、2，清楚显示等价 Qt/Web 输出只占一路。

## 局部修改

- Runtime `output_requests.py` 在现有逐工作流 Qt/外部合并后，仍由原 `outputCaptureLimits` 决定接受与拒绝。仅在拒绝时说明 scope/source/image 预算、实际计数与上限、工作流、来源及作用域地址、Qt/外部归属和等价图片组。必要 Qt 校验与 ID 冲突检查均保留。Runtime 回包不含 pageId、componentId 或 Web 控件信息。
- Web `editor.py` 将通用 sourceIds 关联到候选完整页面源中的控件，增加 `componentRefs[{pageId,pageName,componentId,componentName}]`。只关联相关来源；图片额度诊断不会因共享结果作用域而误指向数量等无关控件。控件关系只存在于 Web 版本记录，未写入 Runtime 请求。
- 16 个作用域、16 个来源 ID、2 路不同图片、8 MiB raw slab、图片语义身份和实际采集/编码/资源归属逻辑均未修改。失败不丢弃来源，不提交采集请求，不启用候选包，不启动或停止检测。
- 前端发布诊断呈现由 `editor_frontend` 子任务负责；本文件记录该后端/Runtime 部分，不把未执行的浏览器操作记为通过。

## 已执行检查

- `quota-runtime-focused.log`：5 passed，包括等价图片真实结果/像素/归属/资源租约、第三种图片拒绝、调用与字段路径区分，以及合并后来源/作用域 17/16 的具体诊断。
- `quota-web-focused-final.log`：10 passed，包括保存拒绝诊断及控件关联、草稿和当前包不变、零写入/零检测控制，以及真实 loopback gRPC 对 JSON 诊断字段的完整透传。
- `runtime-sdk-qt-regression.log`：190 passed / 178.08s；13 个文件的 Runtime 外部请求持久化及重启、Web/Designer/本机统一启动、Qt 正常采集与必要校验、图片通道及资源寿命、ReleaseHost、SDK 契约和 proto 检查。包括本轮 `_startReply.project_revision` 的真实旧任务修订断言。与上述定向组存在重叠，不累加测试数。
- 定向文件 Ruff F 及整个工作区 `git diff --check` 通过；最终源码指纹见 `quota-source-freeze.json`。

保留中间失败证据：`quota-focused-first.log` 为使用产品 Conda 环境同时收集 Web 测试时缺少 httpx2，未执行测试；改用既有 Web `.venv`。`quota-web-focused.log` 为新增 gRPC 测试夹具遗漏 Runtime info 的协议能力标志（1 failed / 9 passed），补齐真实契约要求后最终通过，未放宽产品能力检查或正确断言。

## 产物影响与验收边界

`artifact-runtime-sdk-before-rebuild.json` 为只读归档检查：SDK 1.5.0 wheel 仍为 40,265 字节，SHA256 `97db049bbbc86152f899af809c4e670b582e345b061f6a3e439d6722f5d33a6d`；15 个模块逐字节匹配且契约 manifest 摘要一致，可原样复用。旧 Runtime wheel 的 emo_master 包 603 个模块 inventory 相同，仅 `grpc_server/service.py` 与 `presentation/output_requests.py` 已变化；另有其他 Runtime 包模块，完整 620 模块检查由总打包脚本负责。Runtime 和 Web wheel 必须由主任务重建，本子任务未改 wheel 或打包脚本。

本项证据层级为源码自动检查、临时软件 Runtime 的直接 RPC 方法、公开 DisplayService gRPC 和合成服务的真实 loopback gRPC。未执行原生 Qt 人工操作、冻结 EXE、安装后 Runtime 完整检测、跨机 LAN 或真实设备验收。它们不因此记为通过。
