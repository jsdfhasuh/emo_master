# v5 修订记录与范围核对

日期：2026-11-12。输入：v4 原始 ZIP 与 emo_master_openspec_v4_audit.md。变更 ID 保持 add-independent-project-authoring；本版替代旧版，不并行维护两套需求。

| 审查项 | 修订结果 | 规范与任务落点 |
| --- | --- | --- |
| F1 协议取消 | 以实际协商协议和 SDK 处理单请求取消；草稿不随连接销毁，不撤销已提交操作；阶段 A 固定一个实测组合 | design D8.2；MCP Version-bound protocol behavior / Connection-independent drafts；tasks 1.5、3.4；G6/G7 |
| F2 文件往返 | raw bytes 元数据、修订头、实际大小/摘要、新修订绑定、导出描述、同源受保护下载；提供 HTTP 辅助样例，不假设 MCP 自读本地文件 | file-transfer.md；资源/MCP spec；tasks 2.1–2.4；G4/G7 |
| F3 可定位诊断 | 保留已有 ValidationIssue 字段及已知 resourceId；工具错误与 FAILED/INCOMPLETE 报告分开；增加诊断修复再检查场景 | design D8.1；MCP / 候选 spec；tasks 3.3、3.5；G5/G7 |
| F4 本地与目标检查 | 本地可离线检查并报告范围/缺项，完整预检用指定 Runtime；未完成业务配置不阻止合法草稿保存 | design D7；草稿 Local validation / 候选 Genuine checks；tasks 1.3、3.2；G1/G5 |
| F5 桌面适配时机 | 实际提取与对应桌面适配/局部回归在同一变更完成；4.4 改为遗漏核对 | design D2；tasks 跨阶段约束和 4.4 |
| F6 阶段 D 覆盖 | 复用 fixture 经公开命令验证复杂流程、设备/变量、页面/生产配置代表操作及保存重读；PNG 示例不能单独证明 D 全部完成 | design D9；tasks 4.5；acceptance G2 |

## 仍然不做

不控制或启动 Designer，不整体重构桌面，不迁移旧 Web 产品，不增加生产发布/启停工具，不做多租户/OAuth 或全协议矩阵，不引入支持度评分、任意插件沙箱、独立任务平台或文件管理系统。保留五项能力、四阶段、七组验收和 25 个实现任务；新场景并入现有测试组。

## 证据边界

v5 文档中沿用 v4/再审查的固定源码证据，不声称本轮重新拉取最新仓库或运行实现。协议版本差异经官方文档复核；本轮实际文档检查、工具获取和未执行项记录在 validation.md。PowerShell 只是待实现接口的使用示例，不是已部署服务或运行证据。
