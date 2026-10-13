# 8c13404 Debian 桌面专项复验

SHA：8c1340461d9675ab3a5f078135f17407fa027cc7。日期：2026-10-13。
仓库 jsdfhasuh/emo_master，来源分支 agent/runtime-workflow-architecture-v1。实际验收 detached HEAD 固定完整 SHA，不跟随分支，未新建分支或提交。

本轮不能全部通过：五路真实 UI 订阅配额冲突 FAIL；首次 ABBA 的全 96 显示覆盖 FAIL；正式 9–96 窗口按原分母独立 PASS，原 measurement_status VALID，performance_status NOT_ASSESSED。历史根因保持 OPEN。本轮不查询 Windows CI，不作为全产品、SQLite/O20、P0 性能或现场设备验收。

## 报告与复现构建

- `EmoMaster-8c13404-Debian-acceptance-zh.docx`：中文可编辑报告。
- `EmoMaster-8c13404-Debian-acceptance-zh.pdf`：同次最终渲染 PDF。
- `CASE-MATRIX-8c.json`：规范 PASS/FAIL/NOT_RUN；D01–D08 分条，方法边界另列。
- `build_report_8c.py`：本轮独立报告构建脚本，只构建文档，不运行产品或测试。
- `DOCUMENT-QA-8c.json` 与 `REPORT-SHA256-8c.txt`：最终渲染与摘要。

`render-8c-*` 为排版核查中间目录，不是额外交付报告版本。完整证据位于任务工作区；交付包选择与节选来源由交付包中的出处索引说明，不能把本 README 的原工作区路径列表理解为打包了全部录像或截图。

## 原始证据索引

以下路径相对本轮验收根目录（本 README 的上一级）。

| 主题 | 路径 | 关键边界 |
|---|---|---|
| 环境与源码 | `evidence/environment/identity.txt`、`final-git-projects.json` | 固定 HEAD、git status 空，三个工程和 items/sample 五个文件 hash 与仓库原件相同 |
| 执行计划 | `evidence/EXECUTION-PLAN.txt` | 原96/8、1080p5Hz、500ms server/550ms client、90s watchdog；ABBA 只运行一次 |
| 软件 | `evidence/renderer-*`、`capture-*`、`paths-*` | 13 / 180 / 6 用例组互不重叠，不是全量产品或桌面替代 |
| ABBA 原始 | `evidence/abba/`、`abba.log`、`abba-exit.txt` | 试次0预热序号2缺live/双窗commit/paint，不删样本；两窗共享consumer0 hub |
| ABBA 复算 | `report/independent-bgr-raw-audit.json`、`trial0-warmup-ordinal2-timeline.json` | 正式88完整；supersession仅源码与时序推断，无决策时high/generation直接记录 |
| 独立像素probe | `evidence/independent-pixel-contract/` | 双端96全身份及数组比较记录；静态同图不能单独识别跨ordinal新鲜度，额外开销不能用于ABBA性能 |
| Qt gallery | `evidence/native-bgr-gallery/` | 四布局×四预览比例16组；base220×132，96DPI/DPR1，非系统DPI/非Designer |
| 真实D调试 | `evidence/ui-exports/`；截图012–104 | 原生文件选择及完整JSON/PNG导出，不是offscreen替代；临时99/200未污染正式7/127 |
| D图像与循环审计 | `report/D02-pixel-audit.json`、`D06-export-audit.json`、`D07-pixel-audit.json`、`D08-image-pixel-audit.json` | 原图→127、blur输入、blur→200及最终127的不同内容全数组核对；循环四ZIP值/iteration核对 |
| 调试无正式Job | `evidence/environment/designer-formal-jobs-readonly.json` | Designer退出后独立嵌入库mode=ro，jobs=0/jobEvents=0；不是SQLite/O20复验 |
| NG真实UI | `evidence/ui-v2/observer.jsonl` 与派生PNG；截图106–111 | 两Job各前20提交，共40PNG；非全部帧存档；原AssetStore已清理 |
| 五While真实UI | `evidence/native-five-while-v2/observer.jsonl` 与派生PNG；截图112–125 | main-run/second-run/flow3-run/flow4-run/flow5-run五入口的产品session对display配额2；117/119/120首次错误；22正确样图不取消订阅FAIL |
| 五While定位 | `report/five-while-display-quota-audit.json` | 完整固定文件/行号及observer无额外订阅；无active计数逐拒绝直证 |
| 真实UI图像复核 | `report/native-product-pixel-audit.json`、`native-product-pixel-second-audit.json` | NG40、五路22=5/5/5/5/2，编码字节/owner/完整期望像素均通过；仍限静态采样及同源码oracle |
| 首helper失败 | `evidence/runtime-five.log`、`runtime-five.exit` | psutil导入失败发生在Qt/Runtime之前；仓库外v2改用标准库/proc，未升级依赖或修产品 |
| 资源最终退休 | `evidence/environment/final-process-check.json`、各产品退出文件、observer close/loop行 | 5860在五路close/loop return仍在，后续18PID核对才全不在；NG无loop return字段 |
| 录像 | `evidence/video/desktop.mkv`、`ffmpeg.log` | 原完整录像2898秒、59,248,001字节，SIGINT日志正常结束且ffprobe可解读；无独立wait返回码；交付节选不得冒充原时长 |

## 方法分层

1. 原 ABBA 仅编码 SHA 与解码契约，不是逐像素 oracle；独立 probe 另记，不能改写第一次 ABBA 缺号。
2. NG40、五路22为显示后保存的派生图；与记录原 ImageRef 编码 SHA 恰好相同，但原 AssetStore 已被正常清理，没有 close 后补读原历史资产。
3. 二次期望 overlay 按源码规则离线重建、未导入产品 operator，仍是同源码规则 oracle；不能外推相机真实场景、全部颜色与任意帧新鲜度。
4. D05可证UTC观测区间约84秒，D06约80秒；不伪造未知精确暂停起点。ps lstart使用桌面本地时间，不当UTC或耗时；性能关联使用monotonic ns。
5. 全部状态仅本轮固定版本；软件通过、正式窗口通过、正确图像样本和资源退出，都不能冲销五路显示FAIL或旧历史根因。

五路 observer 的543仅为当前保留事件窗口计数，可能有retention，不作为精确总累计；单停main-run后其余四路仍RUNNING。Designer原生录像已保留325–2133秒连续区间，包含D05/D06长暂停与D02/D07/D08完整操作，不将这些片段列为缺失。

严重度：W01 P1，阻断五路实时结果查看验收；建议修订有界订阅容量/复用契约并增加五真实Session回归。A02 P2仅限本轮全96显示覆盖，实时latest合并是否符合需求待界定，不直接称源数据丢失。
