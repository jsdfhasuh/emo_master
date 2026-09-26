# Qt 运行页面 P0 第二轮执行记录

起点 `e19a9202e9d904cd07a6165ecc00d76b71639a7d`，分支 `agent/runtime-workflow-architecture-v1`，开始时 clean。保留第一轮报告与全部失败证据。本轮只做 P0，不启用生产入口或项目 2.2。

## 批次一：持续异步链路

`pipeline.py` 在一个窗口内保持 Results、Job 身份、业务序号、有限历史；只选择根流程 `load.image` 和 `source.items`。真实 WorkflowRunner 保留旧 PreviewSnapshotWriter 开销。检测只复制并非阻塞提交，两个固定 owner 线程管理原有两个 spawn 导出单元；待处理额度仍为零。导出成功/失败作为真实结果来源参加封闭，成功文件 rename 到独立缓存后才发布 assetId。

`resources.py` 用同一个令牌账本预留 Collector、快照、图像、共享内存、编码、读流、暂存、缓存和租约；物理缓存只计一次，pin 是受 64 MiB 限制的缓存子集。历史32，租约句柄16、最长30秒；实际 unlink/关闭读引用后才归还。客户端另有单张16 MiB传输+解码+8 MiB原图预算，不以服务端账本代替 RSS。

`continuous.py` 两个独立长驻 gRPC channel 经 loopback 按 ID 获取 PNG，分别校验结果身份、PNG摘要、解码后原始像素摘要和冻结 count=7。业务序号跨预热和测量连续。40次测量跨历史淘汰，窗口中不 close/重置账本；窗口结束才停订阅、停服务、回收导出并释放缓存。

第一批验证：`pytest tests/p0 -q`（36项），原型 Ruff；最终原始日志与后续测量统一放第二轮证据目录。当前结果仅证明正常短窗口和资产边界；RPC异常清理、两Job故障饱和及稳定性能由后续批次补齐，不能据此宣称 P0 通过。

## 批次二：网络兼容与异常所有权

真实旧 stub 的 UploadPreviewImage → StreamPreviewAsset → 解码比对通过；保留64 MiB累计上限、1 MiB传输消息上限，另限制1024分块和68 MiB带元数据暂存，超过返回明确错误。接收阶段在 bulk 池写临时 spool，完整接收后才调用原服务，不在 aio 循环做文件操作；部分上传取消不生成资产。原服务的上传解码/临时资产仍是旧路径，不能把展示账本当作它或整个 Runtime RSS 的限制。

构造、next、close 在所属分类池中；取消回调使用固定13线程上限的清理池（仅有准入中的 RPC 可提交），槽位覆盖回调、未完成工作及close全部寿命。close异常记录后归还已完成的工作；close阻塞仍占额度，控制请求保持可用。停服4秒后仍未结束则明确失败，保持loop/池/额度供监督处理，不伪称已回收。

外层监督实测：构造抛错/阻塞、首条前取消、next中取消、close抛错/延迟、序列化异常、回调阻塞、启动失败、停服超时后释放门闩并重试。另用两个真实 Runner Job共享同一Pipeline，重复hang/IPC故障、第三导出拒绝、INCOMPLETE封闭、恢复后两网络客户端解码、慢读取消保留资产引用、500ms读期限、运行中close和最终子进程归零。

`pytest tests/p0 -q`：38 passed（38.07s）；R09六排列/重复/换Job、R10真实spawn回收、R11临时debug隔离及C1—C4均重跑。正式长流若第三方next永久不响应取消，Python线程不能强杀，槽位不会复用；此情况被报告为不可回收并由测试外层进程树监督结束，不能当作正式设备驱动已验收。

## 批次三：测量口径与基线修复

测量使用3轮、每组预热8件+测量96件；五组为原功能、无presentation、采集零客户端、一个客户端、两个客户端。每轮轮换起始组，按本轮原功能P95配对计算回退。绝对5Hz输入不等待导出，保留Runner串行语义；scopeEnd仍来自Runner实际workflow终态。模型P95只统计实际完整解码，同时以全部96件为覆盖率分母；任何缺失/INCOMPLETE都使正确性和性能门槛失败，不能用有效样本P95掩盖缺失。

单任务导出/读图/封闭500ms、回收1s全部不变；外层benchmark watchdog根据固定窗口长度计算，默认432s，其他故障场景90s。证据同时记录执行、正确性、性能、兼容性；写入起止HEAD/status、diff SHA256及受测源码SHA256，禁止覆盖已有证据。

私有预览基线修复独立提交 `323f35d`：原测试长路径下临时PNG文件超过Windows传统路径限制，事件记录preview.snapshot.failed；私有存储使用扩展I/O路径。同时屏障复现GetJobStatus在终态资产promotion前返回COMPLETED，改为promotion完成后发布状态，回调异常仍发布终态。旧测试断言不变，新增长路径和回调屏障/异常回归。Runtime/core/e2e：356 passed、1 skipped（既有无符号链接权限）。完整CI的proto/Ruff/mypy通过，pytest仍原生访问冲突3221225477。

Qt基线隔离：全部Designer在workflow-tabs import用例的processEvents路径崩溃；workflow-tabs单文件10 passed，main-window组34 passed；前置Designer文件拆成两半分别加workflow-tabs，61/46 passed。说明是组合运行/生命周期相关，尚无足够证据定位到某个Qt对象；没有据此猜测性改动Qt生产代码或调整原断言。后续独立进程结果与原失败分列。

补充故障回归：在Designer分文件回归同时运行时，短窗口首次出现33/40与40/40不对称覆盖（`final-p0.txt`保留FAIL）。原客户端读图超时会结束订阅；修复为本件UNAVAILABLE/INCOMPLETE并继续接收，禁止旧OK继续占live。增加真实网络慢读超时后下一件恢复断言，保持500ms读/解码截止与完整率原断言不变。39项P0复验通过；新增3轮不重建AssetStore的120件缓存/租约周转（88次淘汰），以及上传发送中断、上传执行中取消、停服API异常重试。spawn时钟诊断历史也限制为64条，避免长期重复回收积累。

Designer逐文件独立进程验证49个文件、333 passed；原完整CI的组合原生崩溃仍未解决。这是补充回归，不是全量CI通过。性能首组三轮（`final-evidence.json`）完整保留；客户端故障分支修改后再做完整三轮，使用另一证据文件，不择优替换。

## 环境、执行与证据身份

Windows 11 10.0.26200、i7-12700F，Python 3.10.21（`C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe`），PySide2 5.15.2.1；grpcio/grpcio-tools 1.78.0、protobuf 6.33.6、NumPy 1.26.4、OpenCV 4.10.0.84、pytest 9.1.1、Ruff 0.15.6。真实依赖全表见JSON；未换Qt6/QML/Web。所有项目、SQLite、PNG、输出及旧上传资产在临时根目录，无设备调用。

起始HEAD：`e19a9202e9d904cd07a6165ecc00d76b71639a7d`。第一套三轮证据测量`fa166cc4575f227a49033aa5d4663293b4a17b28`，起止clean且源码摘要相同。最终套测量使用客户端故障恢复后的提交，完整HEAD、起止status、diff SHA256和源文件SHA256在`final-code-evidence.json`。证据归档提交仅改变文档/证据；不得把第一套数据声称为后来代码的实测。

复现（仓库根目录，python为上述解释器）：

```powershell
$env:PYTHONPATH = "$(Resolve-Path src);$(Get-Location)"
$env:QT_QPA_PLATFORM = 'offscreen'
$env:HUARAY_CAMERA_SMOKE = '0'
python scripts/ci_check.py
python -m pytest tests/p0 -q
python -m ruff check prototypes tests/p0 scripts/p0_validate.py
python -m pytest tests/runtime tests/core tests/e2e -q
python scripts/p0_validate.py --count 96 --rounds 3 --warmup 8 --output manual_test_workspace/p0-round2/new-evidence.json
git diff --check
```

同名证据文件禁止覆盖；长窗默认外层432秒，逐任务导出/封闭/读取500ms、回收1秒不变。可单独`--scenarios exports network network-faults pipeline-faults`重跑故障。`designer-isolated/index.json`记录49个独立pytest命令、耗时、返回码，配套每个文件日志。初始和最终CI原生崩溃栈、所有中间测试失败和短窗口失败均保留；不是只归档最后成功输出。

## 公共实验接口、预算与验证边界

继续选定一个RuntimeService + 一个loopback grpc.aio端点，分类池控制4、事件2、展示2、模拟相机预览1、资产读2、bulk2，准入外排队0；清理回调池固定上限13，只能由已准入RPC提交。next/构造/close/上传文件操作全部离开事件循环。未结束的next、close、回调或上传仍占原分类额度；停服未完成明确报错，测试外层监督负责不可回收进程树，不结束检测Job伪装隔离。

旧生成proto/stub保留，UploadPreviewImage使用bulk分类有界spool，再执行原服务；原64MiB累计限制保留。1MiB网络消息、1024分块/68MiB含元数据spool是明确拒绝边界，不支持无限空分块。完整上传成功才生成临时旧资产；发送中断、接收取消、执行中取消都有不新增残留资产断言。

实验JSON API：`GetDisplaySnapshot({job})`，`StreamDisplayUpdates({job,cursor})`，`GetDisplayAsset({job,asset_id})`，`Pin({asset_id,seconds})`，`Unpin({lease})`。不是正式typed proto；正式客户端和项目格式不变。结果流有界历史32，缺口标reset；独立快照恢复、末次无新通知及R09排序回归保持。资产读只接受ID/Job，返回64KiB块，校验所属Job、文件摘要、像素摘要、尺寸及resultKey。

账本预算不提高：每Job128MiB / Runtime256MiB；单图8MiB、每结果原图16MiB；两个spawn导出、pending0；暂存64MiB、缓存256MiB、租约64MiB；history32、scope identities16（固定原型最多2个Job身份）、lease handles16且最长30秒。每Job预留16MiB覆盖4个并发控制快照在序列化/发送阶段的副本；展示流逐消息预留4MiB，直到生成器继续/close才解除引用并归还。图像按6倍原始字节保守预留，DONE前子进程先丢弃真实缓冲引用。

非图像仍是每来源256KiB/每结果1MiB、最多16来源、8个OPEN、集合4096/深度12/节点16384，超额UNAVAILABLE不截断。缓存按真实文件共享计账，pin只是缓存的受限子集；资源释放先unlink/解除读引用，后归还令牌。原预览/旧上传自身既有缓存与Python/gRPC库内存另列，账本不冒充整个进程RSS。

窗口内Pipeline、Results、任务身份、消费者均长驻，最终104个序号（含8件预热），只在窗口结束close；每组缓存/历史至少3次周转。两Job故障、慢读取消保留引用、超时后恢复、租约满额/别名/过期、原workspace清理后资产可读、重复阻塞及IPC回收均有断言。有限租约额外在同一AssetStore中120件/3轮周转，88次淘汰，不在轮间清账。

连续测量用真实WorkflowRunner在父进程执行，两个spawn子进程导出；两个客户端是独立channel/接收线程，使用真实网络，未声称是两个独立OS客户端或正式Qt页面。真实JobSupervisor + worker spawn、强杀收尾、R09—R11与C1—C4在同一P0套件的单独场景回归；没有把展示原型接入正式Job生产入口。

## 归因、可迁移部分和剩余条件

既有失败：完整CI的Designer原生访问冲突仍在；单独49文件333 passed不能替代组合CI。旧预览来源为空已用独立小修复解决（Windows长路径和终态promotion竞态），Runtime/core/e2e 356 passed、1 skipped。skip是既有符号链接权限问题，未计通过。

本轮新增失败：并发回归期间短窗口消费者33/40，原订阅读图异常后退出；已修复为当前件不可用并继续，新增慢读恢复测试。完整率断言不弱化，500ms不放宽。性能测量中的迟到、低吞吐和超龄是实际未通过项，全部样本保留，不归入skip或环境未执行。

可迁移：开始ordinal/消息cursor分离、不可变来源冻结、导出参加封闭、统一资源令牌及实体所有权、可回收spawn/新Pipe、分类准入与真实退出后归还、客户端读失败失效/恢复，以及可重复故障测试。可抛弃：JSON generic RPC、固定两来源采集适配、临时测试算子/模拟相机、开发机Windows采样器和测试客户端；不是正式UI、发布或设备接入代码。

剩余P0条件：在同样固定输入、同样旧预览成本下解释开发机文件I/O/调度波动，并重新证明200ms模型P95、检测P95回退≤5%、持续数据年龄≤500ms与实际5Hz；将进程RSS/句柄走势收敛为有依据的稳态判据，不能只靠账本归零；定位并修复组合Qt崩溃后再跑原完整CI。Qt可见率/50控件、冻结包、目标工控机与真实设备属于后续阶段，本轮NOT_RUN，不作为偷换P0结论的依据。完成第二轮后停止，不进入P1。

## 两套三轮完整测量（均保留，不能择优）

### final-evidence.json

受测 `fa166cc4575f227a49033aa5d4663293b4a17b28`；起止clean且代码摘要相同：True。execution=PASS，correctness=PASS，performance=FAIL。

| 轮 | 组 | 检测P95 ms | 配对回退% | 模型P95 ms | 实际Hz | 最大迟到ms | 最大live年龄ms | 实际解码/96 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 1 | original | 136.68 | 0.00 | — | 4.998 | 541.09 | — | — |
| 1 | new-no-presentation | 293.47 | 114.71 | — | 4.999 | 1162.55 | — | — |
| 1 | capture-zero-viewers | 138.33 | 1.21 | 14.83 | 4.997 | 15.85 | — | — |
| 1 | one-consumer | 569.14 | 316.40 | 84.83 | 4.228 | 4558.52 | 813.32 | 96 |
| 1 | two-consumers | 387.66 | 183.62 | 181.31 | 4.656 | 1579.26 | 684.91/684.19 | 96/96 |
| 2 | capture-zero-viewers | 135.32 | -55.50 | 16.03 | 4.920 | 311.48 | — | — |
| 2 | one-consumer | 141.67 | -53.41 | 88.36 | 5.000 | 15.76 | 315.10 | 96 |
| 2 | two-consumers | 148.24 | -51.25 | 171.39 | 5.066 | 1257.93 | 768.95/769.41 | 96/96 |
| 2 | original | 304.10 | 0.00 | — | 4.998 | 1169.06 | — | — |
| 2 | new-no-presentation | 294.19 | -3.26 | — | 4.851 | 596.58 | — | — |
| 3 | two-consumers | 144.48 | -53.80 | 201.73 | 5.001 | 16.06 | 418.26/417.81 | 96/96 |
| 3 | original | 312.71 | 0.00 | — | 5.002 | 1082.65 | — | — |
| 3 | new-no-presentation | 290.60 | -7.07 | — | 4.802 | 796.21 | — | — |
| 3 | capture-zero-viewers | 252.72 | -19.19 | 20.01 | 4.933 | 604.88 | — | — |
| 3 | one-consumer | 173.64 | -44.47 | 118.77 | 4.998 | 1994.26 | 983.28 | 96 |

### final-code-evidence.json

受测 `e09e657f26cf9abf1f510f556f853d24098b7f46`；起止clean且代码摘要相同：True。execution=PASS，correctness=FAIL，performance=FAIL。

| 轮 | 组 | 检测P95 ms | 配对回退% | 模型P95 ms | 实际Hz | 最大迟到ms | 最大live年龄ms | 实际解码/96 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 1 | original | 334.83 | 0.00 | — | 4.723 | 1490.54 | — | — |
| 1 | new-no-presentation | 142.19 | -57.53 | — | 5.001 | 15.93 | — | — |
| 1 | capture-zero-viewers | 420.24 | 25.51 | 26.55 | 4.864 | 2950.52 | — | — |
| 1 | one-consumer | 452.16 | 35.04 | 99.88 | 5.002 | 2046.35 | 662.60 | 96 |
| 1 | two-consumers | 455.64 | 36.08 | 1732.09 | 4.264 | 4471.41 | 2939.29/2677.22 | 90/91 |
| 2 | capture-zero-viewers | 245.87 | -30.30 | 19.06 | 5.002 | 1233.30 | — | — |
| 2 | one-consumer | 165.21 | -53.16 | 100.74 | 4.665 | 1710.95 | 1247.12 | 96 |
| 2 | two-consumers | 176.82 | -49.87 | 248.06 | 5.000 | 59.33 | 505.80/504.89 | 96/96 |
| 2 | original | 352.73 | 0.00 | — | 5.001 | 1508.78 | — | — |
| 2 | new-no-presentation | 145.31 | -58.80 | — | 5.002 | 16.41 | — | — |
| 3 | two-consumers | 381.83 | 26.96 | 182.77 | 4.999 | 1806.45 | 655.16/655.55 | 96/96 |
| 3 | original | 300.75 | 0.00 | — | 5.001 | 1157.58 | — | — |
| 3 | new-no-presentation | 223.78 | -25.59 | — | 5.000 | 104.75 | — | — |
| 3 | capture-zero-viewers | 140.56 | -53.26 | 14.92 | 5.295 | 1069.71 | — | — |
| 3 | one-consumer | 537.84 | 78.84 | 227.31 | 4.345 | 4332.24 | 930.50 | 96 |

最终套第一轮双客户端的11次失败均保留在客户端原始rows中：首件DEADLINE_EXCEEDED，实际工作尚未退出时后续请求RESOURCE_EXHAUSTED；服务端没有提前归还两个asset槽位。两个客户端仍继续消费并恢复，fatal client_errors均为空，但不能因此把90/96、91/96算作完整覆盖。全部服务端结果依旧COMMITTED；客户端本件UNAVAILABLE，下一件不会回退旧OK。该故障说明正常负载下限时完整率仍未达标，不把它从P95/覆盖率判定中删除。表中模型P95计算实际完整件，正确性门禁同时要求全部96件完整；已有缺失的组无论P95怎样均FAIL。

5Hz是绝对计划触发率；串行Runner迟到后追赶可使部分窗口计算Hz大于5，不能解释为稳定余量。所有scheduled/start/end、warmup、latency、client rows、copy/queue/encode/adopt/transfer/decode、CPU/RSS/句柄/线程样本在JSON。原负载/原目标未放宽。脚本另有4.95Hz和最多一周期迟到的诊断门槛；它们不代替按原5Hz负载审查，也不能覆盖200ms/5%/500ms门槛失败。不存在用低吞吐删除分母后的通过结论。

统一账本的窗口结束前稳定值为35,782,656字节展示内存、199,454,432字节缓存，32份metadata/32资产；每窗口72次淘汰，暂存已归还而缓存保留，证明没有逐件提前删除。第一套峰值约70.85MiB，双Job故障峰值约110.57MiB，均在原额度内。每个窗口真实停止后memory/staging/cache/lease全零。各窗口RSS/句柄并非全部回到开始值，跨窗口句柄先上升后回落；只证明了账本、实体文件与工作单元断言，未证明整个进程所有库资源的长稳态。

## 最终分项验收与停止

| P0项目 | 状态 | 结论 |
| --- | --- | --- |
| 环境、基线、HEAD/dirty/源码摘要、原始证据 | PASS | 两套三轮均绑定干净受测提交，旧失败保留 |
| 真实Runner根/循环/重复子流程、冻结、异常封闭 | PASS | 既有契约专项继续回归，新增长驻链路 |
| R09顺序、重复消息、跨scope/Job | PASS | 六种排列×三种终态及真实网络18组；主计划§7.1/B14 |
| R10真实spawn回收、强杀/IPC/退出 | PASS | STARTED后重复阻塞；主计划§8.2/B15 |
| R11临时debug计数/文件隔离 | PASS | release=100不变，debug不覆盖release；主计划§5.3/B16 |
| C1—C4与Supervisor强杀收尾 | PASS | loopback真网络、worker spawn、模拟帧源；控制受理与终态分列 |
| 长驻异步带图链路机制 | PASS | 同一实验Runner→冻结→spawn导出→封闭→接管→两个真实网络解码器 |
| 最终三轮正常负载端到端完整率 | FAIL | 第一轮90/96、91/96，后两轮96/96；故障恢复不能代替完整率 |
| 原5Hz负载与200ms/5%/500ms目标 | FAIL | 完整原始数据见上表，不能用首套成功完整率替代最终套 |
| 统一账本、两Job额度、缓存/租约/读引用寿命 | PASS | 不重建Store的120件周转及运行中故障/取消断言 |
| 进程总RSS/句柄长期平台化验收 | NOT_RUN | 已测走势，但尚无充分稳定窗口与完整稳态断言 |
| 旧上传/读回/取消/超限与长流异常清理 | PASS | 15类故障/兼容场景，未结束工作不提前释放 |
| 旧预览来源失败修复回归 | PASS | 小修复独立提交，原断言保持 |
| 完整CI | FAIL | 既有Qt组合原生访问冲突；49独立文件333 passed不替代它 |
| Qt可见率、冻结包、目标工控机/真实设备 | NOT_RUN | 后续阶段，未把它们计作P0完成 |

**P0退出：FAIL / 未满足。** 本轮代码和风险实验已实施，但正常负载下完整率、性能和资源稳态尚未收口；保留Qt基线阻塞。下一轮仍只能针对上述P0阻塞补证/修复，不能自动进入P1。

测量后只增加`pipeline_faults.py`里的Pin/Unpin/过期真实RPC断言，未再改变benchmark、消费者、Pipeline、导出器、RPC服务或生产代码；最后39项专项和四个风险场景已覆盖该差异。精确受测/最终源码SHA256差异见[manifest](../evidence/p0-runtime-pages/round2/manifest.json)，全改动路径见[文件清单](../evidence/p0-runtime-pages/round2/changed-source-files.txt)。原始输出目录使用局部`-text`属性保留字节/行尾，日志SHA256与Git暂存blob逐项核对。
