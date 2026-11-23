from threading import Event
from types import SimpleNamespace

import pytest

from PySide2.QtCore import Qt
from PySide2.QtTest import QTest

from emo_master.apps.designer.page_designer.runtime_logs import eventPayload
from emo_master.apps.designer.state.job_log import ExecutionTree, JobEventWindow, JobIterationPage, jobExecutionTime
from emo_master.apps.designer.ui.log_dialog import StructuredLogEntry
from emo_master.apps.designer.ui.runtime_job_logs import RuntimeJobLogs
from emo_master.apps.runtime.grpc_server.generated.runtime_pb2 import JobEvent, JobHistorySummary, RunState
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


def testRoundPageKeepsFiftyCompletedOuterRoundsThroughTheOldestStart():
    page = JobIterationPage()

    def event(kind, index, **fields):
        return dict({"eventType": "loop.iteration." + kind, "workflowRunId": "main",
                     "nodeId": "loop", "iterationPath": [index], "payload": {}}, **fields)

    assert not page.observe({"eventType": "node.started", "iterationPath": []})
    assert not page.hasLoopEvidence
    assert not page.observe({"eventType": "node.log", "iterationPath": [174]})
    assert page.hasLoopEvidence
    assert not page.observe(event("started", 174))  # cancelled mid-round
    assert not page.observe(event("completed", 173, payload={"bodyExecuted": False}))
    assert not page.observe(event("started", 173))  # While exit check
    for index in range(172, 123, -1):
        assert not page.observe(event("completed", index, payload={"bodyExecuted": True}))
        assert not page.observe(event("completed", index, iterationPath=[index, 0]))
        assert not page.observe(event("started", index, iterationPath=[index, 0]))
        assert not page.observe(event("started", index))

    assert not page.observe(event("completed", 123))  # Repeat and ForEach omit bodyExecuted
    assert not page.observe(event("started", 123, workflowRunId="another-call"))
    assert not page.observe(event("started", 123, nodeId="another-loop"))
    assert page.observe(event("started", 123))


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
    assert branches["workflow", "first"].title == branches["workflow", "second"].title == "main"
    loop = branches["node", "first", "outer"]
    assert loop.mode == "while" and loop.totalIterations is None and loop.completedIterations == 1
    assert "已加载 1 轮完成 · 另有 1 次迭代结束记录（循环体执行情况未记录）" in loop.title
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


def testLoopPageReportsEndedIterationsWhenBodyExecutionIsUnknown():
    rows = [_event(index + 1, "loop.iteration.completed", caller="loop", nodeRun="outer", path=(index,))
            for index in range(50)]
    rows.append(_event(51, "loop.iteration.started", caller="loop", nodeRun="outer", path=(50,)))
    tree = ExecutionTree(rows)
    loop = next(branch for branch in _walk(tree.roots) if branch.kind == "loop")
    assert loop.completedIterations == 0
    assert loop.title == "loop · 循环类型未记录 · 已加载 50 次迭代结束记录（循环体执行情况未记录） · 总数未记录"


def testOnlyOuterRoundsAreNewestFirstWithinTheirOwningLoop():
    def outer(key, kind, path=()):
        return _event(key, kind, caller="loop", nodeRun="outer", path=path)

    def inner(key, kind, path):
        return _event(key, kind, "body", caller="nested", nodeRun="inner", parent="first", path=path)

    tree = ExecutionTree([
        outer(1, "loop.started"), outer(2, "loop.iteration.started", (0,)),
        _event(3, "workflow.started", "body", caller="loop", parent="first", path=(0,)),
        inner(4, "loop.started", (0,)), inner(5, "loop.iteration.started", (0, 0)),
        inner(6, "loop.iteration.completed", (0, 0)), inner(7, "loop.iteration.started", (0, 1)),
        inner(8, "loop.iteration.completed", (0, 1)), inner(9, "loop.completed", (0,)),
        _event(10, "workflow.completed", "body", caller="loop", parent="first", path=(0,)),
        outer(11, "loop.iteration.completed", (0,)), outer(12, "loop.iteration.started", (1,)),
        outer(13, "loop.iteration.completed", (1,)), outer(14, "loop.iteration.started", (2,)),
        outer(15, "loop.failed"),
    ])
    branches = {branch.key: branch for branch in _walk(tree.roots)}
    assert tree.hasOuterIterations
    assert tree.defaultExpandedKeys == {("workflow", "first"), ("node", "first", "outer")}
    plain = ExecutionTree([_event(1, "workflow.started")])
    assert not plain.hasOuterIterations and plain.defaultExpandedKeys == {("workflow", "first")}
    assert [child.key for child in branches["node", "first", "outer"].children] == [
        ("event", 1), ("iteration", "first", "loop", 2), ("iteration", "first", "loop", 1),
        ("iteration", "first", "loop", 0), ("event", 15)]
    assert [child.key for child in branches["iteration", "first", "loop", 0].children] == [
        ("event", 2), ("workflow", "body"), ("event", 11)]
    assert [child.key for child in branches["node", "body", "inner"].children] == [
        ("event", 4), ("iteration", "body", "nested", 0, 0), ("iteration", "body", "nested", 0, 1), ("event", 9)]


@pytest.mark.parametrize("separateCalls", (False, True), ids=("sibling-loops", "sibling-workflow-calls"))
def testNewerRoundGroupsComeFirstWithoutMovingOtherSiblings(separateCalls):
    old, new = ("old-call", "new-call") if separateCalls else ("first", "first")
    rows = [
        _event(1, "workflow.started"),
        _event(3, "loop.iteration.started", old, caller="old", nodeRun="old", path=(99,)),
        _event(4, "loop.iteration.completed", old, caller="old", nodeRun="old", path=(99,)),
        _event(5, "node.log", caller="ordinary", nodeRun="ordinary"),
        _event(7, "loop.iteration.started", new, caller="new", nodeRun="new", path=(0,)),
        _event(8, "loop.iteration.completed", new, caller="new", nodeRun="new", path=(0,)),
        _event(9, "loop.completed", old, caller="old", nodeRun="old"),
        _event(10, "workflow.completed"),
    ]
    if separateCalls:
        rows.extend([_event(2, "workflow.started", old, parent="first"),
                     _event(6, "workflow.started", new, parent="first")])
    tree = ExecutionTree(rows)
    branches = {branch.key: branch for branch in _walk(tree.roots)}
    olderGroup = ("workflow", old) if separateCalls else ("node", old, "old")
    newerGroup = ("workflow", new) if separateCalls else ("node", new, "new")
    assert [child.key for child in branches["workflow", "first"].children] == [
        ("event", 1), newerGroup, ("node", "first", "ordinary"), olderGroup, ("event", 10)]
    for run, node, index in ((old, "old", 99), (new, "new", 0)):
        assert branches["iteration", run, node, index] in branches["node", run, node].children
        if separateCalls:
            assert branches["node", run, node] in branches["workflow", run].children


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
        assert view._opening and not view._continuous and not view._canReadEarlier()
        view.appendEntry(_event(9, "node.log", caller="node", nodeRun="node"))
        view.appendEntry(_event(11, "node.log", caller="node", nodeRun="node"))
        release.set()
        waitUntil(lambda: not view._opening)
        assert [row.eventId for row in view.window.entries] == [7, 8, 9, 11]
        assert view._continuous
        view.appendEntry(_event(12, "node.log", caller="node", nodeRun="node"))
        assert [row.eventId for row in view.window.entries] == [8, 9, 11, 12]
        view.mode.setCurrentText("时间顺序")
        waitUntil(lambda: view.events.topLevelItemCount() == 4)
        assert [view.events.topLevelItem(i).data(0, Qt.UserRole)[1] for i in range(4)] == [8, 9, 11, 12]
        view.events.scrollToTop()
        QTest.keyClick(view.events, Qt.Key_PageUp)
        waitUntil(lambda: view.pending is None)
        assert requests[-1]["beforeEventId"] == 8 and requests[-1]["upperEventId"] == 12
        assert view.window.paused
        assert [row.eventId for row in view.window.entries] == [7, 8, 9, 11]
    finally:
        release.set()
        view.close()
        view.shutdown()


def testCatalogUsesRunStatusAndProvesRelatedJobsFromCurrentIdentity(designerApplication):
    def active(jobId, status, **fields):
        return RunState(job_id=jobId, runtime_instance_id="runtime", project_id=fields.pop("project", "project"),
                        workflow_name="同名工作流", workflow_id="wf", active=True, status=status, **fields)

    source = active("source", "FAILED", run_group_id="group", source_job_id="source", ended_at_ms=10,
                    allowed_actions=["stop"], message="实际错误")
    one = active("one", "ACCEPTED", run_group_id="group", source_job_id="source")
    two = active("two", "STOPPING", run_group_id="group", source_job_id="source")
    independent = active("independent", "RUNNING", run_group_id="group", source_job_id="other-source")
    otherProject = active("other-project", "RUNNING", run_group_id="group", source_job_id="source", project="other")
    owned = active("owned", "ABORTED", owns_resources=True)
    ordinary = JobHistorySummary(job_id="ordinary", project_id="project", workflow_id="ordinary", status="COMPLETED",
                                 ended_at_ms=20)
    duplicate = JobHistorySummary(job_id=source.job_id, project_id=source.project_id, workflow_id="stale",
                                  status="COMPLETED", ended_at_ms=10)
    view = RuntimeJobLogs(10, StructuredLogEntry.fromRuntimeEvent)
    try:
        view.synchronize(SimpleNamespace(runtimeService=object()), "runtime")
        view.runs = {job.job_id: job for job in (source, one, two, independent, otherProject, owned)}
        view.recent = (ordinary, duplicate)
        view.show()
        view._renderCatalog()

        def rows(section):
            group, = view.directory.findItems(section, Qt.MatchExactly)
            return {group.child(i).toolTip(0): group.child(i) for i in range(group.childCount())}

        assert set(rows("运行中")) == {"one", "two", "independent", "other-project"}
        ended = rows("最近已结束")
        assert set(ended) == {"ordinary", "source", "owned"}
        assert ended["source"].text(2) == "失败" and "还有 2 个关联任务未结束" in ended["source"].text(5)
        assert ended["owned"].text(2) == "已停止" and ended["owned"].text(5) == "结束处理未完成"
        assert "未提供具体原因" in ended["owned"].toolTip(5)
        assert ended["ordinary"].text(5) == ""
        view.directory.setCurrentItem(ended["source"])
        assert view.stopButton.isVisible() and view.catalogRelatedButton.isVisible()
        view._renderCatalog()
        assert view.directory.currentItem().toolTip(0) == "source"

        view.runs.pop("source")
        view.recent = (ordinary,)
        view._renderCatalog()
        assert "source" not in rows("最近已结束")
        view._historyMode = True
        view.history = [ordinary]
        view._renderCatalog()
        assert set(rows("终态任务")) == {"ordinary"}
        view.directory.setCurrentItem(rows("终态任务")["ordinary"])
        assert not view.stopButton.isVisible()
    finally:
        view.close()
        view.shutdown()


def testRelatedBookmarkReplaysTheSavedRangeWithDecreasingPageCursors(designerApplication):
    requests = []

    def query(**options):
        requests.append(options)
        keys = {12: (11, 9), 9: (8, 6, 4), 16: (11, 9)}.get(options.get("beforeEventId"), (21,))
        return dict(ok=True, runtimeInstanceId="runtime", upperEventId=options.get("upperEventId", 21),
                    events=[dict(eventId=key) for key in keys], nextEventId=keys[-1], hasMore=keys[-1] in (4, 9))

    view = RuntimeJobLogs(5, StructuredLogEntry.fromRuntimeEvent)
    try:
        client = SimpleNamespace(queryRuntimeLogs=query)
        scope = dict(beforeEventId=12, upperEventId=15, runtimeInstanceId="runtime", jobId="source")
        page = view._readBookmarkedRange(client, scope, 6, object())
        assert [row["eventId"] for row in page["events"]] == [11, 9, 8, 6]
        assert page["hasMore"] and page["nextEventId"] == 6
        assert [request["beforeEventId"] for request in requests] == [12, 9]
        assert {request["upperEventId"] for request in requests} == {15}

        unavailable = view._readBookmarkedRange(client, dict(scope, beforeEventId=16), 15, object())
        assert [row["eventId"] for row in unavailable["events"]] == [21]
        assert "beforeEventId" not in requests[-1] and "upperEventId" not in requests[-1]
    finally:
        view.shutdown()


@pytest.mark.parametrize("available", (True, False), ids=("restored", "unavailable"))
def testRelatedBookmarkRestoresTemporarilyRevealedEventOrClosesDrawer(designerApplication, available):
    source = RunState(job_id="job", project_id="project", runtime_instance_id="runtime", workflow_id="main",
                      status="RUNNING", active=True, run_group_id="group")
    member = RunState(job_id="related", project_id="project", runtime_instance_id="runtime", workflow_id="secondary",
                      status="RUNNING", active=True, run_group_id="group", source_job_id="job")
    original, revealed = ("event", 6), ("event", 8)
    rows = {
        "job": [_event(5, "workflow.started", projectId="project"),
                _event(6, "node.log", caller="warn", nodeRun="warn", level="WARN", projectId="project"),
                _event(8, "node.started", caller="step", nodeRun="current", projectId="project")],
        "related": [_event(12, "workflow.started", jobId="related", projectId="project")],
    }

    def query(jobId, **options):
        events = [row for row in reversed(rows[jobId]) if row.eventId < options.get("beforeEventId", 26)]
        return dict(ok=True, runtimeInstanceId="runtime", upperEventId=25, hasMore=False,
                    nextEventId=events[-1].eventId if events else 0,
                    events=[dict(eventId=row.eventId, event=row.rawRecord()) for row in events])

    view = RuntimeJobLogs(10, StructuredLogEntry.fromRuntimeEvent)
    try:
        view.synchronize(SimpleNamespace(runtimeService=object(), queryRuntimeLogs=query), "runtime")
        view.capabilities = {"runtime_logs_recent_v1"}
        view.progress("runtime", 25)
        view.runs = {job.job_id: job for job in (source, member)}
        view.show()
        view.openJob(source)
        waitUntil(lambda: not view._opening)
        view.mode.setCurrentIndex(1)
        view.warning.setChecked(True)
        waitUntil(lambda: set(view._items) == {original})
        view.events.setCurrentItem(view._items[original])
        view._locate()
        waitUntil(lambda: not view._opening and view._reveal == revealed)
        assert view.events.currentItem().data(0, Qt.UserRole) == revealed

        view.relatedButton.click()
        view.relatedJobs.setCurrentItem(view.relatedJobs.topLevelItem(0))
        view.openRelatedButton.click()
        waitUntil(lambda: not view._opening and view.job.job_id == "related")
        if not available:
            rows["job"].pop()
        view.returnSourceButton.click()
        waitUntil(lambda: not view._opening and view.job.job_id == "job")
        assert view.warning.isChecked() and view.mode.currentIndex() == 1
        if available:
            assert view._reveal == revealed and not view.returnContext.isHidden()
            assert view.events.currentItem().data(0, Qt.UserRole) == revealed and view._detailEntry.eventId == revealed[1]
            view.returnContext.click()
            assert set(view._items) == {original} and view.events.currentItem().data(0, Qt.UserRole) == original
            assert view._detailEntry.eventId == original[1]
        else:
            assert view._reveal is None and view.returnContext.isHidden()
            assert set(view._items) == {original} and view.events.currentItem() is None
            assert view.drawer.isHidden() and view._detailEntry is None and "无法" in view.jobHint.text()
    finally:
        view.close()
        view.shutdown()
