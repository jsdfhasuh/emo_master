"""Deterministic decision fences; no sleeps or static-image freshness oracle."""
import hashlib
import json
import threading
import time

import numpy as np
import pytest

from emo_master.clients.runtime.display_session import DisplaySession
from emo_master.core.presentation.results import ClosedResult
from emo_master.apps.runtime.presentation.rpc import wireResult
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb


def result(ordinal, *, terminal="COMPLETED"):
    key = f"result-{ordinal}"
    return ClosedResult.model_validate_json(json.dumps(dict(
        identity=dict(runtimeInstanceId="runtime", jobId="job", resultScopeId="root", invocationId=f"call-{ordinal}",
            resultKey=key, resultOrdinal=ordinal, executionRevision="a" * 64, capturePlanRevision="b" * 64, mode="runtime"),
        expectedSourceIds=["image"], sources=[dict(sourceId="image", state="AVAILABLE", image=dict(
            resourceId=f"asset-{ordinal}", ownerResultKey=key, byteSize=3,
            sha256=hashlib.sha256(bytes([ordinal]) * 3).hexdigest(), mimeType="image/png",
            provenance=dict(frameIdentity=key, coordinateSpaceId="pixels", trust="proven")))],
        status="COMPLETE" if terminal == "COMPLETED" else terminal, executionTerminal=terminal)))


@pytest.fixture
def session(monkeypatch):
    # Real session state/queues/locks, but explicit test control of delivery and
    # decode; network and native Qt coverage lives in the W01 integration suite.
    for name in ("_receive", "_health", "_decode"):
        monkeypatch.setattr(DisplaySession, name, lambda self: None)
    value = DisplaySession("127.0.0.1:1", "job", expectedRuntimeInstanceId="runtime", projectId="project")
    try:
        yield value
    finally:
        value.close()


@pytest.mark.parametrize("fence,reason", [("newer", "SUPERSEDED"), ("reset", "GENERATION_CHANGED"),
    ("job", "JOB_CHANGED"), ("runtime", "RUNTIME_CHANGED"), ("expired", "SCOPE_EXPIRED")])
def testDelayedDecodeRecordsExactDecisionAndNeverRestoresOldImage(session, monkeypatch, fence, reason):
    old, new = result(2), result(3, terminal="FAILED")
    entered, release = threading.Event(), threading.Event()
    def read(value, image):
        entered.set()
        assert release.wait(3)
        return np.full((1, 1, 3), value.identity.resultOrdinal, dtype=np.uint8)
    monkeypatch.setattr(session, "_readImage", read)
    session.high["root"] = 2
    session.loading["root"] = old
    worker = threading.Thread(target=session._decodeLive, args=(session.generation, old, time.perf_counter_ns(), 0))
    worker.start()
    try:
        assert entered.wait(2)
        with session.lock:
            if fence == "newer":
                session._accept(pb.DisplaySnapshot(runtime_instance_id="runtime", job_id="job", cursor=3,
                    results=[wireResult(new)]))
            elif fence == "reset":
                session.generation += 1
            elif fence == "job":
                session.jobId = "other"
            elif fence == "runtime":
                session.instanceId = "other"
            else:
                session.expired["root"] = 2
        release.set()
        worker.join(3)
        assert not worker.is_alive()
        row = session.records[-1]
        assert row["live_decision"] == reason and not row["applied_to_live"]
        assert row["decoded"] == ["image"] and row["model_ns"] is None
        assert row["job_id"] == "job" and row["invocation_id"] == "call-2"
        assert row["image_refs"]["image"]["owner_result_key"] == "result-2"
        assert not session.latest
        if fence == "newer":
            assert row["high_ordinal"] == 3 and row["high_result_key"] == "result-3"
            session._decodeLive(session.generation, new, time.perf_counter_ns(), 0)
            shown, images, errors = session.latest["root"]
            assert shown.status == "FAILED" and shown.identity.resultKey == "result-3"
            assert np.array_equal(images["image"], np.full((1, 1, 3), 3, dtype=np.uint8))
            assert not errors and session.records[-1]["live_decision"] == "APPLIED"
            assert old.status == "COMPLETE"  # Live decisions never mutate the result.
    finally:
        release.set()
        worker.join(3)


def testAlreadyStaleQueuedTaskStillHasAnExplicitAuditDecision(session):
    session._decodeLive(session.generation - 1, result(1), time.perf_counter_ns(), 0)
    assert session.records[-1]["live_decision"] == "GENERATION_CHANGED"
    assert session.records[-1]["decoded_ns"] is None


def testTwoNetworkConsumersAll96ChangingImagesAndExplicitSupersession(channel, tmp_path, monkeypatch):
    import cv2
    import os
    from pathlib import Path
    from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer
    from tests.runtime.presentation.test_network import until

    channel.jobs["job"] = {"scopeIds": []}
    server = AioRuntimeServer(channel.runtime, channel)
    clients = [DisplaySession(f"127.0.0.1:{server.port}", "job") for _ in range(2)]
    entered, release = threading.Event(), threading.Event()
    verified = [[], []]
    def pixels(ordinal):
        image = np.zeros((9, 13, 3), np.uint8)
        image[:, :, 0] = ordinal
        image[:, :, 1] = np.arange(13, dtype=np.uint8)
        image[:, :, 2] = np.arange(9, dtype=np.uint8)[:, None]
        return image
    for index, client in enumerate(clients):
        original = client._readImage
        def read(value, image, _original=original, _index=index):
            decoded = _original(value, image)
            ordinal = value.identity.resultOrdinal
            np.testing.assert_array_equal(decoded, pixels(ordinal))
            assert image.ownerResultKey == value.identity.resultKey
            verified[_index].append(ordinal)
            if _index == 0 and ordinal == 2:
                entered.set()
                assert release.wait(5)
            return decoded
        monkeypatch.setattr(client, "_readImage", read)
    def publish(ordinal):
        identity = result(ordinal).identity.model_dump()
        identity["runtimeInstanceId"] = channel.runtimeInstanceId
        path = tmp_path / f"frame-{ordinal}.png"
        assert cv2.imwrite(str(path), pixels(ordinal))
        image = channel.assets.adopt(path, "job", identity["resultKey"], dict(
            frameIdentity=identity["resultKey"], coordinateSpaceId="pixels", trust="proven"))
        assert channel.store.begin(dict(identity=identity, expected=["image"]))
        assert channel.store.close(identity["resultKey"], [dict(sourceId="image", state="AVAILABLE", image=image)], "COMPLETED")
    def has(client, ordinal):
        return any(row["ordinal"] == ordinal for row in client.records)
    try:
        until(lambda: server.active["display"] == 2 and all(c.compatible for c in clients))
        for ordinal in range(1, 97):
            publish(ordinal)
            if ordinal == 2:
                assert entered.wait(2)
                until(lambda: has(clients[1], 2))
                continue
            if ordinal == 3:
                until(lambda: clients[0].high.get("root") == 3 and has(clients[1], 3))
                release.set()
            until(lambda: all(has(c, ordinal) for c in clients))
        assert verified == [list(range(1, 97))] * 2
        for index, client in enumerate(clients):
            rows = list(client.records)
            assert len(rows) == 96 and {r["ordinal"] for r in rows} == set(range(1, 97))
            assert all(r["decoded"] == ["image"] and not r["failures"] for r in rows)
            assert [r["ordinal"] for r in rows if not r["applied_to_live"]] == ([2] if index == 0 else [])
            assert not client.errors
        decision = clients[0].records[1]
        assert decision["live_decision"] == "SUPERSEDED" and decision["high_ordinal"] == 3
        assert decision["high_result_key"] == "result-3"
        assert decision["task_generation"] == decision["decision_generation"]
        assert channel.store.snapshot("job")["results"][0].identity.resultOrdinal == 96
        if os.environ.get("EMO_DISPLAY_EVIDENCE"):
            path = Path(os.environ["EMO_DISPLAY_EVIDENCE"])
            path.mkdir(parents=True, exist_ok=True)
            (path / "changing-96.json").write_text(json.dumps(dict(
                role="deterministic fault injection; NOT historical root cause proof",
                denominator=96, verified_pixels=verified, consumers=[list(c.records) for c in clients]), indent=2), encoding="utf-8")
    finally:
        release.set()
        for client in clients:
            client.close()
        server.close()
        channel.jobs.clear()
