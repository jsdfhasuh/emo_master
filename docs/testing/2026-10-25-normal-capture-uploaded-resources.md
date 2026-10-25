# 普通运行带页面显示：已上传资源误报缺失修复

## 基线与范围

- 源码基线：`9265547dbe61bc57542d3cf0ea143fcf455600fb`，在其工作区中修复；未提交、推送、打包或部署。
- 范围：普通 `StartJob(capture_presentation=true)` 的资源查找、启动失败诊断传输与弹窗。
- 不修改算子实现/版本、工程 schema、循环配置、检测阈值、凭据、模型或图片。
- 使用项目 Python 3.10 / PySide2 环境。Runtime 集成测试使用临时数据目录，覆盖内嵌与回环 gRPC，不访问相机。

## 实际问题及证据

上传/发布链路将资源保存在 Runtime 托管资源目录中，并通过资源 ID 映射到实际文件。
在线工程 JSON 所在的 `online-projects/<revision>` 目录不必再有一套同名文件。

`prepareRuntimeProject()` 已使用 `projectResourceTransfers.resolve(document)`；但页面显示的
`freezeNormalCapture()` 又调用了没有映射参数的 `verifyResources()`。因此，正常的
“上传资源 → 替换在线工程 → 带页面运行”仍在 JSON 目录按相对路径查找，错误地拒绝启动。

针对用户工程进行只读核对，三项本地资源和 Runtime `READY`、有效资源副本均存在，大小与工程
SHA-256 记录一致：

| 资源 | 大小（字节） |
| --- | ---: |
| `models/yolov8n-face-detect.onnx` | 12139175 |
| `samples/astronaut.png` | 791555 |
| `samples/empty.png` | 2300 |

在同一份在线工程文档上，不传映射复现 3 个 `E_RESOURCE_MISSING`；传入已核实映射后准备成功，
文档内容不变。另使用真实 Runtime 模型副本做 CPU 离线推理：样例图 1 张人脸，空白图 0 张。
这是资源/模型证据，不是摄像头或持续运行验收。

## 最小修复

1. `freezeNormalCapture()` 接受可选 `resourcePaths` 并传给现有 `verifyResources()`。
   普通 StartJob 提供 Runtime 已有的资源映射；未映射的本地资源继续按工程目录查找。
   不绕过缺失、普通文件、大小和摘要校验，不手工复制到在线工程目录。
2. `StartJobReply` 增加向后兼容的字段 10 `diagnostics_json`，保留原始 `message`。
   页面准备失败保留已有 ValidationIssue，并附被拒绝的 Runtime 文档中的资源路径和用途，
   不传节点参数或凭据。相同启动请求的核对返回相同诊断，不重放执行。
3. Designer 控制器保留结构化诊断；不覆盖已确认的来源节点诊断，不伪造节点关联。
   启动失败弹窗区分缺文件、大小不符、内容不符、路径无效和已上传资源损坏。
   主面板按模型/输入图片列相对文件名和处理建议；最多列 6 项，完整原因、路径、代码仍在详情。
   不根据当前草稿为另一份 Runtime 文档猜测文件名。

## 验证

本机原始证据位于忽略目录 `build/normal-capture-resources-20261025/` 及同前缀 XML 文件。

| 验证层次 | 结果 |
| --- | --- |
| 修复前正常链路反例（内嵌 / 回环） | `normal-capture-resources-20261025-repro.xml`：2 failed，均为已上传资源误报缺失 |
| 修复后资源、普通/多流程页面捕获、首次/替换发布、失败提示与资源 UI 回归 | `regression.xml`：129 passed，5 skipped |
| RuntimeController / RuntimeWorker 回归 | `controller-worker.xml`：39 passed |
| 最终失败弹窗及身份/详情测试，Windows Qt 100% | `qt100-final.xml`：27 passed，无跳过 |
| 同上，Windows Qt 150% | `qt150-final.xml`：27 passed，无跳过 |
| 用户工程及真实模型只读检查、离线推理 | `actual-resource-check.json`：通过，未改现场 Runtime，未访问摄像头 |
| 协议生成一致性、受影响 Python 文件 Ruff、`git diff --check` | 通过 |

offscreen 回归的 5 项跳过是明确依赖 Windows 原生 QMessageBox 绘制的测试，其中运行失败弹窗
3 项已在两种缩放下补跑；另外 2 项是未修改的工程配置弹窗原生绘制测试，未在本轮补跑。
Windows 绘制采用隐藏的真实 Qt widget，检查文字/按钮未裁剪、尺寸不超出屏幕、详情展开/收起，
并检查截图；不是正在运行的用户桌面程序验收。

最初测试调试还出现过夹具问题：回环 Runtime 已有页面服务、测试清理早于 worker 资源退出，
以及新 basetemp 缺少父目录。原始 `before.xml`、`mapping.xml`、`qt100.xml` 等失败记录保留，
仅修正测试服务归属、清理顺序和证据目录准备，没有放宽产品断言。

## 生效与未验收边界

- 当前打开的 Designer / 内嵌 Runtime 是旧进程，不会热加载此次源码修复；需正常关闭后重新启动。
- 模型和图片已就绪，无需再次下载，也不因这次误报重复上传或重新发布工程。
- 本轮没有启动用户工程、连接真实摄像头、发声、写入现场 Runtime 或修改其数据库。
- 本轮未做冻结包、远程部署、人工桌面操作或真实相机持续循环验收。
