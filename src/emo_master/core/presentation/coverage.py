"""Validated immutable capture metadata for observing a previously started Job."""
from dataclasses import dataclass
import json
import re
from types import MappingProxyType
from typing import Mapping

from emo_master.core.presentation.models import DataSource, ResultScope
from emo_master.core.project.snapshots import canonicalJson, revisionOf


@dataclass(frozen=True)
class CaptureCoverage:
    jobId: str
    runtimeInstanceId: str
    capturePlanRevision: str
    executionRevision: str
    sources: Mapping[str, str]
    scopes: Mapping[str, str]
    enabled: bool

    @classmethod
    def fromMetadata(cls, metadata):
        """Fail closed on malformed/inconsistent server metadata, never infer values."""
        job = str(getattr(metadata, 'job_id', ''))
        runtime = str(getattr(metadata, 'runtime_instance_id', ''))
        if not job or not runtime:
            raise ValueError('任务身份元数据不完整')
        enabled = bool(getattr(metadata, 'capture_enabled', False))
        capture = str(getattr(metadata, 'capture_plan_revision', ''))
        execution = str(getattr(metadata, 'execution_revision', ''))
        if not enabled:
            return cls(job, runtime, capture, execution, MappingProxyType({}), MappingProxyType({}), False)
        if not all(re.fullmatch('[0-9a-f]{64}', value) for value in (capture, execution)):
            raise ValueError('任务采集修订元数据不完整')
        try:
            rawSources = json.loads(metadata.sources_json)
            definition = json.loads(metadata.capture_definition_json)
            if not isinstance(rawSources, dict) or set(definition) != {'ruleVersion', 'sources', 'scopes'}:
                raise ValueError('任务采集定义不完整')
            if definition['ruleVersion'] != '1.0' or revisionOf(definition) != capture:
                raise ValueError('任务采集定义摘要不匹配')
            sources = {key: canonicalJson(DataSource.model_validate(value).model_dump())
                       for key, value in rawSources.items()}
            scopes = {key: canonicalJson(ResultScope.model_validate(value).model_dump())
                      for key, value in definition['scopes'].items()}
            if set(sources) != set(metadata.source_ids):
                raise ValueError('任务来源清单不匹配')
            if set(sources.values()) != {canonicalJson(value) for value in definition['sources']}:
                raise ValueError('任务来源定义不匹配')
            if any(value['resultScopeId'] not in scopes for value in rawSources.values()):
                raise ValueError('任务结果作用域缺失')
        except (AttributeError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError('任务采集元数据无法读取') from error
        return cls(job, runtime, capture, execution, MappingProxyType(sources), MappingProxyType(scopes), True)

    def sourceProblem(self, presentation, sourceId):
        if sourceId not in self.sources:
            return 'SOURCE_NOT_CAPTURED · 此任务未采集，新来源下次明确启动生效'
        source = presentation.dataSources.get(sourceId)
        if source is None or canonicalJson(source.model_dump()) != self.sources[sourceId]:
            return 'SOURCE_DEFINITION_CHANGED · 来源已变更，下次明确启动生效'
        scope = presentation.resultScopes.get(source.resultScopeId)
        if scope is None or canonicalJson(scope.model_dump()) != self.scopes.get(source.resultScopeId):
            return 'SCOPE_DEFINITION_CHANGED · 作用域已变更，下次明确启动生效'
        return ''

    def matches(self, identity):
        return (identity.jobId == self.jobId and identity.runtimeInstanceId == self.runtimeInstanceId
                and identity.capturePlanRevision == self.capturePlanRevision
                and identity.executionRevision == self.executionRevision)
