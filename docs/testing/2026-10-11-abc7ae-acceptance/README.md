# abc7ae Debian 独立验收与代码定位归档

验收时间：2026-10-11。唯一产品验收目标为 **abc7ae0541e997be0bbe3a4794ad604c239de79a**。
本目录是冻结证据的文档归档；发布本目录产生的新 HEAD 不在本轮验收范围内。本目录没有修复产品代码，也不宣称新 HEAD 获得产品验收 PASS。

## 结论与入口

- [完整报告](REPORT.md)、[PDF](acceptance-report.pdf)、[可编辑 DOCX](acceptance-report.docx)、[最终用例矩阵](case-matrix-final.json)。报告原文保持字节不变。
- 总体门禁 **FAIL**：本地全量 **4828 passed / 1 failed / 19 skipped，另29 subtests passed**。保留首次失败，不用后续专项通过覆盖它，也不重复累加不同运行计数。
- 原生 UI 完整主路径 **D01–D08、O20 PASS**；其余32项原 O/U 完整范围 **NOT_RUN**，代表子场景通过不等于完整范围通过；实体设备、其他平台和现场边界保持原限制。
- [精确 SHA CI 摘要](CI-EXACT-SHA.md)：3 runs成功、7 jobs成功、1有条件跳过。直接 abc7ae checkout 与 PR merge SHA checkout 分开记录；发布归档后可能新增的 CI 不属于此冻结快照。
- [定位附录](diagnosis/README.md)：SQLite 首 FAIL、历史 NG/P0 INCOMPLETE、ROI 导出三个专题，以及 VARIABLE_UNAVAILABLE 的单次隔离补证。后者不算原生 UI PASS，不是历史 NG 根因证明。
- [最终身份摘要](FINAL-IDENTITY.json)、[最终清理](final-cleanup.json)：5034个 tracked 文件无改动；source 仅有本轮 GUI 产生的未跟踪 docs.emofixture，原件及同字节证据保留，因此不声称 Git 全 clean。本轮 UI/Runtime/观察进程已释放，最终同 PID namespace 核验 processes=[]、observerAlive=false。

## 完整原始证据：17个 raw 分片

原 ZIP：68,072,637 bytes。
SHA256：`1b01545a84b957375abac33975fe29307f56f72d99ffd57cd469ed6ca1a2f2bf`。
每片最多4 MiB（4,194,304 bytes），文件名 evidence.zip.part001 至 part017；这些是二进制原始分片，单片不是可解压 ZIP。顺序与每片摘要见 [EVIDENCE-PARTS.json](EVIDENCE-PARTS.json)。完整首次失败、原始日志、DB、UI截图、导出物和历史中间状态都在原主ZIP中，没有因发布删除或改写。

在 Windows、macOS、Linux 安装 Python 3 后，在本目录运行：

    python reassemble_evidence.py

Windows 也可使用 `py -3 reassemble_evidence.py`。脚本先验证全部分片，再以排他创建方式重建 evidence.zip，并验证完整大小及SHA256。输出已存在时拒绝覆盖。也可把一个尚不存在的输出路径作为参数。脚本不会运行任何诊断probe，不会自动解压。

解压后根目录为 evidence/。REPORT.md 原文所述证据路径以这个 evidence/ 为根；源码定位对应 abc7ae 的 source/，源码本身不在本发布文档目录复制。诊断目录内文本保持原样，相对引用在其自身结构内保留，原工作区绝对路径是历史来源说明，不能在另一机器直接使用。[code-diagnosis.zip](code-diagnosis.zip) 是独立冻结的原始定位附录ZIP（122,262 bytes），diagnosis/ 提供同一附录的可浏览副本。

## 完整性、安全与排除范围

[SHA256SUMS](SHA256SUMS) 是本地准备时本目录文件摘要清单；发布前新增交接提示/统一manifest后应重新生成。原 diagnosis/SHA256SUMS 保留其原内容，不代替顶层清单。
[PUBLICATION-SAFETY-AUDIT.json](PUBLICATION-SAFETY-AUDIT.json) 记录发布准备的只读扫描范围、模式和候选计数；匹配值永不输出。静态扫描是辅助检查，不证明绝对不存在未知格式秘密。

[原始排除清单](ORIGINAL-PACKAGE-EXCLUSIONS.json) 随原ZIP保存：可再生 Ruff/pytest/UI/renderer 缓存、重复页面渲染等未打入主ZIP。诊断是冻结报告后新增材料，原主ZIP未包含，故在此独立完整归档。Python venv、依赖缓存、整个源码 checkout/.git、早期 checkpoint ZIP 不属于这份证据发布；Library内部上传脚本、回执、临时传输地址与当前发布工作目录也不归档。它们不是原始失败/日志/DB/UI证据。没有清理或删除原工作区。

此归档不执行 probe、不连接设备、不部署。任何后续修复与重跑须保留首FAIL并明确新的代码SHA和运行身份。
