"""Native Qt interaction and one shared read over two real Runner Jobs."""
import time

import emo_master  # noqa: F401 - initialize Windows DLL order before Qt/OpenCV.
import cv2
import numpy as np
from PySide2.QtCore import Qt, QPoint, QEvent
from PySide2.QtGui import QPixmap, QWheelEvent
from PySide2.QtTest import QTest

from emo_master.apps.designer.ui.node_image_viewer import NodeImageViewer
from tests.runtime.test_run_inspection_rpc import running


def wait(app, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, 'Qt condition was not reached before the outer watchdog'
        app.processEvents()
        time.sleep(.005)
    app.processEvents()


def testNativeZoomPanFitFullScreenAndOwnedSharedPixmap(designerApplication):
    viewer = NodeImageViewer()
    try:
        viewer.resize(800, 600)
        pixmap = QPixmap(1920, 1080)
        pixmap.fill(Qt.red)
        identity = ('job-a', 'main', 'node', 'invocation', 'image')
        viewer.showResult(pixmap, '实际 1920 × 1080', identity)
        viewer.show()
        designerApplication.processEvents()
        assert viewer.view.item.pixmap().cacheKey() == pixmap.cacheKey()
        viewer.actual.click()
        assert viewer.view.zoom == 1
        event = QWheelEvent(QPoint(250, 180), QPoint(250, 180), QPoint(), QPoint(0, 120),
                            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        designerApplication.sendEvent(viewer.view.viewport(), event)
        assert viewer.view.zoom > 1
        viewer.view.setZoom(99)
        assert viewer.view.zoom == 8
        before = viewer.view.horizontalScrollBar().value()
        QTest.mousePress(viewer.view.viewport(), Qt.LeftButton, pos=QPoint(200, 200))
        move = QEvent(QEvent.MouseMove)
        from PySide2.QtGui import QMouseEvent
        move = QMouseEvent(QEvent.MouseMove, QPoint(100, 200), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        designerApplication.sendEvent(viewer.view.viewport(), move)
        QTest.mouseRelease(viewer.view.viewport(), Qt.LeftButton, pos=QPoint(100, 200))
        assert viewer.view.horizontalScrollBar().value() != before
        viewer.view.setZoom(.001)
        assert viewer.view.zoom == .1
        viewer.fitButton.click()
        assert .1 <= viewer.view.zoom <= 8 and viewer.view.fitting
        viewer.fullScreen.click()
        assert viewer.isFullScreen()
        QTest.keyClick(viewer, Qt.Key_Escape)
        assert not viewer.isFullScreen() and viewer.isVisible()
        assert viewer.view.item.pixmap().cacheKey() == pixmap.cacheKey()
        viewer.showResult(None, 'E_INSPECTION_MISSING: 所选上一任务无图', ('job-b',))
        assert viewer.view.item.pixmap().isNull() and not viewer.zoomIn.isEnabled()
    finally:
        viewer.close()


def testTwoRealRunsFollowSelectionWithoutExtraReadsAndViewerCloseKeepsRuntime(tmp_path, designerApplication,
                                                                              ownedDesignerWindow):
    with running(tmp_path) as (runtime, client, project, unusedSession):
        # The helper's explicit test lease is closed before Designer opens its
        # own lease. Viewing never creates another Job or subscriber.
        client.inspectionSession('close', project, unusedSession)
        window = ownedDesignerWindow(client)
        window.resize(1280, 720)
        window.show()
        try:
            wait(designerApplication, lambda: window.operatorCatalogController.state == 'ready')
            assert window.loadProjectDirectory(project)
            coordinator = window.nodeResultCoordinator
            panel = coordinator.panel
            window.navigateToNodeFromSidebar('load')
            assert not coordinator.history.current and len(runtime.jobRepository.all()) == 0
            window.startJob()
            wait(designerApplication, lambda: coordinator.imagePacket is not None and not window.runtimeController._jobActive)
            first = window.currentJobId
            assert coordinator.imagePacket['source'].originJobId == first
            assert coordinator.image.pixelColor(210, 20).red() == 0
            panel.enlarge.click()
            viewer = coordinator.viewer
            reads = coordinator.images.reads
            panel.enlarge.click()
            assert coordinator.viewer is viewer and coordinator.images.reads == reads
            assert viewer.view.item.pixmap().cacheKey() == panel.image.pixmap().cacheKey()
            assert viewer.identity == coordinator.imageSelection
            assert len(runtime.jobRepository.all()) == 1
            pixels = cv2.imdecode(np.fromfile(tmp_path / '本地 图片/input.png', np.uint8), cv2.IMREAD_COLOR)
            pixels[10:30, 200:220] = 255
            ok, encoded = cv2.imencode('.png', pixels)
            assert ok
            encoded.tofile(tmp_path / '本地 图片/input.png')
            window.startJob()
            wait(designerApplication, lambda: window.currentJobId not in (None, first))
            second = window.currentJobId
            wait(designerApplication, lambda: coordinator.imagePacket is not None
                 and coordinator.imagePacket['source'].originJobId == second and not window.runtimeController._jobActive)
            assert coordinator.image.pixelColor(210, 20).red() == 255
            panel.previous.click()
            assert coordinator.pixmap.isNull() and viewer.view.item.pixmap().isNull()
            wait(designerApplication, lambda: coordinator.imagePacket is not None)
            assert coordinator.image.pixelColor(210, 20).red() == 0
            assert viewer.identity == coordinator.imageSelection and viewer.identity[0] == first
            assert coordinator.imagePacket['source'].nodeRunId == coordinator.history.previous.inspection.get('main', 'load', first)['nodeRunId']
            window.navigateToNodeFromSidebar('count')
            assert 'count = 2' in panel.values.toPlainText()
            assert not panel.tabs.isTabVisible(0) and viewer.view.item.pixmap().isNull()
            assert viewer.message.text() == '所选节点没有图像输出端口'
            assert window.currentJobId == second
            panel.live.click()
            assert 'count = 3' in panel.values.toPlainText()
            window.navigateToNodeFromSidebar('blob')
            panel.ports.setCurrentIndex(panel.ports.findData('overlay'))
            wait(designerApplication, lambda: coordinator.imagePacket is not None)
            assert coordinator.imagePacket['source'].port == 'overlay'
            assert viewer.view.item.pixmap().cacheKey() == coordinator.pixmap.cacheKey()
            assert coordinator.qtBytes <= 24 * 1024 * 1024 and coordinator.images.peakScratchBytes <= 8 * 1024 * 1024
            beforeClose = coordinator.images.reads
            viewer.close()
            assert coordinator.viewer is None and coordinator.imagePacket is not None
            QTest.mouseDClick(panel.image, Qt.LeftButton)
            assert coordinator.viewer is not None and coordinator.images.reads == beforeClose
            assert len(runtime.jobRepository.all()) == 2 and not runtime._closed
            coordinator.viewer.close()
            window.close()
            assert coordinator.images._thread is not None and not coordinator.images._thread.is_alive()
            assert runtime.runInspectionStore.stats()['sessions'] == 0
            assert runtime.runInspectionStore.stats()['encodedBytes'] == 0
            assert not runtime._closed and runtime.jobRepository.get(second).status == 'COMPLETED'
        except AssertionError:
            print('UI DIAGNOSTIC', {'job': window.currentJobId, 'selectedNode': window.flowModel.selectedNodeId,
                'jobStatus': window.runtimePanelState.jobStatus, 'imageText': panel.image.text(),
                'selection': coordinator.imageSelection, 'reads': coordinator.images.reads,
                'session': coordinator.images.sessionId, 'notice': coordinator.images.message,
                'history': coordinator.history.runs, 'store': runtime.runInspectionStore.stats()})
            print('LOG TAIL', [(entry.eventType, entry.message) for entry in window.logEntries[-15:]])
            raise
        finally:
            window.close()
