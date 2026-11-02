from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import cv2
import numpy as np


@dataclass(frozen=True)
class OperatorMeta:
    operatorId: str
    displayName: str
    version: str
    inputPorts: dict[str, object]
    outputPorts: dict[str, str]
    paramSchema: dict[str, object]


class ImageSaverOperator:
    meta = OperatorMeta(
        operatorId="vision.io.image_saver",
        displayName="Image Saver",
        version="1.2.0",
        inputPorts={
            "image": "image",
            "fileName": {"type": "string", "required": False, "nullable": False},
        },
        outputPorts={"result": "json"},
        paramSchema={
            "type": "object",
            "properties": {
                "outputPath": {
                    "title": "输出路径",
                    "description": "未连接 fileName 时使用完整路径；连接后使用此路径的所在目录，文件名由输入提供，未带扩展名时沿用此路径的扩展名。",
                    "type": "string",
                    "default": "",
                    "xWidget": "file",
                    "xFileMode": "save",
                    "xFilter": "图片文件 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)",
                },
                "namingMode": {
                    "title": "文件命名",
                    "description": "固定名称沿用 outputPath 或 fileName 输入；自动唯一命名在该文件名主体后追加 UTC 时间和唯一标识，保留目录和扩展名，每次创建新文件，不覆盖已有文件。",
                    "type": "string", "enum": ["fixed", "unique"], "default": "fixed",
                    "xOptionLabels": {"fixed": "固定名称", "unique": "自动唯一命名"},
                },
                "overwrite": {"title": "覆盖已有文件", "description": "仅固定名称模式使用；自动唯一命名始终创建新文件。",
                              "type": "boolean", "default": True, "xEnabledWhen": {"namingMode": ["fixed"]}},
            },
            "required": ["outputPath"],
        },
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        outputPath = params.get("outputPath", "")
        overwrite = params.get("overwrite", True)
        namingMode = params.get("namingMode", "fixed")
        if not isinstance(outputPath, str) or outputPath.strip() == "":
            return {
                "code": "E_PARAM_INVALID",
                "message": "outputPath must be non-empty string",
            }
        if not isinstance(overwrite, bool):
            return {"code": "E_PARAM_INVALID", "message": "overwrite must be boolean"}
        if namingMode not in ("fixed", "unique"):
            return {"code": "E_PARAM_INVALID", "message": "namingMode must be fixed or unique"}
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
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
        if "fileName" in inputs:
            fileName = inputs["fileName"]
            if not isinstance(fileName, str):
                return _error("E_INPUT_TYPE", "input 'fileName' must be string")
            fileName = fileName.strip()
            if (not fileName or fileName in {".", ".."} or fileName.endswith(".")
                    or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in fileName)):
                return _error("E_INPUT_SHAPE", "fileName must be a non-empty file name without a directory")
            if not Path(fileName).suffix:
                fileName += outputFile.suffix
            outputFile = outputFile.with_name(fileName)

        if params.get("namingMode", "fixed") == "unique":
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            outputFile = outputFile.with_name(f"{outputFile.stem}_{stamp}_{uuid4().hex}{outputFile.suffix}")
            overwrite = False

        if outputFile.exists() and not overwrite:
            return _error("E_OUTPUT_EXISTS", f"output file exists: {outputFile}")

        checkCancellation = runtimeContext.get("raiseIfCancellationRequested")
        if callable(checkCancellation):
            checkCancellation()
        try:
            writeOk, encoded = cv2.imencode(outputFile.suffix, image)
            if not writeOk:
                return _error("E_OUTPUT_WRITE_FAILED", f"failed to encode image: {outputFile}")
            if callable(checkCancellation):
                checkCancellation()
            outputFile.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation also honors overwrite=False for concurrent jobs.
            try:
                with outputFile.open("wb" if overwrite else "xb") as stream:
                    stream.write(encoded.tobytes())
            except FileExistsError:
                return _error("E_OUTPUT_EXISTS", f"output file exists: {outputFile}")
        except (OSError, ValueError, cv2.error) as error:
            return _error("E_OUTPUT_WRITE_FAILED", f"failed to write image: {outputFile}; {error}")

        elapsedMs = (perf_counter() - startAt) * 1000.0
        return {
            "status": "ok",
            "outputs": {"result": {"saved": True, "path": str(outputFile)}},
            "metrics": {"latencyMs": round(elapsedMs, 3)},
            "diagnostics": {"text": "Image saved"},
        }


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
