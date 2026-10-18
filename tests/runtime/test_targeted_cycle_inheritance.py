"""N01: real consumers must execute retained input, not just report its revision."""
from copy import deepcopy
import json
from threading import Event

import pytest

from emo_master.apps.runtime.frame_dispatch.scheduler import RunGroup
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.configuration import projectChanges
from tests.runtime.test_frame_dispatch import dispatchProject
from tests.runtime.test_project_configuration import authority, request, finish
from tests.runtime.test_device_production_outcomes import waitFor
from tests.runtime.runtime_test_utils import waitForTerminal


def inheritedProject(*, join=True, derived=False):
    p = dispatchProject(join=join)
    p['runtime']['maxConcurrentJobs'] = 3
    main = p['workflows']['main']
    dispatch = next(n for n in main['nodes'] if n['nodeId'] == 'dispatch')
    dispatch['params']['targets'] = dispatch['params']['targets'][:1]
    dispatch['inputPorts']['count'] = 'number'
    dispatch['params']['targets'][0]['inputs']['count'] = 'count'
    main['nodes'].append(dict(nodeId='zero', operatorId='vision.value.number', params=dict(value=0)))
    main['edges'].append(dict(fromNode='zero', fromPort='value', toNode='dispatch', toPort='count'))
    ports = dict(image='image', count='number')
    p['workflows']['consumer'] = dict(name='Consumer', inputs=ports, outputs=ports, nodes=[
        dict(nodeId='input', kind='workflow_input'), dict(nodeId='output', kind='workflow_output'),
        dict(nodeId='loop', kind='loop', inputPorts=ports, outputPorts=ports,
             loop=dict(contractVersion=2, mode='while', maxIterations=2, timeoutMs=0,
                       bodyWorkflowId='body', conditionWorkflowId='condition', cycleBoundary='iteration'))],
        edges=[dict(fromNode=a, fromPort=port, toNode=b, toPort=port)
               for a, b in [('input', 'loop'), ('loop', 'output')] for port in ports])
    p['workflows']['condition'] = dict(name='Condition', inputs=ports, outputs=dict(continue_='boolean'), nodes=[
        dict(nodeId='input', kind='workflow_input'), dict(nodeId='output', kind='workflow_output'),
        dict(nodeId='compare', operatorId='vision.compare.number', params=dict(operator='lt', rightValue=1))],
        edges=[dict(fromNode='input', fromPort='count', toNode='compare', toPort='left'),
               dict(fromNode='compare', fromPort='result', toNode='output', toPort='continue')])
    p['workflows']['condition']['outputs'] = {'continue': 'boolean'}
    p['workflows']['body'] = dict(name='Body', inputs=ports, outputs=ports, nodes=[
        dict(nodeId='input', kind='workflow_input'), dict(nodeId='output', kind='workflow_output'),
        dict(nodeId='value', operatorId='vision.value.number', params=dict(value=1)),
        dict(nodeId='forward', kind='dispatch', inputPorts=dict(image='image'), params=dict(targets=[
            dict(targetSlotId='leaf', workflowId='leaf', inputs=dict(image='image'))])),
        dict(nodeId='join', kind='join', params=dict(dispatchNodeId='forward'))],
        edges=[dict(fromNode='value', fromPort='value', toNode='output', toPort='count'),
               dict(fromNode='forward', fromPort='token', toNode='join', toPort='token')])
    body = p['workflows']['body']
    imageNode = 'input'
    if derived:
        body['nodes'].append(dict(nodeId='blur', operatorId='vision.preprocess.blur', params={}))
        body['edges'].append(dict(fromNode='input', fromPort='image', toNode='blur', toPort='image'))
        imageNode = 'blur'
    body['edges'].extend(dict(fromNode=imageNode, fromPort='image', toNode=dest, toPort='image')
                         for dest in ['forward', 'output'])
    p['workflows']['leaf'] = dict(name='Leaf', inputs=dict(image='image'), outputs=dict(image='image'),
        nodes=[dict(nodeId='input', kind='workflow_input'), dict(nodeId='output', kind='workflow_output')],
        edges=[dict(fromNode='input', fromPort='image', toNode='output', toPort='image')])
    p['workflowOrder'] = list(p['workflows'])
    return p


@pytest.mark.parametrize('join,derived,depth', [(True, False, 2), (False, False, 2), (True, True, 2), (True, False, 3)])
def testQueuedInputWhileAndRedispatchKeepActualPlanAndFrame(tmp_path, monkeypatch, join, derived, depth):
    path = tmp_path / 'project.json'
    payload = inheritedProject(join=join, derived=derived)
    if depth == 3:
        payload['runtime']['maxConcurrentJobs'] = 4
        payload['workflows']['tip'] = deepcopy(payload['workflows']['leaf'])
        leaf = payload['workflows']['leaf']
        leaf['nodes'].extend([
            dict(nodeId='forwardTip', kind='dispatch', inputPorts=dict(image='image'), params=dict(targets=[
                dict(targetSlotId='tip', workflowId='tip', inputs=dict(image='image'))])),
            dict(nodeId='joinTip', kind='join', params=dict(dispatchNodeId='forwardTip'))])
        leaf['edges'].extend([
            dict(fromNode='input', fromPort='image', toNode='forwardTip', toPort='image'),
            dict(fromNode='forwardTip', fromPort='token', toNode='joinTip', toPort='token')])
        payload['workflowOrder'].append('tip')
    path.write_text(json.dumps(payload), encoding='utf-8')
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    import grpc
    from emo_master.apps.runtime.main import createRuntimeServer
    from emo_master.apps.runtime.grpc_server.generated.runtime_pb2_grpc import RuntimeServiceStub
    server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}')
    remote = RuntimeServiceStub(channel)
    entered, release = Event(), Event()
    leafEntered, leafRelease = Event(), Event()
    invocations = []
    images = []
    original = RunGroup.handle
    def gate(group, owner, action, payload):
        result = original(group, owner, action, payload)
        if action == 'dispatchNext' and result['status'] == 'RUNNING':
            from emo_master.apps.runtime.devices.frame_store import copyFrame
            invocations.append(deepcopy(result))
            images.append(copyFrame(next(iter(result['frames'].values()))))
            if result['targetSlotId'] == 'left':
                entered.set()
                assert release.wait(20)
            elif depth == 3 and result['targetSlotId'] == 'leaf':
                leafEntered.set()
                assert leafRelease.wait(20)
        return result
    monkeypatch.setattr(RunGroup, 'handle', gate)
    try:
        loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        started = runtime.StartJob(pb.StartJobRequest(workflow_id='main'), None)
        assert started.ok, started.message
        assert entered.wait(20)
        if not join:
            assert waitForTerminal(runtime, started.job_id).status == 'COMPLETED'
        snapshot = runtime.projectConfiguration.snapshot()
        candidate = deepcopy(snapshot['payload'])
        next(n for n in candidate['workflows']['body']['nodes'] if n['nodeId'] == 'value')['params']['value'] = 99
        reply = remote.WriteProjectConfiguration(pb.ProjectConfigurationWriteRequest(request_id='update',
            expected_runtime_instance_id=runtime.runtimeInstanceId, expected_revision=snapshot['documentRevision'],
            local_payload_json=json.dumps(candidate),
            selected_paths=[c['path'] for c in projectChanges(snapshot['payload'], candidate)]), timeout=10)
        assert reply.ok, reply.message
        assert finish(runtime.projectConfiguration, 'update')['state'] == 'COMMITTED'
        release.set()
        if depth == 3:
            assert leafEntered.wait(20)
            next(n for n in candidate['workflows']['body']['nodes'] if n['nodeId'] == 'value')['params']['value'] = 100
            reply = remote.WriteProjectConfiguration(pb.ProjectConfigurationWriteRequest(request_id='update-r3',
                expected_runtime_instance_id=runtime.runtimeInstanceId, expected_revision=2,
                local_payload_json=json.dumps(candidate),
                selected_paths=['/workflows/body/nodes/value/params/value']), timeout=10)
            assert reply.ok, reply.message
            assert finish(runtime.projectConfiguration, 'update-r3')['state'] == 'COMMITTED'
            leafRelease.set()
        waitFor(lambda: all(r.isTerminal for r in runtime.jobRepository.all()), timeout=25)
        records = runtime.jobRepository.all()
        values = [json.loads(e.payloadJson) for r in records for e in runtime._eventsAfter(r.jobId, 0)
                  if e.eventType == 'node.completed' and e.nodeId == 'value']
        assert values and {v['outputs']['value'] for v in values} == {1}, values
        assert all(r.status == 'COMPLETED' for r in records), records
        assert all(v['planRevision'] == 1 for v in values)
        assert len(invocations) == depth
        first, leaf = invocations[:2]
        assert leaf['cycleId'] == first['cycleId'] == values[0]['cycleId']
        assert leaf['planRevision'] == first['planRevision'] == 1
        assert (set(first['frames']) != set(leaf['frames'])) == derived
        assert all(v['cycleId'] == first['cycleId'] and v['planRevision'] == 1 for v in invocations)
        import numpy as np
        import cv2
        expected = cv2.GaussianBlur(images[0], (5, 5), 0) if derived else images[0]
        assert all(np.array_equal(value, expected) for value in images[1:])
        waitFor(lambda: not runtime.deviceManager.workers, timeout=15)
        assert not runtime.projectConfiguration.cycles and not runtime.projectConfiguration.childCycles
        assert not runtime.deviceManager.frames.frames
    finally:
        release.set()
        leafRelease.set()
        channel.close()
        server.stop(0).wait()
        runtime.close()


def testRetainedRootAndNestedScopesBorrowButNewDetectionRefreshes(tmp_path):
    c = authority(tmp_path)
    try:
        root = c.beginCycle('source', 1)
        c.retainCycle('source', root['cycleId'], 'delivery')
        c.retainCycle('delivery', root['cycleId'], 'consumer')
        c.start(request(c))
        assert finish(c)['state'] == 'COMMITTED'
        c.endCycle('source', root['cycleId'])
        old = c.beginCycle('consumer', 2, parentCycleId=root['cycleId'])
        assert old['cycleId'] == root['cycleId'] and old['planRevision'] == 1
        assert old['ownsCycle'] is False and not c.childCycles
        fresh = c.beginCycle('source', 1)
        outer = c.beginCycle('source', 2, parentCycleId=fresh['cycleId'])
        nested = c.beginCycle('source', 2, parentCycleId=outer['cycleId'])
        assert nested['cycleId'] == outer['cycleId'] and nested['ownsCycle'] is False
        assert outer['ownsCycle'] is True and outer['planRevision'] == 2
    finally:
        for owner in ['source', 'delivery', 'consumer']:
            c.retireOwner(owner)
        c.close()


def testPersistentRunnerCanInstallNewOldNewAndBorrowedExceptionDoesNotEndOwner(tmp_path):
    from dataclasses import replace
    from pathlib import Path
    from emo_master.apps.runtime.context.sqlite_store import SqliteStore
    from emo_master.apps.runtime.project_config.store import ConfigurationStore
    from emo_master.apps.runtime.project_config.coordinator import ConfigurationCoordinator
    from emo_master.apps.runtime.project_config.cycle_plan import CyclePlanSession
    from emo_master.apps.runtime.workflow.runner import WorkflowRunner
    from emo_master.apps.runtime.workflow.context import RunContext
    from emo_master.apps.runtime.workflow.cancellation import CancellationToken
    from emo_master.core.plugin.registry import PluginRegistry
    from emo_master.core.project.models import ProjectDocument
    from emo_master.core.workflow.compiler import WorkflowCompiler
    from tests.runtime.test_loop_cycle_configuration import loopProject
    payload = loopProject()
    body = payload['workflows']['body']
    body['nodes'] = [n for n in body['nodes'] if n['nodeId'] != 'capture']
    next(n for n in body['nodes'] if n['nodeId'] == 'value')['params']['value'] = 1
    body['edges'] = [dict(fromNode='value', fromPort='value', toNode='output', toPort='count')]
    store = ConfigurationStore(SqliteStore(tmp_path / 'runtime.db'))
    store.initialize(payload, str(tmp_path / 'project.json'))
    c = ConfigurationCoordinator(store, 'instance', prepare=lambda p, e: p, publish=lambda p, r: None)
    scan = PluginRegistry('0.6.1').scanRoots([Path(__file__).resolve().parents[2] / 'src/emo_master/plugins/builtins'])
    compiler = WorkflowCompiler(scan.activeOperators, deviceTypes=scan.deviceTypes)
    document = ProjectDocument.model_validate(payload)
    runner = WorkflowRunner(compiler.compile(document), scan.activeOperators, retainOperators=True)
    class Proxy:
        def call(self, action, **fields):
            return getattr(c, action)('consumer', **fields)
    plans = CyclePlanSession(runner, Proxy(), document, compiler, scan.activeOperators, (), 1, lambda: False)
    runner.cycleScope = plans.iteration
    try:
        old = c.beginCycle('old', 1)
        update = request(c)
        update['localPayload']['workflows']['body']['nodes'][-1]['params']['value'] = 99
        update['selectedPaths'] = [v['path'] for v in projectChanges(payload, update['localPayload'])]
        c.start(update)
        assert finish(c)['state'] == 'COMMITTED'
        new = c.beginCycle('new', 1)
        for owner, cycle, expected in [('new', new, 99), ('old', old, 1), ('new', new, 99)]:
            retained = c.retainCycle(owner, cycle['cycleId'], 'consumer')
            plans.install(retained)
            context = replace(RunContext.root('consumer', 'main'), cycleId=cycle['cycleId'],
                              planRevision=cycle['planRevision'], documentRevision=cycle['documentRevision'])
            result = runner.run('main', {'count': 0}, context, CancellationToken())
            assert result.outputs['count'] == expected
            before = plans.document, runner.compiledProject, plans.revision
            with pytest.raises(RuntimeError, match='injected'):
                with plans.iteration(context) as nested:
                    assert nested.cycleId == cycle['cycleId']
                    raise RuntimeError('injected')
            assert (plans.document, runner.compiledProject, plans.revision) == before
            assert c.requireCycle('consumer', cycle['cycleId']) == cycle['planRevision']
            assert not c.childCycles
            c.retireOwner('consumer')
    finally:
        runner.closeSession(RunContext.root('consumer', 'main'))
        for owner in ['old', 'new', 'consumer']:
            c.retireOwner(owner)
        c.close()
