from copy import deepcopy
from pathlib import Path
import json

import pytest

from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.state.workflow_package import buildWorkflowPackage, importWorkflowPackage, loadWorkflowPackage, writeWorkflowPackage
from emo_master.core.workflow.dispatch_contracts import groupSlots
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.project.models import ProjectDocument
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from tests.runtime.test_frame_dispatch import dispatchProject
from tests.runtime.runtime_test_utils import waitForTerminal
from tests.runtime.test_device_production_outcomes import waitFor


@pytest.mark.parametrize('portable,newDevice', [(False, False), (True, False), (True, True)])
def testDispatchCopyImportJoinPortsAndCombinedRun(tmp_path, monkeypatch, portable, newDevice):
    monkeypatch.setattr('emo_master.apps.designer.state.workflow_store.utc_now_iso', lambda: '2026-10-18T00:00:00Z')
    payload = dispatchProject()
    payload['runtime']['maxConcurrentJobs'] = 5
    main = payload['workflows']['main']
    main['outputs'] = {'result': 'object'}
    next(n for n in main['nodes'] if n['nodeId'] == 'join')['outputPorts'] = dict(left='object', right='object')
    main['edges'].append(dict(fromNode='join', fromPort='left', toNode='output', toPort='result'))
    if newDevice:
        payload['resources']['siteBindings'] = [dict(field='serial', purpose='camera_serial', target=dict(
            deviceId='camera', section='connection', parameterPath=['serial']))]
    store = WorkflowStore(payload)
    before = store.toPayload()
    package = buildWorkflowPackage(store, 'main', forInProjectCopy=not portable, sourceRoot=tmp_path)
    if portable:
        package = loadWorkflowPackage(writeWorkflowPackage(tmp_path / 'portable', package))
    scan = PluginRegistry('0.6.1').scanRoots([Path(__file__).resolve().parents[2] / 'src/emo_master/plugins/builtins'])
    result = importWorkflowPackage(store, package, reuseVariableIds=not portable,
        deviceMapping={'camera': None if newDevice else 'camera'}, targetRoot=tmp_path,
        deviceTypes=scan.deviceTypes,
        operatorRequirements={key: descriptor.manifest.deviceRequirements
                              for key, descriptor in scan.activeOperators.items()})
    assert store.toPayload()['workflows']['main'] == before['workflows']['main']
    assert store.toPayload()['devices']['definitions']['camera'] == before['devices']['definitions']['camera']
    if newDevice:
        editable = ProjectDocument.model_validate(store.toPayload())
        device = editable.devices.definitions[result.deviceIdMap['camera']]
        assert not device.enabled and device.connection['serial'] == ''
        importedDevice = store.devices['definitions'][result.deviceIdMap['camera']]
        importedDevice['connection']['serial'] = 'IMPORTED-SIMULATION-ONLY'
        importedDevice['enabled'] = True
    imported = store.get(result.rootWorkflowId)
    dispatch = next(n for n in imported.nodes if n['kind'] == 'dispatch')
    slots = {t['targetSlotId'] for t in dispatch['params']['targets']}
    assert len(slots) == 2 and slots.isdisjoint({'left', 'right'})
    edge = next(e for e in imported.edges if e['fromNode'] == 'join' and e['toPort'] == 'result')
    assert edge['fromPort'] in slots
    document = store.toPayload()
    document['workflows']['both'] = dict(name='Both', inputs={}, outputs={}, nodes=[
        dict(nodeId='input', kind='workflow_input'), dict(nodeId='output', kind='workflow_output'),
        dict(nodeId='a', kind='subflow', targetWorkflowId='main'),
        dict(nodeId='b', kind='subflow', targetWorkflowId=result.rootWorkflowId)], edges=[])
    document['workflowOrder'].append('both')
    model = ProjectDocument.model_validate(document)
    assert len(groupSlots(model, 'both')) == 4
    WorkflowCompiler(scan.activeOperators, deviceTypes=scan.deviceTypes).compile(model)
    path = tmp_path / 'project.emoproj'
    path.write_text(json.dumps(document), encoding='utf-8')
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    try:
        loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        started = runtime.StartJob(pb.StartJobRequest(workflow_id='both'), None)
        assert started.ok, started.message
        assert waitForTerminal(runtime, started.job_id, timeoutSeconds=30).status == 'COMPLETED'
        waitFor(lambda: not runtime.deviceManager.workers, timeout=15)
        assert all(r.status == 'COMPLETED' for r in runtime.jobRepository.all())
    finally:
        runtime.close()


def testTypedJoinSourceRebindsPortWithoutEditingSource():
    from emo_master.apps.designer.state.workflow_package import _populateImportedWorkflow
    payload = dispatchProject()
    main = payload['workflows']['main']
    main['nodes'].append(dict(nodeId='writer', operatorId='vision.io.sqlite_writer', params=dict(
        mappings=[dict(source=dict(kind='node_output', nodeId='join', port='left'))])))
    source = ProjectDocument.model_validate(payload).workflows['main']
    before = deepcopy(source)
    store = WorkflowStore(payload)
    _populateImportedWorkflow(store, source, 'main', {'consumer': 'consumer'}, {'camera': 'camera'},
                              {('left', 'consumer', 2): 'left_copy', ('right', 'consumer', 2): 'right_copy'})
    writer = next(n for n in store.get('main').nodes if n['nodeId'] == 'writer')
    assert writer['params']['mappings'][0]['source'] == dict(kind='node_output', nodeId='join', port='left_copy')
    assert source == before
