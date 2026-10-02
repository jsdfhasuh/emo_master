from PySide2.QtCore import Qt
from PySide2.QtWidgets import QApplication

from test_workspace import designer  # noqa: F401


def testNativePaletteSplitterAndSearch(designer):  # noqa: F811
    c = designer.pageCoordinator
    c.showPages()
    e = c.editor
    designer.resize(1280, 720)
    QApplication.processEvents()
    assert e.splitter.count() == 3
    assert e.tools.palette.count() == 8
    assert e.libraryTabs.count() == 2
    history = len(c.session._undo)
    e.tools.search.setText('图像')
    assert [e.tools.palette.item(i).data(Qt.UserRole) for i in range(8)
            if not e.tools.palette.item(i).isHidden()] == ['image']
    e.tools.search.clear()
    e.togglePanel(0)
    assert e.splitter.sizes()[0] == 0
    e.togglePanel(0)
    assert e.splitter.sizes()[0] > 0
    assert len(c.session._undo) == history
    assert e.propertyScroll.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    assert not e.tools.palette.grab().isNull()
