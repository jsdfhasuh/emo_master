"""UI-independent output capture configuration shared by ordinary clients."""
from typing import Literal

from pydantic import Field, model_validator

from .models import DataSource, Id, Model, ResultScope
from .canonical import revisionOf


class OutputRequestConfig(Model):
    schemaVersion: Literal['1.0'] = '1.0'
    sources: dict[Id, DataSource] = Field(default_factory=dict, max_length=256)
    scopes: dict[Id, ResultScope] = Field(default_factory=dict, max_length=256)

    @model_validator(mode='after')
    def outputOnly(self):
        for key, source in self.sources.items():
            if source.kind not in {'node_output', 'workflow_output'}:
                raise ValueError(f'{key}: external capture accepts workflow/node outputs only')
            if source.resultScopeId not in self.scopes:
                raise ValueError(f'{key}: result scope is missing')
        return self

    @property
    def revision(self):
        return revisionOf(self.model_dump())


def stableOutputIds(source, scope):
    """Identity depends only on addresses and scope, never names or UI layout."""
    scopeId = 'external-scope-' + revisionOf(scope.model_dump())[:32]
    value = source.model_copy(update={'resultScopeId': scopeId})
    sourceId = 'external-source-' + revisionOf(value.model_dump())[:32]
    return sourceId, scopeId, value
