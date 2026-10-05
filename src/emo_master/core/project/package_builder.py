from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import zipfile

from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument


PACKAGE_MANIFEST_SCHEMA_VERSION = "2.0"


def buildPageTestPackage(document, projectDir: Path, outputDir: Path, registry=None) -> Path:
    """Explicit P5-A allowlist entry; the legacy 2.2 guard remains unchanged."""
    import os
    import tempfile
    from uuid import uuid4
    from emo_master.core.project.test_delivery import (
        MAX_PROJECT, canonicalJson, manifestFor, portableDocument, trustedRegistry, validateTestProject,
    )
    root = projectDir.resolve(strict=True)
    if outputDir.resolve().is_relative_to(root):
        raise ValueError('test package output must be outside the project')
    document = portableDocument(document)
    compatibility = validateTestProject(document, root, registry if registry is not None else trustedRegistry())
    data = canonicalJson(document.model_dump()).encode('utf-8')
    if len(data) > MAX_PROJECT:
        raise ValueError('project metadata exceeds 1 MiB')
    manifest = manifestFor(document, data, compatibility)
    outputDir.mkdir(parents=True, exist_ok=True)
    destination = outputDir / f'test-{manifest["revision"][:16]}-{uuid4().hex[:8]}.vxpkg'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=outputDir, delete=False) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('project.json', data)
            for item in document.resources.items.values():
                # Recheck after validation and copy a fixed bounded amount, verifying
                # the copied bytes rather than relying on a prior filesystem hash.
                from emo_master.core.project.snapshots import _inside
                with _inside(root, item.path).open('rb') as stream:
                    blob = stream.read(item.size + 1)
                if len(blob) != item.size or hashlib.sha256(blob).hexdigest() != item.sha256:
                    raise ValueError('resource changed during export')
                archive.writestr(item.path, blob)
            archive.writestr('manifest.json', canonicalJson(manifest))
        with temporary.open('r+b') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        return destination
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


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
    if payload["schemaVersion"] == "2.2":
        raise ValueError("project 2.2 package publication requires the P5 resource/presentation pipeline")
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
        "checksum": _collectChecksums(projectDir, projectJsonBytes, payload),
    }

    with zipfile.ZipFile(packagePath, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filePath in projectDir.rglob("*"):
            if filePath.is_file() and not _databaseFile(filePath, projectDir, payload):
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


def _collectChecksums(projectDir: Path, projectJsonBytes: bytes, payload=None) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for filePath in projectDir.rglob("*"):
        if filePath.is_file() and not _databaseFile(filePath, projectDir, payload or {}):
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


def _databaseFile(path: Path, root: Path, payload: dict) -> bool:
    # Business data and SQLite sidecars must not become project delivery assets.
    name = path.name.casefold()
    base = name.removesuffix('-wal').removesuffix('-shm').removesuffix('-journal')
    if Path(base).suffix in {'.sqlite', '.sqlite3', '.db', '.db3'}:
        return True
    for workflow in payload.get('workflows', {}).values():
        for node in workflow.get('nodes', []):
            if node.get('operatorId') != 'vision.io.sqlite_writer':
                continue
            for raw in (node.get('params', {}).get('databasePath'), node.get('params', {}).get('debugDatabasePath')):
              if isinstance(raw, str) and raw:
                target = Path(raw)
                if not target.is_absolute():
                    target = root / target
                if path.resolve() in {Path(str(target.resolve()) + ending) for ending in ('', '-wal', '-shm', '-journal')}:
                    return True
    return False


def _requiredOperators(payload: dict[str, object]) -> list[object]:
    dependencies = payload.get("dependencies")
    if isinstance(dependencies, dict):
        operators = dependencies.get("operators")
        if isinstance(operators, list):
            return list(operators)
    return []
