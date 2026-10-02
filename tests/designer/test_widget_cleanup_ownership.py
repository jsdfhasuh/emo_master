"""Qt popups must retire through their complete QWidget ownership ancestry."""
import pytest

from tests.qt_widget_owner import createdWidgetRoots


def testNestedComboPopupRetiresThroughCreatedWindow(designerApplication):
    pytest.importorskip("PySide2")
    import shiboken2
    from PySide2.QtCore import QCoreApplication, QEvent
    from PySide2.QtWidgets import QComboBox, QWidget

    window = QWidget()
    nested = QWidget(window)
    combo = QComboBox(nested)
    combo.addItem("ALL")
    popup = combo.view().window()
    try:
        assert popup.isWindow()
        assert popup in designerApplication.topLevelWidgets()
        assert popup.parentWidget() is combo
        # The direct-parent filter selected BOTH widgets and could schedule
        # independent native deletion of the popup and its owning window.
        created = [window, popup]
        assert [item for item in created if item.parentWidget() not in created] == created
        assert createdWidgetRoots(created) == [window]
        # A new popup under a pre-existing window still needs independent
        # retirement; only ancestors created by this test suppress a root.
        assert createdWidgetRoots([popup]) == [popup]
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.DeferredDelete)
        assert not shiboken2.isValid(window)
        assert not shiboken2.isValid(popup)


class Client:
    def listOperators(self):
        return []


def testOwnedDesignerRetiresNativeObjectsAfterClose(ownedDesignerWindow):
    import threading
    import shiboken2
    from PySide2.QtCore import Qt
    window = ownedDesignerWindow(Client())
    native = (window, window.flowScene, window.operatorBubble)
    destroyedOn = []
    for obj in native:
        obj.destroyed.connect(lambda *_: destroyedOn.append(threading.get_ident()), Qt.DirectConnection)
    assert window.close()
    # The original close-only e2e path leaves all three native objects alive.
    assert all(shiboken2.isValid(obj) for obj in native)
    ownedDesignerWindow.retire()
    assert all(not shiboken2.isValid(obj) for obj in native)
    assert destroyedOn == [threading.get_ident()] * 3
    ownedDesignerWindow.retire()
    assert destroyedOn == [threading.get_ident()] * 3


def testOwnedDesignerRetiresAfterBodyAssertionFailure(ownedDesignerWindow):
    import shiboken2
    with pytest.raises(AssertionError, match='synthetic body failure'):
        with ownedDesignerWindow:
            window = ownedDesignerWindow(Client())
            native = (window, window.flowScene, window.operatorBubble)
            window.createWorkflow('unsaved synthetic workflow')
            raise AssertionError('synthetic body failure')
    assert all(not shiboken2.isValid(obj) for obj in native)


@pytest.mark.parametrize('blocker', ['veto', 'runtime', 'icon', 'catalog'])
def testOwnedDesignerKeepsBlockedOwnerAndRetiresHealthyOwner(ownedDesignerWindow, monkeypatch, blocker):
    import shiboken2
    blocked = ownedDesignerWindow(Client())
    healthy = ownedDesignerWindow(Client())
    with monkeypatch.context() as patch:
        if blocker == 'veto':
            patch.setattr(blocked, 'close', lambda: False)
            expected = 'refused close'
        elif blocker == 'runtime':
            originalClose = blocked.close

            def leaveControllerOpen():
                result = originalClose()
                blocked.runtimeController._closed = False
                return result

            patch.setattr(blocked, 'close', leaveControllerOpen)
            expected = 'runtime controller is not closed'
        else:
            assert blocked.close()
            worker = (blocked.operatorIconProvider.worker if blocker == 'icon'
                      else blocked.operatorCatalogWorker)
            patch.setattr(worker, 'isRunning', lambda: True)
            expected = f'{blocker} workers are still running'
        with pytest.raises(AssertionError, match=expected):
            ownedDesignerWindow.retire()
        assert shiboken2.isValid(blocked)
        assert shiboken2.isValid(blocked.flowScene)
        assert shiboken2.isValid(blocked.operatorBubble)
        assert not shiboken2.isValid(healthy)
        assert not shiboken2.isValid(healthy.flowScene)
        assert not shiboken2.isValid(healthy.operatorBubble)
    ownedDesignerWindow.retire()
    assert not shiboken2.isValid(blocked)
