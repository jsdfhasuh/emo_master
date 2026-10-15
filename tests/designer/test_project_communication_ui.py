from copy import deepcopy
import json
import socket
import threading
import time

import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.ui.communication_panel import CommunicationPanel, CommunicationSettingsDialog, summary
from tests.core.project.test_project_communication import project, settings
from tests.runtime.test_managed_tcp import freePort


def spin(app, predicate, seconds=6):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


def retire(app, controller):
    spin(app, controller.close)


def testSettingsDraftRetainsDisabledValuesAndShowsLocalErrors(retainedQtApplication):
    values = settings().model_dump()
    saved = []
    dialog = CommunicationSettingsDialog("长项目名" * 8, values, lambda value: saved.append(value))
    try:
        dialog.enabled.setChecked(False)
        assert not dialog.fields.isEnabled() and dialog.draft()["tcpServer"] == values["tcpServer"]
        dialog.enabled.setChecked(True)
        dialog.addChannel(dict(channelId="rear", name="后", allowedIps=["127.0.0.1"]))
        dialog.saveDraft()
        assert not saved and "唯一" in dialog.error.text()
        dialog.channels.removeRow(1)
        dialog.saveDraft()
        assert len(saved) == 1 and saved[0] == values
    finally:
        dialog.close()
        dialog.deleteLater()
        retainedQtApplication.processEvents()


@pytest.mark.parametrize("state,count,expected", [("LISTENING", 0, "等待客户端"), ("LISTENING", 1, "部分连接"),
    ("LISTENING", 2, "全部连接"), ("STOPPED", 0, "未启动")])
def testStatusDistinguishesEnabledListenerAndClients(state, count, expected):
    assert expected in summary(dict(projectId="project", enabled=True, state=state,
        channels=[dict(connected=i < count) for i in range(2)]))
    assert summary(None) == "TCP 状态未知"
    assert summary(dict(projectId="p", enabled=False, state="STOPPED")) == "TCP 未启用"


def testNativeDesignerSaveSyncNoLoadAndPendingConfig(retainedQtApplication, ownedDesignerWindow, tmp_path, monkeypatch):
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    client = RuntimeClient(service)
    path = tmp_path / "test.emoproj"
    initial = project()
    initial["workflows"]["main"]["nodes"] = [dict(nodeId="input", kind="workflow_input"), dict(nodeId="output", kind="workflow_output")]
    path.write_text(json.dumps(initial), encoding="utf-8")
    window = ownedDesignerWindow(client)
    try:
        assert window.loadProjectDirectory(str(path), confirmed=True), window.projectController.lastLoadError
        window.openCommunicationPanel()
        controller = window.communicationController
        spin(retainedQtApplication, lambda: controller.state is not None and controller.state.get("projectId") == "tcp-project" and controller.thread is None)
        calls = []
        originalLoad = service.LoadProject
        monkeypatch.setattr(service, "LoadProject", lambda *args: (calls.append(1), originalLoad(*args))[1])
        config = settings(freePort()).model_dump()
        assert "尚未写入" in controller.saveSettings(config)
        spin(retainedQtApplication, lambda: not controller.unsynced and controller.thread is None)
        assert calls == []
        assert json.loads(path.read_text(encoding="utf-8"))["communication"] == config
        assert service.communicationManager.state == "STOPPED"
        controller.control("START")
        spin(retainedQtApplication, lambda: controller.thread is None and controller.state and controller.state["state"] == "LISTENING")
        with socket.create_connection(("127.0.0.1", config["tcpServer"]["port"]), timeout=3):
            ref = service.communicationManager.acquire("test", "tcp-server", "front", 1000, lambda: False)
            changed = deepcopy(config)
            changed["tcpServer"]["port"] = freePort()
            assert "尚未写入" in controller.saveSettings(changed)
            spin(retainedQtApplication, lambda: controller.thread is None and controller.state and controller.state["pending"])
            assert calls == [] and service.communicationManager._peers["front"].generation == ref["connectionGeneration"]
            assert f"{config['tcpServer']['port']} → {changed['tcpServer']['port']}" in controller.panel.notice.text()
            disabled = deepcopy(changed)
            disabled["enabled"] = False
            assert "停止服务" in controller.saveSettings(disabled)
            assert json.loads(path.read_text(encoding="utf-8"))["communication"]["enabled"]
        retire(retainedQtApplication, controller)
    finally:
        retire(retainedQtApplication, window.communicationController)
        service.close()


def testObserverDoesNotBlockQtAndRejectsOldProjectResult(retainedQtApplication):
    from emo_master.ui.communication_controller import CommunicationController
    started, release = threading.Event(), threading.Event()
    current = dict(projectId="first", name="First", path="first", token="1", config=settings().model_dump())
    class Client:
        def communicationState(self, projectId, after=0, cancellation=None, projectPath=""):
            started.set()
            release.wait(2)
            return dict(protocolVersion="1.0", projectId=projectId, enabled=True, state="LISTENING", channels=[],
                runtimeInstanceId="runtime", projectLoadGeneration="old", desired=current["config"], effective=current["config"],
                serviceGeneration=1, records=[], sequence=0)
        def closeDisplayOwner(self, owner):
            pass
    panel = CommunicationPanel()
    observer = CommunicationController(Client(), panel, lambda: current)
    try:
        observer.poll()
        assert started.wait(.5)
        current.update(projectId="second", path="", token="2")
        observer.poll()
        assert observer.state is None and not observer.history
        release.set()
        spin(retainedQtApplication, lambda: observer.thread is None and observer.state is not None)
        assert observer.state["projectId"] == "" and "先打开" in panel.stateLabel.text()
    finally:
        release.set()
        retire(retainedQtApplication, observer)
        panel.close()
        panel.deleteLater()


def testSaveFailureDoesNotSyncRuntime(retainedQtApplication):
    from emo_master.ui.communication_controller import CommunicationController
    class Client:
        calls = 0
        def closeDisplayOwner(self, owner):
            pass
        def controlCommunication(self, *args, **kwargs):
            self.calls += 1
    client = Client()
    panel = CommunicationPanel()
    def provider():
        return dict(projectId="p", name="P", path="", token="p", config=settings().model_dump())
    observer = CommunicationController(client, panel, provider, lambda config: "磁盘保存失败")
    try:
        assert observer.saveSettings(settings().model_dump()) == "磁盘保存失败"
        assert client.calls == 0 and observer.thread is None
    finally:
        retire(retainedQtApplication, observer)
        panel.close()
        panel.deleteLater()


def testSettingsCannotBeAppliedToAnotherProjectBeforeNextPoll(retainedQtApplication):
    from emo_master.ui.communication_controller import CommunicationController
    panel = CommunicationPanel()
    current = dict(projectId="one", name="One", path="", token="one", config=settings().model_dump())
    saves = []
    observer = CommunicationController(object(), panel, lambda: current, lambda value: saves.append(value))
    try:
        observer.settings()
        dialog = observer.settingsDialog
        current.update(projectId="two", token="two")
        dialog.saveDraft()
        assert "所属项目已改变" in dialog.error.text() and saves == []
    finally:
        retire(retainedQtApplication, observer)
        panel.close()
        panel.deleteLater()


@pytest.mark.parametrize("code", ["E_TCP_PROJECT_MISMATCH", "E_TCP_UNSUPPORTED", "UNAVAILABLE"])
def testDisabledProjectIsNotAConnectionAlarmButTransportFailureStaysUnknown(retainedQtApplication, code):
    from emo_master.ui.communication_controller import CommunicationController
    from emo_master.core.project.communication import CommunicationSettings
    class Client:
        def communicationState(self, *args, **kwargs):
            raise RuntimeClientError(code, "not available")
    panel = CommunicationPanel()
    current = dict(projectId="disabled", path="disabled.emoproj", token="disabled", name="Disabled",
                   config=CommunicationSettings().model_dump())
    observer = CommunicationController(Client(), panel, lambda: current)
    try:
        observer.poll()
        spin(retainedQtApplication, lambda: observer.thread is None and bool(observer.message))
        if code == "UNAVAILABLE":
            assert observer.state is None and panel.stateLabel.text() == "TCP 状态未知"
        else:
            assert observer.state["localOnly"] and panel.stateLabel.text() == "TCP 未启用"
            assert "尚无此项目的 Runtime 状态确认" in panel.identityLabel.text()
        assert not panel.startButton.isEnabled() and panel.channels.rowCount() == 0
    finally:
        retire(retainedQtApplication, observer)
        panel.close()
        panel.deleteLater()


def testEditableProjectReplacementReleasesOnlyAfterValidDifferentCandidate(retainedQtApplication, ownedDesignerWindow, tmp_path):
    from emo_master.core.project.migration import migrateProjectPayload
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    client = RuntimeClient(service)
    initial = migrateProjectPayload(project(), enableCommunication=True)
    initial["workflows"]["main"]["nodes"] = [dict(nodeId="input", kind="workflow_input"), dict(nodeId="output", kind="workflow_output")]
    initial["communication"] = settings(freePort()).model_dump()
    first = tmp_path / "first.emoproj"
    first.write_text(json.dumps(initial), encoding="utf-8")
    window = ownedDesignerWindow(client)
    try:
        assert window.loadProjectDirectory(str(first), confirmed=True), window.projectController.lastLoadError
        assert client.loadProject(str(first)).ok
        client.controlCommunication("START", client.communicationState("tcp-project"))
        with socket.create_connection(("127.0.0.1", initial["communication"]["tcpServer"]["port"]), timeout=3) as robot:
            ref = service.communicationManager.acquire("test", "tcp-server", "front", 1000, lambda: False)
            assert window.loadProjectDirectory(str(first), confirmed=True)
            assert service.communicationManager._peers["front"].generation == ref["connectionGeneration"]
            invalid = tmp_path / "invalid.emoproj"
            invalid.write_text("{bad", encoding="utf-8")
            assert not window.loadProjectDirectory(str(invalid), confirmed=True)
            assert service.communicationManager.state == "LISTENING" and service.loadedProjectId == "tcp-project"
            other = deepcopy(initial)
            other["project"]["projectId"] = "other-project"
            other["communication"]["enabled"] = False
            second = tmp_path / "second.emoproj"
            second.write_text(json.dumps(other), encoding="utf-8")
            assert window.loadProjectDirectory(str(second), confirmed=True), window.projectController.lastLoadError
            assert service.loadedProjectId == "" and service.communicationManager.state == "STOPPED"
            assert not service.communicationManager._threads and not service.communicationManager._peers
            assert window._currentProjectId() == "other-project"
            assert robot.recv(1) == b""
        retire(retainedQtApplication, window.communicationController)
    finally:
        retire(retainedQtApplication, window.communicationController)
        service.close()
