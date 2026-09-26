"""Declared resource and site parameter contracts; never guesses path strings."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from emo_master.core.presentation.models import Id, Model


def safeRelativePath(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or "\\" in value or ":" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("resource path must be a normalized relative POSIX path")
    return value


class Resource(Model):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    purpose: Literal["input_image", "model", "page_image", "icon"]

    @model_validator(mode="after")
    def checkPath(self) -> Resource:
        safeRelativePath(self.path)
        return self


class ParameterTarget(Model):
    workflowId: Id
    nodeId: Id
    parameterPath: list[Id] = Field(min_length=1, max_length=16)


class ResourceBinding(Model):
    target: ParameterTarget
    resourceId: Id


class SiteBinding(Model):
    target: ParameterTarget
    field: Id
    purpose: Literal["output_directory", "output_file", "device_address", "camera_serial", "secret_ref"]
    required: bool = True


class ResourcePlan(Model):
    schemaVersion: Literal["1.0"] = "1.0"
    items: dict[Id, Resource] = Field(default_factory=dict)
    parameterBindings: list[ResourceBinding] = Field(default_factory=list)
    siteBindings: list[SiteBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def uniqueTargets(self) -> ResourcePlan:
        seen: list[ParameterTarget] = []
        bindings: list[ResourceBinding | SiteBinding] = [*self.parameterBindings, *self.siteBindings]
        for binding in bindings:
            target = binding.target
            for other in seen:
                if (target.workflowId, target.nodeId) == (other.workflowId, other.nodeId):
                    a, b = target.parameterPath, other.parameterPath
                    if a[:len(b)] == b or b[:len(a)] == a:
                        raise ValueError("resource/site parameter targets overlap")
            seen.append(target)
        paths = [item.path.casefold() for item in self.items.values()]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate resource path")
        return self
