from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def createProjectSkeleton(projectDir: Path, projectName: str) -> None:
    projectDir.mkdir(parents=True, exist_ok=True)
    (projectDir / "assets").mkdir(parents=True, exist_ok=True)
    (projectDir / "outputs").mkdir(parents=True, exist_ok=True)

    projectFile = projectDir / "project.json"
    if projectFile.exists():
        return
    payload = {
        "version": "1.0",
        "meta": {
            "name": projectName,
            "createdAt": _nowIso(),
            "updatedAt": _nowIso(),
        },
        "runtime": {
            "sourceImagePath": "",
        },
        "designer": {
            "nodes": [],
            "edges": [],
        },
    }
    projectFile.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8"
    )


def saveProject(projectDir: Path, payload: dict[str, object]) -> None:
    projectDir.mkdir(parents=True, exist_ok=True)
    (projectDir / "assets").mkdir(parents=True, exist_ok=True)
    (projectDir / "outputs").mkdir(parents=True, exist_ok=True)

    projectCopy = dict(payload)
    metaRaw = projectCopy.get("meta", {})
    meta = dict(metaRaw) if isinstance(metaRaw, dict) else {}
    if "createdAt" not in meta:
        meta["createdAt"] = _nowIso()
    meta["updatedAt"] = _nowIso()
    projectCopy["meta"] = meta

    projectFile = projectDir / "project.json"
    projectFile.write_text(
        json.dumps(projectCopy, ensure_ascii=True, indent=2), encoding="utf-8"
    )


def loadProject(projectDir: Path) -> dict[str, object]:
    projectFile = projectDir / "project.json"
    if not projectFile.exists() or not projectFile.is_file():
        raise ValueError("project.json not found")
    try:
        rawText = projectFile.read_text(encoding="utf-8")
        parsed = json.loads(rawText)
    except Exception as err:
        raise ValueError(f"invalid project file: {err}")
    if not isinstance(parsed, dict):
        raise ValueError("invalid project file format")
    return parsed


def _nowIso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
