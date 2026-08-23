from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import zipfile


def buildPackage(projectDir: Path, outputDir: Path) -> Path:
  if not projectDir.exists():
    raise FileNotFoundError(f"project directory not found: {projectDir}")

  outputDir.mkdir(parents=True, exist_ok=True)
  packageName = f"{projectDir.name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.vxpkg"
  packagePath = outputDir / packageName

  requiredOperators = _loadRequiredOperators(projectDir)
  checksums = _collectChecksums(projectDir)
  manifest = {
    "projectId": projectDir.name,
    "projectVersion": "0.1.0",
    "buildTime": datetime.now(timezone.utc).isoformat(),
    "runtimeMin": "0.1.0",
    "runtimeMax": "1.x",
    "requiredOperators": requiredOperators,
    "checksum": checksums
  }

  with zipfile.ZipFile(packagePath, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for filePath in projectDir.rglob("*"):
      if filePath.is_file():
        arcName = filePath.relative_to(projectDir).as_posix()
        archive.write(filePath, arcName)
    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=True, indent=2))

  return packagePath


def _collectChecksums(projectDir: Path) -> dict[str, str]:
  checksums: dict[str, str] = {}
  for filePath in projectDir.rglob("*"):
    if not filePath.is_file():
      continue
    relativePath = filePath.relative_to(projectDir).as_posix()
    digest = hashlib.sha256(filePath.read_bytes()).hexdigest()
    checksums[relativePath] = digest
  return checksums


def _loadRequiredOperators(projectDir: Path) -> list[object]:
  lockPath = projectDir / "plugins.lock"
  if not lockPath.exists():
    return []
  try:
    parsed = json.loads(lockPath.read_text(encoding="utf-8"))
  except Exception:
    return []
  if not isinstance(parsed, list):
    return []
  return parsed
