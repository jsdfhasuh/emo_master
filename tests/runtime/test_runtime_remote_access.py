from dataclasses import replace
import socket
from types import SimpleNamespace

import pytest

from emo_master.apps.operator_runtime.controller import ProductionRuntime, RuntimeInitializationError
from emo_master.apps.operator_runtime.settings import ListenerSettings, readListenerSettings
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.services.runtime_connection import connectCandidate
from emo_master.clients.runtime.connection import ConnectionProfile, createChannel
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb, runtime_pb2_grpc as rpc


def unusedPort():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def testDefaultListenerStaysLocalAndDynamicUntilNeeded(tmp_path):
    settings = ListenerSettings(host='192.0.2.10', port=50123)
    owner = ProductionRuntime(tmp_path, listenerSettings=settings)
    try:
        assert owner.server is None
        assert settings.listenHost == '127.0.0.1' and settings.listenPort == 0
        profile = owner.connectionProfile
        assert profile.target.startswith('127.0.0.1:')
        assert owner.server.port != 0 and owner.server.displaySubscriptions.perJobLimit == 4
        with createChannel(profile) as channel:
            info = rpc.RuntimeServiceStub(channel).GetRuntimeInfo(pb.RuntimeInfoRequest(), timeout=3)
        assert info.ok and info.runtime_instance_id == owner.runtime.runtimeInstanceId
        assert not owner.runtime.jobRepository.all()
    finally:
        owner.close()


def testEnabledListenerIsReachableBeforeLoadingAProjectAndCanBeSelected(tmp_path):
    port = unusedPort()
    owner = ProductionRuntime(tmp_path, listenerSettings=ListenerSettings(True, '127.0.0.1', port, 6))
    client = None
    try:
        assert owner.server is not None and owner.server.port == port
        assert owner.server.displaySubscriptions.perJobLimit == 6
        profile = ConnectionProfile(f'127.0.0.1:{port}', timeoutSeconds=2)
        client, info, snapshot, _types = connectCandidate(profile)
        assert client.connectionProfile is profile and client.deadlineMs == 2000
        assert info['runtimeInstanceId'] == owner.runtime.runtimeInstanceId and snapshot is None
        assert client.getRuntimeInfo().production_mode
        assert not owner.runtime.jobRepository.all()
    finally:
        if client:
            client.close()
        owner.close()


def testFixedPortCollisionFailsWithoutSilentlyChoosingAnotherPort(tmp_path):
    settings = ListenerSettings(True, '127.0.0.1', unusedPort())
    first = ProductionRuntime(tmp_path / 'first', listenerSettings=settings)
    try:
        with pytest.raises(RuntimeInitializationError, match=str(settings.port)):
            ProductionRuntime(tmp_path / 'second', listenerSettings=settings)
        assert first.server.port == settings.port
        with createChannel(first.connectionProfile) as channel:
            assert rpc.DisplayServiceStub(channel).Capabilities(pb.DisplayEmpty(), timeout=2).runtime_instance_id == first.runtime.runtimeInstanceId
    finally:
        first.close()


@pytest.mark.parametrize('changes', [dict(port=0), dict(port=65536), dict(host='bad:address'),
    dict(displaySubscriptionsPerJob=0), dict(displaySubscriptionsPerJob=17)])
def testListenerRejectsInvalidSettings(changes):
    with pytest.raises(ValueError):
        replace(ListenerSettings(), **changes)


def testProfileOptionsFollowNormalConnectionSwitch():
    first = RuntimeClient(SimpleNamespace(), runtimeTarget='localhost:50051')
    profile = ConnectionProfile('127.0.0.1:50052', timeoutSeconds=1.5, maxReceiveMessageBytes=12 * 1024 * 1024)
    second = RuntimeClient(SimpleNamespace(), connectionProfile=profile)
    try:
        first.switchConnection(second)
        assert first.connectionProfile is profile and first.displayAddress() == profile.target
        assert first.deadlineMs == 1500
        assert first._retiredTransports[0].displayAddress() == 'localhost:50051'
    finally:
        first.close()


def testChannelFactoryUsesProfileForSyncAndAsync(monkeypatch):
    from emo_master.clients.runtime import connection
    captured = []
    monkeypatch.setattr(connection.grpc, 'insecure_channel', lambda *args, **kwargs: captured.append((args, kwargs)))
    monkeypatch.setattr(connection.grpc.aio, 'insecure_channel', lambda *args, **kwargs: captured.append((args, kwargs)))
    profile = ConnectionProfile('localhost:50051', maxReceiveMessageBytes=11 * 1024 * 1024)
    createChannel(profile)
    createChannel(profile, asynchronous=True)
    assert all(args == (profile.target,) and kwargs['options'] == profile.channelOptions for args, kwargs in captured)


def testCliCanSaveListenerWithoutCreatingRuntime(tmp_path, monkeypatch, retainedQtApplication):
    from PySide2.QtCore import QSettings
    from emo_master.apps.operator_runtime import main as entry
    preferences = QSettings(str(tmp_path / 'listener.ini'), QSettings.IniFormat)
    monkeypatch.setattr(entry, 'QSettings', lambda *_: preferences)
    monkeypatch.setattr(entry, 'ProductionRuntime', lambda *a, **k: pytest.fail('save started Runtime'))
    assert entry.main(['--allow-lan', '--listen-host', '127.0.0.1', '--listen-port', '50055',
                       '--display-subscriptions-per-job', '5', '--save-listener-settings']) == 0
    assert readListenerSettings(preferences) == ListenerSettings(True, '127.0.0.1', 50055, 5)
    assert readListenerSettings(preferences, allowLan=False).listenPort == 0
    assert entry.main(['--local-only', '--save-listener-settings']) == 0
    assert not readListenerSettings(preferences).allowLan
