"""Job failure notifications preserve the originating node and Job identity."""
from types import SimpleNamespace
import os

import pytest
from PySide2.QtWidgets import QMessageBox

from emo_master.apps.designer.controllers.runtime_controller import RuntimeController
from emo_master.apps.designer.ui.runtime_panel import RuntimePanelState
from tests.designer.test_runtime_log_usability import Client, Settings, addNode


def controllerForFailures():
    failures, running = [], []
    job = [None]
    controller = RuntimeController(
        runtimeClient=None, runtimePanelState=RuntimePanelState(),
        appendLog=lambda *_: None, refreshRuntimePanelView=lambda: None,
        updateToolbarState=lambda: None, syncRuntimeProjectBeforeRun=lambda: True,
        applyRuntimeEventToNode=lambda _: None,
        setCurrentJobId=lambda value: job.__setitem__(0, value),
        setIsJobRunning=running.append, getLoadedProjectPath=lambda: 'project',
        getCurrentJobId=lambda: job[0], getEntryWorkflowId=lambda: 'main',
        onRunFailed=failures.append,
    )
    return controller, failures, running


def accepted(controller, jobId='job-1'):
    controller._onJobAccepted(SimpleNamespace(ok=True, job_id=jobId, status='RUNNING'))


def event(controller, kind='node.failed', *, node='broken', workflow='child', job='job-1'):
    controller._onRuntimeEvent(SimpleNamespace(
        event_type=kind, job_id=job, workflow_id=workflow, node_id=node,
        code='E_TEST', message='specific failure', payload={'diagnostics': {'path': 'missing.png'}},
        node_run_id='call-2', workflow_run_id='child-run', iteration_path=(2,),
    ))


def testFailureUsesFirstConcreteNodeAndOnlyNotifiesAtTerminalStatus():
    controller, failures, running = controllerForFailures()
    accepted(controller)
    event(controller)
    event(controller, node='outer-call', workflow='main')
    event(controller, 'job.failed', node='', workflow='main')
    assert not failures
    controller._onJobStatus(SimpleNamespace(status='FAILED', message='job failure'))
    controller._onJobStatus(SimpleNamespace(status='FAILED', message='job failure'))
    controller._onWorkerFailed('duplicate failure')
    assert len(failures) == 1
    assert failures[0]['nodeId'] == 'broken'
    assert failures[0]['workflowId'] == 'child'
    assert failures[0]['message'] == 'specific failure'
    assert failures[0]['jobMessage'] == 'job failure'
    assert failures[0]['iterationPath'] == (2,)
    assert failures[0]['payload']['diagnostics']['path'] == 'missing.png'
    assert not controller._jobActive and running[-1] is False


@pytest.mark.parametrize('status', ['COMPLETED', 'ABORTED', 'RUNNING', 'STOPPING'])
def testNonfailedJobDoesNotNotify(status):
    controller, failures, _ = controllerForFailures()
    accepted(controller)
    event(controller)
    controller._onJobStatus(SimpleNamespace(status=status, message='status'))
    assert not failures


def testRejectedStartNotifiesOnceWithoutInventingNode():
    controller, failures, _ = controllerForFailures()
    controller._onJobStatus(SimpleNamespace(status='REJECTED', message='resource missing: face_model'))
    controller._onWorkerFailed('resource missing: face_model')
    assert len(failures) == 1
    assert not failures[0].get('nodeId')
    assert failures[0]['workflowId'] == 'main'
    assert failures[0]['status'] == 'REJECTED'


def testWorkerFailureBeforeAcceptanceNotifiesButObserverDisconnectDoesNot():
    controller, failures, _ = controllerForFailures()
    controller._onWorkerFailed('preparation failed')
    assert len(failures) == 1
    other, failures, _ = controllerForFailures()
    accepted(other)
    other._onWorkerFailed('observer disconnected')
    assert not failures and other._startUncertain


def testFailureIgnoresOtherJobAndClosingWindow():
    controller, failures, _ = controllerForFailures()
    accepted(controller)
    event(controller, job='old-job')
    controller._onJobStatus(SimpleNamespace(status='FAILED', message='current failure'))
    assert not failures[0].get('nodeId')
    controller, failures, _ = controllerForFailures()
    controller._closing = True
    controller._onWorkerFailed('closing')
    assert not failures


def testNewStartResetsNotificationAndOldNode(monkeypatch):
    import emo_master.apps.designer.controllers.runtime_controller as module

    class Worker:
        def __init__(self, **_):
            for name in ('jobAccepted', 'eventReceived', 'statusChanged', 'failed'):
                setattr(self, name, SimpleNamespace(connect=lambda _: None))

        def start(self):
            pass

    monkeypatch.setattr(module, 'RuntimeWorker', Worker)
    controller, failures, _ = controllerForFailures()
    accepted(controller)
    event(controller)
    controller._onJobStatus(SimpleNamespace(status='FAILED', message='first'))
    controller.startJob()
    controller._onWorkerFailed('second start failed')
    assert len(failures) == 2
    assert not failures[1].get('nodeId')
    assert failures[1]['message'] == 'second start failed'


def testPreparationFailureDoesNotBorrowPreviousJob():
    controller, failures, _ = controllerForFailures()
    accepted(controller)
    event(controller)
    controller._onJobStatus(SimpleNamespace(status='FAILED', message='first'))
    controller.getLegacySnapshotPolicy = lambda: 'INVALID'
    controller.startJob()
    assert len(failures) == 2
    assert failures[1]['jobId'] == '' and not failures[1].get('nodeId')
    assert failures[1]['status'] == 'REJECTED'
    assert controller.runtimePanelState.lastMessage == failures[1]['message']


@pytest.fixture
def failureBoxes(monkeypatch):
    boxes = []
    monkeypatch.setattr(QMessageBox, 'open', lambda box: boxes.append(box))
    return boxes


def failWindow(window, *, node='shared-node', workflow='main', message='image missing'):
    controller = window.runtimeController.runs[''].controller
    accepted(controller)
    controller._onRuntimeEvent(SimpleNamespace(
        event_type='node.failed' if node else 'job.failed', job_id='job-1',
        workflow_id=workflow, node_id=node, message=message, code='E_TEST', payload={},
    ))
    controller._onJobStatus(SimpleNamespace(status='FAILED', message=message))


def testPopupNamesActualChildNodeAndLocatesWithoutChangingDraft(
    ownedDesignerWindow, failureBoxes,
):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    child = window.workflowStore.addWorkflow('Child inspection')
    window.activateWorkflow(child)
    addNode(window, 2)
    window.flowModel.nodes['shared-node'].displayName = 'Broken image loader'
    window.workflowController.captureActiveWorkflow()
    window.activateWorkflow('main')
    window.pageCoordinator.sync()
    before = window.pageCoordinator.session.payload()
    failWindow(window, workflow=child)
    assert len(failureBoxes) == 1
    box = failureBoxes[0]
    assert box.parent() is window
    assert 'Broken image loader' in box.informativeText()
    assert 'Child inspection' in box.informativeText()
    assert 'shared-node' in box.informativeText()
    assert 'image missing' in box.informativeText()
    assert 'job-1' in box.detailedText()
    next(button for button in box.buttons() if button.text() == '定位失败节点').click()
    assert window.activeWorkflowId == child
    assert window.flowModel.selectedNodeId == 'shared-node'
    assert window.pageCoordinator.session.payload() == before


def testProjectFailureHasFullDetailsAndNoFalseNode(ownedDesignerWindow, failureBoxes):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    window.flowModel.selectNode('shared-node')
    message = 'resource missing: face_model (' + 'long-path/' * 200 + 'model.onnx)'
    failWindow(window, node='', message=message)
    box = failureBoxes[0]
    assert '未报告失败节点' in box.informativeText()
    assert 'shared-node' not in box.informativeText()
    assert message in box.detailedText()
    assert len(box.informativeText()) < 1600
    assert all(button.text() != '定位失败节点' for button in box.buttons())


def testBackgroundWorkflowFailureUsesItsOwnJob(ownedDesignerWindow, failureBoxes):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    other = window.workflowStore.addWorkflow('Other workflow')
    window.activateWorkflow(other)
    addNode(window, 2)
    window.activateWorkflow('main')
    manager = window.runtimeController
    run = manager._create(other)
    accepted(run.controller, jobId='background-job')
    event(run.controller, node='shared-node', workflow=other, job='background-job')
    run.controller._onJobStatus(SimpleNamespace(status='FAILED', message='background failure'))
    failWindow(window)
    assert len(failureBoxes) == 2
    assert 'Other workflow' in failureBoxes[0].informativeText()
    assert 'background-job' in failureBoxes[0].detailedText()
    assert 'job-1' in failureBoxes[1].detailedText()


@pytest.mark.parametrize('nodeFailure', [True, False])
@pytest.mark.skipif(os.environ.get('QT_QPA_PLATFORM') != 'windows',
                   reason='QMessageBox rendering requires the Windows Qt platform; offscreen show crashes')
def testFailurePopupNativeLayout(ownedDesignerWindow, monkeypatch, tmp_path, nodeFailure):
    from PySide2.QtCore import Qt
    from PySide2.QtWidgets import QApplication, QLabel, QTextEdit

    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    window.setAttribute(Qt.WA_DontShowOnScreen)
    addNode(window, 1)
    window.flowModel.nodes['shared-node'].displayName = '人脸检测：模型推理'
    realOpen = QMessageBox.open
    boxes = []

    def openHidden(box):
        box.setAttribute(Qt.WA_DontShowOnScreen)
        boxes.append(box)
        realOpen(box)

    monkeypatch.setattr(QMessageBox, 'open', openHidden)
    reason = '模型文件不存在：D:\\工程目录\\在线工程\\models\\face-model.onnx'
    if not nodeFailure:
        reason = 'resource missing: face_model (' + 'long-path/' * 200 + 'model.onnx)'
    failWindow(window, node='shared-node' if nodeFailure else '', message=reason)
    QApplication.processEvents()
    box = boxes[0]
    assert box.isVisible()
    for name in ('qt_msgbox_label', 'qt_msgbox_informativelabel'):
        label = box.findChild(QLabel, name)
        assert label is not None and label.isVisible()
        rect = label.contentsRect()
        bounds = label.fontMetrics().boundingRect(rect, Qt.TextWordWrap, label.text())
        assert bounds.height() <= rect.height() + 2
        assert label.visibleRegion().boundingRect() == label.rect()
    for button in box.buttons():
        assert button.fontMetrics().horizontalAdvance(button.text()) <= button.contentsRect().width()
    assert box.height() <= box.screen().availableGeometry().height()
    assert box.grab().save(str(tmp_path / f'failure-{nodeFailure}.png'))
    details = next(button for button in box.buttons() if button.text() == '详细信息')
    details.click()
    QApplication.processEvents()
    viewer = box.findChild(QTextEdit)
    assert viewer.isVisible() and details.text() == '收起详情'
    assert reason in viewer.toPlainText()
    assert box.grab().save(str(tmp_path / f'failure-details-{nodeFailure}.png'))
    details.click()
    QApplication.processEvents()
    assert not viewer.isVisible() and details.text() == '详细信息'
    box.close()


def testPopupDoesNotBorrowNodeNameFromOtherProject(ownedDesignerWindow, failureBoxes):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    window._showRunFailure(dict(
        eventType='node.failed', jobId='unknown', projectId='another-project',
        workflowId='main', nodeId='shared-node', message='failure',
    ))
    box = failureBoxes[0]
    assert '数字' not in box.informativeText()
    assert 'shared-node' in box.informativeText()
    assert all(button.text() != '定位失败节点' for button in box.buttons())
