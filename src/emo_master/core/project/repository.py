from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import json

import yaml


class ProjectRepository:
  def __init__(self, workspaceRoot: Path) -> None:
    self.workspaceRoot = workspaceRoot
    self.workspaceRoot.mkdir(parents=True, exist_ok=True)

  def createProject(self, name: str) -> str:
    projectId = str(uuid4())
    projectDir = self.workspaceRoot / projectId
    (projectDir / "graph").mkdir(parents=True, exist_ok=True)
    (projectDir / "params").mkdir(parents=True, exist_ok=True)
    (projectDir / "devices").mkdir(parents=True, exist_ok=True)
    (projectDir / "resources").mkdir(parents=True, exist_ok=True)
    (projectDir / "snapshots").mkdir(parents=True, exist_ok=True)

    projectMeta = {
      "projectId": projectId,
      "name": name,
      "version": "0.1.0",
      "schemaVersion": "1.0",
      "createdAt": _utcNow(),
      "updatedAt": _utcNow()
    }
    with (projectDir / "project.yaml").open("w", encoding="utf-8") as fileObj:
      yaml.safe_dump(projectMeta, fileObj, sort_keys=False, allow_unicode=False)

    (projectDir / "graph" / "flow.json").write_text(
      json.dumps({"schemaVersion": "1.0", "nodes": [], "edges": []}, ensure_ascii=True, indent=2),
      encoding="utf-8"
    )
    (projectDir / "graph" / "layout.json").write_text("{}", encoding="utf-8")
    (projectDir / "params" / "node_params.json").write_text("{}", encoding="utf-8")
    (projectDir / "devices" / "bindings.json").write_text("{}", encoding="utf-8")
    (projectDir / "plugins.lock").write_text("[]", encoding="utf-8")
    return projectId

  def loadProject(self, projectId: str) -> dict[str, object]:
    projectFile = self.workspaceRoot / projectId / "project.yaml"
    with projectFile.open("r", encoding="utf-8") as fileObj:
      loaded = yaml.safe_load(fileObj)
    if not isinstance(loaded, dict):
      raise ValueError("project.yaml content is invalid")
    return loaded


def _utcNow() -> str:
  return datetime.now(timezone.utc).isoformat()
