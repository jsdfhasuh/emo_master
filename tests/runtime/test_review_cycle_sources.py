from copy import deepcopy
import json
from threading import Event

from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.project.configuration import projectChanges
from tests.runtime.test_loop_cycle_configuration import loopProject
from tests.runtime.test_device_production_outcomes import waitFor
from tests.runtime.test_project_configuration import finish


def testLiveNodeSourcesSwitchOnlyNextIterationAndMetadataKeepsJobAndConnection(tmp_path):
    payload = loopProject()
    payload["globalVariables"] = {"exposure": dict(name="Exposure", type="number", initialValue=16000)}
    path = tmp_path / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    runtime = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    entered, release = Event(), Event()
    captured = []
    try:
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        driver = runtime.deviceManager.drivers["camera"]
        execute = driver.execute
        def capture(action, params, cancelled, deadline, **kwargs):
            captured.append(params["exposureUs"])
            if len(captured) == 1:
                entered.set()
                assert release.wait(10)
            return execute(action, params, cancelled, deadline, **kwargs)
        driver.execute = capture
        started = runtime.StartJob(pb.StartJobRequest(workflow_id="main", inputs_json='{"count":0}'), None)
        assert started.ok and entered.wait(10)
        pid = runtime.jobRepository.get(started.job_id).pid
        generation = driver.generation
        c = runtime.projectConfiguration
        def write(name, edit):
            before = c.snapshot()
            after = deepcopy(before["payload"])
            node = next(n for n in after["workflows"]["body"]["nodes"] if n["nodeId"] == "capture")
            edit(after, node)
            runtime._validateProjectConfiguration(after, checkAdmission=False)
            assert runtime._supportsCycleChange(after)
            c.start(dict(requestId=name, expectedRuntimeInstanceId=runtime.runtimeInstanceId,
                expectedRevision=before["documentRevision"], localPayload=after,
                selectedPaths=[v["path"] for v in projectChanges(before["payload"], after)]))
            status = finish(c, name)
            assert status["state"] == "COMMITTED", status
            return status
        write("override", lambda p, n: n.update(deviceOverrides={"camera": {"exposureUs": 12000}}))
        assert captured == [8000]
        release.set()
        waitFor(lambda: 12000 in captured)
        def variable(p, node):
            node["deviceOverrides"] = {}
            node["deviceValueBindings"] = {"camera": {"exposureUs": {"source": "variable", "variableId": "exposure"}}}
        write("variable", variable)
        waitFor(lambda: 16000 in captured)
        count = len(captured)
        write("inherit", lambda p, n: n.update(deviceValueBindings={}))
        waitFor(lambda: 8000 in captured[count:])
        execution = c.snapshot()["executionRevision"]
        status = write("rename", lambda p, n: p["devices"]["definitions"]["camera"].update(name="Display name", description="Label"))
        assert status["executionRevision"] == execution
        assert runtime.jobRepository.get(started.job_id).pid == pid
        assert runtime.jobRepository.get(started.job_id).status == "RUNNING"
        assert driver.generation == generation and runtime.deviceManager.drivers["camera"] is driver
        candidate = deepcopy(c.snapshot()["payload"])
        candidate["devices"]["definitions"]["camera"]["connection"]["serial"] = "new identity"
        assert not runtime._supportsCycleChange(candidate)
    finally:
        release.set()
        runtime.close()
