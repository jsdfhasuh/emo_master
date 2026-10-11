# VARIABLE_UNAVAILABLE：一次隔离模型与资源状态补证

## 结论

在精确 `abc7ae0541e997be0bbe3a4794ad604c239de79a`、既有 Python 3.10.22 venv 上，一次有界运行确认了枚举不一致的模型及状态影响：

1. 真实 `ResultCollector.end` 在注入的 global variable `KeyError` 后生成 reasonCode=`VARIABLE_UNAVAILABLE`。
2. 真实 `ClosedSource` 不接受该值，报 `ValidationError / literal_error / reasonCode`。
3. 真实 `ResultStore.close` 在校验 ClosedResult 之前已将 open 项 pop。抛错后该 key 不在 open，history/latest 都没有结果，closedHigh 未更新；cursor 仍是 begin 后的 1。
4. 隔离调用真实 `PresentationService._consume` 与 `_finish`，首异常后 pending 含 seal，scope credit 未归还。再显式调用一次 `_finish`，模拟下一次 monitor/finish 边界：store.close 因 open 缺失返回 False，pending 被删除、retain 执行，但 credit 仍未归还。
5. 独立合法 reason=`INVALID_VALUE` 对照正常生成 INCOMPLETE，删除 pending 并归还 credit。

这确认的是**注入场景下的 DTO 枚举不一致、结果丢失与 credit 未归还路径**，不是历史生产 NG 的唯一根因。历史 NG fixture 的三项来源是 node_output，没有 global_variable/global_counter，原失败也没有保存 sources；不能据本补证回填历史。

## 隔离范围与方法

- 辅助脚本：[variable_unavailable_probe.py](variable_unavailable_probe.py)。脚本 SHA256：`fb2c38366feb564cd2f9b2857bfee9c4ae6d834c84c44b3a2544ab3d587a8080`。
- 原样首运行日志：[variable-unavailable-first-run.log](variable-unavailable-first-run.log)。机器明细含完整异常/traceback：[variable-unavailable-first-run.json](variable-unavailable-first-run.json)。退出码：[variable-unavailable-first-run.exit](variable-unavailable-first-run.exit)。
- 使用真实 collector、DTO、ResultStore 与 PresentationService 方法。注入 reader 仅抛出明确 KeyError；credit 使用真实 `threading.BoundedSemaphore(1)` 加本地审计计数；AssetSpy 只记录 retain，不写资产。
- `PresentationService` 由 `object.__new__` 分配，**没有调用构造函数**；没有 Runtime owner、后台 monitor/reader、worker、网络、生产数据库、图像编码或设备。线程前后均只有 MainThread。
- capture plan 是最小手工内存 fixture，没有声称通过完整工程准入或真实 UI 操作。
- 首次异常完整保留。后一次 `_finish` 是同一对象的下一边界状态观察，不是重跑首失败到通过；另一个合法 reason 是独立对照。
- 外层 deadline 固定 30s，实测工具进程约 0.31s，exit 0；7 个 observation_checks 为 true。这个 0 表示脚本完整观察到预设缺口和对照，不表示产品缺陷通过验收。

## 精确代码位置

- 生成不在模型枚举内的值：[collector.py:200–214](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/collector.py#L200-L214)
- 合法 reasonCode 枚举：[results.py:54–62](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/core/presentation/results.py#L54-L62)
- pop 在 model_validate_json 之前：[store.py:88–104](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/store.py#L88-L104)
- 仅 close 返回 true 时归还 credit，之后删除 pending：[service.py:296–315](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/service.py#L296-L315)

## 已观察与未验证

| 时点 | store.open | history/latest | pending | credit available / released |
|---|---:|---|---|---|
| collector 发出 open/seal，父消费 open 后 | 1 | 空 | 1 | 0 / 0 |
| 父消费 seal，首次 DTO 校验抛错后 | 0 | 空 | 1，含 seal | 0 / 0 |
| 同一对象下一次 `_finish` 返回后 | 0 | 空 | 0 | 0 / 0 |
| 独立合法 reason 对照结束 | 0 | 一项 INCOMPLETE | 0 | 1 / 1 |

没有启动实际 reader/monitor，也没有观察整个 Job 的长期运行、八份 credit 耗尽或终止资源回收。不能把单次模拟中的一个未归还 credit 扩大成已经测得生产长期泄漏。源码上，真实 `_read` 捕获 ValueError（包括本次 ValidationError）会追加 presentationErrors，调用 `_fence(job, UNKNOWN)` 后返回；该 fence 忽略已含 seal 的 pending。`_monitor` 调用 `_finish` 未在该循环捕获异常。以上线程退出/继续的后果、其他 pending 并发及最终关闭行为，本次均没有启动真实线程验证，不能写成整 Runtime 实测结论。参见 [service.py:226–243](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/service.py#L226-L243)、[322–354](https://github.com/jsdfhasuh/emo_master/blob/abc7ae0541e997be0bbe3a4794ad604c239de79a/src/emo_master/apps/runtime/presentation/service.py#L322-L354)。

## 最小修复建议，未实施

- 统一 collector 输出 reasonCode 与 DTO 枚举，明确变量不可用是否独立业务码或复用已有合法码。
- 在修改共享 open 所有权之前完成可失败的结果构造/校验，或采用能明确恢复/关闭/归还 credit 的异常原子性策略；不能简单无条件归还 credit，造成重复释放或未知 owner 被提前复用。
- 为非法 source、失败闭合、下一次 finalize、合法 unavailable 对照分别加边界测试，保留首异常与 source 原因。此处仅给建议，没有改产品/测试代码或原断言。
