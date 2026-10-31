"""Optional Runtime startup reporting and preparation ownership."""
from threading import Event
from time import perf_counter
from types import SimpleNamespace

import pytest

from emo_master.apps.designer.startup import StartupTiming, prepareRuntime
from emo_master.apps.designer.services import runtime_client
from emo_master.apps.runtime.grpc_server import service


def test_runtime_reports_ordered_startup_stages_without_changing_default_behavior(tmp_path):
    stages = []
    runtime = service.RuntimeService(dbPath=tmp_path / "runtime.db", startupProgress=stages.append)
    try:
        assert stages == ["runtime-database", "runtime-maintenance", "runtime-logs", "runtime-workspaces",
                          "runtime-plugins", "runtime-preview", "runtime-managers", "runtime-online-configuration"]
        assert runtime.pluginScanResult.activeOperators
        assert not runtime.onlineRecoveryError
    finally:
        runtime.close()


def test_cancelled_import_does_not_construct_runtime(monkeypatch):
    monkeypatch.delenv("EMO_RUNTIME_TARGET", raising=False)
    monkeypatch.setattr(service, "RuntimeService", lambda **_: pytest.fail("Runtime constructed after cancellation"))
    cancelled = Event()
    cancelled.set()
    assert prepareRuntime(StartupTiming(perf_counter()), cancelled) is None


def test_failed_client_construction_releases_embedded_runtime(monkeypatch):
    closed = []
    monkeypatch.delenv("EMO_RUNTIME_TARGET", raising=False)
    monkeypatch.setattr(service, "RuntimeService", lambda **_: SimpleNamespace(close=lambda: closed.append("runtime")))

    def fail(**_kwargs):
        raise OSError("client failed")

    monkeypatch.setattr(runtime_client, "RuntimeClient", fail)
    with pytest.raises(OSError, match="client failed"):
        prepareRuntime(StartupTiming(perf_counter()), Event())
    assert closed == ["runtime"]


@pytest.mark.parametrize("fail", [False, True])
def test_remote_startup_does_not_create_embedded_service_and_owns_channel(monkeypatch, fail):
    import grpc
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc

    closed, clients, probes = [], [], []
    channel = SimpleNamespace(close=lambda: closed.append("channel"))
    stub = object()
    monkeypatch.setenv("EMO_RUNTIME_TARGET", "127.0.0.1:59999")
    monkeypatch.setattr(service, "RuntimeService", lambda **_: pytest.fail("remote startup constructed embedded Runtime"))
    monkeypatch.setattr(grpc, "insecure_channel", lambda _, **_options: channel)
    monkeypatch.setattr(runtime_pb2_grpc, "RuntimeServiceStub", lambda _: stub)

    def client(**kwargs):
        clients.append(kwargs)
        if fail:
            raise OSError("client failed")
        return SimpleNamespace(
            deviceNetworkCommand=lambda *_: probes.append("info") or dict(projectId=""),
            listDeviceTypes=lambda: probes.append("types") or ({}, {}, {}),
            close=lambda: closed.append("client"),
        )

    monkeypatch.setattr(runtime_client, "RuntimeClient", client)
    if fail:
        with pytest.raises(OSError, match="client failed"):
            prepareRuntime(StartupTiming(perf_counter()), Event())
        assert closed == ["channel"]
    else:
        prepared = prepareRuntime(StartupTiming(perf_counter()), Event())
        assert callable(prepared.close) and probes == ["info", "types"]
        assert not closed
    from emo_master.clients.runtime.connection import ConnectionProfile
    assert clients == [dict(runtimeService=stub, ownedChannel=channel, runtimeTarget="127.0.0.1:59999",
                            connectionProfile=ConnectionProfile("127.0.0.1:59999"), deadlineMs=5000)]
