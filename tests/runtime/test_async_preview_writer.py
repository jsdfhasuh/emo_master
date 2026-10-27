"""Optional previews coalesce whole invocations without changing business outputs."""
from dataclasses import replace
import hashlib
import json
import threading
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from emo_master.apps.runtime.preview.async_writer import AsyncPreviewSnapshotWriter
from emo_master.apps.runtime.preview.store import PreviewSnapshotWriter
from emo_master.apps.runtime.workflow.cancellation import CancellationRequested, CancellationToken
from emo_master.apps.runtime.workflow.context import RunContext


def _node(nodeId="source", **ports):
    return SimpleNamespace(nodeId=nodeId, outputPorts=ports or {"value": "integer"})


def _context(iteration=0, workflow="main"):
    return replace(RunContext.root("job", workflow).forNode("source"), iterationPath=(iteration,))


def _entries(writer):
    root = writer._writer.root
    return json.loads((root / "index.json").read_text(encoding="utf-8"))["entries"]


def _values(writer):
    return {(row["workflowId"], row["nodeId"], row["port"]):
            json.loads((writer._writer.root / row["relativePath"]).read_text(encoding="utf-8"))
            for row in _entries(writer) if row["mimeType"] == "application/json"}


@pytest.fixture
def heldWriter(tmp_path, monkeypatch):
    """Hold only the first legacy write, never the producer or inspection path."""
    writers, releases = [], []

    def create(**kwargs):
        errors = []
        kwargs.setdefault("errorPublisher", lambda **event: errors.append(event))
        writer = AsyncPreviewSnapshotWriter(tmp_path / str(len(writers)), **kwargs)
        writers.append(writer)
        entered, release = threading.Event(), threading.Event()
        releases.append(release)
        persist = writer._writer.captureLegacy

        def hold(*args, **params):
            if not entered.is_set():
                entered.set()
                assert release.wait(5), "test did not release the legacy writer"
            return persist(*args, **params)

        monkeypatch.setattr(writer._writer, "captureLegacy", hold)
        return writer, entered, release, errors

    yield create
    for release in releases:
        release.set()
    for writer in writers:
        writer.close(drain=False)
        assert not writer._thread.is_alive()


def testCaptureIsNonblockingAndInspectionKeepsFrozenAdmittedInvocations(heldWriter):
    events = []
    writer, entered, release, errors = heldWriter(jobId="job", projectRevision=7,
        inspectionPublisher=lambda **event: events.append(event))
    node = _node(image="image", frame="bbox2d")
    image = np.full((7, 9, 3), 10, np.uint8)
    frame = {"items": [{"x": 1}]}
    writer.capture(node, {"image": image, "frame": frame}, _context())
    assert entered.wait(1) and not release.is_set()
    replaced = writer.capture(node, {"image": image, "frame": frame}, _context(1))
    image[:] = 240
    frame["items"][0]["x"] = 9
    finalContext = _context(2)
    final = writer.capture(node, {"image": image, "frame": frame}, finalContext)
    image[:] = 17
    frame["items"][0]["x"] = 99
    node.outputPorts.clear()
    assert len([event for event in events if event["payload"].get("entries")]) == 1
    assert final["retired"][0]["captureId"] == replaced["captureId"]
    assert not (writer._writer.root / "index.json").exists()
    release.set()
    writer.close()
    captures = [event["payload"]["entries"][0] for event in events if event["payload"].get("entries")]
    assert len(captures) == 2 and captures[-1]["captureId"] == final["captureId"]
    live = captures[-1]
    liveImage = cv2.imdecode(np.fromfile(writer._writer.inspectionRoot / live["relativePath"], np.uint8), -1)
    assert np.all(liveImage == 240)
    rows = _entries(writer)
    assert len(rows) == 2
    assert {row["captureId"] for row in rows} == {live["captureId"]}
    assert all(row["nodeRunId"] == finalContext.nodeRunId and row["iterationPath"] == [2] for row in rows)
    assert all(row["originJobId"] == "job" and row["originProjectRevision"] == 7 for row in rows)
    imageRow = next(row for row in rows if row["port"] == "image")
    persisted = cv2.imdecode(np.fromfile(writer._writer.root / imageRow["relativePath"], np.uint8), -1)
    np.testing.assert_array_equal(persisted, liveImage)
    assert _values(writer)[("main", "source", "frame")] == {"items": [{"x": 9}]}
    assert writer.statistics() == dict(submitted=3, coalesced=1, written=2, failed=0, discarded=0, inspectionFailed=0,
        peakPending=1, peakBufferedBytes=2 * (image.nbytes + len(json.dumps({"items": [{"x": 9}]}).encode())))
    assert not errors and not writer._thread.is_alive()
    assert writer._bufferedBytes == 0
    assert len(list((writer._writer.root / "assets").iterdir())) == 2


def testPartialOutputSetsPreserveLatestPerPortAndSubmissionOrder(heldWriter):
    writer, entered, release, _ = heldWriter()
    writer.capture(_node("blocker"), {"value": 0}, _context())
    assert entered.wait(1)
    node = _node(a="integer", b="integer")
    for iteration, outputs in enumerate(({"a": 1, "b": 1}, {"a": 2}, {"a": 3, "b": 3}, {"b": 4})):
        writer.capture(node, outputs, _context(iteration))
    release.set()
    writer.close()
    values = _values(writer)
    assert values[("main", "source", "a")] == 3
    assert values[("main", "source", "b")] == 4
    assert writer.statistics()["coalesced"] == 1
    assert writer.statistics()["written"] == 4


@pytest.mark.parametrize("limits, output, expectedBytes", [
    ({"maxPending": 1}, {"value": 1}, 2),
    ({"maxBytes": 8}, {"image": np.zeros((2, 2), np.uint8)}, 8),
    ({"maxBytes": 1}, {"image": np.zeros((2, 2), np.uint8)}, 4),
])
def testCapacityNeverWaitsAndOneOversizedCaptureRemainsBounded(heldWriter, limits, output, expectedBytes):
    writer, entered, release, errors = heldWriter(**limits)
    ports = {port: "image" if port == "image" else "integer" for port in output}
    writer.capture(_node("first", **ports), output, _context())
    assert entered.wait(1)
    if limits != {"maxBytes": 1}:
        writer.capture(_node("second", **ports), output, _context())
    done, failures = threading.Event(), []

    def produce():
        try:
            writer.capture(_node("third", **ports), output, _context())
        except Exception as error:
            failures.append(error)
        finally:
            done.set()

    producer = threading.Thread(target=produce)
    producer.start()
    try:
        assert done.wait(.2), "optional preview admission must not wait for the writer"
        assert not release.is_set()
        assert writer.statistics()["peakBufferedBytes"] == expectedBytes
    finally:
        release.set()
        producer.join(3)
    assert done.is_set() and not failures
    writer.close()
    stats = writer.statistics()
    assert stats["written"] == (1 if limits == {"maxBytes": 1} else 2)
    assert stats["discarded"] == 1
    assert stats["written"] + stats["discarded"] == stats["submitted"]
    assert {row["nodeId"] for row in _entries(writer)} == (
        {"first"} if limits == {"maxBytes": 1} else {"first", "third"})
    assert stats["peakBufferedBytes"] == expectedBytes
    assert stats["peakPending"] <= limits.get("maxPending", 64)
    assert writer._bufferedBytes == 0 and not errors


def testCancellationRejectsNewCaptureDiscardsQueueAndJoinsActiveWrite(heldWriter):
    token = CancellationToken()
    writer, entered, release, errors = heldWriter(maxPending=1, cancellation=token)
    writer.capture(_node("first"), {"value": 1}, _context())
    assert entered.wait(1)
    writer.capture(_node("queued"), {"value": 2}, _context())
    done, failures = threading.Event(), []

    def produce():
        try:
            writer.capture(_node("waiting"), {"value": 3}, _context())
        except Exception as error:
            failures.append(error)
        finally:
            done.set()

    producer = threading.Thread(target=produce)
    token.cancel()
    producer.start()
    try:
        assert done.wait(1)
        assert len(failures) == 1 and isinstance(failures[0], CancellationRequested)
        closer = threading.Thread(target=writer.close)
        closer.start()
        closer.join(.05)
        assert closer.is_alive(), "active file I/O must retire before close returns"
    finally:
        release.set()
        producer.join(2)
    closer.join(2)
    assert not closer.is_alive() and not writer._thread.is_alive()
    assert _values(writer) == {("main", "first", "value"): 1}
    assert writer.statistics()["discarded"] == 1
    assert writer._bufferedBytes == 0 and not errors


def testLegacyIoFailureWarnsWithoutDroppingNextValidCapture(heldWriter, monkeypatch):
    writer, entered, release, errors = heldWriter()
    persist = writer._writer.captureLegacy

    def failFirst(node, outputs, context, **kwargs):
        if node.nodeId == "bad":
            entered.set()
            assert release.wait(3)
            raise OSError("snapshot disk write failed")
        return persist(node, outputs, context, **kwargs)

    monkeypatch.setattr(writer._writer, "captureLegacy", failFirst)
    context = _context()
    writer.capture(_node("bad"), {"value": 1}, context)
    assert entered.wait(1)
    writer.capture(_node("good"), {"value": 2}, _context(1))
    release.set()
    writer.close()
    assert len(errors) == 1
    assert errors[0]["context"] == context
    assert errors[0]["code"] == "E_PREVIEW_SNAPSHOT_FAILED" and errors[0]["level"] == "WARN"
    assert _values(writer) == {("main", "good", "value"): 2}
    assert writer.statistics()["failed"] == writer.statistics()["written"] == 1


def testSameNodeIdsAreIsolatedByWorkflowAndJob(heldWriter):
    first, entered, release, _ = heldWriter(jobId="job-a")
    second, secondEntered, secondRelease, _ = heldWriter(jobId="job-b")
    first.capture(_node(), {"value": 1}, _context(workflow="a"))
    assert entered.wait(1)
    first.capture(_node(), {"value": 2}, _context(workflow="b"))
    second.capture(_node(), {"value": 3}, _context(workflow="a"))
    assert secondEntered.wait(1)
    release.set()
    secondRelease.set()
    first.close()
    second.close()
    assert _values(first) == {("a", "source", "value"): 1, ("b", "source", "value"): 2}
    assert _values(second) == {("a", "source", "value"): 3}
    assert {row["originJobId"] for row in _entries(first)} == {"job-a"}
    assert {row["originJobId"] for row in _entries(second)} == {"job-b"}


def testRealCompiledVideoLoopCapturesFrozenPortMappings(tmp_path):
    from emo_master.apps.runtime.workflow.runner import WorkflowRunner
    from tests.runtime.test_video_files_foreach import prepare
    compiled, registry, paths = prepare(tmp_path)
    events, inspections = [], []

    def publish(**event):
        events.append(event)

    writer = AsyncPreviewSnapshotWriter(tmp_path / "job", errorPublisher=publish,
        inspectionPublisher=lambda **event: inspections.append(event))
    try:
        runner = WorkflowRunner(compiled, registry, previewSnapshotStore=writer, eventPublisher=publish)
        result = runner.run("main", {}, RunContext.root("job", "main"), CancellationToken())
        assert result.outputs == {"videoPath": [str(path) for path in paths]}
    finally:
        writer.close()
    assert not [event for event in events if event["eventType"] == "preview.snapshot.failed"]
    assert writer.statistics()["submitted"] > 0 and writer.statistics()["written"] > 0
    assert writer.statistics()["failed"] == 0
    assert _values(writer)[("frame", "load", "frameIndex")] == 5
    assert _values(writer)[("frame", "load", "hasNext")] is False
    images = [entry for event in inspections for entry in event["payload"].get("entries", [])
              if entry["nodeId"] == "load"]
    completed = [event for event in events if event["eventType"] == "node.completed"
                 and event["context"].callerNodeId == "load"]
    imageCaptures = [event["payload"]["previewCapture"]["captureId"] for event in completed
                     if event["payload"].get("previewCapture", {}).get("state") == "PREPARING"]
    assert len(imageCaptures) == 4  # Every business invocation still executes.
    assert 1 <= len(images) <= 4
    assert images[-1]["captureId"] == imageCaptures[-1]
    row = next(row for row in _entries(writer) if row["nodeId"] == "load" and row["port"] == "image")
    assert row["captureId"] == images[-1]["captureId"]
    actual = cv2.imdecode(np.fromfile(writer._writer.root / row["relativePath"], np.uint8), -1)
    assert np.mean(actual) == pytest.approx(50, abs=5)


def testSavedArtifactInspectionRemainsSynchronousAndUnmodified(heldWriter):
    events = []
    writer, entered, _, _ = heldWriter(inspectionPublisher=lambda **event: events.append(event))
    node = _node()
    writer.capture(node, {"value": 1}, _context())
    assert entered.wait(1)
    path = writer._writer.root.parent / "artifacts" / "crop.png"
    path.parent.mkdir()
    ok, encoded = cv2.imencode(".png", np.full((5, 7, 3), 173, np.uint8))
    assert ok
    content = encoded.tobytes()
    path.write_bytes(content)
    writer.captureArtifact(node, dict(path=str(path), mimeType="image/png", artifactId="saved",
        checksum=hashlib.sha256(content).hexdigest()), _context())
    assert len(events) == 1 and events[0]["payload"]["entries"][0]["captureId"] == "saved"
    assert events[0]["payload"]["entries"][0]["port"] == "__saved_result__"
    assert path.read_bytes() == content


@pytest.mark.parametrize("asynchronous", [False, True])
def testUnsupportedOrEmptyOutputsDoNotRewriteIndex(tmp_path, monkeypatch, asynchronous):
    from emo_master.apps.runtime.preview import store
    writer = AsyncPreviewSnapshotWriter(tmp_path, errorPublisher=lambda **event: None) if asynchronous else (
        PreviewSnapshotWriter(tmp_path))

    def forbidden(*args):
        raise AssertionError("no snapshot changes must not touch the index")

    monkeypatch.setattr(store, "_atomicJson", forbidden)
    try:
        writer.capture(_node(opaque="any", image="image"), {"opaque": object(), "image": None}, _context())
        writer.capture(_node(), {}, _context())
    finally:
        if asynchronous:
            writer.close()
            assert writer.statistics()["submitted"] == writer.statistics()["failed"] == 0
    assert not (tmp_path / "preview_staging/index.json").exists()


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('shape, encodes', [((12, 17, 3), 1), ((1080, 1920, 3), 1), ((1082, 1922, 3), 2)])
def testInspectionAndLegacyShareOnlySameResolutionPng(tmp_path, monkeypatch, asynchronous, shape, encodes):
    encode = cv2.imencode
    shapes, inspected = [], []

    def measured(extension, image, *args):
        shapes.append(image.shape)
        return encode(extension, image, *args)

    monkeypatch.setattr(cv2, 'imencode', measured)
    writer = (AsyncPreviewSnapshotWriter(tmp_path, errorPublisher=lambda **event: pytest.fail(str(event)),
              inspectionPublisher=lambda **event: inspected.append(event)) if asynchronous else
              PreviewSnapshotWriter(tmp_path, inspectionPublisher=lambda **event: inspected.append(event)))
    image = np.full(shape, 37, np.uint8)
    writer.capture(_node(image='image'), {'image': image}, _context())
    if asynchronous:
        writer.close()
    assert len(shapes) == encodes
    native = writer._writer if asynchronous else writer
    row = json.loads((native.root / 'index.json').read_text(encoding='utf-8'))['entries'][0]
    actual = cv2.imdecode(np.fromfile(native.root / row['relativePath'], np.uint8), -1)
    np.testing.assert_array_equal(actual, image)
    entry = next(event['payload']['entries'][0] for event in inspected if event['payload'].get('entries'))
    preview = cv2.imdecode(np.fromfile(native.inspectionRoot / entry['relativePath'], np.uint8), -1)
    assert preview.shape[0] <= 1080 and preview.shape[1] <= 1920
    assert np.all(preview == 37)
    assert row['captureId'] == entry['captureId']


@pytest.mark.parametrize('drain', [False, True])
def testPreviewCadenceCoalescesWithoutBlockingAndCloseDoesNotWaitForTimer(tmp_path, monkeypatch, drain):
    from time import monotonic
    writer = AsyncPreviewSnapshotWriter(tmp_path, minIntervalMs=60_000, errorPublisher=lambda **event: None)
    written = threading.Event()
    original = writer._writer.captureLegacy
    values = []

    def capture(node, outputs, context, **kwargs):
        original(node, outputs, context, **kwargs)
        values.append(outputs['value'])
        written.set()

    monkeypatch.setattr(writer._writer, 'captureLegacy', capture)
    try:
        writer.capture(_node(), {'value': 0}, _context())
        assert written.wait(2)
        written.clear()
        for value in range(1, 4):
            writer.capture(_node(), {'value': value}, _context(value))
        assert not written.wait(.05)
        assert len(writer._pending) == 1
        assert writer.statistics()['coalesced'] == 2
        started = monotonic()
        writer.close(drain=drain)
        assert monotonic() - started < 2
        assert values == ([0, 3] if drain else [0])
        assert writer._bufferedBytes == 0
        assert not writer._thread.is_alive()
    finally:
        writer.close(drain=False)
