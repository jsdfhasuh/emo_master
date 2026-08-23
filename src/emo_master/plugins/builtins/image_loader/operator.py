from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import cv2


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, str]
    outputPorts: dict[str, str]
    paramSchema: dict[str, object]


class ImageLoaderOperator:
    meta = OperatorMeta(
        operatorId="vision.io.image_loader",
        displayName="Image Loader",
        version="1.0.0",
        inputPorts={},
        outputPorts={"image": "image"},
        paramSchema={
            "type": "object",
            "properties": {
                "imagePath": {
                    "type": "string",
                    "default": "",
                    "xWidget": "file",
                    "xFileMode": "open",
                    "xFilter": "图片文件 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)",
                },
                "colorMode": {
                    "type": "string",
                    "enum": ["color", "grayscale"],
                    "default": "color",
                },
            },
            "required": ["imagePath"],
        },
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        imagePath = params.get("imagePath", "")
        colorMode = params.get("colorMode", "color")

        if not isinstance(imagePath, str) or imagePath.strip() == "":
            return {
                "code": "E_PARAM_INVALID",
                "message": "imagePath must be non-empty string",
            }
        if colorMode not in ("color", "grayscale"):
            return {
                "code": "E_PARAM_INVALID",
                "message": "colorMode must be color or grayscale",
            }
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        _ = inputs
        _ = runtimeContext
        startAt = perf_counter()

        paramError = self.validateParams(params)
        if paramError is not None:
            return {"status": "error", "error": paramError}

        imagePath = str(params.get("imagePath", "")).strip()
        colorMode = str(params.get("colorMode", "color"))

        pathObj = Path(imagePath)
        if not pathObj.exists() or not pathObj.is_file():
            return {
                "status": "error",
                "error": {
                    "code": "E_INPUT_MISSING",
                    "message": f"image file not found: {imagePath}",
                },
            }

        readFlag = (
            cv2.IMREAD_GRAYSCALE if colorMode == "grayscale" else cv2.IMREAD_COLOR
        )
        image = cv2.imread(str(pathObj), readFlag)
        if image is None:
            return {
                "status": "error",
                "error": {
                    "code": "E_INPUT_DECODE",
                    "message": f"failed to decode image: {imagePath}",
                },
            }

        elapsedMs = (perf_counter() - startAt) * 1000.0
        return {
            "status": "ok",
            "outputs": {"image": image},
            "metrics": {"latencyMs": round(elapsedMs, 3)},
            "diagnostics": {"text": "Image loaded"},
        }
