"""Real CPU inference and preview capture, not field-model accuracy acceptance."""
import base64

import cv2
import numpy as np
import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.run_result_history import RunResultHistory
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.preview.store import PreviewSnapshotWriter
from emo_master.apps.runtime.workflow.cancellation import CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.apps.runtime.workflow.runner import WorkflowRunner
from emo_master.core.contracts.port_types import normalizePortType
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.plugins.builtins.yolo_inference.operator import YoloInferenceOperator
from tests.plugins.test_yolo_onnx_backend import _TINY_YOLO_ONNX
from tests.runtime.test_builtin_vision_operator_workflows import _edge, _registry


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
