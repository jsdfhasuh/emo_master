# 2026-10-22 设备易用性审核修复证据

对应[本轮报告](../../testing/2026-10-22-device-usability-review-fixes.md)。
审查基线 `eefb930481eda864cf03b0725407192c41eff17a`；
固定被测源码 `c41bf6f50e405f1f2c8a286372416d28d1b7cee7`。

## 目录与保留顺序

| 目录 | 实际用途与结果 |
| --- | --- |
| `first-failure/` | 产品未修改时首次补入测试：17 failed、3 passed、2 teardown errors。包含夹具错误，不将失败数等同缺陷数。 |
| `first-counterexamples-corrected-fixtures/` | 只修测试的 QSpinBox API、元数据声明及原错误码期望，补 finally 清理；产品仍为审查基线：13 failed、7 passed。 |
| `fixed-targeted-1/` | 首次产品修复后 20 passed，但原始 stderr 暴露通道行尚未填完整时的 Qt 信号读值问题；不是最终验证。 |
| `fixed-targeted-2/` | 通道表只在整行就绪后通知外层；20 passed，原始输出无上述异常。 |
| `fixed-targeted-3/` | 加入合法禁用空通道的正向入口验证，1 failed、20 passed；失败为夹具在页面初始化后才安装连接器，未重新计算按钮支持状态。 |
| `fixed-targeted-4/` | 修正该夹具的连接器初始化，增加当前候选失败回执对照；22 passed。 |
| `final-source/` | 独立 `git archive` 固定源码，完整 CHAIN、REGRESSION、Ruff、mypy、协议和差异检查；以其 `validation.json`、XML 与原始日志为最终依据。 |

每批 `validation.json` 保存实际 argv、cwd、退出码、环境、被测 SHA／tree、
导入位置及受改文件 blob。前六批明确标记 `workingTree=true`，不能冒充纯提交快照。
首轮失败中的 fixture API／元数据不匹配／错误码断言和两个收尾错误已在后续记录明确修正；
不删除或覆盖首次输出。原错误码 `E_PARAM_INVALID` 与 `E_DEVICE_BINDING` 按来源保留。

`preflight.json` 保存最初两项用户文档的校验值；收尾新出现的并行测试修改独立记录在
`subsequent-worktree-change.json`，不纳入本轮交付或快照结果。
`delivery-candidate.json` 区分已提交源码比较和保留并行修改的工作区比较。

`artifacts.json` 只列本批实际保留产物；`selector-job/` 保存正式 spawned Job、
冻结原始绑定与驱动 selectors，`usability-chain/` 保存 C1—C6 的临时工程、包和 Qt 截图。
旧 runner 继续保留 A1—A6、TCP 字节和 Dispatch 身份原始产物。
全部工程、驱动、IP 均为临时／模拟／localhost，没有连接现场设备。

## 重跑

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$env:PYTHONIOENCODING = 'utf-8'
& 'C:/Users/jsdfhasuh/.conda/envs/emo_master/python.exe' `
  docs/evidence/2026-10-22-device-usability-review-fixes/run_validation.py `
  --repo . --source-sha c41bf6f50e405f1f2c8a286372416d28d1b7cee7 `
  --output-dir <新的独立目录>
```

入口直接复用 `2026-10-21-tcp-device-final-closure/run_validation.py` 的快照、测试、
命令记录与产物保留逻辑，仅扩展既有易用性集合和本轮定位测试。
不覆写旧脚本／历史证据；输出目录已存在时拒绝覆盖。
仅沿用既有两个旧迁移参数化分支排除，没有新增 skip／deselect。
`SHA256SUMS.txt` 覆盖本目录除自身外的保留文件；提交前核对实际 index blob 字节，
`.gitattributes` 保持原始日志、XML、JSON 和二进制包的字节不变。

原生 Qt 自动化不等于人工完整 UI、冻结 EXE 或硬件验收；
本轮没有重新执行历史媒体 181 项，也不把它与历史设备 410 项相加。
