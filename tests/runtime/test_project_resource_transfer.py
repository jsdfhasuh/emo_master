from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import grpc
import pytest

from emo_master.apps.runtime.context.sqlite_store import SqliteStore
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.runtime.project_config.transfer import ResourceTransfers, MAX_CHUNK_BYTES
from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.main import createRuntimeServer
from tests.runtime.test_device_runtime import simulatedProject
from tests.runtime.test_project_configuration import finish


def manifest(data=b"model data", path="models/input.bin"):
    return dict(path=path, size=len(data), sha256=hashlib.sha256(data).hexdigest(), purpose="model")


def request(action, **kwargs):
    return SimpleNamespace(action=action, transfer_id="transfer", project_id="project", data=b"", offset=0,
                           resource_json=json.dumps(manifest()), **kwargs)


@pytest.fixture
def transfers(tmp_path):
    return ResourceTransfers(SqliteStore(tmp_path / "runtime.db"), tmp_path / "uploads")


def chunk(data, offset=0):
    result = request("CHUNK")
    result.data, result.offset = data, offset
    return result


def testOrderedIdempotentChunksAndUnreferencedReadyIsRemovable(transfers):
    assert transfers.execute(request("BEGIN"))["offset"] == 0
    assert transfers.execute(chunk(b"model "))["offset"] == 6
    assert transfers.execute(chunk(b"model "))["offset"] == 6
    with pytest.raises(ValueError, match="differs"):
        transfers.execute(chunk(b"change"))
    with pytest.raises(ValueError, match="offset"):
        transfers.execute(chunk(b"x", 8))
    transfers.execute(chunk(b"data", 6))
    assert transfers.execute(request("COMMIT"))["state"] == "READY"
    assert transfers.execute(request("COMMIT"))["state"] == "READY"
    assert transfers.execute(request("BEGIN"))["state"] == "READY"
    assert transfers.execute(request("CANCEL"))["state"] == "CANCELLED"
    assert not list(transfers.root.iterdir())


def testInvalidDigestIncompleteAndCapacityFailWithoutReady(transfers, monkeypatch):
    import emo_master.apps.runtime.project_config.transfer as module
    monkeypatch.setattr(module, "MAX_TRANSFER_BYTES", 9)
    with pytest.raises(ValueError, match="capacity"):
        transfers.execute(request("BEGIN"))
    monkeypatch.setattr(module, "MAX_TRANSFER_BYTES", 10)
    transfers.execute(request("BEGIN"))
    with pytest.raises(ValueError, match="incomplete"):
        transfers.execute(request("COMMIT"))
    with pytest.raises(ValueError, match="budget"):
        transfers.execute(chunk(b"x" * (MAX_CHUNK_BYTES + 1)))
    transfers.execute(chunk(b"wrong data"))
    with pytest.raises(ValueError, match="digest"):
        transfers.execute(request("COMMIT"))
    assert transfers.execute(request("STATUS"))["state"] == "PENDING"
    transfers.execute(request("CANCEL"))
    assert not list(transfers.root.iterdir())


def testRestartRemovesOnlyPendingOwnedTransfers(transfers):
    transfers.execute(request("BEGIN"))
    transfers.execute(chunk(b"model"))
    unknown = transfers.root / "unknown.dat"
    unknown.write_bytes(b"keep")
    restored = ResourceTransfers(transfers.sqlite, transfers.root)
    restored.recover()
    assert restored.execute(request("STATUS"))["state"] == "CANCELLED"
    assert list(restored.root.iterdir()) == [unknown]
    restored.close()
    with pytest.raises(ValueError, match="closing"):
        restored.execute(request("BEGIN"))


def testIdentityAndManifestConflictsCannotOverwriteTransfer(transfers):
    transfers.execute(request("BEGIN"))
    changed = request("BEGIN")
    changed.resource_json = json.dumps(manifest(b"other"))
    with pytest.raises(ValueError, match="content changed"):
        transfers.execute(changed)
    changed = request("STATUS")
    changed.project_id = "other-project"
    with pytest.raises(ValueError, match="different project"):
        transfers.execute(changed)
    changed = request("BEGIN")
    changed.transfer_id = "../../outside"
    with pytest.raises(ValueError, match="bounded"):
        transfers.execute(changed)


def testClosingAnotherInProcessServiceCannotCancelPendingTransfer(transfers):
    transfers.execute(request("BEGIN"))
    transfers.execute(chunk(b"model"))
    other = ResourceTransfers(transfers.sqlite, transfers.root)
    other.close()
    assert transfers.execute(request("STATUS"))["state"] == "PENDING"
    assert transfers.execute(request("STATUS"))["offset"] == 5


def testCancelledTransferTombstonesCannotExhaustAdmission(transfers):
    with transfers.sqlite.connection() as connection:
        connection.executemany("INSERT INTO projectResourceTransfers (transferId,projectId,manifest,filename,state,offset,instanceId) VALUES (?,?,?,?, 'CANCELLED',0,?)",
            [(f"expired-{index}", "project", json.dumps(manifest()), f"{index:032x}.bin", "old")
             for index in range(4096)])
    assert transfers.execute(request("BEGIN"))["state"] == "PENDING"
    with transfers.sqlite.connection() as connection:
        assert connection.execute("SELECT count(*) FROM projectResourceTransfers").fetchone()[0] == 257


def testSharedUploadOwnersSerializeCapacityAndChunkState(transfers, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import emo_master.apps.runtime.project_config.transfer as module
    monkeypatch.setattr(module, "MAX_TRANSFER_BYTES", 10)
    other = ResourceTransfers(transfers.sqlite, transfers.root)
    assert other.lock is transfers.lock
    barrier = Barrier(2)
    def begin(owner, key):
        value = request("BEGIN")
        value.transfer_id = key
        barrier.wait(timeout=5)
        try:
            return owner.execute(value)["state"]
        except ValueError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(begin, transfers, "first")
        second = pool.submit(begin, other, "second")
        assert sorted([first.result(timeout=10), second.result(timeout=10)]) == ["E_RESOURCE_TRANSFER_CAPACITY", "PENDING"]
    with transfers.sqlite.connection() as connection:
        assert connection.execute("SELECT count(*) FROM projectResourceTransfers").fetchone()[0] == 1


@pytest.mark.parametrize('pageOnly', [False, True])
def testRemoteUploadIsExplicitBeforeCommitAndSurvivesRestart(tmp_path, pageOnly):
    remote, local = tmp_path / "runtime-project", tmp_path / "designer-project"
    remote.mkdir()
    local.mkdir()
    payload = simulatedProject()
    path = remote / "project.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    source = local / "remote-only.png"
    import cv2
    import numpy as np
    source.write_bytes(cv2.imencode(".png", np.zeros((8, 8, 3), dtype=np.uint8))[1].tobytes())
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    server, port, _ = createRuntimeServer(port=0, runtimeService=service)
    server.start()
    try:
        assert service.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            grpc.channel_ready_future(channel).result(5)
            client = RuntimeClient(runtime_pb2_grpc.RuntimeServiceStub(channel))
            snapshot = client.getOnlineProjectSnapshot()
            candidate = deepcopy(snapshot["payload"])
            candidate["workflows"]["main"]["nodes"].append(dict(nodeId="file", kind="operator",
                operatorId="vision.io.image_loader", params=dict(imagePath="unavailable-on-runtime.png")))
            candidate["resources"]["items"]["image"] = dict(manifest(source.read_bytes(), source.name), purpose="input_image")
            candidate["resources"]["parameterBindings"] = [dict(resourceId="image", target=dict(
                workflowId="main", nodeId="file", parameterPath=["imagePath"]))]
            if pageOnly:
                candidate["workflows"]["main"]["nodes"].pop()
                candidate["resources"]["parameterBindings"] = []
                candidate["resources"]["items"]["image"]["purpose"] = "page_image"
                candidate["presentation"] = dict(defaultPageId="page", pageOrder=["page"], pages={
                    "page": dict(name="Static", components=[dict(componentId="picture", type="image",
                        props=dict(imageResourceId="image"))])})
            from emo_master.core.project.configuration import projectChanges
            paths = [change["path"] for change in projectChanges(snapshot["payload"], candidate)]
            with pytest.raises(RuntimeClientError, match="resource missing"):
                client.writeProjectConfiguration(snapshot, candidate, paths, "before-upload", validate=True)
            result = client.uploadProjectResources(snapshot, candidate, local)
            assert result["uploaded"][0]["state"] == "READY"
            assert client.uploadProjectResources(snapshot, candidate, local) == result
            with service.sqliteStore.connection() as connection:
                assert connection.execute("SELECT count(*) FROM projectResourceTransfers").fetchone()[0] == 1
            assert service.projectConfiguration.snapshot()["documentRevision"] == 1
            assert not service.deviceManager.workers
            assert not (remote / source.name).exists()
            client.writeProjectConfiguration(snapshot, candidate, paths, "publish-upload")
            assert finish(service.projectConfiguration, "publish-upload")["state"] == "COMMITTED"
            effective = Path(service.loadedDocument._pageResourcePaths["image"] if pageOnly else
                next(n for n in service.loadedDocument.workflows["main"].nodes if n.nodeId == "file").params["imagePath"])
            assert effective.read_bytes() == source.read_bytes()
            assert effective.is_relative_to(tmp_path / "configuration-resources")
            stale = pb.ProjectResourceTransferRequest(runtime_instance_id="old", project_id=service.loadedProjectId,
                transfer_id="stale", action="BEGIN", resource_json=json.dumps(manifest()))
            reply = service.TransferProjectResource(stale, None)
            assert not reply.ok and reply.code == "E_CONFIG_INSTANCE"
    finally:
        server.stop(0).wait()
        service.close()
    source.unlink()
    restored = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    try:
        assert not restored.onlineRecoveryError
        assert restored.projectConfiguration.snapshot()["documentRevision"] == 2
        effective = Path(restored.loadedDocument._pageResourcePaths["image"] if pageOnly else
            next(n for n in restored.loadedDocument.workflows["main"].nodes if n.nodeId == "file").params["imagePath"])
        assert effective.exists()
        if pageOnly:
            restored._reclaimConfigurationResources()
            assert effective.exists()
        assert restored.loadedPayload["resources"]["items"]["image"]["path"] == source.name
    finally:
        restored.close()
