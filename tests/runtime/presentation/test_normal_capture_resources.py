"""Normal page capture uses the same uploaded assets as online execution."""
from copy import deepcopy
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

import pytest

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.runtime.presentation.normal_capture import freezeNormalCapture
from emo_master.apps.runtime.presentation.service import PresentationService
from emo_master.core.project.configuration import projectChanges
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.errors import WorkflowCompileError
from tests.runtime.test_builtin_vision_operator_workflows import _registry
from tests.runtime.runtime_test_utils import jobFailureDetails, waitForTerminal
from tests.runtime.test_initial_project_configuration import initialRuntime, waitForWrite  # noqa: F401
from tests.runtime.test_project_resource_preflight import resourceProject


def captureProject(root):
    payload = resourceProject(root, extraImages=1)
    model = root / 'models/fixture.onnx'
    model.parent.mkdir()
    # A declared, unbound model is validated, not imported or executed by capture.
    model.write_bytes(b'fixture model; capture must not execute inference')
    payload['resources']['items']['fixture.model'] = dict(path='models/fixture.onnx',
        size=model.stat().st_size, sha256=hashlib.sha256(model.read_bytes()).hexdigest(), purpose='model')
    payload['presentation'].update(defaultPageId='result', pageOrder=['result'],
        resultScopes={'root': dict(entryWorkflowId='main', scopeWorkflowId='main')},
        dataSources={'image': dict(kind='node_output', resultScopeId='root', workflowId='main',
                                  nodeId='load', port='image', expectedType='image')},
        pages={'result': dict(name='Image', components=[dict(componentId='image', type='image',
                                                            bindings={'image': 'image'})])})
    document = ProjectDocument.model_validate(payload)
    saveProject(root / 'resource.emoproj', document.model_dump(mode='json'))
    assert loadProjectDocument(root / 'resource.emoproj') == document
    return document


def publishReplacement(service, client, root):
    original = WorkflowStore().toPayload('Original')
    client.initializeProjectConfiguration(original, root, 'original', runtimeInstanceId=service.runtimeInstanceId)
    assert waitForWrite(service, client, 'original')['state'] == 'COMMITTED'
    snapshot = deepcopy(client.getOnlineProjectSnapshot())
    document = captureProject(root / 'candidate')
    payload = document.model_dump(mode='json')
    uploaded = client.uploadProjectResources(snapshot, payload, root / 'candidate')
    assert len(uploaded['uploaded']) == 3 and not uploaded['errors']
    changes = [change['path'] for change in projectChanges(snapshot['payload'], payload)]
    client.writeProjectConfiguration(snapshot, payload, changes, 'replacement', replacementPolicy='stopAffected')
    assert waitForWrite(service, client, 'replacement')['state'] == 'COMMITTED'
    return document


def testUploadedModelAndImagesCanStartNormalCaptureAfterReplacement(initialRuntime, tmp_path):  # noqa: F811
    service, client = initialRuntime
    owner = getattr(service, '_presentationOwner', None)
    display = owner or PresentationService(service, tmp_path / 'display')
    try:
        document = publishReplacement(service, client, tmp_path)
        root = Path(service.loadedProjectPath)
        assert root.is_dir()
        assert all(not (root / item.path).exists() for item in document.resources.items.values())
        assert len(service.projectResourceTransfers.resolve(document)) == 3
        assert not service.jobRepository.all() and not service.deviceManager.drivers
        started = client.startJob(document.project.projectId, 'main', capturePresentation=True,
                                  startRequestId=uuid4().hex, expectedRuntimeInstanceId=service.runtimeInstanceId)
        assert started.ok, started.message
        terminal = waitForTerminal(service, started.job_id)
        assert terminal.status == 'COMPLETED', jobFailureDetails(service, started.job_id)
        deadline = time.monotonic() + 10
        while not display.store.snapshot(started.job_id)['results'] and time.monotonic() < deadline:
            time.sleep(.02)
        result, = display.store.snapshot(started.job_id)['results']
        assert result.status == 'COMPLETE'
        assert result.sources[0].image is not None
        assert len(service.jobRepository.all()) == 1
        assert not service.deviceManager.drivers
    finally:
        if owner is None:
            service.close()
            display.close()


def testCaptureValidatesMappedResourcesWithoutCreatingRootOrChangingDocument(tmp_path):
    document = captureProject(tmp_path / 'source')
    before = document.model_dump_json()
    paths = {key: tmp_path / 'source' / item.path for key, item in document.resources.items.items()}
    root = tmp_path / 'not-published'
    frozen = freezeNormalCapture(document, _registry(), root, 'main', resourcePaths=paths)
    assert json.loads(frozen.sourceJson)['sources']['image']['nodeId'] == 'load'
    assert not root.exists() and document.model_dump_json() == before
    # Local projects continue to use their own directory when no upload exists.
    assert freezeNormalCapture(document, _registry(), tmp_path / 'source', 'main') == frozen


@pytest.mark.parametrize('problem,code', [
    ('missing', 'E_RESOURCE_MISSING'), ('directory', 'E_RESOURCE_INPUT'),
    ('size', 'E_RESOURCE_SIZE'), ('digest', 'E_RESOURCE_DIGEST'),
])
def testCaptureMappedResourceStillRejectsMissingOrInvalidContent(tmp_path, problem, code):
    document = captureProject(tmp_path / 'source')
    paths = {key: tmp_path / 'source' / item.path for key, item in document.resources.items.items()}
    model = paths['fixture.model']
    if problem in {'missing', 'directory'}:
        model.unlink()
        if problem == 'directory':
            model.mkdir()
    else:
        data = model.read_bytes()
        model.write_bytes(data + b'x' if problem == 'size' else b'x' * len(data))
    with pytest.raises(WorkflowCompileError) as caught:
        freezeNormalCapture(document, _registry(), tmp_path / 'not-published', 'main', resourcePaths=paths)
    issue, = caught.value.issues
    assert issue.code == code and issue.fieldPath.startswith('resources.items.fixture.model.')


def testMissingCaptureAssetsKeepStructuredDiagnosticsThroughStartReconciliation(
        initialRuntime, tmp_path, monkeypatch):  # noqa: F811
    service, client = initialRuntime
    owner = getattr(service, '_presentationOwner', None)
    display = owner or PresentationService(service, tmp_path / 'display')
    try:
        document = publishReplacement(service, client, tmp_path)
        # Direct counterexample: the requested resources really have no usable
        # mapping in this isolated Runtime; do not bypass preflight or start a Job.
        monkeypatch.setattr(service.projectResourceTransfers, 'resolve', lambda _: {})
        requestId = uuid4().hex
        reply = client.startJob(document.project.projectId, 'main', capturePresentation=True,
            startRequestId=requestId, expectedRuntimeInstanceId=service.runtimeInstanceId)
        assert not reply.ok and not reply.job_id and reply.status == 'REJECTED'
        diagnostics = json.loads(reply.diagnostics_json)
        assert {issue['code'] for issue in diagnostics['issues']} == {'E_RESOURCE_MISSING'}
        assert len(diagnostics['issues']) == 3
        assert diagnostics['resources']['fixture.model'] == dict(path='models/fixture.onnx', purpose='model')
        assert diagnostics['resources']['fixture.sample.1']['path'] == 'samples/sample-1.png'
        again = client.getStartRequest(requestId, service.runtimeInstanceId)
        assert again == reply
        assert not service.jobRepository.all() and not service.deviceManager.drivers
    finally:
        if owner is None:
            service.close()
            display.close()
