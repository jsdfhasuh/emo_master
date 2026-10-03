"""Partial source reuse is allowed only under a verified frozen capture manifest."""
import json
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

import pytest

from emo_master.clients.runtime.view_state import ScopeView, SessionView
from emo_master.core.presentation.coverage import CaptureCoverage
from emo_master.core.presentation.models import Component, Placement, walkComponents
from emo_master.core.presentation.results import ClosedResult, ClosedSource, ResultIdentity
from emo_master.core.project.snapshots import captureDefinition, revisionOf
from emo_master.ui.presentation.renderer import RuntimePages
from examples.runtime_pages_p3 import sampleProjectP3


def frozen(config):
    definition = captureDefinition(config)
    used = {key for page in config.pages.values() for component in walkComponents(page.components)
            for key in component.bindings.values()}
    return SimpleNamespace(job_id='job', runtime_instance_id='runtime', capture_enabled=True,
        capture_plan_revision=revisionOf(definition), execution_revision='a'*64, source_ids=sorted(used),
        sources_json=json.dumps({key: config.dataSources[key].model_dump() for key in used}),
        capture_definition_json=json.dumps(definition))


def resultView(coverage):
    identity = ResultIdentity(runtimeInstanceId='runtime', jobId='job', resultScopeId='root',
        invocationId='one', resultKey='result', resultOrdinal=1, executionRevision='a'*64,
        capturePlanRevision=coverage.capturePlanRevision, mode='debug')
    result = ClosedResult(identity=identity, expectedSourceIds=('count',),
        sources=(ClosedSource(sourceId='count', state='AVAILABLE', valueJson='0'),),
        status='COMPLETE', executionTerminal='COMPLETED')
    scope = ScopeView(result, MappingProxyType({}), MappingProxyType({}), 0)
    empty = MappingProxyType({})
    return SessionView(1, 1, 'runtime', 'job', 'CONNECTED', '', MappingProxyType({'root': scope}), empty, empty)


def testNewSourceDoesNotHideExistingSameJobResults(qtApp, tmp_path):
    config = sampleProjectP3(tmp_path).presentation
    coverage = CaptureCoverage.fromMetadata(frozen(config))
    config.dataSources['new-count'] = config.dataSources['count'].model_copy(deep=True)
    config.dataSources['new-count'].port = 'other'
    config.pages['overview'].components.append(Component(componentId='new', type='number',
        layout=Placement(row=9), bindings={'value': 'new-count'}))
    window = RuntimePages(config)
    window.setCaptureCoverage(coverage)
    view = resultView(coverage)
    window.submit(view)
    assert window.widgets['overview']['overview-count'][1].text() == '0 个'
    assert 'SOURCE_NOT_CAPTURED' in window.widgets['overview']['new'][1].text()
    window.navigate('detail')
    assert window.widgets['detail']['detail-count'][1].text() == '0 个'
    window.close()


def testChangedSourceAndScopeAreDistinctAndNeverShowStaleValues(qtApp, tmp_path):
    config = sampleProjectP3(tmp_path).presentation
    coverage = CaptureCoverage.fromMetadata(frozen(config))
    config.dataSources['count'].port = 'other'
    assert 'SOURCE_DEFINITION_CHANGED' in coverage.sourceProblem(config, 'count')
    config.dataSources['count'].port = 'count'
    config.resultScopes['root'].entryWorkflowId = 'changed-entry'
    assert 'SCOPE_DEFINITION_CHANGED' in coverage.sourceProblem(config, 'count')
    window = RuntimePages(config)
    window.setCaptureCoverage(coverage)
    window.submit(resultView(coverage))
    assert 'SCOPE_DEFINITION_CHANGED' in window.widgets['overview']['overview-count'][1].text()
    window.close()


@pytest.mark.parametrize('field,value', [('jobId', 'other-job'), ('runtimeInstanceId', 'new-runtime'),
    ('executionRevision', 'c'*64), ('capturePlanRevision', 'd'*64)])
def testFrozenMetadataNeverCrossesIdentityFence(qtApp, tmp_path, field, value):
    config = sampleProjectP3(tmp_path).presentation
    coverage = CaptureCoverage.fromMetadata(frozen(config))
    view = resultView(coverage)
    scope = view.scopes['root']
    changed = scope.result.model_copy(update={'identity': scope.result.identity.model_copy(update={field: value})})
    view = replace(view, scopes=MappingProxyType({'root': replace(scope, result=changed)}))
    window = RuntimePages(config)
    window.setCaptureCoverage(coverage)
    window.submit(view)
    assert 'CAPTURE_IDENTITY_MISMATCH' in window.widgets['overview']['overview-count'][1].text()
    window.close()


def testMalformedMetadataCannotRelaxStrictFallback(qtApp, tmp_path):
    config = sampleProjectP3(tmp_path).presentation
    metadata = frozen(config)
    metadata.capture_plan_revision = 'f'*64
    with pytest.raises(ValueError, match='摘要'):
        CaptureCoverage.fromMetadata(metadata)
    metadata = frozen(config)
    metadata.source_ids = ['unknown']
    with pytest.raises(ValueError, match='来源清单'):
        CaptureCoverage.fromMetadata(metadata)
    window = RuntimePages(config)
    coverage = CaptureCoverage.fromMetadata(frozen(config))
    config.dataSources['count'].port = 'changed'
    window.reload(config)
    window.submit(resultView(coverage))
    assert 'CAPTURE_REVISION_MISMATCH' in window.widgets['overview']['overview-count'][1].text()
    window.close()
