## 1. 共享配置与作者入口

- [x] 1.1 扩展 PageShell、HostControl、状态与历史字段，并在 Qt/Web 属性入口可编辑、保留和重置。
- [x] 1.2 用现有生成器产出 SDK 1.8.0，更新 Schema/TS、wheel 和消费者；验证正常保存/发布往返。

## 2. Qt 与 Web 表现

- [x] 2.1 运行页头和作者预览应用 logo/页头/宿主按钮配置，保留原命令和真实状态；运行选择标签可区分同名任务。
- [x] 2.2 标题、圆点、值与底色跟随同次状态，并在空/未映射结果时清除旧表现。
- [x] 2.3 独立隐藏历史筛选/排序/分页控制，保持外部上一条、下一条和回到最新操作及同记录联动。

## 3. 通用构线与实例交付

- [x] 3.1 实现两点构线算子、注册和使用文档，复用 Annotate 虚线。
- [x] 3.2 同一可编辑双页示例展示五项配置，生成发布包；连线来自本次所选目标中心。
- [x] 3.3 完成本环境可执行的正常路径验证、Qt 实际截图及验收记录，注明源 SHA 与未提交工作区范围。
- [x] 3.4 Windows Edge 连接真实软件 Runtime，完成四次生产归档、双向跨页回看、切页恢复、返回最新及单独一次确认停止。已逐张检查两种分辨率的生产页和历史页截图。
- [x] 3.5 CI 集成收尾。V1.4 最终 Source CI 对应 `568a944a`，首次完整运行通过；Web CI 对应 `912af529`，三个任务均通过。已核对 Linux/Windows 四份 pytest 报告，以及同一 Runtime 两次启动、第二个 Job 图片归属与两次停止的浏览器记录。

3.4 对应代码 `53b38e1f`。初版环境的 IPC 阻断已作为历史证据保留。实际操作、截图、软件版本与验证边界见[本轮验收记录](../../../docs/testing/2026-11-19-page-configuration/README.md)。

早期 `912af529` 的 Windows Runtime 分片在同一 SHA、同一 500 ms 预算下重跑通过，首次多图超时记录继续保留。后续两处测试时序修正及最终源码复验结果见 [`1e47522c` 的验收记录第 5 节](https://github.com/jsdfhasuh/emo_master/blob/1e47522ccb8451f1665852caa5ae6ff333b340bb/docs/testing/2026-11-19-page-configuration/README.md#5-ci-集成收尾)。[最终 Source CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44533294908/attempts/1) · [Web CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44509447970)
