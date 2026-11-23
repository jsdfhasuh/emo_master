# 完整迁移验收与停止条件

## 1. 成功的定义

最终成功必须同时满足：12 项真实产品计算迁往公共标准 GitHub 托管 Runner、产品原覆盖保留、正确提交状态可阻断、证据和包未公开明文、私有取源与发布可用、旧重任务停止双跑。产品仍有短 dispatcher 的私有用量，明确单列。

计划严格校验、合成项目通过、仓库创建和 Token 下载成功都只是阶段结果。

## 2. 必要验证

| 范围 | 验证与判定 |
|---|---|
| 无人值守准备 | dot 在其实际环境拥有开发／管理登录；公共 CI 和 Private probe 已初始化；Secret 名称／位置齐全，探针实际验证权限；普通测试和本轮 dry-run 无新增逐次人工审批；age 私钥有持久备份 |
| 来源与权限 | 固定产品仓库／SHA checkout 成功；错误 SHA、错误请求 Artifact 或不可信公共分支被拒绝；Token 不出现在日志与 Git 凭据配置 |
| 触发等价 | 原 push / PR / 手动 / tag 与路径路由；手动 stop 不自动扩大，Web 手动与自动差异保留 |
| 主 CI | 同固定产品版本 Linux/Windows 四分片；collected 相等、selected 互斥／并集完整、选中项开始执行；跳过不算通过 |
| Web / Display | 原两平台 Web、owning Runtime 和 Display 契约有实际证据；无设备不伪称硬件验收 |
| 六项诊断 | 每项至少真实跑一次，保留其通过／失败／inconclusive 判定；“迁移执行完成”与“诊断结论通过”分开 |
| Runtime / Designer 包 | 原 source／frozen／archive／native／provenance 验证保留；同 job build+verify 后加密，实际时长符合托管限制 |
| 发布 | 两个目标均验证后，干净 publisher 完成固定 Private probe 的 draft Release 产品包上传／读取；probe Git ref 使用该仓库自身提交，证据单独保存产品版本／SHA；目标、tag 和 digest 一致，小文件连通不代替此项 |
| 输出保护 | 正常与失败样本日志无产品原文／凭据；公开 Artifact 仅密文，可由指定私钥恢复原始报告；加密失败不退明文；不把 marker 检查当恶意代码无外传证明 |
| 状态门禁 | 成功能放行，失败能阻断；新 head／base 不继承旧结果；取消无回报保持 pending 阻断；重跑使用正确 attempt |
| 切换 | required context 无旧 Check Run 同名冲突；Private 账户保护能力真实可用；原 12 重任务停用，仅短 dispatcher 活动 |
| Private 复验 | 真正 Private 后 PR / push / 手动 / 标签控制至少覆盖关键链路，发布使用受控 dry-run，不伪造正式发布 |
| 成本 | 记录公共计算、私有控制、Artifact 和账户费用；无账单数据标明，不能给出虚构百分比 |

## 3. 证据格式

每个 suite 至少记录：

- 产品仓库固定 SHA；PR 的 head / base / merge / check SHA；实际 execution SHA。
- 产品 dispatcher run / request Artifact、公有 CI workflow SHA、packager SHA（适用时）。
- 公共 run ID、attempt、job ID、OS、开始／结束时间、实际结论。
- 加密 Artifact ID、密文 digest、key ID、原始报告位置和读取命令。
- 原有路径对照、跳过项与原因、缺失证据、最终产品 status context / URL。
- 被测工作区是否有未提交修改；若有，必须记录范围，不能声称干净 SHA 已通过。

## 4. 发现问题时如何处理

- 可复现的语义变化、公开明文、错误 SHA 回写、遗漏原测试、发布权限进构建 job：本轮必修，修复相应路径并复验。
- Token 缺失／到期、仓库设置权限不足、Private 门禁方案不支持：记录具体配置阻断与已完成内容，准备最小人工动作；不停止其余可完成工作。
- 诊断自身 inconclusive、原有测试跳过或已有非阻断项：保留证据和原语义，不为追求全绿修改结果。
- 整体取消没有报告上传：标明缺证据并重跑，不假称日志完整。
- 同 VM 恶意代码可外传源码：列为明确的信任模型限制，不改写成已隔离，也不据此把交付终点退回 synthetic-only。
- 暂时回滚时恢复原 GitHub 托管 CI，说明恢复的私有用量；没有自托管或重新公开仓库的隐含授权。

完成直接相关验证后进入交付，不额外要求极端故障排列、所有线程交错、硬件验收或性能专项。
