"""Stop all is dispatch-all, then retire-all with original per-Job deadlines."""
import threading
from types import SimpleNamespace

from emo_master.apps.operator_runtime.controller import ProductionRuntime


def testStopAllDispatchesBeforeWaitingAndPreservesIndependentErrors():
    owner = object.__new__(ProductionRuntime)
    owner.operationLock = threading.RLock()
    owner.sessions = {str(i): SimpleNamespace(jobId=f"job-{i}") for i in range(5)}
    requested = []
    def target(key):
        if key == "1":
            raise RuntimeError("Start outcome unknown")
        return owner.sessions[key]
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
    rows = owner.stopAll()
    assert list(rows) == list(owner.sessions)
    assert rows["0"]["ok"] and rows["3"]["ok"] and rows["4"]["ok"]
    assert rows["1"] == dict(ok=False, jobId="job-1", message="Start outcome unknown")
    assert rows["2"] == dict(ok=False, jobId="job-2", message="Worker still owns Runtime resources")
