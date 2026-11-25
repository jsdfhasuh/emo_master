import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import os
from pathlib import Path
import sys
from time import perf_counter
from typing import Any
from uuid import uuid4

import cv2
import numpy as np


_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_renameat2 = None
if sys.platform == "linux":
    _renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if _renameat2 is not None:
        _renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        _renameat2.restype = ctypes.c_int


class _OutputExistsError(FileExistsError):
    code = "E_OUTPUT_EXISTS"


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
                    "description": "基础输出路径；fileName输入可替换末段，唯一命名再追加UTC时间与uuid。",
                    "type": "string",
                    "default": "",
                    "xWidget": "file",
                    "xFileMode": "save",
                    "xFilter": "图片文件 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)",
                },
                "namingMode": {
                    "title": "文件命名",
                    "description": "fixed用原名；unique追加唯一后缀并强制overwrite=false。",
                    "type": "string", "enum": ["fixed", "unique"], "default": "fixed",
                    "xOptionLabels": {"fixed": "固定名称", "unique": "自动唯一命名"},
                },
                "saveMode": {
                    "title": "保存方式", "description": "sync完成写入后返回；background只提交任务，要求unique与Runtime背景任务服务。",
                    "type": "string", "enum": ["sync", "background"], "default": "sync",
                    "xOptionLabels": {"sync": "同步保存", "background": "后台保存"},
                },
                "overwrite": {"title": "覆盖已有文件", "description": "仅fixed时使用；unique始终不覆盖，后台同样不覆盖。",
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
        saveMode = params.get("saveMode", "sync")
        if saveMode not in ("sync", "background"):
            return {"code": "E_PARAM_INVALID", "message": "saveMode must be sync or background"}
        if saveMode == "background" and namingMode != "unique":
            return {"code": "E_PARAM_INVALID", "message": "background save requires namingMode=unique"}
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
        if params.get("saveMode") == "background":
            tasks = runtimeContext.get("backgroundTasks")
            submit = getattr(tasks, "submit", None)
            if not callable(submit):
                return _error("E_BACKGROUND_TASK_UNAVAILABLE", "background saving requires a Runtime task client")
            checkSize = getattr(tasks, "checkSize", None)
            if callable(checkSize):
                checkSize("image.save", image.nbytes)
            task = submit(_saveInBackground, image.copy(), outputFile,
                          taskName="image.save", estimatedBytes=image.nbytes)
            return {
                "status": "ok",
                "outputs": {"result": {"accepted": True, "saved": False, "state": "SUBMITTED",
                                       "path": str(outputFile), "taskId": task.id}},
                "metrics": {"latencyMs": round((perf_counter() - startAt) * 1000, 3)},
                "diagnostics": {"text": "Image save submitted"},
            }
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


def _saveInBackground(control, image: np.ndarray, outputFile: Path) -> dict[str, object]:
    control.raiseIfCancelled()
    ok, encoded = cv2.imencode(outputFile.suffix, image)
    if not ok:
        raise OSError(f"failed to encode image: {outputFile}")
    control.raiseIfCancelled()
    outputFile.parent.mkdir(parents=True, exist_ok=True)
    with control.temporaryFile(outputFile.parent) as stream:
        stream.write(encoded.data)
        stream.close()
        control.publish(lambda: _publishUniqueImage(stream.name, outputFile))
    return {"saved": True, "path": str(outputFile)}


def _publishUniqueImage(tempPath, outputFile: Path) -> None:
    try:
        if os.name == "nt":
            os.rename(tempPath, outputFile)  # Windows rename fails when the target exists.
            return
        if _renameat2 is not None:
            target = os.fsencode(outputFile)
            if b"\0" in target:
                raise ValueError("embedded null byte in image output path")
            if _renameat2(_AT_FDCWD, os.fsencode(tempPath), _AT_FDCWD, target, _RENAME_NOREPLACE) == 0:
                return
            error = ctypes.get_errno()
            if error not in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
                raise OSError(error, os.strerror(error), outputFile)
        # POSIX fallback: create the final name atomically from the completed file.
        # The caller unlinks the temporary name after publication.
        os.link(tempPath, outputFile)
    except FileExistsError as error:
        raise _OutputExistsError(f"output file exists: {outputFile}") from error


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
