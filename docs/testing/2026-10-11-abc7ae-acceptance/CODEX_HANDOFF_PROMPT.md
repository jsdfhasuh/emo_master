# 给 Codex：EmoMaster 局部修复与固定证据复核

请在 `jsdfhasuh/emo_master` 的现有 `agent/runtime-workflow-architecture-v1` 分支上，依据本目录验收报告完成以下局部修复与回归。不要大规模重构，不新建远端分支或 PR，不合并、发布或部署。

## 先确认版本与现场

1. 从远端读取现有分支，记录当前 HEAD、实际路径、Python/Qt、Git status 和已有本地改动。用独立 worktree/测试数据目录，不 reset --hard、不覆盖别人的未提交改动。
2. 本报告唯一实测版本为 `abc7ae0541e997be0bbe3a4794ad604c239de79a`；报告归档提交只是文档交付，不是新产品版本验收。修复应基于你实际拉取的最新远端代码。若已有后续修复，先逐项对照 diff 与针对性测试，记录“已被哪一提交修复/仍存在”，不要把代码改回旧版本或重复套补丁。
3. 先读仓库 AGENTS/开发测试入口，再读：
   - `docs/testing/2026-10-11-abc7ae-acceptance/README.md`
   - `REPORT.md`、`case-matrix-final.json`
   - `diagnosis/README.md`、`diagnosis/SQLITE.md`、`diagnosis/NG_INCOMPLETE.md`、`diagnosis/ROI_EXPORT.md`
   - `diagnosis/probes/VARIABLE_UNAVAILABLE.md` 及同目录原始脚本/首轮 JSON/日志
4. 完整证据按归档 README 合并分卷，并先验证 SHA256；不要把旧辅助脚本直接当操作授权。代码定位链接均固定在 abc7ae，最新版本行号可能改变。

## P1：已隔离复现的变量错误码／结果闭合 credit 缺口

固定版本位置：
- `src/emo_master/apps/runtime/presentation/collector.py:197–212`：变量/计数器捕获失败发出 `VARIABLE_UNAVAILABLE`。
- `src/emo_master/core/presentation/results.py:54–62`：`ClosedSource.reasonCode` 不接受该值。
- `src/emo_master/apps/runtime/presentation/store.py:88–104`：`close()` 在模型校验前已移除 open。
- `src/emo_master/apps/runtime/presentation/service.py:296–315`：`_finish()` 仅在 close 成功时归还 credit；随后删除 pending。

一次独立内存模拟已证明：真实 collector 的注入 KeyError 产生非法 reason；真实模型抛 ValidationError；open 消失但无 history/latest。首轮 `_consume/_finish` 异常后 pending 保留、credit 未归还；模拟后续 `_finish` 删除 pending 后 credit 仍未归还。合法 reason 对照可生成 INCOMPLETE 并正常归还。该补证没有启动真实 Runtime/Worker/设备，不能冒称完整 Runtime 泄漏实测；也不是历史 NG 的根因。

请完成最小修复：
- 明确并统一生产者、DTO、RPC/客户端支持的合法错误码语义。兼容旧消费者；不要只把异常吞掉或把失败标 COMPLETE。
- 校验与结果发布、open/pending 移除、credit 归还应具备可靠的状态顺序和恰一次所有权处理。避免双重 release、先释放仍被生产者使用的图像/资产，或留下永久占用。
- 为未知或非法 source 的闭合失败提供明确日志与可观察失败状态，不能破坏其他 Job/结果处理。
- 增加回归覆盖变量读取失败、错误码合法性、模型校验失败、重复结束/晚到回调、正常路径、多个结果后仍能继续捕获，以及最终资源退休。可先复核原隔离脚本，再加入真实 Runtime 集成测试；原脚本和首次结果不要改写。

## P2：SQLite 首 FAIL 的观察语义修复，保留业务安全断言

位置：
- `tests/test_r3_sqlite_phases.py:486–503`
- `src/emo_master/apps/runtime/context/sqlite_transactions.py:15–35`
- `scripts/r3_sqlite_phases.py:65–164,187–206,233–260`

原全量失败为 `testRealWriterContentionIsVisibleAtBeginAndPreservesAllDurableRows:493`，提示 `writer never entered measured BEGIN IMMEDIATE`。新 helper 在短 native BEGIN 之间 sleep，observer 的 active 仅覆盖 native 调用。已确认观察语义不匹配；原运行没有阶段 trace，仍不能把这个解释说成唯一已证实根因。

请保留原 2 秒/5ms 预算，不通过加 sleep、增 timeout、重复重跑或删安全断言制造通过。可用累计 entered/failed 与 observer 健康作为进入准入的信号；持锁期间仍需验证未成功 BEGIN、未进入业务读写/commit；释放后仍要验证线程结束、无错误、恰一次提交、持久行、WAL/同步约束。原 `synchronous` 检查的是 blocker，不是 writer，应补真正 writer 的配置证据。

BEGIN 可有界重试，但事务 body、未知 COMMIT、外部 ACK 不得自动重放。deadline oversleep/restore 错误是附录列出的未证边界，先明确契约并补有界故障测试，不把它们假称已发生的生产事故。保留首次失败 `automation/full/pytest.log`、JUnit、原失败数据库和独立 phase 诊断，不用后来通过覆盖。

## P3：ROI 专用预览完整查看／导出，保留身份和生命周期

位置：
- `src/emo_master/plugins/builtins/roi/editor.py:223–303`
- `src/emo_master/plugins/builtins/_editor_support.py:268–348`
- `src/emo_master/apps/designer/operator_editors/builtin_layout.py:17–50,82–107`
- `src/emo_master/apps/designer/ui/widgets.py:75–107`
- `src/emo_master/apps/runtime/grpc_server/service.py:1040–1121`

算法已有四个完整图像输出，但专用界面显示三个，`maskedImage` 未展示；另有 frame/roi 结构化结果。回调只把资产降成端口→ID并缩放显示，没有可导出的完整结果身份。底层完整 PNG 生成与受 session/projectKey 约束的下载已有，不需要重写 ROI 图像算法。

请在局部 UI/结果适配层补自然可发现的完整查看和原始数据保存，并明确四个图像/结构输出的入口与范围。不能保存缩略 pixmap 冒充原图，不能读缓存路径绕过权限；使用合法资产下载。保留不可变请求参数、输入、generation、固定 Runtime/session、端口和资产身份。

当前 pure-preview RPC 没有 operator-debug 的 executionId、resolvedParams 等完整元数据。不能伪造 ID 或拿 rawParams 冒充 resolvedParams；若扩展完整身份包，明确所需协议元数据和兼容行为。原始 PNG 与带身份归档应分开说明。

覆盖未 Apply 草稿、坏图恢复、快速改参旧结果、同 ID 不同窗口、Runtime 重连、取消/过期、关闭中导出、写盘失败。清理必须等待所有 producer 实际退出，指向原 Runtime，不触碰已销毁 Qt 窗口、不误删持久资产。通用算子调试导出可作替代，但它会重新执行，不是刚才 pure-preview 同一次结果。

## P4：历史 NG／P0 只补诊断，不伪称根因已关闭

先读 `diagnosis/NG_INCOMPLETE.md` 和历史原件：
- 生产 NG 旧失败发生在本机 `presentation.store.latest`，缺 source/reason/terminal，后续业务值和 RUNNING 断言未执行。合法 false 是 AVAILABLE，NG文本本身不产生 INCOMPLETE。
- P0 ordinal20服务端已 image UNAVAILABLE/TimeoutError；旧 `_own` 有 STARTED poll、STARTED身份不匹配、completion poll 三个候选，异常类名无法区分。
- 两条实现不同，不得混因；当前 PASS 和生产 presentation代码未变都不能关闭历史根因。其他改动影响调度仅是可能性。

只做有界、可关联的诊断补强：保存 resultKey、Job/workflowRun/invocation、scope/ordinal、terminal、每个 source 的 state/reasonCode/detail、来源 node/port/iteration、导出 task/stage/实际时间线，以及预算/排队/复制/编码/收尾数据。核对已有 abc7ae 诊断是否足够，避免重复堆日志或泄露图片/凭据。不得放宽原 deadline、自动重发外部动作、把非完整结果伪装 COMPLETE。用固定次数、独立进程验证并保留每次结果；未复现应写未复现，不写已修根因。

## 验证、边界和交付

- Python 3.10 / PySide2；Debian真实 Qt 桌面与 offscreen/pytest 分开记账。无法实测的 UI/Windows/设备明确 NOT_RUN。
- 回归相关新增测试及仓库 pytest/Proto/Ruff/mypy入口；不要重复相加全量与专项数量。复查 D01–D08、约80秒暂停继续、O20两次独立Job写入[7,13]及资源释放后保存重开、草稿预览/完整导出/非法JSON恢复。
- 不连接真实相机、PLC、机器人、生产 TCP 或生产数据库。故障注入只在隔离数据和本地模拟服务中进行。
- 每项给 PASS/FAIL/SKIP/NOT_RUN、最小复现、修改位置、实际版本、首次失败及后续固定复跑结果、完整日志/JUnit/截图和资源退休证据。
- 交付局部修改清单及仍未关闭项目，明确哪些是已修功能缺陷、测试同步问题、体验改进或仅诊断加强。
- 提交推送遵循用户在当前 Codex 会话的明确授权；不要把历史“报告归档推送”授权扩成产品发布。无论如何，不新建远端分支/PR、不合并、不发布、不部署。
