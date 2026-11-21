from threading import Event
from types import SimpleNamespace

from emo_master.apps.designer.page_designer.runtime_logs import eventPayload
from emo_master.apps.designer.state.job_log import ExecutionTree, JobEventWindow, jobExecutionTime
from emo_master.apps.designer.ui.log_dialog import StructuredLogEntry
from emo_master.apps.designer.ui.runtime_job_logs import RuntimeJobLogs
from emo_master.apps.runtime.grpc_server.generated.runtime_pb2 import JobEvent
from tests.designer.qt_wait import waitUntil


def _event(eventId, eventType, run="first", *, caller="", nodeRun="", parent="", path=(),
           payload=None, level="INFO", **extra):
    return StructuredLogEntry.fromRuntimeEvent(dict(
        {"eventId": eventId, "eventType": eventType, "jobId": "job", "runtimeInstanceId": "runtime",
         "timestampMs": eventId * 100, "workflowId": "main", "workflowRunId": run,
         "nodeId": caller, "nodeRunId": nodeRun, "parentWorkflowRunId": parent, "iterationPath": list(path),
         "payload": payload or {}, "message": eventType, "level": level}, **extra))


def _walk(branches):
    for branch in branches:
        yield branch
        yield from _walk(branch.children)


def testTreeAssociatesChildCallsAndNestedLoopsBeforeProjectingWarnings():
    rows = [
        _event(1, "workflow.started"),
        _event(2, "node.started", caller="loop", nodeRun="outer"),
        _event(3, "loop.started", caller="loop", nodeRun="outer", payload={"mode": "while"}),
        _event(4, "loop.iteration.started", caller="loop", nodeRun="outer", path=(0,)),
        _event(5, "workflow.started", "body", caller="loop", parent="first", path=(0,)),
        _event(6, "loop.started", "body", caller="repeat", nodeRun="inner", parent="first", path=(0,),
               payload={"mode": "repeat", "totalIterations": 1}),
        _event(7, "loop.iteration.started", "body", caller="repeat", nodeRun="inner", parent="first", path=(0, 0)),
        _event(8, "workflow.started", "nested", caller="repeat", parent="body", path=(0, 0)),
        _event(9, "node.started", "nested", caller="operator", nodeRun="same", parent="body", path=(0, 0)),
        _event(10, "node.log", "nested", caller="operator", nodeRun="same", parent="body", path=(0, 0), level="WARN"),
        _event(11, "node.completed", "nested", caller="operator", nodeRun="same", parent="body", path=(0, 0)),
        _event(12, "workflow.completed", "nested", caller="repeat", parent="body", path=(0, 0)),
        _event(13, "loop.iteration.completed", "body", caller="repeat", nodeRun="inner", parent="first", path=(0, 0)),
        _event(14, "workflow.completed", "body", caller="loop", parent="first", path=(0,)),
        _event(15, "loop.iteration.completed", caller="loop", nodeRun="outer", path=(0,), payload={"bodyExecuted": True}),
        _event(16, "loop.iteration.started", caller="loop", nodeRun="outer", path=(1,)),
        _event(17, "workflow.started", "condition", caller="loop", parent="first", path=(1,)),
        _event(18, "loop.iteration.completed", caller="loop", nodeRun="outer", path=(1,), payload={"bodyExecuted": False}),
        _event(19, "loop.iteration.completed", caller="loop", nodeRun="outer", path=(2,)),
        _event(20, "workflow.completed"),
        _event(21, "workflow.started", "second"),
        _event(22, "node.started", "second", caller="operator", nodeRun="same"),
        _event(23, "workflow.started", "child", parent="unloaded-parent", caller="not-loaded"),
        _event(24, "node.log", "detached", parentWorkflowRunId=None, iterationPath=None),
        _event(25, "device.action.retry", caller="loop", nodeRun="outer", path=(0,)),
    ]
    tree = ExecutionTree(reversed(rows))
    assert tree.rootCount == 2
    branches = {branch.key: branch for branch in _walk(tree.roots)}
    loop = branches["node", "first", "outer"]
    assert loop.mode == "while" and loop.totalIterations is None and loop.completedIterations == 1
    assert branches["node", "body", "inner"].totalIterations == 1
    assert branches["node", "nested", "same"].executionTime().elapsedMs == 200
    assert tree.branchForEvent(10) is branches["node", "nested", "same"]
    assert tree.representativeEventId(("iteration", "body", "repeat", 0, 0)) == 7
    assert branches["node", "second", "same"].end is None
    assert branches["workflow", "unloaded-parent"].children == [branches["workflow", "child"]]
    assert branches["workflow", "detached"] in branches["unlinked",].children
    exitCheck = branches["iteration", "first", "loop", 1]
    assert exitCheck.bodyExecuted is False and "未进入循环体" in exitCheck.title
    assert branches["workflow", "condition"] in exitCheck.children
    unknown = branches["iteration", "first", "loop", 2]
    assert unknown.bodyExecuted is None and "未记录" in unknown.title

    filtered = tree.project(lambda entry: entry.level == "WARN")
    assert [branch.key for branch in _walk(filtered)] == [
        ("workflow", "first"), ("node", "first", "outer"), ("iteration", "first", "loop", 0),
        ("workflow", "body"), ("node", "body", "inner"), ("iteration", "body", "repeat", 0, 0),
        ("workflow", "nested"), ("node", "nested", "same"), ("event", 10),
    ]
    context = tree.contextMatches(10)
    assert context is not None
    assert [row.eventId for row in rows if context(row)] == [7, 8, 9, 10, 11, 12, 13]
    assert tree.branchForEvent(25) is branches["iteration", "first", "loop", 0]


def testHistoryWindowPreservesAnchorsAndCountsLiveWithoutHoldingPayloads():
    window = JobEventWindow("job", maximumEntries=4)
    before = [_event(key, "node.log") for key in (5, 6, 7, 9)]
    window.mergeRecent(reversed(before))
    window.pause()
    assert not window.appendLive([before[-1], _event(10, "node.log"), _event(10, "node.log")])
    assert not window.appendLive([_event(10, "node.log"), _event(12, "node.log", originApp="designer")])
    assert window.newCount == 1 and window.entries == tuple(before)
    earlier = [_event(key, "node.log") for key in (2, 3)]
    assert not window.mergeEarlier(earlier, protected=(5, 9))
    assert window.entries == tuple(before) and not window.trimmed
    assert window.mergeEarlier(earlier, protected=(5, 6))
    assert [entry.eventId for entry in window.entries] == [2, 3, 5, 6]
    assert window.trimmed and window.paused
    window.mergeRecent(before)
    assert window.appendLive([before[-1], _event(10, "node.log")])
    assert [entry.eventId for entry in window.entries] == [6, 7, 9, 10]
    assert window.trimmed and not window.paused and not window.newCount


def testTimeAndCurrentPositionUseOnlyMatchedLifecycleAndCurrentObservation():
    assert jobExecutionTime({"status": "ACCEPTED", "startAtMs": 300}, 800).acceptedAtMs == 300
    stopping = jobExecutionTime({"status": "STOPPING", "startedAtMs": 400, "acceptedAtMs": 300}, 800)
    assert stopping.estimated and stopping.elapsedMs == 400
    completed = jobExecutionTime({"status": "FAILED", "active": True, "startedAtMs": 400, "endedAtMs": 700}, 900)
    assert not completed.estimated and completed.elapsedMs == 300
    restarted = jobExecutionTime({"status": "FAILED", "startedAtMs": 400, "endedAtMs": 700,
                                  "errorCode": "E_RUNTIME_RESTARTED"}, 900)
    assert not restarted.estimated and restarted.elapsedMs is restarted.endedAtMs is None
    assert restarted.confirmedAtMs == 700 and "重启确认" in restarted.display(showRange=True)

    rows = [
        _event(1, "workflow.started"),
        _event(2, "node.started", caller="old", nodeRun="old", timestampMs=0),
        _event(3, "node.completed", caller="old", nodeRun="old"),
        _event(4, "node.skipped", caller="skipped", nodeRun="skip"),
        _event(5, "node.started", caller="unfinished", nodeRun="unfinished"),
        _event(6, "workflow.failed"),
        _event(7, "workflow.started", "next"),
        _event(8, "node.started", "next", caller="active", nodeRun="active"),
        _event(9, "node.log", "next", caller="unrelated", nodeRun="later"),
    ]
    tree = ExecutionTree(rows)
    branches = {branch.key: branch for branch in _walk(tree.roots)}
    missing = branches["node", "first", "old"].executionTime()
    assert missing.elapsedMs is None and "原始时间" in missing.note
    assert branches["node", "first", "skip"].executionTime().elapsedMs is None
    assert branches["node", "first", "unfinished"].executionTime().endedAtMs is None
    options = dict(jobStatus="STOPPING", runtimeInstanceId="runtime", continuous=True)
    assert tree.currentNode(**options) == rows[-2]
    assert tree.currentNode(**dict(options, continuous=False)) is None
    assert tree.currentNode(**dict(options, runtimeInstanceId="other")) is None
    assert tree.currentNode(**dict(options, jobStatus="ABORTED")) is None
    assert ExecutionTree(rows + [_event(10, "node.started", "next", caller="parallel", nodeRun="parallel")]).currentNode(**options) is None


def testRuntimeAttributionAndRawRecordKeepRecordedIdentityAndMissingFields():
    summary = _event(1, "device.fault.summary", jobId="__runtime__", payload={"jobId": "job"})
    system = _event(2, "runtime.started", jobId="__runtime__")
    local = _event(3, "designer.rpc.failed", originApp="designer")
    raw = {"eventType": "node.started", "jobId": "job", "parentWorkflowRunId": "parent", "timestampMs": 0}
    entry = StructuredLogEntry.fromRuntimeEvent(raw)
    assert summary.effectiveJobId == "job" and not summary.isRuntimeSystem
    assert summary.rawRecord()["jobId"] == "__runtime__"
    assert system.isRuntimeSystem and not system.effectiveJobId
    assert not local.isRuntime and not local.effectiveJobId
    assert entry.parentWorkflowRunId == "parent" and entry.rawTimestampMs is None
    assert not entry.iterationPathRecorded and summary.iterationPathRecorded
    assert entry.rawRecord() == raw
    missingPath = StructuredLogEntry.fromRuntimeEvent(eventPayload(JobEvent()))
    emptyPath = StructuredLogEntry.fromRuntimeEvent(eventPayload(JobEvent(iteration_path_json="[]")))
    assert not missingPath.iterationPathRecorded and emptyPath.iterationPathRecorded
    oldDevice = _event(4, "device.action.failed", payload={"cursor": 22, "source": {"iterationPath": [1]}})
    nonLoopDevice = _event(5, "device.action.failed", payload={"cursor": 23, "iterationPath": []})
    assert oldDevice.rawRecord()["iterationPath"] == [] and not oldDevice.iterationPathRecorded
    assert nonLoopDevice.iterationPathRecorded and nonLoopDevice.iterationPath == ()


def testWorkbenchClosesGapBeforeConfirmingContinuityAndReloadsLiveTrimmedRange(designerApplication):
    requests = []
    filling = Event()
    release = Event()

    def page(*keys, upper, nextCursor, more):
        return dict(ok=True, runtimeInstanceId="runtime", upperEventId=upper, nextEventId=nextCursor, hasMore=more,
                    events=[dict(eventId=key, event=_event(key, "node.log", caller="node", nodeRun="node").rawRecord())
                            for key in keys])

    def query(**options):
        requests.append(options)
        if not options.get("latestFirst"):
            filling.set()
            assert release.wait(5)
            return page(8, 9, upper=10, nextCursor=10, more=False)
        if options.get("beforeEventId"):
            return page(7, upper=12, nextCursor=7, more=False)
        return page(7, 6, upper=7, nextCursor=6, more=True)

    view = RuntimeJobLogs(4, StructuredLogEntry.fromRuntimeEvent)
    try:
        view.synchronize(SimpleNamespace(runtimeService=object(), queryRuntimeLogs=query), "runtime")
        view.capabilities = {"runtime_logs_recent_v1"}
        view.progress("runtime", 10)
        view.show()
        view.openJob(SimpleNamespace(job_id="job", project_id="", workflow_id="main", status="RUNNING",
                                     accepted_at_ms=0, started_at_ms=0, ended_at_ms=0, error_code="", message=""))
        waitUntil(filling.is_set)
        assert view._opening and not view._continuous and not view.earlier.isEnabled()
        view.appendEntry(_event(9, "node.log", caller="node", nodeRun="node"))
        view.appendEntry(_event(11, "node.log", caller="node", nodeRun="node"))
        release.set()
        waitUntil(lambda: not view._opening)
        assert [row.eventId for row in view.window.entries] == [7, 8, 9, 11]
        assert view._continuous
        view.appendEntry(_event(12, "node.log", caller="node", nodeRun="node"))
        assert [row.eventId for row in view.window.entries] == [8, 9, 11, 12]
        view.earlier.click()
        waitUntil(lambda: view.pending is None)
        assert requests[-1]["beforeEventId"] == 8 and requests[-1]["upperEventId"] == 12
        assert view.window.paused
        assert [row.eventId for row in view.window.entries] == [7, 8, 9, 11]
    finally:
        release.set()
        view.close()
        view.shutdown()
