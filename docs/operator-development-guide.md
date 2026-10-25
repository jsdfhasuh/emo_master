# 算子与设备插件开发指南

本文说明当前 emo_master 中如何新增、修改和验证算子，以及如何注册设备描述符并接入 Runtime 设备管理器。适用于普通算法、文件/模型资源、状态算子、设备采集和通讯，不授权真实设备联调或现场配置变更。

最新核对日期：**2026-10-25**。本轮整理的源码基线为 `3ebc90954d6514b4e5681eaf7b57812a63d23461`，已包含编译器、执行器、TCP/归档诊断、PatchCore、独立 Runtime 和 Designer 浮层修改。配套文档、可执行示例测试及本轮验证记录随文档提交入库，详见 [工作区整理与验证记录](testing/2026-10-25-workspace-delivery.md)。源码入库不代表已有 EXE 已更新，也不等于真实设备或现场验收通过。

完整历史盘点见 [算子目录](operator-catalog.md)。2026-10-25 新增 RTSP 托管采集后为 **78 个内置算子、55 个入口源文件、9 个托管设备算子、7 种内置设备类型**。RTSP 的兼容边界和使用方式见 [RTSP 托管相机](guides/rtsp-managed-camera.md)；下文历史验证次数不代表新增实现已经通过同一批验收。

### 本轮变化

| 范围 | 当前开发规则 |
| --- | --- |
| 多选一业务输入 | 编译器按处理对象与连线细化必需输入；缺少本次输出可向下游传播跳过，不把全部候选端口设为必需 |
| 错误与副作用 | 保留原始业务错误/取消及已发送、已保存的事实；算子失败不等于自动回滚或可以重试 |
| PatchCore 1.1.0 | 增加 overlayMode / highlightThreshold；高亮只是 NG 图的相对响应显示，不改变模型判定或新增结构化区域输出 |
| Designer 结果入口 | 原生 Qt 使用浮动运行面板，结果定位复用 NodeResultCoordinator.showPanel()，不再直接假定右侧固定面板可见 |
| 未变的基础合同 | 工程 2.7、设备目录 1.0、DeviceType/Driver API 1.1；既有注册和设备配置归属继续有效 |

## 1. 先明确层次

| 层次 | 当前版本/入口 | 责任 |
| --- | --- | --- |
| Python / Qt | Python 3.10、PySide2 / Qt QWidget | 沿用项目环境，不在算子开发中迁移框架 |
| 核心包 | 0.6.1 | 校验插件核心版本范围 |
| 工程 | schemaVersion 2.7 | 工作流、页面、资源、生产和设备定义 |
| 工程内设备目录 | devices.schemaVersion 1.0 | 保存用户设备实例，不是驱动接口版本 |
| 设备类型/驱动 API | DeviceType 1.1 / Driver API 1.1 | 安装声明与 Runtime 驱动协议 |
| 工作流包 | 1.4 | 流程、设备依赖、资源及导入复用 |

版本号不能互换。设备声明自身的 `version` 例如 `1.0.0`，是工程 `driverVersion` 要精确匹配的版本，也不等于 Driver API 1.1。

开发前先读仓库 [AGENTS.md](../AGENTS.md)，记录 HEAD 和未提交修改。正常业务路径、局部实现和现有模块优先；不为普通算子增加通用事务框架、全局设备锁或额外启动门槛。

### 执行主线

```text
manifest.json + Python entry
  -> PluginRegistry.scanRoots()
  -> activeOperators / rejectedOperators
  -> Runtime ListOperators -> Designer 算子目录
  -> ProjectDocument / WorkflowCompiler
  -> WorkflowRunner / OperatorExecutor
  -> executeNode(inputs, params, runtimeContext)
```

设备另有一条声明和资源管理链：

```text
plugin root/**/device_types/*.json
  -> scanDeviceTypes -> deviceTypes / rejectedDeviceTypes
  -> operator.deviceRequirements
  -> project.devices.definitions + node.deviceBindings
  -> compiled devicePlans + frozen configuration cycle
  -> Runtime DeviceManager -> per-device scheduling -> Driver
```

没有中央 `registerOperator()` 调用，也不需要在 MainWindow 加品牌或算子名称分支。`docs/plugin-registration-flow.md` 保留注册背景；其中旧执行入口和历史规划不能替代当前 WorkflowRunner/OperatorExecutor 主线。

## 2. 从已有算子选参考

以下路径均相对 `src/emo_master/plugins/builtins`，各组互不重复，覆盖当前全部 78 个 manifest。

| 类别 | 数量 | 目录 | 主要开发关注点 |
| --- | --- | --- | --- |
| 标量/数值桥接 | 8 | number_value、number_compare、integer_add、number_absolute、number_exact_integer、number_truncate、number_pair、point_components | 有限数值、bool 排除、精确整数与截断、int64 边界、输入与固定值优先级 |
| 分支/选择 | 4 | flow_if、flow_switch、flow_error、value_select | 缺输出与 null 不同；选值不等于循环调度 |
| 共享状态 | 3 | global_counter、variable_read、variable_write | 使用 Runtime 注入的状态 accessor，调试状态独立 |
| 生命周期示例 | 1 | empty_operator | init/dispose 合同，不是设备模板 |
| 本地资源输入 | 5 | file_list、image_loader、image_batch_loader、video_loader、coordinate_reader | Runtime 路径、Unicode、reset、末项、游标与取消 |
| 图像预处理/运算 | 19 | absdiff、add_weighted、apply_mask、mask_logic、affine、blur、canny_edge、clahe、color_convert、crop、equalize、flip、in_range、morphology、perspective、resize、roi、rotate、threshold | 图像/frame/validMask、坐标变换、多输入来源、模式参数 |
| 分析/绘制 | 9 | annotate、blob_analysis、contour、histogram、hough_circle、hough_line、rgb_statistics、shape_measurement、template_match | 语义集合、坐标空间、测量与显示分离 |
| 集合处理 | 4 | collection_count、collection_filter、collection_select、collection_sort | 对象选择与缺输入传播、空集合与索引越界、稳定次序、原输入不可污染 |
| 几何桥接 | 5 | coordinate_calculator、detection_bbox、geometry_points、minimum_enclosing_circle、points_reframe | 点/框/圆类型、回源变换、模式适用性 |
| 模型推理 | 2 | yolo_inference、patchcore_inference | YOLO 1.4.0 可选裁剪、PatchCore 1.1.0 高亮、资源与生命周期、原始判定和显示分离 |
| 文件/数据库输出 | 4 | image_saver、detection_archive、result_writer、sqlite_writer | 覆盖规则、文件名、写入回执、部分完成与取消边界 |
| 独立网络设备 I/O | 2 | rtsp_camera、hikvision_audio | 当前没有设备槽；显式一次性连接/动作、取消、未知结果不重试 |
| Runtime 托管设备 | 9 | simulated_camera、huaray_camera、rtsp_capture、plc_slmp_read、plc_slmp_write、tcp_client、tcp_receive_once、tcp_session、tcp_session_send | 描述符、设备槽、静态 selector、周期、proxy、调度、释放 |
| 报文构造/验证 | 3 | coordinate_message_format、gateway_coordinate_format、gateway_ack_validate | 只处理数据，不拥有 socket |

同族算子可共享一个 Python 文件；manifest 的 entry 才决定实现，不是每个目录都必须有 operator.py。histogram 还继承 `_intensity_operators` 中的实现，查看 entry 后应继续跟踪基类。

Loop、Subflow、While、Dispatch、Join、工作流输入输出属于编译器/Runner 系统节点，不计入插件算子数量。新增其语义是跨层修改，不是添加普通 manifest 即可完成。

## 3. 完整的纯计算算子示例

下面实现一个“输入值乘固定系数”的新算子。它是开发模板，**本文不会自动把它安装进内置目录**，因此不计入上述 78 个。

目录：

```text
src/emo_master/plugins/builtins/example_scale/
  __init__.py
  manifest.json
  operator.py
```

`__init__.py` 为空即可。manifest 的完整示例：

<!-- example: scale-manifest -->
```json
{
  "operatorId": "vision.example.scale",
  "displayName": "数值倍乘",
  "version": "1.0.0",
  "entry": "emo_master.plugins.builtins.example_scale.operator:ScaleOperator",
  "category": "示例",
  "iconKey": "number",
  "summary": "将输入数值乘以固定系数。",
  "inputPorts": {
    "value": {"type": "number", "required": true, "nullable": false}
  },
  "outputPorts": {
    "value": {"type": "number", "required": true, "nullable": false}
  },
  "paramSchema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "factor": {"title": "倍乘系数", "type": "number", "default": 2.0}
    }
  },
  "minCoreVersion": "0.6.1",
  "maxCoreVersion": "0.x"
}
```

对应 `operator.py`：

<!-- example: scale-operator -->
```python
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict
    outputPorts: dict
    paramSchema: dict


def finiteNumber(value):
    return type(value) in (int, float) and math.isfinite(value)


class ScaleOperator:
    meta = OperatorMeta(
        "vision.example.scale", "数值倍乘", "1.0.0",
        {"value": {"type": "number", "required": True, "nullable": False}},
        {"value": {"type": "number", "required": True, "nullable": False}},
        {
            "type": "object", "additionalProperties": False,
            "properties": {
                "factor": {"title": "倍乘系数", "type": "number", "default": 2.0}
            },
        },
    )

    def validateParams(self, params):
        if set(params) - {"factor"} or not finiteNumber(params.get("factor", 2.0)):
            return {"code": "E_PARAM_INVALID", "message": "factor must be finite"}
        return None

    def executeNode(self, inputs, params, runtimeContext):
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        if "value" not in inputs:
            return {"status": "error", "error": {
                "code": "E_INPUT_MISSING", "message": "value is required"}}
        if not finiteNumber(inputs["value"]):
            return {"status": "error", "error": {
                "code": "E_INPUT_TYPE", "message": "value must be finite"}}
        result = inputs["value"] * params.get("factor", 2.0)
        if not finiteNumber(result):
            return {"status": "error", "error": {
                "code": "E_RESULT_INVALID", "message": "result must be finite"}}
        return {"status": "ok", "outputs": {"value": result},
                "metrics": {}, "diagnostics": {}}
```

项目没有要求统一继承某个 Operator 基类。现有 meta 常用 frozen dataclass；可以复用附近已有定义，但不要为这一个算子发明跨仓库框架。`validateParams` 返回 None 或 `code/message`，不能在这里读模型、连接设备或写文件。

### 注册验证

开发环境中将 `src` 加入模块路径，在仓库根执行：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -c "from pathlib import Path; from emo_master import __version__; from emo_master.core.plugin.registry import PluginRegistry; r=PluginRegistry(__version__).scan(Path('src/emo_master/plugins')); print('active:',len(r.activeOperators)); print('operators:',r.rejectedOperators); print('devices:',r.rejectedDeviceTypes); assert not r.rejectedOperators and not r.rejectedDeviceTypes"
```

该命令会 import 已审查的算子模块，不执行算子或连接设备。外部插件先审查 import 行为，不把注册检查当作任意代码的安全沙箱。

manifest 和 meta 的 ID、显示名、版本、PortSpec、完整 paramSchema 必须相符，包括 title、默认值和条件注解。旧插件可以缺部分 meta 字段是兼容行为，不是新插件应复制的捷径。重复 operatorId 会全部拒绝，不是后加载覆盖前者。

新增后更新对应注册期望及直接测试，但不要只把固定数量加一而不检查 ID、端口和拒绝列表。

## 4. 输入输出、状态与取消

### 原始数据合同

- 成功返回 `status="ok"`、按端口组织的 outputs，可带 metrics/diagnostics；失败返回 `status="error"` 和 `error.code/message`。不能把错误伪装为空成功。
- image 仍是 ndarray；语义 DTO 使用 `toPayload()/fromPayload()`，不能直接把 DTO 实例跨节点传递。几何/集合 payload 带真实 type/schemaVersion，数值有限。
- 新端口优先使用明确的 `type/required/nullable/schemaVersion`。当前 PortSpec 不支持任意展示注解；端口隐藏逻辑放在既有 UI 层。
- manifest/meta 和工作流接口支持完整 PortSpec；工程节点自身的 inputPorts/outputPorts 仍是类型字符串映射。生成节点时用 normalizePortType(spec) 提取类型，不把整份描述符直接复制进节点端口表。
- `required=false` 是允许缺键；`nullable=true` 是允许显式 null，不是同一件事。关闭可选图像输出时省略端口，不返回 None、空假图或前次帧。
- integer 可以连 number，反向不自动转换；bool 不能作为数值。JSON 不能凭形似字典连具体语义类型。完整兼容规则见 [输入输出契约](operator-io-contracts.md)。

### 图像与坐标

不改尺寸的算子透传 frame；Crop/Resize/Rotate/Affine/Perspective 同步更新 frame，必要时带 validMask。`transformToSource/homographyToSource` 的方向是当前图到源图，组合为 `H_input_to_source × H_output_to_input`。不能重复叠加 ROI 偏移，不能混用不同来源的 mask/几何，也不能把 overlay 当测量原图。

优先复用 `_image_frame.py`、`_classic_vision.py`、`_collection_ops.py` 和 core DTO。集合筛选/排序保留类型、来源和坐标，不原地污染上游输入。

### 生命周期与动态输入

普通算子类通常每次执行新建；实现 initOperator 或 disposeOperator 的类才会按同一执行器的 `(workflowId,nodeId)` 保留。执行结束按逆序清理。批量图片/视频的最后一项带 hasNext=false 仍是有效数据，继续调用才是耗尽；reset、切换资源和失败不能混淆。

普通动态参数使用 globalVariableBindings；输入覆盖固定参数时参考 video_loader.validateNodeParams；运行时解析后还需验证。不要让不再生效的固定值阻断合法动态绑定。

实时取消使用 runtimeContext 中的 `raiseIfCancellationRequested()`，不要轮询一次性布尔快照 `isCancellationRequested`。构造和初始化不拥有设备 proxy 的全部执行上下文。长等待/循环要有正常取消出口，dispose 释放实际拥有的资源，不能吞掉清理失败。

日志使用 `getOperatorLogger(context)`，绑定设备日志使用 `getDeviceLogger(context, slot)`。归属由 Runtime 注入，不伪造 job/node/device ID，不记录口令。

### 失败、取消与已发生的副作用

返回 status=error 或抛出普通 Python 异常均使当前工作流、外层调用及所属 Job 失败，连续运行也退出；不会默认重试或把失败当成跳过。保留已有业务错误码；无错误码的普通异常使用 E_EXEC_FAILED，不用 E_WORKER_CRASHED 冒充业务失败。取消保持取消异常和 E_CANCELLED，不补执行剩余动作。

文件写入和网络发送可能已发生，随后才解码失败、输出合同校验失败或收到取消。因此诊断要记录本次已确认事实，而不是从任务终态反推“没有执行”：

| 诊断 | 当前内容与边界 |
| --- | --- |
| tcpTransaction | TCP Client / Receive Once 的 action、outcome、bytesSent、bytesReceived、automaticRetry；传输完成后解码失败保留 COMPLETED，并标记 phase=decode |
| archiveProgress | 检测归档的 stage、逐目标一基 detectionIndex / detectionId、cropPaths、savedCropCount、overlayPath；只记录本次已确认保存的文件 |
| tcpReceipt | TCP session_send 原有发送回执，保留未知发送结果，不自动重发 |
| sqliteReceipt | SQLite 原有写入回执，经调用身份校验后保留已提交事实 |

`OperatorExecutor.failureDiagnostics()` 在后续输出校验或取消时显式保留这些既有诊断；Runner 把结果写回异常，继续送入 node.failed、workflow.failed 和上层错误详情。**这不是自动保留任意自定义 diagnostics 的通用机制。** 新增有副作用的算子时，检查它的失败与返回后失败路径，按既有局部机制保留必要回执，不另建通用事务或回滚框架。

Detection Archive 失败或取消不删除已保存文件；即使全部文件保存成功后才收到取消，也保留 stage=completed 的保存事实。无目标或冷却期不写文件，不带入前次 archiveProgress。详见 [输入输出契约](operator-io-contracts.md) 和 [Runtime 事件流](runtime-event-flow.md)。

### 可选输出与互斥业务输入

本工作副本的 YOLO 1.4.0 支持 onMissingCrop=error/skip：默认 error 保持缺裁剪目标时报错；skip 只省略 croppedImage/croppedFrame，仍返回检测和实际启用的其它输出。模型、参数、推理错误不因此忽略。

当前 Runner 对“已连接的必需输入本次未产生”标记 SKIPPED/E_INPUT_NOT_PRODUCED，node.skipped 的 missingInputs 列出端口；即使其它输入已到达，也不执行下游写入等副作用。下一次有数据可以正常执行。可选输入缺失不阻断，未连接的必需输入、上游缺少必需输出、工作流缺少必需最终输出仍受原合同约束。If/Switch 未选分支仍使用 E_BRANCH_NOT_SELECTED。

对于 Count/Filter/Sort/Select、Shape Measurement、Geometry Points、Result Writer，manifest 中的多个候选业务入口并不是全部必需。`WorkflowCompiler._nodePorts()` 通过 `_operator_modes.requiredChoiceInput()` 细化当前节点：

- 静态处理对象有效，且已有候选输入连线不与它冲突时，只将所选端口设为必需。没有连线不会变成合法缺输入调用。
- 旧节点未指定对象，或对象参数来自动态绑定时，根据唯一的候选业务输入连线细化；不读取动态绑定已覆盖的固定草稿来判断对象。
- 辅助 index、fileName 等不计入候选业务输入。多个候选连线或不一致配置不通过“全部设为必需”来掩盖，仍交由原有冲突校验处理。

新增多选一算子需同步考虑输入选择和编译后的必需性，并测试“上游未产出 -> 跳过 -> 下次恢复”。这不是给 schema 加一个选择框就能自动得到的能力，也不应把所有可选输入变成执行门槛。

**缺输出与空集合不同。** 真实产出的空集合照常执行，Count 可得到 0。Select 的 index 模式对空集合仍按 onOutOfRange=empty/error 处理，索引 0 也越界；first/last/topK 的空集合则成功返回空集合。这些行为含工作区修改，不推断旧 EXE 已具备。

### PatchCore 1.1 的开发边界

PatchCore 消费完整 DINO ONNX 部署目录或 transfer 外层目录，不是单个 ONNX 文件；CPU 推理、固定数值预处理、冻结阈值绑定和资源校验沿用部署合同。原始 score >= threshold 决定 NG，相对热图不参与判定。其数值预处理不能替换成 YOLO/OpenCV resize。

| 参数/输出 | 当前语义 |
| --- | --- |
| overlayMode=heatmap | 默认值，保持旧节点的整图相对热图叠加 |
| overlayMode=highlight | 仅 NG 图标示相对高响应；OK 图返回原图独立副本；只改变 overlay |
| highlightThreshold=0.7 | 范围 (0,1]，仅 drawOverlay=true 且 highlight 模式生效，是显示阈值而非部署判定阈值 |
| heatmap / anomalyMap | heatmap 保持整图相对 JET；anomalyMap 按需输出原始 256x256 float32 数据和还原几何，不从彩色图反推 |
| diagnostics.localization | 标记 highlighted、not_anomalous 或 uniform_response；均匀响应可仍为 NG，不以是否有轮廓替代判定 |

高亮依据去补边、还原输入尺寸后的未量化浮点响应；不新增 mask、轮廓集合、框或缺陷数端口，也不是已校准分割。显示参数走既有模式适用性校验，隐藏值保留草稿；显示切换本身不改变模型 session key，但不因此自动成为可在线发布参数。

YOLO 为类级模型缓存，PatchCore 为作业/节点实例 session。同一路径的部署文件被替换不会自动热更新；停止后重新运行或重置调试会话才重新加载，不逐帧校验文件。Runtime 路径与 Designer 本地路径仍分离。详细支持格式、输入输出和限制见 [PatchCore Inference](patchcore-inference.md)。

## 5. 参数与编辑器

参数键与 enum 值是稳定存储合同。中文通过 title、description、xOptionLabels 显示，不能直接把键改成中文。manifest 和 meta 的 schema 同步维护。

模式参数参考 `_operator_modes.py`：xVisibleWhen/xEnabledWhen 控制 UI，xValidateWhen 配合参数验证决定适用性。只在验证副本中中和不适用值，保留草稿数据；切换模式不删除端口和连线，不清空仍兼容的对象选择，不自动选第一个对象。

默认使用 schema form。复杂交互才增加 `.ui + Controller`，editor 声明参考 [histogram manifest](../src/emo_master/plugins/builtins/histogram/manifest.json)。Controller 提供：

```text
bind(rootWidget, context)
loadParams(params)
collectParams()
validate()
onOpen()
onClose()
dispose()
```

使用受限 EditorContext 和稳定 objectName，不 import/保存 MainWindow。dispose 幂等并结束线程、timer、future 和原 Runtime 的预览 session；schema form 继续可回退。错误应展开对应高级组并定位字段，不靠禁用合法操作掩盖校验问题。

### Designer 运行与结果入口

当前原生 Qt 界面通过 `FloatingRunPanel` 承载原有运行摘要和节点结果区域，复用现有开始/停止 QAction 和状态拥有者，不产生第二个 Job 控制器、结果缓存或订阅。收起面板仅改变显示，不停止任务；“停止”针对当前所选任务，“停止全部”仍是单独动作。

应用内“查看结果”和节点右键结果入口统一调用 `NodeResultCoordinator.showPanel()`，由它展开浮层、滚动到结果并定位 tab；没有浮层的环境仍回退原容器。修改这些入口时不要直接 `rightPanelContainer.show()`，也不要为新布局重建结果对象。浮层布局复用 `ui/canvas_overlay.py`；这些应用集成接口不改变自定义算子编辑器必须使用受限 EditorContext 的边界。

图标通过 manifest.iconResource 注册，保留 iconKey 兜底。受限 SVG/PNG、路径和体积规则见 [图标资源](operator-icon-assets.md)。图标或编辑器失败不等于算法必须被拒绝。

## 6. 注册设备描述符

### 声明、定义、要求和绑定

| 对象 | 位置 | 内容 |
| --- | --- | --- |
| DeviceType | 插件根下 `device_types/*.json` | 安装类型、driverEntry、能力与 schema |
| DeviceDefinition | `devices.definitions[deviceId]` | name、deviceTypeId、driverVersion、enabled、description、connection/defaults/policies |
| DeviceRequirement | 算子 manifest.deviceRequirements[slot] | deviceTypeId、requiredCapabilities、selectorSchema |
| Node binding | 节点 deviceBindings[slot] | 工程 deviceId 及静态 selectors |

描述符扫描不 import 驱动、不执行 I/O；Registry 再检查算子槽要求的类型/能力/selector 是否存在。仅有 JSON 不能证明驱动可用、平台支持或硬件已验收。

### 直接参考可运行的 ticker

仓库 [ticker 示例](../examples/device_service/README.md) 演示非 TCP 后台服务，不依赖真实硬件。以下是它的完整最小 DeviceType，入口对应已有 driver.py：

<!-- example: ticker-device -->
```json
{
  "schemaVersion": "1.1",
  "driverApiVersion": "1.1",
  "deviceTypeId": "device.example.ticker",
  "version": "1.0.0",
  "displayName": "后台计数服务",
  "category": "示例服务",
  "minCoreVersion": "0.6.1",
  "maxCoreVersion": "1.x",
  "platforms": ["windows", "linux"],
  "driverEntry": "examples.device_service.driver:Ticker",
  "connectionSchema": {"type": "object", "properties": {}, "additionalProperties": false},
  "defaultsSchema": {"type": "object", "properties": {}, "additionalProperties": false},
  "policiesSchema": {
    "type": "object", "additionalProperties": false,
    "properties": {
      "connect": {"type": "string", "enum": ["manual", "on_project_open", "on_project_run"], "default": "on_project_run"},
      "release": {"type": "string", "enum": ["on_project_close"], "default": "on_project_close"}
    }
  },
  "operationScheduling": "managed_service",
  "connectionLifetime": "persistent",
  "capabilities": ["sample"],
  "managementActions": ["sample"],
  "actionSchemas": {
    "sample": {
      "type": "object", "additionalProperties": false,
      "properties": {"reject": {"type": "boolean", "default": false}}
    }
  },
  "controlCapabilities": ["connect", "disconnect", "maintenance", "resume", "recover"]
}
```

在仓库根，显式把示例根加入本次扫描；不要把它复制到多个扫描根形成重复算子 ID：

```python
from pathlib import Path
from emo_master import __version__
from emo_master.core.plugin.registry import PluginRegistry

result = PluginRegistry(__version__).scanRoots([
    Path("src/emo_master/plugins"), Path("examples/device_service")
])
assert "device.example.ticker" in result.deviceTypes
assert "example.ticker.sample" in result.activeOperators
assert not result.rejectedDeviceTypes and not result.rejectedOperators
```

配置正式 Runtime 时使用其 pluginRootPaths，同时保证 driverEntry/operator entry 模块可 import；扫描根不是 Python 安装器。`examples/device_plugin` 的 metadata-only 相机与这个 runnable ticker 不同，不能用前者声称驱动已实现。

### 描述符字段规则

| 字段 | 作用与限制 |
| --- | --- |
| connectionSchema | 地址/序列号/身份，属于工程设备，不是节点每次工作参数 |
| defaultsSchema | 曝光、超时、编码等默认工作参数 |
| policiesSchema | 连接/释放/排队/操作期限；只声明实现真正支持的策略 |
| capabilities / actionSchemas | 每个业务能力必须有一个 closed-object 参数 schema，两者名称集合相等 |
| controlCapabilities | connect/disconnect/maintenance/resume/recover/test_connection 的实际支持集合 |
| managementActions | 可在设备管理页发起的业务动作，须为 capabilities 子集 |
| overridePaths | 可被节点覆写的 defaults 叶路径，不是整个 object |
| dynamicPaths | 可绑定变量或输入的标量叶路径，是 overridePaths 子集，不能是文件路径 |
| nextCyclePaths | 已确认周期安全、可在下一周期生效的 overridePaths 子集，不能含文件控件 |
| parameterRules | present/requires/whenOverridden 定义工作参数关联约束 |
| bindingSelectors | 静态通道/子对象来源，声明 itemsPath/valueField/labelField，使用稳定 ID |
| cancellableWait | 指定 action 的 timeoutPath=0 及必需模式，且驱动真正支持取消 |

三种配置 schema 和 actionSchema 都要求 object、properties、additionalProperties=false。支持的关键字以 core/project/snapshots.py 的 `_parameters` 为准，不假定完整 JSON Schema 标准都已实现。

`serial` 是每设备 FIFO；`managed_service` 用于长期后台服务且必须 persistent，其业务访问经本设备准入，控制/生命周期仍串行。`per_operation` 不得声明长期 connect/disconnect 控制。`executionLocation` 当前支持 io_thread/camera_host，后者不是任何新 SDK 都可仅靠一个字符串自动使用的隔离能力。

## 7. 驱动与设备算子

驱动使用公共 [device_driver.py](../src/emo_master/core/contracts/device_driver.py)，不要继承 Runtime 私有基类。构造函数接收 DeviceDefinition，只保留配置，不开设备。

| 接口 | 责任 |
| --- | --- |
| physicalIdentity(definition) | 无 I/O 的资源身份；确实无独占身份时可返回 None |
| bindRuntime(context) | 保存 Runtime/project/load/device/generation 和事件 sink |
| connect(params, cancelled, deadline) | persistent 驱动必需；建立连接，不顺便采集、写 PLC 或发送业务报文 |
| validateAction(action, arguments) | 额外动作参数验证，无副作用 |
| execute(action, params, cancelled, deadline, *, arguments, operationContext) | 一次被 Runtime 准入的动作，遵守实时取消和单调时钟期限 |
| revokeOwner(ownerId) | 撤销指定 owner 的等待/引用，不停止无关 Job 的服务 |
| requestStop(reason) | 请求中断，不在 manager 锁下阻塞等退出 |
| close(deadline) | 释放当前会话，后续可显式重用 |
| dispose(deadline) | 最终释放及解绑事件 |
| snapshot() | 被动返回 ready/ownsResources 等真实状态，不隐式连接 |

`validateDriver` 还有条件要求：capture 需要 frameBytes(params)；test_connection 需要 testConnection；connectionRetryCount 需要 resetConnection 和 connectionRetryCodes。清理失败必须报告，不能只把 connected=False 当成资源已释放。

新增内置驱动若需要冻结发行，核对 `apps/runtime/devices/drivers.py` 的显式 import/BUILTINS 依赖边及构建资源。该表用于冻结依赖发现；第三方 driverEntry 有通用 import 路径，不需要为每种类型加 DeviceManager 分支。

### 槽位声明与动作调用

ticker 算子的 manifest 中声明：

<!-- example: ticker-requirement -->
```json
{
  "deviceRequirements": {
    "service": {
      "deviceTypeId": "device.example.ticker",
      "requiredCapabilities": ["sample"]
    }
  }
}
```

这是 manifest 字段片段。其节点参数只有 reject，schema 使用 xDeviceActionFields=["reject"]，与 `examples/device_service/manifest.json` 和类 meta 保持一致。算子执行调用：

```python
result = runtimeContext["executeDevice"](
    "service", "sample", {"reject": params.get("reject", False)}
)
```

相机使用 `image, descriptor = runtimeContext["captureDevice"]("camera")`；已合成工作参数从 `runtimeContext["deviceParameters"][slot]` 读取。不能返回 SDK 指针，不能在 proxy 缺失时偷偷回退直连硬件。

FrameStore 管理相机预算、原始图像和 descriptor；capture 驱动的 frameBytes 必须覆盖实际格式的内存预算。io_thread capture 可返回图像，或公共 `DeviceCaptureResult(image, metadata)`；当前 camera_host 的 image/metadata 二元组仍兼容。manager 通过 `descriptor.acquisition` 发布采集信息，再由 worker proxy 读取。普通动作 arguments 不能用来绕过图像传输路径。

### 配置写在哪里

| 内容 | 正确归属 |
| --- | --- |
| IP、序列号、连接身份 | device.connection |
| 自动连接/释放、排队和操作策略 | device.policies |
| 默认曝光、增益、编码等 | device.defaults |
| 节点特定的工作值 | node.deviceOverrides[slot] |
| 工作值的变量/输入来源 | node.deviceValueBindings[slot] |
| 本次 PLC 地址、写入数据、动作模式等 | 节点动作参数/端口，经算子组成 action arguments |
| TCP 通道等静态子对象 | deviceBindings[slot].selectors |

缺少覆写才表示继承；0、false、schema 允许的 null 仍是显式值。常量和动态来源不能覆盖同一路径或相互包含。动态变量用 variableId，动态输入用 source=input 与 inputName；selector 不允许动态化。

华睿/SLMP 为历史兼容保留了旧大 schema，编译器用 xDeviceActionFields 拒绝托管节点夹带连接/重试参数。华睿还提供 validateDeviceParams。新托管算子直接定义精简动作 schema，不必复制这些历史参数。

旧 `vision.io.rtsp_camera` 与海康发声仍是独立一次性 I/O。新增的 `vision.io.rtsp_capture` 绑定 `device.camera.rtsp`，由 Runtime 持续解码并仅保留最新帧。旧 RTSP 节点通过迁移预览显式转换，不静默升级或强制替换；发声不随 RTSP 迁移。详见 [RTSP 托管相机](guides/rtsp-managed-camera.md)。

## 8. 工程绑定、资源与发布

以下只是完整工程中的设备和节点字段片段，不是可独立打开的 .emoproj：

<!-- example: ticker-definition -->
```json
{
  "name": "示例计数服务",
  "deviceTypeId": "device.example.ticker",
  "driverVersion": "1.0.0",
  "enabled": true,
  "description": "无真实硬件的开发示例",
  "connection": {},
  "defaults": {},
  "policies": {"connect": "on_project_run", "release": "on_project_close"}
}
```

将该定义放入 devices.definitions["ticker-01"]，节点绑定为：

<!-- example: ticker-binding -->
```json
{
  "deviceBindings": {"service": {"deviceId": "ticker-01", "selectors": {}}},
  "deviceOverrides": {},
  "deviceValueBindings": {}
}
```

DeviceManager 检查 owner、冻结 cycle、device/slot/selectors、能力、版本、平台及参数，然后才允许副作用。算子不再自己维护全局连接缓存、资源锁或第二套周期账本。

设备表单读取 title/xOptionLabels/xUnit/xAdvanced。xDeviceIdentity 为离线身份比对提供声明，xAddressLiteral 约束确需字面 IP 的字段。文件与现场字段通过 xResourcePurpose、xWidget 和 xFileMode 接入既有资源合同；secret_ref 要求带版本的引用，不允许明文，且声明字段不等于凭据解析后端已经实现。

Designer 可以明确保存待绑定草稿，但编译和 Runtime 发布仍严格。不要自动选第一个设备/通道；显式解绑清理该 slot 的覆写/动态源，不影响其它槽、节点和边。

**Apply 修改草稿，Save 保存文件，显式发布才写入 Runtime。** ConfigurationCoordinator 拥有不可变周期快照。普通算子的 nextCycleParameterPaths 与设备的 nextCyclePaths 分开声明；不要直接修改 live driver 的参数。

取消排队工作不能补执行；原生调用未退出前保留真实资源归属；取消一个 Job 不误停其它使用者的长期服务。连接重试不等于可重放业务写入。发送结果未知时保留 receipt/outcome，不能自动重发。

TCP 单次驱动当前在完成 send/exchange/receive 后返回 tcpTransaction。Receive Once 的 bytesSent 包含实际 ACK，bytesReceived 是分帧后的业务数据字节数，不是整个 TCP 流字节数。COMPLETED 只确认此处传输完成，不证明对端业务已执行；解码失败时仍保留该事实。不能因为节点最终 FAILED 或 CANCELLED 而自动再发一次请求/ACK。

设备包/工作流包导入复制按既有逻辑生成禁用设备并清理现场字段；完整 Runtime 安装保持它自己的启用/策略语义，不能混用。详见 [设备目录契约](guides/device-catalog-contract-v1.md) 和 [设备与在线配置](guides/device-dispatch-online-configuration.md)。

新建工程必须使用当前正式格式，通过 WorkflowStore().toPayload() 创建完整默认结构，ProjectDocument 校验，saveProject 保存并 loadProjectDocument 重读，再编译。当前为 2.7，devices.schemaVersion 为 1.0；无设备 definitions 为空，不写旧顶层 communication。不要仅改版本号或删除配置伪装迁移，见 [工程格式规范](project-json-spec.md)。

## 9. 调试与验收

正式工作流、pure/live 预览、单算子调试是三种不同准入。注册成功不自动意味着调试器支持，也不自动意味着可以在现场执行。

当前 operator_debug/contracts.py 的 trustedEntries 核对来源/版本/entry/合同，并排除有 deviceRequirements 的算子；不以历史文档中 47/60 等数字判断当前能力。文件/模型/写入调试要有资源 adapter 和明确执行警告，不能只把目录加入白名单。

### 按改动选择测试

| 影响范围 | 应验证的正常路径和直接反例 |
| --- | --- |
| 普通算子 | 正常/无效输入、参数、输出类型、空结果、输入不被污染；注册及一条编译执行链 |
| 可选输出 | 当次缺失、下次恢复、其它输入已到仍不误执行副作用、工作流必需输出仍受约束 |
| 多选一入口 | 显式对象、旧节点唯一连线、动态对象绑定；缺输出传播、空集合、未连接与冲突仍按各自合同处理 |
| 状态/文件/模型 | 同节点复用、不同节点/Job 隔离、reset、最后一项、取消、关闭及切换资源 |
| 外部副作用 | 操作成功后解码/输出校验失败或取消，仍保留本次回执；部分保存不删除、不补执行、不串用前次记录 |
| 描述符/绑定 | 缺类型/能力、错误版本/selector、继承/常量/动态来源、离线无 I/O |
| 驱动 | 模拟/fake SDK、每设备串行或服务调度、一次断连、取消、释放、真实状态与管理动作 |
| UI | schema 回退、绑定/解绑/待绑定、模式切换、错误定位、保存重开、撤销、draft/save/publish 分离 |
| 模型显示 | 原始判定与热图/高亮分离；OK、NG、均匀响应、输出关闭与隐藏参数草稿；不把合成图验证称作真实模型验收 |

示例算法单测至少包含：

<!-- example: scale-test -->
```python
from emo_master.plugins.builtins.example_scale.operator import ScaleOperator


def testScale():
    op = ScaleOperator()
    assert op.executeNode({"value": 3.0}, {"factor": 4.0}, {})["outputs"] == {"value": 12.0}
    assert op.executeNode({"value": 3}, {}, {})["outputs"]["value"] == 6.0
    assert op.executeNode({}, {}, {})["error"]["code"] == "E_INPUT_MISSING"
    assert op.executeNode({"value": True}, {}, {})["error"]["code"] == "E_INPUT_TYPE"
    assert op.executeNode({"value": 1}, {"factor": float("nan")}, {})["error"]["code"] == "E_PARAM_INVALID"
    assert op.executeNode({"value": 1e308}, {"factor": 2.0}, {})["error"]["code"] == "E_RESULT_INVALID"
```

项目 Python 3.10 环境下，在仓库根按影响面选取：

```powershell
python -m pytest -q tests/core/plugin tests/runtime/test_operator_registration.py --tb=short
python -m pytest -q tests/runtime/test_operator_port_specs.py tests/runtime/test_operator_output_contracts.py tests/runtime/test_operator_lifecycle.py --tb=short
python -m pytest -q tests/plugins/test_managed_tcp_operators.py tests/runtime/test_device_manager.py tests/runtime/test_device_contract_closure.py --tb=short
```

单个算子修复优先执行它自己的 tests/plugins 测试，再补直接受影响的工作流/Designer 测试，不要求每次跑所有设备。新增驱动还参考 test_device_executor.py、test_device_lifecycle.py；参数 UI 参考 tests/designer/test_operator_modes.py、test_parameter_titles.py、test_device_catalog.py。

本轮新增行为的针对性入口：`tests/runtime/test_operator_error_propagation.py`、`test_operator_executor_shared.py`，`tests/plugins/test_communication_operators.py`、`test_detection_archive_operator.py`、`test_collection_select_dynamic_index.py`、`test_patchcore_highlight.py`。浮动运行面板和结果入口看 `tests/designer/test_floating_run_panel.py`、`test_node_image_viewer.py`；PatchCore 参数看 `tests/designer/test_patchcore_inference.py`。

tests/conftest.py 使用临时 Runtime 数据目录。真实相机 smoke 是 opt-in，未经授权不要设置 HUARAY_CAMERA_SMOKE=1；普通测试使用临时文件、模拟设备和回环服务。

本文的代码块由 [文档示例回归](../tests/core/plugin/test_operator_development_examples.py) 直接读取验证，覆盖 Scale 的注册、单测、2.7 工程保存重读与执行，以及 ticker 描述符、驱动接口和绑定编译。修改示例后运行 `python -m pytest -q tests/core/plugin/test_operator_development_examples.py`，不会向内置目录安装示例算子，也不会连接真实设备。

报告分别写明静态检查、源码测试、Qt offscreen、原生 Qt、人工 UI、冻结 EXE、真实设备结果。跳过不算通过。运行 WorkflowRunner 不等于“Designer 已打开”；软件模拟不等于硬件验收。

## 10. 常见问题定位

| 现象 | 优先检查 |
| --- | --- |
| 算子未出现在 Designer | manifest 是否被真实 Runtime 根扫描；rejectedOperators；RPC 会话与目录状态 |
| 设备类型未出现 | device_types 路径、schema/API 版本、closed schema、类型冲突、核心版本范围 |
| 算子注册但编译失败 | 节点参数、必需端口、deviceBindings 完整性、driverVersion、selector、xDeviceActionFields |
| 描述符成功但执行失败 | driverEntry 可 import、platforms、validateDriver 条件方法、正常生命周期及 fake 测试 |
| 参数 UI 可填但运行无效 | 参数归属错误；模式字段不适用；动态源与常量混用；只改 JSON 没同步 meta |
| 模型/文件在本地有但 Runtime 找不到 | 路径属于哪台机器；workspacePath；是否完成资源上传/打包，不能把 Designer 本地路径当远端上传 |
| 关闭窗口后设备还连接 | 窗口是观察者；连接归 Runtime 策略，不应因关窗口中断生产 |
| 超时后想自动重发写入 | 先判定 outcome；未知副作用禁止自动重放，连接恢复不等于业务重试 |
| 调试器提示不支持 | 它有独立准入/资源适配；不能为绕过拒绝移除设备要求 |
| 集合节点输出 0 或跳过不符合预期 | 区分实际空集合与未产出；检查 requiredChoiceInput、所选对象、业务连线和 missingInputs |
| 任务失败但文件或报文已产生 | 查看本次 archiveProgress / tcpTransaction / receipt；失败不自动撤销副作用，不据此重试 |
| PatchCore 的 OK 图有红色，或 NG 图没有轮廓 | 区分 heatmap 与 highlight；看 score/threshold、overlayMode 和 localization.status，不用颜色替代模型判定 |
| 查看结果没有展开面板 | 应经 NodeResultCoordinator.showPanel()；检查浮层展开和滚动定位，不新建缓存/订阅 |

## 11. 维护入口

- [插件注册实现](../src/emo_master/core/plugin/registry.py)、[校验器](../src/emo_master/core/plugin/validator.py)、[DeviceType](../src/emo_master/core/plugin/devices.py)。
- [工作流编译器](../src/emo_master/core/workflow/compiler.py)、[节点执行器](../src/emo_master/apps/runtime/execution/operator_executor.py)、[DeviceManager](../src/emo_master/apps/runtime/devices/manager.py)。
- [公共驱动合同](../src/emo_master/core/contracts/device_driver.py)、[模拟相机](../src/emo_master/apps/runtime/devices/simulated.py)、[ticker 完整示例](../examples/device_service/driver.py)。
- [单算子调试准入](../src/emo_master/apps/runtime/operator_debug/contracts.py)、[EditorContext](../src/emo_master/apps/designer/operator_editors/controller_protocol.py)。
- [输入选择与模式规则](../src/emo_master/plugins/builtins/_operator_modes.py)、[TCP 驱动](../src/emo_master/apps/runtime/devices/tcp.py)、[检测归档](../src/emo_master/plugins/builtins/detection_archive/operator.py)。
- [浮动运行面板](../src/emo_master/apps/designer/ui/floating_run_panel.py)、[节点结果协调器](../src/emo_master/apps/designer/ui/node_result_coordinator.py)、[PatchCore 专项说明](patchcore-inference.md)。
- [参数标题约定](operator-parameter-titles.md)、[输入输出合同](operator-io-contracts.md)、[工程格式](project-json-spec.md)、[设备目录合同](guides/device-catalog-contract-v1.md)。

本指南与 `emo-master-operator-authoring` skill 对应。skill 提供按任务读取的指引和只读盘点脚本，本指南提供可独立阅读的开发说明。升级合同后同步相关条目，以源码与执行结果为依据，不把旧快照中的数量、准入和版本号作为永久事实。

安装该 skill 后可通过 `$emo-master-operator-authoring` 调用。重新生成目录时，在仓库根运行以下命令，`<skill-dir>` 替换为 skill 的实际目录；不要求未安装 skill 的开发者依赖某台机器的个人路径：

```powershell
python <skill-dir>/scripts/inspect_catalog.py --repo . --check-registration --format markdown --output docs/operator-catalog.md
```

默认不传 `--check-registration` 时仅做 JSON/AST 解析；该选项会导入已信任的算子模块，不执行算子或实例化设备驱动。仅指定 `--output` 才写报告。

### 2026-10-24 本轮核对结果

| 检查 | 实际结果 |
| --- | --- |
| 全部内置插件注册 | 77 个算子、6 种设备类型；无 rejectedOperators/rejectedDeviceTypes，无图标/编辑器诊断 |
| 源码与合同回归 | 1452 passed、2 skipped，包含本文 3 个可执行示例和下列新增行为测试 |
| Designer Qt offscreen | 180 passed；覆盖浮层、结果入口、模式、PatchCore/YOLO 参数和工作流导航 |
| 文档与 skill | skill 格式校验通过；目录文件摘要与当前扫描相符；文档/skill 本地链接检查通过 |

源码回归命令（Python 3.10；本次进程环境设置 HUARAY_CAMERA_SMOKE=0、QT_QPA_PLATFORM=offscreen）：

```powershell
python -m pytest -q tests/plugins tests/core/plugin tests/core/contracts/test_operator_contracts.py `
  tests/runtime/test_operator_registration.py tests/runtime/test_operator_output_contracts.py `
  tests/runtime/test_operator_lifecycle.py tests/runtime/test_operator_modes_workflow.py `
  tests/runtime/test_optional_output_execution.py tests/runtime/test_device_manager.py `
  tests/runtime/test_device_contract_closure.py tests/runtime/test_tcp_device_integration.py `
  tests/runtime/test_patchcore_debug.py tests/runtime/test_patchcore_workflow.py `
  tests/runtime/test_operator_error_propagation.py tests/runtime/test_operator_executor_shared.py `
  tests/runtime/test_worker_capture_diagnostics.py tests/runtime/test_runtime_workflow_architecture.py -ra --tb=short
```

Designer 单独进程的 offscreen 回归：

```powershell
python -m pytest -q tests/designer/test_floating_run_panel.py tests/designer/test_floating_toolbox.py `
  tests/designer/test_main_window_layout.py tests/designer/test_node_image_viewer.py `
  tests/designer/test_node_context_menu.py tests/designer/test_operator_modes.py `
  tests/designer/test_patchcore_inference.py tests/designer/test_yolo_parameter_presentation.py `
  tests/designer/test_workflow_navigation.py -ra --tb=short
```

两组测试前后摘要均为 `009e205e6e9f61d9bcb14868aa4ffc610a2733baa874ce3fe81ae719b70b8831`。计算方式与下方历史记录相同，覆盖 sourceSnapshot 纳入的 src/proto/tests/scripts 文件；期间这些文件没有变化。文档、examples、依赖声明和运行环境不在该文件摘要内，不把它冒充整个工作区/安装环境的完整指纹。

两项跳过仍是真实 IMV 相机 smoke 和当前环境不可用的符号链接测试。以上不是全仓库测试，也不代表人工 UI、原生 Qt 截图、冻结 EXE 或真实设备验收。本轮只更新文档/skill 与生成目录，未修改业务代码、测试实现或现场配置；未提交、推送或打包。

### 2026-10-23 历史核对结果

以下保留上次记录，不用它代表 2026-10-24 当前工作副本通过测试。两次 HEAD 相同，但工作区内容不同；本轮结果单独记录。

2026-10-23 在 Python 3.10 工作副本完成相关回归：**1258 passed、2 skipped**，包含本文三个示例测试、全部 tests/plugins 与 tests/core/plugin，以及下列 Runtime/执行协议测试：

```powershell
python -m pytest -q tests/plugins tests/core/plugin tests/core/contracts/test_operator_contracts.py `
  tests/runtime/test_operator_registration.py tests/runtime/test_operator_output_contracts.py `
  tests/runtime/test_operator_lifecycle.py tests/runtime/test_operator_modes_workflow.py `
  tests/runtime/test_optional_output_execution.py tests/runtime/test_device_manager.py `
  tests/runtime/test_device_contract_closure.py tests/runtime/test_tcp_device_integration.py `
  tests/runtime/test_patchcore_debug.py tests/runtime/test_patchcore_workflow.py -ra --tb=short
```

测试前后 src/proto/tests/scripts 文件哈希映射摘要均为 `4c48cc16b8040a5c506787f5489d0a848f0ecf5f5d61704eb900101ea009ba09`，期间无源码变化；摘要使用 `scripts/operator_step_validate.py:sourceSnapshot()` 的 sha256 映射排序序列化后计算。此次仅调用快照函数，不执行该历史脚本的报告写入主流程。

两个跳过项是未经授权不运行的真实 IMV 相机 smoke、当前环境不可用的符号链接测试。Qt 相关用例使用 offscreen；未做人工 UI、原生 Qt 截图、冻结 EXE 或真实设备验收，也没有提交、打包、部署或改动业务实现。该结果仅代表本次列出的测试，不是整个仓库测试全通过；后续局部开发仍按影响面选择验证。
