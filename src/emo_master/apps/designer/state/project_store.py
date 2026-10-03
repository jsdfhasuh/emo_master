from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import cast
from uuid import uuid4

from emo_master.core.project.migration import migrateProjectPayload, utc_now_iso
from emo_master.core.project.models import ProjectDocument


class ProjectPayload(dict[str, object]):
    """Canonical payload with read-only aliases for the original MVP API."""

    def __init__(self, payload: dict[str, object]) -> None:
        super().__init__(payload)

    def __getitem__(self, key: str) -> object:
        if key == "version" and key not in self:
            return "1.0"
        if key == "designer" and key not in self:
            workflows = super().get("workflows", {})
            entry = super().get("entryWorkflowId", "main")
            if isinstance(workflows, dict):
                graph = workflows.get(entry)
                if isinstance(graph, dict):
                    return {
                        "nodes": graph.get("nodes", []),
                        "edges": graph.get("edges", []),
                    }
            return {"nodes": [], "edges": []}
        if key == "meta" and key not in self:
            project = super().get("project", {})
            return dict(project) if isinstance(project, dict) else {}
        if key == "runtime" and key not in self:
            return {"sourceImagePath": ""}
        return super().__getitem__(key)

    def get(self, key: str, default: object = None) -> object:
        if key == "version" and key not in self:
            return "1.0"
        if key == "designer" and key not in self:
            return self[key]
        if key in {"meta", "runtime"} and key not in self:
            return self[key]
        return super().get(key, default)


def createProjectSkeleton(projectDir: Path, projectName: str) -> None:
    projectDir.mkdir(parents=True, exist_ok=True)
    (projectDir / "assets").mkdir(parents=True, exist_ok=True)
    (projectDir / "outputs").mkdir(parents=True, exist_ok=True)

    projectFile = projectDir / "project.json"
    if projectFile.exists():
        return
    now = utc_now_iso()
    payload: dict[str, object] = {
        "schemaVersion": "2.1",
        "project": {
            "projectId": str(uuid4()),
            "name": projectName,
            "revision": 1,
            "createdAt": now,
            "updatedAt": now,
        },
        "entryWorkflowId": "main",
        "workflowOrder": ["main"],
        "workflows": {
            "main": {
                "name": "Main",
                "inputs": {},
                "outputs": {},
                "nodes": [
                    {"nodeId": "__workflow_input__", "kind": "workflow_input"},
                    {"nodeId": "__workflow_output__", "kind": "workflow_output"},
                ],
                "edges": [],
                "layout": {"nodePositions": {}},
            }
        },
        "runtime": {
            "maxConcurrentJobs": 2,
            "gracefulStopTimeoutMs": 5000,
            "heartbeatTimeoutMs": 5000,
            "eventRetentionPerJob": 10000,
        },
        "dependencies": {"operators": []},
        "devices": {"bindings": {}},
    }
    _writeProjectFile(projectFile, payload, backup=False)


def saveProject(projectDir: Path, payload: dict[str, object]) -> None:
    projectDir.mkdir(parents=True, exist_ok=True)
    (projectDir / "assets").mkdir(parents=True, exist_ok=True)
    (projectDir / "outputs").mkdir(parents=True, exist_ok=True)
    canonical = migrateProjectPayload(payload)
    document = ProjectDocument.model_validate(canonical)
    normalized = cast(dict[str, object], document.model_dump(mode="json"))
    projectFile = projectDir / "project.json"
    _writeProjectFile(projectFile, normalized, backup=projectFile.exists())


def loadProject(projectDir: Path) -> ProjectPayload:
    projectFile = projectDir / "project.json"
    if not projectFile.exists() or not projectFile.is_file():
        raise ValueError("project.json not found")
    try:
        rawText = projectFile.read_text(encoding="utf-8")
        parsed = json.loads(rawText)
    except Exception as err:
        raise ValueError(f"invalid project file: {err}") from err
    if not isinstance(parsed, dict):
        raise ValueError("invalid project file format")
    try:
        canonical = migrateProjectPayload(parsed)
        document = ProjectDocument.model_validate(canonical)
    except Exception as err:
        raise ValueError(f"invalid project schema: {err}") from err
    return ProjectPayload(document.model_dump(mode="json"))


def loadProjectDocument(projectDir: Path) -> ProjectDocument:
    payload = loadProject(projectDir)
    return ProjectDocument.model_validate(dict(payload))


def _writeProjectFile(
    projectFile: Path, payload: dict[str, object], backup: bool
) -> None:
    projectFile.parent.mkdir(parents=True, exist_ok=True)
    if backup and projectFile.exists():
        shutil.copy2(projectFile, projectFile.with_suffix(projectFile.suffix + ".bak"))
    tempName: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=projectFile.parent,
            prefix=f".{projectFile.name}.",
            suffix=".tmp",
            delete=False,
        ) as tempFile:
            tempName = tempFile.name
            json.dump(payload, tempFile, ensure_ascii=True, indent=2)
            tempFile.write("\n")
            tempFile.flush()
            os.fsync(tempFile.fileno())
        os.replace(tempName, projectFile)
        tempName = None
    finally:
        if tempName is not None:
            try:
                Path(tempName).unlink()
            except FileNotFoundError:
                pass


def _nowIso() -> str:
    """Compatibility alias retained for callers from the MVP."""
    return utc_now_iso()
