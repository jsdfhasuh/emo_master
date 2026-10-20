"""Capture the actual Designer with isolated spawn Jobs and typed loopback gRPC.

This is a low-rate functional fixture, not the 1080p/5 Hz acceptance load.
Every invocation uses a fresh project, database and output directory outside
the repository. Evidence is immutable; only screenshots and measurements are
written to the requested output directory.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(1, str(ROOT))
import emo_master  # noqa: E402,F401 - Windows DLL initialization precedes Qt/OpenCV.
import cv2  # noqa: E402
import grpc  # noqa: E402
import numpy as np  # noqa: E402
from PySide2.QtCore import QCoreApplication, QEvent, QPointF, Qt  # noqa: E402
from PySide2.QtTest import QTest  # noqa: E402
from PySide2.QtWidgets import QApplication, QMessageBox, QScrollArea, QToolButton  # noqa: E402
from emo_master.apps.designer.main import configureHighDpi, applyDesignerStyle  # noqa: E402
from emo_master.apps.designer.services.runtime_client import RuntimeClient  # noqa: E402
from emo_master.apps.designer.state.project_store import saveProject  # noqa: E402
from emo_master.apps.designer.ui.main_window import MainWindow  # noqa: E402
from emo_master.apps.runtime.grpc_server.aio_entry import AioRuntimeServer  # noqa: E402
from emo_master.apps.runtime.grpc_server.generated import runtime_pb2_grpc as rpc  # noqa: E402
from emo_master.apps.runtime.grpc_server.service import RuntimeService  # noqa: E402
from emo_master.apps.runtime.presentation.service import PresentationService  # noqa: E402
from examples.flow_run_inspection import sampleProject  # noqa: E402


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, encoding='utf-8').strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--width', type=int, default=1600, help='Requested logical client width')
    parser.add_argument('--height', type=int, default=900, help='Requested logical client height')
    parser.add_argument('--detailed', action='store_true', help='Capture all four tabs and failures')
    parser.add_argument('--main-fullscreen', action='store_true', help='Use the actual desktop fullscreen client')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    configureHighDpi()
    app = QApplication([])
    applyDesignerStyle(app)
    temporary = tempfile.TemporaryDirectory(prefix='emo-node-results-')
    work = Path(temporary.name)
    project = work / '临时 工程 A'
    saveProject(project, sampleProject(project).model_dump())
    runtime = RuntimeService(dbPath=work / 'temporary.sqlite3', workspaceRoot=work / 'jobs',
                             logDirectory=work / 'logs')
    presentation = PresentationService(runtime, work / 'display')
    server = AioRuntimeServer(runtime, presentation)
    target = f'127.0.0.1:{server.port}'
    connection = grpc.insecure_channel(target)
    client = RuntimeClient(rpc.RuntimeServiceStub(connection), ownedChannel=connection, runtimeTarget=target)
    prefs = {}
    settings = SimpleNamespace(value=lambda key, default=None: prefs.get(key, default),
        setValue=lambda key, value: prefs.update({key: value}))
    constructStart = time.monotonic_ns()
    window = MainWindow(client, settingsStore=settings)
    constructMs = (time.monotonic_ns() - constructStart) / 1e6
    QMessageBox.question = lambda *_args, **_kwargs: QMessageBox.Discard
    coordinator = window.nodeResultCoordinator
    panel = coordinator.panel
    result = {'head': git('rev-parse', 'HEAD'), 'dirty': git('status', '--porcelain=v1').splitlines(),
        'python': sys.version, 'platform': app.platformName(), 'qtScale': os.getenv('QT_SCALE_FACTOR'),
        'requestedLogicalClient': [args.width, args.height], 'runtimeTarget': target,
        'mainFullscreen': args.main_fullscreen,
        'load': '240x360 fixed local fixture; A=2 objects, B=3; three explicit spawn Jobs including missing-input failure',
        'clock': 'time.monotonic_ns; model delivery and QWidget paint completion measured separately',
        'screenshots': [], 'resources': [], 'jobs': [],
        'windowConstructionMs': constructMs,
        'captureSourceSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'fixtureSourceSha256': hashlib.sha256((ROOT / 'examples/flow_run_inspection.py').read_bytes()).hexdigest()}

    def wait(predicate, seconds=30):
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError('Condition not reached before outer GUI watchdog')
            app.processEvents()
            time.sleep(.005)
        app.processEvents()

    def resources(stage):
        result['resources'].append({'stage': stage, 'server': runtime.runInspectionStore.stats(),
            'summariesBytes': coordinator.history.retainedBytes, 'qtImageBytes': coordinator.qtBytes,
            'peakQtImageBytes': coordinator.peakQtBytes, 'imageReads': coordinator.images.reads,
            'workerMailboxBytes': coordinator.images.decodedBytes,
            'peakDecodedBytes': coordinator.images.peakDecodedBytes,
            'peakScratchBytes': coordinator.images.peakScratchBytes})

    def reveal(widget):
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget, 8, 8)
            parent = parent.parentWidget()
        app.processEvents()

    def click(button):
        reveal(button)
        window.raise_()
        app.processEvents()
        assert not button.visibleRegion().isEmpty(), 'Action must be reachable in the actual viewport'
        QTest.mouseClick(button, Qt.LeftButton)

    def chooseTab(page):
        reveal(panel.tabs)
        bar = panel.tabs.tabBar()
        index = panel.tabs.indexOf(page)
        buttons = bar.findChildren(QToolButton)
        for _ in range(8):
            point = bar.tabRect(index).center()
            arrows = [button for button in buttons if button.isVisible()]
            boundary = min((button.geometry().left() for button in arrows), default=bar.width())
            if 0 <= point.x() < boundary:
                break
            arrow = Qt.LeftArrow if point.x() < 0 else Qt.RightArrow
            click(next(button for button in arrows if button.arrowType() == arrow))
            app.processEvents()
        assert 0 <= point.x() < boundary, 'Result tab must be reachable by mouse'
        QTest.mouseClick(bar, Qt.LeftButton, pos=point)
        assert panel.tabs.currentWidget() is page

    def capture(name, widget=None):
        widget = widget or window
        for _ in range(8):
            app.processEvents()
        path = output / (name + '.png')
        assert widget.grab().save(str(path))
        assert panel.tabs.geometry().bottom() < panel.configure.geometry().top(), 'Tabs must not overlap bottom actions'
        assert panel.origin.height() >= panel.origin.heightForWidth(panel.origin.width()), 'Wrapped identity must fit'
        screen = widget.screen()
        record = {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'clientLogical': [widget.width(), widget.height()], 'dpr': widget.devicePixelRatioF(),
            'availableScreen': screen.availableGeometry().getRect(),
            'nativeScreen': screen.geometry().getRect(), 'screenLogicalDpi': screen.logicalDotsPerInch(),
            'selection': coordinator.imageSelection, 'tab': panel.tabs.tabText(panel.tabs.currentIndex()),
            'modelUpdatedNs': coordinator.modelUpdatedNs, 'panelPaintNs': panel.image.lastPaintNs,
            'panelFirstImagePaintNs': panel.image.firstImagePaintNs,
            'viewerPaintNs': coordinator.viewer.view.lastPaintNs if coordinator.viewer else None,
            'viewerImageSetNs': coordinator.viewer.imageSetNs if coordinator.viewer else None,
            'viewerFirstImagePaintNs': coordinator.viewer.view.firstImagePaintNs if coordinator.viewer else None,
            'panelRect': panel.geometry().getRect(), 'imageRect': panel.image.geometry().getRect(),
            'panelScrollRect': coordinator.panelScroll.geometry().getRect(),
            'activeTabRect': panel.tabs.currentWidget().geometry().getRect(),
            'panelMinimum': (panel.minimumSizeHint().width(), panel.minimumSizeHint().height()),
            'panelHeightForWidth': panel.heightForWidth(panel.width()),
            'panelMinimumHeightForWidth': panel.layout().minimumHeightForWidth(panel.width()),
            'tabsRect': panel.tabs.geometry().getRect(),
            'tabsMinimum': (panel.tabs.minimumWidth(), panel.tabs.minimumHeight()),
            'tabBarRect': panel.tabs.tabBar().geometry().getRect(),
            'configureRectInPanel': (*panel.configure.mapTo(panel, panel.configure.rect().topLeft()).toTuple(),
                                     panel.configure.width(), panel.configure.height()),
            'tabButtons': [{'name': button.objectName(), 'visible': button.isVisible(),
                            'rect': button.geometry().getRect(), 'arrow': int(button.arrowType())}
                           for button in panel.tabs.tabBar().findChildren(QToolButton)],
            'tabBarMinimum': panel.tabs.tabBar().minimumSizeHint().width(),
            'panelWidth': panel.width()}
        if coordinator.imagePacket:
            record['actualImage'] = {**asdict(coordinator.imagePacket['source']),
                'format': coordinator.imagePacket['format'], 'decodedBytes': coordinator.image.sizeInBytes(),
                'bytesPerLine': coordinator.image.bytesPerLine(), 'panelCacheKey': panel.image.pixmap().cacheKey(),
                'viewerCacheKey': coordinator.viewer.view.item.pixmap().cacheKey() if coordinator.viewer else None}
        result['screenshots'].append(record)

    def select(node):
        window.focusGraphContent()
        reveal(window.flowView)
        center = window.flowScene.getNodeCenter(node)
        point = window.flowView.mapFromScene(QPointF(*center))
        if not window.flowView.viewport().rect().contains(point):
            window.flowView.centerOn(QPointF(*center))
            app.processEvents()
            point = window.flowView.mapFromScene(QPointF(*center))
        QTest.mouseClick(window.flowView.viewport(), Qt.LeftButton, pos=point)
        wait(lambda: window.flowModel.selectedNodeId == node, 5)

    def imageFor(job, port='image'):
        wait(lambda: coordinator.imagePacket is not None
             and coordinator.imagePacket['source'].originJobId == job
             and coordinator.imagePacket['source'].port == port)
        assert coordinator.viewer is None or coordinator.viewer.identity == coordinator.imageSelection

    def run(previous=None):
        window.startJob()
        wait(lambda: window.currentJobId is not None and window.currentJobId != previous)
        job = window.currentJobId
        wait(lambda: not window.runtimeController._jobActive and window.runtimeController._worker is None)
        result['jobs'].append(asdict(runtime.jobRepository.get(job)))
        return job

    try:
        window.resize(args.width, args.height)
        window.showFullScreen() if args.main_fullscreen else window.show()
        wait(lambda: window.operatorCatalogController.state == 'ready')
        loadStart = time.monotonic_ns()
        assert window.loadProjectDirectory(str(project))
        wait(lambda: 'load' in window.flowModel.nodes)
        result['projectLoadMs'] = (time.monotonic_ns() - loadStart) / 1e6
        layoutStart = time.monotonic_ns()
        window.autoLayoutNodes()
        result['explicitFlowLayoutMs'] = (time.monotonic_ns() - layoutStart) / 1e6
        select('load')
        assert len(runtime.jobRepository.all()) == 0
        if args.detailed:
            capture('01-before-run')
        first = run()
        assert runtime.jobRepository.get(first).status == 'COMPLETED'
        imageFor(first)
        assert coordinator.image.pixelColor(210, 20).red() == 0
        resources('A complete')
        pixels = cv2.imdecode(np.fromfile(project / 'input.png', np.uint8), cv2.IMREAD_COLOR)
        pixels[10:30, 200:220] = 255
        ok, encoded = cv2.imencode('.png', pixels)
        assert ok
        encoded.tofile(project / 'input.png')
        second = run(first)
        assert runtime.jobRepository.get(second).status == 'COMPLETED'
        imageFor(second)
        assert coordinator.image.pixelColor(210, 20).red() == 255
        capture('02-current-B-image')
        reveal(panel.image)
        capture('02b-current-B-image-scrolled')
        resources('A+B retained')
        click(panel.enlarge)
        viewer = coordinator.viewer
        viewer.resize(min(1000, args.width), min(740, args.height))
        wait(lambda: viewer.view.lastPaintNs >= coordinator.modelUpdatedNs, 5)
        before = coordinator.images.reads
        click(panel.enlarge)
        assert coordinator.viewer is viewer and coordinator.images.reads == before
        assert viewer.view.item.pixmap().cacheKey() == panel.image.pixmap().cacheKey()
        viewer.actual.click()
        assert viewer.view.zoom == 1
        viewer.zoomIn.click()
        viewer.zoomOut.click()
        viewer.fitButton.click()
        if args.detailed:
            viewer.fullScreen.click()
            assert viewer.isFullScreen()
            QTest.keyClick(viewer, Qt.Key_Escape)
            assert not viewer.isFullScreen()
        click(panel.previous)
        assert coordinator.pixmap.isNull() and viewer.view.item.pixmap().isNull()
        imageFor(first)
        assert coordinator.image.pixelColor(210, 20).red() == 0
        assert viewer.identity[0] == first
        capture('03-previous-A-image')
        reveal(panel.image)
        capture('03b-previous-A-image-scrolled')
        capture('04-previous-A-viewer', viewer)
        resources('previous A selected with shared large window')
        select('count')
        assert 'count = 2' in panel.values.toPlainText()
        assert not panel.tabs.isTabVisible(0) and viewer.view.item.pixmap().isNull()
        assert window.currentJobId == second
        result['previousCount'] = panel.values.toPlainText()
        if args.detailed:
            capture('05-previous-A-count')
        click(panel.live)
        assert 'count = 3' in panel.values.toPlainText()
        result['currentCount'] = panel.values.toPlainText()
        if args.detailed:
            capture('06-current-B-count')
            chooseTab(panel.execution)
            capture('07-current-B-execution')
            select('presence')
            chooseTab(panel.inputs)
            assert 'left = 3' in panel.inputs.toPlainText()
            capture('08-current-B-inputs')
        select('blob')
        panel.ports.setCurrentIndex(panel.ports.findData('overlay'))
        imageFor(second, 'overlay')
        assert viewer.view.item.pixmap().cacheKey() == panel.image.pixmap().cacheKey()
        if args.detailed:
            capture('09-current-B-overlay')
        select('save')
        chooseTab(panel.imagePage)
        panel.ports.setCurrentIndex(panel.ports.findData('__saved_result__'))
        imageFor(second, '__saved_result__')
        assert coordinator.imagePacket['source'].nodeId == 'save'
        result['savedResultMeta'] = panel.imageMeta.text()
        if args.detailed:
            capture('10-saved-result')
        reads = coordinator.images.reads
        viewer.close()
        assert coordinator.viewer is None and coordinator.imagePacket is not None
        reveal(panel.image)
        QTest.mouseDClick(panel.image, Qt.LeftButton)
        assert coordinator.viewer is not None and coordinator.images.reads == reads
        select('load')
        click(panel.previous)
        imageFor(first)
        # A is now selected. Only acceptance of the failed third Job evicts A.
        window.updateNodeParams('load', {'imagePath': str(project / 'missing.png')})
        third = run(second)
        assert runtime.jobRepository.get(third).status == 'FAILED'
        assert first not in coordinator.history.runs
        assert coordinator.history.selected is coordinator.history.current
        assert '返回本次运行' in coordinator.history.notice
        assert coordinator.pixmap.isNull() and coordinator.viewer.view.item.pixmap().isNull()
        assert 'E_INPUT_MISSING' in panel.execution.toPlainText()
        result['failedText'] = panel.execution.toPlainText()
        if args.detailed:
            capture('11-third-run-missing-input')
        resources('third acceptance evicts A')
        click(panel.previous)
        imageFor(second)
        assert coordinator.image.pixelColor(210, 20).red() == 255
        assert coordinator.viewer.identity[0] == second
        assert not runtime._closed and len(runtime.jobRepository.all()) == 3
        other = work / '切换 工程 B'
        saveProject(other, sampleProject(other).model_dump())
        assert window.loadProjectDirectory(str(other))
        wait(lambda: runtime.runInspectionStore.stats()['sessions'] == 0, 5)
        assert not coordinator.history.runs and coordinator.pixmap.isNull()
        assert coordinator.viewer.view.item.pixmap().isNull()
        assert runtime.runInspectionStore.stats()['encodedBytes'] == 0
        assert len(runtime.jobRepository.all()) == 3 and not runtime._closed
        resources('project switched; external Runtime remains alive')
        result['status'] = 'PASS'
    except BaseException as error:
        result['status'] = 'FAIL'
        result['failure'] = repr(error)
        result['diagnostic'] = {'imageText': panel.image.text(), 'job': window.currentJobId,
            'selection': coordinator.imageSelection,
            'events': [(entry.eventType, entry.message) for entry in window.logEntries[-15:]]}
        raise
    finally:
        window.close()
        window.shutdownOperatorDisplay()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        result['windowClosed'] = not window.isVisible()
        result['inspectionAfterWindowClose'] = runtime.runInspectionStore.stats()
        result['runtimeAliveAfterWindowClose'] = not runtime._closed
        resources('Designer closed')
        server.close()
        runtime.close()
        result['runtimeClosedByTestOwner'] = runtime._closed
        (output / 'measurements.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        temporary.cleanup()
    print(json.dumps({key: result[key] for key in ('status', 'previousCount', 'currentCount',
        'windowClosed', 'inspectionAfterWindowClose', 'runtimeAliveAfterWindowClose')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
