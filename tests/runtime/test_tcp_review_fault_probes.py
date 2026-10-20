"""Regression assertions for the second TCP review and its repairs."""
from copy import deepcopy
from contextlib import contextmanager
import errno
import json
import socket
import threading
import time

import pytest

from emo_master.clients.runtime.communication import CommunicationClient, CommunicationClientError
from emo_master.core.contracts.tcp_service import TcpServiceError
from emo_master.ui.communication_controller import CommunicationController
from emo_master.ui.communication_panel import CommunicationPanel
from tests.designer.test_project_communication_ui import retire, spin
from tests.runtime.tcp_port_probe import assertTcpPortReleased
from tests.runtime.test_managed_tcp import freePort, waitUntil, manager as manager, connect, acquire
from tests.runtime.test_tcp_external_client import rig as rig
from tests.runtime.test_tcp_service_integration import payload


def testPortProbeRejectsLiveListenerAndAcceptsClosedDescriptors(manager):
    manager.start()
    listener = manager._listener
    port = manager.effective.tcpServer.port
    with connect(manager) as client:
        acquire(manager)
        accepted = manager._peers["front"].sock
        threads = tuple(manager._threads)
        with pytest.raises(OSError) as busy:
            assertTcpPortReleased(port)
        assert busy.value.errno in {
            errno.EADDRINUSE, errno.EACCES, getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE)}
        manager.stop()
        assert listener.fileno() == accepted.fileno() == -1
        assert not manager._threads and not manager._workers and not manager._peers
        assert manager._listener is None and not manager.snapshot()["ownsResources"]
        assert not any(thread.is_alive() for thread in threads)
        assert client.recv(1) == b""
    # The server actively closed this connection. POSIX TIME_WAIT is expected;
    # a replacement must use the same bind contract as the production server.
    assertTcpPortReleased(port)


def testListenerFailureCanRestartWithoutBindingOverOwnedSocket(rig, monkeypatch):
    """Inject accept OSError; failure closes the actual socket before restart."""
    rig.load(payload("manual"))
    enter, fail = threading.Event(), threading.Event()

    class AcceptFailure:
        def accept(self):
            enter.set()
            assert fail.wait(5)
            raise OSError("injected accept failure")

    original = rig.manager._accept
    with monkeypatch.context() as patch:
        patch.setattr(rig.manager, "_accept", lambda listener: original(AcceptFailure()))
        rig.control("START")
        assert enter.wait(2)
        fail.set()
        waitUntil(lambda: rig.manager.state == "FAILED" and not rig.manager._threads)
    failed = rig.state()
    assert not failed["ownsResources"] and rig.manager._listener is None
    with pytest.raises(OSError):
        with socket.create_connection(("127.0.0.1", rig.port), timeout=.3):
            pytest.fail("Failed listener still accepts TCP handshakes")
    rig.control("START")
    client = rig.client()
    rig.terminal(rig.startJob(data="RECOVERED"))
    client.received(b"RECOVERED")
    rig.notes.update(injected="accept OSError", afterFailure=failed, directRestart=rig.state()["state"])


@pytest.mark.parametrize("boundary", ["restart", "reconnect", "project-replacement"])
def testOldSendCompletionCannotOverwriteNewGenerationStatus(rig, monkeypatch, boundary):
    value = payload("manual")
    rig.load(value)
    rig.control("START")
    client = rig.client()
    old = rig.manager.acquire("old-job", "tcp-server", "front", 1000, lambda: False)
    peer = rig.manager._peers["front"]
    originalLock = peer.sendLock
    completing, release = threading.Event(), threading.Event()

    class GatedFinalization:
        def acquire(self, **kwargs):
            return originalLock.acquire(**kwargs)

        def release(self):
            originalLock.release()
            completing.set()
            assert release.wait(10)

    monkeypatch.setattr(peer, "sendLock", GatedFinalization())
    replies = []
    sender = threading.Thread(target=lambda: replies.append(rig.manager.send(
        "old-job", "old-send", old, b"OLD", lambda: False)), name="review-delayed-send")
    sender.start()
    try:
        assert completing.wait(3)
        client.received(b"OLD")
        if boundary == "reconnect":
            client.command("close")
            waitUntil(lambda: "front" not in rig.manager._peers)
        else:
            if boundary == "restart":
                rig.control("STOP")
            else:
                rig.load(value, "replacement.emoproj")
            waitUntil(lambda: not client.command("snapshot")["connected"])
            rig.control("START")
        client.command("connect")
        new = rig.manager.acquire("new-job", "tcp-server", "front", 1000, lambda: False)
        assert rig.state()["channels"][0]["lastSend"] is None
        result = rig.manager.send("new-job", "new-send", new, b"NEW", lambda: False)
        assert result["outcome"] == "SENT_TO_TRANSPORT"
        client.received(b"OLDNEW")
        before = rig.state()
        assert before["channels"][0]["lastSend"]["requestId"] == "new-send"
    finally:
        release.set()
        sender.join(3)
    assert not sender.is_alive()
    after = rig.state()
    rig.notes.update(injected="old send finally paused at sendLock.release", beforeLateCompletion=before,
                     afterLateCompletion=after, oldReceipt=replies)
    assert after["channels"][0]["lastSend"]["requestId"] == "new-send"
    oldRecords = [r for r in after["records"] if r.get("requestId") == "old-send"]
    if boundary == "project-replacement":
        assert oldRecords == []
    else:
        assert len(oldRecords) == 1
        assert oldRecords[0]["serviceGeneration"] == old["serviceGeneration"]
        assert oldRecords[0]["connectionGeneration"] == old["connectionGeneration"]


@pytest.mark.parametrize("refresh", [False, True])
def testStaleSettingsDraftDoesNotOverwriteConcurrentConfiguration(rig, retainedQtApplication, refresh):
    value = payload("manual")
    rig.load(value)
    current = dict(projectId="tcp-project", name="Review", token="same-project", path=str(rig.path),
                   config=deepcopy(value["communication"]))
    writes = []

    def save(config):
        writes.append(deepcopy(config))
        current["config"] = deepcopy(config)
        value["communication"] = deepcopy(config)
        rig.path.write_text(json.dumps(value), encoding="utf-8")
        return ""

    client = CommunicationClient(rig.stub)
    panel = CommunicationPanel()
    controller = CommunicationController(client, panel, lambda: current, save)
    try:
        spin(retainedQtApplication, lambda: controller.state is not None and controller.thread is None)
        controller.timer.stop()
        controller.nextPoll = float("inf")
        controller.settings()
        dialog = controller.settingsDialog
        dialog.serviceName.setText("Editor A draft")
        draft = dialog.draft()
        concurrent = deepcopy(current["config"])
        concurrent["tcpServer"]["port"] = freePort()
        rig.control("SYNC", config=concurrent)
        if refresh:
            controller.state = rig.state()  # A newer poll must not rebase the open draft.
            controller.observedAt = time.monotonic()
        dialog.saveDraft()
        if controller.thread is not None:
            spin(retainedQtApplication, lambda: (controller.poll(), controller.thread is None)[1])
        after = rig.state()
        rig.notes.update(editorDraft=draft, concurrentlyAccepted=concurrent, savedWrites=writes,
                         saveError=dialog.error.text(), controllerMessage=controller.message, afterStaleSave=after)
        assert after["desired"] == concurrent
        assert writes == [] and json.loads(rig.path.read_text())["communication"] == value["communication"]
        assert dialog.isVisible() and dialog.isEnabled() and dialog.draft() == draft
        assert "E_TCP_CONFIG_STALE" in dialog.error.text()
    finally:
        retire(retainedQtApplication, controller)
        panel.close()
        panel.deleteLater()
        retainedQtApplication.processEvents()


def testUnexpectedThreadFailureClosesPeersAndInvalidatesLeases(manager):
    manager.start()
    with connect(manager) as peer:
        ref = acquire(manager)

        def fail():
            raise RuntimeError("injected unexpected thread exception")

        with manager._lock:
            manager._spawn(fail, "tcp-review-failure")
        waitUntil(lambda: manager.state == "FAILED" and not manager._threads)
        assert not manager.snapshot()["ownsResources"]
        assert peer.recv(1) == b""
        assert not manager._leases
        manager.start()
        assert manager.send("job", "stale", ref, b"NO", lambda: False)["outcome"] == "NOT_SENT"


def testFailedSocketCleanupCannotBindOrLoseOwnership(manager):
    manager.start()
    original = manager._listener

    class FailedClose:
        failing = True

        def close(self):
            if self.failing:
                raise OSError("injected close failure")
            original.close()

    listener = FailedClose()
    manager._listener = listener
    try:
        manager._failService(OSError("injected listener error"), "E_TCP_LISTENER_FAILED")
        waitUntil(lambda: not manager._threads)
        generation = manager.serviceGeneration
        with pytest.raises(TcpServiceError) as failure:
            manager.start()
        assert failure.value.code == "E_TCP_RELEASE_FAILED"
        assert manager._listener is listener and manager.snapshot()["ownsResources"]
        assert manager.serviceGeneration == generation
        listener.failing = False
        manager.start()
        assert manager.state == "LISTENING" and manager._listener is not listener
    finally:
        listener.failing = False


@contextmanager
def editor(rig, app, save=None):
    current = dict(projectId="tcp-project", name="Review", token="editor", path=str(rig.path),
                   config=deepcopy(rig.value["communication"]))
    writes = []

    def persist(config):
        if save:
            error = save(config)
            if error:
                return error
        writes.append(deepcopy(config))
        current["config"] = deepcopy(config)
        value = deepcopy(rig.value)
        value["communication"] = config
        rig.path.write_text(json.dumps(value), encoding="utf-8")
        return ""

    client = CommunicationClient(rig.stub)
    panel = CommunicationPanel()
    controller = CommunicationController(client, panel, lambda: current, persist)
    try:
        spin(app, lambda: controller.state is not None and controller.thread is None)
        controller.timer.stop()
        controller.nextPoll = float("inf")
        yield controller, client, current, writes
    finally:
        retire(app, controller)
        panel.close()
        panel.deleteLater()
        app.processEvents()


def drain(app, controller):
    spin(app, lambda: (controller.poll(), controller.thread is None)[1])


@pytest.mark.parametrize("diskFailure", [False, True])
def testOrdinarySaveReservationProtectsDiskCallbackWithoutDisconnect(rig, retainedQtApplication, diskFailure):
    rig.load(payload("manual"))
    rig.control("START")
    robot = rig.client()
    ref = rig.manager.acquire("probe", "tcp-server", "front", 1000, lambda: False)
    initial = deepcopy(rig.value["communication"])
    attempted = []

    def duringSave(config):
        state = rig.state()
        assert state["savePending"] and not state["disableSavePending"]
        for action in ("START", "SYNC", "PREPARE_SAVE"):
            with pytest.raises(CommunicationClientError) as failure:
                rig.management.controlCommunication(action, state, config=initial, saveToken="another-editor")
            assert failure.value.code == "E_TCP_SAVE_PENDING"
        attempted.append(True)
        return "injected disk failure" if diskFailure else ""

    with editor(rig, retainedQtApplication, duringSave) as (controller, _, _, writes):
        controller.settings()
        dialog = controller.settingsDialog
        dialog.serviceName.setText("Saved while listening")
        changed = dialog.draft()
        dialog.saveDraft()
        drain(retainedQtApplication, controller)
        assert attempted == [True] and not rig.state()["savePending"]
        assert controller.disableSave is None
        expected = initial if diskFailure else changed
        assert rig.state()["desired"] == expected
        assert json.loads(rig.path.read_text())["communication"] == expected
        assert bool(writes) != diskFailure
        if diskFailure:
            assert dialog.isVisible() and dialog.isEnabled()
            assert dialog.draft() == changed and "injected disk failure" in dialog.error.text()
        assert rig.state()["state"] == "LISTENING"
        assert rig.state()["channels"][0]["connectionGeneration"] == ref["connectionGeneration"]
        assert rig.manager.send("probe", "after-save", ref, b"SAME-CONNECTION", lambda: False)["outcome"] == "SENT_TO_TRANSPORT"
        robot.received(b"SAME-CONNECTION")


@pytest.mark.parametrize("lostAction,afterCommit", [
    ("PREPARE_SAVE", "none"), ("SYNC", "none"), ("SYNC", "newer"), ("SYNC", "replacement")])
def testOrdinarySaveLostReplyDoesNotWriteOrReplayWrongConfig(rig, retainedQtApplication, monkeypatch,
                                                           lostAction, afterCommit):
    rig.load(payload("manual"))
    initial = deepcopy(rig.value["communication"])
    with editor(rig, retainedQtApplication) as (controller, client, _, writes):
        original = client.controlCommunication
        lost = []
        actions = []

        def control(action, *args, **kwargs):
            actions.append(action)
            state = original(action, *args, **kwargs)
            if action == lostAction and not lost:
                lost.append(action)
                raise CommunicationClientError("UNAVAILABLE", "injected lost reply")
            return state

        monkeypatch.setattr(client, "controlCommunication", control)
        changed = deepcopy(initial)
        changed["tcpServer"]["name"] = "Changed"
        controller.saveSettings(changed)
        drain(retainedQtApplication, controller)
        if lostAction == "PREPARE_SAVE":
            assert writes == [] and controller.disableSave is None
            assert rig.state()["desired"] == initial
        else:
            assert len(writes) == 1 and controller.disableSave["saved"]
            newer = deepcopy(changed)
            newer["tcpServer"]["name"] = "Newer from B"
            if afterCommit == "newer":
                rig.control("SYNC", config=newer)
            elif afterCommit == "replacement":
                replacement = deepcopy(rig.value)
                replacement["communication"] = newer
                rig.load(replacement, "new-context.emoproj")
            controller.syncSaved()
            drain(retainedQtApplication, controller)
            assert rig.state()["desired"] == (changed if afterCommit == "none" else newer)
            assert len(writes) == 1 and actions.count("SYNC") == 1
            if afterCommit == "none":
                assert controller.disableSave is None
            else:
                assert "冲突" in controller.message if afterCommit == "newer" else "身份已改变" in controller.message
        assert not rig.state()["savePending"]


@pytest.mark.parametrize("interruption", ["close", "local-edit"])
def testOrdinarySaveInterruptionBeforeWriteAbortsReservation(rig, retainedQtApplication, monkeypatch, interruption):
    rig.load(payload("manual"))
    before = rig.path.read_bytes()
    entered, release = threading.Event(), threading.Event()
    with editor(rig, retainedQtApplication) as (controller, client, current, writes):
        original = client.controlCommunication

        def control(action, *args, **kwargs):
            result = original(action, *args, **kwargs)
            if action == "PREPARE_SAVE":
                entered.set()
                assert release.wait(3)
            return result

        monkeypatch.setattr(client, "controlCommunication", control)
        changed = deepcopy(current["config"])
        changed["tcpServer"]["name"] = "Stale"
        try:
            controller.saveSettings(changed)
            assert entered.wait(2)
            if interruption == "close":
                assert not controller.close()
            else:
                current["config"]["tcpServer"]["name"] = "New local draft"
        finally:
            release.set()
        drain(retainedQtApplication, controller)
        assert writes == [] and rig.path.read_bytes() == before
        assert not rig.state()["savePending"] and controller.disableSave is None
