from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
from time import perf_counter
from typing import Any

from emo_master.plugins.builtins.image_batch_loader.operator import _sortKey
from emo_master.plugins.builtins.image_loader.operator import OperatorMeta


class FileListOperator:
    meta = OperatorMeta(
        operatorId="vision.io.file_list", displayName="文件列表", version="1.0.0",
        inputPorts={},
        outputPorts={"paths": "list<string>", "count": "integer"},
        paramSchema={
            "type": "object",
            "properties": {
                "folderPath": {"title": "文件夹", "type": "string", "default": "",
                               "xWidget": "file", "xFileMode": "directory"},
                "patterns": {"title": "文件类型", "type": "array", "default": ["*"], "minItems": 1,
                             "items": {"type": "string", "minLength": 1}, "xWidget": "file-patterns",
                             "description": "选择视频或图片格式；特殊文件名可使用自定义规则。"},
                "recursive": {"title": "包含子文件夹", "type": "boolean", "default": False},
            },
            "required": ["folderPath"],
        },
    )

    def validateParams(self, params: dict[str, object]) -> dict[str, str] | None:
        folder = params.get("folderPath")
        if not isinstance(folder, str) or not folder.strip():
            return {"code": "E_PARAM_INVALID", "message": "folderPath must be non-empty string"}
        patterns = params.get("patterns", ["*"])
        if (not isinstance(patterns, list) or not patterns
                or any(not isinstance(pattern, str) or not pattern.strip() or "/" in pattern or "\\" in pattern
                       for pattern in patterns)):
            return {"code": "E_PARAM_INVALID", "message": "patterns must be a non-empty list of filename patterns"}
        if not isinstance(params.get("recursive", False), bool):
            return {"code": "E_PARAM_INVALID", "message": "recursive must be a boolean"}
        return None

    def executeNode(self, inputs: dict[str, object], params: dict[str, object],
                    runtimeContext: dict[str, object]) -> dict[str, Any]:
        started = perf_counter()
        error = self.validateParams(params)
        if error is not None:
            return {"status": "error", "error": error}
        checkCancelled = runtimeContext.get("raiseIfCancellationRequested")
        if callable(checkCancelled):
            checkCancelled()
        try:
            root = Path(str(params["folderPath"]).strip()).expanduser().resolve()
            if not root.is_dir():
                return _error("E_INPUT_MISSING", f"folder not found: {root}")
            patterns = params.get("patterns", ["*"])
            assert isinstance(patterns, list)
            patterns = [pattern.strip().casefold() for pattern in patterns]
            paths = []
            for path in (root.rglob("*") if params.get("recursive", False) else root.iterdir()):
                if callable(checkCancelled):
                    checkCancelled()
                if path.is_file() and any(fnmatchcase(path.name.casefold(), pattern) for pattern in patterns):
                    paths.append(path)
            paths.sort(key=lambda path: _sortKey(path, root))
            return {"status": "ok", "outputs": {"paths": [str(path) for path in paths], "count": len(paths)},
                    "metrics": {"latencyMs": round((perf_counter() - started) * 1000, 3)},
                    "diagnostics": {"text": f"Listed {len(paths)} files: {root}"}}
        except (OSError, ValueError) as error:
            return _error("E_INPUT_UNREADABLE", f"cannot list folder: {params['folderPath']}; {error}")


def _error(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code, "message": message}}
