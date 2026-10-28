from __future__ import annotations

import ast
from pathlib import Path

import cv2
import numpy as np

from emo_master.plugins.builtins.yolo_inference.onnx_backend import (
    OnnxBackendUnavailableError,
    OnnxInferenceError,
    OnnxModelError,
    OnnxResultError,
)


class OnnxClassificationSession:
    """The training platform's resize_full_frame_v1 probability contract."""

    def __init__(self, modelPath: Path) -> None:
        try:
            import onnxruntime as ort
        except ImportError as error:
            raise OnnxBackendUnavailableError(
                f"YOLO classification could not load onnxruntime: {type(error).__name__}: {error}"
            ) from error
        try:
            from PIL import Image
        except ImportError as error:
            raise OnnxBackendUnavailableError(
                f"YOLO classification could not load Pillow (PIL.Image): {type(error).__name__}: {error}"
            ) from error
        self._imageModule = Image
        try:
            options = ort.SessionOptions()
            options.add_session_config_entry("session.intra_op.allow_spinning", "0")
            options.add_session_config_entry("session.inter_op.allow_spinning", "0")
            self._session = ort.InferenceSession(
                str(modelPath), sess_options=options, providers=["CPUExecutionProvider"]
            )
        except Exception as error:
            raise OnnxModelError(f"failed to load classification ONNX: {error}") from error
        inputs, outputs = self._session.get_inputs(), self._session.get_outputs()
        if len(inputs) != 1 or len(outputs) != 1:
            raise OnnxModelError("classification model must have one input and one output")
        modelInput, modelOutput = inputs[0], outputs[0]
        shape = modelInput.shape
        if (modelInput.type != "tensor(float)" or len(shape) != 4
                or shape[0] not in (1, None, "batch") or shape[1] != 3
                or any(type(size) is not int or size < 1 for size in shape[2:])):
            raise OnnxModelError("classification input must be float32 [1,3,H,W] with fixed H,W")
        self.height, self.width = shape[2:]
        metadata = self._session.get_modelmeta().custom_metadata_map or {}
        if metadata.get("task") != "classify":
            raise OnnxModelError("model must declare task=classify; detection models are unsupported")
        try:
            names = ast.literal_eval(metadata.get("names", ""))
        except (SyntaxError, ValueError) as error:
            raise OnnxModelError("classification model has no valid class names") from error
        if (not isinstance(names, dict) or not names
                or any(type(key) is not int for key in names)
                or set(names) != set(range(len(names)))
                or any(not isinstance(name, str) or not name.strip() for name in names.values())
                or len(set(names.values())) != len(names)):
            raise OnnxModelError("class names must be non-empty, unique and indexed from zero")
        self.names = [names[index] for index in range(len(names))]
        if (modelOutput.type != "tensor(float)" or len(modelOutput.shape) != 2
                or modelOutput.shape[0] not in (1, None, "batch")
                or modelOutput.shape[1] != len(self.names)):
            raise OnnxModelError("classification output must be float32 [1, classCount]")
        self._inputName, self._outputName = modelInput.name, modelOutput.name

    def close(self) -> None:
        self._session = None

    def prepareInput(self, image: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB if image.ndim == 2 else cv2.COLOR_BGR2RGB)
        # Match torchvision Resize((H,W), BILINEAR) on PIL, not OpenCV resize
        # or the detection model's letterbox. Both differ from training pixels.
        pil = self._imageModule.fromarray(rgb)
        resized = pil.resize((self.width, self.height), self._imageModule.Resampling.BILINEAR)
        tensor = np.asarray(resized, dtype=np.float32).transpose(2, 0, 1) / np.float32(255)
        return np.ascontiguousarray(tensor[None])

    def predict(self, image: np.ndarray) -> np.ndarray:
        tensor = self.prepareInput(image)
        try:
            outputs = self._session.run([self._outputName], {self._inputName: tensor})
        except Exception as error:
            raise OnnxInferenceError(f"classification inference failed: {error}") from error
        if len(outputs) != 1:
            raise OnnxResultError("classification must return one probability tensor")
        probabilities = np.asarray(outputs[0])
        if (probabilities.shape != (1, len(self.names))
                or not np.issubdtype(probabilities.dtype, np.floating)
                or not np.all(np.isfinite(probabilities))
                or np.any(probabilities < 0) or np.any(probabilities > 1)
                or not np.isclose(probabilities.sum(), 1.0, atol=1e-4, rtol=0)):
            raise OnnxResultError("classification output must contain normalized probabilities")
        # YOLO classification exports already contain softmax. Never apply it twice.
        return probabilities[0]
