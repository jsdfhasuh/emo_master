from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from threading import Event, Lock, get_ident
import time
from types import SimpleNamespace

import pytest

import emo_master  # noqa: F401 - preload native dependencies before Qt

pytest.importorskip("PySide2")

from PySide2.QtCore import QDateTime
from PySide2.QtGui import QFont, QFontDatabase, QValidator
from PySide2.QtWidgets import QCheckBox, QLineEdit, QSpinBox

from emo_master.apps.designer.operator_editors.controller_protocol import EditorKey
from emo_master.apps.designer.operator_editors.manager import OperatorEditorManager
from emo_master.apps.designer.operator_editors.trust import isBuiltinController
from emo_master.apps.designer.operator_editors.ui_loader import loadUiBytes
from emo_master.apps.designer.operator_editors.workspace_window import OperatorWorkspaceWindow
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.plugin.registry import PluginRegistry
from emo_master.plugins.builtins._plc_editor import PlcEditorController, _Cancellation
from tests.runtime.plc_debug_server import BlockResponse, PlcDebugServer
from tests.runtime.test_builtin_communication_operator_workflows import _project as plcProject


_ROOT = Path(__file__).resolve().parents[2] / "src/emo_master/plugins/builtins"
_DIRECTORIES = ("plc_slmp_read", "plc_slmp_write")


def _manifest(directory):
    return json.loads((_ROOT / directory / "manifest.json").read_text(encoding="utf-8"))


def _reply(**fields):
    defaults = dict(
        ok=True, code="", message="ok", session_id="session-1", runtime_instance_id="runtime-1",
        state="connected", write_enabled=False, result_json="{}", elapsed_ms=4,
        write_lock_generation=0,
        timestamp_ms=1791331200000, ttl_ms=30000,
    )
    defaults.update(fields)
    return SimpleNamespace(**defaults)


def _readResult(params):
    count, dataType, device = params["count"], params["dataType"], params["device"]
    wordStride = 2 if dataType in ("uint32", "int32", "float32") else 1
    addressStride = 1 if dataType == "bit" else wordStride * (16 if device == "M" else 1)
    values = [bool(index % 2) if dataType == "bit" else index + 10 for index in range(count)]
    return dict(
        **params, values=values, rawWords=[] if dataType == "bit" else list(range(count * wordStride)),
        addresses=[f"{device}{params['startAddress'] + index * addressStride}" for index in range(count)],
    )


class _Context:
    def __init__(self, directory="plc_slmp_read"):
        manifest = _manifest(directory)
        self.operatorId = manifest["operatorId"]
        self.paramSchema = manifest["paramSchema"]
        self.workflowOptions = []
        self.key = EditorKey("project", "workflow", "node")
        self.calls = []
        self.errors = []
        self.statuses = []
        self.logs = []
        self.applied = []
        self.gates = {}
        self.entered = defaultdict(Event)
        self.finished = defaultdict(Event)
        self.overrides = {}
        self.writeEnabled = False
        self.writeLockGeneration = 0
        self.location = "stub-runtime:50051"
        self.ttl = 30000
        self.invalidation = lambda: None
        self._lock = Lock()
        self.activeCalls = 0
        self.activeData = 0
        self.maxActiveData = 0

    def bindPreviewInvalidation(self, callback):
        self.invalidation = callback

    def bindWindowHooks(self, markDirty, setStatus, setError):
        self.markDirty = markDirty
        self._windowStatus = setStatus
        self._windowError = setError

    def setError(self, message):
        self.errors.append(message)

    def setStatus(self, message):
        self.statuses.append(message)

    def log(self, level, message):
        self.logs.append((level, message))

    def applyParams(self, params):
        self.applied.append(deepcopy(params))
        return True

    def plcDebugLocation(self):
        return self.location

    def hold(self, name):
        self.gates[name] = Event()
        return self.gates[name]

    def named(self, name):
        with self._lock:
            return [call for call in self.calls if call.name == name]

    @contextmanager
    def _call(self, name, params, requestId, cancellation, sessionId="session-1", runtimeId="runtime-1"):
        assert isinstance(cancellation, Event)
        assert callable(cancellation.cancel) and callable(cancellation.is_active)
        assert requestId or name == "close"
        call = SimpleNamespace(
            name=name, params=deepcopy(params), requestId=requestId, cancellation=cancellation,
            sessionId=sessionId, runtimeId=runtimeId, thread=get_ident(), start=time.monotonic(), end=None,
            dispatched=False,
        )
        with self._lock:
            self.calls.append(call)
            self.activeCalls += 1
            if name in ("read", "write"):
                self.activeData += 1
                self.maxActiveData = max(self.maxActiveData, self.activeData)
        self.entered[name].set()
        try:
            gate = self.gates.get(name)
            if gate is not None:
                assert gate.wait(5), f"test did not release {name}"
            yield call
        finally:
            call.end = time.monotonic()
            with self._lock:
                self.activeCalls -= 1
                if name in ("read", "write"):
                    self.activeData -= 1
            self.finished[name].set()

    def _override(self, name, default, call):
        override = self.overrides.get(name)
        if isinstance(override, Exception):
            raise override
        if callable(override):
            return override(call)
        return override if override is not None else default

    def openPlcDebugSession(self, params, requestId, cancellation=None):
        with self._call("open", params, requestId, cancellation) as call:
            self.writeEnabled = False
            self.writeLockGeneration = 0
            return self._override("open", _reply(ttl_ms=self.ttl), call)

    def executePlcDebugCommand(self, sessionId, runtimeInstanceId, command, params, requestId, cancellation=None):
        name = ("unlock" if params["enabled"] else "lock") if command == "set_write_enabled" else command
        with self._call(name, params, requestId, cancellation, sessionId, runtimeInstanceId) as call:
            if command == "set_write_enabled":
                if params["enabled"] and params.get("lockGeneration") != self.writeLockGeneration:
                    call.rejectedCode = "E_PLC_WRITE_LOCK_STALE"
                    return _reply(ok=False, code="E_PLC_WRITE_LOCK_STALE", message="写入锁版本已过期",
                                  write_lock_generation=self.writeLockGeneration)
                self.writeEnabled = params["enabled"]
                if not params["enabled"]:
                    self.writeLockGeneration += 1
            payload = {}
            if command == "read":
                payload = _readResult(params)
            elif command == "write":
                generation = params.get("lockGeneration")
                code = ""
                if not self.writeEnabled:
                    code = "E_PLC_WRITE_LOCKED"
                elif type(generation) is not int or not 0 <= generation <= 0x7FFFFFFFFFFFFFFF:
                    code = "E_PARAM_INVALID"
                elif generation != self.writeLockGeneration:
                    code = "E_PLC_WRITE_LOCK_STALE"
                if code:
                    call.rejectedCode = code
                    return _reply(
                        ok=False, code=code, message="写入权限版本无效或已过期",
                        session_id=sessionId, runtime_instance_id=runtimeInstanceId,
                        write_enabled=self.writeEnabled, write_lock_generation=self.writeLockGeneration,
                        result_json=json.dumps(dict(receipt=dict(outcome="rejected"), readback=None, readbackError="")),
                    )
                call.dispatched = True
                gate = self.gates.get("write_response")
                if gate is not None:
                    self.entered["write_response"].set()
                    assert gate.wait(5), "test did not release write response"
                payload = dict(
                    receipt=dict(outcome="confirmed", valueCount=len(params["values"])),
                    readback=_readResult({key: value for key, value in params.items() if key not in ("values", "lockGeneration")}
                                         | {"count": len(params["values"])}),
                    readbackError="",
                )
            default = _reply(
                session_id=sessionId, runtime_instance_id=runtimeInstanceId,
                write_enabled=self.writeEnabled, result_json=json.dumps(payload), ttl_ms=self.ttl,
                state="verified" if command in ("read", "write") else "connected",
                write_lock_generation=self.writeLockGeneration,
            )
            return self._override(name, default, call)

    def closePlcDebugSession(self, sessionId, runtimeInstanceId, cancellation=None):
        with self._call("close", {}, "", cancellation, sessionId, runtimeInstanceId) as call:
            self.writeEnabled = False
            return self._override("close", _reply(state="closed", session_id=sessionId,
                                                   runtime_instance_id=runtimeInstanceId), call)


class _EmbeddedContext(_Context):
    def __init__(self, directory, client, projectId):
        super().__init__(directory)
        self.client = client
        self.key = EditorKey(projectId, "main", "read" if directory == "plc_slmp_read" else "write")

    def openPlcDebugSession(self, params, requestId, cancellation=None):
        with self._call("open", params, requestId, cancellation):
            return self.client.openPlcDebugSession(
                self.key.projectId, self.key.workflowId, self.key.nodeId, self.operatorId,
                params, requestId, cancellation=cancellation,
            )

    def executePlcDebugCommand(self, sessionId, runtimeInstanceId, command, params, requestId, cancellation=None):
        name = ("unlock" if params["enabled"] else "lock") if command == "set_write_enabled" else command
        with self._call(name, params, requestId, cancellation, sessionId, runtimeInstanceId):
            reply = self.client.executePlcDebugCommand(
                sessionId, runtimeInstanceId, command, params, requestId, cancellation=cancellation,
            )
            self.writeEnabled = reply.write_enabled
            self.writeLockGeneration = reply.write_lock_generation
            return reply

    def closePlcDebugSession(self, sessionId, runtimeInstanceId, cancellation=None):
        with self._call("close", {}, "", cancellation, sessionId, runtimeInstanceId):
            return self.client.closePlcDebugSession(sessionId, runtimeInstanceId, cancellation=cancellation)


@pytest.fixture
def embeddedPlcRuntime(tmp_path):
    runtime = RuntimeService(dbPath=tmp_path / "runtime.sqlite3", workspaceRoot=tmp_path / "jobs")
    client = RuntimeClient(runtime, deadlineMs=1000)
    try:
        directory = tmp_path / "project"
        directory.mkdir()
        (directory / "project.json").write_text(plcProject(10001).model_dump_json(), encoding="utf-8")
        loaded = runtime.LoadProject(pb.LoadProjectRequest(project_path=str(directory)), None)
        assert loaded.ok, loaded.message
        yield runtime, client
    finally:
        try:
            client.close()
        finally:
            runtime.close()


def _pump(application, predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.003)
    application.processEvents()
    assert predicate(), "asynchronous Qt operation did not finish"


def _tick(application, duration):
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.003)


@pytest.fixture
def editor(designerApplication):
    # The Windows offscreen plugin has no system font database by default.
    if not QFontDatabase().families():
        fontPath = Path("C:/Windows/Fonts/msyh.ttc")
        if fontPath.is_file():
            QFontDatabase.addApplicationFont(str(fontPath))
    font = QFont("Microsoft YaHei UI") if "Microsoft YaHei UI" in QFontDatabase().families() else designerApplication.font()
    font.setPointSizeF(10.5)
    instances = []

    def make(directory="plc_slmp_read", params=None, *, window=False, context=None):
        manifest = _manifest(directory)
        root = loadUiBytes((_ROOT / directory / manifest["editor"]["uiResource"]).read_bytes())
        context = context or _Context(directory)
        controller = PlcEditorController()
        if window:
            workspace = OperatorWorkspaceWindow(
                key=context.key, title="PLC", context=context, schema=context.paramSchema,
                values=params or {}, customRoot=root, controller=controller,
            )
            workspace.openController()
            workspace.setFont(font)
            workspace.show()
        else:
            controller.bind(root, context)
            controller.loadParams(params or {})
            controller.onOpen()
            root.setFont(font)
            root.resize(980, 680)
            root.show()
            workspace = None
        instances.append((controller, context, root, workspace))
        return controller, context, workspace or root

    yield make
    for controller, context, root, workspace in instances:
        for gate in context.gates.values():
            gate.set()
        if not controller._disposed:
            _pump(designerApplication, lambda: controller._operation is None and not controller._cleanups)
        controller.dispose()
        controller._executor.shutdown(wait=True)
        _pump(designerApplication, lambda: context.activeCalls == 0)
        if workspace is not None:
            workspace.forceClose()
        else:
            root.close()


@pytest.mark.parametrize("directory", _DIRECTORIES)
def testPlcParameterTitlesCoverAdvancedAndDebugControls(editor, directory, designerApplication):
    from PySide2.QtWidgets import QLabel
    from emo_master.plugins.builtins._communication_operators import PLC_READ_PARAM_SCHEMA
    controller, context, root = editor(directory)
    for name, control in controller._endpointControls.items():
        title = PLC_READ_PARAM_SCHEMA["properties"][name]["title"]
        assert f"参数键：{name}" in control.toolTip()
        assert any(label.text() == title and f"参数键：{name}" in label.toolTip()
                   for label in root.findChildren(QLabel))
    for name, control in (("device", controller.deviceCombo), ("startAddress", controller.addressSpin),
                          ("dataType", controller.dataTypeCombo), ("count", controller.countSpin)):
        assert f"参数键：{name}" in control.toolTip()
    controller.tabs.setCurrentWidget(controller.runtimePage)
    controller.advancedButton.setChecked(True)
    designerApplication.processEvents()
    assert controller.advancedScroll.isVisible()
    assert controller.advancedScroll.viewport().height() > 0
    assert not context.calls


def _connect(controller, context, application):
    controller.tabs.setCurrentWidget(controller.runtimePage)
    controller.connect()
    _pump(application, lambda: bool(controller._sessionId) and controller._operation is None)
    assert not controller._writeEnabled
    assert context.named("open")


def _unlock(controller, context, application):
    controller.writeEnabledCheck.setChecked(True)
    _pump(application, lambda: controller._writeEnabled and controller._operation is None)
    assert context.writeEnabled


@pytest.mark.parametrize("directory", _DIRECTORIES)
def testManifestEditorUsesOperatorPackageAndOwnResource(directory):
    scan = PluginRegistry(coreVersion="0.6.1").scan(_ROOT)
    manifest = _manifest(directory)
    descriptor = scan.activeOperators[manifest["operatorId"]]
    assert descriptor.editorIssues == ()
    assert descriptor.resourceRoot == _ROOT / directory
    spec = descriptor.manifest.editor
    assert spec.controllerEntry == "emo_master.plugins.builtins._plc_editor:PlcEditorController"
    assert spec.fallback == "schemaForm" and spec.previewMode == "none"
    operatorPackage = manifest["entry"].split(":")[0].rsplit(".", 1)[0]
    assert spec.controllerEntry.split(":")[0].rsplit(".", 1)[0] == operatorPackage
    assert (descriptor.resourceRoot / spec.uiResource).is_file()


@pytest.mark.parametrize("directory", _DIRECTORIES)
@pytest.mark.parametrize("mode", ["custom", "asset_failure", "controller_failure"])
def testExistingManagerLoadsOrFallsBackWithoutNetwork(directory, mode, tmp_path):
    manifest = _manifest(directory)
    content = (_ROOT / directory / manifest["editor"]["uiResource"]).read_bytes()
    if mode == "controller_failure":
        content = b'<ui version="4.0"><class>Editor</class><widget class="QWidget" name="Editor"/></ui>'
    runtime = SimpleNamespace(getOperatorEditorAsset=lambda *_args: _reply(
        ok=mode != "asset_failure", content=content, sha256=hashlib.sha256(content).hexdigest(),
    ))
    assert isBuiltinController(manifest["editor"]["controllerEntry"])
    manager = OperatorEditorManager(
        runtimeClient=runtime, settingsStore=SimpleNamespace(value=lambda _key, default: default),
        applyParams=lambda *_args: True, appendLog=lambda *_args: None, cacheRoot=tmp_path / "cache",
    )
    try:
        workspace = manager.open(
            projectId="project", workflowId="workflow", nodeId="node", operatorId=manifest["operatorId"],
            displayName=manifest["displayName"], schema=manifest["paramSchema"], values={},
            operatorDefinition=dict(version=manifest["version"], editorSpec=manifest["editor"]),
        )
        if mode == "custom":
            assert isinstance(workspace._controller, PlcEditorController)
        else:
            assert workspace._controller is None
        assert workspace.applyChanges()
    finally:
        manager.closeAll()


@pytest.mark.parametrize("directory", _DIRECTORIES)
def testFirstEnterUsesCurrentDraftAndDebugDoesNotDirtyParameters(editor, designerApplication, directory):
    controller, context, workspace = editor(directory, {"host": "initial"}, window=True)
    assert not context.calls
    assert not workspace.isDirty()
    controller.paramForm._controls["host"].setText("current-draft")
    controller.tabs.setCurrentWidget(controller.runtimePage)
    assert controller.hostEdit.text() == "current-draft"
    assert workspace.isDirty()
    assert workspace.applyChanges()
    assert not workspace.isDirty()
    formal = controller.collectParams()
    controller.hostEdit.setText("debug-only")
    controller.addressSpin.setValue(120)
    controller.countSpin.setValue(3)
    controller.writeTable.cellWidget(0, 1).setValue(17)
    controller.pollIntervalSpin.setValue(800)
    assert controller.collectParams() == formal
    assert not workspace.isDirty()
    assert not context.calls
    _connect(controller, context, designerApplication)
    assert context.named("open")[0].params["host"] == "debug-only"
    assert not context.named("read") and not context.named("write")
    assert not context.named("unlock")
    assert not workspace.isDirty()


@pytest.mark.parametrize(("directory", "params"), [
    ("plc_slmp_read", {"device": "M", "dataType": "bit", "count": 2}),
    ("plc_slmp_read", {"dataType": "uint32", "count": 481}),
    ("plc_slmp_read", {"device": "M", "dataType": "int32"}),
    ("plc_slmp_write", {"device": "M", "dataType": "float32"}),
])
def testFormalValidationUsesExistingOperatorValidator(editor, directory, params):
    controller, context, _root = editor(directory, params)
    validation = controller.validate()
    assert validation and validation["code"] == "E_PARAM_INVALID"
    assert not context.calls


def testMalformedFormalValuesJsonCannotApply(editor):
    controller, context, workspace = editor("plc_slmp_write", window=True)
    controller.paramForm._controls["values"].setPlainText("[broken")
    assert not workspace.applyChanges()
    assert not context.applied and not context.calls


@pytest.mark.parametrize("directory", _DIRECTORIES)
def testDebugTypeLimitsAndAddressStride(editor, directory):
    controller, _context, _root = editor(directory)
    controller.tabs.setCurrentWidget(controller.runtimePage)
    assert controller.pollIntervalSpin.value() == 500
    assert controller.pollIntervalSpin.minimum() == 100
    assert controller.pollIntervalSpin.maximum() == 60000
    assert [controller.dataTypeCombo.itemText(i) for i in range(controller.dataTypeCombo.count())] == [
        "uint16", "int16", "uint32", "int32", "float32",
    ]
    controller.dataTypeCombo.setCurrentText("uint32")
    assert controller.countSpin.maximum() == 480
    controller.countSpin.setValue(2)
    assert controller.writeTable.item(1, 0).text() == "D2"
    controller.deviceCombo.setCurrentText("M")
    assert [controller.dataTypeCombo.itemText(i) for i in range(controller.dataTypeCombo.count())] == [
        "uint16", "int16", "bit",
    ]
    assert controller.countSpin.maximum() == 960
    assert controller.writeTable.item(1, 0).text() == "M16"
    controller.dataTypeCombo.setCurrentText("bit")
    assert isinstance(controller.writeTable.cellWidget(0, 1), QCheckBox)
    assert controller.writeTable.item(1, 0).text() == "M1"
    controller.addressSpin.setValue(0xFFFFFF)
    with pytest.raises(ValueError, match="24 位"):
        controller._readParams()


@pytest.mark.parametrize(("dataType", "valid", "invalid"), [
    ("uint32", "4294967295", "4294967296"),
    ("int32", "-2147483648", "-2147483649"),
    ("int32", "2147483647", "2147483648"),
    ("float32", "3.4e38", "3.5e38"),
])
def testWideValuesUseBoundedValidatedLineEdits(editor, dataType, valid, invalid):
    controller, _context, _root = editor()
    controller.tabs.setCurrentWidget(controller.runtimePage)
    controller.dataTypeCombo.setCurrentText(dataType)
    control = controller.writeTable.cellWidget(0, 1)
    assert isinstance(control, QLineEdit) and not isinstance(control, QSpinBox)
    assert control.validator().validate(valid, len(valid))[0] == QValidator.Acceptable
    assert control.validator().validate(invalid, len(invalid))[0] == QValidator.Invalid
    control.setText(valid)
    assert controller._cellValue(0) == (float(valid) if dataType == "float32" else int(valid))
    control.setText(invalid)
    with pytest.raises(ValueError):
        controller._cellValue(0)


@pytest.mark.parametrize("directory", _DIRECTORIES)
@pytest.mark.parametrize(("device", "dataType", "lastAddress"), [
    ("D", "uint32", "D8"), ("M", "uint16", "M36"), ("M", "bit", "M6"),
])
def testBothOperatorsReadAndDisplayRuntimeAddresses(editor, designerApplication, directory, device, dataType, lastAddress):
    controller, context, _root = editor(directory)
    _connect(controller, context, designerApplication)
    controller.deviceCombo.setCurrentText(device)
    controller.dataTypeCombo.setCurrentText(dataType)
    controller.addressSpin.setValue(4)
    controller.countSpin.setValue(3)
    controller.read()
    _pump(designerApplication, lambda: controller._operation is None)
    assert controller.resultTable.rowCount() == 3
    assert controller.resultTable.item(2, 0).text() == lastAddress
    assert controller.resultTable.columnCount() == 5
    assert controller.resultTable.horizontalHeaderItem(4).text() == "更新时间"
    stamp = QDateTime.fromMSecsSinceEpoch(_reply().timestamp_ms).toString("yyyy-MM-dd HH:mm:ss.zzz")
    assert [controller.resultTable.item(row, 4).text() for row in range(3)] == [stamp] * 3
    if dataType == "bit":
        assert controller.resultTable.horizontalHeaderItem(3).text() == "原始位"
        assert [controller.resultTable.item(row, 3).text() for row in range(3)] == ["0", "1", "0"]
    else:
        assert controller.resultTable.horizontalHeaderItem(3).text() == "原始字"
    assert controller._state == "verified"
    assert controller.metadataLabel.text() == f"最近 PLC 读写：{stamp} | 耗时：4.0 毫秒"
    metadataTop = controller.metadataLabel.mapTo(controller.runtimePage, controller.metadataLabel.rect().topLeft()).y()
    endpointTop = controller.hostEdit.mapTo(controller.runtimePage, controller.hostEdit.rect().topLeft()).y()
    assert metadataTop < endpointTop
    assert context.named("read")[0].thread != get_ident()
    assert not context.named("write") and not controller._writeEnabled


def testFailedReadReplyIsNeverDisplayedAsSuccess(editor, designerApplication):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    context.overrides["read"] = _reply(ok=False, state="verified", code="E_TEST_READ", message="no read")
    controller.pollCheck.setChecked(True)
    controller.read()
    _pump(designerApplication, lambda: controller._operation is None and not controller._cleanups)
    assert controller._state == "error" and not controller._sessionId
    assert not controller.pollCheck.isChecked() and not controller._writeEnabled
    assert controller.resultTable.rowCount() == 0
    assert "E_TEST_READ" in controller.statusLabel.text()
    assert context.named("close")


def testReadResultKeepsCapturedSpecWhenControlsChangeInFlight(editor, designerApplication):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    controller.dataTypeCombo.setCurrentText("uint32")
    controller.addressSpin.setValue(4)
    controller.countSpin.setValue(3)
    gate = context.hold("read")
    controller.read()
    _pump(designerApplication, lambda: context.entered["read"].is_set())
    controller.deviceCombo.setCurrentText("M")
    controller.dataTypeCombo.setCurrentText("bit")
    controller.addressSpin.setValue(80)
    controller.countSpin.setValue(2)
    gate.set()
    _pump(designerApplication, lambda: controller._operation is None)
    assert controller.resultTable.rowCount() == 3
    assert [controller.resultTable.item(row, 0).text() for row in range(3)] == ["D4", "D6", "D8"]
    assert controller.resultTable.item(1, 1).text() == "uint32"
    assert controller.resultTable.item(1, 3).text() == "2, 3"
    assert controller.resultTable.horizontalHeaderItem(3).text() == "原始字"
    stamp = QDateTime.fromMSecsSinceEpoch(_reply().timestamp_ms).toString("yyyy-MM-dd HH:mm:ss.zzz")
    assert controller.resultTable.item(1, 4).text() == stamp
    assert controller._readParams() == dict(device="M", startAddress=80, dataType="bit", count=2)
    assert context.named("read")[0].params == dict(device="D", startAddress=4, dataType="uint32", count=3)


@pytest.mark.parametrize("control", ["renew", "lock", "unlock"])
def testPlcTimestampAndElapsedDoNotFollowControlReplies(editor, designerApplication, control):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    assert controller.metadataLabel.text().startswith("TCP 连接：")
    controller.read()
    _pump(designerApplication, lambda: controller._operation is None)
    stamp = controller.resultTable.item(0, 4).text()
    communication = controller.metadataLabel.text()
    context.overrides[control] = _reply(
        timestamp_ms=_reply().timestamp_ms + 10000, elapsed_ms=999.5,
        write_enabled=control == "unlock", write_lock_generation=1 if control == "lock" else 0,
    )
    if control == "renew":
        controller._renew()
    elif control == "lock":
        controller._writeLockChanged(False)
    else:
        controller.writeEnabledCheck.setChecked(True)
    _pump(designerApplication, lambda: controller._operation is None and not controller._cleanups)
    assert controller.resultTable.item(0, 4).text() == stamp
    assert controller.metadataLabel.text() == communication and stamp in communication
    assert controller._leaseTimer.isActive()


@pytest.mark.parametrize("control", ["renew", "lock"])
def testLateConnectedControlReplyCannotDowngradeVerifiedConnection(editor, designerApplication, control):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    assert controller._state == "connected"
    controller.dataTypeCombo.setCurrentText("uint32")
    controller.countSpin.setValue(2)
    readGate = context.hold("read")
    controller.read()
    _pump(designerApplication, lambda: context.entered["read"].is_set())
    controlGate = context.hold(control)
    context.overrides[control] = _reply(state="connected", write_lock_generation=1 if control == "lock" else 0)
    if control == "renew":
        controller._renew()
    else:
        controller._writeLockChanged(False)
    _pump(designerApplication, lambda: context.entered[control].is_set())
    readGate.set()
    _pump(designerApplication, lambda: controller._operation is None)
    assert controller._state == "verified"
    cells = [controller.resultTable.item(row, column).text()
             for row in range(2) for column in range(controller.resultTable.columnCount())]
    controlGate.set()
    _pump(designerApplication, lambda: not controller._cleanups)
    assert controller._state == "verified" and "通信已验证" in controller.statusLabel.text()
    assert [controller.resultTable.item(row, column).text()
            for row in range(2) for column in range(controller.resultTable.columnCount())] == cells
    assert not controller._writeEnabled and not controller._pollTimer.isActive()
    controller.disconnect()
    _pump(designerApplication, lambda: not controller._cleanups)
    assert controller._state == "closed"
    _connect(controller, context, designerApplication)
    assert controller._state == "connected" and len(context.named("read")) == 1


def testRawBitHeaderUsesReplyTypeAndRestoresForWordReads(editor, designerApplication):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    controller.deviceCombo.setCurrentText("M")
    controller.dataTypeCombo.setCurrentText("bit")
    controller.countSpin.setValue(3)
    gate = context.hold("read")
    controller.read()
    _pump(designerApplication, lambda: context.entered["read"].is_set())
    controller.deviceCombo.setCurrentText("D")
    controller.dataTypeCombo.setCurrentText("uint16")
    gate.set()
    _pump(designerApplication, lambda: controller._operation is None)
    assert controller.resultTable.horizontalHeaderItem(3).text() == "原始位"
    assert [controller.resultTable.item(row, 3).text() for row in range(3)] == ["0", "1", "0"]
    controller.read()
    _pump(designerApplication, lambda: controller._operation is None)
    assert controller.resultTable.horizontalHeaderItem(3).text() == "原始字"
    assert [controller.resultTable.item(row, 3).text() for row in range(3)] == ["0", "1", "2"]


def testPollHasNoBacklogAndSchedulesAfterLastCompletion(editor, designerApplication):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    gate = context.hold("read")
    controller.pollIntervalSpin.setValue(100)
    controller.pollCheck.setChecked(True)
    _pump(designerApplication, lambda: context.entered["read"].is_set())
    _tick(designerApplication, 0.25)
    controller.read()
    assert len(context.named("read")) == 1
    assert not controller._pollTimer.isActive()
    gate.set()
    _pump(designerApplication, lambda: len(context.named("read")) >= 2)
    first, second = context.named("read")[:2]
    assert second.start - first.end >= 0.085
    assert context.maxActiveData == 1
    controller.pollCheck.setChecked(False)


@pytest.mark.parametrize("operation", ["read", "write"])
def testHeartbeatUsesIndependentLaneDuringBlockedDataRequest(editor, designerApplication, operation):
    controller, context, _root = editor()
    context.ttl = 150
    _connect(controller, context, designerApplication)
    assert controller._heartbeatTimer.interval() == 10000
    if operation == "write":
        _unlock(controller, context, designerApplication)
    gate = context.hold(operation)
    controller._heartbeatTimer.setInterval(25)
    getattr(controller, operation)()
    _pump(designerApplication, lambda: context.entered[operation].is_set())
    _tick(designerApplication, 0.25)
    assert len(context.named("renew")) >= 3
    assert controller._sessionId == "session-1"
    assert controller._leaseTimer.isActive()
    assert controller._operation is not None
    assert context.named("renew")[0].thread != context.named(operation)[0].thread
    gate.set()
    _pump(designerApplication, lambda: controller._operation is None)


def testRenewCoalescesAndCloseDoesNotWaitForIt(editor, designerApplication):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    gate = context.hold("renew")
    controller._renew()
    _pump(designerApplication, lambda: context.entered["renew"].is_set())
    for _ in range(20):
        controller._renew()
    assert len(context.named("renew")) == 1
    controller.read()
    _pump(designerApplication, lambda: context.entered["read"].is_set())
    controller.disconnect()
    _pump(designerApplication, lambda: context.entered["close"].is_set())
    assert context.named("renew")[0].cancellation.is_set()
    assert not context.finished["renew"].is_set()
    gate.set()


@pytest.mark.parametrize("directory", _DIRECTORIES)
def testWriteCapturesValuesOnceAndResumesOnlyExplicitPoll(editor, designerApplication, directory):
    controller, context, _root = editor(directory)
    _connect(controller, context, designerApplication)
    assert not controller.writeButton.isEnabled()
    controller.write()
    assert not context.named("write")
    _unlock(controller, context, designerApplication)
    controller._writeLockChanged(False)
    _pump(designerApplication, lambda: not controller._cleanups)
    _unlock(controller, context, designerApplication)
    version = controller._writeLockGeneration
    assert version == 1
    controller.dataTypeCombo.setCurrentText("uint32")
    controller.countSpin.setValue(2)
    controller.writeTable.cellWidget(0, 1).setText("4294967295")
    controller.writeTable.cellWidget(1, 1).setText("123")
    controller.pollIntervalSpin.setValue(60000)
    controller.pollCheck.setChecked(True)
    gate = context.hold("write")
    controller.write()
    _pump(designerApplication, lambda: context.entered["write"].is_set())
    assert not controller._pollTimer.isActive()
    controller.writeTable.cellWidget(0, 1).setText("1")
    controller.addressSpin.setValue(20)
    gate.set()
    _pump(designerApplication, lambda: controller._operation is None)
    assert context.named("write")[0].params == dict(
        device="D", startAddress=0, dataType="uint32", values=[4294967295, 123], lockGeneration=version,
    )
    assert context.named("write")[0].dispatched
    assert controller.receiptLabel.text().startswith("已确认 (confirmed)")
    assert controller.resultTable.item(0, 0).text() == "D0"
    assert controller.resultTable.item(0, 1).text() == "uint32"
    stamp = QDateTime.fromMSecsSinceEpoch(_reply().timestamp_ms).toString("yyyy-MM-dd HH:mm:ss.zzz")
    assert controller.resultTable.item(0, 4).text() == stamp
    assert controller.pollCheck.isChecked() and controller._pollTimer.isActive()
    controller.pollCheck.setChecked(False)


def testExplicitWriteWaitsForBlockedReadAndKeepsOneCapturedDraft(editor, designerApplication):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    _unlock(controller, context, designerApplication)
    controller.dataTypeCombo.setCurrentText("uint32")
    controller.countSpin.setValue(2)
    controller.addressSpin.setValue(4)
    controller.writeTable.cellWidget(0, 1).setText("4294967295")
    controller.writeTable.cellWidget(1, 1).setText("123")
    controller.pollIntervalSpin.setValue(60000)
    controller.pollCheck.setChecked(True)
    gate = context.hold("read")
    controller.read()
    _pump(designerApplication, lambda: context.entered["read"].is_set())
    assert controller.writeButton.isEnabled()
    controller.writeButton.click()
    captured = controller._pendingWrite
    assert captured is not None and not controller.writeButton.isEnabled()
    version = controller._writeLockGeneration
    assert captured.params["lockGeneration"] == version
    assert not controller._pollTimer.isActive() and not context.named("write")
    controller.deviceCombo.setCurrentText("M")
    controller.dataTypeCombo.setCurrentText("bit")
    controller.addressSpin.setValue(80)
    controller.countSpin.setValue(3)
    controller.writeTable.cellWidget(0, 1).setChecked(True)
    controller.write()
    assert controller._pendingWrite is captured and not context.named("write")
    gate.set()
    _pump(designerApplication, lambda: context.finished["write"].is_set() and controller._operation is None)
    assert controller._pendingWrite is None
    assert len(context.named("write")) == 1
    request = context.named("write")[0]
    assert request.requestId == captured.requestId
    assert request.params == dict(device="D", startAddress=4, dataType="uint32", values=[4294967295, 123], lockGeneration=version)
    assert request.dispatched and captured.params["lockGeneration"] == version
    assert request.start >= context.named("read")[0].end
    assert context.maxActiveData == 1
    assert controller._pollTimer.isActive()
    controller.pollCheck.setChecked(False)


@pytest.mark.parametrize(("token", "code"), [
    ({}, "E_PARAM_INVALID"),
    ({"lockGeneration": False}, "E_PARAM_INVALID"),
    ({"lockGeneration": 0}, "E_PLC_WRITE_LOCK_STALE"),
])
def testFakeRuntimeRejectsMissingInvalidOrStaleWritePermission(token, code):
    context = _Context()
    context.writeEnabled = True
    context.writeLockGeneration = 1
    params = dict(device="D", startAddress=0, dataType="uint16", values=[10]) | token
    reply = context.executePlcDebugCommand("session-1", "runtime-1", "write", params,
                                          "test-write-permission", cancellation=_Cancellation())
    assert not reply.ok and reply.code == code
    assert reply.write_lock_generation == 1
    assert json.loads(reply.result_json)["receipt"]["outcome"] == "rejected"
    assert not context.named("write")[0].dispatched


@pytest.mark.parametrize("directory", _DIRECTORIES)
def testWaitingWriteKeepsCapturedPermissionAndRejectsExternalRelock(editor, designerApplication, directory):
    controller, context, _root = editor(directory)
    _connect(controller, context, designerApplication)
    _unlock(controller, context, designerApplication)
    version = controller._writeLockGeneration
    gate = context.hold("write")
    controller.write()
    _pump(designerApplication, lambda: context.entered["write"].is_set())
    # Another client disables and re-enables writes while dispatch is waiting.
    context.writeLockGeneration += 1
    controller._renew()
    _pump(designerApplication, lambda: controller._writeLockGeneration == version + 1)
    assert controller._operation.params["lockGeneration"] == version
    assert context.writeEnabled
    gate.set()
    _pump(designerApplication, lambda: controller._operation is None and not controller._cleanups)
    requests = context.named("write")
    assert len(requests) == 1 and requests[0].params["lockGeneration"] == version
    assert requests[0].rejectedCode == "E_PLC_WRITE_LOCK_STALE" and not requests[0].dispatched
    assert controller.receiptLabel.text().startswith("已拒绝 (rejected)")
    assert not controller._sessionId and not controller._pollTimer.isActive()


def testPendingWriteKeepsOriginalPermissionAndCancelsAfterRenewedVersion(editor, designerApplication):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    _unlock(controller, context, designerApplication)
    version = controller._writeLockGeneration
    gate = context.hold("read")
    controller.read()
    _pump(designerApplication, lambda: context.entered["read"].is_set())
    controller.write()
    pending = controller._pendingWrite
    assert pending is not None and pending.params["lockGeneration"] == version
    context.writeLockGeneration += 1
    controller._renew()
    _pump(designerApplication, lambda: controller._writeLockGeneration == version + 1)
    assert pending.params["lockGeneration"] == version
    gate.set()
    _pump(designerApplication, lambda: controller._operation is None)
    assert controller._pendingWrite is None and pending.cancellation.is_set()
    assert not context.named("write") and not controller._pollTimer.isActive()


@pytest.mark.parametrize("action", ["pageleave", "lock", "reload", "disconnect", "error", "invalidate", "dispose"])
def testPendingWriteIsCanceledAtEveryLifecycleBoundary(editor, designerApplication, action):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    _unlock(controller, context, designerApplication)
    gate = context.hold("read")
    controller.read()
    _pump(designerApplication, lambda: context.entered["read"].is_set())
    operation = controller._operation
    generation = controller._generation
    controller.write()
    pending = controller._pendingWrite
    assert pending is not None
    if action == "pageleave":
        controller.tabs.setCurrentWidget(controller.paramsPage)
    elif action == "lock":
        controller.writeEnabledCheck.setChecked(False)
    elif action == "reload":
        controller.reloadParameters()
    elif action == "invalidate":
        context.invalidation()
    elif action == "error":
        context.overrides["read"] = _reply(ok=False, code="E_READ_TEST", message="读取失败")
    else:
        getattr(controller, action)()
    if action in ("pageleave", "lock"):
        assert not operation.cancellation.is_set() and controller._generation == generation
    gate.set()
    if action != "dispose":
        _pump(designerApplication, lambda: controller._operation is None and not controller._safetyPending())
    else:
        _pump(designerApplication, lambda: context.finished["read"].is_set())
    assert controller._pendingWrite is None and pending.cancellation.is_set()
    assert not context.named("write")
    assert not controller._pollTimer.isActive()


@pytest.mark.parametrize(("outcome", "ok", "readbackError"), [
    ("unknown", True, ""), ("unknown", False, "lost response"),
    ("rejected", False, ""), ("confirmed", False, "readback timeout"),
    ("confirmed", True, "readback timeout"),
])
def testNonSuccessfulWriteReceiptsArePlainAndNeverResumePolling(editor, designerApplication, outcome, ok, readbackError):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    _unlock(controller, context, designerApplication)
    controller.pollIntervalSpin.setValue(60000)
    controller.pollCheck.setChecked(True)
    context.overrides["write"] = _reply(
        ok=ok, code="E_WRITE", message="write reply", write_enabled=True,
        result_json=json.dumps(dict(receipt=dict(outcome=outcome, valueCount=1),
                                    readback=None, readbackError=readbackError)),
    )
    controller.write()
    _pump(designerApplication, lambda: controller._operation is None and not controller._cleanups)
    assert outcome in controller.receiptLabel.text()
    if outcome == "unknown":
        assert "尚未确认" in controller.receiptLabel.text()
    if readbackError:
        assert readbackError in controller.receiptLabel.text()
    assert not controller._writeEnabled and not controller._sessionId
    assert not controller.pollCheck.isChecked() and not controller._pollTimer.isActive()


def testWriteTransportExceptionIsUnknownNotRejected(editor, designerApplication):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    _unlock(controller, context, designerApplication)
    context.overrides["write"] = TimeoutError("acknowledgement lost")
    controller.write()
    _pump(designerApplication, lambda: controller._operation is None and not controller._cleanups)
    assert "结果未知 (unknown)" in controller.receiptLabel.text()
    assert "acknowledgement lost" in controller.receiptLabel.text()
    assert not controller._writeEnabled


def testConfirmedWriteWithCanceledReadbackStaysConfirmed(editor, designerApplication):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    _unlock(controller, context, designerApplication)
    context.overrides["write"] = _reply(
        ok=False, code="E_CANCELLED", message="回读已取消", write_enabled=False,
        result_json=json.dumps(dict(receipt=dict(outcome="confirmed", valueCount=1),
                                    readback=None, readbackError="回读已取消")),
    )
    controller.write()
    _pump(designerApplication, lambda: controller._operation is None and not controller._cleanups)
    assert controller.receiptLabel.text().startswith("已确认 (confirmed)")
    assert "回读已取消" in controller.receiptLabel.text()
    assert "unknown" not in controller.receiptLabel.text()
    assert not controller._writeEnabled and not controller._pollTimer.isActive()


@pytest.mark.parametrize("operation", ["read", "write"])
@pytest.mark.parametrize("returnEarly", [False, True])
def testPageLeaveFencesBlockedDataAndReturnDoesNotResume(editor, designerApplication, operation, returnEarly):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    _unlock(controller, context, designerApplication)
    gateName = "write_response" if operation == "write" else "read"
    gate = context.hold(gateName)
    controller.pollIntervalSpin.setValue(60000)
    controller.pollCheck.setChecked(True)
    getattr(controller, operation)()
    _pump(designerApplication, lambda: context.entered[gateName].is_set())
    generation = controller._generation
    controller.tabs.setCurrentWidget(controller.paramsPage)
    _pump(designerApplication, lambda: context.finished["lock"].is_set())
    assert controller._sessionId == "session-1" and not context.named("close")
    assert not context.named(operation)[0].cancellation.is_set()
    assert controller._generation == generation
    assert not context.writeEnabled and not controller._writeEnabled
    assert not controller.pollCheck.isChecked()
    assert "unknown" not in controller.receiptLabel.text()
    if returnEarly:
        controller.tabs.setCurrentWidget(controller.runtimePage)
    assert not controller._pollTimer.isActive()
    gate.set()
    _pump(designerApplication, lambda: controller._operation is None and not controller._cleanups)
    assert not controller._writeEnabled and not controller._pollTimer.isActive()
    assert controller.resultTable.rowCount() == 1
    if operation == "write":
        assert controller.receiptLabel.text().startswith("已确认 (confirmed)")
    controller.tabs.setCurrentWidget(controller.runtimePage)
    assert not controller._writeEnabled and not controller.pollCheck.isChecked()
    assert not controller._pollTimer.isActive()
    assert len(context.named("unlock")) == 1


def testCanceledSlowUnlockCannotUndoPageLeaveFence(editor, designerApplication):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    gate = context.hold("unlock")
    controller.writeEnabledCheck.setChecked(True)
    _pump(designerApplication, lambda: context.entered["unlock"].is_set())
    controller.tabs.setCurrentWidget(controller.paramsPage)
    _pump(designerApplication, lambda: context.finished["lock"].is_set())
    gate.set()
    _pump(designerApplication, lambda: controller._operation is None and len(context.named("lock")) >= 2)
    assert not context.writeEnabled and not controller._writeEnabled
    assert context.named("unlock")[0].rejectedCode == "E_PLC_WRITE_LOCK_STALE"
    assert controller._writeLockGeneration == context.writeLockGeneration == 2
    controller.tabs.setCurrentWidget(controller.runtimePage)
    assert not controller.writeEnabledCheck.isChecked()
    _unlock(controller, context, designerApplication)
    assert context.named("unlock")[-1].params == {"enabled": True, "lockGeneration": 2}


def testStaleUiGenerationLockRepliesStillRefreshPermissionVersion(editor, designerApplication):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    _unlock(controller, context, designerApplication)
    gate = context.hold("lock")
    controller.tabs.setCurrentWidget(controller.paramsPage)
    _pump(designerApplication, lambda: context.entered["lock"].is_set())
    controller.tabs.setCurrentWidget(controller.runtimePage)
    controller._writeLockChanged(False)
    _pump(designerApplication, lambda: len(context.named("lock")) == 2)
    gate.set()
    _pump(designerApplication, lambda: not controller._cleanups)
    assert controller._writeLockFresh
    assert controller._writeLockGeneration == context.writeLockGeneration == 2
    _unlock(controller, context, designerApplication)
    assert context.named("unlock")[-1].params["lockGeneration"] == 2


def testOldRenewReplyCannotRegressFenceVersionOrReenableWrites(editor, designerApplication):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    gate = context.hold("renew")
    context.overrides["renew"] = _reply(write_lock_generation=0, write_enabled=True)
    controller._renew()
    _pump(designerApplication, lambda: context.entered["renew"].is_set())
    controller.tabs.setCurrentWidget(controller.paramsPage)
    _pump(designerApplication, lambda: context.finished["lock"].is_set())
    _pump(designerApplication, lambda: controller._writeLockGeneration == 1)
    gate.set()
    _pump(designerApplication, lambda: not controller._cleanups)
    assert controller._writeLockGeneration == 1 and not controller._writeEnabled
    controller.tabs.setCurrentWidget(controller.runtimePage)
    _unlock(controller, context, designerApplication)
    assert context.named("unlock")[-1].params["lockGeneration"] == 1


def testUncertainPermissionRequiresRenewThenAnotherExplicitUnlock(editor, designerApplication):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    controller._writeLockFresh = False
    context.writeLockGeneration = 3
    gate = context.hold("renew")
    controller.writeEnabledCheck.setChecked(True)
    _pump(designerApplication, lambda: context.entered["renew"].is_set())
    assert not context.named("unlock") and not controller._writeEnabled
    gate.set()
    _pump(designerApplication, lambda: controller._writeLockFresh)
    assert not controller.writeEnabledCheck.isChecked()
    assert controller._writeLockGeneration == 3
    _unlock(controller, context, designerApplication)
    assert context.named("unlock")[-1].params == {"enabled": True, "lockGeneration": 3}


def testMissingFakeReplyPermissionVersionDefaultsToZero(editor, designerApplication):
    controller, context, _root = editor()
    reply = _reply()
    del reply.write_lock_generation
    context.overrides["open"] = reply
    _connect(controller, context, designerApplication)
    assert controller._writeLockGeneration == 0
    _unlock(controller, context, designerApplication)
    assert context.named("unlock")[-1].params["lockGeneration"] == 0


@pytest.mark.parametrize("operation", ["read", "write"])
def testManualLockBypassesBlockedDataWithoutCanceling(editor, designerApplication, operation):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    _unlock(controller, context, designerApplication)
    gateName = "write_response" if operation == "write" else "read"
    gate = context.hold(gateName)
    getattr(controller, operation)()
    _pump(designerApplication, lambda: context.entered[gateName].is_set())
    generation = controller._generation
    assert controller.writeEnabledCheck.isEnabled()
    controller.writeEnabledCheck.setChecked(False)
    _pump(designerApplication, lambda: context.finished["lock"].is_set())
    assert not context.writeEnabled
    assert not context.finished[operation].is_set()
    assert not context.named(operation)[0].cancellation.is_set()
    assert controller._generation == generation
    gate.set()
    _pump(designerApplication, lambda: controller._operation is None and not controller._cleanups)
    assert controller._sessionId == "session-1" and controller.resultTable.rowCount() == 1
    if operation == "write":
        assert controller.receiptLabel.text().startswith("已确认 (confirmed)")
    assert not controller._writeEnabled and not controller._pollTimer.isActive()


@pytest.mark.parametrize("directory", _DIRECTORIES)
@pytest.mark.parametrize("phase", ["read", "write", "write_readback"])
@pytest.mark.parametrize("pause", ["pageleave", "lock"])
def testEmbeddedRuntimePauseRetainsBlockedDataSocketAndReceipt(
        embeddedPlcRuntime, editor, designerApplication, directory, phase, pause):
    runtime, client = embeddedPlcRuntime
    context = _EmbeddedContext(directory, client, runtime.loadedProjectId)
    blocked = BlockResponse()

    def hold(peer, request, sock):
        if request.command == 0x1401:
            peer.memory_response(request)
        return blocked(peer, request, sock)

    actions = [PlcDebugServer._memory_action, hold] if phase == "write_readback" else [hold]
    with PlcDebugServer(actions) as peer:
        controller, _context, _root = editor(directory, peer.params(responseTimeoutMs=10000), context=context)
        try:
            _connect(controller, context, designerApplication)
            _unlock(controller, context, designerApplication)
            controller.addressSpin.setValue(4)
            controller.writeTable.cellWidget(0, 1).setValue(97)
            peer.words[4] = 73
            savedParams = controller.collectParams()
            controller.pollIntervalSpin.setValue(60000)
            controller.pollCheck.setChecked(True)
            command = "read" if phase == "read" else "write"
            getattr(controller, command)()
            _pump(designerApplication, lambda: blocked.entered.is_set())
            operation = controller._operation
            assert operation is not None
            generation, version = controller._generation, controller._writeLockGeneration
            sessionId = controller._sessionId
            session = runtime.plcDebugManager._sessions[sessionId]
            state = session.client._state
            sock = state.sock
            if pause == "pageleave":
                controller.tabs.setCurrentWidget(controller.paramsPage)
            else:
                controller.writeEnabledCheck.setChecked(False)
            _pump(designerApplication, lambda: context.finished["lock"].is_set() and not controller._safetyPending())
            assert not context.finished[command].is_set()
            assert context.named("lock")[0].thread != context.named(command)[0].thread
            assert controller._generation == generation and not operation.cancellation.is_set()
            assert controller._writeLockGeneration == version + 1
            assert runtime.plcDebugManager._sessions[sessionId] is session and not session.closed.is_set()
            assert not session.writeEnabled and not controller._writeEnabled
            assert session.client._state is state and state.sock is sock and sock.fileno() >= 0
            assert not blocked.peer_closed.is_set() and peer.connections == 1
            assert not controller.pollCheck.isChecked() and not controller._pollTimer.isActive()
            assert "unknown" not in controller.receiptLabel.text()
            blocked.release.set()
            _pump(designerApplication, lambda: controller._operation is None and not controller._cleanups)
            assert controller._sessionId == sessionId and controller._state == "verified"
            assert runtime.plcDebugManager._sessions[sessionId] is session and not session.closed.is_set()
            assert session.client._state is state and state.sock is sock and sock.fileno() >= 0
            assert controller.resultTable.item(0, 0).text() == "D4"
            assert controller.resultTable.item(0, 2).text() == ("73" if command == "read" else "97")
            if command == "write":
                assert controller.receiptLabel.text().startswith("已确认 (confirmed)")
                assert "unknown" not in controller.receiptLabel.text()
            assert context.named("lock")[0].end < context.named(command)[0].end
            assert not context.errors and controller.collectParams() == savedParams
            controller.tabs.setCurrentWidget(controller.runtimePage)
            controller.pollIntervalSpin.setValue(100)
            _tick(designerApplication, 0.15)
            assert not controller._writeEnabled and not controller.writeEnabledCheck.isChecked()
            assert not controller.pollCheck.isChecked() and not controller._pollTimer.isActive()
            assert len(context.named("unlock")) == 1 and not context.named("close")
            assert len(peer.requests) == (1 if command == "read" else 2)
            assert peer.connections == 1 and {request.connection for request in peer.requests} == {1}
        finally:
            blocked.release.set()
            controller.dispose()
            _pump(designerApplication, lambda: context.activeCalls == 0)


def testReloadWaitsForCloseBeforeUpdatingCapturedDraft(editor, designerApplication):
    controller, context, _root = editor(params={"host": "old"})
    _connect(controller, context, designerApplication)
    gate = context.hold("close")
    controller.paramForm._controls["host"].setText("captured-new")
    start = time.monotonic()
    controller.reloadParameters()
    assert time.monotonic() - start < 0.2
    _pump(designerApplication, lambda: context.entered["close"].is_set())
    controller.paramForm._controls["host"].setText("later-edit")
    assert controller.hostEdit.text() == "old"
    assert not controller.connectButton.isEnabled()
    gate.set()
    _pump(designerApplication, lambda: controller.hostEdit.text() == "captured-new")
    assert not controller._sessionId and not controller._writeEnabled
    assert len(context.named("open")) == 1
    assert controller.collectParams()["host"] == "later-edit"


def testReloadDuringOpenClosesLateSessionBeforeUpdating(editor, designerApplication):
    controller, context, _root = editor(params={"host": "old"})
    controller.tabs.setCurrentWidget(controller.runtimePage)
    openGate = context.hold("open")
    closeGate = context.hold("close")
    controller.connect()
    _pump(designerApplication, lambda: context.entered["open"].is_set())
    controller.paramForm._controls["host"].setText("new")
    controller.reloadParameters()
    openGate.set()
    _pump(designerApplication, lambda: context.entered["close"].is_set())
    assert controller.hostEdit.text() == "old"
    assert context.named("close")[0].thread == context.named("open")[0].thread
    closeGate.set()
    _pump(designerApplication, lambda: controller.hostEdit.text() == "new")
    assert not controller._sessionId


def testCloseFailureIsCheckedAndDoesNotApplyReloadDraft(editor, designerApplication):
    controller, context, _root = editor(params={"host": "old"})
    _connect(controller, context, designerApplication)
    context.overrides["close"] = _reply(ok=False, code="E_CLOSE_TEST", message="close rejected")
    controller.paramForm._controls["host"].setText("new")
    controller.reloadParameters()
    _pump(designerApplication, lambda: not controller._cleanups)
    assert controller.hostEdit.text() == "old"
    assert controller._pendingDraft is None
    assert "E_CLOSE_TEST" in controller.statusLabel.text()


@pytest.mark.parametrize("action", ["disconnect", "reloadParameters", "onClose", "invalidate", "dispose"])
def testCleanupCancelsBlockedReadWithoutJoiningOrAcceptingStaleResult(editor, designerApplication, action):
    controller, context, _root = editor()
    _connect(controller, context, designerApplication)
    gate = context.hold("read")
    controller.read()
    _pump(designerApplication, lambda: context.entered["read"].is_set())
    context.location = "replacement-runtime:50051"
    callback = context.invalidation if action == "invalidate" else getattr(controller, action)
    start = time.monotonic()
    callback()
    assert time.monotonic() - start < 0.2
    _pump(designerApplication, lambda: context.entered["close"].is_set())
    assert not context.finished["read"].is_set()
    assert context.named("read")[0].cancellation.is_set()
    assert not controller._sessionId and not controller._writeEnabled
    if action == "invalidate":
        assert "replacement-runtime" in controller.locationLabel.text()
    if action == "dispose":
        assert not any(timer.isActive() for timer in (
            controller._resultTimer, controller._pollTimer, controller._heartbeatTimer, controller._leaseTimer,
        ))
    gate.set()
    if action != "dispose":
        _pump(designerApplication, lambda: controller._operation is None)
    else:
        _pump(designerApplication, lambda: context.finished["read"].is_set())
    assert controller.resultTable.rowCount() == 0


@pytest.mark.parametrize("alreadyQueued", [False, True])
def testDisposeReleasesLateOrUnconsumedOpenReply(editor, designerApplication, alreadyQueued):
    controller, context, _root = editor()
    controller.tabs.setCurrentWidget(controller.runtimePage)
    gate = context.hold("open")
    controller.connect()
    _pump(designerApplication, lambda: context.entered["open"].is_set())
    if alreadyQueued:
        gate.set()
        assert context.finished["open"].wait(1)
        deadline = time.monotonic() + 1
        while controller._results.empty() and time.monotonic() < deadline:
            time.sleep(0.003)
        assert not controller._results.empty()
    controller.dispose()
    gate.set()
    _pump(designerApplication, lambda: context.finished["close"].is_set())
    assert not controller._sessionId
    assert context.named("close")[0].thread != get_ident()
    if not alreadyQueued:
        assert context.named("close")[0].thread == context.named("open")[0].thread


@pytest.mark.parametrize("operation", ["open", "unlock", "renew", "lock"])
def testEveryControlReplyChecksOkAndResetsWriteLock(editor, designerApplication, operation):
    controller, context, _root = editor()
    if operation != "open":
        _connect(controller, context, designerApplication)
    context.overrides[operation] = _reply(ok=False, code="E_CONTROL_TEST", message="not accepted")
    if operation == "open":
        controller.tabs.setCurrentWidget(controller.runtimePage)
        controller.connect()
    elif operation == "unlock":
        controller.writeEnabledCheck.setChecked(True)
    elif operation == "renew":
        controller._renew()
    else:
        controller.tabs.setCurrentWidget(controller.paramsPage)
    _pump(designerApplication, lambda: controller._state == "error" and not controller._operation and not controller._cleanups)
    assert not controller._writeEnabled and not controller._sessionId
    assert "E_CONTROL_TEST" in controller.statusLabel.text()


@pytest.mark.parametrize(("width", "height"), [(640, 430), (800, 430)])
@pytest.mark.parametrize("advanced", [False, True])
@pytest.mark.parametrize("phase", ["closed", "connected", "write"])
def testRuntimePageLayoutKeepsControlsAndTablesUsable(editor, designerApplication, width, height, advanced, phase):
    controller, context, root = editor(window=True)
    designerApplication.processEvents()
    controller.tabs.setCurrentWidget(controller.runtimePage)
    if phase != "closed":
        _connect(controller, context, designerApplication)
    if phase == "write":
        _unlock(controller, context, designerApplication)
        controller.write()
        _pump(designerApplication, lambda: controller._operation is None)
    controller.advancedButton.setChecked(advanced)
    root.resize(width, height)
    designerApplication.processEvents()
    assert root.width() == width and root.height() == height
    assert root.minimumSizeHint().width() <= 600
    assert root.minimumSizeHint().height() <= 430
    for control in (controller.connectButton, controller.disconnectButton, controller.reloadButton,
                    controller.readButton, controller.writeButton):
        assert not control.icon().isNull()
        assert control.toolTip() and control.size().width() == 34
    assert controller.advancedScroll.isVisible() == advanced
    assert controller.resultTable.width() >= 240 and controller.writeTable.width() >= 240
    assert controller.resultTable.geometry().height() >= 100
    assert controller.resultTable.columnCount() == 5
    assert controller.resultTable.horizontalHeaderItem(4).text() == "更新时间"
    if phase == "write":
        scrollbar = controller.resultTable.horizontalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        designerApplication.processEvents()
        assert controller.resultTable.visualItemRect(controller.resultTable.item(0, 4)).width() >= (
            controller.resultTable.fontMetrics().horizontalAdvance(controller.resultTable.item(0, 4).text())
        )
    assert controller.tabs.tabText(0) == "参数" and controller.tabs.tabText(1) == "运行"
    assert controller._endpointControls.keys().isdisjoint({"retryCount", "retryDelayMs"})
    assert {"retryCount", "retryDelayMs"} <= controller.collectParams().keys()
    for control in (controller.connectButton, controller.readButton, controller.writeButton,
                    controller.resultTable, controller.writeTable, root._applyButton):
        bounds = control.rect()
        origin = control.mapTo(root, bounds.topLeft())
        assert 0 <= origin.x() and origin.x() + bounds.width() <= width
        assert 0 <= origin.y() and origin.y() + bounds.height() <= height
    for label in (controller.locationLabel, controller.statusLabel, controller.receiptLabel, controller.metadataLabel):
        if label.text():
            assert label.height() >= label.heightForWidth(label.width())
    qaDirectory = os.environ.get("PLC_EDITOR_QA_DIR")
    if qaDirectory:
        destination = Path(qaDirectory)
        destination.mkdir(parents=True, exist_ok=True)
        assert root.grab().save(str(destination / f"plc-{width}x{height}-{phase}-advanced{advanced}.png"))
        geometry = {
            "requested": [width, height], "actual": [root.width(), root.height()],
            "windowMinimum": [root.minimumSizeHint().width(), root.minimumSizeHint().height()],
            "paramsMinimum": [controller.paramsPage.minimumSizeHint().width(), controller.paramsPage.minimumSizeHint().height()],
            "runtimeMinimum": [controller.runtimePage.minimumSizeHint().width(), controller.runtimePage.minimumSizeHint().height()],
            "tabsMinimum": [controller.tabs.minimumSizeHint().width(), controller.tabs.minimumSizeHint().height()],
        }
        (destination / f"plc-{width}x{height}-{phase}-advanced{advanced}.json").write_text(
            json.dumps(geometry, indent=2), encoding="utf-8",
        )
