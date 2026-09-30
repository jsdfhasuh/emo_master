"""Full native Designer path, driven by Qt clicks/drop events and schema forms."""
import hashlib
import json
import os
from pathlib import Path
import time

from PySide2.QtCore import Qt, QPointF, QCoreApplication, QEvent
from PySide2.QtGui import QDragEnterEvent, QDropEvent
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication, QPushButton, QInputDialog, QMessageBox, QFileDialog

from emo_master.apps.runtime.grpc_server.service import RuntimeService
from emo_master.apps.designer.services.runtime_client import RuntimeClient
from emo_master.apps.designer.state.project_store import saveProject
from emo_master.apps.designer.ui.main_window import MainWindow
from emo_master.apps.designer.page_designer.tools import mime
from emo_master.core.contracts.port_types import normalizePortType
from examples.runtime_pages_p2 import sampleProject
from test_preview import waitFor


def click(root, text):
    button = next(w for w in root.findChildren(QPushButton) if w.text() == text)
    QTest.mouseClick(button, Qt.LeftButton)
    QApplication.processEvents()


def drop(target, payload, point):
    data = mime(payload)
    QApplication.sendEvent(target, QDragEnterEvent(point, Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier))
    event = QDropEvent(QPointF(point), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(target, event)
    QApplication.processEvents()
    return event.isAccepted()


def add(editor, kind):
    body = editor.renderer.pages[editor.pageId].widget()
    body.layout().activate()
    row = max((c.layout.row + c.layout.rowSpan for c in editor.store.snapshot().pages[editor.pageId].components), default=0)
    point = body.layout().cellRect(row, 0).center()
    assert drop(body, {'kind': kind}, point), editor.message.text()
    return editor.tools.selected


def testCompleteDesignerUserPath(qtApp, tmp_path, monkeypatch):
    monkeypatch.setenv('EMO_PAGE_DESIGNER', '1')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    out = Path(os.environ['P4_SCREEN_DIR']) if os.environ.get('P4_SCREEN_DIR') else None
    records = {'platform': QApplication.platformName(), 'steps': [], 'results': [], 'incremental_reload_ms': []}
    def shot(name):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QApplication.processEvents()
        if out:
            assert window.grab().save(str(out/(name+'.png')))
        records['steps'].append(name)
    root = tmp_path/'old-project'
    root.mkdir()
    runtime = RuntimeService(dbPath=tmp_path/'runtime.sqlite3', workspaceRoot=tmp_path/'jobs')
    client = RuntimeClient(runtime)
    doc = sampleProject(root)
    raw = doc.model_dump()
    raw['schemaVersion'] = '2.1'
    raw.pop('presentation')
    raw.pop('resources')
    for node in raw['workflows']['main']['nodes']:
        manifest = runtime.pluginScanResult.activeOperators.get(node.get('operatorId'))
        if manifest:
            for key in ('inputPorts', 'outputPorts'):
                node[key] = {k: normalizePortType(v) for k, v in getattr(manifest.manifest, key).items()}
    raw['workflows']['main']['nodes'][1]['params']['imagePath'] = str(root/'input.png')
    saveProject(root, raw)
    window = MainWindow(client)
    window.resize(1500, 980)
    preview = window.pageCoordinator.preview
    try:
        assert window.loadProjectDirectory(str(root))
        window.show()
        waitFor(lambda: window.operatorCatalogController.state == 'ready')
        assert window.saveProjectToDirectory(str(root))
        assert json.loads((root/'project.json').read_text())['schemaVersion'] == '2.1'
        next(a for a in window.mainToolbar.actions() if a.text() == '页面设计').trigger()
        editor = window.pageCoordinator.editor
        monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('运行总览', True))
        click(editor, '新建页面')
        overview = editor.pageId
        image = add(editor, 'image')
        shot('01-component-drop')
        number = add(editor, 'number')
        text = add(editor, 'text')
        editor.tools.select(text)
        editor.tools.fields['text'].setText('本地图像 · 当前草稿 · 正式 Blob / Count 输出')
        click(editor, '应用属性 / 布局')
        monkeypatch.setattr(QInputDialog, 'getItem', lambda parent, title, label, items, *a, **k: (items[0], True))
        for key, node, port in [(image, 'blob', 'overlay'), (number, 'count', 'count')]:
            index = next(i for i, choice in enumerate(editor.tools.choices) if choice.source.nodeId == node and choice.source.port == port)
            widget = editor.renderer.widgets[overview][key][1]
            assert drop(widget, {'choice': index}, widget.rect().center()), editor.message.text()
        shot('02-output-drop')
        navigation = add(editor, 'navigation_button')
        click(editor, '复制页面')
        detail = editor.pageId
        monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('检测详情', True))
        click(editor, '重命名')
        # Navigate by stable ID through the property panel, not a JSON patch.
        detailNav = next(c for c in editor.store.snapshot().pages[detail].components if c.type == 'navigation_button').componentId
        editor.tools.select(detailNav)
        editor.tools.destination.setCurrentIndex(editor.tools.destination.findData(overview))
        editor.tools.fields['text'].setText('返回总览')
        click(editor, '应用属性 / 布局')
        editor.pageList.setCurrentRow(editor.store.snapshot().pageOrder.index(overview))
        editor.tools.select(navigation)
        editor.tools.destination.setCurrentIndex(editor.tools.destination.findData(detail))
        editor.tools.fields['text'].setText('检测详情')
        click(editor, '应用属性 / 布局')
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(root/'input.png'), ''))
        click(editor, '登记本地输入图片')
        assert window.saveProjectToDirectory(str(root))
        before = window.pageCoordinator.session.document().presentation.model_dump()
        copy = tmp_path/'saved-as'
        assert window.saveProjectToDirectory(str(copy))
        assert window.loadProjectDirectory(str(copy))
        window.pageCoordinator.showPages()
        editor = window.pageCoordinator.editor
        assert window.pageCoordinator.session.document().presentation.model_dump() == before
        window.renameWorkflow('main', '流程另存后改名')
        assert window.saveProjectToDirectory(str(copy))
        assert json.loads((copy/'project.json').read_text())['presentation'] == before
        shot('03-saved-reopened')
        click(editor, '切换为模拟预览')
        assert '模拟' in editor.renderer.banner.text()
        assert preview.session is None
        click(editor, '明确开始隔离草稿调试')
        waitFor(lambda: preview.hub is not None or preview.error is not None)
        assert preview.error is None, preview.error
        waitFor(lambda: bool(editor.renderer.displayed))
        scope = next(iter(editor.renderer.displayed.values()))
        assert scope.result.status == 'COMPLETE', scope.result
        count = next(s.valueJson for s in scope.result.sources if s.valueJson is not None)
        assert count == '2'
        assert len(scope.images) == 1
        records['results'].append({'key': scope.result.identity.resultKey, 'job': scope.result.identity.jobId, 'count': count,
                                  'image_sha': next(s.image.sha256 for s in scope.result.sources if s.image)})
        shot('04-real-overview')
        records['gui_submission_and_paint'] = list(editor.renderer.records)
        navWidget = editor.renderer.widgets[overview][navigation][1]
        QTest.mouseClick(navWidget, Qt.LeftButton)
        QApplication.processEvents()
        assert editor.renderer.currentPageId == detail
        assert next(iter(editor.renderer.displayed.values())).result.identity.resultKey == scope.result.identity.resultKey
        shot('05-real-detail')
        session = preview.session
        baselineUndo = len(window.pageCoordinator.session._undo)
        for _ in range(12):
            start = time.perf_counter_ns()
            editor.refresh()
            QApplication.processEvents()
            records['incremental_reload_ms'].append((time.perf_counter_ns()-start)/1e6)
        assert preview.session is session
        assert len(preview.backend.service.jobs) == 1
        assert len(window.pageCoordinator.session._undo) == baselineUndo
        records['hub'] = preview.hub.stats()
        records['session_stats'] = session.stats
        # Explicit stop, then register a different actual input and start a new snapshot.
        click(editor, '停止自有调试 / 断开观察')
        waitFor(lambda: not preview.active())
        import cv2
        pixels = cv2.imread(str(root/'input.png'))
        cv2.rectangle(pixels, (120, 60), (145, 85), (255, 255, 255), -1)
        changed = tmp_path/'changed.png'
        assert cv2.imwrite(str(changed), pixels)
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(changed), ''))
        click(editor, '登记本地输入图片')
        click(editor, '明确开始隔离草稿调试')
        waitFor(lambda: preview.hub is not None or preview.error is not None)
        assert preview.error is None, preview.error
        waitFor(lambda: bool(editor.renderer.displayed))
        second = next(iter(editor.renderer.displayed.values()))
        secondCount = next(s.valueJson for s in second.result.sources if s.valueJson is not None)
        assert secondCount == '3'
        assert second.result.identity.jobId != scope.result.identity.jobId
        records['results'].append({'key': second.result.identity.resultKey, 'job': second.result.identity.jobId, 'count': secondCount})
        shot('06-new-explicit-debug')
        assert window.saveProjectToDirectory(str(copy))
        if out:
            (out/'two-pages.json').write_text(editor.store.snapshot().model_dump_json(indent=2), encoding='utf-8')
        click(editor, '停止自有调试 / 断开观察')
        waitFor(lambda: not preview.active())
        assert not runtime._closed
        assert window.loadProjectDirectory(str(copy))
        window.pageCoordinator.showPages()
        shot('07-final-reopen')
        records['saved_project_sha256'] = hashlib.sha256((copy/'project.json').read_bytes()).hexdigest()
        records['saved_reopen'] = True
        assert not window.pageCoordinator.session.dirty
        window.close()
        assert not window.isVisible()
        records['shutdown_confirmed'] = True
        if out:
            (out/'path.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    finally:
        if preview.active():
            preview.closeAsync()
            waitFor(lambda: not preview.active())
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
        window.close()
        runtime.close()
