import json
from pathlib import Path
import time

from PySide2.QtCore import Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication

from emo_master.apps.operator_view.main import OperatorView
from emo_master.apps.runtime.release_host import ReleaseHost
from emo_master.core.project.delivery_store import DeliveryStore
from emo_master.core.project.package_builder import buildPageTestPackage
from examples.runtime_pages_p2 import sampleProject


def waitFor(predicate, seconds=25):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('bounded Qt release deadline')


def setup(tmp_path):
    draft = tmp_path/'draft'
    draft.mkdir()
    document = sampleProject(draft)
    # Reuse the actual P4 UI-authored two-page configuration, not a hand-built view.
    config = Path(__file__).resolve().parents[3]/'docs/evidence/p4/c-accepted-visible/screens/two-pages.json'
    payload = document.model_dump()
    payload['presentation'] = json.loads(config.read_text(encoding='utf-8'))
    from emo_master.core.project.models import ProjectDocument
    document = ProjectDocument.model_validate(payload)
    package = buildPageTestPackage(document, draft, tmp_path/'packages')
    store = DeliveryStore(tmp_path/'store')
    store.activate(store.importPackage(package))
    draft.rename(tmp_path/'resources-unavailable')
    host = ReleaseHost(store.root, tmp_path/'data')
    ready = tmp_path/'data/ready.json'
    host.writeReady(ready)
    return host, ready


def testReadyNavigationExplicitStartSharedWindowsAndBorrowedClose(qtApp, tmp_path):
    host, ready = setup(tmp_path)
    view = OperatorView(ready)
    other = None
    view.show()
    try:
        waitFor(lambda: view.document is not None and not view.busy)
        assert view.error is None
        assert view.waitingPage is not None
        assert not host.presentation.jobs
        for page in view.document.presentation.pageOrder:
            QTest.mouseClick(view.waitingPage.buttons[page], Qt.LeftButton)
            assert view.waitingPage.currentPageId == page
        assert not host.presentation.jobs
        QTest.mouseClick(view.startButton, Qt.LeftButton)
        waitFor(lambda: view.hub is not None or view.error)
        assert view.error is None
        renderer = next(iter(view.hub.windows))
        waitFor(lambda: bool(renderer.displayed))
        scope = next(iter(renderer.displayed.values()))
        assert scope.result.identity.mode == 'release' and scope.result.status == 'COMPLETE'
        assert len(scope.images) == 1
        assert next(s.valueJson for s in scope.result.sources if s.valueJson is not None) == '2'
        QTest.mouseClick(view.secondButton, Qt.LeftButton)
        assert len(view.hub.windows) == 2
        assert len(host.presentation.jobs) == 1
        waitFor(lambda: all(w.displayed for w in view.hub.windows))
        assert view.session.stats['decoded'] == 1
        renderer.close()
        assert len(view.hub.windows) == 1
        assert not view.session.stop.is_set()
        other = OperatorView(ready, view.jobId)
        other.show()
        waitFor(lambda: other.hub is not None or other.error)
        assert other.error is None
        waitFor(lambda: bool(next(iter(other.hub.windows)).displayed))
        assert len(host.presentation.jobs) == 1
        view.close()
        waitFor(lambda: view.done)
        assert not host.runtime._closed and not other.session.stop.is_set()
        assert next(iter(next(iter(other.hub.windows)).displayed.values())).result.identity.resultKey == scope.result.identity.resultKey
    finally:
        for item in [view, other]:
            if item and not item.done:
                item.close()
                waitFor(lambda: item.done)
        host.close()


def testStaleDescriptorAndCloseDuringConnectDoNotStart(qtApp, tmp_path, monkeypatch):
    import threading
    host, ready = setup(tmp_path)
    original = OperatorView.connect
    entered, release = threading.Event(), threading.Event()
    def delayed(self):
        entered.set()
        assert release.wait(3)
        return original(self)
    monkeypatch.setattr(OperatorView, 'connect', delayed)
    view = OperatorView(ready)
    view.show()
    try:
        waitFor(entered.is_set)
        view.close()
        release.set()
        waitFor(lambda: view.done)
        assert view.hub is None and not host.presentation.jobs
        monkeypatch.setattr(OperatorView, 'connect', original)
        info = json.loads(ready.read_text())
        info['runtimeInstanceId'] = 'stale'
        ready.write_text(json.dumps(info))
        bad = OperatorView(ready)
        bad.show()
        waitFor(lambda: bad.error)
        assert 'stale' in bad.error
        assert not host.presentation.jobs
        bad.close()
        waitFor(lambda: bad.done)
    finally:
        release.set()
        if not view.done:
            view.close()
            waitFor(lambda: view.done)
        host.close()
