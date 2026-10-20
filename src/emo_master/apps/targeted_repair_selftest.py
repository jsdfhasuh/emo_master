"""Packaged synthetic probes for retained loops and same-manifest recovery."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time


def _wait(predicate, timeout=40):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise RuntimeError('targeted repair self-test timed out')
        time.sleep(.02)


def _project(root):
    import cv2
    import numpy as np
    from emo_master.core.project.models import ProjectDocument
    image = cv2.imencode('.png', np.full((8, 8, 3), 71, dtype=np.uint8))[1].tobytes()
    (root / 'input.png').write_bytes(image)
    ports = dict(image='image', count='number')
    def flow(name, inputs, outputs, nodes, edges):
        return dict(name=name, inputs=inputs, outputs=outputs,
            nodes=[dict(nodeId='input', kind='workflow_input'), *nodes, dict(nodeId='output', kind='workflow_output')],
            edges=[dict(fromNode=a, fromPort=b, toNode=c, toPort=d) for a, b, c, d in edges])
    workflows = {
        'main': flow('Source', {}, {}, [
            dict(nodeId='capture', operatorId='vision.io.simulated_camera', deviceBindings=dict(camera=dict(deviceId='camera'))),
            dict(nodeId='zero', operatorId='vision.value.number', params=dict(value=0)),
            dict(nodeId='file', operatorId='vision.io.image_loader', params=dict(imagePath=str(root / 'input.png'))),
            dict(nodeId='dispatch', kind='dispatch', inputPorts=ports, params=dict(targets=[
                dict(targetSlotId='consumer', workflowId='consumer', inputs=dict(image='image', count='count'))])),
            dict(nodeId='join', kind='join', params=dict(dispatchNodeId='dispatch'))],
            [('capture', 'image', 'dispatch', 'image'), ('zero', 'value', 'dispatch', 'count'), ('dispatch', 'token', 'join', 'token')]),
        'consumer': flow('Consumer', ports, ports, [dict(nodeId='loop', kind='loop', inputPorts=ports, outputPorts=ports,
            loop=dict(contractVersion=2, mode='while', maxIterations=2, timeoutMs=0, bodyWorkflowId='body', conditionWorkflowId='condition', cycleBoundary='iteration'))],
            [(a, port, b, port) for a, b in [('input', 'loop'), ('loop', 'output')] for port in ports]),
        'condition': flow('Condition', ports, {'continue': 'boolean'}, [dict(nodeId='compare', operatorId='vision.compare.number', params=dict(operator='lt', rightValue=1))],
            [('input', 'count', 'compare', 'left'), ('compare', 'result', 'output', 'continue')]),
        'body': flow('Body', ports, ports, [dict(nodeId='value', operatorId='vision.value.number', params=dict(value=1))],
            [('input', 'image', 'output', 'image'), ('value', 'value', 'output', 'count')])}
    payload = dict(schemaVersion='2.6', project=dict(projectId='targeted-repair-selftest', name='Targeted repairs',
        createdAt='2026-10-18T00:00:00Z', updatedAt='2026-10-18T00:00:00Z'), entryWorkflowId='main', workflowOrder=list(workflows), workflows=workflows,
        runtime=dict(maxConcurrentJobs=3), presentation={}, production={}, communication={}, globalVariables={},
        devices=dict(schemaVersion='1.0', definitions=dict(camera=dict(
            name='Simulated', deviceTypeId='device.simulated.camera', driverVersion='1.0.0',
            connection=dict(serial='TARGETED-SELFTEST'), defaults=dict(width=8, height=8, exposureUs=8000, gain=0)))),
        resources=dict(items=dict(image=dict(path='input.png', size=len(image), sha256=hashlib.sha256(image).hexdigest(), purpose='input_image')),
                       parameterBindings=[dict(resourceId='image', target=dict(workflowId='main', nodeId='file', parameterPath=['imagePath']))]))
    return ProjectDocument.model_validate(payload).model_dump(mode='json'), image


def checkTargetedRepairs(*, includeDesigner=False):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.frame_dispatch.scheduler import RunGroup
    from emo_master.core.project.configuration import projectChanges
    with TemporaryDirectory(prefix='emo-targeted-selftest-') as directory:
        root = Path(directory)
        payload, image = _project(root)
        path = root / 'project.emoproj'
        path.write_text(json.dumps(payload), encoding='utf-8')
        if includeDesigner:
            from emo_master.apps.designer.state.workflow_store import WorkflowStore
            from emo_master.apps.designer.state.workflow_package import buildWorkflowPackage, importWorkflowPackage
            from emo_master.core.workflow.dispatch_contracts import groupSlots
            from emo_master.core.project.models import ProjectDocument
            store = WorkflowStore(payload)
            package = buildWorkflowPackage(store, 'main', sourceRoot=root)
            imported = importWorkflowPackage(store, package, targetRoot=root, deviceMapping={'camera': 'camera'})
            model = ProjectDocument.model_validate(store.toPayload())
            if set(groupSlots(model, 'main')) & set(groupSlots(model, imported.rootWorkflowId)):
                raise RuntimeError('copied Dispatch slots collide')
            from emo_master.apps.designer.state.device_package import exportDevices, importDevices
            from emo_master.apps.designer.state.project_edit_session import ProjectEditSession
            from emo_master.core.project.device_resources import trustedDeviceTypes
            session = ProjectEditSession(payload)
            devicePackage = exportDevices(session, ['camera'], root)
            importedDevices = importDevices(session, devicePackage, trustedDeviceTypes(), root)
            if session.document().devices.definitions[importedDevices['camera']].enabled:
                raise RuntimeError('portable device must remain disabled')
            if not session.undo() or set(session.document().devices.definitions) != {'camera'}:
                raise RuntimeError('portable device import must be one reversible edit')
            from PySide2.QtCore import QCoreApplication, QEvent
            from PySide2.QtWidgets import QApplication
            from emo_master.apps.designer.ui.device_wizard import SourceEditor
            application = QApplication.instance() or QApplication([])
            sourceNode = next(n for n in payload['workflows']['main']['nodes'] if n['nodeId'] == 'capture')
            onlineDefinition = deepcopy(payload['devices']['definitions']['camera'])
            onlineDefinition['defaults']['exposureUs'] = 12000
            sourceEditor = SourceEditor(trustedDeviceTypes()['device.simulated.camera'],
                payload['devices']['definitions']['camera'], sourceNode, 'camera', {})
            sourceEditor.setOnline(onlineDefinition, sourceNode, revision=42, deviceId='camera')
            label = sourceEditor.onlineLabels['exposureUs'].text()
            if '12000' not in label or 'R42' not in label or '\u672a\u5e94\u7528' not in label:
                raise RuntimeError('online field comparison lost its effective value or revision')
            sourceEditor.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            application.processEvents()
        service = RuntimeService(dbPath=root / 'runtime.db', workspaceRoot=root / 'jobs')
        entered, release = Event(), Event()
        original = RunGroup.handle
        def handle(group, owner, action, request):
            result = original(group, owner, action, request)
            if action == 'dispatchNext' and result['status'] == 'RUNNING':
                entered.set()
                if not release.wait(30):
                    raise RuntimeError('consumer release timed out')
            return result
        RunGroup.handle = handle
        try:
            loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
            if not loaded.ok:
                raise RuntimeError(loaded.message)
            started = service.StartJob(pb.StartJobRequest(workflow_id='main'), None)
            if not started.ok or not entered.wait(30):
                raise RuntimeError('consumer did not receive retained input: ' + started.message)
            snapshot = service.projectConfiguration.snapshot()
            candidate = deepcopy(snapshot['payload'])
            next(n for n in candidate['workflows']['body']['nodes'] if n['nodeId'] == 'value')['params']['value'] = 99
            reply = service.WriteProjectConfiguration(pb.ProjectConfigurationWriteRequest(request_id='new-parameters',
                expected_runtime_instance_id=service.runtimeInstanceId, expected_revision=snapshot['documentRevision'],
                local_payload_json=json.dumps(candidate), selected_paths=[c['path'] for c in projectChanges(snapshot['payload'], candidate)]), None)
            if not reply.ok:
                raise RuntimeError(reply.message)
            service.projectConfiguration.thread.join(15)
            if service.projectConfiguration.status('new-parameters')['state'] != 'COMMITTED':
                raise RuntimeError('parameter commit failed')
            release.set()
            _wait(lambda: all(r.isTerminal for r in service.jobRepository.all()) and not service.deviceManager.workers)
            values = [json.loads(e.payloadJson) for r in service.jobRepository.all() for e in service._eventsAfter(r.jobId, 0)
                      if e.eventType == 'node.completed' and e.nodeId == 'value']
            if (not values or any(v['outputs']['value'] != 1 or v['planRevision'] != snapshot['executionRevision'] for v in values)
                    or any(r.status != 'COMPLETED' for r in service.jobRepository.all()) or service.deviceManager.frames.frames):
                raise RuntimeError('retained consumer used new parameters or lost original frames')
            owned = service.projectResourceTransfers.resolve(service.loadedDocument)['image']
        finally:
            release.set()
            RunGroup.handle = original
            service.close()
        owned.write_bytes(b'corrupt')
        service = RuntimeService(dbPath=root / 'runtime.db', workspaceRoot=root / 'jobs')
        try:
            snap = service.projectConfiguration.snapshot()
            if not service.onlineRecoveryError:
                raise RuntimeError('corrupt resource did not fence recovery')
            request = pb.ProjectResourceTransferRequest(runtime_instance_id=service.runtimeInstanceId,
                project_id=payload['project']['projectId'], transfer_id='repair', action='LOOKUP',
                resource_json=json.dumps(payload['resources']['items']['image']))
            reply = service.TransferProjectResource(request, None)
            state = json.loads(reply.result_json) if reply.ok else {}
            if state.get('state') != 'CORRUPT':
                raise RuntimeError('corrupt lookup was not repairable')
            request.replacement_of, request.expected_document_revision = state['transferId'], snap['documentRevision']
            for action, data in [('BEGIN', b''), ('CHUNK', image), ('COMMIT', b'')]:
                request.action, request.data = action, data
                reply = service.TransferProjectResource(request, None)
                if not reply.ok:
                    raise RuntimeError(reply.message)
            reply = service.RetryProjectResourceRecovery(pb.RetryProjectResourceRecoveryRequest(request_id='recover',
                project_id=payload['project']['projectId'], expected_runtime_instance_id=service.runtimeInstanceId,
                expected_document_revision=snap['documentRevision']), None)
            if not reply.ok:
                raise RuntimeError(reply.message)
            service.projectConfiguration.thread.join(15)
            if (service.projectConfiguration.status('recover')['state'] != 'RECOVERED' or service.onlineRecoveryError
                    or service.projectConfiguration.snapshot()['documentRevision'] != snap['documentRevision']
                    or any(v.connected for v in service.deviceManager.lifecycles.values())):
                raise RuntimeError('same-revision recovery did not remain software-only')
            connected = service.deviceManager.testConnection(service.deviceManager.definitions['camera'].model_dump())
            if not connected['reused'] or service.deviceManager.maintenanceOwners:
                raise RuntimeError('setup test did not release its borrowed simulated device')
            copy = json.loads((root / 'online-project.json').read_text(encoding='utf-8'))
            if copy != service.projectConfiguration.store.snapshot():
                raise RuntimeError('reconstructible configuration copy differs from SQLite')
            manager = service.deviceManager
            from emo_master.core.project.snapshots import revisionOf
            observerId = service.cameraDiagnostics.open('camera', revisionOf(manager.definitions['camera'].model_dump()),
                                                       manager.executors['camera'].generation)
            observer = service.cameraDiagnostics.sessions[observerId]
            service.cameraDiagnostics.watch(observer, 0)
            if manager.permissions or manager.maintenanceOwners:
                raise RuntimeError('passive camera observation owns production admission')
            owner = 'observation-selftest'
            manager.authorize(owner, ['camera'])
            cycle = service.projectConfiguration.beginCycle(owner, -1)
            frame = manager.execute(owner, 'camera', 'capture', manager.definitions['camera'].defaults, cycleId=cycle['cycleId'])
            manager.frames.release(frame['frameId'], owner)
            observed = service.cameraDiagnostics.watch(observer, 0)
            if (not observed['preview'] or observed['metadata']['frameId'] != frame['frameId']
                    or observed['metadata']['cycleId'] != cycle['cycleId'] or manager.frames.frames):
                raise RuntimeError('camera observation did not retain an independent bounded display copy')
            service.projectConfiguration.endCycle(owner, cycle['cycleId'])
            manager.retire(owner)
            observer.close()
            if observer.observed is not None or manager.maintenanceOwners:
                raise RuntimeError('camera observer did not release its display copy')
        finally:
            service.close()
    return dict(oldInputValue=1, publishedValue=99, originalFramesRetired=True, sameManifestRepaired=True,
                sameRevisionRecovered=True, hardwareAccessed=False, runtimeClosed=True, designerPackageChecked=includeDesigner,
                deviceSetupChecked=True, configurationCopyChecked=True, portableDevicesChecked=includeDesigner,
                sourceFieldCompared=includeDesigner, cameraObserverChecked=True)


def checkTargetedDesignerRepairs():
    return checkTargetedRepairs(includeDesigner=True)
