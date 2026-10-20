"""Admission-time TXT/CSV snapshots shared by every reader in one runner session."""
from __future__ import annotations

import json
from pathlib import Path

from emo_master.plugins.builtins._coordinate_operators import (
    CoordinateFileError, CoordinateReaderOperator, loadCoordinateContent, _resolveCoordinatePath,
)


class CoordinateSnapshots:
    def __init__(self):
        self._contents = {}
        self._identity = None

    @staticmethod
    def _key(path, params):
        # Freeze parsing AND declaration parameters, not just the pathname.
        return str(path.resolve()), json.dumps(dict(params), sort_keys=True, ensure_ascii=False)

    def prepare(self, compiled, root, context, cancellation):
        reachable, pending = set(), [root]
        while pending:
            workflowId = pending.pop()
            if workflowId in reachable:
                continue
            reachable.add(workflowId)
            pending.extend(compiled.workflowCallGraph.get(workflowId, ()))
        readers = [(node, dict(node.params)) for wid in sorted(reachable)
                   for node in compiled.workflows[wid].nodes
                   if node.operatorId == "vision.io.coordinate_reader" and node.params.get("readMode") == "session"]
        if not readers:
            return
        identity = (context.jobId, context.projectId, root, context.workspacePath)
        if self._identity is not None:
            if self._identity != identity:
                raise CoordinateFileError("E_COORDINATE_SNAPSHOT_CONTEXT", "a coordinate session cannot cross Jobs or root entries")
            return
        # Stage all files; failed admission must not leave a partial snapshot.
        rawByPath, staged = {}, {}
        for node, params in readers:
            cancellation.raise_if_cancelled()
            if node.globalVariableBindings:
                raise CoordinateFileError("E_COORDINATE_SNAPSHOT_CONFIG", "session reader configuration must be static at admission")
            validation = CoordinateReaderOperator().validateParams(params)
            if validation:
                raise CoordinateFileError(validation["code"], validation["message"])
            path = _resolveCoordinatePath(params["filePath"], context.workspacePath)
            key = self._key(path, params)
            if key in staged:
                continue
            try:
                if path not in rawByPath:
                    # A later reader may set a larger limit; still freeze once.
                    limits = [int(p.get("maxFileBytes", 4194304)) for _, p in readers
                              if _resolveCoordinatePath(p["filePath"], context.workspacePath) == path]
                    with path.open("rb") as stream:
                        rawByPath[path] = stream.read(max(limits) + 1)
                staged[key] = loadCoordinateContent(path, params, rawByPath[path])
            except FileNotFoundError as exc:
                raise CoordinateFileError("E_INPUT_MISSING", f"coordinate file not found: {path}") from exc
            except OSError as exc:
                raise CoordinateFileError("E_INPUT_SHAPE", f"cannot snapshot coordinate file: {exc}") from exc
        cancellation.raise_if_cancelled()
        self._contents = staged
        self._identity = identity

    def get(self, path: Path, params):
        key = self._key(path, params)
        if key not in self._contents:
            raise CoordinateFileError("E_COORDINATE_SNAPSHOT_CONFIG", "reader file/parameters were not frozen at admission")
        return self._contents[key]

    def clear(self):
        self._contents.clear()
        self._identity = None
