# 捕获通道归还边界修复

本轮基于 `17b7bc76d02d1b7d2c2ffe82aef2b229537aa251`，保留它相对 dot 验收版本
`4bcce9a9e4c158dcd618b04239bb9d8397ba9f73` 的后续修改。仅修复已确认的
“PNG 已完成、资产接管仍占着 raw lane”问题。捕获不完整整体仍未关闭。

## 原始证据与因果范围

使用历史本地 `r36-abba/trial-2-credit-on` 和 dot 保存的精确 SHA Windows
诊断运行 `38223062630`。`replay_credit_evidence.py` 可从原始 producer/parent
记录重算；输出保留输入 SHA256、完整调用身份和阶段时间。相对时间以被拒绝申请为零点。

| 证据 | 被拒绝序号 | 前一个持有者 | 完成回复到达 | 资产接管结束 | raw 额度归还 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 历史本地观察 arm 2 | 75 | 74 | -87.0365ms | +45.5467ms | +47.2858ms |
| Windows CI 观察 arm 1 | 56 | 55 | -9.689ms | +8.533ms | +9.301ms |

两次拒绝均发生在前一个匹配来源的 `asset_adopt` 内，编码回复已成功返回、raw 额度尚未归还。
原 `ExportPool._run` 把 raw 归还放在整个回调结束之后，延长了占用。
可控测试使用实际 spawn 编码进程，在回调入口用事件暂停资产接管；原代码确定性地把
下一张图判为 `BUDGET_EXCEEDED`。首次失败保留在证据包的 `before.log` / `before.xml`。

同一 CI 观察 arm 的另外六次拒绝是 6、11、15、21、24、26，申请时前一任务的完成回复
尚未被父进程接收。现有公共证据不能继续区分子进程编码、文件写入和调度延迟。
本修改不将这六次或 observer-off arm 的拒绝标记为已修复，也不把一次完整覆盖当作历史根因关闭。

## 修改

- `src/emo_master/apps/runtime/presentation/exporter.py`：仅在 taskId 匹配、成功且未超过
  500ms 期限的回复后，先归还 raw lane，再执行资产接管回调。原始图像容量和非阻塞申请不变。
- 串行导出 owner 继续独占当前 staging 文件；旧回调及文件清理结束后才调度后继编码，
  防止新图覆盖旧 PNG。`active` 状态把该所有权与待编码 raw 描述符分开。
- 同 lane 后继任务拥有自己的 `pending` 和信用；旧回调收尾不会移除或重复归还它。
  入队失败也不能把仍执行的回调误标为可释放。失败/超时/未知 taskId/reap 失败继续沿用隔离规则。
- `test_capture_lane_lifetime.py`：实际编码、单/双来源 lane、正常/异常回调、入队故障、
  第三次非阻塞拒绝、两张不同图像逐像素比较、共享内存信用唯一性、真实 owner 退出。
- 同步原 exporter/image 回归及 `docs/runtime-pages-p2-contract.md` 的生命周期描述。

共享内存、导出预留、staging、队列容量和安全期限均不增加。
持续生产快于编码时仍会明确拒绝，不能据此承诺任意负载下逐帧完整。

## 验证

Windows 自动化：首次确定性复现 1 FAIL；初步修复 2 PASS；扩展单/双 lane 及异常路径后
相邻回归 48 PASS。扩大回归为 615 PASS / 3 FAIL / 1 SKIP / 26 subtests；三项失败均为旧测试
要求“callback 后才 release”的旧生命周期断言。更新为 reply→release→callback，并保留/加强
真实资产摘要、两图像素、后继拒绝与信用唯一性验证后，受影响 trace 文件及新增测试 44 PASS。
这些组重叠，不能相加；未宣称最终整组或全量 pytest 重新通过。Ruff、mypy（411 个源码文件）
和 proto drift 均通过。

单独运行原固定 ABBA（offscreen，未与 pytest 并行）：四组各执行 96 次、全部 96 张源图
AVAILABLE，两个消费者各收到并解码 88/88 正式图像；两个观察组均 96/96 acquire 成功，
且各 96 条均验证 reply→release→callback。首次运行未重跑。
端到端结果仍为 INVALID / performance NOT_ASSESSED：arm 0 的消费者 applied 为 87/86，
arm 3 为 87/87；arm 0/2/3 的 Qt committed/painted 有缺号，具体序号见 VALIDATION.json。
这些结果保留，未把图像生成/接收完整等同于实时视图逐帧完整。

验证统计及固定负载结果见同目录 `VALIDATION.json`。首次失败、修复后日志、历史调用关联、
源码身份和测量摘要保存在同目录证据包。pytest 为 Windows offscreen 自动化，
ABBA 单独记录 Qt 平台；不替代 dot 的 Designer/Runtime 人工验收或现场设备验证。

本轮没有修复 Windows SQLite EOF 或 P0 相对性能门槛，也没有合并、发布或部署。
