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


class ProjectRepository:
    """Repository facade backed exclusively by project.json v2."""

    def __init__(self, workspaceRoot: Path) -> None:
        self.workspaceRoot = workspaceRoot
        self.workspaceRoot.mkdir(parents=True, exist_ok=True)

    def createProject(self, name: str) -> str:
        projectId = str(uuid4())
        projectDir = self.workspaceRoot / projectId
        projectDir.mkdir(parents=True, exist_ok=True)
        (projectDir / "assets").mkdir(parents=True, exist_ok=True)
        (projectDir / "outputs").mkdir(parents=True, exist_ok=True)
        now = utc_now_iso()
        payload: dict[str, object] = {
            "schemaVersion": "2.0",
            "project": {
                "projectId": projectId,
                "name": name,
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
                    "nodes": [],
                    "edges": [],
                    "layout": {"nodePositions": {}},
                }
            },
            "runtime": {},
            "dependencies": {"operators": []},
            "devices": {"bindings": {}},
        }
        normalized = ProjectDocument.model_validate(
            migrateProjectPayload(payload)
        ).model_dump(mode="json")
        self._write(projectDir / "project.json", normalized, backup=False)
        return projectId

    def loadProject(self, projectId: str) -> dict[str, object]:
        projectDir = self.workspaceRoot / projectId
        projectFile = projectDir / "project.json"
        if not projectFile.exists():
            raise ValueError("project.json not found")
        parsed = json.loads(projectFile.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("project.json content is invalid")
        payload = migrateProjectPayload(parsed)
        payload = ProjectDocument.model_validate(payload).model_dump(mode="json")
        project = payload.get("project")
        if isinstance(project, dict):
            # Keep the small v1 repository API available to existing callers.
            payload.setdefault("projectId", project.get("projectId", projectId))
            payload.setdefault("name", project.get("name", projectId))
        return payload

    def saveProject(self, projectId: str, payload: dict[str, object]) -> None:
        projectDir = self.workspaceRoot / projectId
        projectDir.mkdir(parents=True, exist_ok=True)
        normalized = cast(
            dict[str, object],
            ProjectDocument.model_validate(migrateProjectPayload(payload)).model_dump(
                mode="json"
            ),
        )
        self._write(projectDir / "project.json", normalized, backup=True)

    def loadProjectDocument(self, projectId: str):
        payload = self.loadProject(projectId)
        return ProjectDocument.model_validate(migrateProjectPayload(payload))

    def _write(self, projectFile: Path, payload: dict[str, object], backup: bool) -> None:
        projectFile.parent.mkdir(parents=True, exist_ok=True)
        if backup and projectFile.exists():
            shutil.copy2(projectFile, projectFile.with_suffix(projectFile.suffix + ".bak"))
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=projectFile.parent,
                prefix=f".{projectFile.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = handle.name
                json.dump(payload, handle, ensure_ascii=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, projectFile)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink()
                except FileNotFoundError:
                    pass


def _utcNow() -> str:
    """Compatibility alias for the pre-v2 repository module."""
    return utc_now_iso()
