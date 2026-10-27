"""Replacement validation keeps missing uploads actionable and side-effect free."""
from copy import deepcopy
import hashlib
from pathlib import Path

import cv2
import numpy as np
import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClientError
from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.core.project.configuration import projectChanges
from emo_master.core.project.models import ProjectDocument
from emo_master.core.project.snapshots import verifyResources
from emo_master.core.workflow.errors import WorkflowCompileError
from tests.runtime.test_initial_project_configuration import initialRuntime, waitForWrite  # noqa: F401


def resourceProject(root, *, extraImages=0):
    root.mkdir(parents=True)
    image = root / 'input.png'
    assert cv2.imwrite(str(image), np.full((9, 13, 3), 120, dtype=np.uint8))
    payload = WorkflowStore().toPayload('Resource preflight')
    payload['workflows']['main']['nodes'].insert(1, dict(
        nodeId='load', kind='operator', operatorId='vision.io.image_loader',
        params=dict(imagePath='input.png', colorMode='color')))
    payload['resources']['items']['fixture.image'] = dict(path='input.png', size=image.stat().st_size,
        sha256=hashlib.sha256(image.read_bytes()).hexdigest(), purpose='input_image')
    payload['resources']['parameterBindings'] = [dict(resourceId='fixture.image', target=dict(
        workflowId='main', nodeId='load', parameterPath=['imagePath']))]
    for index in range(extraImages):
        path = f'samples/sample-{index + 1}.png'
        image = root / path
        image.parent.mkdir(exist_ok=True)
        assert cv2.imwrite(str(image), np.full((9, 13, 3), 121 + index, dtype=np.uint8))
        payload['resources']['items'][f'fixture.sample.{index + 1}'] = dict(
            path=path, size=image.stat().st_size, sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
            purpose='input_image')
    payload = ProjectDocument.model_validate(payload).model_dump(mode='json')
    saveProject(root / 'resource.emoproj', payload)
    assert loadProjectDocument(root / 'resource.emoproj').model_dump(mode='json') == payload
    return payload


def testMissingResourceRootHasLocatedDiagnosticWithoutCreatingDirectory(tmp_path):
    payload = resourceProject(tmp_path / 'source')
    root = tmp_path / 'not-published'
    with pytest.raises(WorkflowCompileError) as caught:
        verifyResources(ProjectDocument.model_validate(payload), root)
    issue, = caught.value.issues
    assert issue.code == 'E_RESOURCE_MISSING'
    assert issue.fieldPath == 'resources.items.fixture.image.path'
    assert 'fixture.image' in issue.message and 'input.png' in issue.message
    assert not root.exists()


@pytest.mark.parametrize('rootExists', [False, True])
def testAllMissingFilesAreListedButAvailableMappingIsExcluded(tmp_path, rootExists):
    payload = resourceProject(tmp_path / 'source', extraImages=2)
    document = ProjectDocument.model_validate(payload)
    root = tmp_path / 'not-published'
    if rootExists:
        root.mkdir()
    with pytest.raises(WorkflowCompileError) as caught:
        verifyResources(document, root)
    assert [issue.fieldPath for issue in caught.value.issues] == [
        f'resources.items.{key}.path' for key in payload['resources']['items']]
    assert all(issue.code == 'E_RESOURCE_MISSING' for issue in caught.value.issues)
    with pytest.raises(WorkflowCompileError) as caught:
        verifyResources(document, root, resourcePaths={'fixture.image': tmp_path / 'source/input.png'})
    assert [issue.fieldPath for issue in caught.value.issues] == [
        'resources.items.fixture.sample.1.path', 'resources.items.fixture.sample.2.path']
    assert root.exists() == rootExists


@pytest.mark.parametrize('problem,code', [
    ('missing', 'E_RESOURCE_MISSING'), ('directory', 'E_RESOURCE_INPUT'),
    ('size', 'E_RESOURCE_SIZE'), ('digest', 'E_RESOURCE_DIGEST'),
])
def testResourceFailuresRemainDistinct(tmp_path, problem, code):
    root = tmp_path / 'source'
    document = ProjectDocument.model_validate(resourceProject(root))
    image = root / 'input.png'
    if problem in {'missing', 'directory'}:
        image.unlink()
        if problem == 'directory':
            image.mkdir()
    elif problem == 'size':
        document.resources.items['fixture.image'].size += 1
    else:
        document.resources.items['fixture.image'].sha256 = '0' * 64
    with pytest.raises(WorkflowCompileError) as caught:
        verifyResources(document, root)
    issue, = caught.value.issues
    assert issue.code == code
    assert issue.fieldPath.startswith('resources.items.fixture.image.')


def testUploadedResourceDoesNotNeedRootAndStillChecksDigest(tmp_path):
    payload = resourceProject(tmp_path / 'source')
    document = ProjectDocument.model_validate(payload)
    root = tmp_path / 'not-published'
    image = tmp_path / 'source/input.png'
    paths = {'fixture.image': str(image)}
    assert verifyResources(document, root, resourcePaths=paths) == paths
    assert not root.exists()
    document.resources.items['fixture.image'].sha256 = '0' * 64
    with pytest.raises(WorkflowCompileError) as caught:
        verifyResources(document, root, resourcePaths=paths)
    assert caught.value.issues[0].code == 'E_RESOURCE_DIGEST'


@pytest.mark.parametrize('extraImages', [0, 2])
def testReplacementValidateUploadValidateWrite(initialRuntime, tmp_path, extraImages):  # noqa: F811
    service, client = initialRuntime
    original = WorkflowStore().toPayload('Online original')
    root = tmp_path / 'original'
    root.mkdir()
    saveProject(root / 'original.emoproj', original)
    client.initializeProjectConfiguration(original, root, 'original', runtimeInstanceId=service.runtimeInstanceId)
    assert waitForWrite(service, client, 'original')['state'] == 'COMMITTED'
    snapshot = deepcopy(client.getOnlineProjectSnapshot())
    candidate = resourceProject(tmp_path / 'candidate', extraImages=extraImages)
    source = tmp_path / 'candidate/resource.emoproj'
    before = source.read_bytes()
    changes = [c['path'] for c in projectChanges(snapshot['payload'], candidate)]
    managedRoot = Path(service._configurationSourcePath(candidate)).parent
    assert not managedRoot.exists()
    with pytest.raises(RuntimeClientError) as caught:
        client.writeProjectConfiguration(snapshot, candidate, changes, 'missing-upload', validate=True,
                                         replacementPolicy='stopAffected')
    issues = caught.value.diagnostics['issues']
    assert all(issue['code'] == 'E_RESOURCE_MISSING' for issue in issues)
    assert [issue['fieldPath'] for issue in issues] == [
        f'resources.items.{key}.path' for key in candidate['resources']['items']]
    assert 'WinError' not in str(caught.value)
    assert client.getOnlineProjectSnapshot() == snapshot
    assert not managedRoot.exists() and not service.jobRepository.all()
    assert not service.deviceManager.drivers

    if extraImages:
        partial = deepcopy(candidate)
        partial['resources']['items'] = {'fixture.image': candidate['resources']['items']['fixture.image']}
        uploaded = client.uploadProjectResources(snapshot, partial, source.parent)
        assert len(uploaded['uploaded']) == 1 and not uploaded['errors']
        with pytest.raises(RuntimeClientError) as caught:
            client.writeProjectConfiguration(snapshot, candidate, changes, 'partial-upload', validate=True,
                                             replacementPolicy='stopAffected')
        assert [issue['fieldPath'] for issue in caught.value.diagnostics['issues']] == [
            'resources.items.fixture.sample.1.path', 'resources.items.fixture.sample.2.path']
        assert client.getOnlineProjectSnapshot() == snapshot and not managedRoot.exists()

    uploaded = client.uploadProjectResources(snapshot, candidate, source.parent)
    assert len(uploaded['uploaded']) == 1 + extraImages and not uploaded['errors']
    assert client.getOnlineProjectSnapshot() == snapshot
    checked = client.writeProjectConfiguration(snapshot, candidate, changes, 'uploaded-check', validate=True,
                                               replacementPolicy='stopAffected')
    assert checked['candidate'] == candidate and checked['projectChanged']
    assert not checked['affectedJobs'] and not checked['disconnectDevices']
    assert not managedRoot.exists() and not service.jobRepository.all()
    client.writeProjectConfiguration(snapshot, candidate, changes, 'replacement', replacementPolicy='stopAffected')
    assert waitForWrite(service, client, 'replacement')['state'] == 'COMMITTED'
    assert service.loadedProjectId == candidate['project']['projectId']
    assert managedRoot.is_dir()
    assert source.read_bytes() == before and not service.jobRepository.all()
