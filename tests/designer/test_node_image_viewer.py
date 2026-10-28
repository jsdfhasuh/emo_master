"""Native Qt interaction and one shared read over two real Runner Jobs."""
import time

import emo_master  # noqa: F401 - initialize Windows DLL order before Qt/OpenCV.
import cv2
import numpy as np
import pytest
from PySide2.QtCore import Qt, QPoint, QEvent, QSettings
from PySide2.QtGui import QPixmap, QWheelEvent
from PySide2.QtTest import QTest

from emo_master.apps.designer.ui.node_image_viewer import NodeImageViewer
from tests.runtime.test_run_inspection_rpc import running


def wait(app, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        app.processEvents()
        sample = predicate()
        if sample:
            return sample
        assert time.monotonic() < deadline, 'Qt condition was not reached before the outer watchdog'
        time.sleep(.005)


def testWaitReturnsTheSampleWithoutProcessingNewEventsAfterIt():
    sample = object()

    class Application:
        calls = 0

        def processEvents(self):
            self.calls += 1

    app = Application()
    assert wait(app, lambda: sample if app.calls == 1 else None) is sample
    assert app.calls == 1


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


@pytest.mark.parametrize('readDisconnect', [False, True])
def testTwoRealRunsFollowSelectionWithoutExtraReadsAndViewerCloseKeepsRuntime(tmp_path, designerApplication,
                                                    ownedDesignerWindow, monkeypatch, readDisconnect):
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
            readAttempts = []
            if readDisconnect:
                from emo_master.apps.designer.services.runtime_client import RuntimeClientError
                read = client.readInspectionAsset

                def failOnce(*args):
                    readAttempts.append(args)
                    if len(readAttempts) == 1:
                        raise RuntimeClientError('StatusCode.UNAVAILABLE', 'temporary image read disconnect')
                    return read(*args)

                monkeypatch.setattr(client, 'readInspectionAsset', failOnce)
            window.startJob()
            if readDisconnect:
                wait(designerApplication, lambda: coordinator.images._disconnected
                     and not window.runtimeController._jobActive
                     and panel.image.text() == 'temporary image read disconnect')
                from types import SimpleNamespace
                for _ in range(10):
                    coordinator.status(SimpleNamespace(status='COMPLETED'))
                    coordinator.refresh()
                assert panel.image.text() == 'temporary image read disconnect'
                assert len(readAttempts) == 1 and len(runtime.jobRepository.all()) == 1
                from tests.designer.test_inspection_images import forceDueRenewal
                forceDueRenewal(coordinator.images)
            # A terminal Job can precede its monitor's queued finished signal.
            # Wait for the same readiness gate as the Run action before rerunning.
            wait(designerApplication, lambda: coordinator.imagePacket is not None
                 and not window.runtimeController._jobActive and window.runtimeController.canStart())
            if readDisconnect:
                assert len(readAttempts) == 2 and not coordinator.images._disconnected
                assert len(runtime.jobRepository.all()) == 1
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


@pytest.mark.parametrize('shape', [(12, 16), (2160, 3840)])
def testLiveLoopPanelAndViewerKeepOneInvocationAcrossFourTabs(tmp_path, designerApplication, ownedDesignerWindow, shape):
    import json
    import os
    from pathlib import Path
    from tests.runtime.inspection_fixtures import autonomousLoopProject
    from tests.runtime.runtime_test_utils import waitForTerminal
    def configure(document, root):
        next(node for node in document.workflows['body'].nodes if node.nodeId == 'image').params.update(
            height=shape[0], width=shape[1])
    with running(tmp_path, factory=autonomousLoopProject, configure=configure) as (runtime, client, project, extra):
        client.inspectionSession('close', project, extra)
        # Keep this visual test independent of saved application preferences.
        settings = QSettings(str(tmp_path / 'live-node-settings.ini'), QSettings.IniFormat)
        window = ownedDesignerWindow(client, settingsStore=settings)
        window.resize(1360, 850)
        window.show()
        try:
            wait(designerApplication, lambda: window.operatorCatalogController.state == 'ready')
            assert window.loadProjectDirectory(project)
            window.navigateToWorkflow('body', 'image')
            coordinator = window.nodeResultCoordinator
            panel = coordinator.panel
            # Publish the Designer-normalized fixture through the normal write
            # API, so this test does not wait inside a comparison dialog.
            from emo_master.core.project.configuration import projectChanges
            window.workflowController.captureActiveWorkflow()
            window.pageCoordinator.sync()
            online = client.getOnlineProjectSnapshot()
            local = window.pageCoordinator.session.payload()
            changes = projectChanges(online['payload'], local)
            if changes:
                client.writeProjectConfiguration(online, local, [change['path'] for change in changes],
                                                  'live-node-qt-fixture')
                def committed():
                    result = client.getProjectWriteStatus('live-node-qt-fixture')
                    assert result['state'] not in ('FAILED', 'CANCELLED', 'REJECTED'), result
                    return result['state'] == 'COMMITTED'
                wait(designerApplication, committed)
            assert not projectChanges(client.getOnlineProjectSnapshot()['payload'], local)
            window.startJob()
            wait(designerApplication, lambda: coordinator.imagePacket is not None)
            assert not window.floatingRunPanel.isExpanded()
            assert runtime.jobRepository.get(window.currentJobId).status == 'RUNNING'
            window.designerActions.invoke('results')
            assert window.floatingRunPanel.isExpanded()

            def firstDisplayedInvocation():
                packet = coordinator.imagePacket
                if packet is None:
                    return None
                if not panel.image.firstImagePaintNs:
                    # On the offscreen 800x600 screen the normal nested scroll
                    # surfaces can clip the image. Expose it before requiring paint.
                    panel.imagePage.ensureWidgetVisible(panel.image)
                    coordinator.panelScroll.ensureWidgetVisible(panel.image)
                    window.floatingRunPanel.contentScroll.ensureWidgetVisible(panel.image)
                    return None
                # Read the four tabs and pixels before processing another Qt event.
                displayedCount = int(panel.values.toPlainText().split('count = ')[1].split()[0])
                assert coordinator.image.pixelColor(0, 0).red() == 20 + displayedCount
                assert f'index = {displayedCount}' in panel.inputs.toPlainText()
                assert packet['source'].nodeRunId in panel.execution.toPlainText()
                assert packet['source'].workflowRunId in panel.execution.toPlainText()
                assert packet['source'].nodeRunId == coordinator.imageSelection[3]
                return packet

            try:
                firstPacket = wait(designerApplication, firstDisplayedInvocation, timeout=20)
            except AssertionError:
                print('LIVE_NODE_FIRST_PAINT', {
                    'job': window.currentJobId, 'message': window.runtimePanelState.lastMessage,
                    'workflow': window.activeWorkflowId, 'node': window.flowModel.selectedNodeId,
                    'packet': coordinator.imagePacket is not None,
                    'visible': panel.image.isVisible(), 'painted': panel.image.firstImagePaintNs,
                    'region': panel.image.visibleRegion().boundingRect().getRect(),
                    'geometry': panel.image.geometry().getRect(),
                    'windowGeometry': window.geometry().getRect(),
                    'updatesEnabled': panel.image.updatesEnabled(),
                    'windowExposed': window.windowHandle().isExposed(),
                    'font': designerApplication.font().toString(),
                    'imageNotice': coordinator.images.message,
                    'values': panel.values.toPlainText(),
                    'logs': [(entry.eventType, entry.message) for entry in window.logEntries[-10:]],
                })
                raise
            job = window.currentJobId
            assert runtime.jobRepository.get(job).status == 'RUNNING'
            assert not any(event.eventType == 'node.started' and event.nodeId == 'gate'
                           for event in runtime.eventStore.readMerged(job))
            panel.enlarge.click()
            viewer = coordinator.viewer
            assert viewer.view.item.pixmap().cacheKey() == panel.image.pixmap().cacheKey()
            wait(designerApplication, lambda: any(event.eventType == 'node.started' and event.nodeId == 'gate'
                  for event in runtime.eventStore.readMerged(job)), timeout=40)
            wait(designerApplication, lambda: coordinator.imagePacket is not None
                 and 'count = 3' in panel.values.toPlainText() and coordinator.image.pixelColor(0, 0).red() == 23
                 and panel.image.firstImagePaintNs > 0)
            assert panel.previous.text() == '上一次执行' and panel.live.text() == '本次执行'
            assert panel.image.firstImagePaintNs > 0
            if shape[1] > 1920:
                assert '原始 3840 × 2160 · 预览 1920 × 1080' in panel.imageMeta.text()
            screenshots = os.environ.get('EMO_NODE_RESULTS_SCREEN_DIR')
            if screenshots:
                output = Path(screenshots) / ('4k' if shape[1] > 1920 else 'small')
                output.mkdir(parents=True, exist_ok=True)
                assert window.grab().save(str(output / 'live-current.png'))
            panel.previous.click()
            wait(designerApplication, lambda: coordinator.imagePacket is not None)
            record = coordinator.history.view('body', 'image')['record']
            assert 'count = 2' in panel.values.toPlainText() and 'index = 2' in panel.inputs.toPlainText()
            assert record['nodeRunId'] in panel.execution.toPlainText()
            assert coordinator.imagePacket['source'].nodeRunId == record['nodeRunId']
            assert coordinator.image.pixelColor(0, 0).red() == 22
            assert viewer.identity == coordinator.imageSelection
            assert viewer.view.item.pixmap().cacheKey() == coordinator.pixmap.cacheKey()
            originalName = panel.title.text()
            window.flowModel.nodes['image'].displayName = 'Changed Draft'
            coordinator.refresh()
            assert panel.title.text() == originalName
            if screenshots:
                designerApplication.processEvents()
                assert window.grab().save(str(output / 'live-previous.png'))
                assert viewer.grab().save(str(output / 'live-viewer.png'))
            window.navigateToWorkflow('body', 'save')
            wait(designerApplication, lambda: coordinator.imagePacket is not None)
            assert panel.ports.currentData() == '__saved_result__'
            assert panel.tabs.currentWidget() is panel.imagePage
            assert coordinator.image.pixelColor(0, 0).red() == 22
            reads = coordinator.images.reads
            viewer.close()
            assert coordinator.viewer is None and runtime.jobRepository.get(job).status == 'RUNNING'
            panel.enlarge.click()
            assert coordinator.images.reads == reads
            print('LIVE_NODE_QT', json.dumps(dict(platform=designerApplication.platformName(),
                dpr=window.devicePixelRatioF(), job=job, firstNodeRun=firstPacket['source'].nodeRunId,
                previousNodeRun=record['nodeRunId'], reads=reads, state=runtime.jobRepository.get(job).status,
                sharedPixmap=True, counts=[3, 2], originalShape=shape,
                previewShape=[coordinator.image.height(), coordinator.image.width()])))
            assert client.stopJob(job).ok and waitForTerminal(runtime, job).status == 'ABORTED'
            wait(designerApplication, lambda: not window.runtimeController._jobActive)
            window.navigateToWorkflow('main', 'gate')
            assert '已取消' in panel.origin.text() and not coordinator.imagePacket
        finally:
            window.close()
