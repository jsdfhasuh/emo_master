from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Event
from time import perf_counter
from types import SimpleNamespace

import grpc
import pytest
from PySide2.QtCore import QSettings, QTimer

from emo_master.apps.designer.services import runtime_client
from emo_master.apps.designer.services.runtime_client import RuntimeClientError
from emo_master.apps.designer.services.runtime_connection import connectCandidate, validateRuntimeTarget
from emo_master.apps.designer.startup import StartupTiming, prepareRuntime
from emo_master.apps.designer.startup_settings import initialStartupOptions
from emo_master.apps.runtime.grpc_server import service
from emo_master.apps.runtime.main import createRuntimeServer


@pytest.mark.parametrize("target", ["localhost:50051", "127.0.0.1:50051", "[::1]:50051", " 主机名:50051 "])
def test_valid_runtime_addresses_use_the_shared_validator(target):
    assert validateRuntimeTarget(target) == target.strip()


@pytest.mark.parametrize("target", ["", "localhost", "http://localhost:50051", "localhost:0", "localhost:65536",
                                    "user@localhost:50051", "localhost:50051/path", "localhost:50051?x=1",
                                    "localhost:50051#part", "local host:50051"])
def test_invalid_addresses_are_rejected_before_creating_transport(target, monkeypatch):
    monkeypatch.setattr(grpc, "insecure_channel", lambda _: pytest.fail("invalid target created a channel"))
    with pytest.raises(ValueError, match="主机:端口"):
        connectCandidate(target)


def test_remote_startup_uses_readonly_probe_without_local_runtime_or_directory(tmp_path, monkeypatch):
    remote = service.RuntimeService(dbPath=tmp_path / "server/runtime.db")
    server, port, _ = createRuntimeServer(port=0, runtimeService=remote)
    server.start()
    root = tmp_path / "unused-local"
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    options = replace(initialStartupOptions(store, environment={}).withDataRoot(root),
                      mode="remote", runtimeTarget=f"127.0.0.1:{port}")
    monkeypatch.setattr(service, "RuntimeService", lambda **_: pytest.fail("created local Runtime"))
    client = None
    try:
        client = prepareRuntime(StartupTiming(perf_counter()), Event(), options=options)
        assert client._ownedRuntimeService is None and client._runtimeTarget == options.runtimeTarget
        assert client.deadlineMs == 5000
        assert client.deviceNetworkCommand('', '', 'info')['runtimeInstanceId'] == remote.runtimeInstanceId
        assert not remote.jobRepository.all() and not remote.loadedProjectId
        assert not root.exists()
    finally:
        if client is not None:
            client.close()
        server.stop(0).wait(5)
        remote.close()


def test_connection_refused_closes_candidate_and_returns_a_retryable_error(monkeypatch):
    pool = ThreadPoolExecutor(1)
    server = grpc.server(pool)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    server.stop(0).wait(5)
    pool.shutdown(wait=True)
    original = runtime_client.RuntimeClient
    clients = []
    def makeClient(**kwargs):
        client = original(**kwargs)
        clients.append(client)
        return client
    monkeypatch.setattr(runtime_client, "RuntimeClient", makeClient)
    with pytest.raises(RuntimeClientError):
        connectCandidate(f"127.0.0.1:{port}")
    assert len(clients) == 1 and clients[0]._closed


def test_cancel_between_readonly_calls_closes_candidate_without_later_requests(monkeypatch):
    cancelled, calls = Event(), []
    def info(*_):
        calls.append("info")
        cancelled.set()
        return dict(projectId="existing")
    client = SimpleNamespace(deviceNetworkCommand=info, close=lambda: calls.append("closed"),
                             getOnlineProjectSnapshot=lambda: pytest.fail("request ran after cancellation"),
                             listDeviceTypes=lambda: pytest.fail("request ran after cancellation"))
    monkeypatch.setattr(runtime_client, "RuntimeClient", lambda **_: client)
    with pytest.raises(CancelledError):
        connectCandidate("127.0.0.1:50051", cancelled=cancelled)
    assert calls == ["info", "closed"]


@pytest.mark.parametrize("replyAfterCancel", [True, False], ids=["reply", "deadline"])
def test_cancel_during_real_rpc_closes_startup_without_further_requests(
        tmp_path, monkeypatch, designerApplication, isolatedDesignerSettings, replyAfterCancel):
    from PySide2 import QtWidgets
    from emo_master.apps.designer import main
    from emo_master.apps.designer.ui import main_window
    from emo_master.apps.designer.ui.startup_dialog import DesignerStartupDialog
    from emo_master.apps.designer.ui.startup_settings_dialog import DesignerStartupSettingsDialog
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2, runtime_pb2_grpc

    started, release, rpcEnded = Event(), Event(), Event()
    methods, requests, deadlines = [], [], []

    class RecordRequests(grpc.ServerInterceptor):
        def intercept_service(self, continuation, details):
            methods.append(details.method.rsplit('/', 1)[-1])
            return continuation(details)

    class WaitingRuntime(runtime_pb2_grpc.RuntimeServiceServicer):
        def DeviceNetworkCommand(self, request, context):
            requests.append(request.action)
            deadlines.append(context.time_remaining())
            context.add_callback(rpcEnded.set)
            started.set()
            if replyAfterCancel and release.wait(7):
                return runtime_pb2.ProjectConfigurationReply(ok=True, result_json=json.dumps(
                    dict(runtimeInstanceId='temporary-runtime', projectId='existing-project')))
            rpcEnded.wait(7)
            context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, 'probe timeout')

    pool = ThreadPoolExecutor(1, thread_name_prefix='startup-cancel-test')
    server = grpc.server(pool, interceptors=[RecordRequests()])
    runtime_pb2_grpc.add_RuntimeServiceServicer_to_server(WaitingRuntime(), server)
    port = server.add_insecure_port('127.0.0.1:0')
    server.start()
    root = tmp_path / 'unused-local'
    monkeypatch.setenv('EMO_RUNTIME_TARGET', f'127.0.0.1:{port}')
    monkeypatch.setenv('EMO_RUNTIME_DATA_DIR', str(root))
    monkeypatch.setattr(service, 'RuntimeService', lambda **_: pytest.fail('created local Runtime'))
    store = QSettings(str(isolatedDesignerSettings), QSettings.IniFormat)
    store.setValue('startup/mode', 'local')
    store.setValue('startup/runtimeTarget', 'previous:50051')
    previous = {key: store.value(key) for key in store.allKeys()}
    clients, channels, selections, dialogs = [], [], [], []
    originalClient = runtime_client.RuntimeClient

    def makeClient(**kwargs):
        client = originalClient(**kwargs)
        clients.append(client)
        channels.append(kwargs['ownedChannel'])
        return client

    def select(dialog):
        selections.append(dialog.initialOptions)
        assert len(selections) == 1, 'cancelled startup returned to settings'
        dialog.accept()
        return dialog._selection

    def cancel():
        if not started.is_set():
            return
        for widget in designerApplication.topLevelWidgets():
            if isinstance(widget, DesignerStartupDialog) and widget.isVisible():
                dialogs.append(widget)
                widget.cancelButton.click()
                release.set()
                timer.stop()

    monkeypatch.setattr(runtime_client, 'RuntimeClient', makeClient)
    monkeypatch.setattr(QtWidgets, 'QApplication', lambda _: designerApplication)
    monkeypatch.setattr(DesignerStartupSettingsDialog, 'execSelection', select)
    monkeypatch.setattr(main_window, 'MainWindow', lambda *_a, **_k: pytest.fail('editor built after cancel'))
    monkeypatch.setattr(main, '_showStartupError', lambda *_: pytest.fail('error dialog shown after cancel'))
    quitPolicy = designerApplication.quitOnLastWindowClosed()
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(cancel)
    timer.start()
    try:
        main.runDesigner()
        assert len(dialogs) == 1 and dialogs[0].cancelled.is_set()
        assert not dialogs[0]._thread.is_alive()
        assert methods == ['DeviceNetworkCommand'] and requests == ['info']
        assert deadlines and 0 < deadlines[0] <= 5.1
        assert len(clients) == 1 and clients[0]._closed and clients[0]._ownedChannel is None
        with pytest.raises(ValueError, match='closed'):
            channels[0].unary_unary('/startup-test/closed')(b'', timeout=.1)
        assert not root.exists()
        assert {key: store.value(key) for key in store.allKeys()} == previous
        assert designerApplication.quitOnLastWindowClosed() is quitPolicy
    finally:
        timer.stop()
        release.set()
        server.stop(0).wait(5)
        pool.shutdown(wait=True)
        for client in clients:
            client.close()
    assert rpcEnded.is_set()


def test_readonly_probe_uses_five_second_deadline_and_closes_after_timeout(monkeypatch):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc

    observed = []
    class SlowRuntime(runtime_pb2_grpc.RuntimeServiceServicer):
        def DeviceNetworkCommand(self, _request, context):
            observed.append(context.time_remaining())
            cancelled = Event()
            context.add_callback(cancelled.set)
            cancelled.wait(7)
            context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "probe timeout")

    pool = ThreadPoolExecutor(1)
    server = grpc.server(pool)
    runtime_pb2_grpc.add_RuntimeServiceServicer_to_server(SlowRuntime(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    clients, original = [], runtime_client.RuntimeClient
    def makeClient(**kwargs):
        client = original(**kwargs)
        clients.append(client)
        return client
    monkeypatch.setattr(runtime_client, "RuntimeClient", makeClient)
    try:
        with pytest.raises(RuntimeClientError) as caught:
            connectCandidate(f"127.0.0.1:{port}")
        assert "DEADLINE_EXCEEDED" in str(caught.value.code)
        assert observed and 0 < observed[0] <= 5.1
        assert clients[0]._closed
    finally:
        server.stop(0).wait(5)
        pool.shutdown(wait=True)
