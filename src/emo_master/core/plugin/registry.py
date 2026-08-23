import json
from pathlib import Path

from emo_master.core.plugin.models import PluginDescriptor, RegistryScanResult, ValidationIssue
from emo_master.core.plugin.validator import (
  isVersionCompatible,
  loadOperatorClass,
  validateConsistency,
  validateManifestFields
)


class PluginRegistry:
  def __init__(self, coreVersion: str) -> None:
    self.coreVersion = coreVersion

  def scan(self, pluginRoot: Path) -> RegistryScanResult:
    activeOperators: dict[str, PluginDescriptor] = {}
    rejectedOperators: dict[str, list[ValidationIssue]] = {}

    manifestPaths = sorted(pluginRoot.glob("**/manifest.json"))
    for manifestPath in manifestPaths:
      issues: list[ValidationIssue] = []
      manifestData = self._loadManifestData(manifestPath)
      if manifestData is None:
        rejectedOperators[str(manifestPath)] = [
          ValidationIssue("R01", "E_MANIFEST_PARSE_FAILED", "manifest json parse failed")
        ]
        continue

      manifest, fieldIssues = validateManifestFields(manifestData)
      issues.extend(fieldIssues)
      if manifest is None:
        key = str(manifestPath)
        rejectedOperators[key] = issues
        continue

      if manifest.operatorId in activeOperators or manifest.operatorId in rejectedOperators:
        issues.append(ValidationIssue("R01", "E_OPERATOR_ID_DUPLICATED", "duplicated operatorId"))

      if not isVersionCompatible(self.coreVersion, manifest.minCoreVersion, manifest.maxCoreVersion):
        issues.append(ValidationIssue("R07", "E_CORE_VERSION_INCOMPATIBLE", "core version incompatible"))

      operatorClass, classIssues = loadOperatorClass(manifest.entry)
      issues.extend(classIssues)
      if operatorClass is not None:
        issues.extend(validateConsistency(manifest, operatorClass))

      if issues:
        rejectedOperators[manifest.operatorId] = issues
        continue

      if operatorClass is None:
        rejectedOperators[manifest.operatorId] = [
          ValidationIssue("R02", "E_ENTRY_IMPORT_FAILED", "operator class not resolved")
        ]
        continue

      activeOperators[manifest.operatorId] = PluginDescriptor(
        manifest=manifest,
        operatorClass=operatorClass
      )

    return RegistryScanResult(activeOperators=activeOperators, rejectedOperators=rejectedOperators)

  def _loadManifestData(self, manifestPath: Path) -> dict[str, object] | None:
    try:
      with manifestPath.open("r", encoding="utf-8") as fileObj:
        parsed = json.load(fileObj)
    except Exception:
      return None

    if not isinstance(parsed, dict):
      return None
    return parsed
