from types import SimpleNamespace

from PySide2.QtWidgets import QDialogButtonBox

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.runtime_connection import RuntimeConnectionDialog, connectCandidate
from tests.designer.test_device_workspace import windowFor, waitUntil
from tests.core.project.test_devices import declaration, definition  # noqa: F401


def testConnectionDialogProbesBeforeSwitchAndKeepsDraft(ownedDesignerWindow, tmp_path, definition, declaration, monkeypatch):  # noqa: F811
    from emo_master.apps.designer.ui import runtime_connection
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    before = window.pageCoordinator.session.payload()
    switched, closed = [], []
    candidate = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(runtime_connection, 'connectCandidate', lambda target: (candidate,
        dict(runtimeInstanceId='other-instance', projectId='other-project'),
        dict(documentRevision=3, executionRevision=2), {'type': declaration}))
    window.runtimeClient.switchConnection = lambda value: switched.append(value)
    dialog = RuntimeConnectionDialog(window.pageCoordinator)
    dialog.show()
    dialog.target.setText('127.0.0.1:40000')
    dialog.buttons.button(QDialogButtonBox.Ok).click()
    waitUntil(lambda: dialog.pending is None)
    assert not switched and 'other-instance' in dialog.status.text() and '在线工程与本地不同' in dialog.status.text()
    dialog.buttons.button(QDialogButtonBox.Ok).click()
    assert switched == [candidate] and not closed and not dialog.isVisible(), dialog.status.text()
    assert window.pageCoordinator.session.payload() == before


def testFailedProbeKeepsOriginalConnection(ownedDesignerWindow, tmp_path, definition, declaration, monkeypatch):  # noqa: F811
    from emo_master.apps.designer.ui import runtime_connection
    window, _ = windowFor(ownedDesignerWindow, tmp_path, definition, declaration)
    original = window.runtimeClient
    def failed(_target):
        raise ValueError('simulated connection refused')
    monkeypatch.setattr(runtime_connection, 'connectCandidate', failed)
    dialog = RuntimeConnectionDialog(window.pageCoordinator)
    dialog.show()
    dialog.target.setText('127.0.0.1:40000')
    dialog.buttons.button(QDialogButtonBox.Ok).click()
    waitUntil(lambda: dialog.pending is None)
    assert window.runtimeClient is original and '原连接未改变' in dialog.status.text()
    dialog.reject()


def testRealTransportSwitchKeepsClientReferencesAndOldEmbeddedOwner(tmp_path):
    from emo_master.apps.runtime.grpc_server.service import RuntimeService
    from emo_master.apps.runtime.main import createRuntimeServer
    old = RuntimeService(dbPath=tmp_path / 'old' / 'runtime.db', workspaceRoot=tmp_path / 'old' / 'jobs')
    remote = RuntimeService(dbPath=tmp_path / 'new' / 'runtime.db', workspaceRoot=tmp_path / 'new' / 'jobs')
    server, port, _ = createRuntimeServer(port=0, runtimeService=remote)
    server.start()
    client = RuntimeClient(old, ownedRuntimeService=old)
    try:
        before = client.runtimeScope
        candidate, info, snapshot, types = connectCandidate(f'127.0.0.1:{port}')
        assert snapshot is None and types
        client.switchConnection(candidate)
        assert client.runtimeScope != before and not old._closing
        assert client.deviceNetworkCommand('', '', 'info')['runtimeInstanceId'] == info['runtimeInstanceId'] == remote.runtimeInstanceId
        assert candidate._closed
    finally:
        client.close()
        server.stop(0).wait(5)
        remote.close()
    assert old._closing
