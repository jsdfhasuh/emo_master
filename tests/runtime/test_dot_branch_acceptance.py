"""Extend dot B's Switch check to real downstream branch consumers."""
import pytest

from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.flow_if.operator import FlowIfOperator
from emo_master.plugins.builtins.flow_switch.operator import FlowSwitchOperator
from tests.runtime.test_workflow_debug_control import nestedProject


@pytest.mark.parametrize('value,branch,port', [(True, 'case0', 'true'), (False, 'case1', 'false'),
    (1, 'case2', 'true'), ('true', 'default', 'true'), ('unmatched', 'default', 'true')])
def testSwitchExecutesOnlyTheSelectedDownstreamConsumer(value, branch, port):
    payload = nestedProject()
    names = ['case0', 'case1', 'case2', 'default']
    workflow = dict(name='B branch consumers', inputs={'value': 'any'}, outputs={
        name + '_' + side: dict(type='any', required=False) for name in names for side in ['true', 'false']},
        nodes=[dict(nodeId='input', kind='workflow_input'), dict(nodeId='switch', operatorId='vision.flow.switch',
            params={'case0Value': 'True', 'case1Value': 'False', 'case2Value': '1'}),
            *[dict(nodeId='consumer-' + name, operatorId='vision.flow.if', params={'mode': 'bool'}) for name in names],
            dict(nodeId='output', kind='workflow_output')],
        edges=[dict(fromNode='input', fromPort='value', toNode='switch', toPort='value'),
            *[dict(fromNode='switch', fromPort=name, toNode='consumer-' + name, toPort='value') for name in names],
            *[dict(fromNode='consumer-' + name, fromPort=side, toNode='output', toPort=name + '_' + side)
              for name in names for side in ['true', 'false']]])
    payload.update(workflowOrder=['main'], workflows={'main': workflow})
    registry = {'vision.flow.switch': FlowSwitchOperator, 'vision.flow.if': FlowIfOperator}
    events = []
    runner = WorkflowRunner(WorkflowCompiler(registry).compile(payload), registry,
        eventPublisher=lambda **event: events.append(event))
    result = runner.run('main', {'value': value}, RunContext.root('dot-B', 'main'), CancellationToken())
    assert result.outputs == {branch + '_' + port: value}
    started = {event['context'].callerNodeId for event in events if event['eventType'] == 'node.started'}
    assert started & {'consumer-' + name for name in names} == {'consumer-' + branch}
    skipped = {event['context'].callerNodeId for event in events if event['eventType'] == 'node.skipped'}
    assert skipped & {'consumer-' + name for name in names} == {'consumer-' + name for name in names if name != branch}
