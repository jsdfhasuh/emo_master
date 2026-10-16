"""Bounded software profile regressions, not inferred field image specifications."""
from copy import deepcopy
from dataclasses import replace
import json
from multiprocessing.shared_memory import SharedMemory
import threading
from types import SimpleNamespace
from uuid import uuid4

import cv2
import numpy as np
import pytest

from examples.runtime_pages_p2 import sampleProject
from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.presentation.collector import ResultCollector
from emo_master.apps.runtime.presentation.exporter import ExportPool, RAW_LIMIT
from emo_master.apps.runtime.presentation.rpc import DisplayRpc
from emo_master.apps.runtime.presentation.store import ResultStore
from emo_master.apps.runtime.workflow.context import RunContext
from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.core.presentation.capture_limits import normalCaptureLimits, normalCaptureProfile
from emo_master.core.project.models import ProjectDocument
from tests.runtime.presentation.test_images import waitFor, blockedEncoder
from tests.runtime.presentation.test_normal_capture import release
from tests.runtime.runtime_test_utils import jobFailureDetails, waitForTerminal


MIB = 1024 * 1024


def twoImages(root, alias=True):
    raw = sampleProject(root).model_dump()
    raw["workflows"]["main"]["nodes"][1]["params"]["imagePath"] = str(root / "input.png")
    presentation = raw["presentation"]
    presentation["dataSources"]["original"] = dict(presentation["dataSources"]["image"], nodeId="load", port="image")
    presentation["pages"]["second"] = {"name": "Original", "components": [
        {"componentId": "original-view", "type": "image", "bindings": {"image": "original"}}]}
    presentation["pageOrder"].append("second")
    if alias:
        presentation["dataSources"]["overlay-alias"] = deepcopy(presentation["dataSources"]["image"])
        presentation["pages"]["second"]["components"].append({"componentId": "overlay-again", "type": "image",
            "bindings": {"image": "overlay-alias"}, "layout": {"row": 1}})
    return ProjectDocument.model_validate(raw)


def twoCalls(root):
    raw = sampleProject(root).model_dump()
    child = raw["workflows"].pop("main")
    child["nodes"][1]["params"]["imagePath"] = str(root / "input.png")
    raw["workflows"]["child"] = child
    raw["workflows"]["main"] = {"name": "Two call sites", "nodes": [
        {"nodeId": "input", "kind": "workflow_input"},
        {"nodeId": "a", "kind": "subflow", "targetWorkflowId": "child"},
        {"nodeId": "b", "kind": "subflow", "targetWorkflowId": "child"},
        {"nodeId": "output", "kind": "workflow_output"}]}
    raw["workflowOrder"] = ["main", "child"]
    raw["resources"]["parameterBindings"][0]["target"]["workflowId"] = "child"
    presentation = raw["presentation"]
    presentation.update(defaultPageId="a", pageOrder=["a", "b"], pages={}, dataSources={}, resultScopes={})
    for scope in ("a", "b"):
        path = [{"nodeId": scope, "relation": "subflow"}]
        presentation["resultScopes"][scope] = {"entryWorkflowId": "main", "scopeWorkflowId": "child", "callPath": path}
        components = []
        for index, (name, node, port, kind, prop) in enumerate([
                ("image", "blob", "overlay", "image", "image"), ("count", "count", "count", "integer", "value")]):
            sourceId = f"{scope}-{name}"
            presentation["dataSources"][sourceId] = {"kind": "node_output", "resultScopeId": scope,
                "workflowId": "child", "callPath": path, "nodeId": node, "port": port, "expectedType": kind}
            components.append({"componentId": sourceId, "type": "image" if kind == "image" else "number",
                "bindings": {prop: sourceId}, "layout": {"row": index}})
        presentation["pages"][scope] = {"name": f"Call {scope}", "components": components}
    return ProjectDocument.model_validate(raw)


def loadAndStart(channel, root, project):
    (root / "project.json").write_text(project.model_dump_json(), encoding="utf-8")
    runtime = channel.runtime
    loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(root)), None)
    assert loaded.ok, loaded.message
    return start(channel, project)


def start(channel, project):
    request = pb.StartJobRequest(project_id=project.project.projectId, capture_presentation=True,
        start_request_id=uuid4().hex, expected_runtime_instance_id=channel.runtime.runtimeInstanceId)
    reply = channel.runtime.StartJob(request, None)
    assert reply.ok, reply.message
    return reply.job_id


def results(channel, job, count=1):
    status = waitForTerminal(channel.runtime, job)
    assert status.status == "COMPLETED", jobFailureDetails(channel.runtime, job, status)
    return waitFor(lambda: value if len(value := channel.store.snapshot(job)["results"]) == count else None)


def testTwoDistinctImagesUseOneSlabAndOneEncodingPerSource(channel, tmp_path):
    project = twoImages(tmp_path)
    job = loadAndStart(channel, tmp_path, project)
    result = results(channel, job)[0]
    assert result.status == "COMPLETE", jobFailureDetails(channel.runtime, job, result=result)
    sources = {source.sourceId: source for source in result.sources}
    assert sources["count"].valueJson == "2"
    original, overlay = sources["original"].image, sources["image"].image
    assert original.resourceId != overlay.resourceId
    assert original.ownerResultKey == overlay.ownerResultKey == result.identity.resultKey
    assert sources["overlay-alias"].image == overlay
    arrays = [cv2.imdecode(np.frombuffer(channel.assets.read(job, image.resourceId)[0], np.uint8),
                          cv2.IMREAD_UNCHANGED) for image in (original, overlay)]
    assert arrays[0].shape == arrays[1].shape == (120, 160, 3)
    assert not np.array_equal(*arrays)
    assert channel.exporter.stats["completed"] == 2
    slots = channel.jobs[job]["slots"]
    assert len(slots) == 2 and len({slot["index"] for slot in slots}) == 1
    assert sum(slot["capacity"] for slot in slots) == RAW_LIMIT
    metadata = DisplayRpc(channel).ListJobs(pb.DisplayEmpty(project_id=project.project.projectId), None).jobs[0]
    limits = json.loads(metadata.capture_limits_json)
    assert limits["rawBytesBySource"] == {"image": 4 * MIB, "original": 4 * MIB, "overlay-alias": 4 * MIB}
    assert limits["imageLaneBySource"]["image"] == limits["imageLaneBySource"]["overlay-alias"]
    assert limits["implicitResize"] is False
    assert channel.resourceStats()["total_reserved"] <= 256 * MIB


def testTwoCallSitesHaveSeparateImagesIdentitiesAndWatermarks(channel, tmp_path):
    project = twoCalls(tmp_path)
    job = loadAndStart(channel, tmp_path, project)
    values = results(channel, job, 2)
    assert {result.identity.resultScopeId for result in values} == {"a", "b"}
    assert len({result.identity.invocationId for result in values}) == 2
    assert len({result.identity.resultKey for result in values}) == 2
    assert channel.store.snapshot(job)["high"] == {"a": 1, "b": 1}
    assert all(result.status == "COMPLETE" for result in values)
    for result in values:
        scope = result.identity.resultScopeId
        assert set(result.expectedSourceIds) == {f"{scope}-image", f"{scope}-count"}
        assert all(source.sourceId.startswith(scope + "-") for source in result.sources)
        assert next(source.image for source in result.sources if source.image).ownerResultKey == result.identity.resultKey
    assert channel.exporter.stats["completed"] == 2


def testTwoExplicitDualImageJobsStayWithinRuntimeBudget(channel, tmp_path):
    project = twoImages(tmp_path, alias=False)
    first = loadAndStart(channel, tmp_path, project)
    second = start(channel, project)
    assert first != second
    for job in (first, second):
        result = results(channel, job)[0]
        assert result.status == "COMPLETE", jobFailureDetails(channel.runtime, job, result=result)
    assert len(channel.exporter.slots) == 2
    assert channel.resourceStats()["shared_capacity"] == 16 * MIB
    assert channel.resourceStats()["total_reserved"] == 256 * MIB
    assert {slot["index"] for slot in channel.jobs[first]["slots"]}.isdisjoint(
        {slot["index"] for slot in channel.jobs[second]["slots"]})
    assert len(channel.runtime.jobRepository.all()) == 2
    release(channel, first)
    release(channel, second)


def testClientDecodesTwoImagesOnceAndSharesAliases(channel, tmp_path):
    project = twoImages(tmp_path)
    job = loadAndStart(channel, tmp_path, project)
    results(channel, job)
    server = AioRuntimeServer(channel.runtime, channel)
    session = DisplaySession(f"127.0.0.1:{server.port}", job)
    try:
        view = waitFor(lambda: state if (state := session.readSnapshot()).scopes
                       and len(state.scopes["root"].images) == 3 else None)
        scope = view.scopes["root"]
        assert session.stats["decoded"] == 2
        assert scope.images["image"] is scope.images["overlay-alias"]
        assert not scope.images["image"].flags.writeable
        pin = session.pins().acquire(scope, view.generation, 2000)
        waitFor(lambda: session.pins().read(pin).state == "PINNED")
        assert channel.assets.stats()["lease_handles"] == 2
        session.pins().release(pin)
        waitFor(lambda: channel.assets.stats()["lease_handles"] == 0)
    finally:
        session.close()
        server.close()
    assert len(channel.runtime.jobRepository.all()) == 1


def testRawLaneExactBoundaryOversizeAndConcurrentIterationCannotReuseCredit():
    memory = SharedMemory(create=True, size=RAW_LIMIT)
    quarantine = SimpleNamespace(value=False)
    slots = [{"index": 0, "lane": lane, "offset": lane * 4 * MIB, "capacity": 4 * MIB,
              "name": memory.name, "free": threading.BoundedSemaphore(1), "quarantined": quarantine}
             for lane in range(2)]
    events = []
    collector = ResultCollector({"plan": '{"sources":{},"scopes":{}}', "slots": slots,
        "imageLaneBySource": {"left": 0, "right": 1}}, events.append)
    def item(key):
        return {"identity": {"resultKey": key, "jobId": "job"}, "rawBytes": 0}
    try:
        value = np.full((2048, 2048), 7, np.uint8)  # exactly4MiB, synthetic boundary only
        assert collector.image("right", value, item("one"))["pendingImage"]
        assert events[0]["descriptor"]["offset"] == 4 * MIB
        assert memory.buf[4 * MIB] == memory.buf[-1] == 7
        assert memory.buf[0] == 0
        concurrent = collector.image("right", value, item("different-iteration"))
        assert concurrent["reasonCode"] == "BUDGET_EXCEEDED" and "busy" in concurrent["reason"]
        assert collector.image("left", value, item("one"))["pendingImage"]
        tooLarge = collector.image("left", np.zeros((1, 4 * MIB + 1), np.uint8), item("oversize"))
        assert tooLarge["reasonCode"] == "BUDGET_EXCEEDED" and "4194304" in tooLarge["reason"]
        assert len(events) == 2
    finally:
        memory.close()
        memory.unlink()


def testProfileRejectsThirdImageAndExplainsTwo1080pBgrLimit(tmp_path):
    project = twoImages(tmp_path)
    limits = normalCaptureLimits(project.presentation)
    assert limits["sourceCount"] == 4 and limits["scopeCount"] == 1
    assert 1920 * 1080 * 3 > normalCaptureProfile()["rawBytesPerSourceDual"]
    raw = project.model_dump()
    raw["presentation"]["dataSources"]["third"] = dict(raw["presentation"]["dataSources"]["image"], port="mask")
    raw["presentation"]["pages"]["second"]["components"].append({"componentId": "third", "type": "image",
        "bindings": {"image": "third"}, "layout": {"row": 2}})
    with pytest.raises(ValueError, match="two distinct image"):
        normalCaptureLimits(ProjectDocument.model_validate(raw).presentation)


def testQueuedLaneDeadlineIncludesWaitingAndCreditIsUnique(tmp_path):
    outcomes = []
    pool = ExportPool(tmp_path, lambda task, path, outcome: outcomes.append((task["lane"], outcome)), worker=blockedEncoder)
    try:
        slots = pool.configureLanes(0, 2)
        for slot in slots:
            assert slot["free"].acquire(False)
            pool.submit({"slot": 0, "lane": slot["lane"], "offset": slot["offset"], "shape": [1, 1], "key": str(slot["lane"])})
        with pytest.raises(ValueError, match="duplicate"):
            pool.submit({"slot": 0, "lane": 0, "shape": [1, 1], "key": "duplicate"})
        waitFor(lambda: len(outcomes) == 2)
        assert outcomes == [(0, "EXPORT_TIMEOUT"), (1, "EXPORT_TIMEOUT")]
        waitFor(lambda: not pool.slots[0]["busy"])
        assert not pool.slots[0]["pending"]
    finally:
        pool.close()


def testExporterCannotAcceptDescriptorWithoutLaneReservation(tmp_path):
    pool = ExportPool(tmp_path, lambda *args: None)
    try:
        pool.configureLanes(0, 2)
        with pytest.raises(ValueError, match="no reserved lane"):
            pool.submit({"slot": 0, "lane": 1, "shape": [1, 1], "key": "unowned"})
        assert not pool.slots[0]["busy"] and not pool.slots[0]["pending"]
    finally:
        pool.close()


def testNewFailureInOneScopeNeverBorrowsOtherScopeOrOlderCompleteValue(tmp_path):
    project = twoCalls(tmp_path)
    sources = {key: value.model_dump() for key, value in project.presentation.dataSources.items()
               if value.expectedType == "integer"}
    scopes = {key: value.model_dump() for key, value in project.presentation.resultScopes.items()}
    store = ResultStore()
    def emit(event):
        if event["eventType"] == "display.open":
            store.begin(event["item"])
        elif event["eventType"] == "display.seal":
            store.close(event["key"], event["sources"], event["terminal"])
    config = {"plan": json.dumps({"sources": sources, "scopes": scopes}),
              "credits": threading.BoundedSemaphore(8), "scopeIds": ["a", "b"], "ordinals": [0] * 16,
              "identity": {"runtimeInstanceId": "runtime", "jobId": "job", "executionRevision": "a" * 64,
                           "capturePlanRevision": "b" * 64, "mode": "runtime"}}
    collector = ResultCollector(config, emit)
    root = RunContext.root("job", "main")
    a = root.childWorkflow("child", "a")
    b = root.childWorkflow("child", "b")
    for context, value in ((a, 1), (b, 10)):
        collector.begin(context)
        collector.output(context.forNode("count"), {"count": value})
        collector.end(context, "COMPLETED")
    delayed = replace(a, workflowRunId=str(uuid4()), iterationPath=(1,))
    collector.begin(delayed)
    collector.output(delayed.forNode("count"), {"count": 999})
    failed = replace(a, workflowRunId=str(uuid4()), iterationPath=(2,))
    collector.begin(failed)
    collector.event("node.failed", failed.forNode("count"))
    collector.end(failed, "FAILED")
    collector.end(delayed, "COMPLETED")
    snapshot = store.snapshot("job")
    latest = {value.identity.resultScopeId: value for value in snapshot["results"]}
    assert snapshot["high"] == {"a": 3, "b": 1}
    assert latest["a"].status == "FAILED" and latest["a"].identity.resultOrdinal == 3
    assert latest["a"].sources[0].sourceId == "a-count"
    assert latest["a"].sources[0].valueJson is None
    assert latest["b"].status == "COMPLETE" and latest["b"].sources[0].valueJson == "10"


def testMissingImageInSecondScopeDoesNotUseFirstScopeImage(channel, tmp_path):
    raw = twoCalls(tmp_path).model_dump()
    raw["workflows"]["other"] = deepcopy(raw["workflows"]["child"])
    raw["workflows"]["other"]["nodes"][2]["params"]["drawOverlay"] = False
    raw["workflows"]["main"]["nodes"][2]["targetWorkflowId"] = "other"
    raw["workflowOrder"].append("other")
    binding = deepcopy(raw["resources"]["parameterBindings"][0])
    binding["target"]["workflowId"] = "other"
    raw["resources"]["parameterBindings"].append(binding)
    raw["presentation"]["resultScopes"]["b"]["scopeWorkflowId"] = "other"
    for source in raw["presentation"]["dataSources"].values():
        if source["resultScopeId"] == "b":
            source["workflowId"] = "other"
    project = ProjectDocument.model_validate(raw)
    job = loadAndStart(channel, tmp_path, project)
    values = {value.identity.resultScopeId: value for value in results(channel, job, 2)}
    assert values["a"].status == "COMPLETE"
    assert values["b"].status == "INCOMPLETE"
    bImage = next(source for source in values["b"].sources if source.sourceId == "b-image")
    assert bImage.image is None and bImage.reasonCode == "OPTIONAL_ABSENT"
    assert len({value.identity.resultKey for value in values.values()}) == 2


def quarantineFinished(slot):
    # The flag is published before credits are drained under this same lock.
    # Keep polling bounded; never block the test while the owner is inside it.
    if not slot["lock"].acquire(False):
        return False
    try:
        return slot["quarantined"].value
    finally:
        slot["lock"].release()


def testQuarantineWaitCannotObserveFlagBeforeBothLaneCreditsAreDrained():
    entered, resume = threading.Event(), threading.Event()
    credits = [threading.BoundedSemaphore(1) for _ in range(2)]
    errors = []

    def gatedAcquire(blocking):
        acquired = credits[0].acquire(blocking)
        if not acquired:
            entered.set()
            assert resume.wait(5), "test did not release the quarantine drain"
        return acquired

    slot = {"lock": threading.RLock(), "quarantined": SimpleNamespace(value=False), "busy": False,
            "laneFree": [SimpleNamespace(acquire=gatedAcquire), credits[1]]}
    pool = ExportPool.__new__(ExportPool)

    def quarantine():
        try:
            pool._quarantine(slot)
        except BaseException as error:
            errors.append(error)

    owner = threading.Thread(target=quarantine)
    owner.start()
    try:
        assert entered.wait(5)
        assert slot["quarantined"].value and slot["busy"]
        assert not quarantineFinished(slot)
        emissions = []
        collector = ResultCollector({"plan": '{"sources":{},"scopes":{}}',
            "slots": [{"lane": 1, "free": credits[1], "quarantined": slot["quarantined"]}],
            "imageLaneBySource": {"image": 1}}, emissions.append)
        rejected = collector.image("image", np.zeros((1, 1), np.uint8), {"rawBytes": 0})
        assert rejected["reasonCode"] == "BUDGET_EXCEEDED" and "quarantined" in rejected["reason"]
        assert not emissions
        # The old flag-only wait would return while this lane still had credit.
        assert not credits[0].acquire(False)
        assert credits[1].acquire(False)
        credits[1].release()
        resume.set()
        waitFor(lambda: quarantineFinished(slot))
        assert slot["busy"] and slot["quarantined"].value
        assert all(not credit.acquire(False) for credit in credits)
    finally:
        resume.set()
        owner.join(5)
    assert not owner.is_alive()
    assert not errors


def testFailedEncoderReapQuarantinesBothLanesUntilActualDisposal(tmp_path, monkeypatch):
    outcomes = []
    pool = ExportPool(tmp_path, lambda task, path, outcome: outcomes.append(outcome), worker=blockedEncoder)
    originalReap = pool._reap
    def cannotReap(slot):
        if slot["index"] == 0:
            raise RuntimeError("injected unreaped encoder")
        return originalReap(slot)
    try:
        descriptors = pool.configureLanes(0, 2)
        monkeypatch.setattr(pool, "_reap", cannotReap)
        assert descriptors[0]["free"].acquire(False)
        pool.submit({"slot": 0, "lane": 0, "shape": [1, 1], "key": "quarantine"})
        waitFor(lambda: quarantineFinished(pool.slots[0]))
        assert pool.slots[0]["busy"] and pool.slots[0]["process"].is_alive()
        assert all(not descriptor["free"].acquire(False) for descriptor in descriptors)
        with pytest.raises(ValueError, match="quarantined"):
            pool.configureLanes(0, 1)
        with pytest.raises(ValueError, match="quarantined"):
            pool.releaseSlot(0)
        with pytest.raises(RuntimeError, match="unreaped"):
            pool.close()
        assert pool.slots[0]["process"].is_alive()
        assert descriptors[0]["quarantined"].value
    finally:
        monkeypatch.setattr(pool, "_reap", originalReap)
        pool.close()
    assert all(slot["process"] is None for slot in pool.slots)


def scalarItem(scope, ordinal):
    return {"identity": {"runtimeInstanceId": "runtime", "jobId": "job", "resultScopeId": scope,
        "invocationId": f"{scope}-{ordinal}", "resultKey": f"{scope}-{ordinal}", "resultOrdinal": ordinal,
        "executionRevision": "a" * 64, "capturePlanRevision": "b" * 64, "mode": "runtime"}, "expected": [scope]}


def closeScalar(store, scope, ordinal, value):
    item = scalarItem(scope, ordinal)
    store.begin(item)
    store.close(item["identity"]["resultKey"], [{"sourceId": scope, "state": "AVAILABLE", "valueJson": json.dumps(value)}], "COMPLETED")


def testQuietScopeSurvivesAsymmetricTrafficHistoryEvictionAndFreshSnapshot():
    store = ResultStore()
    closeScalar(store, "quiet", 1, 7)
    for ordinal in range(1, 81):
        closeScalar(store, "fast", ordinal, ordinal)
    assert len(store.history) == 32
    assert all(result.identity.resultScopeId == "fast" for result in store.history)
    reconnect = store.snapshot("job", cursor=0, incremental=True)
    assert {result.identity.resultScopeId: result.sources[0].valueJson for result in reconnect["results"]} == {
        "quiet": "7", "fast": "80"}
    assert reconnect["high"] == {"quiet": 1, "fast": 80}
    assert not reconnect["expired"]
    assert store.metadataBytes() <= store.metadataLimit


def testLatestAndReplayShareExistingByteCapAndPressureExpiryIsExplicit():
    store = ResultStore()
    closeScalar(store, "quiet", 1, "q" * 128)
    oneSize = store.metadataBytes()
    store.metadataLimit = oneSize + 16  # Test-only small cap that fits one payload, not two.
    closeScalar(store, "fast", 1, "f" * 128)
    snapshot = store.snapshot("job")
    assert snapshot["expired"] == {"quiet": 1}
    assert {result.identity.resultScopeId for result in snapshot["results"]} == {"fast"}
    assert store.metadataBytes() <= store.metadataLimit
    assert not store.history and not store.events  # Optional replay evicted ahead of latest.
    closeScalar(store, "quiet", 2, 9)
    assert "quiet" not in store.snapshot("job")["expired"]
    assert store.latest[("job", "quiet")].identity.resultOrdinal == 2
    assert store.metadataBytes() <= store.metadataLimit


def testQuietImageRemainsOwnedAfterOtherScopeHistoryEviction(channel, tmp_path):
    project = twoCalls(tmp_path)
    job = loadAndStart(channel, tmp_path, project)
    original = {value.identity.resultScopeId: value for value in results(channel, job, 2)}
    quietImage = next(source.image for source in original["a"].sources if source.image is not None)
    for ordinal in range(2, 45):
        item = scalarItem("b", ordinal)
        item["identity"].update(runtimeInstanceId=channel.runtimeInstanceId, jobId=job)
        item["expected"] = ["b-count"]
        channel.store.begin(item)
        channel.store.close(item["identity"]["resultKey"], [{"sourceId": "b-count", "state": "AVAILABLE", "valueJson": str(ordinal)}], "COMPLETED")
    with channel.store.lock:
        channel._retain()
    assert all(value.identity.resultScopeId == "b" for value in channel.store.history)
    assert channel.assets.read(job, quietImage.resourceId)[0]
    server = AioRuntimeServer(channel.runtime, channel)
    observer = DisplaySession(f"127.0.0.1:{server.port}", job)
    try:
        view = waitFor(lambda: value if (value := observer.readSnapshot()).scopes.get("a")
                       and value.scopes["a"].images else None)
        assert view.scopes["a"].result.identity.resultKey == original["a"].identity.resultKey
        assert not view.expiredScopes
    finally:
        observer.close()
        server.close()


def testClientScopeExpiryFencesPreviouslyQueuedDecode(channel, tmp_path):
    project = twoCalls(tmp_path)
    job = loadAndStart(channel, tmp_path, project)
    values = results(channel, job, 2)
    server = AioRuntimeServer(channel.runtime, channel)
    observer = DisplaySession(f"127.0.0.1:{server.port}", job)
    try:
        waitFor(lambda: len(observer.readSnapshot().scopes) == 2)
        before = observer.readSnapshot()
        key = before.scopes["a"].result
        with channel.store.lock:
            channel.store.latest.pop((job, "a"))
            channel.store.expired[(job, "a")] = 1
            channel.store._notify()
        waitFor(lambda: observer.readSnapshot().expiredScopes.get("a") == 1)
        assert "a" not in observer.readSnapshot().scopes
        assert "b" in observer.readSnapshot().scopes
        # A decoder task accepted before expiry must not resurrect quiet values.
        observer.pending.put_nowait((observer.generation, key, 0, 0))
        waitFor(lambda: any(record["key"] == key.identity.resultKey and not record["applied_to_live"]
                           for record in observer.records))
        assert "a" not in observer.readSnapshot().scopes
        assert len(values) == 2
    finally:
        observer.close()
        server.close()


def testLegacyReaderIgnoringExpiryUsesResetToFenceDelayedDecode(channel, tmp_path, monkeypatch):
    entered, resume = threading.Event(), threading.Event()
    class LegacyReader(DisplaySession):
        def _accept(self, snapshot):
            legacy = pb.DisplaySnapshot()
            legacy.CopyFrom(snapshot)
            legacy.ClearField("expired_scope_ordinals")
            super()._accept(legacy)

        def _readImage(self, result, image, **kwargs):
            pixels = super()._readImage(result, image, **kwargs)
            if result.identity.resultScopeId == "a" and not entered.is_set():
                # Gate an actual first asset decode before model publication.
                # Requeueing an already cached result no longer implies decoding.
                entered.set()
                assert resume.wait(5)
            return pixels
    project = twoCalls(tmp_path)
    job = loadAndStart(channel, tmp_path, project)
    values = results(channel, job, 2)
    quiet = next(result for result in values if result.identity.resultScopeId == "a")
    server = AioRuntimeServer(channel.runtime, channel)
    observer = LegacyReader(f"127.0.0.1:{server.port}", job)
    try:
        assert entered.wait(3)
        view = observer.readSnapshot()
        assert observer.stats["decoded"] >= 1
        with channel.store.lock:
            channel.store.metadataLimit = max(len(value.model_dump_json().encode())
                for value in channel.store.latest.values()) + 16
            item = scalarItem("b", 2)
            item["identity"].update(runtimeInstanceId=channel.runtimeInstanceId, jobId=job)
            item["expected"] = ["b-count"]
            channel.store.begin(item)
            channel.store.close(item["identity"]["resultKey"], [{"sourceId": "b-count", "state": "AVAILABLE", "valueJson": "2"}], "COMPLETED")
            assert channel.store.snapshot(job, cursor=observer.cursor)["reset"]
        waitFor(lambda: observer.readSnapshot().generation > view.generation)
        assert "a" not in observer.readSnapshot().scopes
        assert not observer.readSnapshot().expiredScopes  # Legacy reader never used the additive field.
        resume.set()
        waitFor(lambda: any(record["key"] == quiet.identity.resultKey and not record["applied_to_live"]
                           for record in observer.records))
        record = next(record for record in observer.records if record["key"] == quiet.identity.resultKey)
        assert record["decoded"] == ["a-image"] and not record["failures"]
        assert record["applied_to_live"] is False
        assert "a" not in observer.readSnapshot().scopes
    finally:
        resume.set()
        observer.close()
        server.close()


def testLateResetFromSameRuntimeCannotReplaceNewerScopeState(channel, tmp_path):
    project = twoCalls(tmp_path)
    job = loadAndStart(channel, tmp_path, project)
    results(channel, job, 2)
    server = AioRuntimeServer(channel.runtime, channel)
    observer = DisplaySession(f"127.0.0.1:{server.port}", job)
    try:
        waitFor(lambda: len(observer.readSnapshot().scopes) == 2)
        before = observer.readSnapshot()
        observer._accept(pb.DisplaySnapshot(runtime_instance_id=before.runtimeInstanceId,
            job_id=job, cursor=0, reset_required=True, expired_scope_ordinals={"a": 1}))
        after = observer.readSnapshot()
        assert after.generation == before.generation
        assert {scope: value.result.identity.resultKey for scope, value in after.scopes.items()} == {
            scope: value.result.identity.resultKey for scope, value in before.scopes.items()}
        assert not after.expiredScopes
    finally:
        observer.close()
        server.close()


def testDelayedOlderClosureCannotReplayExpiredScopeAfterLegacyReset():
    store = ResultStore()
    old = scalarItem("quiet", 1)
    assert store.begin(old)
    closeScalar(store, "quiet", 2, "q" * 128)
    readerCursor = store.cursor
    store.metadataLimit = store.metadataBytes() + 16
    closeScalar(store, "fast", 1, "f" * 128)
    reset = store.snapshot("job", readerCursor, incremental=True)
    assert reset["reset"] and reset["expired"] == {"quiet": 2}
    assert all(result.identity.resultScopeId != "quiet" for result in reset["results"])
    resetCursor = reset["cursor"]
    # Relieve test-only pressure so the late payload is retained in replay;
    # this specifically tests filtering, rather than another incidental reset.
    store.metadataLimit = 8 * MIB
    # This invocation opened before the higher ordinal closed, so its eventual
    # closure is legitimate history, but cannot revive an expired latest value.
    assert store.close(old["identity"]["resultKey"], [{"sourceId": "quiet", "state": "AVAILABLE", "valueJson": "1"}], "COMPLETED")
    replay = store.snapshot("job", resetCursor, incremental=True)
    assert not replay["reset"]
    assert all(result.identity.resultScopeId != "quiet" for result in replay["results"])


def testQuietLatestOutsideHistoryStillRejectsDuplicateAndAlreadyClosedBegins():
    store = ResultStore()
    closeScalar(store, "quiet", 1, 7)
    for ordinal in range(1, 50):
        closeScalar(store, "fast", ordinal, ordinal)
    assert all(result.identity.resultScopeId == "fast" for result in store.history)
    duplicate = scalarItem("quiet", 1)
    assert store.begin(duplicate) is False
    assert duplicate["identity"]["resultKey"] not in store.open
    duplicate["identity"]["resultKey"] = "different-key-for-same-ordinal"
    assert store.begin(duplicate) is False
    assert store.latest[("job", "quiet")].sources[0].valueJson == "7"
