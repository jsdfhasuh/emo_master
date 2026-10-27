"""Real inference omissions propagate through mutually exclusive input contracts."""
import base64

import numpy as np
import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.context.global_variables import ReadOnlyVariables
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowExecutionError, WorkflowRunner
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins._geometry_bridges import GeometryPointsOperator
from emo_master.plugins.builtins._shape_operators import ShapeMeasurementOperator
from emo_master.plugins.builtins.blob_analysis.operator import BlobAnalysisOperator
from emo_master.plugins.builtins.collection_count.operator import CollectionCountOperator
from emo_master.plugins.builtins.collection_filter.operator import CollectionFilterOperator
from emo_master.plugins.builtins.collection_select.operator import CollectionSelectOperator
from emo_master.plugins.builtins.collection_sort.operator import CollectionSortOperator
from emo_master.plugins.builtins.result_writer.operator import ResultWriterOperator
from emo_master.plugins.builtins.yolo_inference.operator import YoloInferenceOperator
from tests.plugins.test_yolo_onnx_backend import _TINY_YOLO_ONNX
from tests.runtime.test_builtin_vision_operator_workflows import _edge
from tests.runtime.test_global_variables import variable


CASES = [
    (CollectionCountOperator, 'collectionType', 'blobs', {}),
    (CollectionFilterOperator, 'collectionType', 'blobs', {}),
    (CollectionSortOperator, 'collectionType', 'blobs', {}),
    (CollectionSelectOperator, 'collectionType', 'blobs', {'mode': 'index'}),
    (ShapeMeasurementOperator, 'inputType', 'blobs', {}),
    (ResultWriterOperator, 'inputType', 'blobs', {'overwrite': True}),
    (GeometryPointsOperator, 'inputType', 'bbox', {'feature': 'bboxCenter'}),
]


def choiceRunner(tmp_path, case, mode='explicit', connected=True):
    consumer, selector, port, params = case
    params = dict(params)
    model = tmp_path / 'tiny.onnx'
    model.write_bytes(base64.b64decode(_TINY_YOLO_ONNX))
    store = WorkflowStore()
    store.projectExtensions['globalVariables'] = {
        'confidence': variable(.25, 'number', name='confidence'),
        'objectType': variable(port, 'string', name='objectType')}
    flow = store.get('main')
    flow.inputs = {'image': 'image', 'index': 'integer'}
    flow.outputs = {'detections': 'detectionCollection'}
    start = next(node['nodeId'] for node in flow.nodes if node['kind'] == 'workflow_input')
    end = next(node['nodeId'] for node in flow.nodes if node['kind'] == 'workflow_output')
    if mode != 'legacy':
        params[selector] = port if mode == 'explicit' else 'unfinished literal'
    classes = [('yolo', YoloInferenceOperator, dict(modelPath=str(model), outputCrop=True, onMissingCrop='skip'))]
    if port == 'blobs':
        classes.append(('blob', BlobAnalysisOperator, {}))
    classes.append(('consumer', consumer, params))
    registry = {}
    for name, cls, values in classes:
        meta = cls.meta
        node = dict(nodeId=name, operatorId=meta.operatorId, params=values,
            inputPorts={key: normalizePortType(value) for key, value in meta.inputPorts.items()},
            outputPorts={key: normalizePortType(value) for key, value in meta.outputPorts.items()},
            paramSchema=meta.paramSchema)
        if name == 'yolo':
            node['globalVariableBindings'] = [dict(parameterPath=['confidence'], variableId='confidence')]
        elif name == 'consumer' and mode == 'bound':
            node['globalVariableBindings'] = [dict(parameterPath=[selector], variableId='objectType')]
        flow.nodes.insert(-1, node)
        store.dependencies['operators'].append(dict(operatorId=meta.operatorId, version=meta.version))
        registry[meta.operatorId] = cls
    flow.edges = [_edge(start, 'image', 'yolo', 'image'), _edge('yolo', 'detections', end, 'detections')]
    if port == 'blobs':
        flow.edges += [_edge('yolo', 'croppedImage', 'blob', 'image'), _edge('yolo', 'croppedFrame', 'blob', 'frame')]
    if connected:
        flow.edges.append(_edge('blob', 'blobs', 'consumer', port) if port == 'blobs'
                          else _edge('yolo', 'croppedFrame', 'consumer', port))
    if consumer is CollectionSelectOperator:
        flow.edges.append(_edge(start, 'index', 'consumer', 'index'))
    document = ProjectDocument.model_validate(store.toPayload())
    path = tmp_path / 'choice-inputs.emoproj'
    saveProject(path, document.model_dump(mode='json'))
    assert loadProjectDocument(path) == document and document.schemaVersion == '2.7'
    compiled = WorkflowCompiler(registry).compile(document)
    variables = ReadOnlyVariables({'confidence': .25, 'objectType': port})
    events = []
    runner = WorkflowRunner(compiled, registry, globalVariables=variables,
                            eventPublisher=lambda **event: events.append(event))
    return runner, variables, events


@pytest.mark.parametrize('mode', ['explicit', 'legacy', 'bound'])
@pytest.mark.parametrize('case', CASES, ids=[case[0].__name__ for case in CASES])
def testChoiceInputSkipsAndRecoversWithoutTreatingEmptyCollectionAsMissing(tmp_path, case, mode):
    runner, variables, events = choiceRunner(tmp_path, case, mode)
    YoloInferenceOperator.clearCache()
    try:
        for confidence, status in ((.25, 'node.completed'), (.95, 'node.skipped'), (.25, 'node.completed')):
            variables.values['confidence'] = confidence
            events.clear()
            result = runner.run('main', {'image': np.zeros((4, 8, 3), np.uint8), 'index': 0},
                RunContext.root('job', 'main', workspacePath=str(tmp_path)), CancellationToken())
            assert len(result.outputs['detections']['items']) == (0 if confidence == .95 else 1)
            terminal = [e for e in events if e['context'].callerNodeId == 'consumer'
                        and e['eventType'] in {'node.completed', 'node.skipped', 'node.failed'}]
            assert [e['eventType'] for e in terminal] == [status]
            if status == 'node.skipped':
                assert terminal[0]['payload']['missingInputs'] == [case[2]]
                assert terminal[0]['payload']['code'] == 'E_INPUT_NOT_PRODUCED'
            assert not any(e['eventType'].endswith('.failed') for e in events)
    finally:
        YoloInferenceOperator.clearCache()


@pytest.mark.parametrize('case', CASES, ids=[case[0].__name__ for case in CASES])
def testChoiceInputWithoutConnectionStillFails(tmp_path, case):
    runner, variables, events = choiceRunner(tmp_path, case, connected=False)
    variables.values['confidence'] = .95
    try:
        with pytest.raises(WorkflowExecutionError) as failure:
            runner.run('main', {'image': np.zeros((4, 8, 3), np.uint8), 'index': 0},
                RunContext.root('job', 'main', workspacePath=str(tmp_path)), CancellationToken())
        assert failure.value.code == 'E_INPUT_MISSING'
        assert any(e['eventType'] == 'node.failed' and e['context'].callerNodeId == 'consumer' for e in events)
    finally:
        YoloInferenceOperator.clearCache()
