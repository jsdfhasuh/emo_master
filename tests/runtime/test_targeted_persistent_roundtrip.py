"""One spawned consumer processes explicitly delayed R2/R1/R2 deliveries."""
from collections import deque
from copy import deepcopy
import json
from threading import Event

from emo_master.apps.runtime.frame_dispatch.scheduler import RunGroup
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.configuration import projectChanges
from tests.runtime.test_loop_cycle_configuration import loopProject
from tests.runtime.test_project_configuration import finish
from tests.runtime.test_device_production_outcomes import waitFor
from tests.runtime.runtime_test_utils import waitForTerminal


def testOneSpawnedConsumerInstallsNewOldNewWithoutStaleOperators(tmp_path, monkeypatch):
    p = loopProject()
    p['devices']['definitions'] = {}
    p['runtime']['maxConcurrentJobs'] = 2
    p['workflows']['consumer'] = deepcopy(p['workflows']['main'])
    p['workflows']['consumer']['nodes'][1]['loop'].update(
        bodyWorkflowId='consumerBody', conditionWorkflowId='consumerCondition')
    p['workflows']['consumerCondition'] = deepcopy(p['workflows']['condition'])
    p['workflows']['consumerBody'] = dict(name='Body', inputs={'count': 'number'}, outputs={'count': 'number'},
        nodes=[dict(nodeId='input', kind='workflow_input'), dict(nodeId='output', kind='workflow_output'),
               dict(nodeId='value', operatorId='vision.value.number', params=dict(value=1))],
        edges=[dict(fromNode='value', fromPort='value', toNode='output', toPort='count')])
    body = p['workflows']['body']
    body['nodes'] = [dict(nodeId='input', kind='workflow_input'), dict(nodeId='output', kind='workflow_output'),
        dict(nodeId='dispatch', kind='dispatch', inputPorts={'count': 'number'}, params=dict(targets=[
            dict(targetSlotId='consumer', workflowId='consumer', inputs={'count': 'count'}, queueCapacity=4)]))]
    body['edges'].append(dict(fromNode='input', fromPort='count', toNode='dispatch', toPort='count'))
    p['workflowOrder'] = list(p['workflows'])
    path = tmp_path / 'project.json'
    path.write_text(json.dumps(p), encoding='utf-8')
    runtime = RuntimeService(dbPath=tmp_path / 'runtime.db', workspaceRoot=tmp_path / 'jobs')
    first, third, resumeFirst, resumeThird, consume = [Event() for _ in range(5)]
    handle, take = RunGroup.handle, RunGroup.next
    requests = []

    def gate(group, owner, action, payload):
        result = handle(group, owner, action, payload)
        if action == 'dispatchSubmit':
            requests.append(deepcopy(payload))
            if len(requests) == 1:
                first.set()
                assert resumeFirst.wait(15)
            elif len(requests) == 3:
                third.set()
                assert resumeThird.wait(15)
        return result

    def delayed(group, owner):
        if not consume.is_set():
            group.slots['consumer'].ready = True
            return dict(status='IDLE')
        return take(group, owner)

    monkeypatch.setattr(RunGroup, 'handle', gate)
    monkeypatch.setattr(RunGroup, 'next', delayed)
    try:
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        started = runtime.StartJob(pb.StartJobRequest(workflow_id='main', inputs_json='{"count":0}'), None)
        assert started.ok, started.message
        assert first.wait(15)

        def publish(workflow, nodeId, field, value, requestId):
            snap = runtime.projectConfiguration.snapshot()
            candidate = deepcopy(snap['payload'])
            next(n for n in candidate['workflows'][workflow]['nodes'] if n['nodeId'] == nodeId)['params'][field] = value
            reply = runtime.WriteProjectConfiguration(pb.ProjectConfigurationWriteRequest(request_id=requestId,
                expected_runtime_instance_id=runtime.runtimeInstanceId, expected_revision=snap['documentRevision'],
                local_payload_json=json.dumps(candidate),
                selected_paths=[v['path'] for v in projectChanges(snap['payload'], candidate)]), None)
            assert reply.ok, reply.message
            assert finish(runtime.projectConfiguration, requestId)['state'] == 'COMMITTED'

        publish('consumerBody', 'value', 'value', 99, 'r2')
        resumeFirst.set()
        assert third.wait(15)
        assert [v['planRevision'] for v in requests] == [1, 2, 2]
        group = runtime.deviceManager.dispatchGroups[started.job_id]
        # Model a delayed upstream R1 arrival without changing any lease/payload.
        with group.lock:
            a, b, c = group.slots['consumer'].queue
            group.slots['consumer'].queue = deque([b, a, c])
        publish('condition', 'compare', 'rightValue', 0, 'stop-source')
        consume.set()
        resumeThird.set()
        assert waitForTerminal(runtime, started.job_id, timeoutSeconds=25).status == 'COMPLETED'
        waitFor(lambda: not runtime.deviceManager.dispatchGroups, timeout=20)
        consumers = [r for r in runtime.jobRepository.all() if r.workflowId == 'consumer']
        assert len(consumers) == 1 and consumers[0].status == 'COMPLETED'
        values = [json.loads(e.payloadJson) for e in runtime._eventsAfter(consumers[0].jobId, 0)
                  if e.eventType == 'node.completed' and e.workflowId == 'consumerBody' and e.nodeId == 'value']
        assert [(v['planRevision'], v['outputs']['value']) for v in values] == [(2, 99), (1, 1), (2, 99)]
        assert not runtime.projectConfiguration.cycles and not runtime.projectConfiguration.childCycles
    finally:
        resumeFirst.set()
        resumeThird.set()
        consume.set()
        runtime.close()
