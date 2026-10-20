import os

import pytest

from tests.qt_widget_owner import createdWidgetRoots

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import emo_master  # noqa: E402,F401 - DLL bootstrap
from PySide2.QtWidgets import QApplication  # noqa: E402
from PySide2.QtCore import QCoreApplication, QEvent  # noqa: E402


@pytest.fixture(scope='session')
def qtApp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def cleanWindows(qtApp):
    before = set(qtApp.topLevelWidgets())
    yield
    import shiboken2
    created = [widget for widget in qtApp.topLevelWidgets()
               if widget not in before and shiboken2.isValid(widget)]
    # Top-level popup windows are still owned by their QWidget ancestors.
    roots = createdWidgetRoots(created)
    for widget in roots:
        if shiboken2.isValid(widget):
            widget.close()
    for widget in roots:
        if shiboken2.isValid(widget):
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
