"""Slow optional image inspection must not hold workflow progress."""
import json
import threading
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.events.models import RuntimeEvent
from emo_master.apps.runtime.preview.async_writer import AsyncPreviewSnapshotWriter
from emo_master.apps.runtime.workflow.context import RunContext
from tests.runtime.test_run_inspection_store import document, owner  # noqa: F401


def testInspectionEncodingRunsOffProducerAndFreezesPixels(tmp_path, monkeypatch):
    events, failures = [], []
    writer = AsyncPreviewSnapshotWriter(tmp_path, jobId="job", projectRevision=7,
        inspectionPublisher=lambda **event: events.append(event), errorPublisher=lambda **event: failures.append(event))
    entered, release, submitted = (threading.Event() for _ in range(3))
    encode = writer._writer._captureInspection
    encoderThreads = []

    def slow(*args, **kwargs):
        encoderThreads.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return encode(*args, **kwargs)

    monkeypatch.setattr(writer._writer, "_captureInspection", slow)
    node = SimpleNamespace(nodeId="source", outputPorts={"image": "image"})
    pixels = np.full((16, 24, 3), 37, np.uint8)
    metadata = []

    def produce():
        metadata.append(writer.capture(node, {"image": pixels}, RunContext.root("job", "main").forNode("source")))
        submitted.set()

    producer = threading.Thread(target=produce)
    producer.start()
    try:
        assert entered.wait(1)
        assert submitted.wait(.2), "the producer waited for preview PNG encoding"
        pixels[:] = 99
    finally:
        release.set()
        producer.join(3)
        writer.close()
    assert encoderThreads == [writer._thread.ident] and encoderThreads != [producer.ident]
    assert metadata[0]["state"] == "PREPARING"
    entry = next(event["payload"]["entries"][0] for event in events if event["payload"].get("entries"))
    legacy = next(iter(writer._writer._entries.values()))
    assert entry["createdAtMs"] == legacy["createdAtMs"]
    preview = cv2.imdecode(np.fromfile(writer._writer.inspectionRoot / entry["relativePath"], np.uint8), -1)
    assert np.all(preview == 37)
    assert not failures


def testCompletedExecutionWaitsForAsyncImageAndKeepsIdentity(owner, tmp_path, monkeypatch):  # noqa: F811
    store, session = owner
    store.attach(session, "project", "a", document())
    context = RunContext.root("a", "main").forNode("load")
    node = document().workflows["main"].nodes[0]
    entered, release = threading.Event(), threading.Event()
    failures = []
    writer = AsyncPreviewSnapshotWriter(tmp_path / "a", jobId="a", projectRevision=7,
        inspectionPublisher=lambda **event: store.publish("a", event, tmp_path / "a"),
        errorPublisher=lambda **event: failures.append(event))
    encode = writer._writer._captureInspection

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return encode(*args, **kwargs)

    monkeypatch.setattr(writer._writer, "_captureInspection", slow)
    store.observe(RuntimeEvent("a", "node.started", "", sequence=1, workflowId="main", nodeId="load",
        workflowRunId=context.workflowRunId, nodeRunId=context.nodeRunId))
    try:
        capture = writer.capture(node, {"image": np.full((8, 9, 3), 55, np.uint8)}, context)
        assert entered.wait(1)
        store.observe(RuntimeEvent("a", "node.completed", "", sequence=2, workflowId="main", nodeId="load",
            workflowRunId=context.workflowRunId, nodeRunId=context.nodeRunId,
            payloadJson=json.dumps({"previewCapture": capture})))
        assert store.list(session, "project", "a", "main", "load")[1] == "PREPARING"
    finally:
        release.set()
        writer.close()
    store.terminal("a", tmp_path / "a", "COMPLETED")
    sources, state, _ = store.list(session, "project", "a", "main", "load")
    assert state == "CURRENT_AVAILABLE" and len(sources) == 1
    assert sources[0].nodeRunId == context.nodeRunId and sources[0].captureId == capture["captureId"]
    data = b"".join(chunk for chunk, _ in store.stream(session, "project", sources[0].assetId))
    assert np.all(cv2.imdecode(np.frombuffer(data, np.uint8), -1) == 55)
    assert not failures


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "ABORTED"])
def testTerminalDoesNotLeavePendingImageForever(owner, tmp_path, status):  # noqa: F811
    store, session = owner
    store.attach(session, "project", "a", document())
    for sequence, kind in [(1, "node.started"), (2, "node.completed")]:
        store.observe(RuntimeEvent("a", kind, "", sequence=sequence, workflowId="main", nodeId="load",
            workflowRunId="flow", nodeRunId="node", payloadJson=json.dumps({"previewCapture": {
                "captureId": "pending", "state": "PREPARING"}})))
    assert store.list(session, "project", "a", "main", "load")[1] == "PREPARING"
    store.terminal("a", tmp_path / "a", status)
    sources, state, reason = store.list(session, "project", "a", "main", "load")
    assert not sources and state == "NO_CURRENT_SNAPSHOT" and "INSPECTION" in reason


@pytest.mark.parametrize("failEncoding", [False, True])
def testEncoderCompletionBeforeNodeCompletionDoesNotRegressToPreparing(owner, tmp_path, monkeypatch,  # noqa: F811
                                                                     failEncoding):
    store, session = owner
    store.attach(session, "project", "a", document())
    context = RunContext.root("a", "main").forNode("load")
    store.observe(RuntimeEvent("a", "node.started", "", sequence=1, workflowId="main", nodeId="load",
        workflowRunId=context.workflowRunId, nodeRunId=context.nodeRunId))
    errors = []
    writer = AsyncPreviewSnapshotWriter(tmp_path / "a", jobId="a", projectRevision=7,
        inspectionPublisher=lambda **event: store.publish("a", event, tmp_path / "a"),
        errorPublisher=lambda **event: errors.append(event))
    if failEncoding:
        def fail(*_args, **_kwargs):
            raise ValueError("PNG failed")
        monkeypatch.setattr(writer._writer, "_captureInspection", fail)
    try:
        capture = writer.capture(document().workflows["main"].nodes[0],
            {"image": np.ones((8, 9, 3), np.uint8)}, context)
    finally:
        writer.close()
    store.observe(RuntimeEvent("a", "node.completed", "", sequence=2, workflowId="main", nodeId="load",
        workflowRunId=context.workflowRunId, nodeRunId=context.nodeRunId,
        payloadJson=json.dumps({"previewCapture": capture})))
    sources, state, reason = store.list(session, "project", "a", "main", "load")
    assert state == ("NO_CURRENT_SNAPSHOT" if failEncoding else "CURRENT_AVAILABLE")
    if failEncoding:
        assert not sources and "PNG failed" in reason
        assert len(errors) == 1 and errors[0]["code"] == "E_PREVIEW_SNAPSHOT_FAILED"
        assert writer.statistics()["inspectionFailed"] == 1
    else:
        assert sources[0].captureId == capture["captureId"]
        assert not errors and writer.statistics()["inspectionFailed"] == 0
    assert writer.statistics()["written"] == 1  # Inspection failure does not cancel legacy persistence.


def testSupersededPreviewIsExplicitWhileNewestRemainsPending(owner, tmp_path, monkeypatch):  # noqa: F811
    store, session = owner
    store.attach(session, "project", "a", document())
    entered, release = threading.Event(), threading.Event()
    writer = AsyncPreviewSnapshotWriter(tmp_path / "a", jobId="a", projectRevision=7,
        inspectionPublisher=lambda **event: store.publish("a", event, tmp_path / "a"),
        errorPublisher=lambda **event: pytest.fail(str(event)))
    persist = writer._writer.captureLegacy

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return persist(*args, **kwargs)

    monkeypatch.setattr(writer._writer, "captureLegacy", held)
    contexts = []
    try:
        writer.capture(SimpleNamespace(nodeId="blocker", outputPorts={"count": "integer"}), {"count": 1},
                       RunContext.root("a", "main").forNode("blocker"))
        assert entered.wait(1)
        for index in range(2):
            context = RunContext.root("a", "main").forNode("load")
            contexts.append(context)
            store.observe(RuntimeEvent("a", "node.started", "", sequence=index * 2 + 1,
                workflowId="main", nodeId="load", workflowRunId=context.workflowRunId, nodeRunId=context.nodeRunId))
            capture = writer.capture(document().workflows["main"].nodes[0],
                {"image": np.full((8, 9, 3), index, np.uint8)}, context)
            store.observe(RuntimeEvent("a", "node.completed", "", sequence=index * 2 + 2,
                workflowId="main", nodeId="load", workflowRunId=context.workflowRunId, nodeRunId=context.nodeRunId,
                payloadJson=json.dumps({"previewCapture": capture})))
        sources, state, reason = store.list(session, "project", "a", "main", "load",
            contexts[0].workflowRunId, contexts[0].nodeRunId)
        assert not sources and state == "NO_CURRENT_SNAPSHOT" and "SKIPPED" in reason
        assert store.list(session, "project", "a", "main", "load")[1] == "PREPARING"
    finally:
        release.set()
        writer.close()
    assert store.list(session, "project", "a", "main", "load")[0][0].nodeRunId == contexts[-1].nodeRunId
    assert not list((tmp_path / "a" / "inspection_staging").glob("*.png"))


def testRealWorkflowReachesDownstreamBeforeInspectionEncodingFinishes(tmp_path, monkeypatch):
    from emo_master.apps.runtime.workflow.cancellation import CancellationToken
    from emo_master.apps.runtime.workflow.runner import WorkflowRunner
    from emo_master.core.workflow.compiler import WorkflowCompiler
    from emo_master.plugins.builtins.image_saver.operator import ImageSaverOperator
    from tests.runtime.inspection_fixtures import loopProject
    from tests.runtime.inspection_test_operator import InspectionTestOperator

    document = loopProject(tmp_path / "project")
    registry = {operator.meta.operatorId: operator.meta for operator in (InspectionTestOperator, ImageSaverOperator)}
    compiled = WorkflowCompiler(operatorRegistry=registry).compile(document)
    operators = {operator.meta.operatorId: operator for operator in (InspectionTestOperator, ImageSaverOperator)}
    entered, release, downstream, finished = (threading.Event() for _ in range(4))
    events, failures = [], []
    writer = AsyncPreviewSnapshotWriter(tmp_path / "job", inspectionPublisher=lambda **_event: None,
        errorPublisher=lambda **event: failures.append(event))
    encode = writer._writer._captureInspection

    def slow(node, *args, **kwargs):
        if node.nodeId == "image":
            entered.set()
            assert release.wait(5)
        return encode(node, *args, **kwargs)

    monkeypatch.setattr(writer._writer, "_captureInspection", slow)

    def publish(**event):
        events.append(event)
        if event["eventType"] == "node.completed" and event["context"].callerNodeId == "save":
            downstream.set()

    def run():
        try:
            runner = WorkflowRunner(compiled, operators, previewSnapshotStore=writer, eventPublisher=publish)
            result = runner.run("body", {"index": 5}, RunContext.root("job", "body"), CancellationToken())
            assert result.outputs == {"count": 5}
        except Exception as error:
            failures.append(error)
        finally:
            finished.set()

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(2), failures
        assert downstream.wait(1) and finished.wait(1)
        assert not release.is_set()
        event = next(event for event in events if event["eventType"] == "node.completed"
                     and event["context"].callerNodeId == "image")
        assert event["payload"]["previewCapture"]["state"] == "PREPARING"
    finally:
        release.set()
        worker.join(3)
        writer.close()
    assert not failures


def testActiveEncoderBudgetSkipsPreviewWithoutBlockingOrChangingOutput(owner, tmp_path, monkeypatch):  # noqa: F811
    store, session = owner
    store.attach(session, "project", "a", document())
    entered, release = threading.Event(), threading.Event()
    writer = AsyncPreviewSnapshotWriter(tmp_path / "a", jobId="a", projectRevision=7, maxBytes=8,
        inspectionPublisher=lambda **event: store.publish("a", event, tmp_path / "a"),
        errorPublisher=lambda **event: pytest.fail(str(event)))
    persist = writer._writer.captureLegacy

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return persist(*args, **kwargs)

    monkeypatch.setattr(writer._writer, "captureLegacy", held)
    context = RunContext.root("a", "main").forNode("load")
    pixels = np.full((2, 4), 19, np.uint8)
    try:
        writer.capture(SimpleNamespace(nodeId="blocker", outputPorts={"image": "image"}),
            {"image": np.zeros((2, 2), np.uint8)}, context)
        assert entered.wait(1)
        store.observe(RuntimeEvent("a", "node.started", "", sequence=1, workflowId="main", nodeId="load",
            workflowRunId=context.workflowRunId, nodeRunId=context.nodeRunId))
        capture = writer.capture(document().workflows["main"].nodes[0], {"image": pixels}, context)
        assert capture["state"] == "SKIPPED" and not release.is_set()
        store.observe(RuntimeEvent("a", "node.completed", "", sequence=2, workflowId="main", nodeId="load",
            workflowRunId=context.workflowRunId, nodeRunId=context.nodeRunId,
            payloadJson=json.dumps({"previewCapture": capture})))
        sources, state, reason = store.list(session, "project", "a", "main", "load")
        assert not sources and state == "NO_CURRENT_SNAPSHOT" and "SKIPPED" in reason
        assert np.all(pixels == 19) and writer.statistics()["peakBufferedBytes"] == 4
    finally:
        release.set()
        writer.close()


def testUnwatchedNodeCanRetireAnotherNodesQueuedPreview(owner):  # noqa: F811
    store, session = owner
    store.attach(session, "project", "a", document())
    for sequence, kind in [(1, "node.started"), (2, "node.completed")]:
        store.observe(RuntimeEvent("a", kind, "", sequence=sequence, workflowId="main", nodeId="load",
            workflowRunId="flow", nodeRunId="node", payloadJson=json.dumps({"previewCapture": {
                "captureId": "queued", "state": "PREPARING"}})))
    store.observe(RuntimeEvent("a", "node.completed", "", sequence=3, workflowId="other", nodeId="unwatched",
        workflowRunId="other-flow", nodeRunId="other-node", payloadJson=json.dumps({"previewCapture": {
            "captureId": "new", "state": "", "retired": [dict(workflowId="main", nodeId="load",
                workflowRunId="flow", nodeRunId="node", captureId="queued")]}})))
    assert "SKIPPED" in store.list(session, "project", "a", "main", "load")[2]
