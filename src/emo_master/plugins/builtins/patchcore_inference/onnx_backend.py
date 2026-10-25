from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .bundle import BackendUnavailableError, ModelError, loadDeployment
from .numeric import preprocess


class InferenceError(RuntimeError):
    pass


class ResultError(ValueError):
    pass


@dataclass(frozen=True)
class InferenceResult:
    score: float
    rawMap: np.ndarray
    geometry: dict


class PatchCoreSession:
    def __init__(self, directory, checkCancelled=lambda: None):
        self.session = None
        self.deployment = loadDeployment(directory, checkCancelled)
        try:
            import onnxruntime as ort
        except ImportError as error:
            raise BackendUnavailableError("PatchCore requires onnxruntime==1.23.2 in the Runtime environment") from error
        if ort.__version__ != "1.23.2" or "CPUExecutionProvider" not in ort.get_available_providers():
            raise BackendUnavailableError("PatchCore requires ONNX Runtime 1.23.2 with CPUExecutionProvider")
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        checkCancelled()
        try:
            self.session = ort.InferenceSession(str(self.deployment.root / "model.onnx"), options,
                                                providers=["CPUExecutionProvider"])
            if self.session.get_providers() != ["CPUExecutionProvider"]:
                raise ModelError("unexpected PatchCore execution provider")
            inputs = [(p.name, p.shape, p.type) for p in self.session.get_inputs()]
            outputs = [(p.name, p.shape, p.type) for p in self.session.get_outputs()]
            if (inputs != [("image", [1, 3, 256, 256], "tensor(float)")]
                    or outputs != [("pred_score", [1], "tensor(float)"),
                                   ("anomaly_map", [1, 1, 256, 256], "tensor(float)")]):
                raise ModelError("invalid PatchCore ONNX tensor interface")
        except Exception as error:
            self.close()
            raise ModelError(f"cannot load PatchCore ONNX model ({type(error).__name__}): {error}") from error
        try:
            checkCancelled()
        except BaseException:
            self.close()
            raise

    def predict(self, rgb):
        tensor, geometry = preprocess(rgb)
        if self.session is None:
            raise InferenceError("PatchCore session is closed")
        try:
            result = self.session.run(["pred_score", "anomaly_map"], {"image": tensor})
        except Exception as error:
            raise InferenceError(f"PatchCore ONNX inference failed: {error}") from error
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise ResultError("PatchCore must return exactly one score and one anomaly map")
        score, rawMap = result
        if (not isinstance(score, np.ndarray) or not isinstance(rawMap, np.ndarray)
                or score.dtype != np.float32 or rawMap.dtype != np.float32
                or score.shape != (1,) or rawMap.shape != (1, 1, 256, 256)
                or not np.isfinite(score).all() or not np.isfinite(rawMap).all()):
            raise ResultError("PatchCore output must be finite FP32 score[1] and anomaly_map[1,1,256,256]")
        return InferenceResult(float(score[0]), rawMap, geometry)

    def close(self):
        self.session = None
