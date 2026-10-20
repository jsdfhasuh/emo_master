# Codex 证据交接：b35e2af Debian NG 持续更新与资源退休

请先只读审阅本目录6页中文PDF/DOCX和CASE-MATRIX.json。校验SHA256SUMS.txt，执行 `python reassemble_evidence.py` 恢复证据ZIP，按ORIGINAL-ARTIFACTS.json确认原字节/SHA后解压，再验证包内SHA256SUMS.txt。先理解事实与观测边界，不预设还有产品故障。如果没有新的失败证据，可以明确结论为无需产品修改；新增测试、源码修改或扩大范围须另按用户任务授权执行。

## 固定版本与结论

仓库jsdfhasuh/emo_master，既有分支agent/runtime-workflow-architecture-v1。本轮唯一实测SHA是 b35e2af8641683f366393e02388a0ce5bcb7680d。包含此目录的新提交仅归档文档，不能称其新HEAD也实测通过。先记录当前HEAD及与固定版本的差异。

本轮限定PASS：55项专项自动测试仅运行一次；真实Designer NG/计数结果冒烟；真实Runtime连续NG同调用图像/结果身份核对、Stop后历史展示、restart新Job及运行中原生标题栏X关闭；独立原始记录与资源退休审计。没有新的本轮产品FAIL，不要虚构待修故障。历史capture根因仍OPEN，旧验收FAIL也不被此次限定PASS改写。未验全产品、性能窗口、最大容量、生产设备或Windows。

## 必读材料与核对顺序

1. report/ACCEPTANCE-REPORT.pdf、report/UI-OPERATIONS.md与CASE-MATRIX.json：真实桌面点击、观察、辅助取证和自动故障注入分账。55自动用例/injection不是全部原生GUI场景。保持首次执行日志、退出码、JUnit及源码固定/clean身份，不重跑择优取绿。
2. evidence/native-ng/observer.jsonl、runtime-native/logs、evidence/ng-independent-audit.json、scripts/audit_ng_evidence.py：340个观察到的唯一raw任务/proof按原身份配对，是受观察窗口内记录，不是340张PNG或系统全部历史。按Runtime/Job/调用/owner/ref/task/proof实际字段关联，不能只凭数量相等宣布身份一致。原始proof配对与actual owner对象退出证据须一并核对。
3. 40张派生PNG及evidence/expected-pixels-audit.json、scripts/check_expected_pixels.py：记录的同调用ImageRef编码字节SHA与保存PNG一致，完整像素按已知双矩形规则独立构造expected核对通过。40是采样，不是全部帧；不要以静态同图正确单独推断任意跨ordinal新鲜度、所有相机图像或历史根因。
4. 原截图007/008及Stop相关记录必须保留。Stop后保留32个历史展示资产及界面提示符合当前生命周期契约，不应直接判为泄漏或通过清空历史数据“修复”。按Job执行停止、历史结果可读、reader/lease退休与最终窗口close分别解释，不合并为一个时点。
5. READY不等于retired。READY记录之后约357ms，采样首次读到原reader为false；这是观测时间差，不能精确等同线程真实退出延迟，不能声称READY时资源已全释放。短期lease未逐handle穷尽采样，只能据所采末态lease0及其他原始证据陈述，不能把“未观察到”推成“从未存在”。
6. 原生运行中标题栏X关闭有关闭中截图/视频、原actual-owner引用及SHM/lease等终态证据；最后OS退出来自后置实际桌面PID检查（10个tracked PID均gone），不能把该证据提前到close触发或READY时。区分原对象观测、进程检查和仅状态文本，不用新建对象替代原owner证明。
7. evidence/environment/project-semantic-check.json：正式运行副本revision/hydration变更如实披露，原始直接比较false涉及boundary None→空字符串，归一化行为语义一致。不要宣称工程每个原字节都没变，也不要把正常同步保存误写成调试污染。上游旧证据只校验引用/hash，本次附件不是重新上传全部上游或Windows结果。

## 后续判断与建议

先读源码当前契约并与固定版本证据对齐。没有新反例时保留限定PASS即可，不要为了让清理数字更早归零而取消合法历史结果保留、有界资源策略或安全限制。

如需要补观测，先提出最小建议：区分READY/reader终止/owner退休/lease释放/OS退出的时点，为短期lease增加可关联handle的生命周期事件，并在保持业务语义、采样开销可说明的前提下验证。取证缺口本身不是新的已确认产品bug；不要未经授权自动改源码或新增重负载实验。

如另行追查历史capture根因，列明新假设、反例和受控实验，使用变化内容、同调用identity、字节/像素和原task/proof/owner完整链；不得以本轮成功或静态图正确替代历史因果证明。保留旧FAIL与新结果各自的源码SHA、环境、原日志和覆盖边界。

Windows CI由Codex在实际后续代码SHA上按需执行或核验并给精确commit/workflow/run/job结果，不要求本次桌面验收代理代查。本轮没有查询Windows，不能挪用旧CI来声称当前Windows通过。性能/全产品/容量扩展如需执行，应另列授权范围和验证计划。

输出应包括：已读证据与当前差异、是否需要产品修改及依据、必要时的最小观测/回归建议、PASS/FAIL/NOT_RUN、仍OPEN的历史问题和未验证范围。新测试结果放新目录，不覆盖本轮冻结报告和原日志；无授权不force push、merge、deploy或改写分支历史。

## 附件边界

PDF/DOCX与恢复后的ZIP均与已交付原附件逐字节一致。证据含11张原产品截图、40张采样PNG、340raw关联证据、55自动/注入结果、完整observer和产品日志、工程/脚本/独立审计。完整454.166秒原录屏仅任务工作区留存并记录原SHA；附件仅两段无损节选（Designer83.333–156.416秒，Runtime187.500–354.750秒），Runtime保留Stop等待/历史图/restart和最后354.666秒关闭中帧。随后终端画面排除，后置OS退休另有日志。原H.264 packet连续hash匹配，无重编码、产品像素编辑或加速，不声称交付全部录像。ffmpeg日志正常SIGINT结束，无独立wait退出码。
