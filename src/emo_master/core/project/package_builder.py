from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import zipfile

from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument


PACKAGE_MANIFEST_SCHEMA_VERSION = "2.0"


def buildPackage(projectDir: Path, outputDir: Path) -> Path:
    if not projectDir.exists():
        raise FileNotFoundError(f"project directory not found: {projectDir}")
    projectFile = projectDir / "project.json"
    if not projectFile.exists():
        raise FileNotFoundError(f"project.json not found: {projectFile}")
    parsed = json.loads(projectFile.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("project.json must contain an object")
    payload = ProjectDocument.model_validate(migrateProjectPayload(parsed)).model_dump(
        mode="json"
    )
    projectJsonBytes = (
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
    ).encode("utf-8")

    outputDir.mkdir(parents=True, exist_ok=True)
    packageName = f"{projectDir.name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.vxpkg"
    packagePath = outputDir / packageName
    dependencies = _requiredOperators(payload)
    projectMeta = payload.get("project", {})
    projectVersion = "0.2.0"
    projectId = projectDir.name
    if isinstance(projectMeta, dict):
        rawProjectId = projectMeta.get("projectId")
        if isinstance(rawProjectId, str) and rawProjectId:
            projectId = rawProjectId
    manifest = {
        # This versions the package manifest itself.  The independently
        # versioned project schema is declared inside project.json.
        "schemaVersion": PACKAGE_MANIFEST_SCHEMA_VERSION,
        "projectId": projectId,
        "projectVersion": projectVersion,
        "buildTime": datetime.now(timezone.utc).isoformat(),
        "runtimeMin": "0.2.0",
        "runtimeMax": "1.x",
        "requiredOperators": dependencies,
        "checksum": _collectChecksums(projectDir, projectJsonBytes),
    }

    with zipfile.ZipFile(packagePath, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filePath in projectDir.rglob("*"):
            if filePath.is_file():
                relativePath = filePath.relative_to(projectDir).as_posix()
                if relativePath in {
                    "project.json",
                    "project.yaml",
                    "plugins.lock",
                    "manifest.json",
                }:
                    continue
                archive.write(filePath, relativePath)
        archive.writestr("project.json", projectJsonBytes)
        archive.writestr(
            "plugins.lock", json.dumps(dependencies, ensure_ascii=True, indent=2)
        )
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=True, indent=2))
    return packagePath


def _collectChecksums(projectDir: Path, projectJsonBytes: bytes) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for filePath in projectDir.rglob("*"):
        if filePath.is_file():
            relativePath = filePath.relative_to(projectDir).as_posix()
            if relativePath in {
                "project.json",
                "project.yaml",
                "plugins.lock",
                "manifest.json",
            }:
                continue
            checksums[relativePath] = hashlib.sha256(filePath.read_bytes()).hexdigest()
    checksums["project.json"] = hashlib.sha256(projectJsonBytes).hexdigest()
    return checksums


def _requiredOperators(payload: dict[str, object]) -> list[object]:
    dependencies = payload.get("dependencies")
    if isinstance(dependencies, dict):
        operators = dependencies.get("operators")
        if isinstance(operators, list):
            return list(operators)
    return []
