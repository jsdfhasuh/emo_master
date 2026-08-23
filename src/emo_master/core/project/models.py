from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectMeta:
  projectId: str
  name: str
  version: str
  schemaVersion: str
