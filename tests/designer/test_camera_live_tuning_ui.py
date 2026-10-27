import json
from copy import deepcopy
import time
from dataclasses import asdict

import pytest

from emo_master.apps.designer.operator_editors.manager import OperatorEditorManager
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.plugins.builtins._huaray_imv import resetImvApiForTests
from emo_master.plugins.builtins.huaray_camera.operator import PARAM_SCHEMA
from tests.designer.test_builtin_operator_editor_ui import _Settings
from tests.designer.qt_wait import waitUntil
from tests.plugins.test_huaray_camera_operator import _params
from tests.runtime.test_camera_configuration_session import TriggerApi

from PySide2.QtCore import Qt, QTimer
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QDialogButtonBox, QListWidget, QPushButton, QTableWidget


def waitUi(predicate, seconds=4):
    waitUntil(predicate, timeout=seconds)


def pumpFor(milliseconds):
    deadline = time.monotonic() + milliseconds / 1000
    waitUntil(lambda: time.monotonic() >= deadline)


@pytest.fixture
def cameraEditor(tmp_path, monkeypatch):
    resetImvApiForTests()
    api = TriggerApi()
    monkeypatch.setattr("emo_master.plugins.builtins.huaray_camera.operator.getImvApi", lambda: api)
    project = tmp_path / "project"
    project.mkdir()
    params = _params(triggerMode="hardware")
    payload = {"version": "1.0", "meta": {"name": "camera-ui"}, "runtime": {},
               "designer": {"nodes": [{"nodeId": "camera", "operatorId": "vision.io.huaray_camera", "params": params}], "edges": []}}
    # The legacy controller remains testable through its explicit draft transport.
    # Do not formally load an unbound hardware node: current execution requires
    # managed devices, and these tests exercise the isolated configuration UI.
    saved = deepcopy(payload)
    saved["designer"]["nodes"] = []
    (project / "project.json").write_text(json.dumps(saved), encoding="utf-8")
    service = RuntimeService(dbPath=tmp_path / "runtime.db", workspaceRoot=tmp_path / "jobs")
    assert service.LoadProject(runtime_pb2.LoadProjectRequest(project_path=str(project)), None).ok
    client = RuntimeClient(service)
    draft = service.loadedDocument.model_dump()
    draft["workflows"]["main"]["nodes"].extend(payload["designer"]["nodes"])
    applied = []
    manager = OperatorEditorManager(runtimeClient=client, settingsStore=_Settings(),
        applyParams=lambda key, values: not applied.append(values), appendLog=lambda *args: None,
        cacheRoot=tmp_path / "editors", getPreviewProject=lambda _key: deepcopy(draft))
    descriptor = service.pluginScanResult.activeOperators["vision.io.huaray_camera"]
    window = manager.open(projectId=service.loadedProjectId, workflowId="main", nodeId="camera",
        operatorId="vision.io.huaray_camera", displayName="相机", schema=PARAM_SCHEMA, values=params,
        operatorDefinition={"version": descriptor.manifest.version,
                            "editorSpec": asdict(descriptor.manifest.editor)})
    controller = window._controller
    assert controller is not None
    yield window, controller, api, applied, service
    if controller._pending:
        waitUi(lambda: controller._pending is None)
    if controller._sessionId:
        controller._modalAction("close", {"decision": "restore"})
        controller._endStream()
        controller._sessionId = ""
    window.forceClose()
    service.close()
    resetImvApiForTests()


def chooseMode(controller, field, value):
    combo = controller.form._controls[field]
    combo.setCurrentIndex(combo.findData(value))
    combo.activated.emit(combo.currentIndex())
    waitUi(lambda: controller._pending is None)


def connect(controller):
    controller.connectButton.click()
    waitUi(lambda: bool(controller._sessionId) and controller._pending is None)


def testUiDefaultPreviewAndSavedModeAreSeparate(cameraEditor):
    _, controller, api, applied, _ = cameraEditor
    assert controller.form.getValues()["triggerMode"] == "freeRun"
    assert controller.collectParams()["triggerMode"] == "hardware"
    connect(controller)
    assert not api.writes
    assert controller.form.getValues()["triggerMode"] == "freeRun"
    assert controller._snapshot["params"]["triggerMode"] == "freeRun"
    chooseMode(controller, "triggerMode", "software")
    controller.previewButton.click()
    waitUi(lambda: controller._pending is None and controller._snapshot.get("state") == "softwareReady")
    pumpFor(180)
    assert api.commands == []
    controller.singleFrameButton.click()
    waitUi(lambda: controller._pending is None and len(api.commands) == 1 and controller.singleFrameButton.isEnabled())
    assert controller.blockIdLabel.text() != "帧号：-"
    assert applied == [] and controller.collectParams()["triggerMode"] == "hardware"


def testNumericEditsWaitForCommitAndStopKeepsBaseline(cameraEditor):
    _, controller, api, _, _ = cameraEditor
    connect(controller)
    chooseMode(controller, "exposureMode", "manual")
    exposure = controller.form._controls["exposureUs"]
    original = dict(controller._snapshot["original"])
    count = len(api.writes)
    exposure.setFocus()
    exposure.selectAll()
    QTest.keyClicks(exposure, "15000")
    pumpFor(60)
    assert len(api.writes) == count
    QTest.keyClick(exposure, Qt.Key_Return)
    waitUi(lambda: controller._pending is None and api.doubles["ExposureTime"] == 15000)
    controller.previewButton.click()
    waitUi(lambda: controller._pending is None)
    controller.stopButton.click()
    waitUi(lambda: controller._pending is None)
    assert controller._sessionId
    assert {key: controller._snapshot["original"][key] for key in original} == original
    assert api.openCount == 1


@pytest.mark.parametrize("text", ["-", "1e", "nan", "1e999", "-100"])
def testInvalidNumericCommitNeverWritesOrSaves(cameraEditor, text):
    window, controller, api, applied, _ = cameraEditor
    connect(controller)
    chooseMode(controller, "exposureMode", "manual")
    exposure = controller.form._controls["exposureUs"]
    count = len(api.writes)
    exposure.setText(text)
    exposure.editingFinished.emit()
    assert exposure.accessibleDescription()
    assert len(api.writes) == count and controller._pending is None
    assert not window.applyChanges() and applied == []
    # Teardown must not re-commit the deliberately invalid test input.
    exposure.setText(str(api.doubles["ExposureTime"]))


def testNumericReadbackKeepsExactTextAndDoesNotWrite(cameraEditor):
    _, controller, api, _, _ = cameraEditor
    api.doubles["ExposureTime"] = 8123.456789
    connect(controller)
    exposure = controller.form._controls["exposureUs"]
    assert exposure.value() == api.doubles["ExposureTime"]
    assert api.writes == []
    assert "8123.456789" in exposure.text()


@pytest.mark.parametrize("choice", ["继续调整", "保留调整", "放弃并恢复"])
def testCloseConfirmationChoices(cameraEditor, choice, tmp_path):
    window, controller, api, applied, _ = cameraEditor
    connect(controller)
    chooseMode(controller, "gainMode", "manual")
    timer = QTimer(window)

    def answer():
        box = QApplication.activeModalWidget()
        if box is not None and box.windowTitle() == "确认相机调整":
            timer.stop()
            assert box.grab().save(str(tmp_path / "camera-close-confirmation.png"))
            next(button for button in box.findChildren(QPushButton) if button.text() == choice).click()

    timer.timeout.connect(answer)
    timer.start(20)
    try:
        accepted = window.close()
    finally:
        timer.stop()
    if choice == "继续调整":
        assert not accepted and controller._sessionId and window.isVisible()
    else:
        assert accepted and not controller._sessionId
        assert controller._streamThread is None
        assert all(not thread.is_alive() for thread in controller._executor._threads)
        assert api.enums["GainAuto"] == ("Off" if choice == "保留调整" else "Continuous")
    assert applied == []


def testSaveOnlyChosenConfirmedFieldsWithoutRetainingCamera(cameraEditor):
    window, controller, api, applied, _ = cameraEditor
    connect(controller)
    chooseMode(controller, "exposureMode", "manual")
    timer = QTimer(window)

    def choose():
        dialog = QApplication.activeModalWidget()
        if dialog is not None and dialog.windowTitle() == "选择加入节点配置的属性":
            choices = dialog.findChild(QListWidget)
            for index in range(choices.count()):
                if choices.item(index).text().startswith("曝光设置"):
                    choices.item(index).setCheckState(Qt.Checked)
            timer.stop()
            dialog.findChild(QDialogButtonBox).accepted.emit()

    timer.timeout.connect(choose)
    timer.start(10)
    try:
        assert window.applyChanges()
    finally:
        timer.stop()
    assert applied[-1]["exposureMode"] == "manual"
    assert applied[-1]["triggerMode"] == "hardware"
    assert "gainMode" not in applied[-1]
    assert controller._snapshot["pending"]


def testWorkflowAndProjectSwitchCannotBypassPendingAdjustments(cameraEditor):
    _, controller, _, _, service = cameraEditor
    connect(controller)
    chooseMode(controller, "gainMode", "manual")
    reply = service.StartJob(runtime_pb2.StartJobRequest(project_id=service.loadedProjectId, workflow_id="main"), None)
    assert not reply.ok and "E_RESOURCE_BUSY" in reply.message
    reply = service.LoadProject(runtime_pb2.LoadProjectRequest(project_path=service.loadedProjectPath), None)
    assert not reply.ok and "E_RESOURCE_BUSY" in reply.message
    assert controller._sessionId


def testRestoreFailureCanReturnAndRetryWithoutLosingSession(cameraEditor):
    window, controller, api, _, _ = cameraEditor
    connect(controller)
    chooseMode(controller, "gainMode", "manual")
    api.denied.add("GainAuto")

    def closeChoosing(choice):
        timer = QTimer(window)

        def answer():
            dialog = QApplication.activeModalWidget()
            if dialog is not None and dialog.windowTitle() == "确认相机调整":
                timer.stop()
                next(button for button in dialog.findChildren(QPushButton) if button.text() == choice).click()

        timer.timeout.connect(answer)
        timer.start(20)
        try:
            return window.close()
        finally:
            timer.stop()

    assert not closeChoosing("放弃并恢复")
    assert controller._restoreFailed and controller._sessionId
    assert api.enums["GainAuto"] == "Off"
    assert not closeChoosing("返回")
    assert window.isVisible() and controller._sessionId
    api.denied.clear()
    assert closeChoosing("重试恢复")
    assert not controller._sessionId and api.enums["GainAuto"] == "Continuous"


def testDeviceRangesPermissionsAndRefreshDoNotWrite(cameraEditor):
    _, controller, api, _, _ = cameraEditor
    connect(controller)
    api.denied.add("GainAuto")
    count = len(api.writes)
    controller.refreshButton.click()
    waitUi(lambda: controller._pending is None)
    assert not controller.form._controls["gainMode"].isEnabled()
    width = controller.form._controls["width"]
    metadata = controller._snapshot["properties"]["Width"]
    assert (width.minimum(), width.maximum(), width.singleStep()) == (
        metadata["minimum"], metadata["maximum"], metadata["step"])
    assert len(api.writes) == count


def testUnknownResultBlocksSavingAndQueriesSameRequest(cameraEditor, monkeypatch):
    window, controller, _, applied, _ = cameraEditor
    connect(controller)
    original = controller.context.cameraConfiguration
    seen = []

    def uncertain(sessionId, identity, requestId, action, payload):
        seen.append((requestId, action, payload))
        result = original(sessionId, identity, requestId, action, payload)
        if len(seen) == 1:
            raise TimeoutError("transport result unknown")
        return result

    monkeypatch.setattr(controller.context, "cameraConfiguration", uncertain)
    chooseMode(controller, "gainMode", "manual")
    assert controller._unknownRequest is not None
    assert not window.applyChanges() and applied == []
    controller.refreshButton.click()
    waitUi(lambda: controller._pending is None and controller._unknownRequest is None)
    assert seen[0] == seen[1]


def testDisconnectedCloseRequiresExplicitAbandonWarning(cameraEditor):
    window, controller, api, _, _ = cameraEditor
    connect(controller)
    chooseMode(controller, "gainMode", "manual")
    api.online = False
    answered = []
    timer = QTimer(window)

    def answer():
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            return
        if dialog.windowTitle() == "确认相机调整":
            answered.append("review")
            next(button for button in dialog.findChildren(QPushButton) if button.text() == "放弃恢复并关闭").click()
        elif dialog.windowTitle() == "未恢复的相机配置":
            answered.append("warning")
            timer.stop()
            next(button for button in dialog.findChildren(QPushButton) if button.text() == "放弃恢复并关闭").click()

    timer.timeout.connect(answer)
    timer.start(20)
    try:
        assert window.close()
    finally:
        timer.stop()
    assert answered == ["review", "warning"] and not controller._sessionId
    assert api.enums["GainAuto"] == "Off"


def testHardwareWaitingUiKeepsLastFrameAndResponsiveControls(cameraEditor, tmp_path):
    window, controller, api, _, _ = cameraEditor
    connect(controller)
    chooseMode(controller, "triggerMode", "hardware")
    controller.previewButton.click()
    waitUi(lambda: controller._pending is None and controller.connectionStatusLabel.text() == "等待图片")
    assert window._statusLabel.text() == "等待图片"
    assert not controller.previewButton.isEnabled()
    assert not controller.singleFrameButton.isVisible()
    assert controller.stopButton.isEnabled()
    assert window.grab().save(str(tmp_path / "camera-hardware-waiting.png"))
    api.images = 1
    waitUi(lambda: controller.blockIdLabel.text() != "帧号：-")
    assert controller.connectionStatusLabel.text() == "等待下一张图片"
    cacheKey = controller.previewLabel.pixmap().cacheKey()
    pumpFor(350)
    assert controller.previewLabel.pixmap().cacheKey() == cacheKey
    window.resize(700, 520)
    pumpFor(60)
    assert window.grab().save(str(tmp_path / "camera-hardware-last-frame-narrow.png"))
    assert controller.stopButton.isEnabled() and api.commands == []


def testInactiveEdgeOnlyDisablesEdgeControlAndPreviewStillWorks(cameraEditor, monkeypatch):
    from tests.plugins.test_camera_configuration import hideInactiveTriggerEdge
    _, controller, api, _, _ = cameraEditor
    api.enums["TriggerSource"] = "Software"
    hideInactiveTriggerEdge(api, monkeypatch)
    connect(controller)
    assert controller.form._controls["triggerMode"].isEnabled()
    assert not controller.form._controls["triggerActivation"].isEnabled()
    controller.previewButton.click()
    waitUi(lambda: controller._pending is None and controller.blockIdLabel.text() != "帧号：-")
    chooseMode(controller, "triggerMode", "hardware")
    assert controller.form._controls["triggerActivation"].isEnabled()
    assert not controller._snapshot["failed"]


def testUnchangedFocusCommitDoesNotCreateUnsavedParameters(cameraEditor):
    window, controller, _, _, _ = cameraEditor
    controller._committed("captureTimeoutMs", controller.form.getValues()["captureTimeoutMs"])
    assert not window.isDirty()


def testUnsavedDialogListsNodeAndConfirmedCameraChanges(cameraEditor, tmp_path):
    window, controller, api, applied, _ = cameraEditor
    controller._committed("ipAddress", "192.168.1.11")
    connect(controller)
    controller._request("apply", {"roiMode": "custom", "height": 4})
    waitUi(lambda: controller._pending is None)
    originalHeight = controller._snapshot["original"]["Height"]
    timer = QTimer(window)
    rows = []

    def answer():
        dialog = QApplication.activeModalWidget()
        if dialog is not None and dialog.windowTitle() == "未保存的参数":
            timer.stop()
            table = dialog.findChild(QTableWidget)
            rows.extend([[table.item(row, col).text() for col in range(table.columnCount())]
                         for row in range(table.rowCount())])
            assert dialog.grab().save(str(tmp_path / "camera-unsaved-diff.png"))
            next(button for button in dialog.findChildren(QPushButton) if button.text() == "继续编辑").click()

    timer.timeout.connect(answer)
    timer.start(20)
    try:
        assert not window.close()
    finally:
        timer.stop()
    assert any(row[0] == "相机 IP 地址" and row[2] == "192.168.1.11" and row[3] == "节点待保存值" for row in rows)
    assert any(row[0] == "图像高度（像素）" and row[1:3] == [str(originalHeight), "4"] for row in rows)
    assert controller._sessionId and controller._snapshot["pending"]
    assert applied == [] and api.ints["Height"] == 4
