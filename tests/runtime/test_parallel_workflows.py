import pytest

from emo_master.apps.operator_runtime.controller import ProductionRuntime
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from tests.runtime.production_fixture import saveDocument, waitFor
from tests.runtime.two_station_fixture import twoStationProject


def cycles(owner, job):
    return [event for event in owner.runtime.eventStore.read(job) if event.eventType == "workflow.completed"]


@pytest.mark.parametrize('limit,count', [(4, 4), (None, 5)])
def testMoreThanTwoActualWhileJobsWithImages(limit, count, tmp_path):
    from examples.runtime_pages_p2 import pluginRoots
    from tests.runtime.parallel_workflow_fixture import whileProject
    root = tmp_path / 'engineering'
    _, roots = whileProject(root, count=count, limit=limit)
    owner = ProductionRuntime(tmp_path / 'data', pluginRootPaths=pluginRoots(tmp_path))
    try:
        owner.load(root)
        results = owner.startAll(roots)
        assert all(row['ok'] for row in results.values()), results
        jobs = [row['jobId'] for row in results.values()]
        waitFor(lambda: all(len(cycles(owner, job)) >= 4 for job in jobs), seconds=45)
        assert all(owner.runtime.jobRepository.get(job).status == 'RUNNING' for job in jobs)
        assert len({owner.runtime.jobRepository.get(job).pid for job in jobs}) == count
        waitFor(lambda: len(owner.presentation.store.latest) == count)
        assert len(owner.presentation.jobs) == count
        assert len(owner.presentation.exporter.slots) == count
        for job in jobs:
            result = next(r for r in owner.presentation.store.latest.values() if r.identity.jobId == job)
            assert result.identity.resultOrdinal >= 1
        before = {key: value['jobId'] for key, value in owner.startAll(roots).items()}
        assert list(before.values()) == jobs  # Start again does not duplicate healthy Jobs.
        owner.stop(roots[0])
        assert all(owner.runtime.jobRepository.get(job).status == 'RUNNING' for job in jobs[1:])
    finally:
        owner.close()
    assert all(not owner.runtime.jobSupervisor.ownsJobResources(job) for job in jobs)


def testBatchLimitRejectsBeforeAnyJobIsStarted(tmp_path):
    from examples.runtime_pages_p2 import pluginRoots
    from tests.runtime.parallel_workflow_fixture import whileProject
    root = tmp_path / 'engineering'
    _, roots = whileProject(root, count=3, limit=2)
    owner = ProductionRuntime(tmp_path / 'data', pluginRootPaths=pluginRoots(tmp_path))
    try:
        owner.load(root)
        with pytest.raises(ValueError, match='E_MAX_CONCURRENT_JOBS'):
            owner.startAll(roots)
        assert not owner.runtime.jobRepository.all()
    finally:
        owner.close()


def testRetiredDifferentRootDoesNotConsumeAnActiveConcurrencySlot(tmp_path):
    from examples.runtime_pages_p2 import pluginRoots
    from tests.runtime.parallel_workflow_fixture import whileProject
    root = tmp_path / 'engineering'
    _, roots = whileProject(root, count=3, limit=2)
    owner = ProductionRuntime(tmp_path / 'data', pluginRootPaths=pluginRoots(tmp_path))
    try:
        owner.load(root)
        result = owner.startAll(roots[:2])
        assert all(row['ok'] for row in result.values())
        first, second = (result[key]['jobId'] for key in roots[:2])
        waitFor(lambda: all(len(cycles(owner, job)) >= 2 for job in (first, second)))
        owner.stop(roots[0])
        waitFor(lambda: not owner.presentation.readers[first].is_alive())
        third = owner.start(roots[2])
        waitFor(lambda: len(cycles(owner, third)) >= 2)
        assert owner.runtime.jobRepository.get(second).status == 'RUNNING'
        assert owner.sessions[roots[1]].jobId == second
        assert first not in owner.presentation.jobs
        assert set(owner.presentation.jobs) == {second, third}
    finally:
        owner.close()


def testSpawnedJobsIndependentRestartAndDisplayQuota(tmp_path):
    root = tmp_path / "engineering"
    document = twoStationProject(root)
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(root)
        started = owner.startAll(["main", "second"])
        assert all(row['ok'] for row in started.values())
        first, second = started['main']['jobId'], started['second']['jobId']
        waitFor(lambda: len(cycles(owner, first)) >= 2 and len(cycles(owner, second)) >= 3)
        assert len(owner.presentation.jobs) == 2
        waitFor(lambda: len(owner.presentation.store.latest) == 2)
        for result in owner.presentation.store.latest.values():
            assert {source.sourceId for source in result.sources} == ({"count", "image"} if result.identity.jobId == first
                                                                     else {"count-second", "image-second"})
        reply = owner.runtime.LoadProject(pb.LoadProjectRequest(project_path=str(root)), None)
        assert not reply.ok and owner.runtime.loadedDocument is owner.document
        with pytest.raises(ValueError, match="stop the current"):
            owner.start("main")
        document.production.cycleIntervalMs = 300
        saveDocument(root, document)
        for _ in range(2):
            owner.stop("main")
            before = len(cycles(owner, second))
            first = owner.start("main")
            waitFor(lambda: len(cycles(owner, first)) >= 2 and len(cycles(owner, second)) > before)
            assert owner.sessions["second"].jobId == second
            assert len(owner.presentation.jobs) == 2
        assert owner.settings.cycleIntervalMs == 100
        outcomes = owner.stopAll()
        assert all(row["ok"] for row in outcomes.values()) and owner.status()["canLoad"]
    finally:
        owner.close()
    assert owner.closed


def testPerWorkflowStartLedgerUncertaintyAndPartialActions(tmp_path, monkeypatch):
    root = tmp_path / "engineering"
    document = twoStationProject(root)
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(root)
        original = owner.runtime.StartJob
        calls = []
        def uncertain(request, context):
            calls.append((request.workflow_id, request.start_request_id))
            if request.workflow_id == "main":
                raise RuntimeError("boundary outcome unknown")
            return original(request, context)
        monkeypatch.setattr(owner.runtime, "StartJob", uncertain)
        results = owner.startAll(["main", "second"])
        assert not results["main"]["ok"] and results["second"]["ok"]
        second = owner.sessions["second"].jobId
        waitFor(lambda: len(cycles(owner, second)) >= 2)
        assert owner.status("main")["state"] == "FAULT" and not owner.status("main")["canStart"]
        assert not owner.status()["canLoad"]
        retry = owner.startAll(["main", "second"])
        assert retry["second"]["alreadyRunning"] and len(calls) == 2
        assert owner.sessions["second"].jobId == second
        # Stop all attempts the healthy station despite the other uncertain outcome.
        stopped = owner.stopAll()
        assert not stopped["main"]["ok"] and stopped["second"]["ok"]
        assert owner.runtime.jobRepository.get(second).isTerminal
        assert document.project.projectId == owner.document.project.projectId
    finally:
        owner.close()


def testExactAdmittedStartLookupAndIdempotencyAndDirectClientOwnership(tmp_path, monkeypatch):
    root = tmp_path / "engineering"
    document = twoStationProject(root)
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(root)
        original = owner.runtime.StartJob
        calls = []
        def loseReply(request, context):
            calls.append(request)
            original(request, context)
            raise RuntimeError("reply lost after actual admission")
        monkeypatch.setattr(owner.runtime, "StartJob", loseReply)
        job = owner.start("main")
        assert not owner.sessions["main"].startUncertain and owner.sessions["main"].requestId
        assert len(calls) == 1
        assert original(calls[0], None).job_id == job
        assert len(owner.runtime.jobRepository.all()) == 1
        # A direct client starts the second root. The owner must discover it,
        # retain the engineering lock, and include it in stop/close.
        reply = original(pb.StartJobRequest(project_id=document.project.projectId, workflow_id="second"), None)
        assert reply.ok
        assert owner.status("second")["jobId"] == reply.job_id
        with pytest.raises(ValueError, match="stop the current"):
            owner.load(root)
        assert owner.projectOwner.stream is not None and owner.document is not None
        assert all(row["ok"] for row in owner.stopAll().values())
    finally:
        owner.close()


def testAllStopReportsOneRpcFailureButAttemptsEveryWorkflowAndCloseRetiresAll(tmp_path, monkeypatch):
    root = tmp_path / "engineering"
    twoStationProject(root)
    owner = ProductionRuntime(tmp_path / "data")
    try:
        owner.load(root)
        first, second = owner.start("main"), owner.start("second")
        original = owner.runtime.StopJob
        attempted = []
        def stop(request, context):
            attempted.append(request.job_id)
            if request.job_id == first:
                return pb.StopJobReply(ok=False, message="simulated Stop RPC failure")
            return original(request, context)
        monkeypatch.setattr(owner.runtime, "StopJob", stop)
        results = owner.stopAll()
        assert not results["main"]["ok"] and results["second"]["ok"]
        assert attempted == [first, second]
        assert owner.runtime.jobRepository.get(second).isTerminal
        assert owner.runtime.jobSupervisor.ownsJobResources(first)
    finally:
        owner.close()
    assert not owner.runtime.jobSupervisor.ownsJobResources(first)
    assert not owner.runtime.jobSupervisor.ownsJobResources(second)


def testDebugJobCanEditItsOwnShotValueWithoutResettingOtherJob(tmp_path):
    from emo_master.apps.runtime.context.global_variables import ProjectGlobalVariables
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    root = tmp_path / "engineering"
    document = twoStationProject(root)
    # Ordinary debug loading compiles literal file parameters; this test is
    # about variable RPC isolation, not production resource preparation.
    for workflow in document.workflows.values():
        next(node for node in workflow.nodes if node.nodeId == "load").params["imagePath"] = str(root / "input.png")
    saveDocument(root, document)
    runtime = RuntimeService(dbPath=tmp_path / "debug/runtime.sqlite3")
    try:
        assert runtime.LoadProject(pb.LoadProjectRequest(project_path=str(root)), None).ok
        job = runtime.jobManager.createJob(document.project.projectId, document.project.revision, "main")
        otherJob = runtime.jobManager.createJob(document.project.projectId, document.project.revision, "main")
        debug = ProjectGlobalVariables(runtime.sqliteStore, document.project.projectId, document.globalVariables, job.jobId)
        other = ProjectGlobalVariables(runtime.sqliteStore, document.project.projectId, document.globalVariables, otherJob.jobId)
        debug.initializeJob()
        other.initializeJob()
        other.set("s1-count", 1)
        revision = debug.records(["s1-count"])["s1-count"].revision
        request = pb.GlobalVariablesRequest(project_id=document.project.projectId, job_id=job.jobId,
            variable_id="s1-count", value_json="2", expected_revision=revision)
        reply = runtime.SetGlobalVariable(request, None)
        assert reply.ok
        assert debug.get("s1-count") == 2 and other.get("s1-count") == 1
        request.expected_revision = debug.records(["s1-count"])["s1-count"].revision
        reply = runtime.ResetGlobalVariable(request, None)
        assert reply.ok and debug.get("s1-count") == 0 and other.get("s1-count") == 1
        runtime.jobRepository.update(job.jobId, status="COMPLETED")
        runtime.jobRepository.update(otherJob.jobId, status="COMPLETED")
    finally:
        runtime.close()
