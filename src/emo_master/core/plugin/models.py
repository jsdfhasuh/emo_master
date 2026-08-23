from dataclasses import dataclass


@dataclass(frozen=True)
class PluginManifest:
  operatorId: str
  displayName: str
  version: str
  entry: str
  category: str
  iconKey: str
  summary: str
  inputPorts: dict[str, str]
  outputPorts: dict[str, str]
  paramSchema: dict[str, object]
  minCoreVersion: str
  maxCoreVersion: str


@dataclass(frozen=True)
class ValidationIssue:
  ruleId: str
  code: str
  message: str


@dataclass(frozen=True)
class PluginDescriptor:
  manifest: PluginManifest
  operatorClass: type


@dataclass
class RegistryScanResult:
  activeOperators: dict[str, PluginDescriptor]
  rejectedOperators: dict[str, list[ValidationIssue]]
