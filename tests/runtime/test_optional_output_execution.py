"""Absent optional data skips dependent actions, not unrelated work or real errors."""
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from tests.runtime.test_builtin_vision_operator_workflows import _edge


def runnerForOptionalData(tmp_path, *, required=True, connected=True, requiredOutput=False,
                          requiredSource=False):
    calls, events = [], []

    class Source:
        meta = SimpleNamespace(inputPorts={'produce': 'boolean'},
                               outputPorts={'value': {'type': 'integer', 'required': requiredSource}})

        def executeNode(self, inputs, params, context):
            return dict(status='ok', outputs={'value': 7} if inputs['produce'] else {})

    class Action:
        meta = SimpleNamespace(inputPorts={'value': {'type': 'integer', 'required': required},
                                          'metadata': {'type': 'string', 'required': False}},
                               outputPorts={'value': {'type': 'integer', 'required': True}})

        def executeNode(self, inputs, params, context):
            calls.append((context['nodeId'], dict(inputs)))
            return dict(status='ok', outputs={'value': inputs.get('value', 0)})

    store = WorkflowStore()
    flow = store.get('main')
    flow.inputs = {'produce': 'boolean', 'metadata': 'string'}
    flow.outputs = {'value': {'type': 'integer', 'required': requiredOutput}, 'side': 'string'}
    start = next(node['nodeId'] for node in flow.nodes if node['kind'] == 'workflow_input')
    end = next(node['nodeId'] for node in flow.nodes if node['kind'] == 'workflow_output')
    flow.nodes[1:1] = [dict(nodeId='source', operatorId='test.source'),
                       dict(nodeId='first', operatorId='test.action'),
                       dict(nodeId='second', operatorId='test.action')]
    for node in flow.nodes[1:-1]:
        meta = Source.meta if node['nodeId'] == 'source' else Action.meta
        node['inputPorts'] = {name: normalizePortType(spec) for name, spec in meta.inputPorts.items()}
        node['outputPorts'] = {name: normalizePortType(spec) for name, spec in meta.outputPorts.items()}
    flow.edges = [_edge(start, 'produce', 'source', 'produce'),
                  _edge(start, 'metadata', 'first', 'metadata'),
                  _edge(start, 'metadata', 'second', 'metadata'),
                  _edge('first', 'value', 'second', 'value'),
                  _edge('second', 'value', end, 'value'),
                  _edge(start, 'metadata', end, 'side')]
    if connected:
        flow.edges.append(_edge('source', 'value', 'first', 'value'))
    document = ProjectDocument.model_validate(store.toPayload())
    path = tmp_path / 'optional.emoproj'
    saveProject(path, document.model_dump(mode='json'))
    assert loadProjectDocument(path) == document and document.schemaVersion == '2.7'
    registry = {'test.source': Source, 'test.action': Action}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    return WorkflowRunner(compiled, registry, eventPublisher=lambda **event: events.append(event)), calls, events


def run(runner, produce=False):
    return runner.run('main', {'produce': produce, 'metadata': 'current'},
                      RunContext.root('job', 'main'), CancellationToken())


def testMissingConnectedRequiredInputSkipsWholeDependentChainAndRecoversNextCall(tmp_path):
    runner, calls, events = runnerForOptionalData(tmp_path)
    assert run(runner, True).outputs == {'value': 7, 'side': 'current'}
    calls.clear()
    events.clear()
    assert run(runner).outputs == {'side': 'current'}
    assert calls == []
    skipped = [event for event in events if event['eventType'] == 'node.skipped']
    assert [event['context'].callerNodeId for event in skipped] == ['first', 'second']
    for event in skipped:
        assert event['payload']['code'] == 'E_INPUT_NOT_PRODUCED'
        assert event['payload']['missingInputs'] == ['value']
        assert 'value' in event['payload']['message']
        assert event['payload']['ioSummary']['inputs']['portCount'] == 1
        assert event['payload']['ioSummary']['outputs'] is None
    assert not any(event['eventType'].endswith('.failed') for event in events)
    assert run(runner, True).outputs == {'value': 7, 'side': 'current'}
    assert [node for node, _ in calls] == ['first', 'second']


def testMissingOptionalInputStillAllowsConsumerToRun(tmp_path):
    runner, calls, _ = runnerForOptionalData(tmp_path, required=False)
    assert run(runner).outputs == {'value': 0, 'side': 'current'}
    assert [node for node, _ in calls] == ['first', 'second']


@pytest.mark.parametrize('options,code', [
    ({'connected': False}, 'E_INPUT_MISSING'),
    ({'requiredSource': True}, 'E_OUTPUT_MISSING'),
    ({'requiredOutput': True}, 'E_OUTPUT_MISSING'),
])
def testMissingDataPolicyDoesNotHideBrokenContracts(tmp_path, options, code):
    runner, calls, events = runnerForOptionalData(tmp_path, **options)
    with pytest.raises(WorkflowExecutionError) as failure:
        run(runner)
    assert failure.value.code == code
    assert calls == []
    assert events[-1]['eventType'] == 'workflow.failed'
