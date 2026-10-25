"""Real CPU inference and preview capture, not field-model accuracy acceptance."""
import base64
import threading
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.run_result_history import RunResultHistory
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.context.global_variables import ReadOnlyVariables
from emo_master.apps.runtime.jobs.worker_main import _runContinuous
from emo_master.apps.runtime.preview.store import PreviewSnapshotWriter
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.yolo_inference.operator import YoloInferenceOperator
from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator
from tests.plugins.test_yolo_onnx_backend import _TINY_YOLO_ONNX
from tests.runtime.test_builtin_vision_operator_workflows import _edge, _registry
from tests.runtime.test_global_variables import variable


@pytest.mark.parametrize('overlayParams', [{}, {'drawOverlay': False}], ids=['default-on', 'explicit-off'])
@pytest.mark.parametrize('confidence,expectedCount', [(0.25, 1), (0.95, 0)])
def testYoloInvocationPublishesOverlayOrExactOmissionReason(tmp_path, overlayParams, confidence, expectedCount):
    model = tmp_path / 'tiny.onnx'
    model.write_bytes(base64.b64decode(_TINY_YOLO_ONNX))
    meta = YoloInferenceOperator.meta
    store = WorkflowStore()
    workflow = store.get('main')
    workflow.inputs = {'image': 'image'}
    workflow.outputs = {'detections': 'detectionCollection'}
    inputId = next(node['nodeId'] for node in workflow.nodes if node['kind'] == 'workflow_input')
    outputId = next(node['nodeId'] for node in workflow.nodes if node['kind'] == 'workflow_output')
    workflow.nodes.insert(1, {
        'nodeId': 'yolo', 'operatorId': meta.operatorId, 'displayName': 'YOLO',
        'params': {'modelPath': str(model), 'confidence': confidence, **overlayParams},
        'inputPorts': {name: normalizePortType(spec) for name, spec in meta.inputPorts.items()},
        'outputPorts': {name: normalizePortType(spec) for name, spec in meta.outputPorts.items()},
        'paramSchema': meta.paramSchema})
    workflow.edges = [_edge(inputId, 'image', 'yolo', 'image'),
                      _edge('yolo', 'detections', outputId, 'detections')]
    store.dependencies['operators'] = [{'operatorId': meta.operatorId, 'version': meta.version}]
    document = ProjectDocument.model_validate(store.toPayload('YOLO Inspection'))
    assert document.schemaVersion == '2.7' and document.devices.definitions == {}
    path = tmp_path / 'inspection.emoproj'
    saveProject(path, document.model_dump(mode='json'))
    assert loadProjectDocument(path) == document
    registry = _registry()
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    history = RunResultHistory()
    history.accept('job', [{**node.model_dump(), 'workflowId': 'main'}
                           for node in document.workflows['main'].nodes], revision=document.project.revision)
    events = []

    def publish(**event):
        events.append(event)
        context = event['context']
        history.applyEvent({**event, 'sequence': len(events), 'jobId': context.jobId,
                            'workflowId': context.workflowId, 'nodeId': context.callerNodeId,
                            'workflowRunId': context.workflowRunId, 'nodeRunId': context.nodeRunId})

    snapshots = []
    writer = PreviewSnapshotWriter(tmp_path, jobId='job', projectRevision=document.project.revision,
                                   inspectionPublisher=lambda **event: snapshots.append(event))
    image = np.zeros((4, 8, 3), dtype=np.uint8)
    YoloInferenceOperator.clearCache()
    try:
        result = WorkflowRunner(compiled, registry, eventPublisher=publish, previewSnapshotStore=writer).run(
            'main', {'image': image}, RunContext.root('job', 'main', workspacePath=str(tmp_path)), CancellationToken())
    finally:
        YoloInferenceOperator.clearCache()
    assert result.metrics['detectionCount'] == expectedCount
    record = history.view('main', 'yolo')['record']
    assert record['status'] == 'COMPLETED'
    completed = next(event for event in events if event['eventType'] == 'node.completed'
                     and event['context'].callerNodeId == 'yolo')
    assert record['imageOutputMessages'] == completed['payload']['diagnostics']['imageOutputMessages']
    entries = [entry for snapshot in snapshots for entry in snapshot['payload']['entries']
               if entry['nodeId'] == 'yolo']
    if overlayParams.get('drawOverlay', True):
        assert len(entries) == 1 and entries[0]['port'] == 'overlay'
        entry = entries[0]
        assert entry['nodeRunId'] == record['nodeRunId']
        assert entry['workflowRunId'] == record['workflowRunId']
        displayed = cv2.imdecode(np.fromfile(writer.inspectionRoot / entry['relativePath'], np.uint8), cv2.IMREAD_COLOR)
        assert displayed.shape == image.shape
        assert bool(np.any(displayed != image)) == bool(expectedCount)
        assert 'overlay' not in record['imageOutputMessages']
    else:
        assert entries == []
        assert 'overlay' not in completed['payload']['outputs']
        assert '绘制叠加图' in record['imageOutputMessages']['overlay']
    assert 'croppedImage' in record['imageOutputMessages']
    assert not np.any(image)


def testMissingCropSkipsDynamicFileSaveAndContinuousRunRecoversWithCurrentImages(tmp_path):
    model = tmp_path / 'tiny.onnx'
    model.write_bytes(base64.b64decode(_TINY_YOLO_ONNX))
    store = WorkflowStore()
    store.projectExtensions['globalVariables'] = {'confidence': variable(.25, 'number')}
    workflow = store.get('main')
    workflow.inputs = {'image': 'image', 'fileName': 'string'}
    workflow.outputs = {'detections': 'detectionCollection'}
    start = next(node['nodeId'] for node in workflow.nodes if node['kind'] == 'workflow_input')
    end = next(node['nodeId'] for node in workflow.nodes if node['kind'] == 'workflow_output')
    for nodeId, meta, params in (
        ('yolo', YoloInferenceOperator.meta, dict(modelPath=str(model), outputCrop=True, onMissingCrop='skip')),
        ('save', ImageSaverOperator.meta, dict(outputPath=str(tmp_path / 'images' / 'crop.png'))),
    ):
        workflow.nodes.insert(-1, dict(nodeId=nodeId, operatorId=meta.operatorId, params=params,
            inputPorts={name: normalizePortType(spec) for name, spec in meta.inputPorts.items()},
            outputPorts={name: normalizePortType(spec) for name, spec in meta.outputPorts.items()},
            paramSchema=meta.paramSchema))
        store.dependencies['operators'].append(dict(operatorId=meta.operatorId, version=meta.version))
    workflow.nodes[1]['globalVariableBindings'] = [dict(parameterPath=['confidence'], variableId='confidence')]
    workflow.edges = [_edge(start, 'image', 'yolo', 'image'),
                      _edge(start, 'fileName', 'save', 'fileName'),
                      _edge('yolo', 'croppedImage', 'save', 'image'),
                      _edge('yolo', 'detections', end, 'detections')]
    document = ProjectDocument.model_validate(store.toPayload())
    path = tmp_path / 'crop-continuous.emoproj'
    saveProject(path, document.model_dump(mode='json'))
    assert loadProjectDocument(path) == document and document.schemaVersion == '2.7'
    registry = _registry()
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    variables = ReadOnlyVariables({'confidence': .25})
    inputs = {'image': np.zeros((4, 8, 3), dtype=np.uint8), 'fileName': 'first.png'}
    cancelled, events, completed, snapshots = threading.Event(), [], [], []
    history = RunResultHistory()
    history.accept('job', [{**node.model_dump(), 'workflowId': 'main'}
                           for node in document.workflows['main'].nodes], revision=document.project.revision)

    def publish(**event):
        events.append(event)
        context = event['context']
        history.applyEvent({**event, 'sequence': len(events), 'jobId': context.jobId,
                            'workflowId': context.workflowId, 'nodeId': context.callerNodeId,
                            'workflowRunId': context.workflowRunId, 'nodeRunId': context.nodeRunId})
        if event['eventType'] == 'workflow.completed':
            completed.append(context.workflowRunId)
            if len(completed) == 1:
                variables.values['confidence'] = .95
                inputs['fileName'] = 'missing.png'
            elif len(completed) == 2:
                record = history.view('main', 'yolo')['record']
                assert record['workflowRunId'] == context.workflowRunId
                assert 'croppedImage' in record['imageOutputMessages']
                assert history.view('main', 'save')['observation']['status'] == 'SKIPPED'
                assert not (tmp_path / 'images' / 'missing.png').exists()
                variables.values['confidence'] = .25
                inputs.update(image=np.full((4, 8, 3), 73, dtype=np.uint8), fileName='last.png')
            else:
                cancelled.set()

    writer = PreviewSnapshotWriter(tmp_path, jobId='job', projectRevision=document.project.revision,
                                   inspectionPublisher=lambda **event: snapshots.append(event))
    runner = WorkflowRunner(compiled, registry, eventPublisher=publish, globalVariables=variables,
                            previewSnapshotStore=writer, retainOperators=True)
    spec = SimpleNamespace(jobId='job', workflowId='main', projectId=document.project.projectId,
                           jobWorkspacePath=str(tmp_path), cycleIntervalMs=1)
    YoloInferenceOperator.clearCache()
    try:
        with pytest.raises(CancellationRequested):
            _runContinuous(runner, spec, inputs, CancellationToken(cancelled), cancelled)
    finally:
        YoloInferenceOperator.clearCache()
    assert len(completed) == len(set(completed)) == 3
    assert not any(event['eventType'].endswith('.failed') for event in events)
    saveEvents = [event for event in events if event['context'].callerNodeId == 'save'
                  and event['eventType'] in {'node.completed', 'node.skipped'}]
    assert [event['eventType'] for event in saveEvents] == ['node.completed', 'node.skipped', 'node.completed']
    assert saveEvents[1]['payload']['missingInputs'] == ['image']
    assert {path.name for path in (tmp_path / 'images').iterdir()} == {'first.png', 'last.png'}
    first = cv2.imdecode(np.fromfile(tmp_path / 'images' / 'first.png', np.uint8), cv2.IMREAD_COLOR)
    last = cv2.imdecode(np.fromfile(tmp_path / 'images' / 'last.png', np.uint8), cv2.IMREAD_COLOR)
    assert first.size > 0 and np.all(first == 0)
    assert last.shape == first.shape and np.all(last == 73)
    cropEntries = [entry for snapshot in snapshots for entry in snapshot['payload']['entries']
                   if entry['nodeId'] == 'yolo' and entry['port'] == 'croppedImage']
    assert [entry['workflowRunId'] for entry in cropEntries] == [completed[0], completed[2]]
