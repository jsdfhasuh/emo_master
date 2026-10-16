from types import SimpleNamespace
from pathlib import Path

from emo_master.apps.designer.ui.main_window import MainWindow
from PySide2.QtCore import Qt
from PySide2.QtGui import QImage

from tests.designer.test_node_run_inspection import event


class Client:
    def listOperators(self):
        return []


def windowWithNode():
    window = MainWindow(Client())
    window.addNodeFromOperatorPayload({'operatorId': 'vision.collection.count', 'displayName': '计数',
        'inputPorts': {'blobs': 'blobCollection'}, 'outputPorts': {'count': 'integer'}, 'paramSchema': {}})
    return window, next(iter(window.flowModel.nodes))


def deliver(window, node, kind='node.completed', sequence=2, execution='invocation', value=0):
    raw = event(kind, sequence, node=node, execution=execution, value=value)
    window.runtimeController._onRuntimeEvent(SimpleNamespace(**raw))


def testSelectedNodeShowsActualFalsyOutputAndClearsOnFailureAndNewJob():
    window, node = windowWithNode()
    window._setCurrentJobId('job')
    deliver(window, node, value=False)
    assert 'count = false' in window.nodeRunDetailsCard.text()
    assert '实际输入' in window.nodeRunDetailsCard.text()
    assert '算子耗时：1.5 ms' in window.nodeRunDetailsCard.text()
    assert window.nodeRunDetailsCard.textFormat() == Qt.PlainText
    deliver(window, node, 'node.started', 3, 'second')
    assert '等待完成' in window.nodeRunDetailsCard.text()
    assert 'count = false' not in window.nodeRunDetailsCard.text()
    deliver(window, node, sequence=4, execution='invocation', value=123)
    assert 'count = 123' not in window.nodeRunDetailsCard.text()
    assert window._nodeRuntimeState[node]['status'] == 'RUNNING'
    deliver(window, node, 'node.failed', 5, 'second')
    assert 'actual failure' in window.nodeRunDetailsCard.text()
    assert 'count = ' not in window.nodeRunDetailsCard.text()
    window._setCurrentJobId('other-job')
    assert '本次尚未收到' in window.nodeRunDetailsCard.text()
    assert 'actual failure' not in window.nodeRunDetailsCard.text()


def testSelectionSwitchKeepsReadOnlyInspectionWithoutDraftChanges():
    window, first = windowWithNode()
    window.addNodeFromOperatorPayload({'operatorId': 'vision.compare.number', 'displayName': '比较',
        'inputPorts': {'left': 'number'}, 'outputPorts': {'result': 'boolean'}, 'paramSchema': {}})
    second = next(node for node in window.flowModel.nodes if node != first)
    window._setCurrentJobId('job')
    window.navigateToNodeFromSidebar(first)
    deliver(window, first, value=2)
    before = window.flowModel.toProjectGraph()
    window.navigateToNodeFromSidebar(second)
    assert '本次尚未收到' in window.nodeRunDetailsCard.text()
    window.navigateToNodeFromSidebar(first)
    assert 'count = 2' in window.nodeRunDetailsCard.text()
    assert window.flowModel.toProjectGraph() == before


def testResultImageDecodesOncePerArtifactAndRetriesUnavailableFile(tmp_path, monkeypatch):
    import emo_master.apps.designer.ui.main_window as module
    window, _node = windowWithNode()
    window._setCurrentJobId('job')
    path = tmp_path / 'image.png'
    pixmapType = module.QPixmap
    decodes = []
    def load(value):
        decodes.append(value)
        return pixmapType(value)
    monkeypatch.setattr(module, 'QPixmap', load)
    window.runtimePanelState.latestImagePath = str(path)
    window.runtimePanelState.latestArtifact = {'nodeId': 'save', 'artifactId': 'first'}
    window._refreshPreviewImage()
    assert '图片不存在' in window.previewImageLabel.text()
    image = QImage(32, 16, QImage.Format_RGB888)
    image.fill(Qt.green)
    assert image.save(str(path))
    window._refreshPreviewImage()
    for _ in range(10):
        window._refreshRuntimePanelView()
    assert decodes == [str(path)]
    window.runtimePanelState.latestArtifact['artifactId'] = 'second'
    window._refreshPreviewImage()
    assert decodes == [str(path), str(path)]
    assert '来源节点 save' in window.runResultTools.origin.text()
    window._setCurrentJobId('next')
    assert window.previewImageLabel.text() == '暂无图片'
    assert not window.runResultTools.openFile.isEnabled()


def testNativeLogAndLocalResultFileShortcuts(tmp_path, monkeypatch):
    from emo_master.apps.designer.ui.run_result_tools import QDesktopServices
    window, _node = windowWithNode()
    window.show()
    window.runResultTools.logs.click()
    assert window.logDock.isVisible()
    assert not window.runResultTools.openFile.isEnabled()
    path = tmp_path / '本次结果.png'
    path.write_bytes(b'only checks desktop URL, not decoded')
    window._setCurrentJobId('job')
    window.runtimePanelState.latestImagePath = str(path)
    window.runResultTools.refresh()
    urls = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: urls.append(url.toLocalFile()))
    window.runResultTools.openFile.click()
    assert [Path(value) for value in urls] == [path]
    window.runtimePanelState.latestImagePath = str(tmp_path / 'missing.png')
    window.runResultTools.refresh()
    window.runResultTools.openFile.click()
    assert len(urls) == 1


def testWrappingProvenanceCannotOverlapImage(designerApplication):
    window, _node = windowWithNode()
    window.resize(640, 480)
    window.show()
    window._setCurrentJobId('job-with-a-long-identifier')
    window.runtimePanelState.latestImagePath = 'missing.png'
    window.runtimePanelState.latestArtifact = {'nodeId': 'long-source-identifier-' * 4}
    window._refreshPreviewImage()
    for _ in range(12):
        designerApplication.processEvents()
    tools = window.runResultTools
    assert tools.height() >= tools.layout().heightForWidth(tools.width())
    assert tools.geometry().top() > window.previewImageLabel.geometry().bottom()
    assert tools.rect().contains(tools.logs.geometry())
    assert tools.rect().contains(tools.openFile.geometry())


def testClosingOwnerReleasesInspectionPayload():
    window, node = windowWithNode()
    window.show()
    window._setCurrentJobId('job')
    deliver(window, node, value=2)
    assert window.runtimePanelState.nodeInspection.retainedBytes > 0
    window.close()
    assert window.runtimePanelState.nodeInspection.retainedBytes == 0
