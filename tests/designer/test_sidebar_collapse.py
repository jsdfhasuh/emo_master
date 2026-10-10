from emo_master.apps.designer.ui.main_window import MainWindow


def ensureQApp() -> None:
    try:
        from PySide2.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            _ = QApplication([])
    except Exception:
        pass


def testSidebarToggleState() -> None:
    ensureQApp()

    class RuntimeClientStub:
        def listOperators(self):
            return []

    window = MainWindow(RuntimeClientStub())
    assert window.isSidebarCollapsed is True

    window.expandSidebar()
    assert window.isSidebarCollapsed is False
    if hasattr(window, 'floatingToolbox'):
        window.show()
        window.floatingToolbox.tabs.setCurrentIndex(2)
        assert window.nodeListContainer.isVisible()
        window.collapseSidebar()
        assert window.isSidebarCollapsed is True
        assert not window.nodeListContainer.isVisible()
        assert not window.floatingToolbox.isExpanded()
        window.expandSidebar()
        assert window.nodeListContainer.isVisible()
        window.close()
        return
    nodeListContainer = getattr(window, "nodeListContainer", None)
    assert nodeListContainer is not None
    isHidden = getattr(nodeListContainer, "isHidden", None)
    if callable(isHidden):
        assert isHidden() is False

    window.collapseSidebar()
    assert window.isSidebarCollapsed is True
    if callable(isHidden):
        assert isHidden() is True
