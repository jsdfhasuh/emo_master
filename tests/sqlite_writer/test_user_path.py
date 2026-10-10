"""Native controls and real loopback/spawn jobs; no hand-edited node params."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

import emo_master  # noqa: F401 - preload native dependencies before Qt
import grpc
from PySide2.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QTimer, Qt
from PySide2.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QDialogButtonBox, QPlainTextEdit

from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.runtime.main import createRuntimeServer
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc
from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.core.contracts.sqlite_writer import OPERATOR_ID
from tests.sqlite_writer.test_dependencies import project


def waitQt(predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Qt/Runtime operation did not finish within test watchdog')


def doubleNode(window, nodeId):
    item = window.flowScene._nodeItems[nodeId]
    window.flowView.setZoomFactor(.7)
    window.flowView.centerOn(item)
    QApplication.processEvents()
    point = window.flowView.mapFromScene(item.mapToScene(QPointF(item.rect().width() / 2, 20)))
    assert window.flowView.viewport().rect().contains(point), (point, window.flowView.viewport().rect(), item.pos())
    hit = window.flowView.itemAt(point)
    while hit is not None and hit.parentItem() is not None:
        hit = hit.parentItem()
    assert hit is item, (nodeId, point, hit, item.pos())
    QTest.mouseClick(window.flowView.viewport(), Qt.LeftButton, pos=point)
    QTest.mouseDClick(window.flowView.viewport(), Qt.LeftButton, pos=point)
    QTest.mouseRelease(window.flowView.viewport(), Qt.LeftButton, pos=point)
    QApplication.processEvents()
    return window.nodeParamDialog


def dragOperator(window, operatorId, destination, monkeypatch):
    import emo_master.apps.designer.ui.operator_bubble as cards
    window.expandSidebar()
    window.operatorBubble.searchInput.setText(operatorId)
    QApplication.processEvents()
    button = window.operatorBubble._buttons[0]
    viewport = window.flowView.viewport()
    old = set(window.flowModel.nodes)
    def transfer(drag, *_args):
        mime = drag.mimeData()
        QApplication.sendEvent(viewport, QDragEnterEvent(destination, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
        QApplication.sendEvent(viewport, QDragMoveEvent(destination, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
        event = QDropEvent(QPointF(destination), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(viewport, event)
        assert event.isAccepted()
        return Qt.CopyAction
    with monkeypatch.context() as injection:
        injection.setattr(cards.QDrag, 'exec_', transfer)
        point = QPoint(20, 20)
        QTest.mousePress(button, Qt.LeftButton, pos=point)
        event = QMouseEvent(QEvent.MouseMove, QPointF(point + QPoint(40, 0)), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(button, event)
        QTest.mouseRelease(button, Qt.LeftButton, pos=point + QPoint(40, 0))
    return (set(window.flowModel.nodes) - old).pop()


def chooseNodeSource(controller, row, nodeId, port):
    errors = []
    def choose():
        dialog = QApplication.activeModalWidget()
        try:
            assert dialog is not None and hasattr(dialog, 'tree')
            dialog.tabs.setCurrentIndex(0)
            item = next(dialog.tree.topLevelItem(i).child(j)
                for i in range(dialog.tree.topLevelItemCount())
                for j in range(dialog.tree.topLevelItem(i).childCount())
                if dialog.tree.topLevelItem(i).child(j).data(0, Qt.UserRole) == {'kind': 'node_output', 'nodeId': nodeId, 'port': port})
            dialog.tree.scrollToItem(item)
            QTest.mouseClick(dialog.tree.viewport(), Qt.LeftButton, pos=dialog.tree.visualItemRect(item).center())
            box = dialog.findChild(QDialogButtonBox)
            QTest.mouseClick(box.button(QDialogButtonBox.Ok), Qt.LeftButton)
        except Exception as error:
            errors.append(str(error))
            if dialog:
                dialog.reject()
    QTimer.singleShot(40, choose)
    QTest.mouseClick(controller.fields.cellWidget(row, 1), Qt.LeftButton)
    waitQt(lambda: not controller._dialogs)
    assert not errors, errors


def testCompleteSqliteDesignerPath(ownedDesignerWindow, tmp_path, monkeypatch):
    out = Path(os.environ['EMO_SQLITE_SCREEN_DIR']) if os.environ.get('EMO_SQLITE_SCREEN_DIR') else None
    screenshots = {}
    root = tmp_path / '工程 中文 空格'
    root.mkdir()
    blank = project(nodes=[])
    blank['workflows']['main']['inputs'] = {}
    saveProject(root, blank)
    runtime = RuntimeService(dbPath=tmp_path / 'runtime-state.sqlite3', workspaceRoot=tmp_path / 'jobs')
    server, port, _ = createRuntimeServer(port=0, runtimeService=runtime)
    server.start()
    channel = grpc.insecure_channel(f'127.0.0.1:{port}')
    grpc.channel_ready_future(channel).result(timeout=5)
    client = RuntimeClient(rpc.RuntimeServiceStub(channel), runtimeTarget=f'127.0.0.1:{port}')
    records = {'platform': QApplication.platformName(), 'jobs': [], 'rows': [], 'screenSizes': [],
               'devicePixelRatio': QApplication.primaryScreen().devicePixelRatio()}
    def shot(widget, name):
        QApplication.processEvents()
        if out:
            path = out / (name + '.png')
            assert widget.grab().save(str(path))
            screenshots[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    with ownedDesignerWindow as windows:
        window = windows(client)
        try:
            window.resize(1600, 900)
            assert window.loadProjectDirectory(str(root))
            window.show()
            waitQt(lambda: window.operatorCatalogController.state == 'ready')
            viewport = window.flowView.viewport()
            sourceId = dragOperator(window, 'vision.value.number', QPoint(viewport.width() // 3, 80), monkeypatch)
            sourceEditor = doubleNode(window, sourceId)
            assert sourceEditor is not None and sourceEditor._schemaForm is not None
            sourceEditor._schemaForm._controls['value'].setValue(7)
            QTest.mouseClick(sourceEditor._applyButton, Qt.LeftButton)
            assert window.flowModel.nodes[sourceId].params['value'] == 7
            sourceEditor.forceClose()
            writerId = dragOperator(window, OPERATOR_ID, QPoint(viewport.width() * 2 // 3, 180), monkeypatch)
            window.autoLayoutNodes()
            shot(window, '00-drop-before-double')
            writerEditor = doubleNode(window, writerId)
            controller = writerEditor._controller
            assert controller is not None and controller.__class__.__name__ == 'SqliteWriterEditorController', (
                writerId, writerEditor.key, window.operatorEditorManager.keys(),
                window.flowView.viewport().size(), window._operatorDefinition(OPERATOR_ID),
                writerEditor._statusLabel.text())
            controller.path.setText('业务 数据.sqlite3')
            controller.table.setCurrentText('检测记录')
            QTest.mouseClick(controller.addButton, Qt.LeftButton)
            controller.fields.item(0, 0).setText('数量')
            controller.fields.cellWidget(0, 2).setCurrentIndex(controller.fields.cellWidget(0, 2).findData('INTEGER'))
            chooseNodeSource(controller, 0, sourceId, 'value')
            QTest.mouseClick(controller.addButton, Qt.LeftButton)
            controller.fields.selectRow(1)
            QTest.mouseClick(controller.deleteButton, Qt.LeftButton)
            assert controller.fields.rowCount() == 1
            QTest.mouseClick(controller.inspectButton, Qt.LeftButton)
            waitQt(lambda: controller._pending is None)
            assert not (root / '业务 数据.sqlite3').exists()
            assert str(root) in controller.location.text()
            previewErrors = []
            previewTimer = QTimer()
            def closePreview():
                dialog = QApplication.activeModalWidget()
                if dialog is not None and dialog.windowTitle() == '建表预览 · 尚未执行':
                    try:
                        shot(dialog, '02-create-plan')
                        assert 'CREATE TABLE' in dialog.findChild(QPlainTextEdit).toPlainText()
                    except Exception as error:
                        previewErrors.append(str(error))
                    finally:
                        dialog.reject()
                        previewTimer.stop()
            previewTimer.timeout.connect(closePreview)
            previewTimer.start(20)
            QTest.mouseClick(controller.previewButton, Qt.LeftButton)
            waitQt(lambda: controller._pending is None and controller._preview is not None and not controller._dialogs)
            assert not previewErrors and not (root / '业务 数据.sqlite3').exists()
            # The actual confirmation dialog is clicked; it is not bypassed with SQL.
            confirmationErrors = []
            def confirm():
                dialog = QApplication.activeModalWidget()
                try:
                    from emo_master.plugins.builtins.sqlite_writer.editor import InitializationDialog
                    assert isinstance(dialog, InitializationDialog)
                    assert str(root) in dialog.message.toPlainText() and '检测记录' in dialog.message.toPlainText()
                    assert 'CREATE TABLE' in dialog.message.toPlainText()
                    QTest.mouseClick(dialog.yes, Qt.LeftButton)
                except Exception as error:
                    confirmationErrors.append(str(error))
                    if dialog:
                        dialog.reject()
            QTimer.singleShot(40, confirm)
            QTest.mouseClick(controller.createButton, Qt.LeftButton)
            waitQt(lambda: controller._pending is None and not controller._dialogs and (root / '业务 数据.sqlite3').is_file())
            assert not confirmationErrors and (root / '业务 数据.sqlite3').is_file(), controller.error.text()
            assert len(controller._schema['columns']) == 3
            shot(writerEditor, '01-field-mapping')
            before = len(window.pageCoordinator.session._undo)
            QTest.mouseClick(writerEditor._applyButton, Qt.LeftButton)
            assert not writerEditor.isDirty(), controller.error.text()
            assert len(window.pageCoordinator.session._undo) == before + 1
            assert not window.flowModel.edges
            params = window.flowModel.getNodeParams(writerId)
            assert params['mappings'][0]['source'] == {'kind': 'node_output', 'nodeId': sourceId, 'port': 'value'}
            window.pageCoordinator.history()
            assert window.flowModel.getNodeParams(writerId) == {}
            window.pageCoordinator.history(redo=True)
            assert window.flowModel.getNodeParams(writerId) == params
            writerEditor.forceClose()
            for number in (7, 13):
                previousJob = window.currentJobId
                if previousJob:
                    # A terminal event can precede worker/inspection retirement.
                    # Reload still refuses to replace a resource-owned project.
                    runtime.jobSupervisor.waitForRetirement(timeoutSeconds=10)
                    assert runtime.jobSupervisor.getProcess(previousJob) is None
                if number == 13:
                    sourceEditor = doubleNode(window, sourceId)
                    sourceEditor._schemaForm._controls['value'].setValue(number)
                    QTest.mouseClick(sourceEditor._applyButton, Qt.LeftButton)
                    sourceEditor.forceClose()
                # Run action uses the existing RuntimeController and spawn worker.
                window._toolbarActions['开始运行'].trigger()
                waitQt(lambda: window.currentJobId and window.currentJobId != previousJob
                    and not window.isJobRunning and window.runtimeController._worker is None)
                job = window.currentJobId
                state = runtime.jobRepository.get(job)
                assert state.status == 'COMPLETED', state
                receipt = next(json.loads(e.payloadJson)['receipt'] for e in runtime.eventStore.readMerged(job) if e.eventType == 'sqlite.write.finished')
                assert receipt['status'] == 'COMMITTED' and receipt['execution']['nodeId'] == writerId
                records['jobs'].append(receipt)
                window.flowScene.setNodeSelected(writerId)
                window.onNodeSelectionChanged()
                panelText = window.nodeResultCoordinator.panel.values.toPlainText()
                assert 'COMMITTED' in panelText, (panelText,
                    window.nodeResultCoordinator.history.view(window.activeWorkflowId, writerId))
                panel = window.nodeResultCoordinator.panel
                panel.tabs.setCurrentWidget(panel.values)
                shot(window, f'03-run-{number}')
            with sqlite3.connect(root / '业务 数据.sqlite3') as connection:
                rows = connection.execute('SELECT id, write_id, "数量" FROM "检测记录" ORDER BY id').fetchall()
            assert [r[2] for r in rows] == [7, 13]
            assert [r[1] for r in rows] == [r['writeId'] for r in records['jobs']]
            assert records['jobs'][0]['execution']['workflowRunId'] != records['jobs'][1]['execution']['workflowRunId']
            records['rows'] = rows
            # Actual project command performs atomic save, then the same load path.
            window.saveProjectAction()
            saved = json.loads((root / 'project.json').read_text(encoding='utf-8'))
            assert saved['schemaVersion'] == '2.1'
            savedParams = next(n['params'] for n in saved['workflows']['main']['nodes'] if n['nodeId'] == writerId)
            assert savedParams == params
            # COMPLETED publishes the result, not the retirement of its owners.
            # The final reload needs the same release fence as the second run.
            runtime.jobSupervisor.waitForRetirement(timeoutSeconds=10)
            assert runtime.jobSupervisor.getProcess(job) is None
            assert not runtime.jobSupervisor.ownsJobResources(job)
            assert window.loadProjectDirectory(str(root))
            assert window.flowModel.getNodeParams(writerId) == params
            reopened = doubleNode(window, writerId)
            assert reopened._controller.collectParams() == params
            assert reopened._controller.fields.cellWidget(0, 1).property('source')['nodeId'] == sourceId
            shot(reopened, '04-reopened')
            for width, height in ((1280, 720), (1600, 900), (1920, 1080)):
                window.resize(width, height)
                reopened.resize(min(width - 80, 1100), min(height - 100, 780))
                QApplication.processEvents()
                records['screenSizes'].append({'requested': [width, height], 'actualDesigner': [window.width(), window.height()],
                    'actualEditor': [reopened.width(), reopened.height()],
                    'applyVisible': reopened._applyButton.isVisible(), 'tableWidth': reopened._controller.fields.width()})
                assert reopened.width() <= width - 80 and reopened.height() <= height - 100
                applyRect = reopened._applyButton.rect().translated(reopened._applyButton.mapTo(reopened, QPoint(0, 0)))
                assert reopened.rect().contains(applyRect)
                assert reopened._applyButton.isVisible() and reopened._controller.path.isVisible()
                # Scrollable groups, with primary buttons outside the scroll area.
                reopened._controller.scroll.ensureWidgetVisible(reopened._controller.failure)
                QApplication.processEvents()
                rulesRect = reopened._controller.failure.rect().translated(reopened._controller.failure.mapTo(reopened._controller.scroll.viewport(), QPoint(0, 0)))
                assert reopened._controller.scroll.viewport().rect().intersects(rulesRect)
                shot(reopened, f'05-editor-{width}x{height}')
            reopened.forceClose()
            window.pageCoordinator.session.markSaved()
            assert window.close()
        finally:
            window.operatorEditorManager.closeAll()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            QApplication.processEvents()
            channel.close()
            server.stop(0).wait()
            runtime.close()
    if out:
        records['artifacts'] = screenshots
        (out / 'ui-results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')


def testCopyCommandsKeepSingleTransactionAndRebindOnlyCopy(ownedDesignerWindow, tmp_path):
    from tests.sqlite_writer.test_dependencies import config, mapping
    from tests.sqlite_writer.test_backend import makeDb
    root = tmp_path / 'copy-project'
    root.mkdir()
    db = makeDb(root / 'business.sqlite3')
    payload = project()
    payload['workflows']['main']['nodes'][0]['params'] = config(db, [mapping()])
    saveProject(root, payload)
    runtime = RuntimeService()
    window = ownedDesignerWindow(RuntimeClient(runtime))
    try:
        assert window.loadProjectDirectory(str(root))
        window.flowScene.setNodeSelected('writer')
        window.onNodeSelectionChanged()
        history = len(window.pageCoordinator.session._undo)
        copied = window.duplicateSelectedNode()
        assert copied and len(window.pageCoordinator.session._undo) == history + 1
        original = window.flowModel.getNodeParams('writer')
        assert window.flowModel.getNodeParams(copied) == original
        changed = window.flowModel.getNodeParams(copied)
        changed['mappings'][0]['source'] = {'kind': 'constant', 'value': 'only copy'}
        window.applyNodeParams(copied, changed)
        assert window.flowModel.getNodeParams('writer') == original
        duplicated = window.duplicateCurrentWorkflow()
        copy = window.workflowStore.get(duplicated)
        copyWriter = next(n for n in copy.nodes if n.get('operatorId') == OPERATOR_ID)
        copyInput = next(n for n in copy.nodes if n.get('kind') == 'workflow_input')
        assert copyWriter['params']['mappings'][0]['source']['nodeId'] == copyInput['nodeId'] != 'input'
        window.pageCoordinator.session.markSaved()
    finally:
        assert window.close()
        runtime.close()
