"""Read-only preview handoff; no connection is inferred from the page package."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from .connection import ConnectionProfile, validateRuntimeTarget
from .messages import Revision
from .models import Id, Model


class PreviewRuntime(Model):
    target: str
    timeoutSeconds: float = Field(default=5.0, gt=0, le=60)
    maxReceiveMessageBytes: int = Field(default=9 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    runtimeInstanceId: Id
    documentRevision: str = Field(pattern=r'^[1-9][0-9]*$')

    @field_validator('target')
    @classmethod
    def checkTarget(cls, value):
        return validateRuntimeTarget(value)

    def connectionProfile(self) -> ConnectionProfile:
        return ConnectionProfile(self.target, self.timeoutSeconds, self.maxReceiveMessageBytes)


class PreviewContext(Model):
    previewContextVersion: Literal['1'] = '1'
    mode: Literal['fixtures', 'runtime']
    readOnly: Literal[True] = True
    projectId: Id
    packageRevision: Revision
    historyDefinitionRevision: Revision
    runtime: PreviewRuntime | None = None

    @model_validator(mode='after')
    def checkMode(self):
        if (self.mode == 'runtime') != (self.runtime is not None):
            raise ValueError('runtime mode requires a Runtime connection; fixtures forbids one')
        return self

    @classmethod
    def forPackage(cls, manifest):
        return cls(mode='fixtures', projectId=manifest.projectId, packageRevision=manifest.packageRevision,
                   historyDefinitionRevision=manifest.historyDefinitionRevision)

    @classmethod
    def forRuntime(cls, manifest, profile: ConnectionProfile, info):
        context = cls(mode='runtime', projectId=manifest.projectId, packageRevision=manifest.packageRevision,
            historyDefinitionRevision=manifest.historyDefinitionRevision, runtime=PreviewRuntime(
                target=profile.target, timeoutSeconds=profile.timeoutSeconds,
                maxReceiveMessageBytes=profile.maxReceiveMessageBytes,
                runtimeInstanceId=info.runtime_instance_id, documentRevision=str(info.document_revision)))
        context.validateRuntimeInfo(info)
        return context

    def validateRuntimeInfo(self, info):
        """Gate online reads on the same instance and formal history declaration.

        Document revision is informational: a layout-only publication may advance
        it without invalidating this read-only preview. Jobs still need Coverage.
        """
        if self.runtime is None:
            raise ValueError('示例预览不连接 Runtime')
        if not info.ok:
            raise ValueError(info.code + ': ' + info.message)
        if info.runtime_instance_id != self.runtime.runtimeInstanceId:
            raise ValueError('Runtime 实例已变化，请重新打开 Web 预览')
        if info.project_id != self.projectId:
            raise ValueError('当前 Runtime 的正式工程与页面包不一致，请选择对应 Runtime')
        if info.history_definition_revision != self.historyDefinitionRevision:
            raise ValueError('页面的历史数据声明与 Runtime 正式配置不一致，请先明确写入配置，或使用示例预览')
