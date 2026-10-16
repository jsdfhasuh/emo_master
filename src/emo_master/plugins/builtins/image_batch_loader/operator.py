from __future__ import annotations

from pathlib import Path
import re
from time import perf_counter
from typing import Any

from emo_master.plugins.builtins.image_loader.operator import ImageLoaderOperator, OperatorMeta


_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"})


def _sortKey(path: Path, root: Path) -> tuple:
    relative = path.relative_to(root).as_posix()
    parts = tuple(
        (1, int(part)) if part.isascii() and part.isdigit() else (0, part.casefold())
        for part in re.split(r"([0-9]+)", relative)
    )
    return parts, relative


class ImageBatchLoaderOperator:
    meta = OperatorMeta(
        operatorId="vision.io.image_batch_loader",
        displayName="\u56fe\u7247\u6279\u91cf\u5bfc\u5165",
        version="1.0.0",
        inputPorts={"reset": {"type": "boolean", "required": False, "nullable": False}},
        outputPorts={
            **ImageLoaderOperator.meta.outputPorts,
            "imagePath": {"type": "string", "required": True, "nullable": False},
            "index": {"type": "integer", "required": True, "nullable": False},
            "total": {"type": "integer", "required": True, "nullable": False},
            "hasNext": {"type": "boolean", "required": True, "nullable": False},
        },
        paramSchema={
            "type": "object",
            "properties": {
                "folderPath": {
                    "title": "\u56fe\u7247\u6587\u4ef6\u5939",
                    "type": "string",
                    "default": "",
                    "xWidget": "file",
                    "xFileMode": "directory",
                },
                "colorMode": {
                    "title": "\u989c\u8272\u6a21\u5f0f",
                    "type": "string",
                    "enum": ["color", "grayscale"],
                    "default": "color",
                },
                "recursive": {
                    "title": "\u5305\u542b\u5b50\u6587\u4ef6\u5939",
                    "type": "boolean",
                    "default": False,
                },
            },
            "required": ["folderPath"],
        },
    )

    def __init__(self) -> None:
        self._sequenceKey: tuple | None = None
        self._paths: list[Path] = []
        self._nextIndex = 0
        self._loader = ImageLoaderOperator()

    def disposeOperator(self) -> None:
        # The lifecycle hook makes Runtime retain this node across loop iterations.
        self._sequenceKey = None
        self._paths = []
        self._nextIndex = 0

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        folder = params.get("folderPath")
        if not isinstance(folder, str) or not folder.strip():
            return {"code": "E_PARAM_INVALID", "message": "folderPath must be non-empty string"}
        if params.get("colorMode", "color") not in ("color", "grayscale"):
            return {"code": "E_PARAM_INVALID", "message": "colorMode must be color or grayscale"}
        if not isinstance(params.get("recursive", False), bool):
            return {"code": "E_PARAM_INVALID", "message": "recursive must be a boolean"}
        return None

    def executeNode(
        self,
        inputs: dict[str, object],
        params: dict[str, object],
        runtimeContext: dict[str, object],
    ) -> dict[str, Any]:
        startedAt = perf_counter()
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        if "reset" in inputs and not isinstance(inputs["reset"], bool):
            return _error("E_INPUT_TYPE", "reset must be a boolean")
        checkCancelled = runtimeContext.get("raiseIfCancellationRequested")
        if callable(checkCancelled):
            checkCancelled()

        try:
            root = Path(str(params["folderPath"]).strip()).expanduser().resolve()
            recursive = params.get("recursive", False)
            colorMode = params.get("colorMode", "color")
            key = (runtimeContext.get("jobId"), str(root), recursive, colorMode)
            if inputs.get("reset") is True or key != self._sequenceKey:
                self.disposeOperator()
                if not root.is_dir():
                    return _error("E_INPUT_MISSING", f"image folder not found: {root}")
                paths = []
                candidates = root.rglob("*") if recursive else root.iterdir()
                for path in candidates:
                    if callable(checkCancelled):
                        checkCancelled()
                    if path.suffix.lower() in _EXTENSIONS and path.is_file():
                        paths.append(path)
                self._paths = sorted(paths, key=lambda path: _sortKey(path, root))
                self._sequenceKey = key
        except (OSError, ValueError) as err:
            return _error("E_INPUT_MISSING", f"cannot list image folder: {err}")

        total = len(self._paths)
        if not total:
            return _error("E_INPUT_MISSING", f"no supported images in folder: {root}")
        if self._nextIndex >= total:
            return _error("E_INPUT_EXHAUSTED", "all images loaded; stop on hasNext=false or reset")
        path = self._paths[self._nextIndex]
        result = self._loader.executeNode(
            {}, {"imagePath": str(path), "colorMode": colorMode}, runtimeContext
        )
        if result["status"] != "ok":
            return result
        if callable(checkCancelled):
            checkCancelled()
        # Advance only after successful decoding; never retain decoded images.
        self._nextIndex += 1
        result["outputs"].update({
            "imagePath": str(path),
            "index": self._nextIndex - 1,
            "total": total,
            "hasNext": self._nextIndex < total,
        })
        result["metrics"] = {"latencyMs": round((perf_counter() - startedAt) * 1000.0, 3)}
        result["diagnostics"] = {"text": f"Image {self._nextIndex}/{total}: {path.name}"}
        return result


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
