import importlib

from emo_master.core.plugin.models import PluginManifest, ValidationIssue


def isVersionCompatible(coreVersion: str, minCoreVersion: str, maxCoreVersion: str) -> bool:
  coreMajor = coreVersion.split(".")[0]
  minMajor = minCoreVersion.split(".")[0]
  if int(coreMajor) < int(minMajor):
    return False

  if maxCoreVersion.endswith(".x"):
    maxMajor = maxCoreVersion.split(".")[0]
    return int(coreMajor) <= int(maxMajor)

  maxMajor = maxCoreVersion.split(".")[0]
  return int(coreMajor) <= int(maxMajor)


def parseEntry(entry: str) -> tuple[str, str] | None:
  if ":" not in entry:
    return None
  moduleName, className = entry.split(":", 1)
  if not moduleName or not className:
    return None
  return moduleName, className


def validateManifestFields(manifestData: dict[str, object]) -> tuple[PluginManifest | None, list[ValidationIssue]]:
  issues: list[ValidationIssue] = []
  requiredFields = [
    "operatorId",
    "displayName",
    "version",
    "entry",
    "inputPorts",
    "outputPorts",
    "paramSchema",
    "minCoreVersion",
    "maxCoreVersion"
  ]
  for fieldName in requiredFields:
    if fieldName not in manifestData:
      issues.append(ValidationIssue("R01", "E_MANIFEST_FIELD_MISSING", f"missing field: {fieldName}"))

  if issues:
    return None, issues

  inputPorts = _toStrMap(manifestData["inputPorts"])
  outputPorts = _toStrMap(manifestData["outputPorts"])
  paramSchema = _toObjectMap(manifestData["paramSchema"])
  if inputPorts is None:
    issues.append(ValidationIssue("R01", "E_MANIFEST_TYPE_INVALID", "inputPorts must be object map"))
  if outputPorts is None:
    issues.append(ValidationIssue("R01", "E_MANIFEST_TYPE_INVALID", "outputPorts must be object map"))
  if paramSchema is None:
    issues.append(ValidationIssue("R01", "E_MANIFEST_TYPE_INVALID", "paramSchema must be object map"))

  if issues:
    return None, issues

  if inputPorts is None or outputPorts is None or paramSchema is None:
    return None, [ValidationIssue("R01", "E_MANIFEST_TYPE_INVALID", "manifest map fields are invalid")]

  manifest = PluginManifest(
    operatorId=str(manifestData["operatorId"]),
    displayName=str(manifestData["displayName"]),
    version=str(manifestData["version"]),
    entry=str(manifestData["entry"]),
    category=str(manifestData.get("category", "其他")),
    iconKey=str(manifestData.get("iconKey", "default")),
    summary=str(manifestData.get("summary", "")),
    inputPorts=inputPorts,
    outputPorts=outputPorts,
    paramSchema=paramSchema,
    minCoreVersion=str(manifestData["minCoreVersion"]),
    maxCoreVersion=str(manifestData["maxCoreVersion"])
  )
  return manifest, issues


def _toStrMap(rawValue: object) -> dict[str, str] | None:
  if not isinstance(rawValue, dict):
    return None
  parsed: dict[str, str] = {}
  for key, value in rawValue.items():
    if not isinstance(key, str) or not isinstance(value, str):
      return None
    parsed[key] = value
  return parsed


def _toObjectMap(rawValue: object) -> dict[str, object] | None:
  if not isinstance(rawValue, dict):
    return None
  parsed: dict[str, object] = {}
  for key, value in rawValue.items():
    if not isinstance(key, str):
      return None
    parsed[key] = value
  return parsed


def loadOperatorClass(entry: str) -> tuple[type | None, list[ValidationIssue]]:
  parsedEntry = parseEntry(entry)
  if parsedEntry is None:
    return None, [ValidationIssue("R02", "E_ENTRY_INVALID", "entry must be module:Class")]

  moduleName, className = parsedEntry
  try:
    module = importlib.import_module(moduleName)
  except Exception as err:
    return None, [ValidationIssue("R02", "E_ENTRY_IMPORT_FAILED", str(err))]

  if not hasattr(module, className):
    return None, [ValidationIssue("R02", "E_ENTRY_CLASS_MISSING", f"missing class: {className}")]

  operatorClass = getattr(module, className)
  if not isinstance(operatorClass, type):
    return None, [ValidationIssue("R03", "E_ENTRY_NOT_CLASS", "entry target is not a class")]

  requiredMethods = ["validateParams", "executeNode"]
  missingMethods = [methodName for methodName in requiredMethods if not hasattr(operatorClass, methodName)]
  if missingMethods:
    return None, [
      ValidationIssue("R03", "E_OPERATOR_METHOD_MISSING", f"missing methods: {','.join(missingMethods)}")
    ]

  return operatorClass, []


def validateConsistency(manifest: PluginManifest, operatorClass: type) -> list[ValidationIssue]:
  issues: list[ValidationIssue] = []
  if not hasattr(operatorClass, "meta"):
    issues.append(ValidationIssue("R04", "E_META_MISSING", "operator class missing meta"))
    return issues

  meta = getattr(operatorClass, "meta")
  metaInputPorts = getattr(meta, "inputPorts", None)
  metaOutputPorts = getattr(meta, "outputPorts", None)

  if metaInputPorts is not None and dict(metaInputPorts) != manifest.inputPorts:
    issues.append(ValidationIssue("R04", "E_META_INPUT_MISMATCH", "manifest inputPorts mismatch operator meta"))
  if metaOutputPorts is not None and dict(metaOutputPorts) != manifest.outputPorts:
    issues.append(ValidationIssue("R04", "E_META_OUTPUT_MISMATCH", "manifest outputPorts mismatch operator meta"))

  return issues
