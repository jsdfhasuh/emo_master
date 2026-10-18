from copy import deepcopy

import pytest

from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.core.project.migration import migrateProjectPayload
from emo_master.core.project.models import ProjectDocument
from tests.runtime.production_fixture import productionProject, saveDocument
from tests.runtime.test_project_configuration import finish


@pytest.mark.parametrize('mixedParameter', [False, True])
def testPageAssetReplacementChangesPublicationAndRetainsPortablePaths(tmp_path, mixedParameter):
    import hashlib
    import cv2
    import numpy as np
    from pathlib import Path
    from emo_master.core.presentation.models import Component
    from emo_master.core.project.resources import Resource
    from emo_master.core.project.configuration import projectChanges
    root = tmp_path / 'project'
    document = productionProject(root, save=False)
    document = ProjectDocument.model_validate(migrateProjectPayload(document.model_dump(), enableDevices=True))
    from emo_master.core.project.models import WorkflowNode
    document.workflows['main'].nodes.append(WorkflowNode(nodeId='number', operatorId='vision.value.number', params=dict(value=12)))
    for name, color in [('first', 20), ('second', 200)]:
        data = cv2.imencode('.png', np.full((8, 8, 3), color, dtype=np.uint8))[1].tobytes()
        (root / (name + '.png')).write_bytes(data)
    data = (root / 'first.png').read_bytes()
    document.resources.items['static'] = Resource(path='first.png', purpose='page_image',
        size=len(data), sha256=hashlib.sha256(data).hexdigest())
    document.presentation.pages['main'].components = [Component(componentId='static', type='image',
        props=dict(imageResourceId='static'))]
    saveDocument(root, document)
    owner = ProductionRuntime(tmp_path / 'runtime')
    try:
        owner.load(root)
        from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
        from tests.runtime.production_fixture import waitFor
        started = owner.runtime.StartJob(pb.StartJobRequest(workflow_id='main'), None)
        assert started.ok, started.message
        waitFor(lambda: owner.runtime.jobRepository.get(started.job_id).status == 'RUNNING')
        revision = owner.status()['pagePublicationRevision']
        oldPath = Path(owner.document._pageResourcePaths['static'])
        coordinator = owner.runtime.projectConfiguration
        before = coordinator.snapshot()['payload']
        executionRevision = coordinator.snapshot()['executionRevision']
        candidate = deepcopy(before)
        if mixedParameter:
            next(node for node in candidate['workflows']['main']['nodes'] if node['nodeId'] == 'number')['params']['value'] = 34
        data = (root / 'second.png').read_bytes()
        candidate['resources']['items']['static'].update(path='second.png', size=len(data),
            sha256=hashlib.sha256(data).hexdigest())
        coordinator.start(dict(requestId='static-replace', expectedRuntimeInstanceId=owner.runtime.runtimeInstanceId,
            expectedRevision=1, localPayload=candidate,
            selectedPaths=[c['path'] for c in projectChanges(before, candidate)]))
        result = finish(coordinator, 'static-replace')
        assert result['state'] == 'COMMITTED', result
        assert owner.status()['pagePublicationRevision'] != revision
        assert owner.runtime.jobRepository.get(started.job_id).status == 'RUNNING'
        assert coordinator.snapshot()['executionRevision'] == executionRevision + int(mixedParameter)
        effective = Path(owner.document._pageResourcePaths['static'])
        assert effective != oldPath and effective.read_bytes() == data
        (root / 'second.png').unlink()
        assert effective.read_bytes() == data
        assert coordinator.snapshot()['payload']['resources']['items']['static']['path'] == 'second.png'
    finally:
        owner.close()


def testPublishedPageUsesOnlineDocumentWithoutSavingLocalFile(tmp_path):
    root = tmp_path / "project"
    document = productionProject(root, save=False)
    document = ProjectDocument.model_validate(migrateProjectPayload(document.model_dump(), enableDevices=True))
    saveDocument(root, document)
    before = (root / "project.json").read_bytes()
    owner = ProductionRuntime(tmp_path / "runtime")
    try:
        owner.load(root)
        from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
        graph = owner.runtime.GetOnlineGraphSnapshot(pb.OnlineGraphRequest(), None)
        assert graph.ok, graph.message
        oldRevision = owner.status()["pagePublicationRevision"]
        coordinator = owner.runtime.projectConfiguration
        candidate = deepcopy(coordinator.snapshot()["payload"])
        candidate["presentation"]["pages"]["main"]["name"] = "Published page"
        coordinator.start(dict(requestId="publish", expectedRuntimeInstanceId=owner.runtime.runtimeInstanceId,
            expectedRevision=1, localPayload=candidate, selectedPaths=["/presentation/pages/main/name"]))
        status = finish(coordinator, "publish")
        assert status["state"] == "COMMITTED", repr(status)
        newStatus = owner.status()
        assert newStatus["pagePublicationRevision"] != oldRevision
        assert owner.presentationForWorkflow("main").pages["main"].name == "Published page"
        assert (root / "project.json").read_bytes() == before
    finally:
        owner.close()


def testInvalidPageTransitionDoesNotStopProduction(tmp_path):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from tests.runtime.production_fixture import waitFor
    root = tmp_path / "project"
    document = productionProject(root, save=False)
    document = ProjectDocument.model_validate(migrateProjectPayload(document.model_dump(), enableDevices=True))
    saveDocument(root, document)
    owner = ProductionRuntime(tmp_path / "runtime")
    try:
        owner.load(root)
        started = owner.runtime.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        assert started.ok, started.message
        waitFor(lambda: owner.runtime.jobRepository.get(started.job_id).status == "RUNNING")
        coordinator = owner.runtime.projectConfiguration
        candidate = deepcopy(coordinator.snapshot()["payload"])
        candidate["presentation"]["pages"]["main"]["components"] = [dict(componentId="unbound", type="image")]
        coordinator.start(dict(requestId="invalid-page", expectedRuntimeInstanceId=owner.runtime.runtimeInstanceId,
            expectedRevision=1, localPayload=candidate, replacementPolicy="stopAffected",
            selectedPaths=["/presentation/pages/main/components"]))
        result = finish(coordinator, "invalid-page")
        assert result["state"] == "FAILED" and result["code"] == "E_CONFIG_PAGE_BINDING", repr(result)
        assert owner.runtime.jobRepository.get(started.job_id).status == "RUNNING"
        assert coordinator.snapshot()["documentRevision"] == 1
    finally:
        owner.close()
