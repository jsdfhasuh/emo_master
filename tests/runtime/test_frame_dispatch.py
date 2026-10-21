"""Persistent fan-out software evidence. No physical device or SDK is used."""
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from emo_master.apps.runtime.devices.executor import DeviceError
from emo_master.apps.runtime.devices.manager import DeviceManager
from emo_master.apps.runtime.frame_dispatch.scheduler import RunGroup, Slot
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.models import ProjectDocument
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.core.workflow.errors import WorkflowCompileError
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_project_configuration import authority
from tests.runtime.test_device_production_outcomes import waitFor
from tests.runtime.runtime_test_utils import waitForTerminal


def dispatchProject(*, join=True):
    payload = simulatedProject()
    payload["runtime"]["maxConcurrentJobs"] = 3
    payload["workflows"]["consumer"] = dict(name="Consumer", inputs={"image": "image"}, outputs={"image": "image"},
        nodes=[dict(nodeId="input", kind="workflow_input"), dict(nodeId="output", kind="workflow_output")],
        edges=[dict(fromNode="input", fromPort="image", toNode="output", toPort="image")])
    payload["workflowOrder"].append("consumer")
    flow = payload["workflows"]["main"]
    flow["nodes"].append(dict(nodeId="dispatch", kind="dispatch", inputPorts={"image": "image"}, params=dict(targets=[
        dict(targetSlotId=key, workflowId="consumer", inputs={"image": "image"}, queueCapacity=2)
        for key in ("left", "right")])))
    flow["edges"].append(dict(fromNode="capture", fromPort="image", toNode="dispatch", toPort="image"))
    if join:
        flow["nodes"].append(dict(nodeId="join", kind="join", params=dict(dispatchNodeId="dispatch", timeoutMs=30000)))
        flow["edges"].append(dict(fromNode="dispatch", fromPort="token", toNode="join", toPort="token"))
    return payload


@pytest.mark.parametrize("join", [True, False])
def testSpawnedPersistentFanoutRetiresOriginalsAndIndependentWorkers(tmp_path, join):
    path = tmp_path / "project.json"
    path.write_text(json.dumps(dispatchProject(join=join)), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        started = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        assert started.ok, started.message
        result = waitForTerminal(service, started.job_id, timeoutSeconds=45)
        assert result.status == "COMPLETED", result
        waitFor(lambda: not service.deviceManager.workers and not service.deviceManager.dispatchGroups, timeout=30)
        records = service.jobRepository.all()
        assert len(records) == 3
        assert len({r.pid for r in records}) == 3
        assert all(r.status == "COMPLETED" for r in records), records
        completions = [json.loads(e.payloadJson) for r in records for e in service._eventsAfter(r.jobId, 0)
                       if e.eventType == "dispatch.invocation.completed"]
        assert len(completions) == 2
        assert len({c["invocationId"] for c in completions}) == 2
        assert completions[0]["frameIds"] == completions[1]["frameIds"]
        assert completions[0]["cycleId"] == completions[1]["cycleId"]
        assert not service.deviceManager.frames.frames
        assert not service.deviceManager.dispatchGroups
        assert not service.projectConfiguration.cycles
        assert service.deviceManager.drivers["camera"].sequence == 1
    finally:
        service.close()


def testGroupCapacityRejectedBeforeCapture(tmp_path):
    payload = dispatchProject()
    payload["runtime"]["maxConcurrentJobs"] = 2
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        reply = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        assert not reply.ok and "MAX_CONCURRENT" in reply.message
        assert not service.deviceManager.workers
        assert service.deviceManager.drivers["camera"].sequence == 0
    finally:
        service.close()


@pytest.mark.parametrize('fail', [False, True])
def testConsumerDeviceLogsKeepRetainedCycleIdentityAndPersistOnce(tmp_path, monkeypatch, fail):
    # The consumer uses a device absent from the producer's own device bindings.
    builtins = Path(__file__).resolve().parents[2] / 'src/emo_master/plugins/builtins'
    plugin = tmp_path / 'dispatch_logging'
    plugin.mkdir()
    (plugin / '__init__.py').write_text('''
from dataclasses import replace
from emo_master.plugins.builtins.simulated_camera.operator import SimulatedCameraOperator
class LoggedCamera(SimulatedCameraOperator):
    meta = replace(SimulatedCameraOperator.meta, operatorId='test.dispatch.camera')
    def validateParams(self, params):
        return None
    def executeNode(self, inputs, params, context):
        result = super().executeNode(inputs, {}, context)
        context['logger'].warning('consumer capture', payload=dict(reading=42))
        if params['fail']:
            return dict(status='error', error=dict(code='E_TEST_CAPTURE', message='consumer rejected capture'))
        return result
''', encoding='utf-8')
    manifest = json.loads((builtins / 'simulated_camera/manifest.json').read_text(encoding='utf-8'))
    manifest.update(operatorId='test.dispatch.camera', entry='dispatch_logging:LoggedCamera',
        inputPorts=dict(image='image'), paramSchema=dict(type='object', properties=dict(fail=dict(type='boolean')),
                                                       required=['fail'], additionalProperties=False))
    (plugin / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    monkeypatch.syspath_prepend(str(tmp_path))
    payload = dispatchProject()
    consumer = payload['workflows']['consumer']
    consumer['nodes'].insert(1, dict(nodeId='consumer-capture', operatorId='test.dispatch.camera',
        params=dict(fail=fail), deviceBindings=dict(camera=dict(deviceId='consumer-camera'))))
    consumer['edges'] = [dict(fromNode='input', fromPort='image', toNode='consumer-capture', toPort='image'),
        dict(fromNode='consumer-capture', fromPort='image', toNode='output', toPort='image')]
    payload['devices']['definitions']['consumer-camera'] = deepcopy(payload['devices']['definitions']['camera'])
    payload['devices']['definitions']['consumer-camera']['connection']['serial'] = 'CONSUMER-ONLY'
    path = tmp_path / 'project.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    service = RuntimeService(dbPath=tmp_path/'runtime.db', workspaceRoot=tmp_path/'jobs',
        pluginRootPaths=(str(builtins), str(plugin)))
    originals = []
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        manager = service.deviceManager
        scope = dict(manager.scope)
        generation = manager.executors['consumer-camera'].generation
        started = service.StartJob(pb.StartJobRequest(workflow_id='main'), None)
        assert started.ok, started.message
        result = waitForTerminal(service, started.job_id, timeoutSeconds=45)
        assert result.status == ('FAILED' if fail else 'COMPLETED'), result
        waitFor(lambda: not manager.workers and not manager.dispatchGroups, timeout=30)
        assert not manager.frames.frames and not manager.frames.reservations
        assert not service.projectConfiguration.cycles and not service.projectConfiguration.childCycles
        records = [r for r in service.jobRepository.all() if r.workflowId == 'consumer']
        assert len(records) == 2 and len({r.pid for r in records}) == 2
        assert all(r.status == ('FAILED' if fail else 'COMPLETED') for r in records)
        originals = [e for r in records for e in service._eventsAfter(r.jobId, 0)
                     if e.nodeId == 'consumer-capture' and e.eventType in {'node.log', 'node.failed'}]
        assert sum(e.eventType == 'node.log' for e in originals) == 2
        assert sum(e.eventType == 'node.failed' for e in originals) == (2 if fail else 0)
        manager.recover('consumer-camera', generation)
        assert manager.executors['consumer-camera'].generation != generation
        rows = manager.diagnostics.query(scope, 'consumer-camera')['records']
        for event in originals:
            fields = json.loads(event.payloadJson)
            binding = fields['deviceBindings']['camera']
            assert binding['scope'] == scope
            assert binding['deviceGeneration'] == generation
            row = next(r for r in rows if r['eventType'] == event.eventType and r['nodeRunId'] == event.nodeRunId)
            assert all(row[key] == value for key, value in scope.items())
            assert row['deviceId'] == 'consumer-camera' and row['deviceGeneration'] == generation
            assert row['timestampMs'] == event.timestampMs and row['level'] == event.level
            assert row['workflowRunId'] == event.workflowRunId and row['ownerId'] == event.jobId
            assert row['cycleId'] == fields['cycleId'] and row['planRevision'] == fields['planRevision']
            assert row['iterationPath'] == json.loads(event.iterationPathJson)
            assert row['phase'] == fields.get('phase', '') and row['data'] == fields.get('data', {})
            if event.eventType == 'node.log':
                assert row['level'] == 'WARN' and row['data'] == dict(reading=42)
    finally:
        service.close()
    persisted = [json.loads(line) for path in tmp_path.rglob('runtime-*.jsonl')
                 for line in path.read_text(encoding='utf-8').splitlines()]
    for event in originals:
        assert sum(row['eventType'] == event.eventType and row['nodeRunId'] == event.nodeRunId
                   for row in persisted) == 1


def testActualConsumerProcessCrashAfterDurableSideEffectNeverReplays(tmp_path, monkeypatch):
    from threading import Event
    from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
    payload = dispatchProject()
    payload["globalVariables"] = {"effects": dict(name="Effects", type="integer", kind="variable",
        initialValue=0, lifetime="persistent")}
    flow = payload["workflows"]["consumer"]
    flow["outputs"]["count"] = "any"
    flow["nodes"].append(dict(nodeId="effect", kind="operator", operatorId="vision.state.variable_write",
        params=dict(variableId="effects", operation="increment", delta=1)))
    flow["edges"].extend([dict(fromNode="input", fromPort="image", toNode="effect", toPort="after"),
        dict(fromNode="effect", fromPort="value", toNode="output", toPort="count")])
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    completed, release = Event(), Event()
    interrupted = []
    handle = RunGroup.handle
    def beforeAcknowledgement(group, owner, action, request):
        if owner == group.slots["left"].jobId and action == "dispatchComplete":
            interrupted.append((owner, group))
            completed.set()
            assert release.wait(15)
            raise DeviceError("E_DEVICE_OUTCOME_UNKNOWN", "consumer died before completion acknowledgement", outcome="UNKNOWN")
        return handle(group, owner, action, request)
    monkeypatch.setattr(RunGroup, "handle", beforeAcknowledgement)
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        started = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        assert started.ok, started.message
        assert completed.wait(30)
        variables = ProjectGlobalVariables(service.sqliteStore, service.loadedProjectId, service.loadedDocument.globalVariables)
        waitFor(lambda: variables.get("effects") == 2)
        owner, group = interrupted[0]
        assert group.slots["left"].active and service.deviceManager.frames.frames
        with service.jobSupervisor._lock:
            process = service.jobSupervisor.getProcess(owner)
            process.terminate()
            process.join(5)
            assert not process.is_alive()
        release.set()
        result = waitForTerminal(service, started.job_id, timeoutSeconds=30)
        assert result.status == "FAILED", result
        waitFor(lambda: not service.deviceManager.dispatchGroups, timeout=30)
        assert variables.get("effects") == 2
        assert len(service.jobRepository.all()) == 3
        assert service.jobRepository.get(owner).status == "FAILED"
        assert not service.deviceManager.frames.frames and not service.projectConfiguration.cycles
        assert not service.jobSupervisor._ownedJobs()
    finally:
        release.set()
        service.close()


def testPartialConsumerStartupFailureRetiresWithoutProducerAction(tmp_path, monkeypatch):
    path = tmp_path / "project.json"
    path.write_text(json.dumps(dispatchProject()), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        start = service.jobSupervisor.startJob
        def failSecond(spec):
            if spec.targetSlotId == "right":
                raise RuntimeError("injected consumer startup failure")
            return start(spec)
        monkeypatch.setattr(service.jobSupervisor, "startJob", failSecond)
        reply = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        assert not reply.ok and "injected consumer" in reply.message
        waitFor(lambda: not service.deviceManager.dispatchGroups, timeout=30)
        assert all(record.isTerminal for record in service.jobRepository.all())
        assert not service.deviceManager.workers
        assert not service.deviceManager.frames.frames
        assert not service.projectConfiguration.cycles
        assert service.deviceManager.drivers["camera"].sequence == 0
        assert not service.jobSupervisor._ownedJobs()
    finally:
        service.close()


def testContinuousInvocationsReuseProcessesWithoutGrowingLeases(tmp_path):
    payload = dispatchProject()
    payload["production"].update(mode="continuous", cycleIntervalMs=10)
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        service.productionMode = True
        reply = service.StartJob(pb.StartJobRequest(workflow_id="main"), None)
        assert reply.ok, reply.message
        def completions():
            return [json.loads(e.payloadJson) for r in service.jobRepository.all() for e in service._eventsAfter(r.jobId, 0)
                    if e.eventType == "dispatch.invocation.completed"]
        waitFor(lambda: len(completions()) >= 12, timeout=40)
        assert len(service.jobRepository.all()) == 3
        assert all(r.status == "RUNNING" for r in service.jobRepository.all())
        group = service.deviceManager.dispatchGroups[reply.job_id]
        with group.lock:
            assert len(group.records) <= 1
            assert len(service.deviceManager.frames.frames) <= 1
        outcome = service.jobSupervisor.stopJobOutcome(reply.job_id)
        assert outcome.ok, outcome
        waitFor(lambda: not service.deviceManager.workers and not service.deviceManager.dispatchGroups, timeout=30)
        assert not service.deviceManager.frames.frames
        assert not service.projectConfiguration.cycles
        assert not service.deviceManager.dispatchGroups
    finally:
        service.close()


@pytest.fixture
def group(tmp_path):
    devices = DeviceManager({})
    devices.configuration = authority(tmp_path)
    document = ProjectDocument.model_validate(dispatchProject())
    slots = {key: Slot("consumer", 1, key, ready=True) for key in ("left", "right")}
    group = RunGroup("source", slots, devices, document)
    cycle = devices.configuration.beginCycle("source", 0)
    reservation = devices.frames.reserve("source", 35)
    frame = devices.frames.publish(reservation, np.arange(35, dtype=np.uint8).reshape(5, 7),
        cycleId=cycle["cycleId"], planRevision=cycle["planRevision"], generation=1, requestId="capture")
    request = dict(action="dispatchSubmit", dispatchSequence=1, workflowId="main", nodeId="dispatch",
        cycleId=cycle["cycleId"], planRevision=cycle["planRevision"], inputs={"image": dict(kind="frame", value=frame)})
    yield group, request, frame
    group.stop()
    for owner in group.members:
        devices.frames.retireOwner(owner)
        devices.configuration.retireOwner(owner)
        group.workerRetired(owner)
    devices.frames.close()


def finish(group, slot):
    invocation = group.next(slot)
    request = {key: invocation[key] for key in ("deliveryId", "invocationId", "cycleId", "planRevision", "dispatchId", "runGroupId")}
    group.complete(slot, dict(request, status="COMPLETED", outputs=dict(kind="dict", value={"ok": dict(kind="value", value=False)})))
    return invocation


def testDuplicateNoReplayAndPartialQueueReceipts(group):
    group, request, _ = group
    first = group.submit("source", request)
    assert group.submit("source", deepcopy(request)) == first
    assert group.submit("source", dict(request, sequence=99)) == first
    assert len(group.slots["left"].queue) == 1
    finish(group, "left")
    second = group.submit("source", dict(request, dispatchSequence=2))
    assert not second["accepted"]
    assert second["targets"]["left"]["status"] == "QUEUED"
    assert second["targets"]["right"]["code"] == "E_DISPATCH_QUEUE_FULL"
    assert first["dispatchId"] != second["dispatchId"]


def testSourceEndDoesNotRetireLiveConsumersAndOldPlan(group):
    group, request, frame = group
    group.submit("source", request)
    group.devices.configuration.endCycle("source", request["cycleId"])
    group.devices.frames.retireOwner("source")
    group.workerRetired("source")
    assert frame["frameId"] in group.devices.frames.frames
    left = finish(group, "left")
    assert left["cycle"]["planRevision"] == request["planRevision"]
    assert frame["frameId"] in group.devices.frames.frames
    finish(group, "right")
    assert not group.devices.frames.frames
    assert not group.devices.configuration.cycles


def testJoinIdentityAndNgIsNormalCompletion(group):
    group, request, _ = group
    token = group.submit("source", request)
    join = dict(token=token, cycleId=request["cycleId"], planRevision=request["planRevision"])
    assert group.join("source", join)["status"] == "WAITING"
    with pytest.raises(DeviceError, match="cycle"):
        group.join("source", dict(join, cycleId="foreign"))
    with pytest.raises(DeviceError, match="foreign"):
        group.join("left", join)
    finish(group, "left")
    finish(group, "right")
    result = group.join("source", join)
    assert result["status"] == "COMPLETED"
    assert result["results"]["left"]["outputs"]["value"]["ok"]["value"] is False


def testThreeHundredJoinsRetireDeliveriesAndBoundReplayReceipts(group):
    group, request, frame = group
    first = None
    for sequence in range(1, 301):
        current = dict(request, dispatchSequence=sequence)
        token = group.submit("source", current)
        if first is None:
            first = token
        finish(group, "left")
        finish(group, "right")
        joined = dict(token=token, cycleId=request["cycleId"], planRevision=request["planRevision"])
        result = group.join("source", joined)
        assert result["status"] == "COMPLETED"
        assert group.join("source", joined) == result
        assert group.submit("source", current) == token
        assert not group.records and not group.deliveries
        assert len(group.joinReceipts) <= 256
        assert group.devices.frames.frames[frame["frameId"]].owners == {"source"}
    with pytest.raises(DeviceError, match="expired"):
        group.submit("source", request)
    with pytest.raises(DeviceError, match="expired"):
        group.join("source", dict(joined, token=first))
    with pytest.raises(DeviceError, match="changed"):
        group.submit("source", dict(current, inputs={}))
    group.endCycle("source", request["cycleId"])
    assert not group.joinReceipts


def testCachedJoinNeverKeepsReleasedOutputAlive(group):
    group, request, frame = group
    token = group.submit("source", request)
    for slot in ("left", "right"):
        invocation = group.next(slot)
        completion = {key: invocation[key] for key in (
            "deliveryId", "invocationId", "cycleId", "planRevision", "dispatchId", "runGroupId")}
        group.complete(slot, dict(completion, status="COMPLETED", outputs=dict(
            kind="dict", value={"image": dict(kind="frame", value=frame)})))
    joined = dict(token=token, cycleId=request["cycleId"], planRevision=request["planRevision"])
    result = group.join("source", joined)
    assert group.join("source", joined) == result
    assert group.devices.frames.frames[frame["frameId"]].owners == {"source"}
    group.devices.frames.release(frame["frameId"], "source")
    assert not group.devices.frames.frames
    with pytest.raises(DeviceError, match="released"):
        group.join("source", joined)
    assert not group.records and not group.deliveries


def testCrashDoesNotReplayAndOtherConsumerRetainsFrame(group):
    group, request, frame = group
    group.submit("source", request)
    invocation = group.next("left")
    group.devices.frames.retireOwner("source")
    group.devices.frames.retireOwner("left")
    group.devices.configuration.retireOwner("left")
    group.workerRetired("left")
    assert group.deliveries[invocation["deliveryId"]]["code"] == "E_DISPATCH_WORKER_EXITED"
    assert frame["frameId"] in group.devices.frames.frames
    assert not group.slots["left"].queue
    finish(group, "right")
    assert not group.devices.frames.frames


def testQueuedConsumersKeepOldPlanAcrossNewSourceCycle(group):
    from tests.runtime.test_project_configuration import finish as configurationFinished
    group, request, frame = group
    configuration = group.devices.configuration
    token = group.submit("source", request)
    configuration.endCycle("source", request["cycleId"])
    group.endCycle("source", request["cycleId"])
    candidate = deepcopy(configuration.current["payload"])
    candidate["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] = 12000
    configuration.start(dict(requestId="cycle-update", expectedRuntimeInstanceId=configuration.runtimeInstanceId,
        expectedRevision=1, localPayload=candidate, selectedPaths=["/devices/definitions/camera/defaults/exposureUs"]))
    assert configurationFinished(configuration, "cycle-update")["state"] == "COMMITTED"
    newer = configuration.beginCycle("source", 0)
    assert newer["planRevision"] == 2
    left = finish(group, "left")
    assert left["cycle"]["planRevision"] == 1
    assert left["cycle"]["payload"]["devices"]["definitions"]["camera"]["defaults"]["exposureUs"] == 8000
    assert frame["frameId"] in group.devices.frames.frames
    right = finish(group, "right")
    assert left["dispatchId"] == right["dispatchId"] == token["dispatchId"]
    assert not group.records
    with pytest.raises(DeviceError, match="expired"):
        group.submit("source", request)


def testTimeoutAndStopDoNotUnlinkAnActiveConsumer(group):
    group, request, frame = group
    token = group.submit("source", request)
    group.next("left")
    assert group.join("source", dict(token=token, cycleId=request["cycleId"], planRevision=request["planRevision"]))["status"] == "WAITING"
    group.devices.frames.retireOwner("source")
    group.devices.configuration.retireOwner("source")
    group.workerRetired("source")
    group.stop()
    assert frame["frameId"] in group.devices.frames.frames
    assert group.deliveries[group.slots["left"].active]["status"] == "RUNNING"
    assert not group.slots["right"].queue


def testMutatedCopiesUseNewRuntimeOwnedFrames(group):
    from types import SimpleNamespace
    from threading import Event
    from emo_master.apps.runtime.devices.ipc import DeviceProxy
    from emo_master.apps.runtime.devices.frame_store import copyFrame
    group, request, frame = group
    store = group.devices.frames
    proxy = DeviceProxy(None, Event())
    def call(action, **fields):
        if action == "frameBeginWrite":
            return store.beginWrite("source", fields["shape"], fields["cycleId"], request["planRevision"])
        assert action == "frameCommitWrite"
        return store.commitWrite("source", fields["frameId"])
    proxy.framework = call
    context = SimpleNamespace(cycleId=request["cycleId"], planRevision=request["planRevision"])
    original = copyFrame(frame)
    proxy.rememberFrame(original, frame)
    assert proxy.encode(original, context)["value"]["frameId"] == frame["frameId"]
    independent = copyFrame(frame)
    original[:] = 255
    encoded = proxy.encode(original, context)
    assert encoded["value"]["frameId"] != frame["frameId"]
    assert np.array_equal(copyFrame(frame), independent)
    assert np.all(copyFrame(encoded["value"]) == 255)
    assert not store.writes and not store.reservations


def testDerivedImageOutputsAndIndependentRunGroups(tmp_path):
    payload = dispatchProject()
    payload["runtime"]["maxConcurrentJobs"] = 6
    consumer = payload["workflows"]["consumer"]
    consumer["nodes"].insert(1, dict(nodeId="blur", operatorId="vision.preprocess.blur",
                                   params=dict(mode="gaussian", kernelSize=3)))
    consumer["edges"] = [dict(fromNode="input", fromPort="image", toNode="blur", toPort="image"),
                         dict(fromNode="blur", fromPort="image", toNode="output", toPort="image")]
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        loaded = service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None)
        assert loaded.ok, loaded.message
        started = [service.StartJob(pb.StartJobRequest(workflow_id="main"), None) for _ in range(2)]
        assert all(item.ok for item in started)
        for item in started:
            assert waitForTerminal(service, item.job_id, timeoutSeconds=45).status == "COMPLETED"
        waitFor(lambda: not service.deviceManager.dispatchGroups, timeout=30)
        joined = [json.loads(e.payloadJson) for item in started for e in service._eventsAfter(item.job_id, 0)
                  if e.eventType == "dispatch.joined"]
        assert len(joined) == 2
        groupIds, outputFrames = set(), set()
        for result in joined:
            targets = result["results"]
            assert set(targets) == {"left", "right"}
            groupIds.update(value["runGroupId"] for value in targets.values())
            for target in targets.values():
                descriptor = target["outputs"]["value"]["image"]["value"]
                assert descriptor["frameId"] not in target["frames"]
                outputFrames.add(descriptor["frameId"])
        assert len(groupIds) == 2 and len(outputFrames) == 4
        assert len({r.pid for r in service.jobRepository.all()}) == 6
        assert not service.deviceManager.frames.frames and not service.deviceManager.frames.writes
    finally:
        service.close()


@pytest.mark.parametrize("mutation", ["mapping", "type", "recursive", "join"])
def testCompilerRejectsInvalidDispatch(mutation):
    payload = dispatchProject()
    # Remove the capture operator so these checks require no plugin registry.
    flow = payload["workflows"]["main"]
    flow["nodes"] = [n for n in flow["nodes"] if n["nodeId"] != "capture"]
    flow["edges"] = [e for e in flow["edges"] if e["fromNode"] != "capture"]
    dispatch = next(n for n in flow["nodes"] if n["kind"] == "dispatch")
    if mutation == "mapping":
        dispatch["params"]["targets"][0]["inputs"] = {}
    elif mutation == "type":
        dispatch["inputPorts"]["image"] = "number"
    elif mutation == "recursive":
        dispatch["params"]["targets"][0].update(workflowId="main", inputs={})
    else:
        next(n for n in flow["nodes"] if n["kind"] == "join")["params"]["dispatchNodeId"] = "missing"
    with pytest.raises(WorkflowCompileError):
        WorkflowCompiler({}).compile(ProjectDocument.model_validate(payload))
