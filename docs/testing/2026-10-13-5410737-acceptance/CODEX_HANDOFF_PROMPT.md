# Codex 后续交接提示词：5410737 限定验收

请先阅读本目录中文 PDF/DOCX（7页）、CASE-MATRIX.json 和 README.md。执行 `sha256sum -c SHA256SUMS.txt` 后运行 `python reassemble_evidence.py` 恢复无损分片的原 ZIP，校验 ORIGINAL-ARTIFACTS.json，再将三个证据包解压到同一独立目录，验证包内 SHA 清单。先读证据，再决定是否需要产品修改；不要为“修复”本轮并不存在的失败而改代码。

## 固定版本与结论

仓库 jsdfhasuh/emo_master，既有分支 agent/runtime-workflow-architecture-v1。本轮只实测 5410737a561e1f192ee9a081b3f208c9b27d9886，Debian/xcb 原生 Qt。包含此目录的提交仅归档文档；新归档 HEAD 或其他后续提交没有因此被实测。先记录当前 HEAD 与固定 SHA 差异。

本轮限定范围通过：五真实 GUI DisplaySession 生命周期、两场真实原生窗口操作、原 ABBA 四组完整1–96展示链、确定性 supersession/变化图像契约检查和一次 Designer 正式 Run 冒烟。不要虚构剩余本轮 FAIL，不要把这解释为全产品、全部平台、最大容量、性能或历史根因全部关闭。旧8c13404的 W01/A02 原始 FAIL 保持历史事实；更早 capture 历史根因仍 OPEN。

## 必读证据与正确解释

1. 原 ABBA：evidence/abba 和 report/INDEPENDENT-DISPLAY-AUDIT.json。四组源1–96、两个独立网络 consumer received/decoded/live 及两 Qt commit/paint 均完整，正式9–96各88也完整。原全96不能被只报正式88替代。两 Qt 接共享 consumer0 的 hub，并不是每窗独占一个网络 consumer。
2. 确定性注入：consumer0 ordinal2 的 SUPERSEDED 有同 Runtime/Job/scope/generation、high3及先后时序证据；这是刻意构造的契约测试，不是本轮原 ABBA 缺号或产品故障。变化96图像检查保留 JSON 原断言，未交付96原始 PNG 全档。不得从旧静态同图 pixel probe 推断跨ordinal新鲜度，也不得用这次确定性测试倒推旧缺号根因已直接证明。
3. 真实五路：evidence/native-full-lifecycle/observer.jsonl、native-close-running/observer.jsonl、runtime-native/logs、截图003–019、report/UI-OPERATIONS.md。原生选择五个-run目标并启动，逐tab查看，固定顺序20次快切；共享第二 RuntimePages 及取消流/自动重连使用醒目标注的辅助控制，不是产品原生按钮。辅助复用已有 session/hub，不增加 network consumer。两个真正的窗口关闭均为原生标题栏X。
4. 第一场：五路运行、共享窗、断流重连、单停主路而其余四路继续、主路新Job重启、all-stop，再标题栏X退出；订阅7 admitted/7 retired。第二场新 Runtime：五路运行中不先停止，直接标题栏X关闭，订阅5/5退休。两场 rejected=0，workPeak=2，五流成立且工作位仍有界。按原日志区分控制操作、故障注入和被测产品行为，不能以辅助按钮覆盖冒称原生产品入口覆盖。
5. 两场终态：active/work/byJob/cleanup、资产/cache/reader/lease及Session/exporter线程释放有原证据，28个追踪PID和额外Designer worker由后置/proc检查证实退出；两resource_tracker在各eventloop_return时尚存在，不能把后置证据提前到loop-return。直接关闭后的 supervisor ownsJobResources 字段没有单独采样，不能声称内部断言已执行，也不要把这个取证缺口虚构为新产品泄漏bug。
6. 独立35 native采样PNG尺寸/dtype/pixelSHA正确，不能冒称存档了所有帧。15项软件自动测试、2项offscreen GUI测试、两场真实xcb操作各自记账，offscreen不是原生桌面替代。Designer仅原工程独立副本正式Run输出7；revision1→2是产品同步保存，节点/参数/边/坐标语义一致，非调试污染，也不是D01–D08整套重验。
7. 原始环境/工具错误保留：uv缓存目录只读、窗口paste和Alt+F4路径限制，发生位置与处理见UI-OPERATIONS，不算产品失败。录屏有SIGINT正常结束日志，无独立wait返回码。

## 后续工作顺序

本提示词首先交接证据并作只读审阅；若没有新的失败证据，结论可为无需产品修改。新增测试/源码修改另按用户任务授权执行。先判断任务目标和当前代码差异，再做最小必要工作，不预设仍须修复五路容量问题。本轮已证明该固定版本在受测五路场景成立，不能反向取消有界容量、安全限制、公平性或背压。

如扩展自动回归，优先把本轮实际场景覆盖写清楚：五真实Session、逐tab和快切、共享同hub不新开流、可控断流恢复、单停四路继续、重启新Job、all-stop、运行中原生关闭，以及生命周期 admission/retirement 配对。若补直接关闭后的 ownsJobResources 观测，使用明确的取证时点，先补观测/断言，不把未采字段当成已确认缺陷。保持原始失败与新结果分开，禁止删除预热、修改deadline或重跑择优取绿。

如继续追查历史capture根因，应另列假设和新实验，使用变化内容、完整 identity/owner/SHA/决策事件与生命周期边界，不以本轮通过或静态图正确代替因果证明。live latest展示契约与可靠结果/审计完整性分层，不无条件强迫实时UI画每一帧，也不通过口径变化隐藏遗漏。

Windows CI由你在实际后续代码 SHA 上按需运行/核验，给精确commit/workflow/run/job结果；本轮未查询Windows，不能借历史CI声称当前Windows通过。性能窗口、完整产品、32Job/64流容量、生产设备和跨平台均未验，如用户要求扩展再单列计划和结果。

输出：已读证据与固定SHA、当前差异、是否确需改代码及理由、必要时提出最小改动及回归建议、PASS/FAIL/NOT_RUN、未关闭历史问题和证据边界。新结果放新目录，不覆盖本轮冻结报告或原始日志。无额外授权不要force push、merge、deploy或改写分支历史。

## 附件与复现边界

本目录PDF/DOCX和恢复后的三个ZIP与已交付原附件字节相同。21张原截图、35 native PNG、原始JSONL及7段无损录像节选保留；完整1202秒录像仅在任务工作区留存，ZIP中只有节选和原hash/时间映射。两个native节选最后均为关闭中画面，实际退休由原日志及后置进程检查独立证明。关键帧对齐省略部分干净开头，不声称录了全部操作。

审计原脚本 audit541.py 和 audit541extra.py 已保留；最后35PNG像素核对为一次性内联命令，未留独立脚本。脚本含原工作区绝对路径且extra含当时进程采样，不可声称重新运行两脚本能逐字段再现原最终JSON。报告构建脚本与取证脚本是复现材料，不是产品代码修改。
