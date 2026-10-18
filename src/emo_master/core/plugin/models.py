from dataclasses import dataclass, field
from pathlib import Path
from emo_master.core.plugin.devices import DeviceRequirement, DeviceType


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
  iconResource: str = ""
  deviceRequirements: dict[str, DeviceRequirement] = field(default_factory=dict)
  deviceBindingSince: str = ""
  nextCycleParameterPaths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationIssue:
  ruleId: str
  code: str
  message: str


@dataclass(frozen=True)
class PluginIconAsset:
    content: bytes
    mimeType: str
    sha256: str


@dataclass(frozen=True)
class PluginDescriptor:
    manifest: PluginManifest
    operatorClass: type
    resourceRoot: Path | None = None
    editorIssues: tuple[ValidationIssue, ...] = ()
    editorUiContent: bytes | None = None
    editorUiSha256: str = ""
    iconAsset: PluginIconAsset | None = None
    iconIssues: tuple[ValidationIssue, ...] = ()

    @property
    def iconStatus(self) -> str:
        return "invalid" if self.iconIssues else "ready" if self.iconAsset else "none"


@dataclass
class RegistryScanResult:
  activeOperators: dict[str, PluginDescriptor]
  rejectedOperators: dict[str, list[ValidationIssue]]
  deviceTypes: dict[str, DeviceType] = field(default_factory=dict)
  rejectedDeviceTypes: dict[str, list[ValidationIssue]] = field(default_factory=dict)
