"""Startup guidance uses Runtime slot semantics without starting any Jobs."""
from types import SimpleNamespace

import pytest
from PySide2.QtCore import QPoint, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QDialog, QDialogButtonBox, QLabel, QWidget

from emo_master.apps.designer.state.project_store import loadProjectDocument, saveProject
from emo_master.apps.designer.state.workflow_store import WorkflowStore
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.operator_runtime.main import OperatorWindow
from emo_master.core.project.models import ProjectDocument, WorkflowNode
from emo_master.core.workflow.compiler import WorkflowCompiler
from emo_master.ui.run_targets_dialog import RunTargetsDialog
from tests.designer.test_main_window_project_save_load import RuntimeClientStub


def queueDocument(slots=("faces",)):
    payload = WorkflowStore().toPayload("Startup guidance fixture")

    def workflow(name, nodes=()):
        return dict(name=name, nodes=[dict(nodeId="input", kind="workflow_input"), *nodes,
                                     dict(nodeId="output", kind="workflow_output")], edges=[])

    payload["workflows"] = {
        "main": workflow("摄像头队列检测", [dict(nodeId="loop", kind="loop", loop=dict(
            contractVersion=2, mode="repeat", repeatCount=2, maxIterations=2, bodyWorkflowId="tick"))]),
        "tick": workflow("采集与队列投递", [dict(nodeId="enqueue", kind="subflow", targetWorkflowId="queue")]),
        "queue": workflow("队列投递", [dict(nodeId="dispatch", kind="dispatch", params=dict(targets=[
            dict(targetSlotId=slot, workflowId="consumer", inputs={}, queueCapacity=1) for slot in slots]))]),
        "consumer": workflow("人脸检测与归档"),
        "other": workflow("离线检测"),
    }
    payload["workflowOrder"] = list(payload["workflows"])
    return ProjectDocument.model_validate(payload)


def makeDialog(document, selected=("main",), limit=2, **kwargs):
    dialog = RunTargetsDialog(document.workflows, selected, limit, entryWorkflowId="main",
                              document=document, **kwargs)
    dialog.setAttribute(Qt.WA_DontShowOnScreen)
    return dialog


def row(dialog, key):
    return next(dialog.targets.item(i) for i in range(dialog.targets.count())
                if dialog.targets.item(i).data(Qt.UserRole) == key)


def testProducerMarksAutomaticConsumerThroughLoopAndSubflow(designerApplication):
    document = queueDocument()
    before = document.model_dump()
    dialog = makeDialog(document)
    assert dialog.minimumJobCount() == 2
    assert "1 个入口 Job + 1 个自动消费者 Job = 2 个 Job" in dialog.capacityHint.text()
    assert "满足本次需求" in dialog.capacityHint.text()
    assert row(dialog, "consumer").text().startswith("【自动消费者】")
    assert row(dialog, "consumer").checkState() == Qt.Unchecked
    assert "main / 槽位 faces" in row(dialog, "consumer").toolTip()
    assert "自动消费者" not in row(dialog, "tick").text()
    assert "无需勾选" in dialog.queueHint.text()
    assert "Runtime 已占用容量" in dialog.capacityHint.text()
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    assert document.model_dump() == before


@pytest.mark.parametrize("slots,selected,expected", [
    (("faces", "archive"), ("main",), 3),
    (("faces",), ("main", "queue"), 4),
    (("faces", "archive"), ("main", "queue"), 6),
])
def testCountsSlotsPerGroupNotDistinctWorkflows(designerApplication, slots, selected, expected):
    dialog = makeDialog(queueDocument(slots), selected, limit=expected)
    assert dialog.minimumJobCount() == expected
    assert f"= {expected} 个 Job" in dialog.capacityHint.text()
    dialog.accept()
    assert dialog.result() == QDialog.Accepted


def testRepeatedSlotIsSharedButNestedDispatchAddsItsOwnConsumer(designerApplication):
    document = queueDocument()
    duplicate = document.workflows["queue"].nodes[1].model_copy(deep=True)
    duplicate.nodeId = "again"
    duplicate.params["targets"][0]["queueCapacity"] = 1
    document.workflows["queue"].nodes.insert(2, duplicate)
    dialog = makeDialog(document)
    assert dialog.minimumJobCount() == 2
    document.workflows["consumer"].nodes.insert(1, WorkflowNode(nodeId="nested", kind="dispatch",
        params=dict(targets=[dict(targetSlotId="results", workflowId="other", inputs={}, queueCapacity=64)])))
    nested = makeDialog(document, limit=3)
    assert nested.minimumJobCount() == 3
    assert "自动消费者" in row(nested, "other").text()
    assert "results" in row(nested, "other").toolTip()


def testExtraConsumerSelectionWarnsButAllowsIntentionalIndependentJob(designerApplication):
    dialog = makeDialog(queueDocument(), limit=3)
    row(dialog, "consumer").setCheckState(Qt.Checked)
    assert "已额外勾选 1 个自动消费者" in dialog.queueHint.text()
    assert "不会接入原队列" in dialog.queueHint.text()
    assert dialog.minimumJobCount() == 3
    dialog.accept()
    assert dialog.result() == QDialog.Accepted


def testInsufficientLimitHasActionableFeedbackAndRecovers(designerApplication):
    dialog = makeDialog(queueDocument(), limit=1, allowPersist=True)
    assert "至少需要 2 个 Job，当前上限 1" in dialog.capacityHint.text()
    dialog.accept()
    assert dialog.result() != QDialog.Accepted
    assert "至少需要 2 个 Job" in dialog.notice.text()
    dialog.limit.setValue(2)
    assert "满足本次需求" in dialog.capacityHint.text()
    assert "超过" not in dialog.notice.text()
    dialog.accept()
    assert dialog.result() == QDialog.Accepted


def testCheckboxAndLimitControlsCorrectInsufficientSelection(designerApplication):
    dialog = makeDialog(queueDocument(), allowPersist=True)
    dialog.show()
    designerApplication.processEvents()
    rect = dialog.targets.visualItemRect(row(dialog, "consumer"))
    QTest.mouseClick(dialog.targets.viewport(), Qt.LeftButton, pos=QPoint(rect.left() + 10, rect.center().y()))
    assert row(dialog, "consumer").checkState() == Qt.Checked
    assert dialog.minimumJobCount() == 3 and "已额外勾选" in dialog.queueHint.text()
    ok = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)
    QTest.mouseClick(ok, Qt.LeftButton)
    assert dialog.result() != QDialog.Accepted and dialog.isVisible()
    dialog.limit.setFocus()
    dialog.limit.selectAll()
    QTest.keyClicks(dialog.limit, "3")
    QTest.keyClick(dialog.limit, Qt.Key_Tab)
    assert dialog.maximum() == 3 and "超过" not in dialog.notice.text()
    QTest.mouseClick(ok, Qt.LeftButton)
    assert dialog.result() == QDialog.Accepted


def testTemporarySelectionUsesEffectiveLimitAndRestoreRefreshesGuidance(designerApplication):
    dialog = makeDialog(queueDocument(), limit=2, allowPersist=True, projectTargets=["main"])
    dialog.limit.setValue(5)
    row(dialog, "consumer").setCheckState(Qt.Checked)
    dialog.temporary.setChecked(True)
    assert dialog.maximum() == 2 and not dialog.limit.isEnabled()
    assert "当前上限 2" in dialog.capacityHint.text()
    assert "取消“仅本次临时使用”" in dialog.capacityHint.text()
    dialog.accept()
    assert dialog.result() != QDialog.Accepted
    dialog.restoreProjectTargets()
    assert dialog.selectedWorkflowIds() == ["main"] and dialog.maximum() == 2
    assert "已额外勾选" not in dialog.queueHint.text()
    assert "满足本次需求" in dialog.capacityHint.text()
    dialog.accept()
    assert dialog.result() == QDialog.Accepted


def testUnlimitedAndNoDispatchSelectionsRetainExistingBehavior(designerApplication):
    dialog = makeDialog(queueDocument(), selected=["other"], limit=1)
    assert dialog.minimumJobCount() == 1 and dialog.queueHint.isHidden()
    assert "0 个自动消费者 Job = 1 个 Job" in dialog.capacityHint.text()
    row(dialog, "main").setCheckState(Qt.Checked)
    dialog.unlimited.setChecked(True)
    assert dialog.maximum() is None
    assert "未限制 Job 数量" in dialog.capacityHint.text()
    assert "并发不足" not in dialog.capacityHint.text()
    dialog.accept()
    assert dialog.result() == QDialog.Accepted


@pytest.mark.parametrize("problem", ["target", "mapping"])
def testIncompleteQueueIsUnknownNotZeroAndDoesNotBlockDraftSettings(designerApplication, problem):
    document = queueDocument()
    target = document.workflows["queue"].nodes[1].params["targets"][0]
    if problem == "target":
        target["workflowId"] = "missing"
    else:
        target["inputs"] = {"image": "missing"}
    dialog = makeDialog(document)
    assert "暂无法完整计算" in dialog.capacityHint.text()
    assert "0 个自动消费者" not in dialog.capacityHint.text()
    assert "main" in dialog.capacityHint.toolTip()
    assert "Dispatch" in dialog.capacityHint.toolTip()
    assert "满足本次需求" not in dialog.capacityHint.text()
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    row(dialog, "main").setCheckState(Qt.Unchecked)
    assert "尚未选择" in dialog.capacityHint.text()
    row(dialog, "other").setCheckState(Qt.Checked)
    assert not dialog.slotErrors and dialog.minimumJobCount() == 1


def testNoSelectionAndLegacyCallerDoNotClaimFullCapacityCheck(designerApplication):
    dialog = RunTargetsDialog({"main": {"name": "Main"}}, [], 2)
    dialog.accept()
    assert dialog.result() != QDialog.Accepted and "至少选择" in dialog.notice.text()
    row(dialog, "main").setCheckState(Qt.Checked)
    assert "自动消费者数量未核算" in dialog.capacityHint.text()
    dialog.accept()
    assert dialog.result() == QDialog.Accepted


def testDesignerOpenUsesCurrentDraftWithoutSavingOrStarting(designerApplication, tmp_path, monkeypatch):
    document = queueDocument()
    WorkflowCompiler().compile(document)
    path = tmp_path / "queue.emoproj"
    saveProject(path, document.model_dump(mode="json"))
    assert loadProjectDocument(path) == document
    window = MainWindow(RuntimeClientStub())
    window.setAttribute(Qt.WA_DontShowOnScreen)
    assert window.loadProjectDirectory(str(path), confirmed=True)
    before = window.pageCoordinator.session.document().model_dump()

    def inspect(dialog):
        assert dialog.document.schemaVersion == "2.7"
        assert dialog.minimumJobCount() == 2
        assert "自动消费者" in row(dialog, "consumer").text()
        return False

    monkeypatch.setattr(RunTargetsDialog, "exec_", inspect)
    window.openRunTargetsDialog()
    assert window.pageCoordinator.session.document().model_dump() == before
    assert not window.pageCoordinator.session.dirty
    assert not window.isJobRunning and not window.currentJobId


def testOperatorWindowPassesLoadedDocumentAndKeepsLimitReadOnly(designerApplication, monkeypatch):
    window = QWidget()
    document = queueDocument()
    window.controller = SimpleNamespace(document=document, workflows=document.workflows)
    window.selectedWorkflowIds = ["main"]
    window.closing = False
    window.chooseTargets = lambda: OperatorWindow.chooseTargets(window)
    window._deferCommand = lambda callback: False
    window._controls = lambda: None

    def inspect(dialog):
        assert dialog.document is document and dialog.minimumJobCount() == 2
        assert not dialog.limit.isEnabled() and not dialog.unlimited.isEnabled()
        assert "自动消费者" in row(dialog, "consumer").text()
        return False

    monkeypatch.setattr(RunTargetsDialog, "exec_", inspect)
    OperatorWindow.chooseTargets(window)
    assert window.selectedWorkflowIds == ["main"]


@pytest.mark.parametrize("width,longNames", [(600, False), (480, True)])
def testNativeLayoutForNormalWarningAndInvalidStates(designerApplication, tmp_path, width, longNames):
    document = queueDocument()
    if longNames:
        for workflow in document.workflows.values():
            workflow.name += " Long Workflow " * 12
    dialog = makeDialog(document, allowPersist=True, projectTargets=["main"])
    dialog.resize(width, 560)
    dialog.show()
    for state in ("normal", "duplicate", "capacity", "unknown"):
        if state == "duplicate":
            row(dialog, "consumer").setCheckState(Qt.Checked)
        elif state == "capacity":
            dialog.limit.setValue(1)
        elif state == "unknown":
            document.workflows["queue"].nodes[1].params["targets"][0]["workflowId"] = "missing"
            row(dialog, "consumer").setCheckState(Qt.Unchecked)
        for _ in range(5):
            designerApplication.processEvents()
        labels = [label for label in dialog.findChildren(QLabel) if label.isVisible()]
        for label in labels:
            assert label.height() >= label.heightForWidth(label.width())
            rect = label.rect()
            rect.moveTopLeft(label.mapTo(dialog, QPoint()))
            assert dialog.rect().contains(rect)
        for label in (dialog.queueHint, dialog.capacityHint, dialog.notice):
            if label.isVisible():
                assert not label.geometry().intersects(dialog.targets.geometry())
        buttons = dialog.findChild(QDialogButtonBox)
        assert not buttons.geometry().intersects(dialog.notice.geometry())
        assert dialog.targets.height() >= 110
        assert not dialog.targets.horizontalScrollBar().isVisible()
        assert dialog.width() == width
        capture = dialog.grab()
        assert not capture.isNull()
        assert capture.save(str(tmp_path / f"run-targets-{width}-{state}.png"))
    dialog.close()
