# R3 计划包检查记录

2026-11-27，Codex。检查对象为 `runtime-platform-overall-v1` 的 R3 文档、三份增量规格及交付 ZIP。

## 版本与范围

- 输入 ZIP 的 SHA-256 为 `d86c4d8dba8dd551d50e20cfce1dee2a9cb956fb7040b68937271270c4ad973b`。本版继续使用同一 Change ID、目录结构和 P0–P9 编号。
- 计划基线为 `48b30191884ad5ef5d96e8d26591104d16d54a58`。本轮通过 `git ls-remote origin refs/heads/main` 核实远端 main 指向该提交。
- 本地工作区 HEAD 为 `78a547270324553b8e88dea5a05258e562fcfb08`，修订开始时工作区干净。计划在独立临时目录中编辑。验证环境为 Windows、OpenSpec CLI 2.0.0。
- 上轮用于定位缺口的内存反例和 Qt 离屏走查见[修订记录](review-notes.md)。R3 产品任务与验收状态留待实施后更新。

## OpenSpec strict

从固定提交导出 `openspec/config.yaml` 和正式 `openspec/specs/`，在临时工作区的 `openspec/changes/` 下放入 R3 Change，执行以下命令。

```powershell
$env:OPENSPEC_TELEMETRY = '0'
openspec validate runtime-platform-overall-v1 --strict --no-interactive --json
```

首次执行返回 2 条正文超过 500 字符的警告，涉及日志宿主接入和 Job 导出范围。已精简 Requirement 正文并将示例细节放入 Scenario。修订后退出码为 0，1 个 Change 通过，issues 为空。[R3 校验输出](verification/openspec-validation-r3.json)

另一个临时工作区从成品 ZIP 解压同一 Change，以相同基线和命令复验。R2 用 CLI 2.2.0 执行的历史记录单列保存。[R2 校验输出](verification/openspec-validation.json)

## 成品复核

- 对照 R2 清点文件，核对参考原型、两张截图、参考说明以及 R2 校验 JSON 的原始字节。新增一份 R3 校验 JSON。
- 检查文档 UTF-8、JSON、Markdown 包内链接、引用的任务编号。核对 82 个任务编号及顺序、P1–P9 产品任务的未勾选状态、正式日志来源 Requirement 和其中原有的三个 Scenario。
- 复核五项行为在规格、任务、验收中的对应关系，特别区分“返回 A 的第二页”和“仍查看 B 时停止 A”两次操作的次序。
- 检查 ZIP CRC，解压并逐文件比对打包输入，完成上述 strict 复验后将同一 ZIP 交付，按 SHA-256 核对交付副本。

本次交付为计划包。产品代码、回归测试、原生桌面完整主链、冻结产物及现场设备的结果由实施阶段记录。
