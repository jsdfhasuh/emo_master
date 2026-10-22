# 本地视频采样与图片文件名输入：源码提交验证

日期：2026-10-22，Asia/Shanghai。
分支：`agent/runtime-workflow-architecture-v1`。

将工作区已有的视频取帧、Image Saver 文件名输入、三个示例工程及对应测试整理提交。
上一轮设备易用性交付 `83bcd5a187abc1b502396d99591ad73272f65213` 已在远端；本次只承接其后的这组未提交文件。

## 固定源码与行为

- 源码提交：`196c0823c37f68f5833b877b366bbb8ec4955a2d`。
- 被测 tree：`4eaf72ca85535d9e9b71753621452304166fda31`。
- 明确暂存 27 个源码、测试、示例和说明文件，再从索引 tree 导出独立 `git archive` 快照。
  提交后核对实际提交 tree 与被测 tree 完全相同。
- 实际导入：`C:/Users/JSDFHA~1/AppData/Local/Temp/emo-media-delivery-dqn66h8e/source/src/emo_master/__init__.py`。

`vision.io.video_loader` 逐次输出按视频时间间隔采样的图像，保留最终帧、reset、Job 独立游标和取消释放；
接入现有单节点显式调试及 Runtime 文件路径检查。Image Saver 1.1.0 增加可选 string `fileName`，
未连接时保持原固定路径；连接时只替换 basename，保留扩展名规则、中文路径、覆盖策略和已有输出重叠保护。
示例串联 While 视频采样、YOLO 多目标遍历、原始像素裁剪及逐目标命名保存。

## 本次实际验证

环境：Windows，conda Python **3.10.21**、PySide2 **5.15.2.1**、Qt **5.15.2**、OpenCV **4.10.0**。
Qt 设置为 `offscreen`，`PYTHONPATH` 指向独立快照；视频、图像、输出和 Runtime 数据均为临时夹具。

| 检查 | 本次结果 |
| --- | --- |
| 相关回归 | **181 passed in 36.04s**，JUnit failures/errors/skipped 均为 0 |
| Ruff | 17 个受改 Python 文件，通过 |
| mypy | 5 个受改源码文件，`--follow-imports=silent`，通过 |
| 协议生成 | `scripts/gen_proto.py --check`，通过 |
| 暂存差异 | `git diff --cached --check`，通过 |
| 源码归属 | 工作区原文件哈希未变；实际提交 tree 与被测索引 tree 相同 |

181 项为此次实际执行数量，覆盖两个算子、三个示例、注册与端口契约、路径保护、正式 Runtime Job、
显式调试写入、While 最终帧和取消释放、Qt 文件选择与端口展示，以及原图片加载链路。
精确的 pytest selectors、完整 argv、cwd、时间和退出码保存在
[validation.json](../evidence/2026-10-22-video-image-delivery/validation.json)。
该数量不包含旧报告的 91／104 项，也不表示全仓测试通过。

首次环境核对使用 `Path.is_relative_to` 比较了 Windows 长路径与 `JSDFHA~1` 短路径，错误返回 exit 1。
原始输出已经显示导入来自快照，修正验证脚本为 `os.path.samefile` 后 exit 0；
两次输出分别保留在 `environment.log` 和 `environment-fixed.log/json`。没有为此修改产品源码或测试断言，
也没有覆盖首次记录。其他命令的原始退出码均为 0。

## 证据与范围

[独立证据目录](../evidence/2026-10-22-video-image-delivery/)保留本次原始日志、JUnit、环境修正说明、
命令元数据和 SHA-256 清单。较早的
[图片保存验证](2026-10-21-image-saver-filename-validation.md)及
[视频采样验证](2026-10-21-video-loader-validation.md)原结果继续保留。
它们记载的旧设备直接调试／通信用例失败没有在本次修复或改成跳过；此次仅复跑上述功能的受影响用例，
没有再次执行那组历史扩展批次。

原生桌面人工操作、真实用户视频和模型质量、真实硬件、冻结 EXE、Linux 和跨机器均未执行。
本次只提交源码并正常推送，不构建、发布或替换已安装产品。
使用打包程序时仍需后续构建；旧 EXE 不会加载这次源码修改。

文档与证据另行提交，提交前后核对 `src/`、`tests/` 及示例工程与被测源码一致。
最终交付 SHA 和 `git ls-remote` 核验由交付回复提供，避免文档自引用提交 SHA。
