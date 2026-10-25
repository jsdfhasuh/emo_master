"""Job failure notifications preserve the originating node and Job identity."""
from types import SimpleNamespace
import json
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


def resourceFailureReply(code='E_RESOURCE_MISSING'):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    resources = {'face_model': dict(path='models/yolov8n-face-detect.onnx', purpose='model'),
                 'sample': dict(path='samples/astronaut.png', purpose='input_image'),
                 'empty': dict(path='samples/empty.png', purpose='input_image')}
    field = {'E_RESOURCE_SIZE': 'size', 'E_RESOURCE_DIGEST': 'sha256'}.get(code, 'path')
    issues = [dict(code=code, projectId='online-project', fieldPath=f'resources.items.{key}.{field}',
                   message=f'resource validation failed: {key} (D:/Runtime/online-projects/' +
                           'a' * 64 + '/' + value['path'] + ')') for key, value in resources.items()]
    return pb.StartJobReply(ok=False, status='REJECTED', message='; '.join(issue['message'] for issue in issues),
        diagnostics_json=json.dumps(dict(issues=issues, resources=resources)))


def capacityFailureReply(required=2, active=2, limit=2):
    from emo_master.apps.runtime.grpc_server.generated import runtime_pb2 as pb
    return pb.StartJobReply(ok=False, status='REJECTED',
        message=f'E_MAX_CONCURRENT_JOBS: required={required}, active={active}, limit={limit}',
        diagnostics_json=json.dumps(dict(code='E_MAX_CONCURRENT_JOBS', capacity=dict(
            requiredJobs=required, consumerJobs=required - 1, activeJobs=active,
            maxConcurrentJobs=limit, availableJobs=max(0, limit - active)))))


@pytest.mark.parametrize('required,active,limit', [(2, 2, 2), (3, 0, 2), (1, 2, 2)])
def testCapacityPopupUsesRuntimeCountsAndExplainsRecovery(
        ownedDesignerWindow, failureBoxes, required, active, limit):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    window.workflowStore.runtime['maxConcurrentJobs'] = 99
    reply = capacityFailureReply(required, active, limit)
    window.runtimeController.runs[''].controller._onJobStatus(reply)
    box, = failureBoxes
    main = box.informativeText()
    assert f'本次需要 {required} 个 Job' in main
    assert f'自动消费者 {required - 1} 个' in main
    assert f'Runtime 已占用 {active} 个，上限 {limit} 个' in main
    assert '99' not in main and 'TCP' not in main
    assert '运行目标与并发' in main and '工程与 Runtime 配置' in main
    if active:
        assert '停止对应入口' in main and '待资源释放' in main
    else:
        assert '停止对应入口' not in main
    if required > limit:
        assert f'至少需要上限 {required}' in main
    else:
        assert f'至少需要 {active + required}' in main
    assert reply.message in box.detailedText() and 'E_MAX_CONCURRENT_JOBS' in box.detailedText()
    assert box.text() == '作业未能启动'
    assert all(button.text() != '定位失败节点' for button in box.buttons())


def testIncompleteCapacityDiagnosticPreservesRawFailure(ownedDesignerWindow, failureBoxes):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    reply = capacityFailureReply()
    reply.diagnostics_json = json.dumps(dict(code='E_MAX_CONCURRENT_JOBS'))
    window.runtimeController.runs[''].controller._onJobStatus(reply)
    box, = failureBoxes
    assert reply.message in box.informativeText()


def testRejectedStartKeepsStructuredIssuesAndRawReasonWithoutInventingNode():
    controller, failures, _ = controllerForFailures()
    reply = resourceFailureReply()
    controller._onJobStatus(reply)
    controller._onWorkerFailed(reply.message)
    failure, = failures
    assert failure['payload']['diagnostics'] == json.loads(reply.diagnostics_json)
    assert failure['message'] == reply.message and failure['status'] == 'REJECTED'
    assert not failure.get('nodeId') and not failure['jobId']


@pytest.mark.parametrize('diagnostics', ['not-json', '[]'], ids=['syntax', 'list'])
def testInvalidOptionalStartupDiagnosticsKeepOriginalFailure(diagnostics):
    controller, failures, _ = controllerForFailures()
    controller._onJobStatus(SimpleNamespace(status='REJECTED', message='original failure',
                                              diagnostics_json=diagnostics))
    assert failures[0]['message'] == 'original failure'


def testTerminalReplyDoesNotReplaceOriginatingNodeDiagnostics():
    controller, failures, _ = controllerForFailures()
    accepted(controller)
    event(controller)
    reply = resourceFailureReply()
    reply.status = 'FAILED'
    controller._onJobStatus(reply)
    assert failures[0]['nodeId'] == 'broken'
    assert failures[0]['payload']['diagnostics'] == {'path': 'missing.png'}


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


@pytest.mark.parametrize('code,phrase', [
    ('E_RESOURCE_MISSING', '缺少工程所需的文件'),
    ('E_RESOURCE_SIZE', '大小不符'), ('E_RESOURCE_DIGEST', '内容不符'),
    ('E_RESOURCE_INPUT', '路径无效'),
], ids=['missing', 'size', 'digest', 'input'])
def testStartupPopupSummarizesRuntimeFilesNotCurrentDraft(
        ownedDesignerWindow, failureBoxes, code, phrase):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    addNode(window, 1)
    window.flowModel.selectNode('shared-node')
    # The rejected Runtime document can differ from the open draft. Do not
    # attribute its files to the selected node or borrow this other filename.
    window.workflowStore.projectExtensions['resources']['items']['face_model'] = dict(
        path='models/draft-other.onnx', purpose='model', sha256='0' * 64, size=1)
    reply = resourceFailureReply(code)
    window.runtimeController.runs[''].controller._onJobStatus(reply)
    box, = failureBoxes
    main = box.informativeText()
    assert phrase in main and '启动 / 工程准备' in main
    assert '模型：models/yolov8n-face-detect.onnx' in main
    assert '输入图片：samples/astronaut.png' in main and 'samples/empty.png' in main
    assert 'draft-other' not in main and 'shared-node' not in main
    assert 'online-projects' not in main and 'resource validation failed' not in main
    assert len(main) < 600
    if code == 'E_RESOURCE_INPUT':
        assert '修正工程资源路径' in main and '上传候选资源' not in main
    else:
        assert '上传候选资源' in main
    assert reply.message in box.detailedText() and code in box.detailedText()
    assert all(button.text() != '定位失败节点' for button in box.buttons())


def testCorruptUploadedResourceIsNotPresentedAsJustMissingModel(ownedDesignerWindow, failureBoxes):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    window.runtimeController.runs[''].controller._onJobStatus(SimpleNamespace(
        status='REJECTED', message='completed upload is missing or corrupt',
        diagnostics_json=json.dumps({'code': 'E_RESOURCE_TRANSFER_DIGEST'})))
    box, = failureBoxes
    assert '已上传的工程资源缺失或损坏' in box.informativeText()
    assert '确认修复并重新上传' in box.informativeText()
    assert 'E_RESOURCE_TRANSFER_DIGEST' in box.detailedText()


def testResourceSummaryKeepsLongListsInDetails(ownedDesignerWindow, failureBoxes):
    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    reply = resourceFailureReply()
    diagnostics = json.loads(reply.diagnostics_json)
    for index in range(8):
        key = f'extra.{index}'
        diagnostics['resources'][key] = dict(path=f'samples/{index}.png', purpose='input_image')
        diagnostics['issues'].append(dict(code='E_RESOURCE_MISSING', fieldPath=f'resources.items.{key}.path'))
    reply.diagnostics_json = json.dumps(diagnostics)
    window.runtimeController.runs[''].controller._onJobStatus(reply)
    box, = failureBoxes
    assert box.informativeText().count('• ') == 6
    assert '另有 5 项' in box.informativeText()
    assert 'samples/7.png' not in box.informativeText() and 'samples/7.png' in box.detailedText()


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


@pytest.mark.parametrize('kind', ['resource', 'capacity'])
@pytest.mark.skipif(os.environ.get('QT_QPA_PLATFORM') != 'windows',
                   reason='QMessageBox rendering requires the Windows Qt platform; offscreen show crashes')
def testStructuredStartupPopupNativeLayout(ownedDesignerWindow, monkeypatch, tmp_path, kind):
    from PySide2.QtCore import Qt
    from PySide2.QtWidgets import QApplication, QLabel, QTextEdit

    window = ownedDesignerWindow(Client(), settingsStore=Settings())
    window.setAttribute(Qt.WA_DontShowOnScreen)
    boxes = []
    realOpen = QMessageBox.open

    def openHidden(box):
        box.setAttribute(Qt.WA_DontShowOnScreen)
        boxes.append(box)
        realOpen(box)

    monkeypatch.setattr(QMessageBox, 'open', openHidden)
    reply = resourceFailureReply() if kind == 'resource' else capacityFailureReply()
    code = 'E_RESOURCE_MISSING' if kind == 'resource' else 'E_MAX_CONCURRENT_JOBS'
    window.runtimeController.runs[''].controller._onJobStatus(reply)
    QApplication.processEvents()
    box, = boxes
    assert box.isVisible() and box.text() == '作业未能启动'
    for name in ('qt_msgbox_label', 'qt_msgbox_informativelabel'):
        label = box.findChild(QLabel, name)
        assert label is not None and label.isVisible()
        bounds = label.fontMetrics().boundingRect(label.contentsRect(), Qt.TextWordWrap, label.text())
        assert bounds.height() <= label.contentsRect().height() + 2
        assert label.visibleRegion().boundingRect() == label.rect()
    for button in box.buttons():
        assert button.fontMetrics().horizontalAdvance(button.text()) <= button.contentsRect().width()
    assert box.width() <= box.screen().availableGeometry().width()
    assert box.height() <= box.screen().availableGeometry().height()
    assert box.grab().save(str(tmp_path / f'{kind}-start-failure.png'))
    details = next(button for button in box.buttons() if button.text() == '详细信息')
    details.click()
    QApplication.processEvents()
    viewer = box.findChild(QTextEdit)
    assert viewer.isVisible() and details.text() == '收起详情'
    assert reply.message in viewer.toPlainText()
    assert code in viewer.toPlainText()
    assert box.grab().save(str(tmp_path / f'{kind}-start-failure-details.png'))
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
