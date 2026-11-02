"""Stop all is dispatch-all, then retire-all with original per-Job deadlines."""
import threading
from types import SimpleNamespace

from emo_master.apps.operator_runtime import controller
from emo_master.apps.operator_runtime.controller import ProductionRuntime, WorkflowSession
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb


def testStopAllDispatchesBeforeWaitingAndPreservesIndependentErrors(monkeypatch):
    owner = object.__new__(ProductionRuntime)
    owner.operationLock = threading.RLock()
    owner.sessions = {str(i): WorkflowSession(jobId=f"job-{i}", startUncertain=i == 1) for i in range(5)}
    owner.runtime = object()
    owner.document = SimpleNamespace(project=SimpleNamespace(projectId="project"))
    reconciled = []
    requested = []
    def target(key):
        reconciled.append(key)
        if key == "1":
            raise RuntimeError("Start outcome unknown")
        return owner.sessions[key]
    def currentRuns(runtime, projectId):
        assert runtime is owner.runtime and projectId == "project"
        assert reconciled == ["1"]
        return [pb.RunState(workflow_id=str(i), job_id=f"job-{i}", active=True) for i in (0, 2, 3, 4)]
    def request(session):
        requested.append(session.jobId)
        return 123
    def wait(session, deadline):
        assert requested == ["job-0", "job-2", "job-3", "job-4"]
        assert deadline == 123
        if session.jobId == "job-2":
            raise TimeoutError("Worker still owns Runtime resources")
    owner._stopTarget = target
    owner._requestStopSession = request
    owner._waitRetired = wait
    monkeypatch.setattr(controller, "currentRuns", currentRuns)
    rows = owner.stopAll()
    assert list(rows) == ["1", "0", "2", "3", "4"]
    assert rows["0"]["ok"] and rows["3"]["ok"] and rows["4"]["ok"]
    assert rows["1"] == dict(ok=False, jobId="job-1", message="Start outcome unknown")
    assert rows["2"] == dict(ok=False, jobId="job-2", message="Worker still owns Runtime resources")
