"""Device UI support through real Runtime paths and substituted IMV hardware."""
from copy import deepcopy
import json

import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from tests.runtime.imv_test_support import SimulatedImvRuntimeService
from tests.runtime.test_managed_imv import cameraProject


def testPassiveWatchUsesProductionCopyAndDoesNotOwnOriginalOrBlockWrites(tmp_path):
    path = tmp_path / 'camera.emoproj'
    path.write_text(json.dumps(cameraProject()), encoding='utf-8')
    service = SimulatedImvRuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    client = RuntimeClient(service)
    manager = service.deviceManager
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        runtimeId, states = client.getDeviceStates(service.loadedProjectId)
        args = (service.loadedProjectId, runtimeId, 'camera', states['camera'])
        opened = client.cameraDiagnostic(*args, 'open')
        session = opened['sessionId']
        assert opened['metadata']['cameraPreviewVersion'] == 1
        assert not manager.permissions and not manager.maintenanceOwners
        watching = client.cameraDiagnostic(*args, 'watch', sessionId=session)
        assert not watching['preview'] and not watching['metadata']['productionActive']
        assert manager.drivers['camera'].process is None
        # Passive viewers must not become blockers for a valid structural write.
        candidate = deepcopy(service.projectConfiguration.current['payload'])
        candidate['workflows']['main']['nodes'][1]['displayName'] = 'changed label'
        service._checkConfigurationAdmission(candidate)
        manager.authorize('production-job', ['camera'])
        cycle = service.projectConfiguration.beginCycle('production-job', -1)
        descriptor = manager.execute('production-job', 'camera', 'capture', manager.definitions['camera'].defaults,
            cycleId=cycle['cycleId'], source=dict(workflowId='main', nodeId='capture', slot='camera'))
        manager.frames.release(descriptor['frameId'], 'production-job')
        watched = client.cameraDiagnostic(*args, 'watch', sessionId=session)
        assert watched['preview'] and watched['metadata']['frameId'] == descriptor['frameId']
        assert watched['metadata']['cycleId'] == cycle['cycleId']
        assert watched['metadata']['owner'] == 'production-job' and watched['metadata']['planRevision'] == 1
        assert watched['metadata']['productionActive']
        assert not manager.frames.frames
        again = client.cameraDiagnostic(*args, 'watch', sessionId=session, sequence=watched['metadata']['watchSequence'])
        assert not again['preview']  # No repeated transfer of the same JPEG.
        with pytest.raises(ValueError, match='E_CAMERA_PRODUCTION_ACTIVE'):
            client.cameraDiagnostic(*args, 'preview_capture', sessionId=session, sequence=1, confirmed=True)
        assert not manager.maintenanceOwners
        service.projectConfiguration.endCycle('production-job', cycle['cycleId'])
        manager.retire('production-job')
        preview = client.cameraDiagnostic(*args, 'preview_capture', sessionId=session, sequence=2, confirmed=True)
        assert preview['preview'] and not manager.frames.frames
        assert preview['metadata']['acquisition']['blockId'] == descriptor['acquisition']['blockId'] + 1
        assert not manager.maintenanceOwners and not service.projectConfiguration.cycles
        client.cameraDiagnostic(*args, 'close', sessionId=session)
        assert service.cameraDiagnostics.sessions[session].observed is None
    finally:
        client.close()
        service.close()


def testHardwareRealtimePreviewRefusesBeforeAnyAcquisition(tmp_path):
    path = tmp_path / 'hardware.emoproj'
    path.write_text(json.dumps(cameraProject('hardware')), encoding='utf-8')
    service = SimulatedImvRuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    client = RuntimeClient(service)
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        runtimeId, states = client.getDeviceStates(service.loadedProjectId)
        args = (service.loadedProjectId, runtimeId, 'camera', states['camera'])
        opened = client.cameraDiagnostic(*args, 'open')
        with pytest.raises(ValueError, match='E_CAMERA_PREVIEW_UNSAFE'):
            client.cameraDiagnostic(*args, 'preview_capture', sessionId=opened['sessionId'], sequence=1, confirmed=True)
        assert service.deviceManager.drivers['camera'].process is None
        assert not service.deviceManager.frames.frames and not service.deviceManager.frames.reservations
        assert not service.deviceManager.maintenanceOwners and not service.projectConfiguration.cycles
    finally:
        client.close()
        service.close()


def testPreflightDescribesPerChangeEffectsWithoutSideEffects(tmp_path):
    from tests.runtime.test_device_runtime import simulatedProject
    path = tmp_path / 'project.emoproj'
    path.write_text(json.dumps(simulatedProject()), encoding='utf-8')
    service = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    client = RuntimeClient(service)
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        snapshot = client.getOnlineProjectSnapshot()
        payload = deepcopy(snapshot['payload'])
        payload['devices']['definitions']['camera']['defaults']['exposureUs'] = 12000
        payload['devices']['definitions']['camera']['connection']['serial'] = 'OTHER-SIMULATED-SERIAL'
        paths = ['/devices/definitions/camera/defaults/exposureUs', '/devices/definitions/camera/connection/serial']
        result = client.writeProjectConfiguration(snapshot, payload, paths, 'preflight', validate=True)
        assert result['changeEffects'][paths[0]]['effect'] == 'next_cycle'
        assert result['changeEffects'][paths[1]]['effect'] == 'reconnect'
        assert result['changeEffects'][paths[0]]['devices'] == ['camera']
        assert 'main' in result['changeEffects'][paths[0]]['workflows']
        assert client.getOnlineProjectSnapshot() == snapshot
        assert not service.deviceManager.lifecycles['camera'].connected
    finally:
        client.close()
        service.close()
