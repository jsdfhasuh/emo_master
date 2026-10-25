import json
from types import SimpleNamespace

import pytest

from emo_master.apps.runtime.presentation.normal_capture import freezeNormalCapture
from emo_master.core.presentation.coverage import DispatchCoverage
from emo_master.core.presentation.validation import validateBindings
from emo_master.core.presentation.workflow_view import presentationForWorkflow, presentationForWorkflows
from tests.runtime.dispatch_page_fixture import sharedPageProject
from tests.runtime.test_builtin_vision_operator_workflows import _registry


def metadata(document, root, registry, path):
    frozen = freezeNormalCapture(document, registry, path, root)
    sources = json.loads(frozen.sourceJson)['sources']
    return SimpleNamespace(job_id=root, runtime_instance_id='runtime', project_id=document.project.projectId,
        run_group_id='group', source_job_id='main',
        target_slot_id='detection' if root == 'consumer' else '', capture_enabled=True,
        capture_plan_revision=frozen.capturePlanRevision, execution_revision=frozen.executionRevision,
        sources_json=json.dumps(sources), source_ids=list(sources), capture_definition_json=frozen.captureDefinitionJson)


def testSharedPageHasIndependentCapturePlans(tmp_path):
    document = sharedPageProject(tmp_path)
    registry = _registry()
    before = document.model_dump()
    assert not validateBindings(document, {key: item.manifest for key, item in registry.items()})
    assert not presentationForWorkflow(document, 'main').pages
    combined = presentationForWorkflows(document, {'main', 'consumer'})
    assert combined == document.presentation
    producer, consumer = [metadata(document, root, registry, tmp_path) for root in ('main', 'consumer')]
    coverage = DispatchCoverage.fromMetadata(producer, consumer)
    assert set(coverage.members[0].sources) == {'live'}
    assert set(coverage.members[1].sources) == {'count', 'image'}
    assert all(not coverage.sourceProblem(combined, key) for key in ('live', 'count', 'image'))
    assert document.model_dump() == before
    document.workflows['capture'].nodes = [node for node in document.workflows['capture'].nodes if node.kind != 'dispatch']
    assert any(issue.code == 'scope_invalid' for issue in validateBindings(document, {}))


@pytest.mark.parametrize('field,value', [('run_group_id', 'other'), ('project_id', 'other'),
    ('runtime_instance_id', 'other'), ('source_job_id', 'other'), ('target_slot_id', '')])
def testCannotCombineUnrelatedJobs(tmp_path, field, value):
    document = sharedPageProject(tmp_path)
    registry = _registry()
    producer, consumer = [metadata(document, root, registry, tmp_path) for root in ('main', 'consumer')]
    setattr(consumer, field, value)
    with pytest.raises(ValueError, match='unrelated'):
        DispatchCoverage.fromMetadata(producer, consumer)


def testGroupImageBudgetIsCheckedBeforeConnecting(tmp_path, monkeypatch):
    from emo_master.clients.runtime import dispatch_display
    from emo_master.core.presentation.models import Component, Placement
    document = sharedPageProject(tmp_path)
    config = document.presentation
    config.dataSources['third'] = config.dataSources['live'].model_copy(update={'nodeId': 'camera'})
    config.pages['consumer'].components.append(Component(componentId='third', type='image',
        layout=Placement(row=2), bindings={'image': 'third'}))
    registry = _registry()
    producer, consumer = [metadata(document, root, registry, tmp_path) for root in ('main', 'consumer')]
    def unexpected(*args, **kwargs):
        pytest.fail('over-budget page opened a connection')
    monkeypatch.setattr(dispatch_display, 'DisplaySession', unexpected)
    with pytest.raises(ValueError, match='two distinct image sources'):
        dispatch_display.DispatchDisplaySession('127.0.0.1:1', producer, consumer, config)
