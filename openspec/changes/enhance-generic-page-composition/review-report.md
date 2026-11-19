# V1.3 实现审核与收尾范围

日期：2026-11-18（北京时间）。固定审核源码：ce8f84caf4d0fd779e579b23ff82fa4e80de978d。执行者：ChatGPT Web。源码检出在审核和直接复现期间保持干净；临时探针在仓库外。本文记录已完成的审核，不表示 V1.3 修复已实施。

## 1. 总体结论

通用控件、正式双页工程、实际写图/写库与稳定键回看已落地。V1.3 只关闭三个正常使用缺陷、两处目标表现差距和当前 CI 失败。原 V1.2 对旧工程兼容、故障矩阵和重复验收的删减继续有效。

原 V1.2 tasks 中“全部完成”和 acceptance 中“待实现”的冲突由本版统一为：已有实现与证据，当前六项收尾待完成。原历史记录通过固定提交保留，不修改其所属 SHA 或验证环境。

## 2. 已确认问题

| 编号 | 正常触发 | 实际证据 | 最小职责 |
| --- | --- | --- | --- |
| F1 | Qt 历史字段从 — 更新为时间或多行日志 | 时间所需宽159但卡片仍13；四行日志所需高72但卡片仍18；内容更新后横排对齐130变1。提交 Windows 历史截图同样只显示时间开头20 | WrappedText 的测量失效通知与对齐保持 |
| F2 | Web 新建/重新绑定 integer 指示器 | 当前 compatible 返回“判定指示不支持 integer 输出”；boolean 允许 | 正式绑定类型规则同步 |
| F3 | Web 复制含已声明 Runtime 历史表的页后发布 | 原 build_package 成功，真实 copyPage 后报“当前 Runtime 历史来源已变化”，来源实际未变 | 将 UI 实例身份与既有 Runtime 声明身份贯通分开 |

F3 仅指独立 Web 编辑器的 Runtime 历史引用；没有证据表明原生 Designer 的来源拥有型复制同样失败。新 historySource 是直接来源引用，不是生产输出的 exposed-variable 层。

## 3. 视觉完成度差距

- Web 主体前仍有 Runtime/包/查看运行工具栏和另一整条工作流/控制工具栏；收起诊断没有完成紧凑宿主页头。
- indicator 的 soft 只作用于值区域；row 需要按状态覆盖整行。
- 高内容允许正常滚动。1366×768 第一屏容纳不下全部详情本身不是缺陷；动态文本内部裁切才是 F1。
- 原目标图约束结构、层级与信息密度，不要求逐像素相同，也不以脚垫文字或图像素材作为通用控件类型。

## 4. 当前 CI 的事实

| 固定 run | 已观测结果 | 解释边界 |
| --- | --- | --- |
| [Source CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44271356400) | 四个 Linux/Windows source job 均在 appearance.py:13 的 mypy 类型错误停止 | 后续源码测试未完成；不是所有源码测试均失败 |
| [Web CI](https://github.com/jsdfhasuh/emo_master/actions/runs/44271357295) | Ubuntu/Windows 的 asset_retirement_browser 在等待 retained scope 的显示结果时超时 | 后续若干浏览器/分发步骤 skipped，不得算通过 |
| 同一 Web run 的 Owning Runtime integration | 5 项 Runtime 测试通过；product_runtime_e2e 在 /api/me 返回401时结束 | 尚未证明产品登录功能损坏，需核对真实会话与等待条件 |

401 或 locator 超时可能来自测试与新页面结构不同步，也可能暴露产品问题。先依据当前正式路径定位；保留身份、目标、终态和图像归属断言，不改成无鉴权模式、全局延长等待或删除有效测试。

## 5. 已完成验证可继续引用

- Python 3.10.22 / PySide2 5.15.2.1 / Linux offscreen：历史选择、SQLite 组件、Runtime 历史 RPC 三文件合计29项通过。
- Annotate 当前用例5项通过。
- 当前提交实际运行 check_archive.py：软件 Runtime 六次执行，ImageSaver/SQLiteWriter 写入；主图756×600、局部图142×96；生产/历史主图像素一致；恢复第3条后下一条是2，最新是6；过滤结果[5,4]，count=2。
- Web TypeScript/Vite 构建和 protobuf 生成一致性检查通过。
- 上述 F1/F2/F3 已经直接函数或 Qt 探针复现。
- 原交付 Windows/浏览器截图与日志来自提交前本地工作区，按原说明保留；审核环境未重做 Windows 人工操作。审核环境的浏览器下载失败，完整浏览器端到端依据上述 CI 日志，未记本地通过。

证据索引见 review-evidence.json。修复时根据 acceptance 的影响表决定重跑范围，不把所有旧通过项改写成新 SHA 的执行。

## 6. 本补丁做了什么

本次只修改 OpenSpec 和交接资料，未改平台实现、未推送、未操作现场设备。具体待办集中在 tasks.md，正式语义集中在 contracts/specs；不新建另一项平台工程。最终文档结构、链接、官方 strict 与补丁应用检查写入 validation-report.md。
