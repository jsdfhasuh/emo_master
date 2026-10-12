import json
from pathlib import Path

import numpy as np
import pytest

from emo_master.plugins.builtins.value_select.operator import ValueSelectOperator


@pytest.mark.parametrize('condition', [True, False])
@pytest.mark.parametrize('value', [False, 0, '', [], {'nested': [1]}, np.zeros((2, 3), np.uint8)])
def testSelectPreservesExactChosenValueAndIdentity(condition, value):
    key = 'trueValue' if condition else 'falseValue'
    other = 'falseValue' if condition else 'trueValue'
    operator = ValueSelectOperator()
    for inputs in ({'condition': condition, key: value}, {'condition': condition, key: value, other: object()}):
        result = operator.executeNode(inputs, {}, {})
        assert result['status'] == 'ok'
        assert result['outputs']['value'] is value


@pytest.mark.parametrize('inputs,code', [
    ({}, 'E_INPUT_MISSING'), ({'condition': None}, 'E_INPUT_TYPE'),
    ({'condition': 1}, 'E_INPUT_TYPE'), ({'condition': 'false'}, 'E_INPUT_TYPE'),
    ({'condition': True, 'falseValue': 42}, 'E_INPUT_MISSING'),
    ({'condition': False, 'trueValue': 42}, 'E_INPUT_MISSING'),
    ({'condition': True, 'trueValue': None}, 'E_INPUT_TYPE'),
])
def testSelectNeverFallsBackOrRemembersPreviousValue(inputs, code):
    operator = ValueSelectOperator()
    assert operator.executeNode({'condition': True, 'trueValue': 'old'}, {}, {})['status'] == 'ok'
    result = operator.executeNode(inputs, {}, {})
    assert result['status'] == 'error' and result['error']['code'] == code
    assert 'outputs' not in result


def testSelectManifestMatchesImplementationAndRejectsParams():
    from emo_master.plugins.builtins.value_select import operator as module
    manifest = json.loads(Path(module.__file__).with_name('manifest.json').read_text(encoding='utf-8'))
    for field in ('operatorId', 'displayName', 'version', 'inputPorts', 'outputPorts', 'paramSchema'):
        assert manifest[field] == getattr(ValueSelectOperator.meta, field)
    assert ValueSelectOperator().validateParams({}) is None
    assert ValueSelectOperator().validateParams({'fallback': 1})['code'] == 'E_PARAM_INVALID'


@pytest.mark.parametrize('reverse', [False, True])
def testSelectMergesRealExclusiveBranchesThroughTypedWorkflowOutput(tmp_path, reverse):
    from emo_master import __version__
    from emo_master.apps.runtime.workflow.cancellation import CancellationToken
    from emo_master.apps.runtime.workflow.context import RunContext
    from emo_master.apps.runtime.workflow.runner import WorkflowRunner
    from emo_master.core.plugin.registry import PluginRegistry
    from emo_master.core.presentation.models import Presentation
    from emo_master.core.project.models import ProductionSettings
    from emo_master.core.project.resources import ResourcePlan
    from emo_master.core.workflow.compiler import WorkflowCompiler
    root = Path(__file__).resolve().parents[2]
    registry = PluginRegistry(__version__).scan(root / 'src/emo_master/plugins').activeOperators
    def edge(source, port, target, target_port):
        return dict(fromNode=source, fromPort=port, toNode=target, toPort=target_port)
    flow = dict(name='generic selection', inputs={'condition': 'boolean'}, outputs={'value': 'boolean'}, nodes=[
        dict(nodeId='input', kind='workflow_input'),
        dict(nodeId='branch', kind='operator', operatorId='vision.flow.if', params={'mode': 'bool'}),
        dict(nodeId='select', kind='operator', operatorId='vision.flow.select_value'),
        dict(nodeId='output', kind='workflow_output')], edges=[
        edge('input', 'condition', 'branch', 'value'), edge('input', 'condition', 'select', 'condition'),
        edge('branch', 'true', 'select', 'trueValue'), edge('branch', 'false', 'select', 'falseValue'),
        edge('select', 'value', 'output', 'value')])
    if reverse:
        flow['nodes'].reverse()
        flow['edges'].reverse()
    payload = dict(schemaVersion='2.4', project=dict(projectId='selection', name='selection', revision=1,
        createdAt='2026-10-11T00:00:00Z', updatedAt='2026-10-11T00:00:00Z'),
        entryWorkflowId='main', workflowOrder=['main'], workflows={'main': flow},
        presentation=Presentation().model_dump(), resources=ResourcePlan().model_dump(),
        production=ProductionSettings().model_dump(), globalVariables={})
    runner = WorkflowRunner(WorkflowCompiler(registry).compile(payload), registry, retainOperators=True)
    context = RunContext.root('selection', 'main', str(tmp_path))
    try:
        for condition in (True, False, True, False):
            result = runner.run('main', {'condition': condition}, context, CancellationToken())
            assert result.outputs['value'] is condition
    finally:
        runner.closeSession(context)
