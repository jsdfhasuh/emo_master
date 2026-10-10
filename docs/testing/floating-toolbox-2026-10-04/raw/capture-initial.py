"""Native Designer toolbox captures; manifest metadata only, no Runtime/Job."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(1, str(ROOT))
import emo_master  # noqa: E402,F401 - Windows DLL order
from PySide2.QtCore import QPoint, QEvent, QPointF, QCoreApplication, Qt  # noqa: E402
from PySide2.QtGui import QMouseEvent  # noqa: E402
from PySide2.QtTest import QTest  # noqa: E402
from PySide2.QtWidgets import QApplication, QMessageBox  # noqa: E402
from emo_master.apps.designer.main import configureHighDpi, applyDesignerStyle  # noqa: E402
from emo_master.apps.designer.ui.main_window import MainWindow  # noqa: E402
from emo_master.core.contracts.port_types import normalizePortType  # noqa: E402
from scripts.validate_flow_layout import populate  # noqa: E402


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, encoding='utf-8').strip()


def rect(value):
    return [value.x(), value.y(), value.width(), value.height()]


def catalog():
    result = []
    for folder in ('image_loader', 'yolo_inference', 'collection_count', 'number_compare', 'resize', 'image_saver'):
        raw = json.loads((ROOT / 'src/emo_master/plugins/builtins' / folder / 'manifest.json').read_text(encoding='utf-8'))
        raw['inputPortSpecs'], raw['outputPortSpecs'] = raw['inputPorts'], raw['outputPorts']
        raw['inputPorts'] = {key: normalizePortType(value) for key, value in raw['inputPorts'].items()}
        raw['outputPorts'] = {key: normalizePortType(value) for key, value in raw['outputPorts'].items()}
        result.append(SimpleNamespace(**raw))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--width', type=int, default=1600)
    parser.add_argument('--height', type=int, default=900)
    parser.add_argument('--physical-screen', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    configureHighDpi()
    app = QApplication([])
    applyDesignerStyle(app)
    prefs = {}
    settings = SimpleNamespace(value=lambda key, default=None: prefs.get(key, default),
                               setValue=lambda key, value: prefs.update({key: value}))
    client = SimpleNamespace(listOperators=catalog)
    window = MainWindow(client, settingsStore=settings)
    QMessageBox.question = lambda *_args: QMessageBox.Discard
    measurements = {'head': git('rev-parse', 'HEAD'), 'dirty': git('status', '--porcelain=v1').splitlines(),
                    'requested': [args.width, args.height], 'platform': os.getenv('QT_QPA_PLATFORM'),
                    'scale': os.getenv('QT_SCALE_FACTOR'), 'physicalScreen': args.physical_screen,
                    'load': 'six real built-in manifests/ports; no operator execution, Runtime or Job'}

    def settle():
        for _ in range(20):
            app.processEvents()

    def capture(name):
        settle()
        assert window.grab().save(str(args.output / (name + '.png')))
        viewport = window.flowView.viewport()
        toolbox = window.floatingToolbox
        assert viewport.geometry().contains(toolbox.geometry())
        assert toolbox.rect().contains(toolbox.toggleButton.geometry())
        measurements[name] = {'toolbox': rect(toolbox.geometry()), 'viewport': rect(viewport.geometry()),
                              'tabs': rect(window.workflowTabs.geometry()), 'zoom': window.flowView.getZoomFactor(),
                              'expanded': toolbox.isExpanded()}

    try:
        window.resize(args.width, args.height)
        window.show()
        deadline = time.monotonic() + 5
        while not window.operatorCatalogController.hasCatalog and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.003)
        assert window.operatorCatalogController.hasCatalog
        settle()
        target = [args.width, args.height]
        if args.physical_screen:
            screen = app.primaryScreen()
            ratio = window.devicePixelRatioF()
            target = [round(args.width / ratio) - (window.frameGeometry().width() - window.width()),
                      round(args.height / ratio) - (screen.geometry().height() - screen.availableGeometry().height())
                      - (window.frameGeometry().height() - window.height())]
            window.resize(*target)
            settle()
        measurements.update(actual=[window.width(), window.height()], targetClient=target,
                            dpr=window.devicePixelRatioF(), availableScreen=rect(app.primaryScreen().availableGeometry()))
        populate(window)
        window.autoLayoutNodes()
        window.focusGraphContent()
        settle()
        capture('collapsed')
        canvas, tabs = window.flowView.viewport().geometry(), window.workflowTabs.geometry()
        zoom = window.flowView.getZoomFactor()
        QTest.mouseClick(window.sidebarToggleButton, Qt.LeftButton)
        capture('expanded')
        assert window.flowView.viewport().geometry() == canvas
        assert window.workflowTabs.geometry() == tabs
        assert window.flowView.getZoomFactor() == zoom
        assert window.floatingToolbox.grab().save(str(args.output / 'toolbox.png'))
        toolbox = window.floatingToolbox
        for button in (*window.categoryButtons.values(), *window.operatorBubble._buttons):
            toolbox.libraryScroll.ensureWidgetVisible(button)
            if button in window.operatorBubble._buttons:
                window.operatorBubble._scroll.ensureWidgetVisible(button)
            settle()
            assert not button.visibleRegion().isEmpty(), button.text()
        window.operatorBubble.searchInput.setText('resize')
        capture('search')
        assert len(window.operatorBubble.getVisibleOperatorIds()) == 1
        window.operatorBubble.searchInput.clear()
        for index, name in ((1, 'dependencies'), (2, 'nodes')):
            toolbox.tabs.setCurrentIndex(index)
            capture(name)
        toolbox.tabs.setCurrentIndex(0)
        handle = toolbox.dragHandle
        origin = handle.mapToGlobal(QPoint(10, 14))
        destination = origin + QPoint(2000, 2000)
        for kind, globalPoint, buttons in ((QEvent.MouseButtonPress, origin, Qt.LeftButton),
                                           (QEvent.MouseMove, destination, Qt.LeftButton),
                                           (QEvent.MouseButtonRelease, destination, Qt.NoButton)):
            app.sendEvent(handle, QMouseEvent(kind, QPointF(handle.mapFromGlobal(globalPoint)), QPointF(globalPoint),
                                             Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton,
                                             buttons, Qt.NoModifier))
        capture('moved')
        window.flowView.centerOn(1800, 1800)
        settle()
        assert toolbox.geometry() == measurementsRect(measurements['moved']['toolbox'])
        window.operatorBubble.searchInput.setFocus()
        QTest.keyClick(window.operatorBubble.searchInput, Qt.Key_Escape)
        assert not toolbox.isExpanded()
        measurements['checks'] = {'stationaryCanvas': True, 'dragClamped': True, 'allControlsReachable': True,
                                  'escapeCollapse': True, 'jobCreated': window.currentJobId is not None}
        assert window.currentJobId is None
        sourceFiles = set(git('ls-files', '--cached', '--others', '--exclude-standard', 'src', 'tests', 'scripts').splitlines())
        measurements['sources'] = {name: hashlib.sha256((ROOT / name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                                  for name in sorted(sourceFiles) if (ROOT / name).is_file()}
        (args.output / 'geometry.json').write_text(json.dumps(measurements, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({key: measurements[key] for key in ('actual', 'dpr', 'checks')}, ensure_ascii=False))
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def measurementsRect(value):
    from PySide2.QtCore import QRect
    return QRect(*value)


if __name__ == '__main__':
    main()
