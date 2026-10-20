"""Native Qt page-editor visual evidence. No Runtime, devices or inference.

Use QT_QPA_PLATFORM=windows and QT_SCALE_FACTOR in fresh processes. Requested
window dimensions are logical pixels; screen limits and actual DPR are recorded.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
os.environ['HUARAY_CAMERA_SMOKE'] = '0'
import emo_master  # noqa: E402,F401 - native DLL order
from PySide2 import __version__ as qtVersion  # noqa: E402
from PySide2.QtCore import Qt, QCoreApplication, QEvent, QPointF, QPoint  # noqa: E402
from PySide2.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent  # noqa: E402
from PySide2.QtWidgets import QApplication, QMessageBox, QInputDialog  # noqa: E402
from PySide2.QtTest import QTest  # noqa: E402
from emo_master.apps.designer.main import configureHighDpi, applyDesignerStyle  # noqa: E402
from emo_master.apps.designer.ui.main_window import MainWindow  # noqa: E402
from emo_master.apps.designer.page_designer.tools import mime  # noqa: E402
from emo_master.core.contracts.port_types import normalizePortType  # noqa: E402


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def settle(app):
    for _ in range(12):
        app.processEvents()


def nativeAdd(editor, kind, row, column):
    body = editor.renderer.pages[editor.pageId].widget()
    body.layout().activate()
    point = body.layout().cellRect(row, column).center()
    data = mime({'kind': kind})
    QApplication.sendEvent(body, QDragEnterEvent(point, Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier))
    event = QDropEvent(QPointF(point), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(body, event)
    assert event.isAccepted(), editor.message.text()
    settle(QApplication.instance())
    return editor.tools.selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--width', type=int, default=1600)
    parser.add_argument('--height', type=int, default=900)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    files = [p for p in git('ls-files', '-co', '--exclude-standard').splitlines()
             if p.endswith('.py') and ('page_designer' in p or '/presentation/' in p or p == 'scripts/validate_page_editor.py')]
    evidence = dict(head=git('rev-parse', 'HEAD'), dirty=git('status', '--porcelain'), python=sys.version,
                    os=platform.platform(), pyside2=qtVersion, platform=os.getenv('QT_QPA_PLATFORM'),
                    scale=os.getenv('QT_SCALE_FACTOR', 'system'), requested=[args.width, args.height],
                    sourceSha256={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files})
    configureHighDpi()
    app = QApplication([])
    applyDesignerStyle(app)
    with TemporaryDirectory(prefix='emo-page-visual-') as data:
        os.environ['EMO_RUNTIME_DATA_DIR'] = data
        client = SimpleNamespace(listOperators=lambda: [], loadProject=lambda path: SimpleNamespace(ok=True, message='ok'))
        prefs = {}
        settings = SimpleNamespace(value=lambda key, default=None: prefs.get(key, default), setValue=lambda k, v: prefs.update({k: v}))
        window = MainWindow(client, settingsStore=settings)
        QMessageBox.question = lambda *a, **k: QMessageBox.Yes
        try:
            window.resize(args.width, args.height)
            window.show()
            settle(app)
            manifest = json.loads((ROOT/'src/emo_master/plugins/builtins/collection_count/manifest.json').read_text(encoding='utf-8'))
            window.operatorCatalog = [manifest]
            node = window.flowModel.addNode(manifest['operatorId'], '目标数量',
                {p: normalizePortType(t) for p, t in manifest['inputPorts'].items()},
                {p: normalizePortType(t) for p, t in manifest['outputPorts'].items()}, manifest['paramSchema'])
            window.workflowController.refreshActiveWorkflow()
            c = window.pageCoordinator
            c.showPages()
            e = c.editor
            e.pageId = e.store.createPage('运行总览')
            e.refresh()
            e.tools.select(None)
            e.tools.fields['columns'].setValue(2)
            e.run(e.tools.apply)
            settle(app)
            image = nativeAdd(e, 'image', 0, 0)
            number = nativeAdd(e, 'number', 0, 1)
            e.tools.fields['title'].setText('检测数量')
            e.tools.fields['unit'].setText(' 件')
            e.tools.fields['decimals'].setValue(0)
            e.run(e.tools.apply)
            overview = e.pageId
            e.tools.commands().add(overview, 'text', 1, 0)
            nav = e.tools.commands().add(overview, 'navigation_button', 1, 1)
            second = e.store.createPage('检测详情')
            e.tools.commands().add(second, 'table', 0, 0)
            e.tools.commands().add(second, 'indicator', 1, 0)
            e.refresh()
            e.tools.select(nav)
            e.tools.fields['text'].setText('查看检测详情 →')
            e.tools.actionMode.setCurrentIndex(e.tools.actionMode.findData('navigate'))
            e.tools.destination.setCurrentIndex(e.tools.destination.findData(second))
            e.run(e.tools.apply)
            e.tools.select(number)
            settle(app)
            assert window.grab().save(str(args.output/'overview.png'))
            # Native output drag: paint compatibility before committing the
            # same MIME drop. The source is a real static operator port.
            choice = next(i for i, value in enumerate(e.tools.choices) if value.source.nodeId == node and value.source.port == 'count')
            target = e.renderer.widgets[overview][number][1]
            point = target.rect().center()
            data = mime({'choice': choice})
            history = len(c.session._undo)
            e.libraryTabs.setCurrentIndex(1)
            QApplication.sendEvent(target, QDragEnterEvent(point, Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier))
            settle(app)
            assert len(c.session._undo) == history
            assert window.grab().save(str(args.output/'binding-drag.png'))
            QInputDialog.getItem = lambda *a, **k: ('绑定', True)
            event = QDropEvent(QPointF(point), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier)
            QApplication.sendEvent(target, event)
            assert event.isAccepted()
            settle(app)
            assert len(c.session._undo) == history + 1
            # Resize an unobstructed text card with native mouse events.
            text = next(component.componentId for component in e.store.snapshot().pages[overview].components if component.type == 'text')
            e.tools.select(text)
            handle = e.tools.selection.handles[1]
            history = len(c.session._undo)
            QTest.mousePress(handle, Qt.LeftButton, pos=handle.rect().center())
            local = handle.rect().center() + QPoint(0, 64)
            QApplication.sendEvent(handle, QMouseEvent(QEvent.MouseMove, local, handle.mapToGlobal(local), Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
            settle(app)
            assert e.tools.selection.valid, e.message.text()
            assert len(c.session._undo) == history
            assert window.grab().save(str(args.output/'resize-preview.png'))
            QTest.mouseRelease(handle, Qt.LeftButton, pos=local)
            settle(app)
            assert len(c.session._undo) == history + 1
            changed = c.session.payload()
            c.history()
            c.history(True)
            assert c.session.payload() == changed
            e.tools.select(number)
            e.libraryTabs.setCurrentIndex(0)
            settle(app)
            assert window.grab().save(str(args.output/'bound-overview.png'))
            evidence['actual'] = [window.width(), window.height()]
            evidence['dpr'] = window.devicePixelRatioF()
            evidence['screen'] = [app.primaryScreen().availableGeometry().width(), app.primaryScreen().availableGeometry().height()]
            evidence['splitter'] = e.splitter.sizes()
            evidence['resources'] = e.renderer.editorResourceUsage()
            evidence['horizontalScrollMaximum'] = e.propertyScroll.horizontalScrollBar().maximum()
            evidence['workspaceFitsWindow'] = e.width() <= window.width()
            evidence['propertyPanelWidth'] = e.propertyScroll.widget().width()
            evidence['propertyViewportWidth'] = e.propertyScroll.viewport().width()
            evidence['propertyInputVisible'] = e.tools.fields['unit'].width() > 80
            evidence['propertyInputFits'] = all(widget.width() <= e.propertyScroll.viewport().width()
                for widget in e.tools.fields.values() if widget.isVisibleTo(e.propertyScroll.widget()))
            evidence['nativeOutputBind'] = True
            evidence['nativeResizeSingleUndoRedo'] = True
            before = e.store.snapshot()
            assert window.saveProjectToDirectory(str(args.output/'project'))
            assert window.loadProjectDirectory(str(args.output/'project'))
            assert c.session.document().presentation == before
            evidence['saveReopen'] = True
            c.showPages()
            e = c.editor
            preview = c.preview.openObserver()
            preview.widgets[overview][nav][1].click()
            settle(app)
            assert preview.currentPageId == second
            assert preview.grab().save(str(args.output/'detail.png'))
            evidence['navigation'] = True
            evidence['imageComponent'] = image
            evidence['noJob'] = window.currentJobId is None and c.preview.backend is None
        finally:
            QMessageBox.question = lambda *a, **k: QMessageBox.Discard
            evidence['closeAccepted'] = window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(window, QEvent.DeferredDelete)
            (args.output/'validation.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == '__main__':
    main()
