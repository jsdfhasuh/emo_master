from dataclasses import dataclass
from time import perf_counter
from typing import Any, cast

import cv2
import numpy as np

from emo_master.core.contracts.geometry2d import PayloadValidationError
from emo_master.plugins.builtins._image_frame import frameForInput


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, object]
    paramSchema: dict[str, object]


class CannyEdgeOperator:
    meta = OperatorMeta(
        operatorId="vision.edge.canny",
        displayName="Canny Edge",
        version="1.1.0",
        inputPorts={
            "image": {"type": "image", "required": True, "nullable": False},
            "frame": {
                "type": "bbox2d",
                "required": False,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        outputPorts={
            "edges": {"type": "image", "required": True, "nullable": False},
            "overlay": {"type": "image", "required": True, "nullable": False},
            "frame": {
                "type": "bbox2d",
                "required": True,
                "nullable": False,
                "schemaVersion": "1.x",
            },
        },
        paramSchema={
            "type": "object",
            "properties": {
                "thresholdLow": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 255,
                    "default": 50,
                },
                "thresholdHigh": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 255,
                    "default": 150,
                },
                "apertureSize": {"type": "integer", "enum": [3, 5, 7], "default": 3},
                "l2Gradient": {"type": "boolean", "default": False},
            },
            "required": ["thresholdLow", "thresholdHigh"],
        },
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        thresholdLow = params.get("thresholdLow", 50)
        thresholdHigh = params.get("thresholdHigh", 150)
        apertureSize = params.get("apertureSize", 3)

        if not isinstance(thresholdLow, int) or thresholdLow < 0 or thresholdLow > 255:
            return {
                "code": "E_PARAM_INVALID",
                "message": "thresholdLow must be int in [0, 255]",
            }
        if (
            not isinstance(thresholdHigh, int)
            or thresholdHigh < 0
            or thresholdHigh > 255
        ):
            return {
                "code": "E_PARAM_INVALID",
                "message": "thresholdHigh must be int in [0, 255]",
            }
        if thresholdLow >= thresholdHigh:
            return {
                "code": "E_PARAM_INVALID",
                "message": "thresholdLow must be < thresholdHigh",
            }
        if apertureSize not in (3, 5, 7):
            return {
                "code": "E_PARAM_INVALID",
                "message": "apertureSize must be 3, 5 or 7",
            }

        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        startAt = perf_counter()

        if "image" not in inputs:
            return {
                "status": "error",
                "error": {
                    "code": "E_INPUT_MISSING",
                    "message": "input 'image' is required",
                },
            }

        image = inputs["image"]
        if not isinstance(image, np.ndarray):
            return {
                "status": "error",
                "error": {
                    "code": "E_INPUT_TYPE",
                    "message": "input 'image' must be numpy.ndarray",
                },
            }
        if image.dtype != np.uint8:
            return {
                "status": "error",
                "error": {
                    "code": "E_INPUT_TYPE",
                    "message": "input 'image' must use uint8 pixels",
                },
            }
        if image.size == 0:
            return {
                "status": "error",
                "error": {
                    "code": "E_INPUT_SHAPE",
                    "message": "image must be non-empty",
                },
            }

        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}

        thresholdLow = cast(int, params.get("thresholdLow", 50))
        thresholdHigh = cast(int, params.get("thresholdHigh", 150))
        apertureSize = cast(int, params.get("apertureSize", 3))
        l2Gradient = cast(bool, params.get("l2Gradient", False))

        if image.ndim == 3 and image.shape[2] == 3:
            grayImage = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            overlayImage = image.copy()
        elif image.ndim == 2:
            grayImage = image
            overlayImage = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        else:
            return {
                "status": "error",
                "error": {
                    "code": "E_INPUT_SHAPE",
                    "message": "image must be grayscale or BGR",
                },
            }

        height, width = grayImage.shape
        try:
            frame = frameForInput(inputs, width, height)
        except PayloadValidationError as err:
            return {
                "status": "error",
                "error": {
                    "code": "E_INPUT_TYPE",
                    "message": f"invalid frame payload: {err}",
                },
            }
        except ValueError as err:
            return {
                "status": "error",
                "error": {"code": "E_INPUT_SHAPE", "message": str(err)},
            }

        edgeImage = cv2.Canny(
            grayImage,
            threshold1=thresholdLow,
            threshold2=thresholdHigh,
            apertureSize=apertureSize,
            L2gradient=l2Gradient,
        )
        overlayImage[edgeImage > 0] = (0, 255, 0)

        elapsedMs = (perf_counter() - startAt) * 1000.0
        return {
            "status": "ok",
            "outputs": {
                "edges": edgeImage,
                "overlay": overlayImage,
                "frame": frame.toPayload(),
            },
            "metrics": {"latencyMs": round(elapsedMs, 3)},
            "diagnostics": {"text": "Canny edge detection completed"},
        }
