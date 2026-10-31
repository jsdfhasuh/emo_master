"""The final Designer window stays alive until its Web renderer has exited."""
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

from PySide2.QtCore import QTimer
from PySide2.QtWidgets import QWidget

from emo_master.apps.designer.page_designer.coordinator import PageCoordinator
from emo_master.apps.designer.page_designer.web_preview import WebPreviewProcess
from emo_panel_protocol.preview import PreviewContext


def spin(app, condition):
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition()


def context():
    return PreviewContext(mode='fixtures', projectId='project', packageRevision='a' * 64,
                          historyDefinitionRevision='b' * 64)


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.closeResults = []
        coordinator = self.coordinator = PageCoordinator.__new__(PageCoordinator)
        coordinator.window = self
        coordinator._canSync = lambda: True
        coordinator.closeApproved = 'unchanged'
        coordinator.session = SimpleNamespace(_signature=lambda: 'unchanged')
        coordinator.preview = SimpleNamespace(active=lambda: False, timer=QTimer(self))

    def closeEvent(self, event):
        result = self.coordinator.prepareClose()
        self.closeResults.append(result)
        event.setAccepted(result)


def testWindowCloseWaitsForRendererAndRetriesOnlyOnce(retainedQtApplication, tmp_path):
    window = Window()
    script = ('import json,time; print(json.dumps({"event":"ready","protocolVersion":"1",'
              '"url":"http://127.0.0.1:51234/?token=test"}),flush=True); time.sleep(30)')
    owner = WebPreviewProcess(window, command=[sys.executable, '-c', script])
    window.coordinator.webPreview = owner
    try:
        window.show()
        owner.start(tmp_path / 'preview.emoweb', context())
        directory = Path(owner.directory.name)
        spin(retainedQtApplication, lambda: owner.isReady)
        assert not window.close()
        assert window.isVisible() and not owner.finished
        # Repeated user closes must not enqueue repeated automatic closes.
        assert not window.close()
        spin(retainedQtApplication, lambda: not window.isVisible())
        assert owner.finished and not directory.exists()
        assert window.closeResults == [False, False, True]
    finally:
        owner.close()
        spin(retainedQtApplication, lambda: owner.finished)
        window.close()
        window.deleteLater()
        retainedQtApplication.processEvents()


def testWindowCanCloseDuringPreparationWithoutLaunchingRenderer(retainedQtApplication, tmp_path, monkeypatch):
    from emo_master.apps.designer.page_designer import web_preview
    entered, release = threading.Event(), threading.Event()

    def prepare(*_):
        entered.set()
        assert release.wait(3)
        return context()

    monkeypatch.setattr(web_preview, 'runtimePreviewContext', prepare)
    window = Window()
    directory = tempfile.TemporaryDirectory(prefix='emo-preview-shutdown-')
    path = Path(directory.name)
    owner = WebPreviewProcess(window, directory=directory, command=[sys.executable])
    window.coordinator.webPreview = owner
    started = []
    owner.process.started.connect(lambda: started.append(True))
    try:
        window.show()
        owner.prepareRuntime(tmp_path / 'preview.emoweb', None,
                             SimpleNamespace(runtimeScope='scope', runtimeService=object()))
        spin(retainedQtApplication, entered.is_set)
        pending = owner.pending
        window.close()
        spin(retainedQtApplication, lambda: not window.isVisible())
        assert owner.finished and not path.exists()
        assert window.closeResults.count(True) == 1
        release.set()
        spin(retainedQtApplication, pending.done)
        assert not started
    finally:
        release.set()
        owner.close()
        spin(retainedQtApplication, lambda: owner.finished)
        window.close()
        window.deleteLater()
        retainedQtApplication.processEvents()
