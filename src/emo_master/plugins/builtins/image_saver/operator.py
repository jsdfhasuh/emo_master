from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, str]
    outputPorts: dict[str, str]
    paramSchema: dict[str, object]


class ImageSaverOperator:
    meta = OperatorMeta(
        operatorId="vision.io.image_saver",
        displayName="Image Saver",
        version="1.0.0",
        inputPorts={"image": "image"},
        outputPorts={"result": "json"},
        paramSchema={
            "type": "object",
            "properties": {
                "outputPath": {
                    "type": "string",
                    "default": "",
                    "xWidget": "file",
                    "xFileMode": "save",
                    "xFilter": "图片文件 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)",
                },
                "overwrite": {"type": "boolean", "default": True},
            },
            "required": ["outputPath"],
        },
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        outputPath = params.get("outputPath", "")
        overwrite = params.get("overwrite", True)
        if not isinstance(outputPath, str) or outputPath.strip() == "":
            return {
                "code": "E_PARAM_INVALID",
                "message": "outputPath must be non-empty string",
            }
        if not isinstance(overwrite, bool):
            return {"code": "E_PARAM_INVALID", "message": "overwrite must be boolean"}
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = runtimeContext
        startAt = perf_counter()

        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}

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

        outputPath = str(params.get("outputPath", "")).strip()
        overwrite = bool(params.get("overwrite", True))
        outputFile = Path(outputPath)

        if outputFile.exists() and not overwrite:
            return {
                "status": "error",
                "error": {
                    "code": "E_OUTPUT_EXISTS",
                    "message": f"output file exists: {outputPath}",
                },
            }

        outputFile.parent.mkdir(parents=True, exist_ok=True)
        writeOk = cv2.imwrite(str(outputFile), image)
        if not writeOk:
            return {
                "status": "error",
                "error": {
                    "code": "E_OUTPUT_WRITE_FAILED",
                    "message": f"failed to write image: {outputPath}",
                },
            }

        elapsedMs = (perf_counter() - startAt) * 1000.0
        return {
            "status": "ok",
            "outputs": {"result": {"saved": True, "path": str(outputFile)}},
            "metrics": {"latencyMs": round(elapsedMs, 3)},
            "diagnostics": {"text": "Image saved"},
        }
