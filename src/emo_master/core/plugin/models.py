from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OperatorEditorSpec:
  schemaVersion: str
  kind: str
  openMode: str
  uiResource: str
  controllerEntry: str
  fallback: str
  previewMode: str


@dataclass(frozen=True)
class PluginManifest:
  operatorId: str
  displayName: str
  version: str
  entry: str
  category: str
  iconKey: str
  summary: str
  inputPorts: dict[str, object]
  outputPorts: dict[str, object]
  paramSchema: dict[str, object]
  minCoreVersion: str
  maxCoreVersion: str
  editor: OperatorEditorSpec | None = None


@dataclass(frozen=True)
class ValidationIssue:
  ruleId: str
  code: str
  message: str


@dataclass(frozen=True)
class PluginDescriptor:
    manifest: PluginManifest
    operatorClass: type
    resourceRoot: Path | None = None
    editorIssues: tuple[ValidationIssue, ...] = ()
    editorUiContent: bytes | None = None
    editorUiSha256: str = ""


@dataclass
class RegistryScanResult:
  activeOperators: dict[str, PluginDescriptor]
  rejectedOperators: dict[str, list[ValidationIssue]]
