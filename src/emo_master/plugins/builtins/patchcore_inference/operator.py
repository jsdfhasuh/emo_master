from __future__ import annotations

from pathlib import Path
from time import perf_counter

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import PayloadValidationError
from emo_master.plugins.builtins._image_frame import frameForInput
from emo_master.plugins.builtins._operator_modes import modeSchema
from emo_master.plugins.builtins.image_loader.operator import OperatorMeta

from .bundle import BackendUnavailableError, ModelError, SCORE_KIND
from .numeric import decision, restore
from .onnx_backend import InferenceError, PatchCoreSession, ResultError
from .visualization import highlightOverlay


class PatchCoreInferenceOperator:
    meta = OperatorMeta(
        operatorId="vision.inference.patchcore", displayName="PatchCore Inference", version="1.1.0",
        inputPorts={
            "image": {"type": "image", "required": True, "nullable": False},
            "frame": {"type": "bbox2d", "required": False, "nullable": False, "schemaVersion": "1.x"},
        },
        outputPorts={
            "score": {"type": "number", "required": True, "nullable": False},
            "threshold": {"type": "number", "required": True, "nullable": False},
            "isAnomaly": {"type": "boolean", "required": True, "nullable": False},
            "label": {"type": "string", "required": True, "nullable": False},
            "heatmap": {"type": "image", "required": False, "nullable": False},
            "overlay": {"type": "image", "required": False, "nullable": False},
            "anomalyMap": {"type": "object", "required": False, "nullable": False},
            "frame": {"type": "bbox2d", "required": True, "nullable": False, "schemaVersion": "1.x"},
        },
        paramSchema=modeSchema("vision.inference.patchcore", {
            "type": "object",
            "properties": {
                "modelPath": {
                    "title": "模型部署目录", "type": "string", "default": "",
                    "xWidget": "file", "xFileMode": "directory",
                    "description": "Runtime 所在电脑上的完整 DINO ONNX 部署目录，或包含 transfer.json 和 payload 的导出目录；不会自动上传。",
                },
                "drawHeatmap": {
                    "title": "输出彩色热图", "type": "boolean", "default": True,
                    "description": "每张图相对响应的彩色显示，不是像素缺陷判定。",
                },
                "drawOverlay": {"title": "输出叠加图", "type": "boolean", "default": True},
                "overlayMode": {
                    "title": "叠加显示", "type": "string", "default": "heatmap",
                    "enum": ["heatmap", "highlight"],
                    "xOptionLabels": {"heatmap": "整图热图", "highlight": "疑似缺陷高亮"},
                    "description": "高亮模式仅标出 NG 图中的相对高响应位置；OK 图保留原图。不是像素级缺陷判定。",
                },
                "highlightThreshold": {
                    "title": "高亮相对阈值", "type": "number", "default": .7,
                    "exclusiveMinimum": 0, "maximum": 1,
                    "description": "仅用于显示，范围 (0, 1]；数值越大，高亮范围越小。不修改模型判定阈值。",
                },
                "outputAnomalyMap": {
                    "title": "输出原始异常数值", "type": "boolean", "default": False,
                    "description": "输出原始 256×256 float32 数值和几何还原信息；不进行色彩归一化。",
                },
            },
            "required": ["modelPath"],
        }),
    )

    def __init__(self):
        self._session = None
        self._sessionKey = None

    def initOperator(self, initContext):
        self.disposeOperator()

    def disposeOperator(self):
        if self._session is not None:
            self._session.close()
        self._session = None
        self._sessionKey = None

    def validateParams(self, params):
        path = params.get("modelPath")
        if not isinstance(path, str) or not path.strip() or "\x00" in path:
            return {"code": "E_PARAM_INVALID", "message": "请选择 Runtime 可访问的完整模型部署目录"}
        for name, default in (("drawHeatmap", True), ("drawOverlay", True), ("outputAnomalyMap", False)):
            if not isinstance(params.get(name, default), bool):
                return {"code": "E_PARAM_INVALID", "message": name + " must be boolean"}
        if params.get("drawOverlay", True):
            mode = params.get("overlayMode", "heatmap")
            if mode not in ("heatmap", "highlight"):
                return {"code": "E_PARAM_INVALID", "message": "请选择有效的叠加显示模式"}
            threshold = params.get("highlightThreshold", .7)
            if mode == "highlight" and (type(threshold) not in (int, float) or not 0 < threshold <= 1):
                return {"code": "E_PARAM_INVALID", "message": "高亮相对阈值必须大于 0 且不超过 1（仅用于显示）"}
        return None

    def executeNode(self, inputs, params, runtimeContext):
        started = perf_counter()
        if "image" not in inputs:
            return _error("E_INPUT_MISSING", "input 'image' is required")
        image = inputs["image"]
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8:
            return _error("E_INPUT_TYPE", "image must be a uint8 numpy.ndarray")
        if (image.size == 0 or image.ndim not in (2, 3)
                or (image.ndim == 3 and image.shape[2] != 3)
                or image.shape[0] * image.shape[1] > 40_000_000):
            return _error("E_INPUT_SHAPE", "image must be GRAY/BGR with at most 40 million pixels")
        try:
            frame = frameForInput(inputs, image.shape[1], image.shape[0])
        except (PayloadValidationError, ValueError) as error:
            return _error("E_INPUT_SHAPE", str(error))
        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}
        checkCancelled = runtimeContext.get("raiseIfCancellationRequested")
        if not callable(checkCancelled):
            def checkCancelled():
                pass
        checkCancelled()
        path = Path(params["modelPath"].strip()).expanduser()
        if not path.is_absolute() and runtimeContext.get("workspacePath"):
            path = Path(runtimeContext["workspacePath"]) / path
        try:
            path = path.resolve()
            key = (runtimeContext.get("jobId"), str(path))
            if key != self._sessionKey:
                self.disposeOperator()
                if not path.is_dir():
                    return _error("E_MODEL_NOT_FOUND", "模型部署目录不存在（需要完整目录而非单个 ONNX 文件）：" + str(path))
                try:
                    self._session = PatchCoreSession(path, checkCancelled)
                except BackendUnavailableError as error:
                    return _error("E_BACKEND_UNAVAILABLE", str(error))
                except ModelError as error:
                    return _error("E_MODEL_LOAD_FAILED", str(error))
                self._sessionKey = key
            checkCancelled()
            rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB if image.ndim == 2 else cv2.COLOR_BGR2RGB)
            prediction = self._session.predict(rgb)
            checkCancelled()
            deployment = self._session.deployment
            isAnomaly = decision(prediction.score, deployment.threshold)
            outputs = {"score": prediction.score, "threshold": deployment.threshold,
                       "isAnomaly": isAnomaly, "label": "NG" if isAnomaly else "OK", "frame": frame.toPayload()}
            messages = {}
            visualization = {}
            if params.get("drawHeatmap", True) or params.get("drawOverlay", True):
                heat = restore(prediction.rawMap, prediction.geometry)
                mode = params.get("overlayMode", "heatmap")
                if params.get("drawHeatmap", True) or mode == "heatmap":
                    display = (255 * (heat - heat.min()) / max(float(heat.max() - heat.min()), 1e-12)).astype(np.uint8)
                    color = cv2.applyColorMap(display, cv2.COLORMAP_JET)
                if params.get("drawHeatmap", True):
                    outputs["heatmap"] = color
                if params.get("drawOverlay", True):
                    bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if image.ndim == 2 else image
                    if mode == "highlight":
                        threshold = params.get("highlightThreshold", .7)
                        if isAnomaly:
                            outputs["overlay"], localized = highlightOverlay(bgr, heat, threshold)
                            state = "highlighted" if localized else "uniform_response"
                            message = ("仅标示相对高响应位置，未经像素级校准。" if localized else
                                       "响应近似均匀，未标示局部位置；图像判定仍为 NG。")
                        else:
                            outputs["overlay"] = bgr.copy()
                            state, message = "not_anomalous", "图像判定为 OK，高亮叠加图保留原图。"
                        visualization = {"localization": {"method": "per_image_relative_response",
                            "relativeThreshold": threshold, "status": state, "message": message}}
                    else:
                        outputs["overlay"] = cv2.addWeighted(bgr, .6, color, .4, 0)
                    visualization["overlayMode"] = mode
            for parameter, port, title in (("drawHeatmap", "heatmap", "输出彩色热图"),
                                            ("drawOverlay", "overlay", "输出叠加图")):
                if not params.get(parameter, True):
                    messages[port] = f"此次执行未启用“{title}”，未生成 {port}。"
            if params.get("outputAnomalyMap", False):
                outputs["anomalyMap"] = {"shape": [256, 256], "dtype": "float32",
                                         "values": prediction.rawMap[0, 0].tolist(), "geometry": prediction.geometry}
            checkCancelled()
            return {"status": "ok", "outputs": outputs,
                    "metrics": {"latencyMs": round((perf_counter() - started) * 1000, 3)},
                    "diagnostics": {"text": f"PatchCore {outputs['label']}: {prediction.score:.8g} / {deployment.threshold:.8g}",
                        "backend": "onnxruntime", "provider": "CPUExecutionProvider", "optimization": "BASIC",
                        "modelPath": str(deployment.root), "manifestSha256": deployment.manifestSha256,
                        "profile": deployment.manifest["profile"], "scoreKind": SCORE_KIND,
                        "thresholdSource": "deployment", "imageOutputMessages": messages, **visualization}}
        except ResultError as error:
            return _error("E_RESULT_INVALID", str(error))
        except InferenceError as error:
            return _error("E_INFERENCE_FAILED", str(error))
        except (OSError, ValueError, cv2.error) as error:
            return _error("E_INFERENCE_FAILED", str(error))


def _error(code, message):
    return {"status": "error", "error": {"code": code, "message": message}}
