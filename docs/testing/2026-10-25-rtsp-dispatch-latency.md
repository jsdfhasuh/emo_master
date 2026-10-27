# RTSP 取图到检测投递延迟：实机定位与改进

日期：2026-10-25。源码 HEAD：`9265547dbe61bc57542d3cf0ea143fcf455600fb`。
验证基于该提交上的当前未提交工作区；保留本轮之前的设备、队列、UI 和容量报错修复。
没有提交、推送、打包或部署。

## 授权与隔离

用户明确授权连接自己的设备，并说明已关闭正在运行的工程。本轮通过源码 RuntimeService
加载其已保存的在线工程副本，实际启动生产者和检测消费者进程，读取真实 RTSP 相机。
不是模拟设备，也不是仅阅读日志；没有把它描述为人工 Designer UI 验收。

- 原工程为完整 2.7；副本经过 ProjectDocument 校验、saveProject 保存重读、Runtime
  LoadProject 和 ValidateProject。模型及图片资源核对 SHA-256 和大小后复制。
- 只将副本中的业务归档输出路径改到隔离目录。相机连接、分辨率、翻转、YOLO 参数、
  Dispatch 容量和 wait 策略保持不变；没有修改相机/网络设置，没有访问 PLC。
- 数据库、Job workspace、日志和输出均独立。原在线工程以及三项来源资源在最终复测前后
  校验一致。工程副本含现场连接信息，只保留在 Git 忽略的本机验证目录，不进入提交。
- 每轮到达观察窗口后显式 StopJob，再等待资源退休并关闭 Runtime。ABORTED/E_CANCELLED
  是这次主动停止的结果，不能记作检测业务失败，也不能记作无限循环自然完成。

本机证据根目录：
`D:/jsdfhasuh/documents/my_project/emo_master/manual_test_workspace/rtsp-latency-20261025/`。
隔离探针为 `manual_test_workspace/rtsp_latency_probe.py`，没有连接信息硬编码；读取本机已有快照。

## 计时口径

在真实 Worker 内用 perf_counter 计时，记录取图算子返回到 Dispatch 节点返回的间隔，
包括翻转、调试采集、事件投递和入队确认。它不是单独的队列 API 耗时，也不是检测完成延迟。
Dispatch 的返回点包含 accepted 通知，因此报告中的 readyToAdmissionMs 应按上述口径理解。
原截图中的 receivedAgeMs 是选取缓存帧时的帧龄，不是整条采集到检测链路的耗时。

不使用 Runtime 消费事件时生成的 timestampMs 推算 Worker 精确耗时；事件通道有积压时，
它包含消费延迟。相机序号和 received timestamp 在本轮样本中对应约 25 fps 的接收速率。

每轮观察请求为 25 秒，从 StartJob 返回后开始，包含首次打开流；停机收尾另计。
下面是同设备、同工程参数的顺序短测，不是严格控制全部机器负载的长期节拍保证。

| 轮次 | 调试策略 | 投递数 / 已完成数 | 取图返回至 Dispatch 返回均值 | 中位数 |
| --- | --- | --- | --- | --- |
| baseline-all | ALL + 实时检查 + 页面采集 | 12 / 11 | 1670.465 ms | 1617.486 ms |
| baseline-none，仅定位对照 | NONE + 页面采集 | 98 / 96 | 177.784 ms | 106.471 ms |
| optimized-inline | ALL + 实时检查 + 页面采集 | 26 / 26 | 633.471 ms | 574.435 ms |
| final-all，最终源码复测 | ALL + 实时检查 + 页面采集 | 23 / 23 | 731.346 ms | 732.876 ms |

最终轮相对首轮同口径均值降低约 **56.2%**，投递数约为 **1.92 倍**。驱动接收到该帧
至 Dispatch 返回的均值则由 1711.728 ms 降到 777.305 ms。
最终轮样本最大值仍有 **1966.336 ms**，不能声称所有帧已消除秒级峰值。
NONE 是诊断对照，不是最终修复；最终保持 ALL，未降低业务图像分辨率或识别参数。

## 根因与实现

1. 临时调试快照对每个图像、伴随 JSON 和索引都执行 fsync。额外 15 秒计时轮
   baseline-all-detail 中，生产者单次原子写平均 300.373 ms，其中 fsync 平均
   277.358 ms；真实入队节点平均约 9 ms。
2. Windows 临时图片删除也很慢。optimized-profile 中，生产者实时检查接管路径约
   11.282 秒累计时间里，nt.unlink 占 8.993 秒；消费者还生成了未绑定检查会话的图片，
   随后被立即丢弃。这些操作阻塞事件通道，进一步反压 Worker。
3. 最终使用既有有界 Worker 通道传递不可变 PNG bytes，每个 inspection.snapshot
   只带一张最多 4 MiB 的显示图。保留原有缩放/编码和节点执行身份，不混入普通 JSON
   日志事件，不新增第二条事件队列或后台清理框架。
4. RunInspectionStore 在同一执行对象内持有这些字节，沿用最近两次执行、Job 64 MiB、
   会话 128 MiB、项目/全局额度以及读取租约。内存图片和已有文件图片合并计账；被淘汰
   但仍有读取者的图片继续计账，直到读取结束。既有文件传递入口仍保留。
5. Job 内临时旧快照继续采用已有有界、可合并的异步写入，只取消其逐文件 fsync；
   文件关闭和原子替换仍保留。正式缓存索引提交仍 fsync，业务归档、工程配置和正式任务
   状态持久化规则没有降低。
6. Dispatch 消费者不再继承仅属于主 Job 的 inspectionEnabled。消费者原有 ALL 快照、
   检测和页面采集保留；本轮没有宣称增加了消费者独立节点检查会话功能。

最终轮实时检查接管平均 **4.158 ms**，中位数 3.837 ms。
进程队列仍有 64 项边界，单图最多 4 MiB，排队图像字节的理论上界约 256 MiB/Job；
这是队列 payload 上界，不是进程总内存上界。保留区另按原有额度限制，未添加无界图像历史。

## 保留的中间证据

- 仅取消 fsync 的 optimized-all 均值仍为 1525.785 ms，不能算完整解决。
- 文件重命名接管的 optimized-handoff 为 1148.040 ms；尝试将删除移到维护线程的
  optimized-retirement 为 1142.013 ms。收益不足，最终没有保留这两项中间实现或新增
  的异步清理状态；这些轮次的报告仍保留。
- 首次内存传递回归为 **5 failed, 37 passed**，位于 pytest-3468：Worker 的普通
  JSON 清洗把 bytes 转成了字符串，实际 spawn/RPC 看图出现 NO_CURRENT_SNAPSHOT。
  已在私有 inspection IPC 入口保留 bytes，并补专门回归；没有修改原来的看图断言来
  绕过失败。修复后的原 RPC/终态组 **13 passed**，之后纳入最终完整相关回归。
- 文件删除小探针只使用自己新建的隔离文件；截断、重命名和 Windows short-lived
  标记没有形成足够的稳定收益，未加入产品实现。

## 最终验证

Python 3.10.21、PySide2 5.15.2.1；Qt 测试使用 offscreen。

- 20 个相关测试文件：**251 passed in 123.71s**。覆盖快照、原子提交、身份/额度/租约、
  真实 spawn/gRPC 读图、失败/正常取消/强停、事件批处理屏障、Dispatch 及 Qt 看图组件。
- Detection Archive 算子单独回归：**15 passed in 0.98s**。合计 **266 项通过**，
  不是全仓库测试。结果 XML 为证据根目录下 regression-final.xml 和 archive-regression.xml。
- Ruff、本轮文件 git diff --check 通过。没有以未执行的全仓类型检查或完整测试代替验收。
- final-all Job：`cdafa382-b499-45a6-a62f-d933fc9aa2df`；23 次投递、23 次完成，
  观察时队列等待 0、处理中 0、失败/拒绝 0。事件只有主动停止相关告警，没有业务错误。
- source/orient 的 image 和 validMask 可读，执行身份吻合；原图 2560x1440、显示图
  1920x1080，和原规则一致。检查存储约 15,999,538 字节，readers/pendingFiles/retiredJobs
  均为 0，未发生额度拒收。
- 检测页面结果为 COMPLETE，包含计数、存在标记、原图和叠加图，两路图像源均存在。
- graceful StopJob 成功、retired=true；Worker PID 43192/71188 在结束后均查无进程。
  原在线工程、模型及来源样例资源校验一致，没有占用设备等待用户手动清理。

## 剩余边界

事件数据库仍是可测瓶颈。optimized-inline-profile 的 appendJobEvents 中，每次 SQLite
commit 平均约 100 至 117 ms；最终轮普通事件批处理含锁等待平均 164.647 ms。
本轮没有改 SQLite 的持久化等级或重构日志系统，因此没有承诺 25 fps 的端到端检测吞吐，
也没有承诺每帧低于 100 ms。PNG 显示图编码和业务翻转也仍有正常开销。

没有做长期稳定性、识别准确率、人工 Designer UI、冻结 EXE 或部署验收。
通过当前源码重新启动 Designer/Runtime 后使用这些改动；已打包 EXE 需要另行重新构建。
