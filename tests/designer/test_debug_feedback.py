"""Field regressions: errors survive polling, and READY is not an execution ACK."""
from copy import deepcopy
import json
from pathlib import Path
import threading

import numpy as np
import pytest
import emo_master  # noqa: F401
from PySide2.QtCore import QPoint, Qt
from PySide2.QtTest import QTest
from shiboken2 import isValid

from emo_master.apps.designer.services.debug_artifacts import saveFixture
from emo_master.apps.designer.services.operator_debug import DebugConnection
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.ui.operator_debug_window import OperatorDebugWindow
from emo_master.apps.designer.ui.workflow_debug_window import WorkflowDebugWindow
from emo_master.apps.runtime.operator_debug.assets import imageBytes
from tests.designer.test_operator_debug_window import editorFor, wait
from tests.runtime.test_operator_debug_rpc import runtime as runtime
from tests.runtime.test_workflow_debug_control import nestedProject


@pytest.mark.parametrize("supported", [True, False])
def testZeroInputArtifactPanelIsLaidOutBeforeOpenAndAfterReadyOrFault(runtime, monkeypatch, supported):
    entered, release = threading.Event(), threading.Event()
    original = DebugConnection.open
    def slow(self, *args):
        entered.set()
        assert release.wait(5)
        return original(self, *args)
    monkeypatch.setattr(DebugConnection, "open", slow)
    editor, *_ = editorFor(runtime, "vision.value.number" if supported else "vision.io.huaray_camera")
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    try:
        wait(entered.is_set)
        assert dialog.state == "STARTING" and not dialog.rows
        def check():
            panel = dialog.artifacts.panel
            assert panel.parentWidget() is dialog.inputBody
            assert dialog.inputBody.layout().indexOf(panel) == 0
            assert panel.mapTo(dialog, QPoint()).y() >= dialog.tabs.y()
            assert dialog.inputBody.layout().count() == 3
        check()
        release.set()
        wait(lambda: dialog.state == ("READY" if supported else "FAULTED"))
        check()
        if supported:
            for _ in range(4):
                dialog.buildInputs()
            check()
            assert dialog.inputForm.rowCount() == 0
    finally:
        release.set()
        dialog.close()
        wait(lambda: not isValid(dialog))
        editor.forceClose()


def testPrepareFailureHasNoExecutionAndKeepsHistoryIdentityAcrossPolls(runtime, monkeypatch):
    manifest = json.loads(Path("src/emo_master/plugins/builtins/clahe/manifest.json").read_text(encoding="utf-8"))
    editor, *_ = editorFor(runtime, manifest["operatorId"])
    editor.context.paramSchema = manifest["paramSchema"]
    editor._schemaForm.setSchema(manifest["paramSchema"], {"clipLimit": 2.0})
    dialog = OperatorDebugWindow(editor)
    editor._debugWindow = dialog
    dialog.show()
    calls, polls = [], []
    original = dialog.connection.call
    def observed(method, **fields):
        calls.append(method)
        if method == "GetOperatorDebugSession":
            polls.append(method)
        return original(method, **fields)
    monkeypatch.setattr(dialog.connection, "call", observed)
    try:
        wait(lambda: dialog.run.isEnabled())
        wire = dialog.connection.upload(imageBytes(np.arange(256, dtype=np.uint8).reshape(16, 16)),
            "image/png", {"kind": "local"})
        dialog.rows["image"].setReference(wire, "gradient.png")
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 1 and dialog.run.isEnabled())
        previous = deepcopy(dialog.records[0])
        executed = calls.count("ExecuteOperatorDebugNode")
        editor._schemaForm._controls["clipLimit"].setValue(0)
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: "准备失败" in dialog.resultLabel.text() and dialog.run.isEnabled())
        assert "E_PARAM_INVALID" in dialog.actionStatus.text()
        assert not dialog.actionStatus.isHidden()
        count = len(polls)
        wait(lambda: len(polls) >= count + 3)
        assert "准备失败" in dialog.resultLabel.text() and "执行中" not in dialog.resultLabel.text()
        assert not dialog.executionId and dialog.records == [previous]
        assert calls.count("ExecuteOperatorDebugNode") == executed
        dialog.showResult()
        assert "以下为历史结果" in dialog.resultLabel.text()
        assert dialog.exportSelection()["identity"]["rawParams"] == previous["rawParams"]
        editor._schemaForm._controls["clipLimit"].setValue(1.2345)
        QTest.mouseClick(dialog.run, Qt.LeftButton)
        wait(lambda: len(dialog.records) == 2 and dialog.run.isEnabled())
        assert calls.count("ExecuteOperatorDebugNode") == executed + 1
        assert dialog.records[0]["rawParams"]["clipLimit"] == 1.2345
        assert not dialog.actionFeedback.errors and not dialog.executionStatus
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))
        editor.forceClose()


@pytest.mark.parametrize("kind", ["operator", "workflow"])
def testInvalidInputSetKeepsOldRowsAndErrorUntilSuccessfulLoad(runtime, tmp_path, monkeypatch, kind):
    from emo_master.apps.designer.ui import debug_artifact_tools as tools
    editor = None
    if kind == "operator":
        editor, *_ = editorFor(runtime, "vision.compare.number")
        dialog = OperatorDebugWindow(editor)
        editor._debugWindow = dialog
        def ready():
            return dialog.run.isEnabled()
    else:
        payload = nestedProject()
        payload["workflows"]["main"]["inputs"] = {"value": "integer"}
        dialog = WorkflowDebugWindow(RuntimeClient(runtime), payload, "main")
        def ready():
            return dialog.startButton.isEnabled()
    dialog.show()
    try:
        wait(ready)
        port = next(iter(dialog.rows))
        dialog.rows[port].mode.setCurrentIndex(1)
        dialog.rows[port].text.setText("13")
        before = {port: row.wire() for port, row in dialog.rows.items()}
        bad, good = tmp_path / "bad.emofixture", tmp_path / "good.emofixture"
        bad.write_bytes(b"not a fixture archive")
        path = [bad]
        monkeypatch.setattr(tools.QFileDialog, "getOpenFileName", lambda *_: (str(path[0]), ""))
        QTest.mouseClick(dialog.artifacts.load, Qt.LeftButton)
        wait(lambda: "artifact:load" in dialog.actionFeedback.errors and ready())
        message = dialog.actionStatus.text()
        assert message and not dialog.actionStatus.isHidden()
        count = [0]
        call = dialog.connection.call
        def observed(method, **fields):
            if method in {"GetOperatorDebugSession", "GetWorkflowDebugSession"}:
                count[0] += 1
            return call(method, **fields)
        monkeypatch.setattr(dialog.connection, "call", observed)
        wait(lambda: count[0] >= 3)
        assert dialog.actionStatus.text() == message and not dialog.actionStatus.isHidden()
        assert {port: row.wire() for port, row in dialog.rows.items()} == before
        rows = {port: dict(type=row.portType, mode="value" if port == next(iter(dialog.rows)) else "missing",
            wire={"inline": 7} if port == next(iter(dialog.rows)) else None) for port, row in dialog.rows.items()}
        saveFixture(good, "修正后的输入", rows, {}, {}, dialog.connection)
        path[0] = good
        QTest.mouseClick(dialog.artifacts.load, Qt.LeftButton)
        wait(lambda: dialog.rows[next(iter(dialog.rows))].wire() == {"inline": 7} and ready())
        assert not dialog.actionFeedback.errors
        assert runtime.loadedDocument is None and not runtime.jobRepository.all()
    finally:
        dialog.close()
        wait(lambda: not isValid(dialog))
        if editor is not None:
            editor.forceClose()
