from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import threading
import time
from types import SimpleNamespace

import grpc
import pytest

from emo_master.apps.designer.services.display_calls import DisplayCallContext
from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.plugin.models import PluginDescriptor, PluginIconAsset, PluginManifest, RegistryScanResult
from tests.icon_fixtures import SVG


def iconService():
    service = RuntimeService.__new__(RuntimeService)
    manifest = PluginManifest("test.icon", "Icon", "1.0.0", "test:Icon", "Other", "default", "", {}, {}, {}, "0.1", "1.x")
    asset = PluginIconAsset(SVG, "image/svg+xml", hashlib.sha256(SVG).hexdigest())
    descriptor = PluginDescriptor(manifest, object, iconAsset=asset)
    service.pluginScanResult = RegistryScanResult({manifest.operatorId: descriptor}, {})
    return service


@contextmanager
def network(service):
    pool = ThreadPoolExecutor(max_workers=4)
    server = grpc.server(pool)
    rpc.add_RuntimeServiceServicer_to_server(service, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    grpc.channel_ready_future(channel).result(timeout=5)
    client = RuntimeClient(rpc.RuntimeServiceStub(channel), ownedChannel=channel)
    try:
        yield client
    finally:
        client.close()
        server.stop(0).wait(3)
        pool.shutdown()


@pytest.mark.parametrize("remote", [False, True])
def testAssetAndCatalogRoundtrip(remote):
    service = iconService()
    def check(client):
        operators = client.listOperators(timeoutMs=500)
        info = operators[0]
        assert info.icon["status"] == "ready"
        assert info.icon["byteSize"] == len(SVG)
        reply = client.getOperatorIconAsset(info.operatorId, info.version, info.icon["sha256"])
        assert reply.ok and reply.content == SVG
        assert hashlib.sha256(reply.content).hexdigest() == reply.sha256
        assert client.deadlineMs == 10000
    if remote:
        with network(service) as client:
            check(client)
    else:
        check(RuntimeClient(service))


@pytest.mark.parametrize("fields,code", [
    ({"operator_id": ""}, "E_ICON_REQUEST_INVALID"),
    ({"expected_sha256": "bad"}, "E_ICON_REQUEST_INVALID"),
    ({"operator_id": "missing"}, "E_ICON_OPERATOR_NOT_FOUND"),
    ({"version": "2.0.0"}, "E_ICON_VERSION_MISMATCH"),
    ({"expected_sha256": "0" * 64}, "E_ICON_DIGEST_MISMATCH"),
])
def testResourceRequestFailures(fields, code):
    request = {"operator_id": "test.icon", "version": "1.0.0", "expected_sha256": hashlib.sha256(SVG).hexdigest(), **fields}
    reply = iconService().GetOperatorIconAsset(pb.GetOperatorIconAssetRequest(**request), None)
    assert not reply.ok and reply.code == code and not reply.content


def testNoResourceAndOldMetadataCompatibility():
    service = iconService()
    descriptor = service.pluginScanResult.activeOperators["test.icon"]
    service.pluginScanResult.activeOperators["test.icon"] = replace(descriptor, iconAsset=None)
    client = RuntimeClient(service)
    assert client.listOperators()[0].icon["status"] == "none"
    reply = client.getOperatorIconAsset("test.icon", "1.0.0", "0" * 64)
    assert reply.code == "E_ICON_ASSET_UNAVAILABLE"
    old = SimpleNamespace(ListOperators=lambda request, context: pb.ListOperatorsReply(operators=[pb.OperatorInfo(operator_id="old")]))
    assert RuntimeClient(old).listOperators()[0].icon["status"] == "none"
    with pytest.raises(RuntimeClientError, match="no icon"):
        RuntimeClient(old).getOperatorIconAsset("old", "1", "0" * 64)


def testRealUnaryDeadlineAndOwnerCancellationDoNotCloseChannel():
    started = threading.Event()
    class Slow(rpc.RuntimeServiceServicer):
        def ListOperators(self, request, context):
            return pb.ListOperatorsReply()
        def GetOperatorIconAsset(self, request, context):
            started.set()
            while context.is_active():
                time.sleep(0.005)
            return pb.GetOperatorIconAssetReply()
    with network(Slow()) as client:
        before = time.monotonic()
        with pytest.raises(RuntimeClientError):
            client.getOperatorIconAsset("icon", "1", "0" * 64, timeoutMs=40)
        assert time.monotonic() - before < 1
        started.clear()
        errors = []
        def call():
            try:
                client.getOperatorIconAsset("icon", "1", "0" * 64, owner="panel")
            except RuntimeClientError as error:
                errors.append(error.code)
        thread = threading.Thread(target=call)
        thread.start()
        assert started.wait(1)
        client.closeDisplayOwner("panel")
        thread.join(1)
        assert not thread.is_alive() and errors == ["E_DISPLAY_CANCELLED"]
        assert client.listOperators(timeoutMs=500) == []
        assert client.deadlineMs == 10000
        with pytest.raises(RuntimeClientError):
            client.getOperatorIconAsset("icon", "1", "0" * 64, owner="panel")


def testLateEmbeddedResultRejectedAndFutureAttachRace():
    class Local:
        def ListOperators(self, request, context):
            time.sleep(0.04)
            return pb.ListOperatorsReply()
    with pytest.raises(RuntimeClientError) as error:
        RuntimeClient(Local()).listOperators(timeoutMs=5)
    assert error.value.code == "E_DISPLAY_TIMEOUT"
    context = DisplayCallContext()
    context.cancel()
    cancelled = []
    context.attach(SimpleNamespace(cancel=lambda: cancelled.append(True)))
    assert cancelled == [True]


def testRealChannelRecoveryRenewsDisplayScope():
    pool = ThreadPoolExecutor(max_workers=2)

    def startServer(address):
        server = grpc.server(pool)
        rpc.add_RuntimeServiceServicer_to_server(iconService(), server)
        port = server.add_insecure_port(address)
        assert port
        server.start()
        return server, port

    server, port = startServer("127.0.0.1:0")
    channel = grpc.insecure_channel(f"127.0.0.1:{port}", options=[
        ("grpc.initial_reconnect_backoff_ms", 100),
        ("grpc.min_reconnect_backoff_ms", 100),
        ("grpc.max_reconnect_backoff_ms", 200),
    ])
    client = RuntimeClient(rpc.RuntimeServiceStub(channel), ownedChannel=channel)
    try:
        grpc.channel_ready_future(channel).result(timeout=5)
        assert client.listOperators(timeoutMs=1000)
        oldScope = client.runtimeScope
        server.stop(0).wait(3)
        deadline = time.monotonic() + 5
        while not client._displayDisconnected:
            with pytest.raises(RuntimeClientError):
                client.listOperators(timeoutMs=100)
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert client.runtimeScope == oldScope
        pending = DisplayCallContext()
        client._displayCalls["old-session"] = {pending}
        server, reopenedPort = startServer(f"127.0.0.1:{port}")
        assert reopenedPort == port
        grpc.channel_ready_future(channel).result(timeout=5)
        deadline = time.monotonic() + 5
        while client.runtimeScope == oldScope:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert not pending.is_active()
        assert client.listOperators(timeoutMs=1000)
        assert client.deadlineMs == 10000
    finally:
        client.close()
        server.stop(0).wait(3)
        pool.shutdown()
