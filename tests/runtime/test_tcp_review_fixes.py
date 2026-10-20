"""Regression cases from the project TCP service implementation review."""
from copy import deepcopy
import json
import multiprocessing
import socket
import threading
import time

import pytest

from emo_master.apps.designer.services.runtime_client import RuntimeClient, RuntimeClientError
from emo_master.apps.runtime.communication.ipc import CommunicationProxy, WorkerBridge
from emo_master.apps.runtime.communication.pipe_io import sendBytes, receiveBytes
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.core.contracts.tcp_service import TcpServiceError
from tests.designer.test_project_communication_ui import spin, retire
from tests.runtime.test_tcp_service_integration import payload, load, runtime as runtime


@pytest.mark.parametrize("field,value", [("enabled", False), ("startupPolicy", "on_project_run")])
def testManagementRevisionIncludesEnableAndPolicy(runtime, tmp_path, field, value):
    initial = payload("manual")
    load(runtime, tmp_path, initial)
    client = RuntimeClient(runtime)
    before = client.communicationState("tcp-project")
    changed = deepcopy(initial["communication"])
    changed[field] = value
    after = client.controlCommunication("SYNC", before, config=changed)
    assert before["desiredRevision"] != after["desiredRevision"]
    assert before["configRevision"] == after["configRevision"]
    with pytest.raises(RuntimeClientError) as error:
        client.controlCommunication("SYNC", before, config=initial["communication"])
    assert error.value.code == "E_TCP_CONFIG_STALE"
    assert runtime.communicationManager.desired.model_dump() == changed


def testQueriesAndControlsFenceSameIdDifferentFile(runtime, tmp_path):
    first = load(runtime, tmp_path, payload("manual"))
    client = RuntimeClient(runtime)
    state = client.communicationState("tcp-project", projectPath=str(first))
    other = tmp_path / "copy.emoproj"
    other.write_bytes(first.read_bytes())
    with pytest.raises(RuntimeClientError) as error:
        client.communicationState("tcp-project", projectPath=str(other))
    assert error.value.code == "E_TCP_PROJECT_MISMATCH"
    state["projectPath"] = str(other)
    with pytest.raises(RuntimeClientError) as error:
        client.controlCommunication("START", state)
    assert error.value.code == "E_TCP_STALE_CONTEXT"
    assert runtime.communicationManager.state == "STOPPED"


def testDisableSaveGuardBlocksStartReloadAndForeignSync(runtime, tmp_path):
    initial = payload()
    path = load(runtime, tmp_path, initial)
    client = RuntimeClient(runtime)
    before = client.communicationState("tcp-project")
    prepared = client.controlCommunication("PREPARE_DISABLE", before, saveToken="owner")
    assert prepared["disableSavePending"] and prepared["enabled"]
    for action in ("START", "SYNC", "UNLOAD"):
        with pytest.raises(RuntimeClientError) as error:
            client.controlCommunication(action, before, config=initial["communication"])
        assert error.value.code == "E_TCP_SAVE_PENDING"
    assert not runtime.LoadProject(pb.LoadProjectRequest(project_path=str(path)), None).ok
    started = runtime.StartJob(pb.StartJobRequest(project_id="tcp-project", workflow_id="front", inputs_json='{"data":"NO"}'), None)
    assert not started.ok and "E_TCP_SAVE_PENDING" in started.message
    with pytest.raises(RuntimeClientError) as error:
        client.controlCommunication("ABORT_DISABLE", before, saveToken="other")
    assert error.value.code == "E_TCP_SAVE_TOKEN"
    disabled = deepcopy(initial["communication"])
    disabled["enabled"] = False
    committed = client.controlCommunication("SYNC", prepared, config=disabled, saveToken="owner")
    assert not committed["enabled"] and not committed["disableSavePending"]
    assert runtime.communicationManager.state == "STOPPED"


def testDisablePreparationAfterConcurrentStartCannotWrite(runtime, tmp_path):
    load(runtime, tmp_path, payload("manual"))
    client = RuntimeClient(runtime)
    before = client.communicationState("tcp-project")
    client.controlCommunication("START", before)
    with pytest.raises(RuntimeClientError) as error:
        client.controlCommunication("PREPARE_DISABLE", before, saveToken="late")
    assert error.value.code == "E_TCP_BUSY"
    assert not runtime.communicationManager.snapshot()["disableSavePending"]


@pytest.mark.parametrize("failure", [False, True])
def testDesignerDisableSaveIsProtectedUntilDiskOutcome(runtime, tmp_path, retainedQtApplication,
                                                      ownedDesignerWindow, monkeypatch, failure):
    value = payload("manual")
    path = load(runtime, tmp_path, value)
    client = RuntimeClient(runtime)
    window = ownedDesignerWindow(client)
    try:
        assert window.loadProjectDirectory(str(path), confirmed=True)
        c = window.communicationController
        spin(retainedQtApplication, lambda: c.state and c.thread is None)
        original = c.save
        blocked = []
        if failure:
            monkeypatch.setattr(window, "saveProjectToDirectory", lambda _: False)
        def save(config):
            other = RuntimeClient(runtime)
            with pytest.raises(RuntimeClientError) as error:
                other.controlCommunication("START", other.communicationState("tcp-project"))
            blocked.append(error.value.code)
            return original(config)
        c.save = save
        disabled = deepcopy(value["communication"])
        disabled["enabled"] = False
        assert "尚未写入" in c.saveSettings(disabled)
        spin(retainedQtApplication, lambda: c.thread is None and c.disableSave is None)
        assert blocked == ["E_TCP_SAVE_PENDING"]
        assert json.loads(path.read_text(encoding="utf-8"))["communication"]["enabled"] == failure
        assert runtime.communicationManager.desired.enabled == failure
        assert window._communicationProject()["config"]["enabled"] == failure
        assert not runtime.communicationManager.snapshot()["disableSavePending"]
    finally:
        retire(retainedQtApplication, window.communicationController)


def testSaveAsRequiresPathSynchronizationBeforeControl(runtime, tmp_path, retainedQtApplication, ownedDesignerWindow):
    initial = payload("manual")
    first = load(runtime, tmp_path, initial)
    client = RuntimeClient(runtime)
    window = ownedDesignerWindow(client)
    try:
        assert window.loadProjectDirectory(str(first), confirmed=True)
        c = window.communicationController
        spin(retainedQtApplication, lambda: c.state and c.thread is None)
        generation = c.state["projectLoadGeneration"]
        second = tmp_path / "copy.emoproj"
        client.controlCommunication("START", c.state)
        with socket.create_connection(("127.0.0.1", initial["communication"]["tcpServer"]["port"]), timeout=2) as peer:
            runtime.communicationManager.acquire("test", "tcp-server", "front", 1000, lambda: False)
            assert window.saveProjectToDirectory(str(second))
            assert peer.recv(1) == b""
        assert runtime.loadedProjectId == ""
        c.control("START")  # Even before the next poll, do not use cached A identity.
        assert runtime.communicationManager.state == "STOPPED"
        spin(retainedQtApplication, lambda: c.thread is None and "MISMATCH" in c.message)
        assert c.state is None and not c.panel.startButton.isEnabled()
        c.syncSaved()
        spin(retainedQtApplication, lambda: c.thread is None and c.state and not c.unsynced)
        assert runtime.loadedProjectFile == str(second)
        assert c.state["projectLoadGeneration"] != generation
    finally:
        retire(retainedQtApplication, window.communicationController)


def testProjectReplacementDoesNotTrustDisabledLocalDraft(runtime, tmp_path, retainedQtApplication, ownedDesignerWindow):
    initial = payload("manual")
    first = load(runtime, tmp_path, initial)
    client = RuntimeClient(runtime)
    window = ownedDesignerWindow(client)
    try:
        assert window.loadProjectDirectory(str(first), confirmed=True)
        client.controlCommunication("START", client.communicationState("tcp-project"))
        window.workflowStore.projectExtensions["communication"]["enabled"] = False
        other = deepcopy(initial)
        other["project"]["projectId"] = "unrelated"
        other["communication"]["enabled"] = False
        path = tmp_path / "other.emoproj"
        path.write_text(json.dumps(other), encoding="utf-8")
        assert window.loadProjectDirectory(str(path), confirmed=True)
        assert runtime.loadedProjectId == "" and runtime.communicationManager.state == "STOPPED"
        assert not runtime.communicationManager._threads
    finally:
        retire(retainedQtApplication, window.communicationController)


@pytest.mark.parametrize("cancel", [True, False])
def testRealPipeWriteCanBeCancelledOrExpireWithoutParentDrain(cancel):
    parent, child = multiprocessing.get_context("spawn").Pipe()
    event = threading.Event()
    proxy = CommunicationProxy(child, event)
    proxy.sendTimeoutMs = 150 if not cancel else 5000
    failures = []
    def send():
        try:
            proxy.call("send", dataBase64="A" * (4 * 1024 * 1024))
        except TcpServiceError as error:
            failures.append(error.code)
    thread = threading.Thread(target=send)
    thread.start()
    try:
        assert parent.poll(2)
        if cancel:
            event.set()
        thread.join(2)
        assert not thread.is_alive(), "cancellation must not need a reader to drain the pipe"
        assert failures == ["E_TCP_OUTCOME_UNKNOWN"] and proxy.closed
    finally:
        event.set()
        parent.close()
        thread.join(3)
        proxy.close()
        assert not thread.is_alive()


def testLargePipeMessageRoundTripAndNativeFraming():
    parent, child = multiprocessing.get_context("spawn").Pipe()
    data = b"01234567" * (512 * 1024)
    errors = []
    def write():
        try:
            sendBytes(child, data, lambda: False, time.monotonic() + 5)
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=write)
    thread.start()
    try:
        assert receiveBytes(parent, len(data), lambda: False, time.monotonic() + 5) == data
        thread.join(2)
        assert not thread.is_alive() and not errors
        child.send_bytes(b"native")
        assert receiveBytes(parent, 100, lambda: False, time.monotonic() + 1) == b"native"
        sendBytes(child, b"reply", lambda: False, time.monotonic() + 1)
        assert parent.recv_bytes(100) == b"reply"
    finally:
        parent.close()
        child.close()
        thread.join(2)


def testExpiredDispatchNeverTouchesTransport(runtime, tmp_path):
    value = payload("manual")
    load(runtime, tmp_path, value)
    manager = runtime.communicationManager
    manager.start()
    parent, child = multiprocessing.get_context("spawn").Pipe()
    bridge = WorkerBridge(manager, "job", parent, threading.Event())
    try:
        with socket.create_connection(("127.0.0.1", value["communication"]["tcpServer"]["port"]), timeout=1):
            session = manager.acquire("job", "tcp-server", "front", 1000, lambda: False)
            request = dict(protocolVersion="1.0", sequence=1, action="send", session=session,
                           dataBase64="QQ==", deadlineNs=time.monotonic_ns() - 1)
            reply = bridge.dispatch(json.dumps(request).encode())
            assert not reply["ok"] and reply["code"] == "E_TCP_REQUEST_EXPIRED"
            assert not [r for r, _ in manager._records if r["kind"] == "tx"]
    finally:
        parent.close()
        child.close()


@pytest.mark.parametrize("lostAction", ["PREPARE_DISABLE", "SYNC"])
def testLostDisableReceiptCanBeReconciledWithoutEnabling(runtime, tmp_path, retainedQtApplication,
                                                       ownedDesignerWindow, monkeypatch, lostAction):
    initial = payload("manual")
    path = load(runtime, tmp_path, initial)
    client = RuntimeClient(runtime)
    window = ownedDesignerWindow(client)
    try:
        assert window.loadProjectDirectory(str(path), confirmed=True)
        c = window.communicationController
        spin(retainedQtApplication, lambda: c.state and c.thread is None)
        original = client.controlCommunication
        injected = []
        def control(action, *args, **kwargs):
            state = original(action, *args, **kwargs)
            if action == lostAction and not injected:
                injected.append(action)
                raise RuntimeClientError("UNAVAILABLE", "injected lost reply after mutation")
            return state
        monkeypatch.setattr(client, "controlCommunication", control)
        disabled = deepcopy(initial["communication"])
        disabled["enabled"] = False
        assert "尚未写入" in c.saveSettings(disabled)
        spin(retainedQtApplication, lambda: c.thread is None and bool(injected))
        if lostAction == "PREPARE_DISABLE":
            assert c.disableSave is None
            assert json.loads(path.read_text())["communication"]["enabled"]
            assert runtime.communicationManager.desired.enabled
        else:
            assert c.disableSave is not None
            assert not runtime.communicationManager.desired.enabled
            c.syncSaved()
            spin(retainedQtApplication, lambda: c.thread is None and c.disableSave is None)
        assert not runtime.communicationManager.snapshot()["disableSavePending"]
        assert runtime.communicationManager.state == "STOPPED"
    finally:
        retire(retainedQtApplication, window.communicationController)


def testClosingDuringDisablePreparationAbortsWithoutSaving(runtime, tmp_path, retainedQtApplication,
                                                          ownedDesignerWindow, monkeypatch):
    initial = payload("manual")
    path = load(runtime, tmp_path, initial)
    client = RuntimeClient(runtime)
    window = ownedDesignerWindow(client)
    prepared, release = threading.Event(), threading.Event()
    try:
        assert window.loadProjectDirectory(str(path), confirmed=True)
        c = window.communicationController
        spin(retainedQtApplication, lambda: c.state and c.thread is None)
        original = client.controlCommunication
        def control(action, *args, **kwargs):
            state = original(action, *args, **kwargs)
            if action == "PREPARE_DISABLE":
                prepared.set()
                assert release.wait(3)
            return state
        monkeypatch.setattr(client, "controlCommunication", control)
        disabled = deepcopy(initial["communication"])
        disabled["enabled"] = False
        c.saveSettings(disabled)
        assert prepared.wait(2)
        assert not c.close()
        release.set()
        retire(retainedQtApplication, c)
        assert not runtime.communicationManager.snapshot()["disableSavePending"]
        assert json.loads(path.read_text())["communication"]["enabled"]
        assert runtime.communicationManager.desired.enabled
    finally:
        release.set()
        retire(retainedQtApplication, window.communicationController)


def testPipeReadDeadlineWithoutReplyAndMessageLimit():
    parent, child = multiprocessing.get_context("spawn").Pipe()
    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            receiveBytes(parent, 1024, lambda: False, started + .1)
        assert time.monotonic() - started < 1
        child.send_bytes(b"12345")
        with pytest.raises(OSError, match="字节上限"):
            receiveBytes(parent, 4, lambda: False, time.monotonic() + 1)
    finally:
        parent.close()
        child.close()


def testOldManagementProtocolIsRejectedRatherThanIgnoringIdentity(runtime):
    result = runtime.GetCommunicationState(pb.CommunicationRequest(protocol_version="1.0"), None)
    assert not result.ok and result.code == "E_TCP_UNSUPPORTED"


def testReplacementCannotTreatProtocolMismatchAsResourceAbsence():
    class OldTcpRuntime:
        def GetCommunicationState(self, request, context):
            return pb.CommunicationReply(ok=False, code="E_TCP_UNSUPPORTED", message="old TCP protocol")
    client = RuntimeClient(OldTcpRuntime())
    with pytest.raises(RuntimeClientError) as error:
        client.releaseCommunicationForReplacement("copy.emoproj", "project")
    assert error.value.code == "E_TCP_PROTOCOL_MISMATCH"


@pytest.mark.parametrize("operation", ["open", "save_as"])
def testFailedReleaseKeepsOriginalDesignerAndRuntimeIdentity(runtime, tmp_path, retainedQtApplication,
                                                           ownedDesignerWindow, monkeypatch, operation):
    from PySide2.QtWidgets import QMessageBox
    warnings = []
    # This fault-injection test checks the emitted error, not a modal event loop.
    monkeypatch.setattr(QMessageBox, "warning", lambda owner, title, text: warnings.append(text))
    initial = payload("manual")
    first = load(runtime, tmp_path, initial)
    client = RuntimeClient(runtime)
    window = ownedDesignerWindow(client)
    try:
        assert window.loadProjectDirectory(str(first), confirmed=True)
        client.controlCommunication("START", client.communicationState("tcp-project"))
        second = tmp_path / "copy.emoproj"
        def failStop():
            raise TcpServiceError("E_TCP_RELEASE_FAILED", "injected release failure")
        with monkeypatch.context() as patch:
            patch.setattr(runtime.communicationManager, "stop", failStop)
            if operation == "open":
                second.write_bytes(first.read_bytes())
                assert not window.loadProjectDirectory(str(second), confirmed=True)
            else:
                assert not window.saveProjectToDirectory(str(second))
                assert len(warnings) == 1 and "副本已写入" in warnings[0]
                assert second.is_file()
            assert window.projectController.currentProjectFile == first
            assert window.loadedProjectPath == str(first)
            assert runtime.loadedProjectFile == str(first)
            assert runtime.communicationManager.state == "LISTENING"
    finally:
        retire(retainedQtApplication, window.communicationController)
